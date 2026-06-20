import os
import json
import time
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

from .config import get_config, AuditLog, dataclass_to_dict

CST = timezone(timedelta(hours=8))


def get_cst_now() -> datetime:
    return datetime.now(CST)


def get_cst_now_str() -> str:
    return get_cst_now().strftime("%Y-%m-%d %H:%M:%S")


def get_timestamp_str() -> str:
    return get_cst_now().strftime("%Y%m%d%H%M%S")


def generate_id(prefix: str) -> str:
    ts = get_cst_now().strftime("%Y%m%d%H%M%S")
    rand = int(time.time() * 1000) % 1000
    return f"{prefix}_{ts}_{rand:03d}"


class LoggerManager:
    _instance: Optional["LoggerManager"] = None
    _loggers: Dict[str, logging.Logger] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_logger(self, name: str = "release_platform", log_level: Optional[str] = None) -> logging.Logger:
        if name in self._loggers:
            return self._loggers[name]

        config = get_config()
        level_str = log_level or config.get("system.log_level", "INFO")
        level = getattr(logging, level_str.upper(), logging.INFO)

        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(filename)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        try:
            log_dir = config.get_storage_path("base_dir") / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = TimedRotatingFileHandler(
                log_dir / f"{name}.log",
                when="midnight",
                interval=1,
                backupCount=30,
                encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass

        self._loggers[name] = logger
        return logger


def get_logger(name: str = "release_platform") -> logging.Logger:
    return LoggerManager().get_logger(name)


class AuditLogger:
    _instance: Optional["AuditLogger"] = None
    _executor: ThreadPoolExecutor = None
    _prev_hash: str = ""

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        config = get_config()
        self.enabled = config.get("audit.enabled", True)
        self.immutable = config.get("audit.immutable_logs", True)
        self.hash_algorithm = config.get("audit.hash_algorithm", "sha256")
        self.log_dir = config.get_storage_path("audit_log_dir")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audit_logger")
        self._prev_hash = self._load_last_hash()
        self.logger = get_logger("audit")

    def _load_last_hash(self) -> str:
        hash_file = self.log_dir / ".last_hash"
        if hash_file.exists():
            return hash_file.read_text(encoding="utf-8").strip()
        return "GENESIS"

    def _save_last_hash(self, h: str):
        hash_file = self.log_dir / ".last_hash"
        hash_file.write_text(h, encoding="utf-8")

    def _compute_chain_hash(self, log_entry: AuditLog) -> str:
        combined = f"{self._prev_hash}|{log_entry.log_id}|{log_entry.timestamp}|{log_entry.operator}|{log_entry.action}"
        new_hash = hashlib.new(self.hash_algorithm, combined.encode("utf-8")).hexdigest()
        return new_hash

    def _write_log_async(self, log_entry: AuditLog):
        try:
            if self.immutable:
                log_entry.hash = self._compute_chain_hash(log_entry)
                self._prev_hash = log_entry.hash
                self._save_last_hash(self._prev_hash)

            today = get_cst_now().strftime("%Y-%m-%d")
            log_file = self.log_dir / f"audit_{today}.jsonl"

            data = dataclass_to_dict(log_entry)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

            self.logger.info(f"审计日志已写入: {log_entry.log_id} [{log_entry.action}]")

            if self.immutable:
                self._verify_chain_integrity(today)
        except Exception as e:
            self.logger.error(f"写入审计日志失败: {e}")

    def log(self, operator: str, action: str, target_id: str, target_type: str,
            before_state: Optional[Dict] = None, after_state: Optional[Dict] = None,
            ip_address: str = "", user_agent: str = "", write_async: bool = True) -> Optional[AuditLog]:
        if not self.enabled:
            return None

        log_entry = AuditLog(
            log_id=generate_id("AUD"),
            timestamp=get_cst_now_str(),
            operator=operator,
            action=action,
            target_id=target_id,
            target_type=target_type,
            before_state=before_state or {},
            after_state=after_state or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        if write_async:
            self._executor.submit(self._write_log_async, log_entry)
        else:
            self._write_log_async(log_entry)

        return log_entry

    def _verify_chain_integrity(self, date_str: str) -> bool:
        log_file = self.log_dir / f"audit_{date_str}.jsonl"
        if not log_file.exists():
            return True

        prev_hash = self._load_last_hash()
        logs = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))

        if not logs:
            return True

        for i, log_data in enumerate(logs):
            expected_prev = "GENESIS" if i == 0 else logs[i - 1].get("hash", "GENESIS")
            combined = f"{expected_prev}|{log_data['log_id']}|{log_data['timestamp']}|{log_data['operator']}|{log_data['action']}"
            expected_hash = hashlib.new(self.hash_algorithm, combined.encode("utf-8")).hexdigest()
            if log_data.get("hash") != expected_hash:
                self.logger.error(f"审计日志哈希链断裂! 位置: {i}, log_id: {log_data['log_id']}")
                return False

        self.logger.info(f"审计日志哈希链验证通过, 共 {len(logs)} 条记录")
        return True

    def query_logs(self, start_time: Optional[str] = None, end_time: Optional[str] = None,
                   operator: Optional[str] = None, action: Optional[str] = None,
                   target_id: Optional[str] = None, target_type: Optional[str] = None) -> List[Dict]:
        results = []
        start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST) if start_time else None
        end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST) if end_time else None

        for log_file in sorted(self.log_dir.glob("audit_*.jsonl")):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        log_dt = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)

                        if start_dt and log_dt < start_dt:
                            continue
                        if end_dt and log_dt > end_dt:
                            continue
                        if operator and data["operator"] != operator:
                            continue
                        if action and data["action"] != action:
                            continue
                        if target_id and data["target_id"] != target_id:
                            continue
                        if target_type and data["target_type"] != target_type:
                            continue

                        results.append(data)
            except Exception:
                continue

        return sorted(results, key=lambda x: x["timestamp"])

    def export_logs(self, output_path: str, format: str = "jsonl", **filters) -> str:
        logs = self.query_logs(**filters)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        if format == "jsonl":
            with open(output, "w", encoding="utf-8") as f:
                for log in logs:
                    f.write(json.dumps(log, ensure_ascii=False) + "\n")
        elif format == "json":
            with open(output, "w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
        elif format == "csv":
            import csv
            keys = ["log_id", "timestamp", "operator", "action", "target_id", "target_type", "ip_address", "hash"]
            with open(output, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(logs)

        self.logger.info(f"审计日志已导出到 {output}, 共 {len(logs)} 条")
        return str(output)


def get_audit_logger() -> AuditLogger:
    return AuditLogger()
