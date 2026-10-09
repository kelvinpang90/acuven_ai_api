# AI API 计费设计（2026-10-09 Kelvin 批准）

设计已批准，但真实计费联调仍须满足下文「联调前置」各项，本文不授权部署。Billing Hub 事实以只读的 `origin/main`（本次核对提交 `02fd13b75507338dad40713f2d7cc9ceb524a950`）为准。其 `docs/TODO.md` 和 PR 237 已将 `acuven_ai_api` 定为第 3 阶段第一条真实计费链路；`docs/api.md` 经 PR 238 修正后，与 `REQ-AVAIL-001` 的查询故障放行规则一致。

## 1. 调用前：有效状态

**现状（代码已实现）：**`app/billing.py:BillingClient.allows_ai` 调用 `GET /api/v1/integration/effective-status`。按 `origin/main:docs/api.md`，请求头为 `X-Acuven-Api-Key`、`X-Acuven-Key-Version`、`X-Acuven-Timestamp`、`X-Acuven-Request-Id`、`X-Acuven-Signature`；签名覆盖 `GET`、完整路径、时间戳、唯一请求 ID 和空请求体的 SHA-256。成功 `data` 含 `tenant_id`、`project_id`、`billing_mode`、`billing_status`、`account_status`、`effective_status`、`status_version`。代码只核对归属、有效状态和版本类型，尚不读前三种状态字段；`app/main.py:create_app.invoke` 对经核对的 `BLOCK_AI` 返回 403，查询不可用时放行，无本地状态记录。未配置 Billing URL 返回 503。

**缺口或提议：**第 3 阶段实时查询路径只拦归属正确、经校验的 `BLOCK_AI`；查询失败或应答无法校验时放行，用量仍进本地 outbox。`status_version` 必须严格为整数，排除 Python 布尔值。Billing Hub 已规划状态同步设计闸门（出站 webhook、周期对账、`reason_code`）；本地版本化状态、webhook 接收端、版本应用与解除规则均待该闸门批准后再设计，目前不引入本地持久服务状态。

## 2. 租户归属与凭据

**现状（代码已实现）：**`app/config.py:Settings.from_env` 把 `client_id` 服务端绑定到 token 摘要、`tenant_id`、`billing_project_id`、Billing Hub key／secret／版本和能力范围；`app/main.py:create_app.invoke` 不接收调用方自选租户。`app/billing.py:usage_payload` 写入绑定的诊断 ID；Billing Hub 摄取端的权威归属仍取签名凭据，诊断 ID 不匹配返回 403。

**缺口或提议：**Kelvin 办理内部计量项目及另一个 `PREPAID` 测试租户／项目的凭据签发；本项目配置第二个 `client_id` 绑定，分别核对各自租户／项目归属。两套凭据独立，只放运行环境，不入仓库或日志。正式多租户阶段再验收凭据轮换、吊销与跨租户隔离；现有配置校验无法单靠本地证明凭据归属，需用状态与摄取应答交叉核对。

## 3. 用量事件与 outbox 提交

**现状（代码已实现）：**`app/provider.py:AnthropicProvider.generate` 取实际模型与四类 token；`app/service.py:run_capability` 在模型返回后调用 `app/billing.py:usage_payload`，生成规范 ULID `event_id`、`schema_version="1.0"`、`request_id`、租户／项目诊断 ID、`provider="anthropic"`、模型、`usage_type="LLM_TOKEN"`、四类 token 与 `occurred_at`。本地事务提交 `app/db.py:UsageOutbox` 后才回 AI 结果；outbox 不含提示词、联系人上下文或回答。

**缺口或提议：**内部联调核对实际模型、四类 token、`event_id` 与 Billing Hub 单条回执。正式多租户再补严格字段／16 KiB 请求体校验、调用前 outbox 容量预检、长期积压演练；本地无法可靠持久保存时停止新模型调用。预检后提交仍可能失败：返回 503、不回正文，记录可能遗漏供应商用量的异常。`event_id` 一次生成，重投沿用原 body。

## 3a. 请求级幂等

**现状（代码已实现）：**`app/main.py:create_app.invoke` 每次 HTTP 请求进入 `app/service.py:run_capability`，生成新 `request_id`、新模型调用和新 `event_id`。现有 Billing Hub `event_id` 幂等只保护同一用量事件的重复投递。

