# 系统侧 AI API 需求说明（评审稿）

状态：待 Kelvin 批准。本项目仅提供员工使用的只读 AI 能力；顾客 AI Chatbot 独立。2026-10-09 Kelvin 确定本项目为 Billing Hub 第 3 阶段试点的第一条真实计费链路（PR 237）。计费细节与待决清单见 [BILLING_DESIGN.md](BILLING_DESIGN.md)。现有 app 只是本地演示，尚未接真实业务模块或完成真实计费联调。

## 使用者与边界

| 模块后端 | 员工入口（目标） | 能力范围 |
| --- | --- | --- |
| CRM | 联系人详情页生成摘要和下一步建议 | 第一批正式契约：`crm.contact_summary` |
| ERP | 获授权单据页摘要 | `erp.document_summary` 仅为演示名，后续设计 |
| 网店 | 员工订单管理页摘要 | `shop.order_summary` 仅为演示名，不是客服对话 |
| Inventory | 库存差异页解释 | 后续设计 |
| POS | 销售或班次页摘要 | 后续设计 |

只有模块后端持客户端凭据调用 `POST /v1/capabilities/{capability}`。正式环境默认禁用除 CRM 首批能力外的四个演示能力；每项经独立契约、权限、计费归属及故障验收后再开放。

业务模块负责员工登录、角色与对象权限、业务事实、CRM 员工／联系人与 AI `request_id` 的映射及全部业务写入。AI API 负责模块客户端鉴权、能力范围、服务端租户和项目绑定、模型调用、用量 outbox 与客户端／能力总量限制。CRM 决定联系人数据字段；AI API 不直连业务数据库、不把模型建议当事实或写入许可。

## 核心流程与故障

1. 模块后端完成员工与对象授权，按其获批准的结构传入上下文；调用方不能指定计费租户、项目或凭据。
2. AI API 验证 `X-Acuven-Client-Id`、Bearer token 和能力范围，再用服务端绑定的凭据查询 Billing Hub `GET /api/v1/integration/effective-status`。该接口以五个 `X-Acuven-*` 头签名，GET 请求体为空；应答包含 `tenant_id`、`project_id`、`billing_mode`、`billing_status`、`account_status`、`effective_status`、`status_version`。
3. 归属正确且经校验的 `BLOCK_AI` 拒绝新模型调用；中心查询失败或应答无法校验时按 Billing Hub `REQ-AVAIL-001` 和 PR 238 后的 `api.md` 放行。现阶段每次调用前实时查询；本地版本化状态、webhook 接收与周期对账依赖 Billing Hub 后续状态同步设计闸门，本项目不预定实现规则。
4. 模型只做摘要、建议。取得实际 token 用量后，先把不含提示词、联系人上下文和回答的 `LLM_TOKEN` 事件提交本地 outbox，再返回结果。worker 异步投递，AI 响应不等 Billing Hub 计价。
5. 模型失败返回 AI 不可用，模块保留人工流程。Billing Hub 断联但本地 outbox 可持久保存时继续积压；本地无法保存时不发起新的模型调用。模型完成后 outbox 仍可能提交失败：不返回正文，记录异常供正式阶段对账。单次投递失败不撤回已返回的 AI 结果。

## 第一批能力：`crm.contact_summary`

**目标契约 v1（待批准、未实现）：**沿用 `POST /v1/capabilities/crm.contact_summary`，请求顶层只收下表字段。CRM 负责 `contact_snapshot` 内部业务字段及版本；AI API 验证获准结构和大小，不替 CRM 决定是否传完整业务记录。正式传个人数据前须另过设计审查。

| 输入字段 | 规则 |
| --- | --- |
| `schema_version` | 必填，`"1.0"` |
| `contact_ref` | 必填，CRM 的不透明引用，1–128 字符；只在请求与响应中使用 |
| `contact_schema_version` | 必填，CRM 批准的结构版本，1–32 字符 |
| `contact_snapshot` | 必填，按该版本的 JSON 对象；评审建议序列化后 ≤16 KiB |
| `as_of` / `output_language` | 前者必填、带时区 RFC 3339；后者为 `zh-CN`／`en`／`ms`，默认 `zh-CN` |

整份请求的暂定上限为 20 KiB；超长、非法版本或额外顶层字段在调用模型前返回 422。目标 200 响应含 `schema_version="1.0"`、`request_id`、原样 `contact_ref`、`summary`（1–600 字符）、`suggested_next_step`（0–240 字符）和供应商实际 `model`。缺事实时说明缺口，不声称已修改业务记录。

