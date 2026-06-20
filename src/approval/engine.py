import json
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core.config import (
    get_config, ReleaseRequest, ReleaseStatus,
    ApprovalStatus, ApprovalNode, dataclass_to_dict, ReleaseType,
)
from ..core.logger import get_logger, get_cst_now_str, get_audit_logger
from ..core.notifier import get_notifier


@dataclass
class ApprovalWorkflow:
    release_id: str
    release_type: ReleaseType
    channel_config: Dict[str, Any]
    nodes: List[ApprovalNode] = field(default_factory=list)
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    current_node_index: int = 0
    status: str = "pending"
    overall_passed: bool = False
    rejected_by: str = ""
    rejected_reason: str = ""
    post_sign_completed: bool = False
    total_duration_hours: float = 0.0
    approval_durations: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "release_id": self.release_id,
            "release_type": self.release_type.value,
            "channel_name": self.channel_config.get("name", ""),
            "channel_config": self.channel_config,
            "nodes": [dataclass_to_dict(n) for n in self.nodes],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_node_index": self.current_node_index,
            "status": self.status,
            "overall_passed": self.overall_passed,
            "rejected_by": self.rejected_by,
            "rejected_reason": self.rejected_reason,
            "post_sign_completed": self.post_sign_completed,
            "total_duration_hours": self.total_duration_hours,
            "approval_durations": self.approval_durations,
        }


