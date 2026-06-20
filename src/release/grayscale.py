import json
import random
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import (
    get_config, ReleaseRequest, ReleaseStatus,
    GrayscaleStage, MonitoringMetric, CircuitBreakerState,
    CircuitBreakerEvent, StationInfo, dataclass_to_dict,
)
from ..core.logger import get_logger, get_cst_now_str, get_audit_logger, generate_id
from ..core.notifier import get_notifier


@dataclass
class GrayscaleReleaseSession:
    release_id: str
    version: str
    rollback_version: str
    stages: List[GrayscaleStage]
    stations: List[StationInfo]
    created_at: str
    status: str = "created"
    current_stage_index: int = -1
    circuit_breaker_state: CircuitBreakerState = CircuitBreakerState.CLOSED
    active_monitoring: bool = False
    monitoring_started_at: str = ""
    events: List[CircuitBreakerEvent] = field(default_factory=list)
    all_affected_stations: List[str] = field(default_factory=list)
    completed_at: str = ""
    final_result: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "release_id": self.release_id,
            "version": self.version,
            "rollback_version": self.rollback_version,
            "stages": [dataclass_to_dict(s) for s in self.stages],
            "stations": [dataclass_to_dict(s) for s in self.stations],
            "created_at": self.created_at,
            "status": self.status,
            "current_stage_index": self.current_stage_index,
            "circuit_breaker_state": self.circuit_breaker_state.value,
            "active_monitoring": self.active_monitoring,
            "monitoring_started_at": self.monitoring_started_at,
            "events": [dataclass_to_dict(e) for e in self.events],
            "all_affected_stations": self.all_affected_stations,
            "completed_at": self.completed_at,
            "final_result": self.final_result,
        }


class MetricsCollector:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("metrics_collector")
        self.metrics_cfg = self.config.get("release.monitoring.metrics", {})

    def collect(self, release_id: str, stations: List[StationInfo],
                window_minutes: int = 15) -> List[MonitoringMetric]:
        metrics = []
        for metric_name, m_cfg in self.metrics_cfg.items():
            value = self._simulate_metric_value(metric_name, release_id, stations)
            threshold = m_cfg.get("threshold", 0.05)
            critical_threshold = m_cfg.get("critical_threshold", threshold * 1.5)
            exceeded = value >= threshold
            critical_exceeded = value >= critical_threshold

            metric = MonitoringMetric(
                metric_name=metric_name,
                value=round(value, 4),
                threshold=threshold,
                critical_threshold=critical_threshold,
                timestamp=get_cst_now_str(),
                window_minutes=window_minutes,
                exceeded=exceeded,
                critical_exceeded=critical_exceeded,
            )
            metrics.append(metric)
        return metrics

    def get_metric_summary(self, metrics: List[MonitoringMetric]) -> Dict[str, Any]:
        summary = {}
        for m in metrics:
            label_map = {
                "pickup_failure_rate": "取件失败率",
                "terminal_offline_rate": "柜机离线率",
                "mail_abnormal_rate": "寄件异常率",
            }
            label = label_map.get(m.metric_name, m.metric_name)
            summary[label] = {
                "值": f"{m.value:.2%}",
                "阈值": f"{m.threshold:.2%}",
                "严重阈值": f"{m.critical_threshold:.2%}",
                "状态": "🔴 严重超标" if m.critical_exceeded else ("🟡 超标" if m.exceeded else "🟢 正常"),
            }
        return summary

    def _simulate_metric_value(self, metric_name: str, release_id: str,
                               stations: List[StationInfo]) -> float:
        seed = hash(f"{metric_name}_{release_id}_{int(time.time() / 300)}") % 10000
        random.seed(seed)

        base_rates = {
            "pickup_failure_rate": 0.008,
            "terminal_offline_rate": 0.025,
            "mail_abnormal_rate": 0.012,
        }
        base = base_rates.get(metric_name, 0.01)
        noise = random.uniform(-base * 0.3, base * 2.0)
        value = max(0.0, min(base * 8, base + noise))
        return round(value, 4)

    def get_metric_details(self, metric: MonitoringMetric, stations: List[StationInfo]) -> Dict[str, Any]:
        details = {
            "sample_stations": len(stations),
            "affected_count_estimate": int(len(stations) * metric.value),
        }
        if metric.exceeded:
            sample_stations = random.sample(stations, min(5, len(stations)))
            details["affected_station_samples"] = [
                {
                    "station_id": s.station_id,
                    "station_name": s.station_name,
                    "region": s.region,
                    "metric_value": round(metric.value + random.uniform(-0.005, 0.01), 4),
                }
                for s in sample_stations
            ]
        return details


