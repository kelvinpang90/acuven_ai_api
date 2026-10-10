# acuven_ai_api

独立的系统侧 AI API。CRM、ERP、网店、Inventory 和 POS 通过各自后端调用；面向顾客的 AI Chatbot 保持独立。当前交付的是**只读 AI 能力与计费接入的第一条可运行链路**，尚未连接真实业务模块或生产计费账户。

## 当前接口

- `GET /health`：返回 `status` 与镜像构建时注入的提交 SHA（`version`）。
- `POST /v1/capabilities/{capability}`：服务端凭据鉴权，基于最小化的业务上下文生成只读文本。

已定义的能力：`crm.contact_summary`、`erp.document_summary`、`shop.order_summary`、`inventory.stock_explanation`、`pos.sale_summary`。每个客户端只可调用配置里授权的能力。AI 不直接连接业务数据库，不执行退款、改价、调库存或其他业务写入。

## 运行

Python 3.12+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

另开进程运行本地 outbox 投递：

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

同一个 `Dockerfile` 可分别运行 API（默认命令）与 worker（覆盖命令为 `python -m app.worker`）。容器部署应配置独立 MySQL 数据库及上述环境变量；本地 SQLite 文件方案仅供演示，不能直接用于多副本服务。生产部署见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)：合并到 `main` 后按提交 SHA 部署。

测试默认用 SQLite；设 `AI_API_TEST_DATABASE_URL`（例如一次性 MySQL 8.0 库）即改用该库，每个测试开始时清空本项目的表。

先配置 `.env.example` 所列环境变量；程序不会自动读取 `.env`。`AI_API_CLIENTS_JSON` 为服务端 JSON 配置，包含模块客户端的 SHA-256 token 摘要、租户与 `ai_billing_hub` 分配的项目、凭据和能力范围。真实凭据只放运行环境，不写入 Git。用 `python -c "import hashlib; print(hashlib.sha256(b'YOUR_RANDOM_TOKEN').hexdigest())"` 计算示例格式，实际 token 应由安全随机数生成。每个租户的计费凭据须与配置中的 `tenant_id`、`billing_project_id` 绑定一致。每次调用模型前实时查询 `ai_billing_hub` 的 `effective-status`：明确返回 `BLOCK_AI` 时拒绝（403）；查询失败或应答无法校验时照常放行（不变量 1：中心计费故障不得中断客户 AI 服务），用量仍写本地 outbox，恢复后补送。

调用示例（`crm.contact_summary` 用结构化契约 v1，见 [docs/CONTACT_SUMMARY_V1.md](docs/CONTACT_SUMMARY_V1.md)；其他四个能力仍收 `{"context_text": "..."}`）：

```powershell
$headers = @{ Authorization = 'Bearer <client-token>'; 'X-Acuven-Client-Id' = 'crm-demo' }
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8010/v1/capabilities/crm.contact_summary' -Headers $headers -ContentType 'application/json' -Body '{"schema_version":"1.0","contact_ref":"contact:1","contact_schema_version":"demo-1","contact_snapshot":{"note":"客户希望下周回电。"},"as_of":"2026-10-10T09:30:00+08:00"}'
```

## 计费边界

AI API 在每次模型调用前以 HMAC 凭据查询 Billing Hub 的有效服务状态；Billing Hub 返回经校验的 `BLOCK_AI`（如余额暂停、管理员禁用）时不发起新的模型调用；查询失败或应答无法校验时照常放行。获得允许后，AI API 从模型响应读取实际 token 数量，将不含提示词和回答正文的 `LLM_TOKEN` 事件持久保存到本地 outbox，随后返回 AI 答案。独立 worker 按 `ai_billing_hub` 的 HMAC 签名契约投递；重试沿用**同一个 `event_id` 和原始请求体**，每次 HTTP 尝试生成新的签名请求 ID。中心计费端返回持久化 `202` 或合法重复 `200` 后，本地事件标记为已送达；失败按响应中的 `retryable` 处理。已允许的模型调用完成后，单次用量投递失败由 outbox 重试。

接口依据：[ai_billing_hub 用量摄取与签名契约](../ai_billing_hub/docs/api.md)。模型请求格式依据 [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create)。

## 当前交付边界

- 已用模拟模型及模拟计费端验证调用、鉴权、隔离、HMAC、可重试与不可重试错误；**未使用真实密钥做在线联调**。
- 当前配置和建表方式用于隔离演示。正式多租户上线前需要凭据管理、数据库迁移、限流、审计、死信操作与告警、业务模块权限和数据最小化验收。
- 当前只读能力由模块后端先完成用户授权检查；AI API 按配置检查模块客户端和能力范围。具体对象权限与业务事实仍由模块负责。
- 供应商调用在超时后可能产生无法从响应获知的用量，需在生产设计中增加供应商侧对账方案。
- 客户自行部署时，`ai_billing_hub` 现有信任边界尚未覆盖客户控制的机器，需要单独设计，不视为已交付。
- Acuven 自用的「超级租户：只计算费用、不扣钱包、余额不足不停用」**不是当前 Billing Hub 已有模式**。在该平台的计费规则完成设计与实现前，不能把普通租户配置成超级租户并接真实用量；详见 [待办](docs/TODO.md)。
