# 🚚 末端驿站/自提柜管理系统 - 发布与自动回滚自动化平台

> **Last-Mile Station & Locker Management System - Release & Auto-Rollback Automation Platform**

一套完整的末端驿站/自提柜系统发布运维自动化解决方案，覆盖**前置校验→多级审批→区域灰度→实时监控→熔断回滚→演练复盘**全链路闭环。

---

## ✨ 核心能力

### 1️⃣ 发布前置校验与多维质量门禁
| 校验维度 | 说明 | 阈值（默认） |
|---------|------|-------------|
| 📬 寄件成功率 | 基于7日历史数据回归测试 | ≥ 98% |
| 🔢 取件码准确率 | 生成与解析准确率 | ≥ 99.5% |
| 🖥️ 柜机在线率 | 终端心跳连通性 | ≥ 95% |
| 📶 系统连通性 | PDA/PC双端拨测 | PDA≥98% / PC≥99% |

- **阻断机制**：任一核心指标不达标自动阻断，生成结构化修复建议
- **放行机制**：全部通过后方可进入审批环节
- **并行执行**：支持4维度并行校验，≤5分钟完成全量检查

### 2️⃣ 分级审批流转与动态路由

```
发布申请 → 自动识别通道 → 动态分配审批矩阵
    │
    ├─ 📋 常规迭代通道（三级串行）
    │   ├─ 🔵 运营审批：业务影响/用户体验评估
    │   ├─ 🟢 驿站负责人审批：网点运营/客诉风险评估
    │   └─ 🔴 技术审批：架构方案/代码质量评估
    │
    └─ 🚨 紧急Hotfix通道（并行+事后补签）
        └─ 全部干系人并行审批，超时SLA自动催办
```

### 3️⃣ 区域灰度发布 + 实时熔断回滚

**三级灰度放量策略**：
```
阶段1  偏远低流量驿站 → 10%量 → 观察30分钟
阶段2  社区普通驿站   → 40%量 → 观察60分钟
阶段3  核心商圈/高校   → 50%量 → 观察120分钟
```

**高频监控指标（每5分钟采集）**：
- ❌ 取件失败率（阈值3%，严重阈值5%）
- 📴 柜机离线率（阈值8%，严重阈值15%）
- ⚠️ 寄件异常率（阈值4%，严重阈值7%）

**熔断触发→自动回滚链路**：
```
指标突破阈值
  ↓
连续2次异常/1次严重超标
  ↓
🚨 熔断触发 → 暂停发布
  ↓
版本回切 → 网关重路由 → 配置回滚 → 服务重启
  ↓
健康检查 → 链路验证 → 监控重启
  ↓
📄 结构化回滚报告 → 企微/钉钉/邮件多渠道通知
```

### 4️⃣ 演练验证 + 复盘报表 + 合规审计

| 能力 | 说明 |
|-----|------|
| 🎯 **常态化回滚演练** | 每月自动执行，验证熔断-回滚全链路有效性，可回放所有步骤 |
| 📊 **每周运营报告** | 周一9:00自动生成PDF/Excel，含发布成功率、回滚率、审批时长趋势图 |
| 🔍 **多维检索** | 按时间/驿站/版本号/操作人/发布类型检索历史记录 |
| 🛡️ **审计日志** | 全流程操作不可篡改哈希链存证，支持CSV/JSON/Excel导出 |

---

## 📁 目录结构

```
e:\work\d020\
├── config/
│   └── settings.yaml              # 全系统配置（阈值/审批矩阵/通知渠道等）
├── src/
│   ├── core/
│   │   ├── config.py              # 数据模型 + 配置管理器
│   │   ├── logger.py              # 日志 + 哈希链审计日志
│   │   └── notifier.py            # 企微/钉钉/邮件多渠道通知
│   ├── precheck/
│   │   ├── checks.py              # 4大维度质量门禁检查器
│   │   └── engine.py              # 前置校验引擎（并行/阻断）
│   ├── approval/
│   │   └── engine.py              # 审批工作流引擎（串行/并行/补签）
│   ├── release/
│   │   └── grayscale.py           # 灰度+监控+熔断+回滚引擎
│   ├── reporting/
│   │   ├── drill.py               # 回滚演练管理器
│   │   ├── weekly_report.py       # 周报生成器（含matplotlib趋势图）
│   │   └── audit_query.py         # 多维度审计查询引擎
│   └── orchestrator.py            # 发布流水线编排器 + 定时任务
├── storage/                       # 运行时数据（自动创建）
│   ├── db/                        # 发布/审批/演练/流水线状态
│   ├── audit_logs/                # 审计日志（JSONL+哈希链）
│   └── reports/                   # 周报/趋势图
├── main.py                        # CLI命令行入口
├── examples.py                    # 示例脚本（快速上手）
├── requirements.txt               # Python依赖
└── README.md                      # 本文档
```

