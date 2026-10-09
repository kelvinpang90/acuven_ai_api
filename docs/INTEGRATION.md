# AI API 与 Billing Hub 接入记录

## 已实现的最小闭环

1. 模块后端持客户端 token 调 AI API；`client_id` 在服务端映射租户、可用能力和计费凭据，调用方不能自行指定计费租户。AI API 每次生成前用同一凭据签名查询 Billing Hub 的 `GET /api/v1/integration/effective-status`；管理员禁用或正常租户余额暂停时拒绝调用；状态不可查询或应答无法校验时照常放行（Billing Hub 不变量 1：中心计费故障不得中断客户 AI 服务），用量仍进 outbox。
2. AI API 调 Claude Messages API，读取响应中的 `usage` 四个 token 字段。
3. 使用 `LLM_TOKEN` 计量类型生成规范 ULID `event_id`；本地 outbox 事务提交后才返回结果。outbox 只保存计费元数据，不保存客户上下文、提示词或回答。
4. 独立 worker 以 `POST /api/v1/integration/usage-events` 上报；请求头使用 `X-Acuven-*` 五个字段，签名为五行规范串的 HMAC-SHA256。失败按 `retryable` 重试或进入 `DEAD`。
5. `202 accepted` 与 `200 already_received / already_processed` 的回执必须匹配 `event_id` 才视为送达。

## 接正式计费环境前核对

- Acuven 自用租户与两个项目已在 Billing Hub 生产建好（2026-10-08，见 `docs/TODO.md`）；还需签发集成凭据，并确认模型目录和 `LLM_TOKEN` 的定价规则；`provider` 为 `anthropic`，`model` 取供应商实际响应。
- 将凭据配置在 AI API 的运行环境；不要把密钥、价格或客户数据写入仓库。
- Acuven 自用租户已设为 `INTERNAL_METERED_ONLY`（单向，开通早于签发任何凭据）。在线联调先用模拟业务数据，核对预计供应商成本、未来客户参考售价、钱包实扣 0，以及经校验的管理员禁用 `BLOCK_AI` 拒绝调用；查询失败时仍放行。不得把实际凭据或租户 ID 写入仓库。
- Billing Hub 第 3 阶段试点的首条真实计费链路是本项目（2026-10-09 Kelvin，PR 237）。Kelvin 另建 `PREPAID` 测试租户／项目并签发凭据后，本服务配置独立的第二客户端绑定；两路均验离线回复、本地积压与恢复补投。`PREPAID` 路径另验同事件钱包只扣一次、余额停机与复机、停机时余额和透支额。两路都通过才算试点成功。
- 检查 outbox 中的 `PENDING`、`SENDING` 和 `DEAD` 数量，配置告警及人工重投流程。当前仅有 worker 的错误日志，没有完整运维界面。

## 下一阶段

为 CRM 建立受控业务 API 和员工界面入口，形成第一条真实业务能力；随后按模块逐个接入。Billing Hub 状态同步的出站 webhook、本地版本化状态和周期对账待其设计闸门批准，本项目届时按契约接入。跨系统工具执行、写入审批、客户 BYOK、独立部署计费以及统一门户均未在本次实现。
