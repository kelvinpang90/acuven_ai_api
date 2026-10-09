# 后续工作

当前本地演示已覆盖独立 AI API、只读能力与 `ai_billing_hub` 用量摄取客户端。下一步按风险和依赖推进：

1. 以本项目完成 Billing Hub 第 3 阶段首条真实计费链路：内部计量租户与独立 `PREPAID` 测试租户两路验收（见下节）。
2. 为 CRM 实现受控的客户摘要接口和员工界面入口，核对当前用户与对象权限；再逐个接 ERP、网店、Inventory、POS。
3. 正式部署前补数据库迁移、凭据轮换、限流、操作审计、死信重投与告警、供应商异常用量对账、备份恢复及部署契约。
4. 跨系统工具、人工审批写入、BYOK、客户自托管计费与统一门户接入另立设计和验收。

GitHub 仓库、OpenClaw 控制面登记和生产部署尚未建立。不要把本地演示结果写成已完成生产联调。

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
- [ ] 按 VPS 部署位置配齐环境变量并启动 API 与 worker
- [ ] Kelvin 在 Billing Hub 创建 `PREPAID` 测试租户、项目并签发独立凭据；本服务在运行环境配置第二客户端绑定，用于钱包扣费、余额停机／复机及 ADR-0010 透支额验收
- [ ] `acuven-crm`：用现有 `crm.contact_summary` 打通第一条真实链路，核对预计供应商成本、参考售价、钱包实扣 0，以及管理员停用后被拦
- [ ] `acuven-shop-customer-service`：先定义客服能力。现有 `shop.order_summary` 只是订单摘要、不是对话，网店本身也还没有客服功能