**缺口或提议：**2026-10-09 Kelvin 评审后修改：正式多租户阶段，AI API 仅按（已鉴权 `client_id`、能力名、幂等键）保存状态、原 `request_id`、`event_id` 及规范化请求摘要的哈希，**不保存摘要正文或联系人内容**。同键同内容已完成：提议返回 409、附原 `request_id`，不调模型、不新增用量；进行中：提议 409 `REQUEST_IN_PROGRESS`、附原 `request_id`。同键不同内容选择 409 `IDEMPOTENCY_CONFLICT`，理由是键已被另一请求占用，属于资源冲突而非 JSON 字段格式错误。CRM 只在审计记录中保存 `request_id`，不保存摘要正文（2026-10-09 Kelvin，见 [REQUIREMENTS.md](REQUIREMENTS.md) 第一轮决定）。若首次响应在网络中丢失，员工用新幂等键再发会重新生成，可能多一次 token 费用；内部计量租户钱包实扣仍为 0。内部联调可选做，本地演示不以此为验收项。

## 4. Outbox 状态机

**现状（代码已实现）：**`app/service.py:run_capability` 新建 `PENDING`。`app/worker.py:claim_one` 原子条件更新为 `SENDING`、`attempts+1`、60 秒租约与新 `claim_id`；到期的 `SENDING` 可被重领。`deliver_one` 仅凭当前 `claim_id` 写 `DELIVERED`、退回 `PENDING` 或转 `DEAD`。当前可重试事件无最大次数。

**缺口或提议：**

`PENDING → SENDING → DELIVERED`（合法回执）；`SENDING → PENDING`（可重试）；`SENDING → DEAD`（不可重试）；租约过期的 `SENDING` 用新 claim 收回。内部联调只需发现 `DEAD > 0`。正式多租户阶段验证目标 MySQL 下多 worker 不重复领取、旧 claim 不覆盖新状态，再批准退避、最大次数及人工重投规则；重复 HTTP 可出现，但 Billing Hub 同 `event_id` 最多一次财务效果。

## 5. 投递、回执与重试

**现状（代码已实现）：**`app/billing.py:BillingClient.send` 每次尝试用新 `X-Acuven-Request-Id` 对**原始 body**签名，`event_id` 与 body 不变。只把 `event_id` 匹配的 202 `accepted`、200 `already_received`／`already_processed` 判送达；网络失败可重试，服务端错误只按顶层 `retryable=true` 重试。`app/worker.py:deliver_one` 当前指数退避封顶 1 小时，无最大次数或抖动。

**缺口或提议：**两条试点路径都验证离线时用量留在本地、恢复后补投积压；离线只在 AI API 一侧模拟：临时把其运行环境的计费地址指向不可达地址，或在 AI API 容器层阻断出站，验证后恢复配置，不得停止、重启或改动生产 Billing Hub。`PREPAID` 路径以同一 `event_id` 重投验证钱包只扣一次；内部路径核对合法重复回执，worker 日志须能显示 `DEAD`。正式多租户阶段再定抖动、最大次数及可审计重投。202 只证明 Billing Hub 持久接收为 `RECEIVED`，不证明最终计价；不能因 HTTP 码自行覆盖 `retryable` 契约。

## 5a. 摄取后的计价核对

**现状（代码已实现）：**AI API 在匹配的 202／200 后把本地 outbox 置 `DELIVERED`，没有自动查询最终计价的代码。Billing Hub `origin/main:docs/api.md` 已有 ADMIN 专用 `GET /api/v1/admin/usage-events` 和 `GET /api/v1/admin/usage-events/{usage_event_id}`；列表可按 `request_id` 等条件筛选，详情显示状态、估算供应商成本、参考客户售价及钱包实扣。健康检查表已有 `usage_pricing_error`、`usage_model_unknown`、`usage_failed`、`usage_processing_backlog`。

**缺口或提议：**2026-10-09 Kelvin 评审后修改：内部计量真实联调**无需新增最终状态查询接口**。AI API 提供 `event_id`／`request_id` 供 Billing Hub 侧授权 ADMIN 人工核对事件；模型目录、价格配置、ADMIN 权限和中心健康告警由 Billing Hub 侧准备，属于跨项目联调条件，不是 AI API 的配置或 Kelvin 在本项目的待决设计。AI API 运维查本地投递，Billing Hub 运维处理计价。自动按 `event_id` 核对最终状态移到正式多租户档，依赖 Billing Hub **以后新增**按项目凭据隔离的查询契约；AI API 不自行计算权威费用。

## 6. 死信与运维

**现状（代码已实现）：**`app/worker.py:deliver_one` 对不可重试结果置 `DEAD`，日志记录 `event_id` 和错误码；`app/db.py:UsageOutbox` 保留载荷及 `last_error`。没有告警通道或人工重投工具。

**缺口或提议：**内部联调由 AI API 侧通过 worker 日志加人工检查发现本地 `DEAD > 0`；这不需要 Billing Hub ADMIN 权限，也不以重投工具为闸门。正式多租户阶段增加告警、可审计重投、角色限制和操作记录；排查后沿用同一 `event_id`／原始 body，只换签名请求 ID。AI API 运维先排查投递，Billing Hub 运维负责摄取后的计价，重大或逾期问题升级 Kelvin。

