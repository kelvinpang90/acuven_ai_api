# 部署设计

范围：Billing Hub 第 3 阶段试点（内部计量与 `PREPAID` 两路）的 VPS 部署。正式多租户的迁移、备份恢复、告警与容量另行验收。本文不写主机名、IP、域名、路径或密钥；这些只在 GitHub secrets 和 VPS 运行环境里。

## 已定输入（2026-10-09 Kelvin）

- 部署在 VPS 共享栈；合并到 `main` 后由部署工作流按提交 SHA 自动部署。
- 数据库用共享 MySQL 中的独立库。
- 表结构试点沿用 `create_all`；任何表结构变更之前先引入 Alembic。
- CI 加 `mysql:8.0` 服务容器的 job，并设为 `main` 的必需检查。
- AI API **只在内网访问**：无域名、无 nginx 配置、不发布宿主机端口。
- Billing Hub 走其**公网 HTTPS** 地址。
- 仓库公开。

## 拓扑

| 项 | 设计 |
| --- | --- |
| compose 项目名 | `acuven_ai_api` |
| 服务 | `ai_api`（`uvicorn app.main:app`，容器内 8010）与 `ai_api_worker`（`python -m app.worker`），同一镜像 `ghcr.io/kelvinpang90/acuven_ai_api:<SHA>` |
| 网络 | 只挂 `data_net`；不挂 `proxy_net`，不加入 `infra_nginx`。服务名带 `ai_api` 前缀，避免与共享网络上其他项目重名 |
| 出站 | `data_net` 是普通 bridge，经 NAT 访问 Anthropic 与 Billing Hub 公网 HTTPS；不需要额外配置 |
| 内网调用方 | 同一 VPS 上挂 `data_net` 的容器以 `http://ai_api:8010` 访问（CRM 接入时同理）；试点手工调用在容器内发起（见「试点操作」） |
| 资源 | `ai_api` 内存上限 256m，`ai_api_worker` 128m；日志 json-file 10m×3，与 `acuven-shop` 一致 |
| 重启 | `restart: unless-stopped` |

## 运行配置

VPS 上部署目录里的 `.env`（`chmod 600`，不进仓库，由 Kelvin 写），compose 用 `${VAR:?}` 读取，缺任何一项直接报错，不会悄悄回落到 SQLite：

| 变量 | 内容 |
| --- | --- |
| `AI_API_DATABASE_URL` | `mysql+pymysql://<用户>:<密码>@mysql:3306/acuven_ai_api` |
| `AI_API_CLIENTS_JSON` | 两个客户端绑定（内部计量 CRM 项目、`PREPAID` 测试项目）；整段 JSON 用单引号包住 |
| `AI_API_ANTHROPIC_KEY` | Anthropic API key |
| `AI_API_ANTHROPIC_MODEL` | 所用模型；响应中的实际模型名须已在 Billing Hub 模型目录里 |
| `AI_API_BILLING_BASE_URL` | Billing Hub 公网 HTTPS 源地址（不带路径） |

## 数据库

- 用 `vps_infra/scripts/provision-project.sh acuven_ai_api <redis起始号>` 建库 `acuven_ai_api` 和用户 `acuven_ai_api_app`。本项目不用 Redis，但脚本必须传这个参数，它只打印、不分配；脚本打印的是 `aiomysql` 连接串，本项目用同步的 `pymysql`，按上表改写。
- **表结构（已定）**：试点沿用启动时的 `create_all`，它只会建表、不会改表；试点只有 `usage_outbox` 一张表。**任何表结构变更之前先引入 Alembic**，以 `stamp` 对齐已有表，`deploy/deploy.sh` 届时加迁移步骤；这条变更本身需单独评审。
- **MySQL 兼容（已定）**：现有测试只跑 SQLite。`DateTime(timezone=True)` 在 MySQL 是不带时区的 `DATETIME`；共享 MySQL 的会话时区是 `+00:00`、代码全用 UTC，按理可用，但未验证。CI 加一个 `mysql:8.0` 服务容器的 job `tests-mysql`，用同一套测试跑一遍（测试读 `AI_API_TEST_DATABASE_URL`，缺省仍用 SQLite）。Kelvin 在 GitHub 分支保护里把 `tests-mysql` 加为 `main` 的必需检查（与现有 `tests` 并列）。2026-10-09 用本机 MySQL 8.0 实测：默认 `DATETIME` 只到秒且四舍五入，到期时间为「现在」的行可能被舍入到未来、当轮领取不到（测试 3 次失败 2 次）；因此 `next_attempt_at` 在 MySQL 上用 `DATETIME(6)`（生产库尚未建表，不属于对已有表的变更）。

## 镜像与健康检查

- `Dockerfile` 增加构建参数 `GIT_SHA`，写入环境变量 `AI_API_GIT_SHA`。
- `GET /health` 返回 `{"status": "ok", "version": "<SHA>"}`。API 启动时会连数据库建表，数据库不可达时进程起不来，容器即不健康。
- worker 每轮循环写一次心跳文件；容器健康检查要求 180 秒内更新过。数据库异常会让进程退出、由 `restart` 拉起，期间不健康。
- 两个容器的健康检查都用镜像里的 Python 执行，不另装 curl。

## 部署工作流

照 `acuven-shop` 的 `deploy.yml` 加 `deploy/deploy.sh`，满足 D1–D5：

