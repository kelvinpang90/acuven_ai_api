# 后续工作

当前本地演示已覆盖独立 AI API、只读能力与 `ai_billing_hub` 用量摄取客户端。下一步按风险和依赖推进：

1. 以本项目完成 Billing Hub 第 3 阶段首条真实计费链路：内部计量租户与独立 `PREPAID` 测试租户两路验收（见下节）。
2. 为 CRM 实现受控的客户摘要接口和员工界面入口，核对当前用户与对象权限；再逐个接 ERP、网店、Inventory、POS。
3. 正式部署前补数据库迁移、凭据轮换、限流、操作审计、死信重投与告警、供应商异常用量对账、备份恢复及部署契约。
4. 跨系统工具、人工审批写入、BYOK、客户自托管计费与统一门户接入另立设计和验收。

GitHub 公开仓库已建（2026-10-09）。2026-10-09 Kelvin 决定本项目登记 OpenClaw 控制面，尚未登记；生产部署尚未建立。不要把本地演示结果写成已完成生产联调。

## 规划（OpenClaw planning-v1）

> 下面两个标记之间的块由 OpenClaw 控制面按固定格式解析，决定下一项任务；只管「做什么、先做什么」，执行权限仍以 `.platform/tasks.yaml` 为准。格式不对整块作废，项目 fail closed。project_id `acuven_ai_api`，任务编号 `^AIAPI-TASK-[0-9]{3}$`（2026-10-09 Kelvin 确认）。

<!-- 块内每一行只能是下面三种格式之一（空行可以有，别的内容一律不行，包括注释）：
     当前计划：`1. `<任务 id>` <标题>`，从 1 连续编号，最多 20 项；每一项都必须在 tasks.yaml 登记为 ready，第一项就是下一次「开启」的任务。
     已阻塞：  `- `<任务 id>` <标题>｜阻塞：<原因>` 或 `- 待登记：<标题>｜阻塞：<原因>`（分隔符是全角竖线）。
     后续计划：`- `<任务 id>` <标题>` 或 `- 待登记：<标题>`。
     任务 id 必须符合控制面登记的 task_id_pattern，全块不重复；tasks.yaml 里每个 ready 任务都必须出现在块里。
     「待登记」行不带 id，只给人看，不会变成可执行的东西。三个标题与两个标记逐字不改。 -->

<!-- openclaw:planning-v1:begin -->
### 当前计划

### 已阻塞

### 后续计划
<!-- openclaw:planning-v1:end -->

## 第一轮 CRM 接入（2026-10-09 Kelvin 决定）

决定见 [REQUIREMENTS.md](REQUIREMENTS.md)「第一轮 CRM 接入决定」。各项目的任务在各自仓库进行，本节只跟踪与本项目有关的依赖。

- [ ] 本项目：补 `.platform/` 契约与部署工作流；合并后在控制面仓库另开会话登记
- [ ] 本项目：`crm.contact_summary` 结构化契约 v1 定稿，经 Kelvin 批准后实现；crm_os 的 AI 接入以此为前置
- [x] 本项目：`status_version` 改为严格整数校验（见 [BILLING_DESIGN.md](BILLING_DESIGN.md)「冲突与差异」第 2 条；2026-10-10 随部署工作流 PR 完成）
- [ ] crm_os：公开注册与按 id 接口越权修复，与 AI 接入并行
- [ ] crm_os：客户摘要接入（对象级权限检查、审计记录、超时与降级、界面入口），v1 契约批准后进行
- [ ] 评测集：从真实 CRM 取 20–30 个客户，不进仓库；Kelvin 人工打分认可后上线
- [ ] 试点期间记录每次摘要的平均 token 与成本
- [ ] 以后：crm_os 部署改为按提交 SHA 后登记 OpenClaw；Billing Hub「套餐内含额度」设计

## Billing Hub 内部计量模式联调

**已就绪（2026-10-08）**

- Billing Hub 的 `INTERNAL_METERED_ONLY`、`GET /api/v1/integration/effective-status` 和两种金额展示已合并部署到生产（ai_billing_hub PR 236）。
- 生产上已建 Acuven 自用租户 `Acuven Technology`，已设为 `INTERNAL_METERED_ONLY`：照常计价、钱包实扣 0、余额为 0 也放行；管理员停用仍拦。开通是单向的。
- 该租户下已建两个项目：`acuven-shop-customer-service`、`acuven-crm`。以后可随时加项目，每个项目一份凭据。租户与项目的 ID 在 Billing Hub 后台客户详情页查看，不写进本仓库。
- 本服务查不到计费状态或应答无法校验时照常放行（Billing Hub 不变量 1），用量仍进 outbox。

**待办**

- [ ] Kelvin 在 Billing Hub 后台给内部计量 CRM 项目签发集成凭据；secret 只显示一次，直接放进本服务运行环境的 `AI_API_CLIENTS_JSON`，不经过聊天、不写进仓库；网店项目待能力契约确定后再签发
- [ ] 在 Billing Hub 后台配置 Anthropic 模型目录、`LLM_TOKEN` 供应商价格与参考客户定价；否则用量上报后会计价失败（`MODEL_UNKNOWN` / `PRICING_ERROR`）
- [x] 2026-10-09 Kelvin 确定本服务部署位置为 VPS
- [x] 2026-10-09 部署设计定稿（[DEPLOYMENT.md](DEPLOYMENT.md)）：VPS 共享 MySQL 中的独立库，试点沿用 `create_all`，CI 加 MySQL 必需检查
- [ ] 实现部署工作流：合并到 `main` 后按提交 SHA 部署
- [ ] 按 VPS 部署位置配齐环境变量并启动 API 与 worker
- [ ] Kelvin 在 Billing Hub 创建 `PREPAID` 测试租户、项目并签发独立凭据；本服务在运行环境配置第二客户端绑定，用于钱包扣费、余额停机／复机及 ADR-0010 透支额验收
- [ ] `acuven-crm`：用现有 `crm.contact_summary` 打通第一条真实链路，核对预计供应商成本、参考售价、钱包实扣 0，以及管理员停用后被拦
- [ ] `acuven-shop-customer-service`：先定义客服能力。现有 `shop.order_summary` 只是订单摘要、不是对话，网店本身也还没有客服功能

