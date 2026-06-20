import json
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import get_config
from ..core.logger import get_logger, get_audit_logger, CST


class AuditQueryEngine:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("audit_query")
        self.audit = get_audit_logger()
        self.db_dir = self.config.get_storage_path("db_dir")

    def query_release_records(self,
                              start_time: Optional[str] = None,
                              end_time: Optional[str] = None,
                              station: Optional[str] = None,
                              version: Optional[str] = None,
                              operator: Optional[str] = None,
                              release_type: Optional[str] = None,
                              status: Optional[str] = None) -> List[Dict]:
        self.logger.info(
            f"查询发布记录: time=[{start_time} ~ {end_time}], "
            f"station={station}, version={version}, operator={operator}, "
            f"type={release_type}, status={status}"
        )

        results = []
        release_dir = self.db_dir / "release"
        if release_dir.exists():
            for file in sorted(release_dir.glob("REL_*.json")):
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if self._match_filters(data, start_time, end_time, station,
                                           version, operator, release_type, status):
                        results.append(self._summarize_release(data))
                except Exception as e:
                    self.logger.warning(f"解析发布记录失败 {file}: {e}")

        self.logger.info(f"查询到 {len(results)} 条发布记录")
        return results

    def query_rollback_records(self,
                               start_time: Optional[str] = None,
                               end_time: Optional[str] = None,
                               station: Optional[str] = None,
                               version: Optional[str] = None) -> List[Dict]:
        results = []
        release_dir = self.db_dir / "release"
        if release_dir.exists():
            for file in sorted(release_dir.glob("REL_*.json")):
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    events = data.get("events", [])
                    for ev in events:
                        triggered_at = ev.get("triggered_at", "")
                        if start_time and triggered_at < start_time:
                            continue
                        if end_time and triggered_at > end_time + " 23:59:59":
                            continue
                        if version and data.get("version") != version:
                            continue
                        if station and station not in ev.get("affected_stations", []):
                            continue
                        results.append({
                            "event_id": ev.get("event_id"),
                            "release_id": data.get("release_id"),
                            "version": data.get("version"),
                            "rollback_version": data.get("rollback_version"),
                            "triggered_at": triggered_at,
                            "trigger_stage": ev.get("trigger_stage"),
                            "trigger_metric": ev.get("trigger_metric"),
                            "trigger_value": f"{ev.get('trigger_value', 0):.2%}",
                            "threshold": f"{ev.get('threshold', 0):.2%}",
                            "affected_station_count": len(ev.get("affected_stations", [])),
                            "rollback_started": ev.get("rollback_started"),
                            "rollback_completed": ev.get("rollback_completed"),
                            "rollback_duration_seconds": ev.get("rollback_duration_seconds"),
                            "rollback_successful": ev.get("rollback_successful"),
                        })
                except Exception:
                    continue
        return sorted(results, key=lambda x: x["triggered_at"], reverse=True)

    def query_drill_records(self, start_time: Optional[str] = None,
                            end_time: Optional[str] = None,
                            status: Optional[str] = None) -> List[Dict]:
        results = []
        drill_dir = self.db_dir / "drills"
        if drill_dir.exists():
            for file in sorted(drill_dir.glob("DRL_*.json"), reverse=True):
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if start_time and data.get("started_at", "") < start_time:
                        continue
                    if end_time and data.get("started_at", "") > end_time + " 23:59:59":
                        continue
                    if status and data.get("status") != status:
                        continue
                    results.append(data)
                except Exception:
                    continue
        return results

    def query_approval_records(self, start_time: Optional[str] = None,
                               end_time: Optional[str] = None,
                               release_id: Optional[str] = None,
                               approver: Optional[str] = None) -> List[Dict]:
        results = []
        approval_dir = self.db_dir / "approval"
        if approval_dir.exists():
            for file in sorted(approval_dir.glob("REL_*.json")):
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if start_time and data.get("created_at", "") < start_time:
                        continue
                    if end_time and data.get("created_at", "") > end_time + " 23:59:59":
                        continue
                    if release_id and data.get("release_id") != release_id:
                        continue
                    if approver:
                        matched = any(
                            approver in n.get("approvers", []) or n.get("approved_by") == approver
                            for n in data.get("nodes", [])
                        )
                        if not matched:
                            continue

                    node_details = []
                    for n in data.get("nodes", []):
                        node_details.append({
                            "role": n.get("role"),
                            "name": n.get("name"),
                            "status": n.get("status"),
                            "approved_by": n.get("approved_by"),
                            "approved_at": n.get("approved_at"),
                            "comment": n.get("comment"),
                        })

                    results.append({
                        "release_id": data.get("release_id"),
                        "release_type": data.get("release_type"),
                        "channel_name": data.get("channel_name"),
                        "created_at": data.get("created_at"),
                        "started_at": data.get("started_at"),
                        "completed_at": data.get("completed_at"),
                        "status": data.get("status"),
                        "overall_passed": data.get("overall_passed"),
                        "total_duration_hours": data.get("total_duration_hours"),
                        "rejected_by": data.get("rejected_by"),
                        "rejected_reason": data.get("rejected_reason"),
                        "nodes": node_details,
                    })
                except Exception:
                    continue
        return sorted(results, key=lambda x: x["created_at"], reverse=True)

    def _match_filters(self, data: Dict, start_time, end_time, station,
                       version, operator, release_type, status) -> bool:
        created = data.get("created_at", "")
        if start_time and created < start_time:
            return False
        if end_time and created > end_time + " 23:59:59":
            return False
        if version and data.get("version") != version:
            return False
        if station:
            all_stations = [s.get("station_id") for s in data.get("stations", [])]
            affected = data.get("all_affected_stations", [])
            if station not in all_stations and station not in affected:
                return False
        session_status = data.get("status", "")
        if status:
            if status == "rollback" and session_status not in ["rollback_completed", "circuit_breaker_triggered"]:
                return False
            if status == "success" and session_status not in ["completed"]:
                return False
            if status == "in_progress" and session_status not in ["in_progress", "drill_in_progress"]:
                return False
        return True

    def _summarize_release(self, data: Dict) -> Dict:
        events = data.get("events", [])
        stages = data.get("stages", [])
        status = data.get("status", "")
        status_map = {
            "created": "创建",
            "in_progress": "灰度进行中",
            "completed": "发布成功",
            "rollback_completed": "已回滚",
            "rollback_failed": "回滚失败",
            "circuit_breaker_triggered": "熔断触发",
            "cancelled": "已取消",
            "failed": "发布失败",
        }
        return {
            "release_id": data.get("release_id"),
            "version": data.get("version"),
            "rollback_version": data.get("rollback_version"),
            "created_at": data.get("created_at"),
            "completed_at": data.get("completed_at"),
            "status": status_map.get(status, status),
            "current_stage": data.get("current_stage_index", -1) + 1,
            "total_stages": len(stages),
            "total_stations": len(data.get("stations", [])),
            "affected_stations": len(data.get("all_affected_stations", [])),
            "circuit_breaker_state": data.get("circuit_breaker_state"),
            "rollback_events": len([e for e in events if e.get("rollback_successful")]),
            "total_events": len(events),
        }

    def export(self, records: List[Dict], output_path: str,
               format: str = "excel") -> str:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        if format == "csv":
            if records:
                keys = list(records[0].keys())
                with open(output, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                    writer.writeheader()
                    for r in records:
                        flat = {}
                        for k, v in r.items():
                            if isinstance(v, (dict, list)):
                                flat[k] = json.dumps(v, ensure_ascii=False)
                            else:
                                flat[k] = v
                        writer.writerow(flat)
        elif format == "json":
            with open(output, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2, default=str)
        elif format == "excel":
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
                wb = Workbook()
                ws = wb.active
                if records:
                    keys = list(records[0].keys())
                    ws.append(keys)
                    hf = Font(bold=True, color="FFFFFF")
                    hfill = PatternFill("solid", fgColor="1976D2")
                    for c in range(1, len(keys) + 1):
                        cell = ws.cell(row=1, column=c)
                        cell.font = hf
                        cell.fill = hfill
                        cell.alignment = Alignment(horizontal="center")
                    for r in records:
                        row_data = []
                        for k in keys:
                            v = r.get(k, "")
                            if isinstance(v, (dict, list)):
                                v = json.dumps(v, ensure_ascii=False)
                            row_data.append(v)
                        ws.append(row_data)
                    for col in ws.columns:
                        mx = 12
                        for cell in col:
                            if cell.value:
                                mx = max(mx, min(50, len(str(cell.value)) * 2))
                        ws.column_dimensions[col[0].column_letter].width = mx
                wb.save(output)
            except ImportError:
                self.logger.warning("openpyxl未安装，降级为CSV导出")
                return self.export(records, str(output.with_suffix(".csv")), "csv")

        self.logger.info(f"记录已导出到 {output}, 共 {len(records)} 条")
        return str(output)


def get_audit_query_engine() -> AuditQueryEngine:
    return AuditQueryEngine()
