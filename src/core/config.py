import os
import yaml
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum


class ReleaseType(str, Enum):
    REGULAR = "regular"
    HOTFIX = "hotfix"


class ReleaseStatus(str, Enum):
    DRAFT = "draft"
    PRECHECK_PENDING = "precheck_pending"
    PRECHECK_PASSED = "precheck_passed"
    PRECHECK_FAILED = "precheck_failed"
    APPROVAL_PENDING = "approval_pending"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_PASSED = "approval_passed"
    GRAYSCALE_IN_PROGRESS = "grayscale_in_progress"
    RELEASE_COMPLETED = "release_completed"
    ROLLBACK_IN_PROGRESS = "rollback_in_progress"
    ROLLBACK_COMPLETED = "rollback_completed"
    RELEASE_FAILED = "release_failed"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


@dataclass
class StationInfo:
    station_id: str
    station_name: str
    station_type: str
    region: str
    region_tier: str
    daily_volume: int
    contact_person: str = ""
    contact_phone: str = ""
    current_version: str = ""
    online_status: bool = True


@dataclass
class ReleaseRequest:
    release_id: str
    version: str
    title: str
    description: str
    release_type: ReleaseType
    submitted_by: str
    submitted_at: str
    package_url: str
    target_stations: list = field(default_factory=list)
    changelog: str = ""
    hotfix_reason: str = ""
    rollback_version: str = ""
    additional_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PrecheckResult:
    check_name: str
    passed: bool
    score: float
    threshold: float
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""
    checked_at: str = ""
    duration_seconds: float = 0.0


@dataclass
class ApprovalNode:
    role: str
    name: str
    description: str
    required: bool
    approvers: list
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: str = ""
    approved_at: str = ""
    comment: str = ""


@dataclass
class GrayscaleStage:
    stage: int
    name: str
    station_types: list
    region_priority: list
    scale_percent: int
    observation_minutes: int
    status: str = "pending"
    started_at: str = ""
    completed_at: str = ""
    affected_stations: list = field(default_factory=list)


@dataclass
class MonitoringMetric:
    metric_name: str
    value: float
    threshold: float
    critical_threshold: float
    timestamp: str
    window_minutes: int
    exceeded: bool = False
    critical_exceeded: bool = False


@dataclass
class CircuitBreakerEvent:
    event_id: str
    release_id: str
    version: str
    trigger_stage: int
    trigger_metric: str
    trigger_value: float
    threshold: float
    affected_stations: list
    triggered_at: str
    rollback_started: str = ""
    rollback_completed: str = ""
    rollback_successful: bool = False
    rollback_duration_seconds: float = 0.0
    report_generated: bool = False


@dataclass
class AuditLog:
    log_id: str
    timestamp: str
    operator: str
    action: str
    target_id: str
    target_type: str
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    ip_address: str = ""
    user_agent: str = ""
    hash: str = ""

    def compute_hash(self, hash_algorithm: str = "sha256") -> str:
        data = json.dumps({
            "log_id": self.log_id,
            "timestamp": self.timestamp,
            "operator": self.operator,
            "action": self.action,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "before_state": self.before_state,
            "after_state": self.after_state,
        }, sort_keys=True)
        return hashlib.new(hash_algorithm, data.encode("utf-8")).hexdigest()


class ConfigManager:
    _instance: Optional["ConfigManager"] = None
    _config: Dict[str, Any] = {}
    _config_path: Path = None

    def __new__(cls, config_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize(config_path)
        return cls._instance

    def _initialize(self, config_path: Optional[str]):
        base_dir = Path(__file__).parent.parent.parent
        self._config_path = Path(config_path) if config_path else base_dir / "config" / "settings.yaml"
        self._load_config()
        self._ensure_directories()

    def _load_config(self):
        if not self._config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._config_path}")
        with open(self._config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    def _ensure_directories(self):
        storage_config = self.get("storage", {})
        for key in ["base_dir", "audit_log_dir", "report_dir", "db_dir", "temp_dir"]:
            dir_path = storage_config.get(key)
            if dir_path:
                base = self._config_path.parent.parent
                abs_path = base / dir_path if not Path(dir_path).is_absolute() else Path(dir_path)
                abs_path.mkdir(parents=True, exist_ok=True)

    def get(self, key_path: str, default: Any = None) -> Any:
        keys = key_path.split(".")
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def get_all(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self._config))

    def get_storage_path(self, dir_key: str) -> Path:
        storage_config = self.get("storage", {})
        dir_path = storage_config.get(dir_key)
        if not dir_path:
            raise KeyError(f"未找到存储路径配置: {dir_key}")
        base = self._config_path.parent.parent
        return base / dir_path if not Path(dir_path).is_absolute() else Path(dir_path)

    def reload(self):
        self._load_config()
        self._ensure_directories()


def get_config() -> ConfigManager:
    return ConfigManager()


def dataclass_to_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        result = asdict(obj)
        for k, v in result.items():
            if isinstance(v, Enum):
                result[k] = v.value
            elif isinstance(v, list):
                result[k] = [dataclass_to_dict(i) if hasattr(i, "__dataclass_fields__") else (i.value if isinstance(i, Enum) else i) for i in v]
        return result
    return obj