class CircuitBreaker:
    def __init__(self, on_trigger: Optional[Callable[[CircuitBreakerEvent], None]] = None):
        self.config = get_config()
        self.logger = get_logger("circuit_breaker")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.cfg = self.config.get("release.circuit_breaker", {})
        self.enabled = self.cfg.get("enabled", True)
        self.auto_rollback = self.cfg.get("auto_rollback", True)
        self._on_trigger = on_trigger
        self._consecutive_failures: Dict[str, int] = {}
        self._cooldown_until: Dict[str, float] = {}

    def evaluate(self, session: GrayscaleReleaseSession,
                 metrics: List[MonitoringMetric]) -> Optional[CircuitBreakerEvent]:
        if not self.enabled:
            return None

        release_id = session.release_id
        now = time.time()

        if release_id in self._cooldown_until and now < self._cooldown_until[release_id]:
            self.logger.info(f"[{release_id}] 熔断冷却中, 跳过评估")
            return None

        critical_violations = [m for m in metrics if m.critical_exceeded]
        normal_violations = [m for m in metrics if m.exceeded and not m.critical_exceeded]

        if critical_violations:
            self._consecutive_failures[release_id] = self._consecutive_failures.get(release_id, 0) + 3
        elif normal_violations:
            self._consecutive_failures[release_id] = self._consecutive_failures.get(release_id, 0) + 1
        else:
            self._consecutive_failures[release_id] = 0
            return None

        threshold = 2 if normal_violations else 1
        if self._consecutive_failures[release_id] >= threshold:
            trigger_metric = critical_violations[0] if critical_violations else normal_violations[0]
            event = self._trigger(session, trigger_metric, metrics)
            self._consecutive_failures[release_id] = 0
            self._cooldown_until[release_id] = now + self.cfg.get("cooldown_minutes", 60) * 60
            return event

        self.logger.warning(
            f"[{release_id}] 指标异常但未达到熔断阈值 "
            f"({self._consecutive_failures[release_id]}/{threshold}): "
            f"{', '.join([f'{m.metric_name}={m.value:.2%}' for m in normal_violations + critical_violations])}"
        )
        return None

    def _trigger(self, session: GrayscaleReleaseSession,
                 trigger_metric: MonitoringMetric,
                 all_metrics: List[MonitoringMetric]) -> CircuitBreakerEvent:
        event = CircuitBreakerEvent(
            event_id=generate_id("CBE"),
            release_id=session.release_id,
            version=session.version,
            trigger_stage=session.current_stage_index + 1,
            trigger_metric=trigger_metric.metric_name,
            trigger_value=trigger_metric.value,
            threshold=trigger_metric.threshold,
            affected_stations=[s.station_id for s in session.stations[:20]],
            triggered_at=get_cst_now_str(),
        )

        session.circuit_breaker_state = CircuitBreakerState.OPEN
        session.events.append(event)
        session.status = "circuit_breaker_triggered"

        label_map = {
            "pickup_failure_rate": "取件失败率",
            "terminal_offline_rate": "柜机离线率",
            "mail_abnormal_rate": "寄件异常率",
        }
        self.logger.critical(
            f"[{session.release_id}] 🚨 熔断触发! 指标: {label_map.get(trigger_metric.metric_name, trigger_metric.metric_name)} "
            f"= {trigger_metric.value:.2%}, 阈值: {trigger_metric.threshold:.2%}, "
            f"阶段: {event.trigger_stage}"
        )

        self.audit.log(
            operator="SYSTEM",
            action="CIRCUIT_BREAKER_TRIGGERED",
            target_id=session.release_id,
            target_type="RELEASE",
            before_state={"status": session.status, "circuit_breaker": "closed"},
            after_state={
                "status": "circuit_breaker_triggered",
                "circuit_breaker": "open",
                "event_id": event.event_id,
                "trigger_metric": trigger_metric.metric_name,
                "trigger_value": trigger_metric.value,
                "trigger_stage": event.trigger_stage,
            },
        )

        if self._on_trigger:
            try:
                self._on_trigger(event)
            except Exception as e:
                self.logger.error(f"熔断回调执行异常: {e}")

        return event


