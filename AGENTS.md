# acuven_ai_api — 项目规则

本文件与同目录另一份项目规则文件同步。本项目是独立系统侧 AI API；顾客 AI Chatbot 与 `ai_billing_hub` 另有代码库。合并到 `main` 即按提交 SHA 自动部署到 VPS 生产（`.github/workflows/deploy.yml`，设计见 `docs/DEPLOYMENT.md`）。

1. 先明确需求、成功标准和信任边界，歧义先核实。
2. 用最少代码解决当前任务，不预建多层微服务。
3. 只改任务相关文件，不顺手重构相邻项目。
4. 以可验证的调用、用量事件和故障行为作为完成标准。
5. 模型只用于摘要、起草等判断任务；鉴权、计费签名、重试由代码决定。
6. 控制单任务范围，超过范围先拆分。
7. 发现冲突契约时标明权威来源，不融合两套规则。
8. 修改计费接入前先读 `ai_billing_hub/docs/api.md`；该仓库有只读规则。
9. 测试应验证计费幂等、租户归属与故障行为，不只镜像代码。
10. 每个关键步骤记录已验证与未验证的部分。
11. 遵守现有接口命名和本项目约定。
12. 测试未执行、联调未完成或有死信时明确报告。

密钥、客户上下文、供应商凭据、真实价格均不得进入仓库或日志。AI API 不直接读写业务模块数据库；模块负责用户和对象权限。正式接入个人数据、生产计费或部署前应完成设计审查与运维验收。

## OpenClaw 契约（project_id `acuven_ai_api`，任务编号 `AIAPI-TASK-NNN`）

- `.platform/` 三个文件默认 deny，只由运营者的 PR 改，OpenClaw 的 run 改不了。自动收尾未启用：任务交付后由人手工开收尾 PR（`tasks.yaml` 里该任务 `ready` 改 `done`、从 planning-v1「当前计划」移除），只改这两个文件不触发部署（`deploy.yml` 的 `paths-ignore`）。
- 每个 `ready` 任务都要出现在 `docs/TODO.md` 的 planning-v1 块里；`allowed_change_paths` 逐个写精确文件路径；`allowed_commands` 只引用 `commands.yaml` 里的 id。
- 只在 CI 跑的检查不写进 `allowed_commands`：`tests.api`（TestClient 要回环，Worker 沙箱里会挂住）。沙箱能跑的是 `tests.rules`（只用标准库）。验收标准依赖 API 测试时，写明由必需检查 `tests` / `tests-mysql` 执行。
- 任务的标题、目的、验收标准不写 `xxx://` 地址、主机名、邮箱、IP、绝对路径；`title` 一句中文、80 字符内，不用反引号、尖括号、「」『』【】〖〗。
- 碰钱、碰个人数据、改表结构的任务，登记为 `ready` 之前按控制面 `docs/ONBOARD-PROJECT.md`「登记任务之前」跑设计预审，拆分规则同该文。
