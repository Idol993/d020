import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core.config import (
    get_config, ReleaseRequest, ReleaseStatus,
    PrecheckResult, dataclass_to_dict,
)
from ..core.logger import get_logger, get_cst_now_str, get_audit_logger, generate_id
from ..core.notifier import get_notifier
from .checks import PrecheckRegistry, BaseCheck


@dataclass
class PrecheckSummary:
    release_id: str
    version: str
    started_at: str
    completed_at: str = ""
    total_duration_seconds: float = 0.0
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    overall_passed: bool = False
    results: List[PrecheckResult] = field(default_factory=list)
    blocking_issues: List[str] = field(default_factory=list)
    aggregated_suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "release_id": self.release_id,
            "version": self.version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_seconds": self.total_duration_seconds,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "overall_passed": self.overall_passed,
            "results": [dataclass_to_dict(r) for r in self.results],
            "blocking_issues": self.blocking_issues,
            "aggregated_suggestion": self.aggregated_suggestion,
        }


class PrecheckEngine:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("precheck_engine")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.parallel = self.config.get("precheck.parallel_check", True)
        self.timeout = self.config.get("precheck.timeout_seconds", 300)
        self._summary_cache: Dict[str, PrecheckSummary] = {}
        self._lock = threading.Lock()

    def run_precheck(self, request: ReleaseRequest,
                     progress_callback: Optional[Callable[[str, float], None]] = None) -> PrecheckSummary:
        summary = PrecheckSummary(
            release_id=request.release_id,
            version=request.version,
            started_at=get_cst_now_str(),
        )

        self.logger.info(f"[{request.release_id}] 开始执行前置校验, 版本: {request.version}")
        self.audit.log(
            operator=request.submitted_by,
            action="PRECHECK_STARTED",
            target_id=request.release_id,
            target_type="RELEASE",
            before_state={"version": request.version},
            after_state={"status": ReleaseStatus.PRECHECK_PENDING.value},
        )

        checks = self._get_enabled_checks(request)
        summary.total_checks = len(checks)

        if self.parallel:
            results = self._run_parallel(checks, request, progress_callback)
        else:
            results = self._run_sequential(checks, request, progress_callback)

        summary.results = results
        summary.completed_at = get_cst_now_str()
        summary.total_duration_seconds = round(
            (time.mktime(time.strptime(summary.completed_at, "%Y-%m-%d %H:%M:%S")) -
             time.mktime(time.strptime(summary.started_at, "%Y-%m-%d %H:%M:%S"))), 2
        )

        passed_list = [r for r in results if r.passed]
        failed_list = [r for r in results if not r.passed]
        summary.passed_checks = len(passed_list)
        summary.failed_checks = len(failed_list)

        for r in failed_list:
            summary.blocking_issues.append(f"[{r.check_name}] {r.message}")

        summary.overall_passed = len(failed_list) == 0

        if not summary.overall_passed:
            suggestions = [f"【{r.check_name}】\n{r.suggestion}" for r in failed_list if r.suggestion]
            summary.aggregated_suggestion = "\n\n".join(suggestions)

        with self._lock:
            self._summary_cache[request.release_id] = summary

        self._save_summary(summary)
        self._post_process(request, summary)

        status = ReleaseStatus.PRECHECK_PASSED if summary.overall_passed else ReleaseStatus.PRECHECK_FAILED
        self.audit.log(
            operator="SYSTEM",
            action="PRECHECK_COMPLETED",
            target_id=request.release_id,
            target_type="RELEASE",
            before_state={"status": ReleaseStatus.PRECHECK_PENDING.value},
            after_state={
                "status": status.value,
                "passed": summary.overall_passed,
                "passed_checks": summary.passed_checks,
                "failed_checks": summary.failed_checks,
            },
        )

        if summary.overall_passed:
            self.logger.info(
                f"[{request.release_id}] 前置校验全部通过, 耗时: {summary.total_duration_seconds}s"
            )
        else:
            self.logger.warning(
                f"[{request.release_id}] 前置校验失败, 阻断项: {summary.failed_checks}/{summary.total_checks}"
            )
            self._notify_failure(request, summary)

        return summary

    def _get_enabled_checks(self, request: ReleaseRequest) -> List[BaseCheck]:
        cfg = self.config.get("precheck", {})
        enabled = []
        for check in PrecheckRegistry.get_all():
            check_cfg = cfg.get(check.check_name, {})
            if check_cfg.get("enabled", True):
                if request.release_type.value == "hotfix" and check.check_name in ["terminal_online_rate"]:
                    self.logger.info(f"[{request.release_id}] Hotfix模式跳过校验: {check.check_name}")
                    continue
                enabled.append(check)
        return enabled

    def _run_parallel(self, checks: List[BaseCheck], request: ReleaseRequest,
                      progress_callback: Optional[Callable]) -> List[PrecheckResult]:
        results: List[PrecheckResult] = []
        total = len(checks)
        completed = 0

        with ThreadPoolExecutor(max_workers=min(total, 4)) as executor:
            future_map = {executor.submit(c.execute, request): c for c in checks}

            for future in as_completed(future_map, timeout=self.timeout):
                check = future_map[future]
                try:
                    result = future.result()
                except Exception as e:
                    self.logger.error(f"校验 [{check.check_name}] 执行异常: {e}")
                    result = PrecheckResult(
                        check_name=check.check_name,
                        passed=False,
                        score=0.0,
                        threshold=0.0,
                        message=f"执行异常: {str(e)}",
                        suggestion="请排查校验服务后重试，或联系运维支持",
                        checked_at=get_cst_now_str(),
                        duration_seconds=0.0,
                        details={"error": str(e), "exception_type": type(e).__name__},
                    )
                results.append(result)
                completed += 1
                if progress_callback:
                    try:
                        progress_callback(check.check_name, completed / total)
                    except Exception:
                        pass
        return results

    def _run_sequential(self, checks: List[BaseCheck], request: ReleaseRequest,
                        progress_callback: Optional[Callable]) -> List[PrecheckResult]:
        results = []
        total = len(checks)
        for idx, check in enumerate(checks):
            self.logger.info(f"[{request.release_id}] 执行校验 {idx+1}/{total}: {check.check_name}")
            try:
                result = check.execute(request)
            except Exception as e:
                self.logger.error(f"校验 [{check.check_name}] 执行异常: {e}")
                result = PrecheckResult(
                    check_name=check.check_name,
                    passed=False,
                    score=0.0,
                    threshold=0.0,
                    message=f"执行异常: {str(e)}",
                    suggestion="请排查校验服务后重试",
                    checked_at=get_cst_now_str(),
                    duration_seconds=0.0,
                    details={"error": str(e)},
                )
            results.append(result)
            if progress_callback:
                try:
                    progress_callback(check.check_name, (idx + 1) / total)
                except Exception:
                    pass

            if not result.passed:
                self.logger.warning(
                    f"[{request.release_id}] 校验失败: {check.check_name} - {result.message}"
                )
        return results

    def _save_summary(self, summary: PrecheckSummary):
        try:
            db_dir = self.config.get_storage_path("db_dir")
            precheck_dir = db_dir / "precheck"
            precheck_dir.mkdir(parents=True, exist_ok=True)
            file = precheck_dir / f"{summary.release_id}.json"
            with open(file, "w", encoding="utf-8") as f:
                json.dump(summary.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存前置校验摘要失败: {e}")

    def _post_process(self, request: ReleaseRequest, summary: PrecheckSummary):
        pass

    def _notify_failure(self, request: ReleaseRequest, summary: PrecheckSummary):
        details = {}
        for r in summary.results:
            details[r.check_name] = {
                "passed": r.passed,
                "score": f"{r.score:.2%}",
                "threshold": f"{r.threshold:.2%}",
                "message": r.message,
            }

        context = {
            "title": f"发布前置校验失败 - 发布已阻断",
            "release_id": request.release_id,
            "release_title": request.title,
            "version": request.version,
            "release_type": "紧急热修复" if request.release_type.value == "hotfix" else "常规迭代",
            "operator": request.submitted_by,
            "status": f"已阻断（{summary.failed_checks}/{summary.total_checks}项未通过）",
            "description": request.description,
            "precheck_details": {
                "总耗时": f"{summary.total_duration_seconds}s",
                "通过校验": f"{summary.passed_checks}/{summary.total_checks}",
                "失败校验": f"{summary.failed_checks}/{summary.total_checks}",
                "各维度详情": details,
                "阻断问题列表": summary.blocking_issues,
            },
            "suggestion": summary.aggregated_suggestion,
            "action_required": f"请根据修复建议处理后，重新提交发布申请",
        }

        try:
            self.notifier.send(
                template_key="precheck_failed",
                context=context,
                receivers=[request.submitted_by] if "@" in request.submitted_by else None,
                priority="high",
            )
        except Exception as e:
            self.logger.error(f"发送前置校验失败通知异常: {e}")

    def get_summary(self, release_id: str) -> Optional[PrecheckSummary]:
        with self._lock:
            if release_id in self._summary_cache:
                return self._summary_cache[release_id]

        try:
            db_dir = self.config.get_storage_path("db_dir")
            file = db_dir / "precheck" / f"{release_id}.json"
            if file.exists():
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results = []
                for r in data.get("results", []):
                    results.append(PrecheckResult(**r))
                summary = PrecheckSummary(
                    release_id=data["release_id"],
                    version=data["version"],
                    started_at=data["started_at"],
                    completed_at=data.get("completed_at", ""),
                    total_duration_seconds=data.get("total_duration_seconds", 0.0),
                    total_checks=data.get("total_checks", 0),
                    passed_checks=data.get("passed_checks", 0),
                    failed_checks=data.get("failed_checks", 0),
                    overall_passed=data.get("overall_passed", False),
                    results=results,
                    blocking_issues=data.get("blocking_issues", []),
                    aggregated_suggestion=data.get("aggregated_suggestion", ""),
                )
                with self._lock:
                    self._summary_cache[release_id] = summary
                return summary
        except Exception as e:
            self.logger.error(f"加载前置校验摘要失败: {e}")
        return None

    def can_proceed_to_approval(self, release_id: str) -> bool:
        summary = self.get_summary(release_id)
        if not summary:
            return False
        return summary.overall_passed


def get_precheck_engine() -> PrecheckEngine:
    return PrecheckEngine()