class RollbackEngine:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("rollback_engine")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.cfg = self.config.get("release.circuit_breaker", {})

    def execute(self, session: GrayscaleReleaseSession,
                event: CircuitBreakerEvent,
                is_drill: bool = False) -> Tuple[bool, float]:
        self.logger.warning(
            f"[{session.release_id}] 开始{'[演练模式] ' if is_drill else ''}"
            f"执行自动回滚: {session.version} → {session.rollback_version}"
        )

        event.rollback_started = get_cst_now_str()
        start_ts = time.time()

        success = True
        try:
            steps = self._build_rollback_steps(session, event)
            for step_idx, step in enumerate(steps):
                self.logger.info(f"[{session.release_id}] 回滚步骤 {step_idx + 1}/{len(steps)}: {step['name']}")
                step_success = self._execute_step(session, step, is_drill)
                if not step_success and step.get("critical", True):
                    self.logger.error(f"[{session.release_id}] 关键步骤失败: {step['name']}")
                    success = False
                    break
                time.sleep(random.uniform(0.1, 0.5))

            event.rollback_duration_seconds = round(time.time() - start_ts, 2)
            event.rollback_completed = get_cst_now_str()
            event.rollback_successful = success

            session.status = "rollback_completed" if success else "rollback_failed"
            session.active_monitoring = False

            self.audit.log(
                operator="SYSTEM" if not is_drill else "DRILL",
                action="ROLLBACK_EXECUTED",
                target_id=session.release_id,
                target_type="RELEASE",
                before_state={"status": "circuit_breaker_triggered"},
                after_state={
                    "status": session.status,
                    "rollback_from": session.version,
                    "rollback_to": session.rollback_version,
                    "duration_seconds": event.rollback_duration_seconds,
                    "success": success,
                    "is_drill": is_drill,
                },
            )

            if success:
                self.logger.info(
                    f"[{session.release_id}] 回滚完成, 耗时 {event.rollback_duration_seconds}s"
                )
            else:
                self.logger.error(f"[{session.release_id}] 回滚失败，需要人工介入")

            self._send_rollback_report(session, event, is_drill)

            return success, event.rollback_duration_seconds

        except Exception as e:
            event.rollback_duration_seconds = round(time.time() - start_ts, 2)
            event.rollback_completed = get_cst_now_str()
            event.rollback_successful = False
            self.logger.error(f"[{session.release_id}] 回滚过程异常: {e}", exc_info=True)
            return False, event.rollback_duration_seconds

    def _build_rollback_steps(self, session: GrayscaleReleaseSession,
                              event: CircuitBreakerEvent) -> List[Dict]:
        steps = [
            {
                "name": "暂停所有灰度发布流水线",
                "critical": True,
                "action": "pause_pipelines",
            },
            {
                "name": f"切换版本标记: {session.version} → {session.rollback_version}",
                "critical": True,
                "action": "swap_version_tag",
            },
            {
                "name": "回滚API网关路由权重",
                "critical": True,
                "action": "rollback_gateway",
            },
            {
                "name": "回滚配置中心版本",
                "critical": True,
                "action": "rollback_config",
            },
            {
                "name": "通知柜机/PDA终端拉取旧版资源",
                "critical": False,
                "action": "notify_terminals",
            },
            {
                "name": "重启核心服务实例",
                "critical": True if self.cfg.get("restart_services_on_rollback") else False,
                "action": "restart_services",
            },
            {
                "name": "等待服务健康检查通过",
                "critical": True,
                "action": "wait_healthcheck",
            },
            {
                "name": "验证核心链路恢复正常",
                "critical": True,
                "action": "verify_core_paths",
            },
            {
                "name": "重启业务监控并设定观察窗口",
                "critical": False,
                "action": "restart_monitoring",
            },
        ]
        return steps

    def _execute_step(self, session: GrayscaleReleaseSession, step: Dict, is_drill: bool) -> bool:
        seed = hash(f"{session.release_id}_{step['action']}_{is_drill}") % 100
        random.seed(seed)
        failure_prob = 0.0 if is_drill else 0.02
        return random.random() > failure_prob

    def _send_rollback_report(self, session: GrayscaleReleaseSession,
                              event: CircuitBreakerEvent, is_drill: bool):
        label_map = {
            "pickup_failure_rate": "取件失败率",
            "terminal_offline_rate": "柜机离线率",
            "mail_abnormal_rate": "寄件异常率",
        }

        affected_stations_detail = []
        for sid in event.affected_stations[:15]:
            station = next((s for s in session.stations if s.station_id == sid), None)
            if station:
                affected_stations_detail.append({
                    "驿站ID": station.station_id,
                    "驿站名称": station.station_name,
                    "区域": station.region,
                    "类型": station.station_type,
                    "日均单量": station.daily_volume,
                })

        context = {
            "title": f"{'[演练] ' if is_drill else '🚨 '}自动回滚完成报告",
            "release_id": session.release_id,
            "release_title": session.release_id,
            "version": session.version,
            "release_type": f"{'演练模式' if is_drill else '正式发布'}",
            "operator": "SYSTEM",
            "status": "✅ 回滚成功" if event.rollback_successful else "❌ 回滚失败需人工介入",
            "description": f"熔断触发自动回滚: {session.version} → {session.rollback_version}",
            "circuit_breaker_info": {
                "事件ID": event.event_id,
                "触发阶段": f"第 {event.trigger_stage} 阶段",
                "触发指标": label_map.get(event.trigger_metric, event.trigger_metric),
                "触发值": f"{event.trigger_value:.2%}",
                "安全阈值": f"{event.threshold:.2%}",
                "触发时间": event.triggered_at,
            },
            "rollback_info": {
                "回滚开始": event.rollback_started,
                "回滚完成": event.rollback_completed,
                "回滚耗时(秒)": event.rollback_duration_seconds,
                "回滚版本": session.rollback_version,
                "回滚结果": "成功" if event.rollback_successful else "失败",
                "影响驿站总数": len(event.affected_stations),
            },
            "grayscale_info": {
                "影响驿站详情": affected_stations_detail,
            },
            "action_required": "" if event.rollback_successful else "回滚未完全成功，请立即人工介入处理！",
        }

        event.report_generated = True

        try:
            self.notifier.send(
                template_key="rollback_completed",
                context=context,
                priority="urgent",
            )
        except Exception as e:
            self.logger.error(f"发送回滚报告异常: {e}")


