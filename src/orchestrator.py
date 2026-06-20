import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False

from .core.config import (
    get_config, ReleaseRequest, ReleaseStatus, ReleaseType,
    dataclass_to_dict,
)
from .core.logger import (
    get_logger, get_cst_now, get_cst_now_str,
    get_audit_logger, generate_id,
)
from .core.notifier import get_notifier
from .precheck.engine import PrecheckEngine, get_precheck_engine
from .approval.engine import ApprovalEngine, get_approval_engine
from .release.grayscale import GrayscaleReleaseEngine, get_grayscale_engine
from .reporting.drill import RollbackDrillManager, get_drill_manager
from .reporting.weekly_report import WeeklyReportGenerator, get_weekly_report_generator


@dataclass
class ReleasePipeline:
    request: ReleaseRequest
    status: ReleaseStatus = ReleaseStatus.DRAFT
    current_step: str = "initialized"
    step_history: List[Dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "release_id": self.request.release_id,
            "request": dataclass_to_dict(self.request),
            "status": self.status.value,
            "current_step": self.current_step,
            "step_history": self.step_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
        }


class ReleasePipelineOrchestrator:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("orchestrator")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.precheck = get_precheck_engine()
        self.approval = get_approval_engine()
        self.grayscale = get_grayscale_engine()
        self.drill = get_drill_manager()
        self.weekly = get_weekly_report_generator()
        self._pipelines: Dict[str, ReleasePipeline] = {}
        self._lock = threading.Lock()

    def submit_release(self, request: ReleaseRequest) -> ReleasePipeline:
        if not request.release_id:
            request.release_id = generate_id("REL")
        if not request.submitted_at:
            request.submitted_at = get_cst_now_str()

        pipeline = ReleasePipeline(
            request=request,
            status=ReleaseStatus.DRAFT,
            current_step="submitted",
            created_at=get_cst_now_str(),
            updated_at=get_cst_now_str(),
        )

        self._add_history(pipeline, "SUBMIT", f"发布申请提交: {request.title}")

        with self._lock:
            self._pipelines[request.release_id] = pipeline

        self._save_pipeline(pipeline)
        self.logger.info(
            f"[{request.release_id}] 发布申请已提交, 类型={request.release_type.value}, "
            f"版本={request.version}, 提交人={request.submitted_by}"
        )
        return pipeline

    def run_precheck(self, release_id: str,
                     progress_cb: Optional[Callable] = None) -> ReleasePipeline:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            raise ValueError(f"未找到发布流水线: {release_id}")

        pipeline.status = ReleaseStatus.PRECHECK_PENDING
        pipeline.current_step = "precheck_running"
        pipeline.updated_at = get_cst_now_str()
        self._add_history(pipeline, "PRECHECK_START", "启动前置校验")

        self.logger.info(f"[{release_id}] 启动前置校验...")

        try:
            summary = self.precheck.run_precheck(pipeline.request, progress_cb)

            if summary.overall_passed:
                pipeline.status = ReleaseStatus.PRECHECK_PASSED
                pipeline.current_step = "precheck_passed"
                self._add_history(
                    pipeline, "PRECHECK_PASS",
                    f"前置校验通过 ({summary.passed_checks}/{summary.total_checks}项), "
                    f"耗时{summary.total_duration_seconds}s"
                )
            else:
                pipeline.status = ReleaseStatus.PRECHECK_FAILED
                pipeline.current_step = "precheck_failed"
                pipeline.error_message = f"{summary.failed_checks}项校验未通过"
                self._add_history(
                    pipeline, "PRECHECK_FAIL",
                    f"前置校验失败 ({summary.failed_checks}/{summary.total_checks}项阻断): "
                    + "; ".join(summary.blocking_issues)
                )
        except Exception as e:
            pipeline.status = ReleaseStatus.PRECHECK_FAILED
            pipeline.current_step = "precheck_error"
            pipeline.error_message = f"校验异常: {str(e)}"
            self._add_history(pipeline, "PRECHECK_ERROR", f"校验异常: {str(e)}")
            self.logger.error(f"[{release_id}] 前置校验异常: {e}", exc_info=True)

        pipeline.updated_at = get_cst_now_str()
        self._save_pipeline(pipeline)
        return pipeline

    def start_approval(self, release_id: str) -> ReleasePipeline:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            raise ValueError(f"未找到发布流水线: {release_id}")
        if pipeline.status not in [ReleaseStatus.PRECHECK_PASSED, ReleaseStatus.DRAFT]:
            raise ValueError(
                f"当前状态 {pipeline.status.value} 不允许启动审批流程，"
                f"需先通过前置校验"
            )

        if not self.precheck.can_proceed_to_approval(release_id):
            if pipeline.status == ReleaseStatus.DRAFT:
                self.run_precheck(release_id)
                pipeline = self._get_pipeline(release_id)
                if pipeline.status != ReleaseStatus.PRECHECK_PASSED:
                    raise ValueError("前置校验未通过，无法进入审批")
            else:
                raise ValueError("前置校验未通过，无法进入审批")

        pipeline.status = ReleaseStatus.APPROVAL_PENDING
        pipeline.current_step = "approval_started"
        pipeline.updated_at = get_cst_now_str()
        self._add_history(pipeline, "APPROVAL_START", "启动审批流程")

        try:
            self.approval.start_workflow(pipeline.request)
            self._add_history(
                pipeline, "APPROVAL_FLOW",
                f"审批类型: {pipeline.request.release_type.value}, "
                f"等待审批人处理"
            )
        except Exception as e:
            pipeline.error_message = f"审批启动异常: {str(e)}"
            self.logger.error(f"[{release_id}] 审批启动异常: {e}", exc_info=True)

        self._save_pipeline(pipeline)
        return pipeline

    def approve(self, release_id: str, approver: str, role: str,
                comment: str = "") -> ReleasePipeline:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            raise ValueError(f"未找到发布流水线: {release_id}")

        try:
            workflow = self.approval.approve(release_id, approver, role, comment)
            self._add_history(
                pipeline, "APPROVAL_PASS",
                f"节点【{role}】审批通过, 审批人: {approver}, 意见: {comment or '无'}"
            )

            if workflow.overall_passed:
                pipeline.status = ReleaseStatus.APPROVAL_PASSED
                pipeline.current_step = "approval_passed"
                self._add_history(
                    pipeline, "APPROVAL_COMPLETE",
                    f"审批全部通过, 总耗时 {workflow.total_duration_hours:.2f}h"
                )
        except Exception as e:
            pipeline.error_message = f"审批操作异常: {str(e)}"
            self.logger.error(f"[{release_id}] 审批异常: {e}", exc_info=True)

        pipeline.updated_at = get_cst_now_str()
        self._save_pipeline(pipeline)
        return pipeline

    def reject(self, release_id: str, approver: str, role: str,
               reason: str) -> ReleasePipeline:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            raise ValueError(f"未找到发布流水线: {release_id}")

        try:
            self.approval.reject(release_id, approver, role, reason)
            pipeline.status = ReleaseStatus.APPROVAL_REJECTED
            pipeline.current_step = "approval_rejected"
            self._add_history(
                pipeline, "APPROVAL_REJECT",
                f"节点【{role}】拒绝, 拒绝人: {approver}, 原因: {reason}"
            )
        except Exception as e:
            pipeline.error_message = f"拒绝操作异常: {str(e)}"
            self.logger.error(f"[{release_id}] 拒绝异常: {e}", exc_info=True)

        pipeline.updated_at = get_cst_now_str()
        self._save_pipeline(pipeline)
        return pipeline

    def start_release(self, release_id: str,
                      blocking: bool = False,
                      progress_cb: Optional[Callable] = None) -> ReleasePipeline:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            raise ValueError(f"未找到发布流水线: {release_id}")

        if pipeline.status != ReleaseStatus.APPROVAL_PASSED:
            if self.approval.can_proceed_to_release(release_id):
                pipeline.status = ReleaseStatus.APPROVAL_PASSED
            else:
                raise ValueError(
                    f"当前状态 {pipeline.status.value} 不允许启动发布，需先完成审批"
                )

        pipeline.status = ReleaseStatus.GRAYSCALE_IN_PROGRESS
        pipeline.current_step = "grayscale_started"
        pipeline.updated_at = get_cst_now_str()
        self._add_history(pipeline, "RELEASE_START", "启动灰度发布流程")

        try:
            session = self.grayscale.create_session(pipeline.request)
            self.grayscale.start(release_id)
            self._add_history(
                pipeline, "GRAYSCALE_FLOW",
                f"灰度会话启动, 共 {len(session.stages)} 阶段, "
                f"目标驿站 {len(session.stations)} 个"
            )
        except Exception as e:
            pipeline.status = ReleaseStatus.RELEASE_FAILED
            pipeline.error_message = f"发布启动异常: {str(e)}"
            self._add_history(pipeline, "RELEASE_ERROR", f"异常: {str(e)}")
            self.logger.error(f"[{release_id}] 发布启动异常: {e}", exc_info=True)

        self._save_pipeline(pipeline)

        if blocking:
            self._wait_for_completion(release_id, progress_cb)
            pipeline = self._get_pipeline(release_id)

        return pipeline

    def manual_rollback(self, release_id: str, reason: str,
                        operator: str = "MANUAL") -> ReleasePipeline:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            raise ValueError(f"未找到发布流水线: {release_id}")

        event = None
        try:
            event = self.grayscale.manual_rollback(release_id, reason, operator)
            if event and event.rollback_successful:
                pipeline.status = ReleaseStatus.ROLLBACK_COMPLETED
                pipeline.current_step = "rollback_manual"
                self._add_history(
                    pipeline, "MANUAL_ROLLBACK",
                    f"手动回滚触发: {reason}, 操作人: {operator}, 结果: 成功"
                )
            else:
                pipeline.status = ReleaseStatus.RELEASE_FAILED
                pipeline.current_step = "rollback_manual_failed"
                fail_reason = (event.failure_reason if event and hasattr(event, 'failure_reason') and event.failure_reason
                               else "回滚执行失败，需要人工介入")
                pipeline.error_message = fail_reason
                self._add_history(
                    pipeline, "MANUAL_ROLLBACK_FAILED",
                    f"手动回滚失败: {reason}, 操作人: {operator}, 原因: {fail_reason}"
                )
                raise RuntimeError(fail_reason)
        except Exception as e:
            if "回滚执行失败" not in str(e):
                pipeline.error_message = f"手动回滚异常: {str(e)}"
                self.logger.error(f"[{release_id}] 手动回滚异常: {e}", exc_info=True)
                self._add_history(pipeline, "MANUAL_ROLLBACK_ERROR",
                                  f"手动回滚异常: {str(e)}")
            pipeline.updated_at = get_cst_now_str()
            self._save_pipeline(pipeline)
            raise

        pipeline.updated_at = get_cst_now_str()
        self._save_pipeline(pipeline)
        return pipeline

    def _wait_for_completion(self, release_id: str,
                             progress_cb: Optional[Callable] = None,
                             timeout: int = 3600):
        start = time.time()
        while time.time() - start < timeout:
            session = self.grayscale.get_session(release_id)
            if session:
                if progress_cb:
                    try:
                        progress_cb(session.status, session.current_stage_index)
                    except Exception:
                        pass
                if session.status in ["completed", "rollback_completed",
                                      "rollback_failed", "cancelled", "failed"]:
                    pipeline = self._get_pipeline(release_id)
                    if session.status == "completed":
                        pipeline.status = ReleaseStatus.RELEASE_COMPLETED
                        pipeline.current_step = "release_completed"
                        self._add_history(
                            pipeline, "RELEASE_COMPLETE",
                            f"灰度发布成功完成, 共影响 {len(session.all_affected_stations)} 驿站"
                        )
                    elif session.status == "rollback_completed":
                        pipeline.status = ReleaseStatus.ROLLBACK_COMPLETED
                        pipeline.current_step = "rollback_completed"
                        self._add_history(
                            pipeline, "ROLLBACK_COMPLETE",
                            f"自动回滚完成，熔断事件 {len(session.events)} 次"
                        )
                    pipeline.updated_at = get_cst_now_str()
                    self._save_pipeline(pipeline)
                    return
            time.sleep(5)

    def get_pipeline_status(self, release_id: str) -> Dict[str, Any]:
        pipeline = self._get_pipeline(release_id)
        if not pipeline:
            return {"exists": False}
        return pipeline.to_dict()

    def _add_history(self, pipeline: ReleasePipeline, action: str, detail: str):
        pipeline.step_history.append({
            "timestamp": get_cst_now_str(),
            "action": action,
            "detail": detail,
        })

    def _save_pipeline(self, pipeline: ReleasePipeline):
        try:
            db_dir = self.config.get_storage_path("db_dir")
            pdir = db_dir / "pipelines"
            pdir.mkdir(parents=True, exist_ok=True)
            file = pdir / f"{pipeline.request.release_id}.json"
            with open(file, "w", encoding="utf-8") as f:
                json.dump(pipeline.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存流水线失败: {e}")

    def _get_pipeline(self, release_id: str) -> Optional[ReleasePipeline]:
        with self._lock:
            if release_id in self._pipelines:
                return self._pipelines[release_id]
        try:
            db_dir = self.config.get_storage_path("db_dir")
            file = db_dir / "pipelines" / f"{release_id}.json"
            if not file.exists():
                return None
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            req_data = data.get("request", {})
            request = ReleaseRequest(
                release_id=req_data.get("release_id", release_id),
                version=req_data.get("version", ""),
                title=req_data.get("title", ""),
                description=req_data.get("description", ""),
                release_type=ReleaseType(req_data.get("release_type", "regular")),
                submitted_by=req_data.get("submitted_by", ""),
                submitted_at=req_data.get("submitted_at", ""),
                package_url=req_data.get("package_url", ""),
                target_stations=req_data.get("target_stations", []),
                changelog=req_data.get("changelog", ""),
                hotfix_reason=req_data.get("hotfix_reason", ""),
                rollback_version=req_data.get("rollback_version", ""),
                additional_info=req_data.get("additional_info", {}),
            )
            pipeline = ReleasePipeline(
                request=request,
                status=ReleaseStatus(data.get("status", ReleaseStatus.DRAFT.value)),
                current_step=data.get("current_step", "loaded"),
                step_history=data.get("step_history", []),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                error_message=data.get("error_message", ""),
            )
            with self._lock:
                self._pipelines[release_id] = pipeline
            return pipeline
        except Exception as e:
            self.logger.error(f"加载流水线失败: {e}")
        return None


class ScheduledTaskManager:
    def __init__(self, orchestrator: ReleasePipelineOrchestrator):
        self.config = get_config()
        self.logger = get_logger("scheduler")
        self.orchestrator = orchestrator
        self.drill = get_drill_manager()
        self.weekly = get_weekly_report_generator()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if not HAS_SCHEDULE:
            self.logger.warning("schedule库未安装，定时任务功能不可用")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="scheduler", daemon=True
        )
        self._thread.start()
        self._setup_jobs()
        self.logger.info("定时任务调度器已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("定时任务调度器已停止")

    def _setup_jobs(self):
        drill_cfg = self.config.get("drill.schedule", {})
        if self.config.get("drill.auto_execute", True):
            day = drill_cfg.get("day_of_month", 15)
            hour = drill_cfg.get("hour", 2)
            minute = drill_cfg.get("minute", 0)
            schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(
                self._check_and_run_drill, day
            )
            self.logger.info(f"已注册定时演练任务: 每月{day}日 {hour:02d}:{minute:02d}")

        wr_cfg = self.config.get("reporting.weekly_report", {})
        if wr_cfg.get("enabled", True):
            day_map = {
                "monday": 0, "tuesday": 1, "wednesday": 2,
                "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            }
            sched_day = wr_cfg.get("schedule_day", "monday").lower()
            hour = wr_cfg.get("schedule_hour", 9)
            minute = wr_cfg.get("schedule_minute", 0)
            day_idx = day_map.get(sched_day, 0)
            schedule.every().monday.at(f"{hour:02d}:{minute:02d}").do(
                self._run_weekly_report
            )
            if day_idx != 0:
                self.logger.warning("周报仅支持周一（简化实现），将在周一执行")
            self.logger.info(f"已注册周报任务: 每周{sched_day} {hour:02d}:{minute:02d}")

    def _run_loop(self):
        while self._running:
            try:
                schedule.run_pending()
            except Exception as e:
                self.logger.error(f"定时任务执行异常: {e}", exc_info=True)
            time.sleep(30)

    def _check_and_run_drill(self, target_day: int):
        now = get_cst_now()
        if now.day == target_day:
            self.logger.info(f"触发月度回滚演练: {now.strftime('%Y-%m-%d')}")
            try:
                self.drill.execute_drill()
            except Exception as e:
                self.logger.error(f"定时演练执行异常: {e}", exc_info=True)

    def _run_weekly_report(self):
        self.logger.info("触发每周报告生成")
        try:
            self.weekly.generate()
        except Exception as e:
            self.logger.error(f"周报生成异常: {e}", exc_info=True)


_orchestrator_singleton: Optional[ReleasePipelineOrchestrator] = None


def get_orchestrator() -> ReleasePipelineOrchestrator:
    global _orchestrator_singleton
    if _orchestrator_singleton is None:
        _orchestrator_singleton = ReleasePipelineOrchestrator()
    return _orchestrator_singleton