---

## 🚀 快速开始

### 1. 环境准备
```bash
cd e:\work\d020
pip install -r requirements.txt
```

### 2. 查看所有命令
```bash
python main.py --help
```

### 3. 一键完整演示
```bash
# 正常成功流程演示
python main.py demo --mode normal

# 前置校验阻断演示
python main.py demo --mode fail_precheck

# 熔断触发回滚演示
python main.py demo --mode trigger_rollback
```

### 4. 分步操作示例
```bash
# Step 1: 提交发布申请
python main.py submit \
  --version "v2.8.0" \
  --title "取件码算法优化+柜机心跳调优" \
  --description "1.优化取件码生成算法性能 2.调整柜机心跳频率" \
  --type regular \
  --operator "dev_zhangsan@company.com" \
  --rollback-version "v2.7.5"

# 输出: 发布编号 REL_XXXXXXXX_XXX（后续步骤使用此ID）

# Step 2: 执行前置校验
python main.py precheck REL_XXXXXXXX_XXX

# Step 3: 启动审批流程
python main.py approval REL_XXXXXXXX_XXX --action start

# Step 4: 三级审批
python main.py approval REL_XXXXXXXX_XXX --action approve \
  --role operations --approver "ops_mgr@company.com" --comment "业务无影响"

python main.py approval REL_XXXXXXXX_XXX --action approve \
  --role station_manager --approver "station_head@company.com" --comment "客诉风险可控"

python main.py approval REL_XXXXXXXX_XXX --action approve \
  --role tech --approver "tech_lead@company.com" --comment "代码Review通过"

# Step 5: 启动灰度发布（非阻塞后台运行）
python main.py release REL_XXXXXXXX_XXX

# Step 6: 查看发布进度
python main.py status REL_XXXXXXXX_XXX

# Step 7: 紧急情况手动回滚
python main.py rollback REL_XXXXXXXX_XXX \
  --reason "监控发现取件失败率异常飙升" \
  --operator "oncall_engineer"
```

### 5. 报表与审计
```bash
# 立即生成周报
python main.py report

# 执行一次回滚演练
python main.py drill

# 查询近7天发布记录
python main.py list --type release --start "2026-06-14"

# 导出回滚历史到Excel
python main.py list --type rollback \
  --start "2026-06-01" --end "2026-06-21" \
  --format excel --output "./storage/rollback_history.xlsx"

# 查询审计日志
python main.py list --type audit \
  --start "2026-06-01" --operator "tech_lead@company.com"
```

### 6. 启动常驻后台服务
```bash
# 启动定时任务（月度演练、每周周报）
python main.py serve

# 启动后台并自动执行一次演示
python main.py serve --run-demo
```

---

## ⚙️ 配置指南

所有配置集中在 `config/settings.yaml`，主要配置域：

### 质量门禁阈值调整
```yaml
precheck:
  mail_success_rate:
    threshold: 0.98        # 寄件成功率
  pickup_code_accuracy:
    threshold: 0.995       # 取件码准确率
  terminal_online_rate:
    threshold: 0.95        # 柜机在线率
```

### 审批矩阵自定义
```yaml
approval:
  channels:
    regular:
      flow:
        - role: operations
          approvers: ["ops@company.com"]
        - role: station_manager
          approvers: ["station_head@company.com"]
        - role: tech
          approvers: ["tech@company.com"]
    hotfix:
      parallel_approval: true     # 启用并行审批
      allow_post_sign: true       # 允许事后补签
```

### 熔断与灰度策略
```yaml
release:
  grayscale:
    default_stages:
      - stage: 1
        name: "偏远低流量"
        scale_percent: 10              # 放量10%
        observation_minutes: 30        # 观察30分钟
  monitoring:
    interval_seconds: 300              # 每5分钟采集
  circuit_breaker:
    auto_rollback: true                # 自动回滚
    cooldown_minutes: 60               # 熔断冷却1小时
```