class GrayscaleReleaseEngine:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("grayscale_engine")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.metrics_collector = MetricsCollector()
        self.circuit_breaker = CircuitBreaker(on_trigger=self._on_circuit_triggered)
        self.rollback_engine = RollbackEngine()
        self._sessions: Dict[str, GrayscaleReleaseSession] = {}
        self._monitor_threads: Dict[str, threading.Thread] = {}
        self._monitor_stop_flags: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create_session(self, request: ReleaseRequest,
                       stations: Optional[List[StationInfo]] = None) -> GrayscaleReleaseSession:
        grayscale_cfg = self.config.get("release.grayscale", {})
        stages_cfg = grayscale_cfg.get("default_stages", [])

        stages = [
            GrayscaleStage(
                stage=s.get("stage"),
                name=s.get("name", f"阶段{s.get('stage')}"),
                station_types=s.get("station_types", []),
                region_priority=s.get("region_priority", []),
                scale_percent=s.get("scale_percent", 0),
                observation_minutes=s.get("observation_minutes", 30),
            )
            for s in stages_cfg
        ]

        if stations is None:
            stations = self._generate_mock_stations(request)

        session = GrayscaleReleaseSession(
            release_id=request.release_id,
            version=request.version,
            rollback_version=request.rollback_version or "stable-last",
            stages=stages,
            stations=stations,
            created_at=get_cst_now_str(),
        )

        with self._lock:
            self._sessions[request.release_id] = session

        self._save_session(session)

        self.audit.log(
            operator=request.submitted_by,
            action="GRAYSCALE_SESSION_CREATED",
            target_id=request.release_id,
            target_type="RELEASE",
            before_state={"release_status": ReleaseStatus.APPROVAL_PASSED.value},
            after_state={
                "release_status": ReleaseStatus.GRAYSCALE_IN_PROGRESS.value,
                "stages_count": len(stages),
                "target_stations": len(stations),
            },
        )

        self.logger.info(
            f"[{request.release_id}] 灰度会话创建, 阶段数: {len(stages)}, "
            f"目标驿站: {len(stations)}"
        )
        return session

    def start(self, release_id: str) -> GrayscaleReleaseSession:
        with self._lock:
            session = self._sessions.get(release_id)
        if not session:
            raise ValueError(f"未找到灰度会话: {release_id}")

        session.status = "in_progress"
        self._advance_to_stage(session, 0)
        self._start_monitoring(session)
        self._save_session(session)
        return session

    def _advance_to_stage(self, session: GrayscaleReleaseSession, stage_index: int):
        if stage_index >= len(session.stages):
            self._complete_session(session, success=True)
            return

        stage = session.stages[stage_index]
        session.current_stage_index = stage_index
        stage.status = "in_progress"
        stage.started_at = get_cst_now_str()

        stage_stations = self._select_stations_for_stage(session, stage)
        stage.affected_stations = stage_stations
        session.all_affected_stations.extend(stage_stations)

        self.logger.info(
            f"[{session.release_id}] 进入灰度阶段 {stage.stage}: {stage.name} "
            f"({len(stage_stations)} 个驿站, 观察 {stage.observation_minutes} 分钟)"
        )

        self.audit.log(
            operator="SYSTEM",
            action="GRAYSCALE_STAGE_STARTED",
            target_id=session.release_id,
            target_type="RELEASE",
            before_state={"current_stage": stage_index - 1 if stage_index > 0 else -1},
            after_state={
                "current_stage": stage_index,
                "stage_name": stage.name,
                "station_count": len(stage_stations),
                "observation_minutes": stage.observation_minutes,
            },
        )

        self._notify_stage_started(session, stage, stage_stations)

    def _select_stations_for_stage(self, session: GrayscaleReleaseSession,
                                   stage: GrayscaleStage) -> List[str]:
        selected_ids = set(session.all_affected_stations)
        remaining = [s for s in session.stations if s.station_id not in selected_ids]

        prioritized = []
        for region in stage.region_priority:
            region_stations = [s for s in remaining if region in s.region.lower() or region in s.region_tier.lower()]
            for stype in stage.station_types:
                type_stations = [s for s in region_stations if s.station_type == stype]
                prioritized.extend(type_stations)

        other = [s for s in remaining if s not in prioritized]
        prioritized.extend(other)

        total = len(session.stations)
        target_count = int(total * stage.scale_percent / 100)
        selected = prioritized[:max(1, target_count)]
        return [s.station_id for s in selected]

    def _start_monitoring(self, session: GrayscaleReleaseSession):
        session.active_monitoring = True
        session.monitoring_started_at = get_cst_now_str()

        stop_event = threading.Event()
        self._monitor_stop_flags[session.release_id] = stop_event

        thread = threading.Thread(
            target=self._monitor_loop,
            args=(session, stop_event),
            name=f"monitor-{session.release_id}",
            daemon=True,
        )
        self._monitor_threads[session.release_id] = thread
        thread.start()
        self.logger.info(f"[{session.release_id}] 监控线程已启动")

    def _monitor_loop(self, session: GrayscaleReleaseSession, stop_event: threading.Event):
        interval = self.config.get("release.monitoring.interval_seconds", 300)
        observation_counter = 0
        current_stage_target = 0
        if session.current_stage_index >= 0 and session.current_stage_index < len(session.stages):
            current_stage_target = session.stages[session.current_stage_index].observation_minutes * 60 // interval

        while not stop_event.is_set():
            try:
                if session.circuit_breaker_state == CircuitBreakerState.OPEN:
                    self.logger.info(f"[{session.release_id}] 熔断已触发，停止监控循环")
                    break

                if session.status in ["completed", "rollback_completed", "cancelled"]:
                    break

                metrics = self.metrics_collector.collect(
                    session.release_id, session.stations
                )

                metric_parts = []
                for m in metrics:
                    flag = "(!)" if m.exceeded else ""
                    metric_parts.append(f"{m.metric_name}={m.value:.2%}{flag}")
                self.logger.info(
                    f"[{session.release_id}] 监控采样 "
                    f"[{get_cst_now_str()}]: "
                    f"{', '.join(metric_parts)}"
                )

                event = self.circuit_breaker.evaluate(session, metrics)
                if event:
                    stop_event.set()
                    if self.circuit_breaker.auto_rollback:
                        self.rollback_engine.execute(session, event)
                    break

                observation_counter += 1
                if session.current_stage_index >= 0 and observation_counter >= current_stage_target:
                    current_stage = session.stages[session.current_stage_index]
                    current_stage.status = "completed"
                    current_stage.completed_at = get_cst_now_str()
                    self.logger.info(
                        f"[{session.release_id}] 阶段 {current_stage.stage} 观察完成, 进入下一阶段"
                    )
                    next_stage_idx = session.current_stage_index + 1
                    self._advance_to_stage(session, next_stage_idx)
                    observation_counter = 0
                    if next_stage_idx < len(session.stages):
                        current_stage_target = session.stages[next_stage_idx].observation_minutes * 60 // interval
                    else:
                        break
                    self._save_session(session)

                self._save_session(session)

            except Exception as e:
                self.logger.error(f"[{session.release_id}] 监控循环异常: {e}", exc_info=True)

            for _ in range(min(60, interval)):
                if stop_event.is_set():
                    break
                time.sleep(min(1, interval / 60))

        session.active_monitoring = False
        self.logger.info(f"[{session.release_id}] 监控线程已退出")

    def _on_circuit_triggered(self, event: CircuitBreakerEvent):
        pass

    def _complete_session(self, session: GrayscaleReleaseSession, success: bool):
        session.status = "completed" if success else "failed"
        session.completed_at = get_cst_now_str()
        session.final_result = "success" if success else "failed"
        session.active_monitoring = False

        if success:
            for stage in session.stages:
                if stage.status == "in_progress":
                    stage.status = "completed"
                    stage.completed_at = get_cst_now_str()

        self.audit.log(
            operator="SYSTEM",
            action="GRAYSCALE_RELEASE_COMPLETED",
            target_id=session.release_id,
            target_type="RELEASE",
            before_state={"status": "in_progress"},
            after_state={
                "status": ReleaseStatus.RELEASE_COMPLETED.value if success else ReleaseStatus.RELEASE_FAILED.value,
                "total_affected_stations": len(session.all_affected_stations),
                "final_result": session.final_result,
            },
        )

        self.logger.info(
            f"[{session.release_id}] 灰度发布{'成功完成' if success else '失败结束'}, "
            f"共影响 {len(session.all_affected_stations)} 个驿站"
        )

        self._save_session(session)
        self._notify_release_completed(session, success)

    def manual_rollback(self, release_id: str, reason: str,
                        operator: str = "MANUAL") -> Optional[CircuitBreakerEvent]:
        with self._lock:
            session = self._sessions.get(release_id)
        if not session:
            raise ValueError(f"未找到灰度会话: {release_id}")

        if release_id in self._monitor_stop_flags:
            self._monitor_stop_flags[release_id].set()

        event = CircuitBreakerEvent(
            event_id=generate_id("CBE"),
            release_id=release_id,
            version=session.version,
            trigger_stage=max(0, session.current_stage_index) + 1,
            trigger_metric="manual_trigger",
            trigger_value=0.0,
            threshold=0.0,
            affected_stations=list(session.all_affected_stations),
            triggered_at=get_cst_now_str(),
        )
        session.events.append(event)
        session.circuit_breaker_state = CircuitBreakerState.OPEN

        self.audit.log(
            operator=operator,
            action="MANUAL_ROLLBACK_REQUESTED",
            target_id=release_id,
            target_type="RELEASE",
            before_state={"status": session.status},
            after_state={"status": "manual_rollback_requested", "reason": reason},
        )

        success, duration = self.rollback_engine.execute(session, event)
        self._save_session(session)
        return event

    def _generate_mock_stations(self, request: ReleaseRequest) -> List[StationInfo]:
        regions = [
            ("east_first_tier", "华东-上海", ["commercial_core", "university", "transport_hub"]),
            ("south_first_tier", "华南-深圳", ["commercial_core", "residential", "transport_hub"]),
            ("north_first_tier", "华北-北京", ["commercial_core", "university", "community_normal"]),
            ("east", "华东-杭州", ["community_normal", "residential", "commercial_core"]),
            ("south", "华南-广州", ["community_normal", "residential"]),
            ("central", "华中-武汉", ["community_normal", "university", "residential"]),
            ("southwest", "西南-成都", ["community_normal", "residential", "rural"]),
            ("northwest", "西北-西安", ["rural", "remote_low_volume", "community_normal"]),
            ("northeast", "东北-沈阳", ["rural", "remote_low_volume", "community_normal"]),
        ]
        stations = []
        total = 200
        for i in range(total):
            region_info = regions[i % len(regions)]
            type_pool = region_info[2]
            volume_map = {
                "commercial_core": (1500, 5000),
                "university": (1000, 3500),
                "transport_hub": (2000, 6000),
                "residential": (300, 1200),
                "community_normal": (200, 800),
                "rural": (50, 200),
                "remote_low_volume": (20, 100),
            }
            stype = random.choice(type_pool)
            vol_range = volume_map.get(stype, (100, 500))
            stations.append(StationInfo(
                station_id=f"S{10000 + i}",
                station_name=f"{region_info[1]}{i+1}号店",
                station_type=stype,
                region=region_info[1].split("-")[0],
                region_tier=region_info[0],
                daily_volume=random.randint(*vol_range),
                current_version=request.rollback_version or "v2.3.1",
            ))
        return stations

    def _notify_stage_started(self, session: GrayscaleReleaseSession,
                              stage: GrayscaleStage, station_ids: List[str]):
        station_details = []
        for sid in station_ids[:10]:
            station = next((s for s in session.stations if s.station_id == sid), None)
            if station:
                station_details.append({
                    "ID": station.station_id,
                    "名称": station.station_name,
                    "区域": station.region,
                    "类型": station.station_type,
                    "单量": station.daily_volume,
                })

        context = {
            "title": f"灰度阶段 {stage.stage} 开始 - {stage.name}",
            "release_id": session.release_id,
            "release_title": session.release_id,
            "version": session.version,
            "release_type": "灰度发布",
            "operator": "SYSTEM",
            "status": f"第 {stage.stage}/{len(session.stages)} 阶段发布中",
            "grayscale_info": {
                "阶段名称": stage.name,
                "驿站类型": stage.station_types,
                "区域优先级": stage.region_priority,
                "发布比例": f"{stage.scale_percent}%",
                "本轮驿站数": len(station_ids),
                "观察窗口": f"{stage.observation_minutes} 分钟",
                "驿站详情 (前10)": station_details,
            },
        }
        try:
            self.notifier.send("grayscale_started", context)
        except Exception as e:
            self.logger.error(f"发送阶段开始通知异常: {e}")

    def _notify_release_completed(self, session: GrayscaleReleaseSession, success: bool):
        stage_details = []
        for s in session.stages:
            stage_details.append({
                "阶段": f"{s.stage} - {s.name}",
                "状态": s.status,
                "开始时间": s.started_at,
                "完成时间": s.completed_at,
                "驿站数": len(s.affected_stations),
            })

        context = {
            "title": f"{'✅' if success else '❌'} 灰度发布{'成功完成' if success else '结束'}",
            "release_id": session.release_id,
            "release_title": session.release_id,
            "version": session.version,
            "status": "全部驿站发布完成" if success else "发布流程异常结束",
            "grayscale_info": {
                "总阶段数": len(session.stages),
                "总影响驿站数": len(session.all_affected_stations),
                "开始时间": session.created_at,
                "完成时间": session.completed_at,
                "阶段详情": stage_details,
            },
        }
        try:
            self.notifier.send("release_completed", context)
        except Exception as e:
            self.logger.error(f"发送发布完成通知异常: {e}")

    def _save_session(self, session: GrayscaleReleaseSession):
        try:
            db_dir = self.config.get_storage_path("db_dir")
            rel_dir = db_dir / "release"
            rel_dir.mkdir(parents=True, exist_ok=True)
            file = rel_dir / f"{session.release_id}.json"
            with open(file, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存灰度会话失败: {e}")

    def get_session(self, release_id: str) -> Optional[GrayscaleReleaseSession]:
        with self._lock:
            if release_id in self._sessions:
                return self._sessions[release_id]
        try:
            db_dir = self.config.get_storage_path("db_dir")
            file = db_dir / "release" / f"{release_id}.json"
            if not file.exists():
                return None
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            stages = []
            for s in data.get("stages", []):
                stages.append(GrayscaleStage(**s))
            stations = []
            for s in data.get("stations", []):
                stations.append(StationInfo(**s))
            events = []
            for e in data.get("events", []):
                events.append(CircuitBreakerEvent(**e))
            session = GrayscaleReleaseSession(
                release_id=data["release_id"],
                version=data["version"],
                rollback_version=data.get("rollback_version", ""),
                stages=stages,
                stations=stations,
                created_at=data.get("created_at", ""),
                status=data.get("status", "created"),
                current_stage_index=data.get("current_stage_index", -1),
                circuit_breaker_state=CircuitBreakerState(data.get("circuit_breaker_state", "closed")),
                active_monitoring=data.get("active_monitoring", False),
                monitoring_started_at=data.get("monitoring_started_at", ""),
                events=events,
                all_affected_stations=data.get("all_affected_stations", []),
                completed_at=data.get("completed_at", ""),
                final_result=data.get("final_result", ""),
            )
            with self._lock:
                self._sessions[release_id] = session
            return session
        except Exception as e:
            self.logger.error(f"加载灰度会话失败: {e}")
        return None

    def stop(self, release_id: str):
        if release_id in self._monitor_stop_flags:
            self._monitor_stop_flags[release_id].set()
        with self._lock:
            session = self._sessions.get(release_id)
            if session:
                session.status = "cancelled"
                session.active_monitoring = False


def get_grayscale_engine() -> GrayscaleReleaseEngine:
    return GrayscaleReleaseEngine()