class ApprovalEngine:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("approval_engine")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self._workflow_cache: Dict[str, ApprovalWorkflow] = {}
        self._lock = threading.Lock()

    def create_workflow(self, request: ReleaseRequest) -> ApprovalWorkflow:
        channels = self.config.get("approval.channels", {})
        channel_cfg = channels.get(request.release_type.value, channels.get("regular", {}))
        nodes = self._build_approval_nodes(request, channel_cfg)

        workflow = ApprovalWorkflow(
            release_id=request.release_id,
            release_type=request.release_type,
            channel_config=channel_cfg,
            nodes=nodes,
            created_at=get_cst_now_str(),
            status="created",
        )

        with self._lock:
            self._workflow_cache[request.release_id] = workflow

        self._save_workflow(workflow)

        self.audit.log(
            operator=request.submitted_by,
            action="APPROVAL_WORKFLOW_CREATED",
            target_id=request.release_id,
            target_type="RELEASE",
            before_state={"release_status": ReleaseStatus.PRECHECK_PASSED.value},
            after_state={
                "release_status": ReleaseStatus.APPROVAL_PENDING.value,
                "workflow_type": channel_cfg.get("name", ""),
                "approval_nodes_count": len(nodes),
            },
        )

        self.logger.info(
            f"[{request.release_id}] 创建审批流程: {channel_cfg.get('name')}, "
            f"审批节点数: {len(nodes)}"
        )

        return workflow

    def start_workflow(self, request: ReleaseRequest) -> ApprovalWorkflow:
        with self._lock:
            workflow = self._workflow_cache.get(request.release_id)
        if not workflow:
            workflow = self.create_workflow(request)

        workflow.started_at = get_cst_now_str()
        workflow.status = "in_progress"

        if workflow.release_type.value == "hotfix" and workflow.channel_config.get("parallel_approval"):
            self._start_parallel_approval(request, workflow)
        else:
            self._activate_node(request, workflow, 0)

        self._save_workflow(workflow)
        self.logger.info(f"[{request.release_id}] 审批流程已启动")
        return workflow

    def approve(self, release_id: str, approver: str, role: str,
                comment: str = "", force_skip: bool = False) -> ApprovalWorkflow:
        workflow = self._get_workflow(release_id)
        if not workflow:
            raise ValueError(f"未找到审批流程: {release_id}")

        request_type = "审批通过" if not force_skip else "紧急跳过"
        self.logger.info(
            f"[{release_id}] {request_type}: 角色={role}, 审批人={approver}"
        )

        if workflow.release_type.value == "hotfix" and workflow.channel_config.get("parallel_approval"):
            return self._process_parallel_approve(workflow, approver, role, comment, force_skip)
        else:
            return self._process_serial_approve(workflow, approver, role, comment, force_skip)

    def reject(self, release_id: str, approver: str, role: str,
               reason: str) -> ApprovalWorkflow:
        workflow = self._get_workflow(release_id)
        if not workflow:
            raise ValueError(f"未找到审批流程: {release_id}")

        self.logger.info(f"[{release_id}] 审批拒绝: 角色={role}, 审批人={approver}, 原因={reason}")

        target_node = None
        for node in workflow.nodes:
            if node.role == role and node.status in [ApprovalStatus.PENDING, ApprovalStatus.APPROVED]:
                target_node = node
                break

        if not target_node:
            raise ValueError(f"未找到待处理审批节点: {role}")

        target_node.status = ApprovalStatus.REJECTED
        target_node.approved_by = approver
        target_node.approved_at = get_cst_now_str()
        target_node.comment = reason

        workflow.status = "rejected"
        workflow.overall_passed = False
        workflow.rejected_by = approver
        workflow.rejected_reason = reason
        workflow.completed_at = get_cst_now_str()
        self._calculate_durations(workflow)

        self._save_workflow(workflow)

        self.audit.log(
            operator=approver,
            action="APPROVAL_REJECTED",
            target_id=release_id,
            target_type="RELEASE",
            before_state={"status": workflow.status},
            after_state={
                "status": ReleaseStatus.APPROVAL_REJECTED.value,
                "rejected_by": approver,
                "rejected_role": role,
                "reason": reason,
            },
        )

        self._notify_rejected(workflow, approver, role, reason)

        return workflow

    def _build_approval_nodes(self, request: ReleaseRequest,
                              channel_cfg: Dict) -> List[ApprovalNode]:
        nodes = []
        flow_config = channel_cfg.get("flow", [])
        if not flow_config and channel_cfg.get("approvers"):
            node = ApprovalNode(
                role="hotfix_combined",
                name=channel_cfg.get("name", "紧急审批"),
                description="紧急发布合并审批",
                required=True,
                approvers=channel_cfg.get("approvers", []),
            )
            nodes.append(node)
        else:
            for item in flow_config:
                node = ApprovalNode(
                    role=item.get("role", ""),
                    name=item.get("name", item.get("role", "")),
                    description=item.get("description", ""),
                    required=item.get("required", True),
                    approvers=item.get("approvers", []),
                )
                nodes.append(node)
        return nodes

    def _start_parallel_approval(self, request: ReleaseRequest, workflow: ApprovalWorkflow):
        for idx, node in enumerate(workflow.nodes):
            node.status = ApprovalStatus.PENDING
        self._notify_pending_parallel(request, workflow)

    def _activate_node(self, request: ReleaseRequest, workflow: ApprovalWorkflow,
                       node_index: int):
        if node_index >= len(workflow.nodes):
            self._complete_workflow(workflow)
            return
        workflow.current_node_index = node_index
        node = workflow.nodes[node_index]
        self._notify_pending(request, workflow, node)

    def _process_serial_approve(self, workflow: ApprovalWorkflow, approver: str,
                                role: str, comment: str, force_skip: bool) -> ApprovalWorkflow:
        current_idx = workflow.current_node_index
        if current_idx >= len(workflow.nodes):
            raise ValueError(f"审批流程已完成")

        current_node = workflow.nodes[current_idx]
        if current_node.role != role and not force_skip:
            raise ValueError(
                f"当前审批节点不匹配: 需要 {current_node.role}, 传入 {role}"
            )

        if current_node.approvers and approver not in current_node.approvers:
            self.logger.warning(
                f"审批人 {approver} 不在预设审批人列表 {current_node.approvers} 中，仍将记录"
            )

        current_node.status = ApprovalStatus.SKIPPED if force_skip else ApprovalStatus.APPROVED
        current_node.approved_by = approver
        current_node.approved_at = get_cst_now_str()
        current_node.comment = comment

        node_start = workflow.nodes[current_idx - 1].approved_at if current_idx > 0 else workflow.started_at
        try:
            duration_s = time.mktime(time.strptime(current_node.approved_at, "%Y-%m-%d %H:%M:%S")) - \
                        time.mktime(time.strptime(node_start, "%Y-%m-%d %H:%M:%S"))
            workflow.approval_durations[current_node.role] = round(duration_s / 3600, 2)
        except Exception:
            pass

        self.audit.log(
            operator=approver,
            action="APPROVAL_NODE_PASSED" if not force_skip else "APPROVAL_NODE_SKIPPED",
            target_id=workflow.release_id,
            target_type="RELEASE",
            before_state={"current_node": current_node.role, "status": "pending"},
            after_state={
                "current_node": current_node.role,
                "status": current_node.status.value,
                "approved_by": approver,
                "comment": comment,
            },
        )

        next_idx = current_idx + 1
        if next_idx >= len(workflow.nodes):
            self._complete_workflow(workflow)
        else:
            from ..core.config import ReleaseRequest as _RR
            dummy_req = _RR(
                release_id=workflow.release_id,
                version="",
                title="",
                description="",
                release_type=workflow.release_type,
                submitted_by="",
                submitted_at="",
                package_url="",
            )
            self._activate_node(dummy_req, workflow, next_idx)

        self._save_workflow(workflow)
        return workflow

    def _process_parallel_approve(self, workflow: ApprovalWorkflow, approver: str,
                                  role: str, comment: str, force_skip: bool) -> ApprovalWorkflow:
        target_node = None
        for node in workflow.nodes:
            if node.role == role:
                target_node = node
                break
        if not target_node:
            raise ValueError(
                f"未找到审批节点: {role}, 可用节点: {[n.role for n in workflow.nodes]}"
            )

        if target_node.status in [ApprovalStatus.APPROVED, ApprovalStatus.SKIPPED]:
            self.logger.info(
                f"[{workflow.release_id}] 节点 {role} 已由 {target_node.approved_by} 签过"
            )
            return workflow

        target_node.status = ApprovalStatus.SKIPPED if force_skip else ApprovalStatus.APPROVED
        target_node.approved_by = approver
        target_node.approved_at = get_cst_now_str()
        target_node.comment = comment

        self.audit.log(
            operator=approver,
            action="APPROVAL_NODE_PASSED",
            target_id=workflow.release_id,
            target_type="RELEASE",
            before_state={"node": target_node.role, "status": "pending"},
            after_state={"node": target_node.role, "status": target_node.status.value},
        )

        all_passed = all(
            n.status in [ApprovalStatus.APPROVED, ApprovalStatus.SKIPPED] or not n.required
            for n in workflow.nodes
        )

        if all_passed:
            self._complete_workflow(workflow)

        self._save_workflow(workflow)
        return workflow

    def _complete_workflow(self, workflow: ApprovalWorkflow):
        workflow.status = "approved"
        workflow.overall_passed = True
        workflow.completed_at = get_cst_now_str()
        self._calculate_durations(workflow)

        self.audit.log(
            operator="SYSTEM",
            action="APPROVAL_COMPLETED",
            target_id=workflow.release_id,
            target_type="RELEASE",
            before_state={"status": "in_progress"},
            after_state={
                "status": ReleaseStatus.APPROVAL_PASSED.value,
                "total_duration_hours": workflow.total_duration_hours,
            },
        )

        self.logger.info(f"[{workflow.release_id}] 审批流程全部通过, 总耗时: {workflow.total_duration_hours:.2f}小时")
        self._notify_approved(workflow)

    def _calculate_durations(self, workflow: ApprovalWorkflow):
        try:
            if workflow.started_at and workflow.completed_at:
                total_s = time.mktime(time.strptime(workflow.completed_at, "%Y-%m-%d %H:%M:%S")) - \
                          time.mktime(time.strptime(workflow.started_at, "%Y-%m-%d %H:%M:%S"))
                workflow.total_duration_hours = round(total_s / 3600, 2)
        except Exception:
            pass

    def _notify_pending(self, request: ReleaseRequest, workflow: ApprovalWorkflow, node: ApprovalNode):
        context = {
            "title": f"待审批 - {node.name}",
            "release_id": workflow.release_id,
            "release_title": getattr(request, "title", workflow.release_id),
            "version": getattr(request, "version", ""),
            "release_type": workflow.channel_config.get("name", ""),
            "operator": getattr(request, "submitted_by", ""),
            "description": getattr(request, "description", ""),
            "status": f"待审批（{workflow.current_node_index + 1}/{len(workflow.nodes)}）",
            "approval_info": {
                "当前节点": node.name,
                "节点职责": node.description,
                "审批人列表": node.approvers,
                "审批链进度": f"{workflow.current_node_index + 1} / {len(workflow.nodes)}",
            },
            "action_required": f"请角色【{node.role}】尽快完成审批，超时将自动提醒",
        }
        try:
            self.notifier.send(
                template_key="approval_pending",
                context=context,
                receivers=node.approvers,
            )
        except Exception as e:
            self.logger.error(f"发送待审批通知异常: {e}")

    def _notify_pending_parallel(self, request: ReleaseRequest, workflow: ApprovalWorkflow):
        all_approvers = []
        for n in workflow.nodes:
            all_approvers.extend(n.approvers)
        all_approvers = list(set(all_approvers))

        context = {
            "title": f"🚨 紧急待审批 - {workflow.channel_config.get('name', '')}",
            "release_id": workflow.release_id,
            "release_title": getattr(request, "title", workflow.release_id),
            "version": getattr(request, "version", ""),
            "release_type": workflow.channel_config.get("name", ""),
            "operator": getattr(request, "submitted_by", ""),
            "description": getattr(request, "description", ""),
            "hotfix_reason": getattr(request, "hotfix_reason", ""),
            "status": "紧急并行审批中，请立即处理",
            "approval_info": {
                "紧急原因": getattr(request, "hotfix_reason", "未提供"),
                "审批模式": "并行审批/事后补签",
                "超时时限": f"{workflow.channel_config.get('timeout_hours', 4)}小时",
                "审批人列表": all_approvers,
            },
            "action_required": "请所有审批人立即并行处理，无法及时处理的请委托他人代办",
        }
        try:
            self.notifier.send(
                template_key="approval_pending",
                context=context,
                receivers=all_approvers,
                priority="high",
            )
        except Exception as e:
            self.logger.error(f"发送并行审批通知异常: {e}")

    def _notify_approved(self, workflow: ApprovalWorkflow):
        node_details = []
        for n in workflow.nodes:
            dur = workflow.approval_durations.get(n.role, "-")
            node_details.append({
                "节点": f"{n.name} ({n.role})",
                "审批人": n.approved_by or "-",
                "审批时间": n.approved_at or "-",
                "耗时(小时)": dur if dur != "-" else "-",
                "审批意见": n.comment or "-",
            })

        context = {
            "title": "审批通过通知",
            "release_id": workflow.release_id,
            "status": "审批通过，可进入灰度发布阶段",
            "release_type": workflow.channel_config.get("name", ""),
            "approval_info": {
                "审批通道": workflow.channel_config.get("name", ""),
                "总耗时": f"{workflow.total_duration_hours:.2f}小时",
                "节点详情": node_details,
            },
        }
        try:
            self.notifier.send(
                template_key="approval_completed",
                context=context,
            )
        except Exception as e:
            self.logger.error(f"发送审批通过通知异常: {e}")

    def _notify_rejected(self, workflow: ApprovalWorkflow, approver: str, role: str, reason: str):
        context = {
            "title": "**紧急** 审批被拒绝",
            "release_id": workflow.release_id,
            "status": "审批拒绝，发布流程已终止",
            "release_type": workflow.channel_config.get("name", ""),
            "approval_info": {
                "拒绝节点": role,
                "拒绝人": approver,
                "拒绝时间": workflow.completed_at,
                "拒绝原因": reason,
            },
            "action_required": "请根据拒绝原因调整发布内容后重新提交申请",
        }
        try:
            self.notifier.send(
                template_key="approval_rejected",
                context=context,
                priority="high",
            )
        except Exception as e:
            self.logger.error(f"发送审批拒绝通知异常: {e}")

    def _save_workflow(self, workflow: ApprovalWorkflow):
        try:
            db_dir = self.config.get_storage_path("db_dir")
            approval_dir = db_dir / "approval"
            approval_dir.mkdir(parents=True, exist_ok=True)
            file = approval_dir / f"{workflow.release_id}.json"
            with open(file, "w", encoding="utf-8") as f:
                json.dump(workflow.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存审批流程失败: {e}")

    def _get_workflow(self, release_id: str) -> Optional[ApprovalWorkflow]:
        with self._lock:
            if release_id in self._workflow_cache:
                return self._workflow_cache[release_id]
        try:
            db_dir = self.config.get_storage_path("db_dir")
            file = db_dir / "approval" / f"{release_id}.json"
            if not file.exists():
                return None
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            nodes = []
            for n in data.get("nodes", []):
                n["status"] = ApprovalStatus(n["status"])
                nodes.append(ApprovalNode(**n))
            workflow = ApprovalWorkflow(
                release_id=data["release_id"],
                release_type=ReleaseType(data.get("release_type", "regular")),
                channel_config=data.get("channel_config", {}),
                nodes=nodes,
                created_at=data.get("created_at", ""),
                started_at=data.get("started_at", ""),
                completed_at=data.get("completed_at", ""),
                current_node_index=data.get("current_node_index", 0),
                status=data.get("status", "pending"),
                overall_passed=data.get("overall_passed", False),
                rejected_by=data.get("rejected_by", ""),
                rejected_reason=data.get("rejected_reason", ""),
                post_sign_completed=data.get("post_sign_completed", False),
                total_duration_hours=data.get("total_duration_hours", 0.0),
                approval_durations=data.get("approval_durations", {}),
            )
            with self._lock:
                self._workflow_cache[release_id] = workflow
            return workflow
        except Exception as e:
            self.logger.error(f"加载审批流程失败: {e}")
        return None

    def can_proceed_to_release(self, release_id: str) -> bool:
        workflow = self._get_workflow(release_id)
        if not workflow:
            return False
        return workflow.overall_passed

    def get_workflow_status(self, release_id: str) -> Dict[str, Any]:
        workflow = self._get_workflow(release_id)
        if not workflow:
            return {"exists": False}
        d = workflow.to_dict()
        d["exists"] = True
        return d


def get_approval_engine() -> ApprovalEngine:
    return ApprovalEngine()