### 通知渠道配置
```yaml
notification:
  channels:
    wechat_work:
      enabled: true
      webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=XXX"
    dingtalk:
      enabled: true
      webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=XXX"
      secret: "SECxxxx"
    email:
      enabled: true
      smtp_server: "smtp.company.com"
      smtp_user: "release@company.com"
      smtp_password: "xxxx"
```

---

## 🔌 API 编程接口

除CLI外，也可在Python代码中直接调用：

```python
from main import *
from src.orchestrator import get_orchestrator
from src.core.config import ReleaseRequest, ReleaseType

# 初始化编排器
orch = get_orchestrator()

# 1. 提交发布
req = ReleaseRequest(
    release_id="",
    version="v3.0.0",
    title="驿站系统大版本升级",
    description="全面升级驿站管理能力",
    release_type=ReleaseType.REGULAR,
    submitted_by="engineer@company.com",
    submitted_at="",
    package_url="https://.../v3.tar.gz",
    rollback_version="v2.9.5",
)
pipeline = orch.submit_release(req)
rid = pipeline.request.release_id

# 2. 前置校验
orch.run_precheck(rid)

# 3. 审批
orch.start_approval(rid)
orch.approve(rid, "ops_mgr", "operations", "通过")
orch.approve(rid, "station_head", "station_manager", "通过")
orch.approve(rid, "tech_lead", "tech", "通过")

# 4. 灰度发布（阻塞直到完成）
orch.start_release(rid, blocking=True, timeout=7200)

# 5. 查询最终状态
final = orch.get_pipeline_status(rid)
print(final["status"])
```

---

## 🛡️ 安全与合规

| 安全特性 | 实现方案 |
|---------|---------|
| 审计日志不可篡改 | SHA-256哈希链，每条日志hash与前一条关联，任何篡改可验证 |
| 审批留痕 | 全部审批操作记录审批人/时间/意见，不可修改 |
| 敏感信息保护 | 密码/Token仅存配置文件，日志自动脱敏 |
| 操作权限分级 | 审批角色与操作人绑定，禁止越级操作 |
| 熔断保护 | 熔断后强制冷却60分钟，避免抖动循环 |

---

## 📊 关键SLA指标参考

| 指标 | 目标值 | 说明 |
|-----|-------|------|
| 发布成功率 | ≥ 90% | 通过前置校验+审批的发布最终成功比例 |
| 前置校验覆盖率 | 100% | 所有发布强制经过质量门禁 |
| 回滚成功率 | ≥ 99% | 熔断触发后回滚动作成功比例 |
| 回滚耗时 | ≤ 5分钟 | 从熔断触发到核心链路恢复时间 |
| 审批SLA达成率 | ≥ 95% | 审批节点在时限内完成比例 |
| 月度演练执行率 | 100% | 每月至少一次回滚演练 |

---

## 📚 术语表

| 术语 | 说明 |
|-----|------|
| 熔断 (Circuit Breaker) | 类比电路保险丝，指标异常时自动断开发布 |
| 灰度 (Canary/Grayscale) | 类比金丝雀，小比例先行验证，逐步放量 |
| Hotfix | 紧急热修复，跳过常规流程快速上线 |
| PDA | 驿站手持扫码设备 |
| 取件码 | 用于自提柜身份核验的编码（数字/二维码） |

---

## 🎓 设计思想

1. **防御性发布（Defensive Release）**：通过前置校验+多级审批+分层灰度建立三道防线，降低发布变更风险。

2. **故障快速自愈（Fail-Fast + Self-Healing）**：高频监控+自动熔断+自动回滚三位一体，确保异常情况下业务损失最小化（MTTR ≤ 5min）。

3. **可靠性验证（Reliability Drill）**：定期主动演练「故障注入→熔断→回滚→恢复」链路，确保极端场景下系统行为符合预期。

4. **全链路可追溯（End-to-End Traceability）**：从提交申请到发布完成或回滚，每一步有审计、每一环有报表、每一异常有根因记录。

---

## 📝 License

本项目为内部生产级系统设计参考实现。如有问题请联系平台运维团队。
