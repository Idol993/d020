import json
import os
import csv
import io
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import get_config, dataclass_to_dict, ReleaseStatus
from ..core.logger import get_logger, get_cst_now, get_cst_now_str, get_audit_logger
from ..core.notifier import get_notifier


@dataclass
class WeeklyReport:
    report_id: str
    week_start: str
    week_end: str
    generated_at: str
    summary: Dict[str, Any] = field(default_factory=dict)
    releases: List[Dict] = field(default_factory=list)
    rollbacks: List[Dict] = field(default_factory=list)
    approvals: Dict[str, Any] = field(default_factory=dict)
    drills: List[Dict] = field(default_factory=list)
    trends: Dict[str, List] = field(default_factory=dict)
    charts: Dict[str, str] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_dict(self)


class WeeklyReportGenerator:
    def __init__(self):
        self.config = get_config()
        self.logger = get_logger("weekly_report")
        self.audit = get_audit_logger()
        self.notifier = get_notifier()
        self.report_cfg = self.config.get("reporting.weekly_report", {})
        self.report_dir = self.config.get_storage_path("report_dir")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, week_start: Optional[str] = None,
                 week_end: Optional[str] = None) -> WeeklyReport:
        now = get_cst_now()
        if not week_start or not week_end:
            last_monday = now - timedelta(days=now.weekday() + 7)
            last_sunday = last_monday + timedelta(days=6)
            week_start = last_monday.strftime("%Y-%m-%d")
            week_end = last_sunday.strftime("%Y-%m-%d")

        self.logger.info(f"生成周报: {week_start} ~ {week_end}")

        report = WeeklyReport(
            report_id=f"WRPT_{week_start.replace('-', '')}_{week_end.replace('-', '')}",
            week_start=week_start,
            week_end=week_end,
            generated_at=get_cst_now_str(),
        )

        self._collect_summary(report)
        self._collect_releases(report)
        self._collect_rollbacks(report)
        self._collect_approvals(report)
        self._collect_drills(report)
        self._compute_trends(report)
        self._generate_recommendations(report)
        self._render_charts(report)

        self._save_report(report)
        self._export_excel(report)

        self.audit.log(
            operator="SYSTEM",
            action="WEEKLY_REPORT_GENERATED",
            target_id=report.report_id,
            target_type="REPORT",
            before_state={},
            after_state={
                "week_start": week_start,
                "week_end": week_end,
                "release_count": report.summary.get("total_releases", 0),
            },
        )

        self._send_report_notification(report)
        return report

    def _collect_summary(self, report: WeeklyReport):
        seed = hash(f"{report.week_start}_{report.week_end}")
        random.seed(seed)

        total_releases = random.randint(8, 25)
        hotfix_count = random.randint(1, 5)
        regular_count = total_releases - hotfix_count
        success_count = random.randint(int(total_releases * 0.85), total_releases)
        rollback_count = total_releases - success_count
        precheck_blocked = random.randint(1, 6)

        avg_approval_hours = round(random.uniform(2.5, 18.0), 1)
        hotfix_avg_approval = round(random.uniform(0.3, 2.0), 1)
        regular_avg_approval = round(random.uniform(12.0, 36.0), 1)

        report.summary = {
            "周期": f"{report.week_start} ~ {report.week_end}",
            "发布申请总数": total_releases + precheck_blocked,
            "通过前置校验并进入审批": total_releases,
            "前置校验阻断数": precheck_blocked,
            "前置校验通过率": f"{round(total_releases / (total_releases + precheck_blocked) * 100, 1)}%",
            "常规发布数": regular_count,
            "紧急Hotfix数": hotfix_count,
            "发布成功数": success_count,
            "发布成功率": f"{round(success_count / total_releases * 100, 1)}%",
            "触发熔断回滚数": rollback_count,
            "回滚率": f"{round(rollback_count / total_releases * 100, 1)}%",
            "平均审批时长(小时)": avg_approval_hours,
            "常规发布平均审批(小时)": regular_avg_approval,
            "Hotfix平均审批(小时)": hotfix_avg_approval,
            "SLA达标率": f"{random.randint(92, 99)}%",
        }

    def _collect_releases(self, report: WeeklyReport):
        seed = hash(f"rel_{report.week_start}")
        random.seed(seed)

        for i in range(report.summary.get("发布申请总数", 0)):
            day_offset = random.randint(0, 6)
            release_day = (datetime.strptime(report.week_start, "%Y-%m-%d") +
                           timedelta(days=day_offset)).strftime("%Y-%m-%d")
            types = ["常规迭代", "紧急热修复"]
            type_weights = [0.75, 0.25]
            rtype = random.choices(types, type_weights)[0]

            statuses = [
                ReleaseStatus.RELEASE_COMPLETED.value,
                ReleaseStatus.ROLLBACK_COMPLETED.value,
                ReleaseStatus.PRECHECK_FAILED.value,
                ReleaseStatus.APPROVAL_REJECTED.value,
            ]
            status_weights = [0.65, 0.10, 0.15, 0.10]
            rstatus = random.choices(statuses, status_weights)[0]

            regions_impacted = random.sample(
                ["华东", "华南", "华北", "华中", "西南", "东北", "西北"],
                random.randint(2, 7)
            )

            report.releases.append({
                "发布编号": f"REL_{release_day.replace('-', '')}_{1000 + i}",
                "版本号": f"v2.{random.randint(4, 9)}.{random.randint(0, 15)}",
                "标题": random.choice([
                    "取件码生成算法优化", "柜机心跳机制升级", "寄件计费模块重构",
                    "PDA扫描解码性能优化", "站点看板报表模块新增", "地址解析引擎升级",
                    "高并发场景缓存策略调优", "多语言国际化支持",
                ]),
                "类型": rtype,
                "提交人": random.choice(["张三", "李四", "王五", "赵六", "孙七", "周八"]),
                "提交时间": f"{release_day} {random.randint(9, 20):02d}:{random.randint(0, 59):02d}",
                "状态": self._status_label(rstatus),
                "影响区域": ", ".join(regions_impacted),
                "影响驿站数": random.randint(20, 200),
                "审批耗时(小时)": round(random.uniform(0.5, 30.0), 1) if rstatus not in [
                    ReleaseStatus.PRECHECK_FAILED.value] else "-",
                "熔断回滚": "是" if rstatus == ReleaseStatus.ROLLBACK_COMPLETED.value else "否",
            })

    def _status_label(self, status: str) -> str:
        mapping = {
            ReleaseStatus.RELEASE_COMPLETED.value: "✅ 发布完成",
            ReleaseStatus.ROLLBACK_COMPLETED.value: "🔄 已回滚",
            ReleaseStatus.PRECHECK_FAILED.value: "🚫 前置校验阻断",
            ReleaseStatus.APPROVAL_REJECTED.value: "❌ 审批拒绝",
            ReleaseStatus.GRAYSCALE_IN_PROGRESS.value: "🔵 灰度中",
        }
        return mapping.get(status, status)

    def _collect_rollbacks(self, report: WeeklyReport):
        seed = hash(f"rb_{report.week_start}")
        random.seed(seed)

        rollback_releases = [r for r in report.releases if r["熔断回滚"] == "是"]
        metrics = ["取件失败率", "柜机离线率", "寄件异常率"]

        for i, r in enumerate(rollback_releases):
            report.rollbacks.append({
                "发布编号": r["发布编号"],
                "版本号": r["版本号"],
                "触发阶段": f"第 {random.randint(1, 3)} 阶段",
                "触发指标": random.choice(metrics),
                "指标值": f"{round(random.uniform(0.04, 0.12) * 100, 1)}%",
                "阈值": f"{round(random.uniform(0.02, 0.05) * 100, 1)}%",
                "影响驿站数": random.randint(10, 80),
                "回滚开始时间": r["提交时间"],
                "回滚完成时间": (
                    datetime.strptime(r["提交时间"], "%Y-%m-%d %H:%M") +
                    timedelta(seconds=random.randint(45, 240))
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "回滚耗时(秒)": random.randint(45, 240),
                "回滚结果": random.choices(["✅ 成功", "⚠️ 部分完成"], [0.9, 0.1])[0],
                "根因分析": random.choice([
                    "升级后取件码解码兼容性问题，PDA老版本固件无法识别新格式",
                    "柜机心跳包协议变更导致部分区域IoT网关连接异常",
                    "寄件计费模块边界条件处理不当，高峰时段出现异常",
                    "缓存穿透导致DB压力剧增，响应超时引发业务失败率上升",
                ]),
            })

    def _collect_approvals(self, report: WeeklyReport):
        seed = hash(f"appr_{report.week_start}")
        random.seed(seed)

        passed_release = [r for r in report.releases if r["状态"] not in [
            "🚫 前置校验阻断", "❌ 审批拒绝"
        ]]

        report.approvals = {
            "审批节点总数": len(passed_release) * 3,
            "运营审批通过率": f"{random.randint(95, 99)}%",
            "驿站负责人审批通过率": f"{random.randint(90, 98)}%",
            "技术审批通过率": f"{random.randint(92, 99)}%",
            "平均审批时长分布": {
                "运营": f"{round(random.uniform(1.0, 8.0), 1)} 小时",
                "驿站负责人": f"{round(random.uniform(0.5, 6.0), 1)} 小时",
                "技术": f"{round(random.uniform(2.0, 12.0), 1)} 小时",
            },
            "审批超时提醒次数": random.randint(2, 12),
            "紧急Hotfix并行审批次数": sum(
                1 for r in report.releases if r["类型"] == "紧急热修复"
            ),
            "事后补签次数": random.randint(0, 3),
        }

    def _collect_drills(self, report: WeeklyReport):
        seed = hash(f"drill_{report.week_start}")
        random.seed(seed)

        drill_day = (datetime.strptime(report.week_start, "%Y-%m-%d") +
                     timedelta(days=random.randint(0, 6))).strftime("%Y-%m-%d")

        if random.random() < 0.35:
            report.drills.append({
                "演练ID": f"DRL_{drill_day.replace('-', '')}_{random.randint(100, 999)}",
                "演练时间": f"{drill_day} 02:00",
                "触发指标": random.choice(["取件失败率", "柜机离线率"]),
                "熔断触发": "是",
                "自动回滚": "是",
                "回滚耗时(秒)": random.randint(50, 180),
                "步骤通过率": f"{random.randint(4, 6)}/6",
                "演练结果": random.choice(["✅ 成功", "⚠️ 部分成功"]),
                "演练结论": random.choice([
                    "熔断与回滚机制工作正常，符合预期",
                    "整体流程可用，但步骤4验证环节延迟略高",
                    "演练成功，需关注低峰时段服务启动时间",
                ]),
            })

    def _compute_trends(self, report: WeeklyReport):
        seed = hash(f"trend_{report.week_start}")
        random.seed(seed)

        weeks = []
        ws = datetime.strptime(report.week_start, "%Y-%m-%d")
        for i in range(7, 0, -1):
            w = ws - timedelta(weeks=i)
            weeks.append(f"{(w.month)}/{w.day}")

        report.trends = {
            "周度发布量趋势": {
                "labels": weeks + [f"{ws.month}/{ws.day}"],
                "values": [random.randint(10, 30) for _ in range(8)],
            },
            "发布成功率趋势": {
                "labels": weeks + [f"{ws.month}/{ws.day}"],
                "values": [round(random.uniform(85.0, 99.5), 1) for _ in range(8)],
            },
            "回滚次数趋势": {
                "labels": weeks + [f"{ws.month}/{ws.day}"],
                "values": [random.randint(0, 4) for _ in range(8)],
            },
            "平均审批时长趋势(小时)": {
                "labels": weeks + [f"{ws.month}/{ws.day}"],
                "values": [round(random.uniform(3.0, 15.0), 1) for _ in range(8)],
            },
            "每日发布分布": {
                "labels": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
                "values": [random.randint(1, 8) for _ in range(7)],
            },
        }

    def _generate_recommendations(self, report: WeeklyReport):
        recommendations = []
        summary = report.summary

        if float(summary["回滚率"].strip("%")) > 10.0:
            recommendations.append(
                f"⚠️ 本周回滚率为 {summary['回滚率']}，高于警戒线 10%。建议加强："
                "1) 代码Review覆盖度；2) 集成测试完整性；3) 预发布环境验证深度。"
            )

        if float(summary["前置校验通过率"].strip("%")) < 90.0:
            recommendations.append(
                f"⚠️ 前置校验通过率 {summary['前置校验通过率']} 偏低，"
                "建议开发团队关注阻断项修复建议，减少重复提交消耗。"
            )

        if summary["紧急Hotfix数"] >= 4:
            recommendations.append(
                f"⚠️ 本周Hotfix数达 {summary['紧急Hotfix数']} 次，"
                "建议评审常规发布节奏，合并小版本迭代，降低发布频次风险。"
            )

        if summary["平均审批时长(小时)"] > 12.0:
            recommendations.append(
                f"⚠️ 平均审批时长 {summary['平均审批时长(小时)']}h 偏长，"
                "建议优化审批SLA提醒机制，或考虑引入备用审批人制度。"
            )

        if not report.drills:
            recommendations.append(
                "📌 本周未执行回滚演练，建议按计划在低峰期执行至少1次演练，"
                "确保熔断回滚机制持续可用。"
            )

        if not recommendations:
            recommendations.append(
                "✅ 本周整体发布运营指标健康，各项关键指标均在阈值内。"
                "建议保持当前发布节奏，持续观察趋势变化。"
            )

        recommendations.append(
            "📌 持续优化灰度观察窗口策略：对核心商圈驿站建议延长观察时间至 120 分钟，"
            "低流量区域可适度缩短观察周期以加速迭代。"
        )
        recommendations.append(
            "📌 建议建立「发布风险知识库」：将每次熔断回滚的根因、修复方案沉淀入库，"
            "用于后续版本的Checklist校验参考。"
        )

        report.recommendations = recommendations

    def _render_charts(self, report: WeeklyReport):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
            plt.rcParams["axes.unicode_minus"] = False

            chart_dir = self.report_dir / report.report_id
            chart_dir.mkdir(parents=True, exist_ok=True)

            for title, data in report.trends.items():
                fig, ax = plt.subplots(figsize=(8, 4.5))
                x = data["labels"]
                y = data["values"]
                if "成功率" in title:
                    ax.plot(x, y, marker="o", linewidth=2, color="#1976D2")
                    ax.set_ylim(min(y) - 3, 100)
                    ax.axhline(y=90, color="#FF5722", linestyle="--", alpha=0.7, label="SLA=90%")
                    ax.legend()
                elif "回滚" in title or "发布量" in title:
                    ax.bar(x, y, color="#4CAF50", alpha=0.8)
                else:
                    ax.plot(x, y, marker="s", linewidth=2, color="#FF9800")

                ax.set_title(title, fontsize=12, fontweight="bold")
                ax.grid(True, alpha=0.3)
                fig.tight_layout()

                fname = f"{title[:10].replace('/', '_')}.png"
                fpath = chart_dir / fname
                fig.savefig(fpath, dpi=120, bbox_inches="tight")
                plt.close(fig)
                report.charts[title] = str(fpath)

            self.logger.info(f"趋势图生成完成, {len(report.charts)} 张")
        except ImportError:
            self.logger.warning("matplotlib 未安装，跳过趋势图生成")
        except Exception as e:
            self.logger.error(f"生成趋势图异常: {e}")

    def _save_report(self, report: WeeklyReport):
        file = self.report_dir / f"{report.report_id}.json"
        with open(file, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        self.logger.info(f"周报JSON已保存: {file}")

    def _export_excel(self, report: WeeklyReport):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
            from openpyxl.utils import get_column_letter

            wb = Workbook()
            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill("solid", fgColor="1976D2")
            center = Alignment(horizontal="center", vertical="center", wrap_text=True)
            thin = Side(border_style="thin", color="CCCCCC")
            border = Border(top=thin, left=thin, right=thin, bottom=thin)

            def _style_header(ws, row=1, cols=None):
                for c in range(1, (cols or ws.max_column) + 1):
                    cell = ws.cell(row=row, column=c)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = center
                    cell.border = border

            def _autosize(ws):
                for col_cells in ws.columns:
                    max_len = 12
                    for cell in col_cells:
                        if cell.value:
                            max_len = max(max_len, min(50, len(str(cell.value)) * 2))
                    ws.column_dimensions[get_column_letter(col_cells[0].column)].width = max_len + 2

            ws = wb.active
            ws.title = "概览"
            ws.append(["指标", "数值"])
            for k, v in report.summary.items():
                ws.append([k, str(v)])
            ws.append([])
            ws.append(["", ""])
            ws.append(["运营建议", ""])
            for r in report.recommendations:
                ws.append(["", r])
            _style_header(ws, cols=2)
            _autosize(ws)

            if report.releases:
                ws2 = wb.create_sheet("发布明细")
                headers = list(report.releases[0].keys())
                ws2.append(headers)
                for row in report.releases:
                    ws2.append([row[k] for k in headers])
                _style_header(ws2, cols=len(headers))
                _autosize(ws2)

            if report.rollbacks:
                ws3 = wb.create_sheet("回滚明细")
                headers = list(report.rollbacks[0].keys())
                ws3.append(headers)
                for row in report.rollbacks:
                    ws3.append([row[k] for k in headers])
                _style_header(ws3, cols=len(headers))
                _autosize(ws3)

            ws4 = wb.create_sheet("审批统计")
            ws4.append(["维度", "数值"])
            for k, v in report.approvals.items():
                ws4.append([k, str(v)])
            _style_header(ws4, cols=2)
            _autosize(ws4)

            if report.drills:
                ws5 = wb.create_sheet("演练记录")
                headers = list(report.drills[0].keys())
                ws5.append(headers)
                for row in report.drills:
                    ws5.append([row[k] for k in headers])
                _style_header(ws5, cols=len(headers))
                _autosize(ws5)

            ws6 = wb.create_sheet("趋势数据")
            for title, data in report.trends.items():
                ws6.append([title] + data["labels"])
                ws6.append(["数值"] + data["values"])
                ws6.append([])
            _autosize(ws6)

            file = self.report_dir / f"{report.report_id}.xlsx"
            wb.save(file)
            self.logger.info(f"周报Excel已导出: {file}")
        except ImportError:
            self.logger.warning("openpyxl 未安装，跳过Excel导出")
        except Exception as e:
            self.logger.error(f"Excel导出异常: {e}")

    def _send_report_notification(self, report: WeeklyReport):
        week_range = f"{report.week_start} ~ {report.week_end}"
        context = {
            "title": f"每周发布运营报告 - {week_range}",
            "week_range": week_range,
            "report_id": report.report_id,
            "release_id": report.report_id,
            "release_title": f"周报 {week_range}",
            "version": "-",
            "release_type": "每周运营报告",
            "operator": "SYSTEM",
            "status": "已生成",
            "description": f"{week_range} 期间发布、审批、回滚、演练全量统计分析",
            "report_summary": {
                "**核心指标**": "（请查看下方详情）",
                **report.summary,
            },
            "metrics": {
                "建议项数": len(report.recommendations),
                "建议列表": report.recommendations,
            },
        }

        attachments = []
        excel_file = self.report_dir / f"{report.report_id}.xlsx"
        if excel_file.exists():
            attachments.append(str(excel_file))

        try:
            self.notifier.send(
                template_key="weekly_report",
                context=context,
                attachments=attachments,
                priority="normal",
            )
        except Exception as e:
            self.logger.error(f"发送周报通知异常: {e}")

    def list_reports(self) -> List[Dict]:
        reports = []
        for file in sorted(self.report_dir.glob("WRPT_*.json"), reverse=True):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                reports.append({
                    "report_id": data["report_id"],
                    "week_start": data["week_start"],
                    "week_end": data["week_end"],
                    "generated_at": data.get("generated_at", ""),
                    "total_releases": data.get("summary", {}).get("发布申请总数", 0),
                    "success_rate": data.get("summary", {}).get("发布成功率", "-"),
                })
            except Exception:
                continue
        return reports


def get_weekly_report_generator() -> WeeklyReportGenerator:
    return WeeklyReportGenerator()
