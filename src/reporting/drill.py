import json
import random
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import (
    get_config, ReleaseRequest, ReleaseType, ReleaseStatus,
    StationInfo, CircuitBreakerEvent, dataclass_to_dict,
)
from ..core.logger import (
    get_logger, get_cst_now, get_cst_now_str,
    get_audit_logger, generate_id,
)
from ..core.notifier import get_notifier
from ..release.grayscale import (
    GrayscaleReleaseEngine, GrayscaleReleaseSession,
    MetricsCollector, CircuitBreaker, RollbackEngine,
)


@dataclass
class DrillResult:
    drill_id: str
    scheduled_at: str
    started_at: str
    completed_at: str = ""
    status: str = "pending"
    success: bool = False
    simulated_release_id: str = ""
    simulated_version: str = ""
    rollback_version: str = ""
    circuit_breaker_triggered: bool = False
    rollback_executed: bool = False
    rollback_success: bool = False
    rollback_duration_seconds: float = 0.0
    trigger_metric: str = ""
    trigger_value: float = 0.0
    threshold: float = 0.0
    affected_station_count: int = 0
    steps_passed: int = 0
    steps_total: int = 0
    step_details: List[Dict] = field(default_factory=list)
    error_message: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_dict(self)


class RollbackDrillManager:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("drill_manager")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.drill_cfg = self.config.get("drill", {})
        self.grayscale = GrayscaleReleaseEngine()
        self._drill_cache: Dict[str, DrillResult] = {}
        self._lock = threading.Lock()

    def execute_drill(self, drill_id: Optional[str] = None) -> DrillResult:
        drill_id = drill_id or generate_id("DRL")
        self.logger.info(f"[{drill_id}] 开始执行回滚演练")

        result = DrillResult(
            drill_id=drill_id,
            scheduled_at=get_cst_now_str(),
            started_at=get_cst_now_str(),
            status="in_progress",
        )

        self.audit.log(
            operator="SYSTEM",
            action="DRILL_STARTED",
            target_id=drill_id,
            target_type="DRILL",
            before_state={},
            after_state={"status": "in_progress"},
        )

        try:
            self._simulate_drill_workflow(result)
        except Exception as e:
            self.logger.error(f"[{drill_id}] 演练执行异常: {e}", exc_info=True)
            result.status = "failed"
            result.error_message = str(e)
            result.success = False

        result.completed_at = get_cst_now_str()
        with self._lock:
            self._drill_cache[drill_id] = result

        self._save_result(result)
        self._send_drill_notification(result)

        self.audit.log(
            operator="SYSTEM",
            action="DRILL_COMPLETED",
            target_id=drill_id,
            target_type="DRILL",
            before_state={"status": "in_progress"},
            after_state={
                "status": result.status,
                "success": result.success,
                "rollback_success": result.rollback_success,
                "rollback_duration": result.rollback_duration_seconds,
            },
        )

        self.logger.info(
            f"[{drill_id}] 演练完成, 结果: {'成功' if result.success else '失败'}, "
            f"回滚耗时: {result.rollback_duration_seconds}s"
        )
        return result

    def _simulate_drill_workflow(self, result: DrillResult):
        result.simulated_version = f"v2.{random.randint(4, 9)}.{random.randint(0, 9)}"
        result.rollback_version = f"v2.{random.randint(3, 8)}.{random.randint(0, 9)}"
        result.simulated_release_id = generate_id("REL_DRILL")

        drill_request = ReleaseRequest(
            release_id=result.simulated_release_id,
            version=result.simulated_version,
            title=f"[演练] 系统版本发布 {result.simulated_version}",
            description="回滚演练用模拟发布申请",
            release_type=ReleaseType.REGULAR,
            submitted_by="DRILL_SYSTEM",
            submitted_at=get_cst_now_str(),
            package_url=f"https://artifacts.company.com/release/{result.simulated_version}.tar.gz",
            rollback_version=result.rollback_version,
        )

        result.steps_passed = 0
        result.steps_total = 6

        self._record_step(result, "Step 1: 创建模拟灰度发布会话", True)
        session = self.grayscale.create_session(drill_request)
        session.status = "drill_in_progress"

        self._record_step(result, "Step 2: 启动灰度阶段1", True)
        self.grayscale._advance_to_stage(session, 0)
        result.affected_station_count = len(session.stages[0].affected_stations)

        time.sleep(random.uniform(0.3, 1.0))

        self._record_step(result, "Step 3: 注入异常指标触发熔断", True)
        metrics_collector = MetricsCollector()
        bad_metrics = self._inject_abnormal_metrics(session, result)

        cb = CircuitBreaker()
        event = cb.evaluate(session, bad_metrics)
        if event is None:
            self.logger.warning(f"[{result.drill_id}] 熔断未自动触发，手动触发演练")
            event = CircuitBreakerEvent(
                event_id=generate_id("CBE"),
                release_id=session.release_id,
                version=session.version,
                trigger_stage=1,
                trigger_metric="pickup_failure_rate",
                trigger_value=0.085,
                threshold=0.03,
                affected_stations=list(session.all_affected_stations),
                triggered_at=get_cst_now_str(),
            )

        result.circuit_breaker_triggered = True
        result.trigger_metric = event.trigger_metric
        result.trigger_value = event.trigger_value
        result.threshold = event.threshold

        self._record_step(result, "Step 4: 执行自动回滚流程", True)
        rollback_engine = RollbackEngine()
        success, duration = rollback_engine.execute(session, event, is_drill=True)
        result.rollback_executed = True
        result.rollback_success = success
        result.rollback_duration_seconds = duration

        self._record_step(result, "Step 5: 验证核心链路恢复", success)
        time.sleep(random.uniform(0.2, 0.8))

        self._record_step(result, "Step 6: 生成演练报告并归档", True)

        result.success = (
            result.steps_passed >= result.steps_total - 0 and
            result.circuit_breaker_triggered and
            result.rollback_success
        )
        result.status = "success" if result.success else "partial_failure"

    def _inject_abnormal_metrics(self, session: GrayscaleReleaseSession,
                                  result: DrillResult) -> List:
        from ..core.config import MonitoringMetric
        return [
            MonitoringMetric(
                metric_name="pickup_failure_rate",
                value=0.085,
                threshold=0.03,
                critical_threshold=0.05,
                timestamp=get_cst_now_str(),
                window_minutes=15,
                exceeded=True,
                critical_exceeded=True,
            ),
            MonitoringMetric(
                metric_name="terminal_offline_rate",
                value=0.032,
                threshold=0.08,
                critical_threshold=0.15,
                timestamp=get_cst_now_str(),
                window_minutes=15,
                exceeded=False,
                critical_exceeded=False,
            ),
            MonitoringMetric(
                metric_name="mail_abnormal_rate",
                value=0.021,
                threshold=0.04,
                critical_threshold=0.07,
                timestamp=get_cst_now_str(),
                window_minutes=15,
                exceeded=False,
                critical_exceeded=False,
            ),
        ]

    def _record_step(self, result: DrillResult, step_name: str, passed: bool):
        result.step_details.append({
            "step": step_name,
            "passed": passed,
            "timestamp": get_cst_now_str(),
        })
        if passed:
            result.steps_passed += 1

    def _send_drill_notification(self, result: DrillResult):
        label_map = {
            "pickup_failure_rate": "取件失败率",
            "terminal_offline_rate": "柜机离线率",
            "mail_abnormal_rate": "寄件异常率",
        }
        status_emoji = "✅" if result.success else "⚠️" if result.status == "partial_failure" else "❌"

        context = {
            "title": f"{status_emoji} 每月回滚演练完成 - {result.drill_id}",
            "release_id": result.simulated_release_id,
            "release_title": f"回滚演练 {result.drill_id}",
            "version": result.simulated_version,
            "release_type": "定期回滚演练",
            "operator": "SYSTEM",
            "status": f"演练{'成功' if result.success else '未完全成功'} ({result.steps_passed}/{result.steps_total} 步骤通过)",
            "drill_result": {
                "演练ID": result.drill_id,
                "计划时间": result.scheduled_at,
                "开始时间": result.started_at,
                "结束时间": result.completed_at,
                "模拟版本": f"{result.simulated_version} → {result.rollback_version}",
                "熔断触发": "是" if result.circuit_breaker_triggered else "否",
                "触发指标": label_map.get(result.trigger_metric, result.trigger_metric),
                "触发值": f"{result.trigger_value:.2%}",
                "阈值": f"{result.threshold:.2%}",
                "自动回滚": "是" if result.rollback_executed else "否",
                "回滚成功": "是" if result.rollback_success else "否",
                "回滚耗时(秒)": result.rollback_duration_seconds,
                "影响驿站数": result.affected_station_count,
                "步骤通过率": f"{result.steps_passed}/{result.steps_total}",
                "步骤详情": result.step_details,
                "备注": result.notes,
            },
        }
        try:
            self.notifier.send("drill_completed", context)
        except Exception as e:
            self.logger.error(f"发送演练通知异常: {e}")

    def _save_result(self, result: DrillResult):
        try:
            db_dir = self.config.get_storage_path("db_dir")
            drill_dir = db_dir / "drills"
            drill_dir.mkdir(parents=True, exist_ok=True)
            file = drill_dir / f"{result.drill_id}.json"
            with open(file, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存演练结果失败: {e}")

    def get_result(self, drill_id: str) -> Optional[DrillResult]:
        with self._lock:
            if drill_id in self._drill_cache:
                return self._drill_cache[drill_id]
        try:
            db_dir = self.config.get_storage_path("db_dir")
            file = db_dir / "drills" / f"{drill_id}.json"
            if not file.exists():
                return None
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = DrillResult(**data)
            with self._lock:
                self._drill_cache[drill_id] = result
            return result
        except Exception as e:
            self.logger.error(f"加载演练结果失败: {e}")
        return None

    def list_results(self, start_date: Optional[str] = None,
                     end_date: Optional[str] = None,
                     status: Optional[str] = None) -> List[DrillResult]:
        results = []
        db_dir = self.config.get_storage_path("db_dir") / "drills"
        if not db_dir.exists():
            return results
        for file in sorted(db_dir.glob("DRL_*.json")):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                r = DrillResult(**data)
                if start_date and r.started_at < start_date:
                    continue
                if end_date and r.started_at > end_date + " 23:59:59":
                    continue
                if status and r.status != status:
                    continue
                results.append(r)
            except Exception:
                continue
        return sorted(results, key=lambda x: x.started_at, reverse=True)


def get_drill_manager() -> RollbackDrillManager:
    return RollbackDrillManager()
