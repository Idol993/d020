#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
末端驿站/自提柜管理系统 - 发布与自动回滚自动化平台
命令行入口 (CLI)
"""
import json
import sys
import time
from pathlib import Path
from typing import Optional

import click

from src.core.config import (
    get_config, ReleaseRequest, ReleaseType, ReleaseStatus,
)
from src.core.logger import (
    get_logger, get_cst_now_str, get_audit_logger, generate_id,
)
from src.orchestrator import (
    get_orchestrator, ScheduledTaskManager,
)
from src.precheck.engine import get_precheck_engine
from src.approval.engine import get_approval_engine
from src.release.grayscale import get_grayscale_engine
from src.reporting.drill import get_drill_manager
from src.reporting.weekly_report import get_weekly_report_generator
from src.reporting.audit_query import get_audit_query_engine


def _print_banner():
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║     🚚 末端驿站 / 自提柜管理系统 发布与自动回滚自动化平台        ║
║                                                                  ║
║     Last-Mile Station / Locker Management System                 ║
║     Release & Auto-Rollback Automation Platform                  ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
    click.secho(banner, fg="cyan", bold=True)


def _print_dict(data: dict, title: str = ""):
    if title:
        click.secho(f"\n{'='*60}", fg="yellow")
        click.secho(f"  {title}", fg="yellow", bold=True)
        click.secho(f"{'='*60}", fg="yellow")
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _progress_printer(stage: str, value: any):
    click.secho(f"  → [{get_cst_now_str()}] {stage}: {value}", fg="bright_black")


@click.group()
@click.option("--config", "config_path", type=str, default=None,
              help="配置文件路径")
@click.option("-v", "--verbose", is_flag=True, help="详细输出")
@click.pass_context
def cli(ctx, config_path: Optional[str], verbose: bool):
    """末端驿站/自提柜管理系统 - 发布与自动回滚自动化平台"""
    ctx.ensure_object(dict)
    if config_path:
        from src.core.config import ConfigManager
        ConfigManager(config_path)
    ctx.obj["verbose"] = verbose
    ctx.obj["orchestrator"] = get_orchestrator()
    ctx.obj["logger"] = get_logger("cli")


@cli.command(short_help="提交发布申请")
@click.option("-V", "--version", "version", type=str, required=True,
              help="发布版本号")
@click.option("-t", "--title", type=str, required=True, help="发布标题")
@click.option("-d", "--description", type=str, default="", help="发布描述")
@click.option("--type", "release_type", type=click.Choice(["regular", "hotfix"]),
              default="regular", help="发布类型")
@click.option("-u", "--url", "package_url", type=str, default="",
              help="发布包URL")
@click.option("--rollback-version", type=str, default="",
              help="回滚基线版本")
@click.option("-o", "--operator", type=str, required=True, help="提交人")
@click.option("--hotfix-reason", type=str, default="",
              help="Hotfix紧急原因")
@click.option("--changelog", type=str, default="", help="变更日志")
@click.pass_context
def submit(ctx, version: str, title: str, description: str, release_type: str,
           package_url: str, rollback_version: str, operator: str,
           hotfix_reason: str, changelog: str):
    """提交发布申请"""
    _print_banner()
    orch = ctx.obj["orchestrator"]

    request = ReleaseRequest(
        release_id="",
        version=version,
        title=title,
        description=description,
        release_type=ReleaseType(release_type),
        submitted_by=operator,
        submitted_at="",
        package_url=package_url,
        changelog=changelog,
        hotfix_reason=hotfix_reason,
        rollback_version=rollback_version,
    )

    pipeline = orch.submit_release(request)
    click.secho(f"\n✅ 发布申请已提交成功!", fg="green", bold=True)
    click.secho(f"   发布编号: {pipeline.request.release_id}", fg="green")
    click.secho(f"   版本号:   {pipeline.request.version}", fg="green")
    click.secho(f"   类型:     {pipeline.request.release_type.value}", fg="green")
    click.secho(f"   提交人:   {pipeline.request.submitted_by}", fg="green")
    if hotfix_reason:
        click.secho(f"   紧急原因: {hotfix_reason}", fg="red")


@cli.command(short_help="执行前置校验")
@click.argument("release_id", type=str)
@click.pass_context
def precheck(ctx, release_id: str):
    """对已提交的发布申请执行多维度前置校验"""
    orch = ctx.obj["orchestrator"]
    click.secho(f"\n🔍 开始执行前置校验 [{release_id}]", fg="blue", bold=True)

    pipeline = orch.run_precheck(
        release_id, progress_cb=_progress_printer
    )

    summary = get_precheck_engine().get_summary(release_id)
    if summary:
        click.secho(f"\n{'─'*60}", fg="yellow")
        click.secho("  前置校验结果汇总", fg="yellow", bold=True)
        click.secho(f"{'─'*60}", fg="yellow")
        click.echo(f"  通过项:  {summary.passed_checks}/{summary.total_checks}")
        click.echo(f"  失败项:  {summary.failed_checks}/{summary.total_checks}")
        click.echo(f"  总耗时:  {summary.total_duration_seconds}s")

        for r in summary.results:
            status = "✅" if r.passed else "❌"
            label_map = {
                "mail_success_rate": "寄件成功率",
                "pickup_code_accuracy": "取件码准确率",
                "terminal_online_rate": "柜机在线率",
                "system_connectivity": "系统连通性",
            }
            name = label_map.get(r.check_name, r.check_name)
            click.echo(f"\n  {status} {name}")
            click.echo(f"     {r.message}")
            click.echo(f"     得分: {r.score:.2%} / 阈值: {r.threshold:.2%} | 耗时: {r.duration_seconds}s")
            if not r.passed and r.suggestion:
                click.secho(f"     💡 修复建议:", fg="yellow")
                for line in r.suggestion.split("\n"):
                    click.echo(f"       {line}")

        if summary.overall_passed:
            click.secho(f"\n✅ 前置校验全部通过！可进入审批流程。",
                        fg="green", bold=True)
        else:
            click.secho(f"\n🚫 前置校验未通过，发布已阻断。",
                        fg="red", bold=True)
            click.secho(f"   请根据修复建议处理后重新提交。", fg="red")


@cli.command(short_help="审批流程管理")
@click.argument("release_id", type=str)
@click.option("--action", type=click.Choice(["start", "approve", "reject", "status"]),
              default="status", help="操作类型")
@click.option("--role", type=str, default="", help="审批角色 (operations/station_manager/tech)")
@click.option("--approver", type=str, default="", help="审批人标识/邮箱")
@click.option("--comment", type=str, default="", help="审批意见")
@click.option("--reason", type=str, default="", help="拒绝原因")
@click.pass_context
def approval(ctx, release_id: str, action: str, role: str, approver: str,
             comment: str, reason: str):
    """启动审批流程、审批、拒绝或查看审批状态"""
    orch = ctx.obj["orchestrator"]
    engine = get_approval_engine()

    if action == "start":
        click.secho(f"\n📝 启动审批流程 [{release_id}]", fg="blue", bold=True)
        try:
            pipeline = orch.start_approval(release_id)
            click.secho(f"✅ 审批流程已启动，请等待审批人处理", fg="green")
        except Exception as e:
            click.secho(f"❌ 启动失败: {e}", fg="red")
            sys.exit(1)

    elif action == "approve":
        if not role or not approver:
            click.secho("❌ 审批操作需要 --role 和 --approver 参数", fg="red")
            sys.exit(1)
        click.secho(f"\n👍 审批通过 [{release_id}] 节点: {role}", fg="blue", bold=True)
        try:
            pipeline = orch.approve(release_id, approver, role, comment)
            click.secho(f"✅ 审批通过已记录", fg="green")
        except Exception as e:
            click.secho(f"❌ 审批失败: {e}", fg="red")
            sys.exit(1)

    elif action == "reject":
        if not role or not approver:
            click.secho("❌ 拒绝操作需要 --role 和 --approver 参数", fg="red")
            sys.exit(1)
        if not reason:
            reason = click.prompt("请输入拒绝原因", type=str)
        click.secho(f"\n👎 审批拒绝 [{release_id}] 节点: {role}", fg="red", bold=True)
        try:
            pipeline = orch.reject(release_id, approver, role, reason)
            click.secho(f"✅ 拒绝已记录", fg="green")
        except Exception as e:
            click.secho(f"❌ 操作失败: {e}", fg="red")
            sys.exit(1)

    status = engine.get_workflow_status(release_id)
    if not status.get("exists"):
        click.secho("⚠️ 未找到审批流程记录", fg="yellow")
        return

    click.secho(f"\n{'─'*60}", fg="yellow")
    click.secho("  审批流程状态", fg="yellow", bold=True)
    click.secho(f"{'─'*60}", fg="yellow")
    click.echo(f"  流程状态: {status.get('status')}")
    click.echo(f"  审批通道: {status.get('channel_name')}")
    click.echo(f"  当前节点: 第 {status.get('current_node_index', 0) + 1} / {len(status.get('nodes', []))} 个")
    click.echo(f"  总耗时:   {status.get('total_duration_hours', '-')}h")

    for i, node in enumerate(status.get("nodes", [])):
        s = node.get("status", "pending")
        mark = "✅" if s == "approved" else ("❌" if s == "rejected" else ("⏭️" if s == "skipped" else "⏳"))
        click.echo(f"\n  {mark} 节点{i+1}: {node.get('name')} [{node.get('role')}]")
        click.echo(f"     职责: {node.get('description')}")
        click.echo(f"     状态: {s}")
        if node.get("approved_by"):
            click.echo(f"     审批人: {node.get('approved_by')} @ {node.get('approved_at')}")
        if node.get("comment"):
            click.echo(f"     意见: {node.get('comment')}")


@cli.command(short_help="执行灰度发布")
@click.argument("release_id", type=str)
@click.option("--blocking", is_flag=True, default=False,
              help="阻塞模式，等待发布完成后退出")
@click.option("--timeout", type=int, default=3600,
              help="阻塞模式超时时间(秒)")
@click.pass_context
def release(ctx, release_id: str, blocking: bool, timeout: int):
    """执行区域灰度发布（审批通过后）"""
    orch = ctx.obj["orchestrator"]
    click.secho(f"\n🚀 启动灰度发布 [{release_id}]", fg="blue", bold=True)

    try:
        pipeline = orch.start_release(
            release_id, blocking=blocking,
            progress_cb=_progress_printer
        )
    except Exception as e:
        click.secho(f"❌ 发布启动失败: {e}", fg="red")
        sys.exit(1)

    session = get_grayscale_engine().get_session(release_id)
    if not session:
        click.secho("⚠️ 未获取到发布会话信息", fg="yellow")
        return

    click.secho(f"\n{'─'*60}", fg="yellow")
    click.secho("  灰度发布配置", fg="yellow", bold=True)
    click.secho(f"{'─'*60}", fg="yellow")
    click.echo(f"  目标版本: {session.version}")
    click.echo(f"  回滚版本: {session.rollback_version}")
    click.echo(f"  目标驿站: {len(session.stations)} 个")

    for stage in session.stages:
        mark = "🟢" if stage.status == "completed" else (
            "🔵" if stage.status == "in_progress" else "⚪"
        )
        click.echo(f"\n  {mark} 阶段 {stage.stage}: {stage.name}")
        click.echo(f"     驿站类型: {', '.join(stage.station_types)}")
        click.echo(f"     区域优先级: {', '.join(stage.region_priority)}")
        click.echo(f"     发布比例: {stage.scale_percent}%")
        click.echo(f"     观察窗口: {stage.observation_minutes}分钟")
        if stage.affected_stations:
            click.echo(f"     影响驿站: {len(stage.affected_stations)}个")
        if stage.status == "completed":
            click.echo(f"     完成时间: {stage.completed_at}")

    if not blocking:
        click.secho(
            f"\n⏳ 发布已启动，非阻塞模式。使用 release status {release_id} 查看进度。",
            fg="cyan",
        )


@cli.command(short_help="查看发布进度/状态")
@click.argument("release_id", type=str)
@click.pass_context
def status(ctx, release_id: str):
    """查看发布会话的详细状态与进度"""
    orch = ctx.obj["orchestrator"]
    gs = get_grayscale_engine()
    session = gs.get_session(release_id)
    pipeline = orch.get_pipeline_status(release_id)

    click.secho(f"\n{'═'*60}", fg="cyan")
    click.secho(f"  发布状态报告 | {release_id}", fg="cyan", bold=True)
    click.secho(f"{'═'*60}", fg="cyan")

    if pipeline and pipeline.get("exists", True):
        click.echo(f"  整体状态: {pipeline.get('status')}")
        click.echo(f"  当前步骤: {pipeline.get('current_step')}")
        click.echo(f"  创建时间: {pipeline.get('created_at')}")
        click.echo(f"  更新时间: {pipeline.get('updated_at')}")

        history = pipeline.get("step_history", [])
        if history:
            click.secho(f"\n  执行轨迹:", fg="bright_black")
            for h in history:
                click.echo(f"    [{h.get('timestamp')}] {h.get('action')} - {h.get('detail')}")

    if session:
        label_map = {
            "closed": "正常运行",
            "half_open": "恢复探测",
            "open": "已熔断",
        }
        click.secho(f"\n  熔断状态: {label_map.get(session.circuit_breaker_state.value, '未知')}",
                    fg="red" if session.circuit_breaker_state.value == "open" else "green")
        click.echo(f"  监控状态: {'运行中' if session.active_monitoring else '已停止'}")
        click.echo(f"  总影响驿站: {len(session.all_affected_stations)} 个")

        if session.events:
            click.secho(f"\n  ⚠️ 熔断事件 ({len(session.events)} 次):", fg="red")
            metric_map = {
                "pickup_failure_rate": "取件失败率",
                "terminal_offline_rate": "柜机离线率",
                "mail_abnormal_rate": "寄件异常率",
            }
            for ev in session.events:
                m = metric_map.get(ev.trigger_metric, ev.trigger_metric)
                click.echo(
                    f"    阶段{ev.trigger_stage} 触发: {m}={ev.trigger_value:.2%} "
                    f"(阈值{ev.threshold:.2%}) @ {ev.triggered_at}"
                )
                if ev.rollback_successful:
                    click.echo(
                        f"    ✅ 回滚成功, 耗时 {ev.rollback_duration_seconds}s "
                        f"[{ev.rollback_started} → {ev.rollback_completed}]"
                    )


@cli.command(short_help="手动触发回滚")
@click.argument("release_id", type=str)
@click.option("--reason", type=str, required=True, help="回滚原因")
@click.option("--operator", type=str, default="MANUAL", help="操作人")
@click.pass_context
def rollback(ctx, release_id: str, reason: str, operator: str):
    """对正在进行的发布执行手动回滚操作"""
    orch = ctx.obj["orchestrator"]
    click.secho(f"\n🔄 手动触发回滚 [{release_id}]", fg="red", bold=True)
    click.echo(f"  原因: {reason}")
    click.echo(f"  操作人: {operator}")

    if not click.confirm("\n⚠️  确认立即执行回滚？此操作不可撤销"):
        click.echo("操作已取消")
        return

    try:
        pipeline = orch.manual_rollback(release_id, reason, operator)
        session = get_grayscale_engine().get_session(release_id)
        metric_map = {
            "pickup_failure_rate": "取件失败率",
            "terminal_offline_rate": "柜机离线率",
            "mail_abnormal_rate": "寄件异常率",
            "manual_trigger": "手动触发",
        }
        if session and session.events:
            ev = session.events[-1]
            m = metric_map.get(ev.trigger_metric, ev.trigger_metric)
            click.secho(f"\n{'─'*60}", fg="yellow")
            click.secho("  回滚事件报告", fg="yellow", bold=True)
            click.secho(f"{'─'*60}", fg="yellow")
            click.echo(f"  事件ID:     {ev.event_id}")
            click.echo(f"  触发阶段:   第 {ev.trigger_stage} 阶段")
            click.echo(f"  触发指标:   {m} = {ev.trigger_value:.2%} (阈值 {ev.threshold:.2%})")
            click.echo(f"  触发时间:   {ev.triggered_at}")
            click.echo(f"  影响驿站:   {len(ev.affected_stations)} 个")
            click.echo(f"  回滚开始:   {ev.rollback_started or '-'}")
            click.echo(f"  回滚完成:   {ev.rollback_completed or '-'}")
            click.echo(f"  回滚耗时:   {ev.rollback_duration_seconds}s")
            if ev.rollback_successful:
                click.secho(f"  回滚结果:   ✅ 成功", fg="green", bold=True)
            else:
                click.secho(f"  回滚结果:   ⚠️ 需人工介入", fg="yellow", bold=True)
            click.echo(f"  版本:       {session.version} → {session.rollback_version}")
        else:
            click.secho("\n✅ 回滚流程已执行", fg="green")
    except Exception as e:
        click.secho(f"❌ 回滚操作异常: {e}", fg="red")
        sys.exit(1)


@cli.command(short_help="执行回滚演练")
@click.pass_context
def drill(ctx):
    """立即执行一次回滚演练（验证熔断与回滚机制）"""
    dm = get_drill_manager()
    click.secho(f"\n🎯 执行回滚演练", fg="blue", bold=True)

    if not click.confirm("确认立即执行演练？演练不会影响真实业务。"):
        click.echo("操作已取消")
        return

    result = dm.execute_drill()

    click.secho(f"\n{'─'*60}", fg="yellow")
    click.secho("  演练结果报告", fg="yellow", bold=True)
    click.secho(f"{'─'*60}", fg="yellow")
    mark = "✅" if result.success else ("⚠️" if result.status == "partial_failure" else "❌")
    click.echo(f"  演练ID: {result.drill_id}")
    click.echo(f"  结果: {mark} {'成功' if result.success else result.status}")
    click.echo(f"  模拟版本: {result.simulated_version} → {result.rollback_version}")
    click.echo(f"  熔断触发: {'是' if result.circuit_breaker_triggered else '否'}")
    click.echo(f"  自动回滚: {'是' if result.rollback_executed else '否'}")
    click.echo(f"  回滚成功: {'是' if result.rollback_success else '否'}")
    click.echo(f"  回滚耗时: {result.rollback_duration_seconds}s")
    click.echo(f"  步骤通过率: {result.steps_passed}/{result.steps_total}")

    metric_map = {
        "pickup_failure_rate": "取件失败率",
        "terminal_offline_rate": "柜机离线率",
    }
    if result.trigger_metric:
        m = metric_map.get(result.trigger_metric, result.trigger_metric)
        click.echo(f"  触发指标: {m}={result.trigger_value:.2%} (阈值{result.threshold:.2%})")

    click.secho(f"\n  演练步骤:", fg="bright_black")
    for step in result.step_details:
        mark = "✅" if step["passed"] else "❌"
        click.echo(f"    {mark} {step['step']} @ {step['timestamp']}")


@cli.command(short_help="生成周报")
@click.option("--start", "week_start", type=str, default=None,
              help="周开始日期 YYYY-MM-DD")
@click.option("--end", "week_end", type=str, default=None,
              help="周结束日期 YYYY-MM-DD")
@click.pass_context
def report(ctx, week_start: Optional[str], week_end: Optional[str]):
    """生成并发送每周发布运营报告"""
    wr = get_weekly_report_generator()
    click.secho(f"\n📊 生成每周运营报告", fg="blue", bold=True)

    report = wr.generate(week_start, week_end)

    click.secho(f"\n{'═'*60}", fg="cyan")
    click.secho(f"  每周运营报告 | {report.week_start} ~ {report.week_end}",
                fg="cyan", bold=True)
    click.secho(f"{'═'*60}", fg="cyan")

    click.secho("\n  📌 核心指标:", fg="yellow")
    for k, v in report.summary.items():
        click.echo(f"    {k}: {v}")

    click.secho("\n  📈 趋势数据可用:", fg="yellow")
    for name in report.trends.keys():
        click.echo(f"    • {name}")

    click.secho("\n  💡 运营建议:", fg="yellow")
    for idx, rec in enumerate(report.recommendations, 1):
        click.echo(f"    {idx}. {rec}")

    if report.charts:
        click.secho(f"\n  📊 已生成趋势图 {len(report.charts)} 张:", fg="yellow")
        for name, path in report.charts.items():
            click.echo(f"    • {name}: {path}")

    excel_path = get_config().get_storage_path("report_dir") / f"{report.report_id}.xlsx"
    click.secho(f"\n  📄 Excel报告: {excel_path if excel_path.exists() else '未生成'}",
                fg="yellow")


@cli.command(name="list", short_help="查询/检索记录")
@click.option("--type", "record_type",
              type=click.Choice(["release", "rollback", "drill", "approval", "audit"]),
              default="release", help="记录类型")
@click.option("--start", "start_time", type=str, default=None,
              help="开始时间 YYYY-MM-DD [HH:MM:SS]")
@click.option("--end", "end_time", type=str, default=None,
              help="结束时间 YYYY-MM-DD [HH:MM:SS]")
@click.option("--version", type=str, default=None, help="版本号过滤")
@click.option("--station", type=str, default=None, help="驿站ID过滤")
@click.option("--format", "output_format",
              type=click.Choice(["table", "json", "csv", "excel"]),
              default="table", help="输出格式")
@click.option("--output", type=str, default=None, help="导出文件路径")
@click.pass_context
def list_records(ctx, record_type: str, start_time: Optional[str],
                 end_time: Optional[str], version: Optional[str],
                 station: Optional[str], output_format: str,
                 output: Optional[str]):
    """查询发布、回滚、演练、审批、审计历史记录"""
    aq = get_audit_query_engine()

    if record_type == "release":
        records = aq.query_release_records(start_time, end_time, station, version)
    elif record_type == "rollback":
        records = aq.query_rollback_records(start_time, end_time, station, version)
    elif record_type == "drill":
        raw = aq.query_drill_records(start_time, end_time)
        records = [r.to_dict() for r in raw]
    elif record_type == "approval":
        records = aq.query_approval_records(start_time, end_time)
    else:
        alog = get_audit_logger()
        records = alog.query_logs(start_time, end_time)

    if not records:
        click.secho("⚠️ 未查询到匹配的记录", fg="yellow")
        return

    click.secho(f"\n查询到 {len(records)} 条记录", fg="green")

    if output_format == "json" and not output:
        click.echo(json.dumps(records, ensure_ascii=False, indent=2))
        return

    if output_format in ["csv", "excel", "json"] and output:
        path = aq.export(records, output, output_format)
        click.secho(f"✅ 已导出到: {path}", fg="green")
        return

    click.secho(f"\n{'─'*80}", fg="yellow")
    if record_type == "release":
        click.secho(f"  {'发布编号':<22} {'版本':<12} {'状态':<14} {'阶段':<6} {'创建时间':<20}",
                    fg="yellow", bold=True)
        click.secho(f"{'─'*80}", fg="yellow")
        for r in records[:50]:
            click.echo(
                f"  {r['release_id']:<22} {r['version']:<12} "
                f"{r['status']:<14} {r['current_stage']}/{r['total_stages']:<4} "
                f"{r['created_at']:<20}"
            )
    elif record_type == "rollback":
        for r in records[:20]:
            click.echo(
                f"  [{r['triggered_at']}] {r['release_id']} "
                f"阶段{r['trigger_stage']} {r['trigger_metric']}={r['trigger_value']} "
                f"→ {r['rollback_successful']} ({r['rollback_duration_seconds']}s)"
            )
    elif record_type == "drill":
        for r in records[:20]:
            mark = "✅" if r.get("success") else "❌"
            click.echo(
                f"  [{r.get('started_at', '')[:16]}] {r['drill_id']} "
                f"{mark} {r.get('steps_passed', 0)}/{r.get('steps_total', 6)} "
                f"回滚{r.get('rollback_duration_seconds', 0)}s"
            )
    elif record_type == "approval":
        for r in records[:20]:
            passed = "✅" if r.get("overall_passed") else ("❌" if r.get("rejected_by") else "⏳")
            click.echo(
                f"  [{r['created_at'][:16]}] {r['release_id']} "
                f"{r['channel_name']:<6} {passed} "
                f"{r.get('status'):<10} {r.get('total_duration_hours', '-')}h"
            )
    else:
        for r in records[:50]:
            click.echo(
                f"  [{r['timestamp']}] {r['operator']:<12} "
                f"{r['action']:<28} {r['target_id']:<20} {r['target_type']}"
            )

    if len(records) > 50:
        click.secho(f"\n... 仅显示前50条，共 {len(records)} 条。使用 --format excel --output 导出完整数据。",
                    fg="bright_black")


@cli.command(short_help="启动常驻后台服务（定时任务）")
@click.option("--run-demo", is_flag=True, help="启动后台同时执行一次完整演示流程")
@click.pass_context
def serve(ctx, run_demo: bool):
    """启动平台常驻服务，执行定时演练、定时周报等任务"""
    _print_banner()
    orch = ctx.obj["orchestrator"]
    scheduler = ScheduledTaskManager(orch)

    click.secho("启动平台后台常驻服务...", fg="green")
    scheduler.start()

    if run_demo:
        click.secho("\n🎬 将执行一次完整演示流程", fg="cyan")
        import threading
        threading.Thread(target=_run_demo_flow, args=(ctx,), daemon=True).start()

    click.secho("\n  后台服务已启动，按 Ctrl+C 退出\n", fg="green")
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        click.secho("\n正在停止服务...", fg="yellow")
        scheduler.stop()
        click.secho("服务已停止", fg="green")


def _run_demo_flow(ctx):
    try:
        click.echo("\n🎬 === 开始完整流程演示 ===")
        orch = ctx.obj["orchestrator"]

        demo_release = ReleaseRequest(
            release_id="",
            version="v2.7.3",
            title="[演示] 柜机心跳机制与取件码优化",
            description="演示用发布申请：优化柜机心跳上报频率与取件码生成算法",
            release_type=ReleaseType.REGULAR,
            submitted_by="demo_user@company.com",
            submitted_at="",
            package_url="https://artifacts.company.com/release/v2.7.3.tar.gz",
            rollback_version="v2.7.2",
            changelog="1. 心跳包压缩率提升30%\n2. 取件码生成算法重构\n3. PDA解码性能优化",
        )
        pipeline = orch.submit_release(demo_release)
        rid = pipeline.request.release_id
        click.echo(f"  ✅ 提交发布: {rid}")
        time.sleep(1)

        orch.run_precheck(rid)
        click.echo(f"  ✅ 前置校验完成")
        time.sleep(1)

        pipeline = orch.start_approval(rid)
        click.echo(f"  ✅ 审批流程启动")
        time.sleep(0.5)

        for role, approver in [("operations", "ops_manager@company.com"),
                               ("station_manager", "station_head@company.com"),
                               ("tech", "tech_lead@company.com")]:
            orch.approve(rid, approver, role, f"[演示审批] {role}通过")
            click.echo(f"  ✅ 审批通过: {role}")
            time.sleep(0.3)

        orch.start_release(rid, blocking=False)
        click.echo(f"  ✅ 灰度发布启动，后台自动运行中")

    except Exception as e:
        click.secho(f"❌ 演示异常: {e}", fg="red")


@cli.command(short_help="完整演示一次发布全流程")
@click.option("--mode", type=click.Choice(["normal", "fail_precheck", "trigger_rollback"]),
              default="normal", help="演示模式")
@click.pass_context
def demo(ctx, mode: str):
    """完整演示发布流程：提交→校验→审批→灰度→监控"""
    _print_banner()
    click.secho(f"\n🎬 演示模式: {mode}", fg="cyan", bold=True)
    orch = ctx.obj["orchestrator"]

    versions = {
        "normal": ("v2.8.0", "v2.7.5"),
        "fail_precheck": ("v2.8.0-bad", "v2.7.5"),
        "trigger_rollback": ("v2.8.0-rc1", "v2.7.5"),
    }
    ver, rb_ver = versions[mode]

    titles = {
        "normal": "[演示-正常] 常规版本发布：PDA解码优化+柜机心跳调优",
        "fail_precheck": "[演示-阻断] 质量不达标的版本发布（将被前置校验阻断）",
        "trigger_rollback": "[演示-熔断] 高风险变更（可能触发熔断回滚）",
    }

    click.secho(f"\n[Step 1/5] 📝 提交发布申请", fg="blue", bold=True)
    request = ReleaseRequest(
        release_id="",
        version=ver,
        title=titles[mode],
        description="自动化平台功能演示用例",
        release_type=ReleaseType.REGULAR,
        submitted_by="demo_operator@company.com",
        submitted_at="",
        package_url=f"https://artifacts.company.com/{ver}.tar.gz",
        rollback_version=rb_ver,
        changelog="演示变更日志",
    )
    pipeline = orch.submit_release(request)
    rid = pipeline.request.release_id
    click.echo(f"  → 发布编号: {rid}")
    click.echo(f"  → 版本: {ver}")
    time.sleep(0.5)

    click.secho(f"\n[Step 2/5] 🔍 执行多维前置校验", fg="blue", bold=True)
    orch.run_precheck(rid, progress_cb=lambda a, b: click.echo(f"  → {a}: {int(b*100)}%"))
    summary = get_precheck_engine().get_summary(rid)
    if summary and not summary.overall_passed:
        click.secho(f"\n🚫 前置校验阻断，演示结束（符合预期：{mode}模式）",
                    fg="red", bold=True)
        return
    time.sleep(0.5)

    click.secho(f"\n[Step 3/5] 📝 三级审批流转", fg="blue", bold=True)
    orch.start_approval(rid)
    approval_order = [
        ("operations", "ops_mgr@company.com", "业务影响评估通过"),
        ("station_manager", "station_head@company.com", "客诉风险可控"),
        ("tech", "tech_lead@company.com", "代码Review通过"),
    ]
    for role, approver, comment in approval_order:
        orch.approve(rid, approver, role, comment)
        click.echo(f"  → 审批通过 [{role}] {approver}: {comment}")
        time.sleep(0.3)
    time.sleep(0.5)

    click.secho(f"\n[Step 4/5] 🚀 区域灰度发布启动", fg="blue", bold=True)
    orch.start_release(rid, blocking=False)
    session = get_grayscale_engine().get_session(rid)
    if session:
        for stage in session.stages:
            click.echo(f"  → 阶段{stage.stage}: {stage.name} ({stage.scale_percent}%, 观察{stage.observation_minutes}分)")

    click.secho(f"\n[Step 5/5] 👀 实时监控中 (为演示加速运行30秒)...", fg="blue", bold=True)
    click.echo("  → 每5分钟采集一次指标（演示模式下加速）")

    for i in range(6):
        time.sleep(3)
        session = get_grayscale_engine().get_session(rid)
        if not session:
            break
        status_text = f"阶段{session.current_stage_index+1}/{len(session.stages)}"
        if session.circuit_breaker_state.value == "open":
            click.secho(f"  ⏱️  [{i*5}s] 状态: {status_text} | 熔断: 已触发 ⚠️", fg="red")
            if session.events and session.events[-1].rollback_completed:
                break
        else:
            click.echo(f"  ⏱️  [{i*5}s] 状态: {status_text} | 熔断: 正常")

    get_grayscale_engine().stop(rid)
    time.sleep(1)

    final = orch.get_pipeline_status(rid)
    click.secho(f"\n{'═'*60}", fg="cyan")
    click.secho(f"  演示完成 | 最终状态: {final.get('status', 'unknown')}",
                fg="cyan", bold=True)
    click.secho(f"{'═'*60}", fg="cyan")
    click.secho(f"\n  使用 status {rid} 查看完整详情。", fg="bright_black")
    click.secho(f"  使用 list release 查看历史发布记录。", fg="bright_black")


if __name__ == "__main__":
    cli(obj={})