## 6a. 本地记录保留

**现状（代码已实现）：**`app/db.py:UsageOutbox` 保存事件与状态；`app/worker.py:deliver_one` 不删除。当前没有清理任务或最终计价核对记录。

**缺口或提议：**正式多租户阶段，最终核对完成后按批准期限清理本地用量及对账记录；仅收到 202／本地 `DELIVERED` 不足以清理。`PENDING`、`SENDING`、`DEAD` 与未解决异常应保留。这不列为内部联调门槛。

## 7. 供应商超时与未知用量

**现状（代码已实现）：**`app/provider.py:AnthropicProvider.generate` HTTP 超时为 60 秒；无可读 `usage` 时不生成事件，`app/main.py:create_app.invoke` 对模型异常返回 502。供应商可能已产生费用。

**缺口或提议：**正式多租户阶段记录不含上下文的“用量未知”索引。只在供应商提供可核实的逐请求模型和四类 token 用量、且原请求尚无事件时，按原 `request_id` 补报一次；否则保留供人工核对，绝不把估算 token 冒充真实用量。供应商是否提供所需证据尚未核实；内部联调不以此自动化为门槛。

## 8. `INTERNAL_METERED_ONLY`

**现状（代码已实现）：**`app/billing.py:usage_payload` 不带计费模式、价格或特权标记；仍上报正常 `LLM_TOKEN`。Billing Hub `origin/main` 的已批准内部模式按同一引擎记录预计供应商成本与参考客户售价，钱包实扣 0；`effective-status` 返回 `billing_mode` 和有效状态。

**缺口或提议：**AI API 不算权威费用，也不在请求中自称内部租户。内部计量路径用真实凭据／模型产生事件，ADMIN 后台核对两种展示金额与钱包实扣 0；管理员禁用返回经校验的 `BLOCK_AI` 时拦截，查询失败时放行。客户端／能力限流 429 留到正式多租户。

## 8a. `PREPAID` 测试路径

**现状（代码已实现）：**`app/config.py:Settings.from_env` 支持按多个 `client_id` 配置独立租户／项目凭据，`app/billing.py:usage_payload` 不决定钱包扣费。尚未配置或真实联调第二个客户端绑定。

**缺口或提议：**Kelvin 在 Billing Hub 创建测试租户、项目并签发独立凭据。本项目按独立绑定走同一实时状态查询、模型与 outbox 链路；按第 5 节的 AI API 单侧断联方法验证离线回复、积压用量和恢复补投，不动生产 Billing Hub。用同一事件重投证明钱包只扣一次；余额停机经校验的 `BLOCK_AI` 拦新调用、复机经校验的 `ALLOW_AI` 恢复。Billing Hub 侧记录每次停机余额及透支额供 ADR-0010 评估。两路均通过才算第 3 阶段试点成功。

## 9. 失败矩阵

**现状（代码已实现）：**HTTP 行为由 `app/main.py:create_app.invoke` 决定；worker 在响应之后运行。`DEAD`、`DELIVERED` 不会改已返回的 AI 响应。

**缺口或提议：**下表把当前行为与评审后目标分开；标“正式”者不是内部联调门槛。

| 情形 | HTTP | 用量结果 |
| --- | --- | --- |
| 归属正确的 `BLOCK_AI` | 当前／目标 403 | 不调模型，无事件 |
| 状态查询失败或应答无法校验（离线演练在 AI API 单侧进行） | 当前／目标：模型及 outbox 成功则 200 | 两条试点路径均放行、用量入本地 outbox；验证后恢复 AI API 配置，不动生产 Billing Hub |
| 模型失败／超时 | 当前 502 | 无可读 usage 时无事件；正式阶段核查未知用量 |
| 模型成功而 outbox 提交失败 | 当前 503 | 不回正文；可能有供应商费用，正式阶段对账 |
| 已入 outbox、投递失败 | AI 请求已 200 | 重试或 `DEAD`；内部联调用日志人工发现 |
| 同 `event_id` 重投 | AI 请求已 200 | 合法重复回执可置 `DELIVERED`；`PREPAID` 钱包只扣一次 |
| `PREPAID` 余额停机、复机 | 经校验 `BLOCK_AI` 为 403；经校验 `ALLOW_AI` 且模型成功为 200 | 停机不调模型、无新事件；复机恢复正常上报 |
| 客户端／能力超限（正式） | 目标 429 | 不调模型、无事件 |
| 同键同内容请求已完成（正式） | 目标 409，带原 `request_id` | 不调模型、无新事件；首次响应丢失后须用新键重新生成 |

## 10. 测试计划与证据