- **D1**：`push` 到 `main` 触发；`paths-ignore` 保留 `.platform/tasks.yaml` 与 `docs/TODO.md`（供以后 OpenClaw 自动收尾）；`workflow_dispatch` 只收 40 位 SHA，用于手工重部署或回滚。
- **D2**：`build` job 以 `DEPLOY_SHA` 为标签构建并推送一个镜像（带 `GIT_SHA` 和 `acuven.project=acuven_ai_api` 标签）；`deploy` job 经 SSH 在 VPS 上 `git checkout --detach "$DEPLOY_SHA"`，运行该提交自己的 `deploy/deploy.sh`。
- **D3**：`deploy/deploy.sh` 先记下当前镜像 → 拉新镜像 → 同时换两个容器 → 等两个都健康 → 清理本项目旧镜像；不健康就回滚到旧镜像，**回滚成功也以非零退出**；首次部署没有回滚目标，保留现场。之后另一个 SSH 步骤在 VPS 上从 `ai_api` 容器内请求 `/health`，核对 `version` 等于 `DEPLOY_SHA`。内网服务从 GitHub runner 访问不到，所以**不用** `HEALTHCHECK_URL`；控制面 D3 只要求项目自己的上线后健康检查。
- **D4**：`concurrency` 固定分组，`cancel-in-progress: false`。
- **D5**：两个 job 的 `timeout-minutes` 合计不超过 25；不用需要人工批准的 environment。
- 新旧 worker 交替：表结构不变；`claim_id` 条件更新防止两个 worker 同时完成同一行；即使重复发出 HTTP，Billing Hub 按 `event_id` 只生效一次。
- 首次推送时 `.env` 或 secrets 还没配，部署失败是预期结果，按现象报告，不反复重跑。

**需要 Kelvin 在 GitHub 配的 secrets**（名称与 `acuven-shop` 相同，值不可跨仓库复制，需要重新填）：`VPS_HOST`、`VPS_USER`、`VPS_SSH_KEY`、`VPS_PORT`、`VPS_FINGERPRINT`、`VPS_APP_DIR`。不需要 repository variables。

2026-10-10 首次上线时踩到的两点：
- 在 Windows 的 Git Bash 里用 `gh secret set VPS_APP_DIR --body /opt/...` 会被自动改写成 Windows 路径，部署时 `cd` 失败；用 PowerShell 或交互式输入设置路径类 secret。
- `VPS_FINGERPRINT` 要用服务器 **ECDSA** 主机密钥的 SHA256 指纹（`ssh-keygen -l -f /etc/ssh/ssh_host_ecdsa_key.pub`）：`appleboy/ssh-action` v1.0.3 的 Go SSH 库优先协商 ECDSA，ed25519 排在最后。

## 试点操作

**首次上线顺序（Kelvin 在 VPS 上做）：**

1. 建部署目录并克隆本仓库（公开仓库，拉取不需要凭据）。
2. 运行建库脚本，记下数据库密码。
3. 写 `.env`，`chmod 600`。
4. 在 GitHub 配齐 secrets。
5. 合并部署 PR 会自动触发首次部署。1–4 步在合并前做完，这次就应成功；没做完则这次失败是预期的，做完后在 Actions 里手工触发一次部署（填 `main` 当前的完整 SHA），确认 `build`、`deploy` 都成功。

**手工执行 compose 命令之前**：compose 文件要求 `AI_API_IMAGE`，没设时连 `docker compose ps` 都会报错。在部署目录里先设为正在运行的镜像：

```bash
export AI_API_IMAGE="$(docker inspect --format '{{.Config.Image}}' acuven_ai_api-ai_api-1)"
```

**手工重建容器**（改了 `.env` 之后）：设好上面的变量后 `docker compose up -d --force-recreate`。

**试点调用**：在 `ai_api` 容器内用 Python 请求 `http://127.0.0.1:8010/v1/capabilities/crm.contact_summary`，带客户端 token。token 由 Kelvin 持有，不进命令历史、不进仓库。

**查死信**：`docker compose logs ai_api_worker` 里搜 `requires intervention`；或在 MySQL 里按 `status` 统计 `usage_outbox`。

### 离线演练

已批准的计费设计原本允许两种方法，这里只保留第一种：

1. 把 `.env` 的 `AI_API_BILLING_BASE_URL` 临时改成本机不可达的 HTTPS 地址（例如 `https://127.0.0.1:9`），按上面的方法重建容器。
2. 发起调用：应照常返回 AI 结果，用量停在 `PENDING`，worker 日志显示投递失败并重试。
3. 改回原地址并重建容器：积压的用量应补投成功，Billing Hub 后台每个 `event_id` 只出现一次。

**不用「容器层阻断出站」**：`ai_api` 访问 Anthropic 和 Billing Hub 走的是同一条出站，一起阻断后模型调用也会失败，测不到「离线仍能回复」；只拦 Billing Hub 又要改宿主机防火墙，属于高风险操作。全程不停止、不重启、不改动生产 Billing Hub。

## 风险

- **Cloudflare 拦截**：Billing Hub 公网地址走 Cloudflare 代理，可能对非浏览器请求发起挑战。若出现，状态查询会按「应答无法校验」放行（不中断服务），但用量投递会失败。首次部署后第一件事是看 worker 日志和一次状态查询的结果。
- **`create_all` 不改表**：见「数据库」；违反「先引入 Alembic」的规定就会在部署后才暴露问题。
- **共享 VPS**：两个容器有内存上限；镜像只清理带本项目标签的。

## 实施（批准后，一个分支一个 PR）

- 代码：`/health` 返回 SHA、worker 心跳、`Dockerfile` 的 `GIT_SHA`、测试可切换数据库、`tests-mysql` CI job。
- 部署：`docker-compose.yml`、`deploy/deploy.sh`、`.github/workflows/deploy.yml`、`.env.example` 更新。
- OpenClaw 接手准备：`.platform/` 三个文件、planning-v1，以及供 Worker 沙箱用的纯逻辑测试（不依赖 `TestClient`）。
- 合并即触发首次生产部署。合并前确认 secrets 已配、VPS 上 `.env` 已就绪；否则首次部署失败是预期的。
