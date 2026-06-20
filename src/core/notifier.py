import json
import smtplib
import hmac
import base64
import hashlib
import time
from typing import Any, Dict, List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

import requests

from .config import get_config
from .logger import get_logger, get_cst_now_str


class Notifier:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("notifier")
        self.enabled = self.config.get("notification.enabled", True)
        self.channels = self.config.get("notification.channels", {})
        self.templates = self.config.get("notification.templates", {})

    def send(self, template_key: str, context: Dict[str, Any],
             receivers: Optional[List[str]] = None,
             attachments: Optional[List[str]] = None,
             channels: Optional[List[str]] = None,
             priority: Optional[str] = None) -> Dict[str, bool]:
        if not self.enabled:
            self.logger.warning("通知功能已禁用")
            return {}

        template = self.templates.get(template_key, {})
        subject = template.get("subject", f"[发布平台] {template_key}").format(**context)
        body = self._render_message(template_key, context)
        prio = priority or template.get("priority", "normal")

        result = {}
        target_channels = channels or [k for k, v in self.channels.items() if v.get("enabled", False)]
        if not target_channels:
            target_channels = ["email"]

        if "wechat_work" in target_channels and self.channels.get("wechat_work", {}).get("enabled"):
            result["wechat_work"] = self._send_wechat_work(subject, body, context, prio)
        if "dingtalk" in target_channels and self.channels.get("dingtalk", {}).get("enabled"):
            result["dingtalk"] = self._send_dingtalk(subject, body, context, prio)
        if "email" in target_channels and self.channels.get("email", {}).get("enabled"):
            result["email"] = self._send_email(subject, body, receivers, attachments)

        self.logger.info(f"通知发送完成: {template_key}, 结果: {result}")
        return result

    def _render_message(self, template_key: str, context: Dict[str, Any]) -> str:
        title = context.get("title", template_key)
        lines = [
            f"## {title}",
            f"**时间**: {get_cst_now_str()}",
            "",
        ]

        special_keys = ["precheck_details", "approval_info", "grayscale_info",
                        "circuit_breaker_info", "rollback_info", "metrics",
                        "report_summary", "drill_result"]

        for key in ["release_id", "version", "release_title", "release_type",
                    "operator", "description", "hotfix_reason"]:
            if key in context and context[key]:
                label = {
                    "release_id": "发布编号",
                    "version": "版本号",
                    "release_title": "发布标题",
                    "release_type": "发布类型",
                    "operator": "提交人",
                    "description": "描述",
                    "hotfix_reason": "紧急原因",
                }.get(key, key)
                lines.append(f"**{label}**: {context[key]}")

        if "status" in context:
            lines.append(f"**状态**: {context['status']}")

        for key in special_keys:
            if key in context and context[key]:
                lines.append("")
                section_titles = {
                    "precheck_details": "### 前置校验详情",
                    "approval_info": "### 审批信息",
                    "grayscale_info": "### 灰度发布信息",
                    "circuit_breaker_info": "### 熔断事件详情",
                    "rollback_info": "### 回滚详情",
                    "metrics": "### 指标详情",
                    "report_summary": "### 周报摘要",
                    "drill_result": "### 演练结果",
                }
                lines.append(section_titles.get(key, f"### {key}"))
                lines.append(self._format_section(context[key]))

        if "suggestion" in context and context["suggestion"]:
            lines.append("")
            lines.append("### 修复建议")
            lines.append(context["suggestion"])

        if "action_required" in context and context["action_required"]:
            lines.append("")
            lines.append(f"> **⚠️ 需要处理**: {context['action_required']}")

        lines.append("")
        lines.append("---")
        lines.append(f"*本消息由发布自动化平台自动生成于 {get_cst_now_str()}*")

        return "\n".join(lines)

    def _format_section(self, data: Any, indent: int = 0) -> str:
        prefix = "  " * indent
        lines = []
        if isinstance(data, dict):
            for k, v in data.items():
                label = k.replace("_", " ").title()
                if isinstance(v, (dict, list)):
                    lines.append(f"{prefix}- **{label}**:")
                    lines.append(self._format_section(v, indent + 1))
                else:
                    lines.append(f"{prefix}- **{label}**: {v}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.append(self._format_section(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {item}")
        else:
            lines.append(f"{prefix}{data}")
        return "\n".join(lines)

    def _send_wechat_work(self, subject: str, body: str, context: Dict, priority: str) -> bool:
        try:
            wc = self.channels.get("wechat_work", {})
            webhook = wc.get("webhook_url", "")
            if not webhook:
                return False

            msg_type = "markdown" if priority in ["urgent", "high"] else "text"
            payload = {
                "msgtype": msg_type,
            }
            if priority == "urgent":
                body = f"<font color=\"warning\">🚨 紧急通知 🚨</font>\n\n{body}"

            payload[msg_type] = {
                "content": body,
                "mentioned_list": ["@all"],
                "mentioned_mobile_list": wc.get("mentioned_mobile_list", []),
            }

            resp = requests.post(webhook, json=payload, timeout=10)
            ok = resp.status_code == 200 and resp.json().get("errcode", -1) == 0
            if not ok:
                self.logger.error(f"企微发送失败: {resp.text}")
            return ok
        except Exception as e:
            self.logger.error(f"企微发送异常: {e}")
            return False

    def _send_dingtalk(self, subject: str, body: str, context: Dict, priority: str) -> bool:
        try:
            dc = self.channels.get("dingtalk", {})
            webhook = dc.get("webhook_url", "")
            if not webhook:
                return False

            secret = dc.get("secret", "")
            if secret:
                timestamp = str(round(time.time() * 1000))
                string_to_sign = f"{timestamp}\n{secret}"
                hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                                     digestmod=hashlib.sha256).digest()
                sign = base64.b64encode(hmac_code).decode("utf-8")
                webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

            if priority == "urgent":
                body = f"# 🚨 紧急通知 🚨\n\n{body}"
            else:
                body = f"# {subject}\n\n{body}"

            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": subject,
                    "text": body,
                },
                "at": {
                    "atMobiles": dc.get("at_mobiles", []),
                    "isAtAll": priority in ["urgent", "high"],
                },
            }

            resp = requests.post(webhook, json=payload, timeout=10)
            ok = resp.status_code == 200 and resp.json().get("errcode", -1) == 0
            if not ok:
                self.logger.error(f"钉钉发送失败: {resp.text}")
            return ok
        except Exception as e:
            self.logger.error(f"钉钉发送异常: {e}")
            return False

    def _send_email(self, subject: str, body: str,
                    receivers: Optional[List[str]] = None,
                    attachments: Optional[List[str]] = None) -> bool:
        try:
            ec = self.channels.get("email", {})
            if not receivers:
                receivers = ec.get("default_receivers", [])
            if not receivers:
                return False

            msg = MIMEMultipart()
            msg["Subject"] = subject
            msg["From"] = ec.get("smtp_user", "release-platform@company.com")
            msg["To"] = ", ".join(receivers)
            cc = ec.get("default_cc", [])
            if cc:
                msg["Cc"] = ", ".join(cc)
                receivers = receivers + cc

            html_body = self._markdown_to_html(body)
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            if attachments:
                for att_path in attachments:
                    att_file = Path(att_path)
                    if att_file.exists():
                        part = MIMEBase("application", "octet-stream")
                        with open(att_file, "rb") as f:
                            part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition",
                                        f"attachment; filename=\"{att_file.name}\"")
                        msg.attach(part)

            with smtplib.SMTP(ec.get("smtp_server", "localhost"),
                              ec.get("smtp_port", 25),
                              timeout=30) as server:
                if ec.get("use_tls", False):
                    server.starttls()
                user = ec.get("smtp_user", "")
                password = ec.get("smtp_password", "")
                if user and password:
                    server.login(user, password)
                server.sendmail(msg["From"], receivers, msg.as_string())

            return True
        except Exception as e:
            self.logger.error(f"邮件发送异常: {e}")
            return False

    def _markdown_to_html(self, md_text: str) -> str:
        import re
        html = md_text
        html = re.sub(r'^### (.*)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.*)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.*)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)
        html = re.sub(r'`(.*?)`', r'<code>\1</code>', html)
        html = re.sub(r'^> (.*)$', r'<blockquote style="background:#fff3cd;padding:10px;border-left:4px solid #ffc107;">\1</blockquote>', html, flags=re.MULTILINE)
        html = re.sub(r'^- (.*)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'(<li>.*</li>\n?)+', lambda m: f'<ul style="margin-left:20px;">{m.group(0)}</ul>', html)
        html = re.sub(r'\n', r'<br/>', html)
        html = re.sub(r'^---$', r'<hr/>', html, flags=re.MULTILINE)
        html = html.replace('<font color="warning">', '<span style="color:#dc3545;font-size:18px;font-weight:bold;">')
        html = html.replace('</font>', '</span>')
        return html


def get_notifier() -> Notifier:
    return Notifier()