**现状（代码已实现）：**`tests/test_ai_and_billing.py` 覆盖鉴权／能力范围、状态允许／拦截及中心故障放行、跨租户状态拒信、HMAC、稳定事件体、不可重试死信、空文本用量和坏回执。2026-10-08 本地执行 pytest 为 9 passed；**2026-10-09 本次执行 `pytest -q` 为 9 passed、1 warning**。两次均使用模拟模型、模拟计费端与 SQLite，不是真实联调。

**缺口或提议：**

| 验证层 | 必需证据 |
| --- | --- |
| 纯逻辑（不启动 asyncio／`TestClient`，可在 Worker 沙箱） | 签名规范串、ULID 与四类 token 校验、回执 `event_id` 匹配、`retryable` 分支、租户／项目归属及严格整数 `status_version`（布尔值无效）；正式阶段再测幂等键作用域／哈希冲突分支。 |
| CI／受控联调 | 内部计量：真实模型和 HMAC、离线回复及本地积压与补投、后台成本／参考价／实扣 0、经校验 `BLOCK_AI` 拦截而查询故障放行、`DEAD > 0` 可发现。`PREPAID`：独立绑定和凭据、同样的离线／补投链路、同事件钱包只扣一次、余额停机拦调用、复机恢复、停机余额及透支额观测。两路离线测试只临时改变 AI API 侧计费地址或容器出站，随后恢复；不得停止、重启或改动生产 Billing Hub。正式档另验 MySQL 多 worker、请求级幂等、429、未知用量、自动计价核对、清理与可审计死信重投。`TestClient`、asyncio、数据库并发和真实外部调用只放 CI／受控环境。 |

## 冲突与差异

1. **查询故障规则（历史）：**Billing Hub PR 238 已修正 `origin/main:docs/api.md` 的“失败时停止调用”旧句；现行接口契约与 `REQ-AVAIL-001` 一致，查询失败或应答无法校验时放行。
2. **代码与设计要求：**`app/billing.py:BillingClient.allows_ai` 用 `isinstance(value, int)` 校验 `status_version`，会接受布尔值；设计要求严格整数，需后续实现。
3. **用量确认层次：**本地 `DELIVERED` 对应 Billing Hub 持久接收，不等于计价完成。内部档用现有 ADMIN 查询与健康告警人工核对；正式多租户自动核对依赖未来新契约。
4. **答复与 outbox 顺序：**Billing Hub 架构摘要的图示先画 AI 答复再画 outbox；本项目代码及本次要求为先提交 outbox 再返回。以可验证的本项目持久用量要求为准，摘要图不作为执行顺序契约。

## 待 Kelvin 决定

### 联调前置（已定、未完成）

1. Kelvin 在 Billing Hub 为内部计量 CRM 项目签发凭据。
2. Kelvin 在 Billing Hub 创建 `PREPAID` 测试租户和项目，并签发独立凭据。
3. Billing Hub 配好 Anthropic 模型目录、`LLM_TOKEN` 供应商价格和参考客户售价；否则第一笔用量会报 `MODEL_UNKNOWN` 或 `PRICING_ERROR`。这是 Billing Hub 侧准备，不是本项目的设计待决项。
4. 部署：GitHub 公开仓库已建（2026-10-09）；已定直接使用部署工作流，合并到 `main` 后按提交 SHA 部署，并使用 VPS 共享 MySQL 中的独立库。部署设计和工作流另行完成，不在本文展开。
5. 在 VPS 运行环境配置两个独立客户端绑定，启动 API 和 worker。

Billing Hub 的目录／价格、ADMIN 权限及中心告警属跨项目准备，不列入本项目设计待决项。

### 正式多租户上线前

1. CRM 批准的 `contact_snapshot` 字段、个人数据审查、输入上限与 `context_text` 迁移；员工与对象权限由 CRM 验收。
2. 请求级幂等键的格式／保留期限、409 响应体、进行中／结果未知处理；CRM 不存摘要正文（2026-10-09 Kelvin），同键同内容的 409 只能带回原 `request_id`，员工要看摘要须用新键重新生成。
3. 客户端／能力限流阈值、outbox 容量和重试上限；本地版本化状态、webhook 接收与周期对账须等 Billing Hub 状态同步设计闸门，本项目不预定版本应用和解除规则。
4. VPS 上 API／worker 的运维职责；Billing Hub 新的项目凭据级最终计价查询契约与 AI API 核对频率；供应商能否提供准确逐请求用量；本地记录保留／清理期限、可审计死信重投与告警升级时限。

### 以后再说

1. `acuven-shop-customer-service` 计费项目究竟对应员工订单摘要、未来网店员工客服辅助，还是独立顾客 AI Chatbot；现有 `shop.order_summary` 不是客服对话，网店目前也没有客服功能，不在本稿代选。
2. Billing Hub 若以后新增项目停用或安全暂停状态，再按当时批准的状态同步契约处理；跨系统工具、BYOK、客户自托管计费与统一工作台也另立设计。
