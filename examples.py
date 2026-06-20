#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
末端驿站/自提柜系统 - 发布与自动回滚自动化平台
================================================
示例脚本：演示3种典型场景

使用方式：
    python examples.py              # 交互式选择示例
    python examples.py 1            # 直接运行示例1
"""
import json
import sys
import time
from pathlib import Path

from src.core.config import (
    ReleaseRequest, ReleaseType, ReleaseStatus,
    get_config,
)
from src.core.logger import (
    get_logger, get_cst_now_str,
)
from src.orchestrator import get_orchestrator
from src.precheck.engine import get_precheck_engine
from src.approval.engine import get_approval_engine
from src.release.grayscale import get_grayscale_engine
from src.reporting.drill import get_drill_manager
from src.reporting.weekly_report import get_weekly_report_generator
from src.reporting.audit_query import get_audit_query_engine


logger = get_logger("examples")


def _print_title(text: str, emoji: str = "📌"):
    line = "═" * 70
    print()
    print(f"\033[96m{line}\033[0m")
    print(f"\033[96m  {emoji} {text}\033[0m")
    print(f"\033[96m{line}\033[0m")
    print()


def _print_ok(text: str):
    print(f"  \033[92m✅ {text}\033[0m")


def _print_warn(text: str):
    print(f"  \033[93m⚠️  {text}\033[0m")


def _print_err(text: str):
    print(f"  \033[91m❌ {text}\033[0m")


def _print_info(text: str):
    print(f"  ℹ️  {text}")


def example_1_normal_release():
    """
    示例1：常规成功发布流程
    ======================================
    场景：正常迭代版本，质量达标，三级审批通过，灰度平稳完成
    """
    _print_title("示例1: 常规成功发布流程 (Regular Success)", "🚀")

    orch = get_orchestrator()
    pre = get_precheck_engine()
    appr = get_approval_engine()
    gs = get_grayscale_engine()

    # Step 1: 提交发布申请
    _print_info("Step 1: 提交常规迭代发布申请")
    request = ReleaseRequest(
        release_id="",
        version="v2.8.0",
        title="PDA扫码解码性能优化 + 柜机心跳机制调优",
        description="""
        1. 升级PDA端扫码解码库，解码速度提升35%
        2. 优化柜机心跳上报频率，从30s→60s，降低IoT平台压力
        3. 新增柜机离线自动重连指数退避算法
        """,
        release_type=ReleaseType.REGULAR,
        submitted_by="dev_lihua@company.com",
        submitted_at="",
        package_url="https://artifacts.company.com/release/station/v2.8.0.tar.gz",
        rollback_version="v2.7.5",
        changelog="",
    )
    pipeline = orch.submit_release(request)
    rid = pipeline.request.release_id
    _print_ok(f"发布申请已提交，发布编号: {rid}")
    _print_info(f"版本: v2.8.0 | 基线回滚版本: v2.7.5")
    time.sleep(0.5)

    # Step 2: 执行前置校验
    _print_info("\nStep 2: 执行4维前置质量门禁校验")
    orch.run_precheck(rid, progress_cb=lambda n, p: _print_info(f"校验进度 {int(p*100)}%: {n}"))
    summary = pre.get_summary(rid)
    if summary:
        _print_ok(
            f"前置校验完成: 通过 {summary.passed_checks}/{summary.total_checks} 项, "
            f"耗时 {summary.total_duration_seconds}s"
        )
        if not summary.overall_passed:
            _print_err("前置校验未通过（演示中止，实际业务中需修复后重试）")
            return
    time.sleep(0.5)

    # Step 3: 审批流程
    _print_info("\nStep 3: 启动三级串行审批流程")
    orch.start_approval(rid)
    approvers = [
        ("operations", "wangfang_ops@company.com",
         "评估：本次发布为性能优化类，用户体验无负面影响，业务指标可预期提升"),
        ("station_manager", "zhangwei_station@company.com",
         "评估：客诉风险低，PDA扫码优化提升驿站操作员效率，无网点运营风险"),
        ("tech", "chenming_techlead@company.com",
         "评估：代码Review通过，架构影响范围可控，回滚方案完备"),
    ]
    for role, approver, comment in approvers:
        orch.approve(rid, approver, role, comment)
        role_label = {"operations": "运营", "station_manager": "驿站负责人", "tech": "技术"}.get(role, role)
        _print_ok(f"审批通过 [{role_label}] {approver}")
        _print_info(f"  审批意见: {comment[:40]}...")
    wf = appr.get_workflow_status(rid)
    _print_ok(f"审批全部通过，总耗时: {wf.get('total_duration_hours', '-')} 小时 (模拟)")
    time.sleep(0.5)

    # Step 4: 灰度发布（加速运行）
    _print_info("\nStep 4: 启动三级灰度发布（偏远→社区→核心商圈）")
    orch.start_release(rid, blocking=False)
    session = gs.get_session(rid)
    if session:
        for stage in session.stages:
            _print_info(
                f"  阶段 {stage.stage}: {stage.name} | 比例 {stage.scale_percent}% | "
                f"观察窗口 {stage.observation_minutes} 分钟"
            )

    _print_info("\n模拟观察期（加速运行15秒，正常需数小时）...")
    for i in range(5):
        time.sleep(3)
        session = gs.get_session(rid)
        if not session:
            break
        cb_state = session.circuit_breaker_state.value
        mark = "🟢" if cb_state == "closed" else "🔴"
        stage_progress = min(session.current_stage_index + 2, len(session.stages))
        _print_info(
            f"  [{(i+1)*3:02d}s] 阶段{stage_progress}/{len(session.stages)} | "
            f"熔断状态 {mark} {cb_state} | 影响驿站 {len(session.all_affected_stations)}"
        )

    gs.stop(rid)
    time.sleep(1)

    # 最终报告
    final = orch.get_pipeline_status(rid)
    _print_info("\n════════════════════════════════════════════")
    _print_ok(f"发布最终状态: {final.get('status')}")
    _print_ok(f"共影响驿站: {len(session.all_affected_stations) if session else 0} 个")
    _print_info(f"使用命令查看完整详情: python main.py status {rid}")


def example_2_precheck_blocked():
    """
    示例2：前置校验阻断发布
    ======================================
    场景：质量不达标的版本被前置校验自动阻断，生成详细修复建议
    """
    _print_title("示例2: 前置校验阻断发布 (Precheck Blocked)", "🚫")

    orch = get_orchestrator()
    pre = get_precheck_engine()

    _print_info("提交一个质量未达标的版本（将被自动阻断）")
    request = ReleaseRequest(
        release_id="",
        version="v2.8.0-rc-bad",
        title="[问题版本] 未经充分测试的紧急版本",
        description="该版本未经过完整回归测试，预期会被前置校验阻断",
        release_type=ReleaseType.REGULAR,
        submitted_by="dev_newbie@company.com",
        submitted_at="",
        package_url="https://artifacts.company.com/staging/v2.8.0-rc-bad.tar.gz",
        rollback_version="v2.7.5",
    )
    pipeline = orch.submit_release(request)
    rid = pipeline.request.release_id
    _print_ok(f"发布编号: {rid}")

    _print_info("\n执行前置校验（将模拟触发多项不达标）...")
    orch.run_precheck(rid)
    summary = pre.get_summary(rid)

    if summary:
        print()
        for r in summary.results:
            if r.passed:
                _print_ok(f"{r.check_name}: {r.score:.2%} ≥ {r.threshold:.2%}")
            else:
                _print_err(f"{r.check_name}: {r.score:.2%} < {r.threshold:.2%}")
                for line in r.suggestion.split("\n")[:3]:
                    _print_info(f"  💡 {line}")

        print()
        if summary.overall_passed:
            _print_ok("校验通过（该版本模拟失败，如有此输出请检查随机种子）")
        else:
            _print_err("=" * 60)
            _print_err(f"🚫 发布已自动阻断！失败 {summary.failed_checks}/{summary.total_checks} 项")
            _print_err("   系统已向开发人员发送阻断通知邮件/消息")
            _print_err("   请根据上方修复建议处理后重新提交")
            _print_err("=" * 60)


def example_3_circuit_breaker_and_rollback():
    """
    示例3：熔断触发 → 自动回滚
    ======================================
    场景：灰度期间取件失败率突增突破阈值，自动熔断→9步回滚→链路验证
    """
    _print_title("示例3: 熔断触发 + 自动回滚 (Circuit Breaker + Rollback)", "🚨")

    orch = get_orchestrator()
    appr = get_approval_engine()
    gs = get_grayscale_engine()
    dm = get_drill_manager()

    _print_info("Step 1: 提交并审批发布（流程加速）")
    request = ReleaseRequest(
        release_id="",
        version="v2.9.0-experimental",
        title="[高风险] 取件码加密算法全面升级",
        description="""
        取件码算法从SHA1切换为国密SM4，涉及服务端+PDA+柜机三端同步升级。
        若有一端升级不同步将导致大面积取件失败。
        """,
        release_type=ReleaseType.HOTFIX,
        submitted_by="senior_dev@company.com",
        submitted_at="",
        package_url="https://artifacts.company.com/hotfix/v2.9.0-exp.tar.gz",
        rollback_version="v2.8.0",
        hotfix_reason="安全合规紧急要求，月底前需完成算法切换",
    )
    pipeline = orch.submit_release(request)
    rid = pipeline.request.release_id
    _print_ok(f"发布编号: {rid}")

    _print_info("Step 2: 前置校验 + 紧急并行审批")
    orch.run_precheck(rid)
    orch.start_approval(rid)
    for role, approver in [
        ("operations", "ops_oncall@company.com"),
        ("station_manager", "station_oncall@company.com"),
        ("tech", "tech_oncall@company.com"),
    ]:
        try:
            orch.approve(rid, approver, role, "紧急审批：合规截止期临近，同意加速上线")
        except Exception:
            pass
    _print_ok("审批已完成（Hotfix并行模式）")

    _print_info("\nStep 3: 启动灰度发布，模拟阶段2取件失败率飙升")
    orch.start_release(rid, blocking=False)
    session = gs.get_session(rid)

    if session:
        _print_info(f"目标版本 {session.version} → 回滚基线 {session.rollback_version}")
        for stage in session.stages[:2]:
            _print_info(f"  阶段{stage.stage}: {stage.name} - {stage.scale_percent}%")

    _print_info("\n模拟监控运行（将在阶段1/2触发取件失败率=8.5% > 阈值3%）...")
    time.sleep(3)

    # 模拟触发手动回滚（演示快速看到效果）
    _print_warn("\n⚠️  [告警] 取件失败率 8.5% > 阈值 3%，持续2个采样周期")
    _print_warn("⚠️  [告警] 柜机离线率 6.2% 接近阈值 8%")
    _print_warn("🚨 达到熔断条件！触发自动回滚流程...")
    time.sleep(1)

    event = orch.manual_rollback(
        rid,
        reason="模拟熔断：阶段2取件失败率8.5%突破安全阈值3%，连续2次采样异常",
        operator="SYSTEM[AUTO-CIRCUIT-BREAKER]"
    )

    if event:
        print()
        _print_err("═══ 🚨 熔断与自动回滚执行报告 ═══")
        _print_err(f"  熔断事件ID: {event.event_id}")
        _print_err(f"  触发阶段: 第 {event.trigger_stage} 阶段")
        metric_map = {
            "pickup_failure_rate": "取件失败率",
            "terminal_offline_rate": "柜机离线率",
            "mail_abnormal_rate": "寄件异常率",
        }
        metric = metric_map.get(event.trigger_metric, event.trigger_metric)
        _print_err(f"  触发指标: {metric} = {event.trigger_value:.2%} (阈值 {event.threshold:.2%})")
        _print_err(f"  触发时间: {event.triggered_at}")
        _print_err(f"  影响驿站: {len(event.affected_stations)} 个（已自动回滚）")
        print()
        _print_ok(f"  ✅ 回滚启动: {event.rollback_started}")
        _print_ok(f"  ✅ 回滚完成: {event.rollback_completed}")
        _print_ok(f"  ✅ 回滚耗时: {event.rollback_duration_seconds} 秒 (MTTR)")
        result_text = "成功" if event.rollback_successful else "部分失败需人工介入"
        _print_ok(f"  ✅ 回滚结果: {result_text}")
        print()
        steps = [
            "1. 暂停所有灰度发布流水线",
            "2. 版本标记切换 v2.9.0 → v2.8.0",
            "3. API网关路由权重100%切回旧版本",
            "4. 配置中心版本回滚",
            "5. 推送终端资源更新通知",
            "6. 核心服务实例滚动重启",
            "7. 全部服务健康检查通过",
            "8. 核心链路冒烟测试通过（寄件/取件/查件）",
            "9. 业务监控重启，设定60分钟观察窗口",
        ]
        _print_info("  回滚执行步骤:")
        for s in steps:
            _print_ok(s)
        print()
        _print_info("  📧 已通过【企微+钉钉+邮件】发送结构化回滚报告给:")
        _print_info("     • 技术OnCall团队")
        _print_info("     • 运营团队")
        _print_info("     • 驿站管理团队")
        _print_info("     • 技术管理层")

    _print_info("\n" + "═" * 60)
    _print_ok("本次熔断演练完成！系统自动恢复到稳定版本 v2.8.0")
    _print_info("后续动作：")
    _print_info("  1. 根因分析：PDA端SM4解码库与老固件兼容性问题")
    _print_info("  2. 修复方案：增加版本协商+灰度范围缩小至1%先行验证")
    _print_info("  3. 知识库沉淀：三端同步升级类变更需增加兼容性测试Checklist")


def example_4_weekly_report_and_audit():
    """
    示例4：周报生成 + 审计查询
    ======================================
    场景：每周运营报表生成与审计日志查询导出
    """
    _print_title("示例4: 运营周报 + 审计查询 (Weekly Report + Audit)", "📊")

    wr = get_weekly_report_generator()
    aq = get_audit_query_engine()
    dm = get_drill_manager()

    _print_info("Step 1: 执行一次月度回滚演练（先产生一些数据）")
    drill_result = dm.execute_drill()
    mark = "✅" if drill_result.success else "❌"
    _print_ok(
        f"演练完成: {mark} {drill_result.drill_id} | "
        f"步骤通过率 {drill_result.steps_passed}/{drill_result.steps_total} | "
        f"回滚耗时 {drill_result.rollback_duration_seconds}s"
    )

    _print_info("\nStep 2: 生成上周运营周报（含趋势图 + Excel导出）")
    report = wr.generate()

    print()
    _print_ok(f"周报ID: {report.report_id}")
    _print_ok(f"统计周期: {report.week_start} ~ {report.week_end}")
    print()
    _print_info("📌 核心指标摘要:")
    for k, v in list(report.summary.items())[:10]:
        _print_info(f"   {k}: {v}")
    print()
    _print_info("💡 本周运营建议:")
    for i, rec in enumerate(report.recommendations, 1):
        print(f"   {i}. {rec[:80]}")
    print()

    excel_path = get_config().get_storage_path("report_dir") / f"{report.report_id}.xlsx"
    json_path = get_config().get_storage_path("report_dir") / f"{report.report_id}.json"
    _print_ok(f"📄 Excel报告已生成: {excel_path if excel_path.exists() else '(openpyxl未安装)'}")
    _print_ok(f"📄 JSON数据已生成: {json_path}")
    if report.charts:
        _print_ok(f"📊 趋势图({len(report.charts)}张):")
        for name, path in list(report.charts.items())[:3]:
            _print_info(f"   • {name}: {path}")

    _print_info("\nStep 3: 多维审计查询演示")
    releases = aq.query_release_records()
    _print_ok(f"查询到历史发布记录: {len(releases)} 条")

    rollbacks = aq.query_rollback_records()
    _print_ok(f"查询到历史回滚记录: {len(rollbacks)} 条")

    drills = aq.query_drill_records()
    _print_ok(f"查询到演练记录: {len(drills)} 条")

    # 演示导出
    export_dir = Path("./storage/exports")
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = aq.export(releases, str(export_dir / "release_history.xlsx"), "excel")
    _print_ok(f"发布记录已导出到: {export_path}")

    _print_info("\n══════════════════════════════════════")
    _print_ok("报表与审计模块功能验证完成！")
    _print_info("更多查询命令：")
    _print_info("  python main.py list --type release --start '2026-06-14'")
    _print_info("  python main.py list --type rollback --format excel --output rb.xlsx")
    _print_info("  python main.py list --type audit --operator 'tech_lead@company.com'")


EXAMPLES = [
    ("常规成功发布流程", example_1_normal_release,
     "提交→校验→审批→灰度→完成，全流程正向案例"),
    ("前置校验阻断发布", example_2_precheck_blocked,
     "质量不达标版本被自动阻断，输出结构化修复建议"),
    ("熔断触发 + 自动回滚", example_3_circuit_breaker_and_rollback,
     "灰度期间异常→熔断→9步回滚→结构化报告通知"),
    ("周报生成 + 审计查询", example_4_weekly_report_and_audit,
     "运营指标报表、趋势图、多维审计检索与导出"),
]


def main():
    print()
    print("\033[95m" + "╔" + "═" * 70 + "╗" + "\033[0m")
    print("\033[95m" + "║       末端驿站/自提柜系统 发布与自动回滚自动化平台 - 示例演示        ║" + "\033[0m")
    print("\033[95m" + "╚" + "═" * 70 + "╝" + "\033[0m")
    print()

    if len(sys.argv) > 1:
        try:
            idx = int(sys.argv[1]) - 1
            if 0 <= idx < len(EXAMPLES):
                EXAMPLES[idx][1]()
                return
        except ValueError:
            pass

    print("请选择要运行的示例（输入序号 1-4，回车运行全部）:")
    print()
    for i, (name, _, desc) in enumerate(EXAMPLES, 1):
        print(f"  [{i}] {name}")
        print(f"      └─ {desc}")
    print(f"  [0] 运行全部示例")
    print()

    try:
        choice = input("▶  请选择 [0-4] (默认 0): ").strip() or "0"
        idx = int(choice)
    except (ValueError, KeyboardInterrupt):
        idx = 0

    if idx == 0:
        for i, (name, func, _) in enumerate(EXAMPLES, 1):
            print()
            print(f"\033[95m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
            print(f"\033[95m  运行示例 {i}/{len(EXAMPLES)}: {name}\033[0m")
            print(f"\033[95m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
            try:
                func()
            except Exception as e:
                _print_err(f"示例运行异常: {e}")
                import traceback
                traceback.print_exc()
            time.sleep(1)
    elif 1 <= idx <= len(EXAMPLES):
        EXAMPLES[idx - 1][1]()
    else:
        _print_err("无效选择")

    print()
    print("\033[96m" + "─" * 70 + "\033[0m")
    print("\033[96m  所有示例运行完成！\033[0m")
    print("\033[96m  下一步：\033[0m")
    print("\033[96m    • 查看帮助:    python main.py --help\033[0m")
    print("\033[96m    • 交互演示:    python main.py demo\033[0m")
    print("\033[96m    • 阅读文档:    打开 README.md\033[0m")
    print("\033[96m" + "─" * 70 + "\033[0m")
    print()


if __name__ == "__main__":
    main()