现状 `app/main.py:CapabilityRequest` 只收 1–12000 字符的 `context_text`；响应只有 `request_id/text/model`。`app/service.py:run_capability` 把该文本作为不可信上下文发给模型。结构化 v1 拟替代它；迁移窗口待定。

**2026-10-09 Kelvin 评审后修改：请求级幂等放在正式多租户档，内部联调可选做。**AI API 对已鉴权 `client_id`、能力名、幂等键保存状态、原 `request_id`、`event_id` 和规范化请求摘要的哈希；不保存摘要正文或联系人内容。已完成的同键同内容请求返回 409 和原 `request_id`，不再调用模型或生成用量；同键不同内容也返回 409，具体错误码见计费设计。CRM 保存首次正文及其 `request_id` 映射。若首次响应在网络中丢失，员工须以新幂等键重新发起，可能多产生一次 token 费用；内部计量租户的钱包实扣仍为 0。

## 明确不做

- 不读写 CRM、ERP、网店、Inventory、POS 业务数据库；不做退款、改价、库存变更或其他业务写入。
- 不做顾客对话、渠道接待、人工接管；不做 BYOK、客户自托管计费。
- 不做跨系统工具或统一 AI 工作台；这些需另立设计。项目停用与安全暂停的专门状态也依赖 Billing Hub 以后新增。

## 分档验收

### 本地演示

- 使用模拟模型和模拟计费端验证现有调用、客户端鉴权与能力范围、`ALLOW_AI`／`BLOCK_AI`、中心故障放行、用量先入 outbox、HMAC 重试、合法重复回执与不可重试死信。
- **测试记录：2026-10-08 本地 pytest 为 9 passed；2026-10-09 本次 pytest 为 9 passed、1 warning。**只证明模拟链路，不代表真实模型、真实凭据或 Billing Hub 联调已完成。

### 内部计量真实联调（第 3 阶段双路径试点）

**`INTERNAL_METERED_ONLY` 路径：**使用独立客户端绑定，真实凭据仅放运行环境。真实模型调用与 HMAC 摄取跑通；计费平台离线时仍能回复、用量留在本地 outbox，恢复后补投积压。Billing Hub ADMIN 后台人工核对单事件的预计供应商成本、参考客户售价和钱包实扣 0；经校验的管理员禁用 `BLOCK_AI` 拦调用，查询失败或应答无法校验时放行。同一 `event_id` 重投只有一次计量效果；`DEAD > 0` 可从 worker 日志加人工检查发现。

**`PREPAID` 测试路径：**Kelvin 在 Billing Hub 创建测试租户、项目并签发独立凭据；本项目配置第二个客户端绑定，与内部路径隔离，凭据只放运行环境。计费平台离线时 Claude 仍能回复、用量落本地，恢复后补投积压；同一事件重复投递钱包只扣一次。余额停机返回经校验的 `BLOCK_AI` 后不再发起模型调用，复机返回经校验的 `ALLOW_AI` 后恢复；Billing Hub 侧记录每次停机时的余额和透支额，供 ADR-0010 评估。查询失败或应答无法校验时仍放行。

两条路径都通过，Billing Hub 第 3 阶段试点才算成功；内部计量租户钱包实扣 0，不能替代 `PREPAID` 的扣费、余额停机和复机验收。

### 正式多租户

- CRM 结构化契约、员工和对象授权、个人数据审查及跨租户隔离验收；其他四项演示能力默认禁用。
- 请求级幂等、模块员工级与 AI API 客户端／能力级限流（超限 429）、outbox 容量预检与积压演练通过。
- 供应商未知用量对账、AI API 自动核对 Billing Hub 最终计价（依赖 Billing Hub 新增按事件查询契约）、本地记录按批准期限清理、可审计死信重投工具与告警验收。
- 本地版本化服务状态、状态 webhook 接收端及周期对账依赖 Billing Hub 的状态同步设计闸门；届时按批准的契约实现，不预设 `status_version` 的应用、解除条件或故障策略。
- 数据库迁移、备份恢复、凭据轮换、部署回滚及运维验收；无未处置死信。

## 技术栈与部署

沿用 Python 3.12、FastAPI、SQLAlchemy、MySQL／SQLite。SQLite 仅供本地演示；API 与 outbox worker 独立运行。Kelvin 已确定 AI API 部署在 VPS；本文件不授权实际部署。

唯一待决清单见 [BILLING_DESIGN.md：待 Kelvin 决定](BILLING_DESIGN.md#待-kelvin-决定)。
