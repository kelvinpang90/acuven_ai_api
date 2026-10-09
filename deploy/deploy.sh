#!/usr/bin/env bash
#
# 把一个提交的镜像部署到本机的生产栈。由 .github/workflows/deploy.yml 经 SSH 调用。
#
# 用法（在服务器上本仓库检出的根目录里）：
#     AI_API_IMAGE_REPO=ghcr.io/所有者/仓库名 deploy/deploy.sh 完整的40位commitSHA
#
# 顺序：记下正在跑的版本 → 拉新镜像 → 同时换 ai_api 与 ai_api_worker → 等两个都健康 → 不健康则回滚。
# 成功才以 0 退出；换容器失败、不健康、回滚（无论回滚成败）都以非零退出（部署观察契约 D3）。
# 没有迁移步骤：试点表结构由启动时的 create_all 建；任何表结构变更之前先引入 Alembic（docs/DEPLOYMENT.md）。

set -euo pipefail

SHA="${1:-}"
HEALTH_TIMEOUT_SECONDS="${AI_API_HEALTH_TIMEOUT_SECONDS:-180}"

log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# compose 文件里的全部服务都在、且都报 healthy 才算健康。
wait_for_health() {
    local deadline expected healthy status
    deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
    expected="$(docker compose config --services | wc -l)"
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if status="$(docker compose ps --format '{{.Service}} {{.Health}}')"; then
            healthy="$(printf '%s\n' "$status" | awk '$2 == "healthy"' | wc -l)"
            if [ "$healthy" -eq "$expected" ]; then
                return 0
            fi
        fi
        sleep 5
    done
    return 1
}

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "usage: deploy/deploy.sh <full 40-character lowercase commit SHA>"
[ -n "${AI_API_IMAGE_REPO:-}" ] || die "AI_API_IMAGE_REPO is not set"

export AI_API_IMAGE="${AI_API_IMAGE_REPO}:${SHA}"

# 回滚目标必须在任何改动之前记下：出事时正在跑的那一版就是回滚目标。两个容器同一镜像，以 ai_api 为准。
PREVIOUS="$(docker compose ps --format '{{.Image}}' ai_api)"
log "currently running: ${PREVIOUS:-nothing}"

log "pulling $AI_API_IMAGE"
docker compose pull --quiet || die "pull failed; nothing has been changed"

log "starting $SHA"
HEALTHY=0
if docker compose up -d --no-build && wait_for_health; then
    HEALTHY=1
fi

if [ "$HEALTHY" = "1" ]; then
    log "deployed $SHA"
    # 只清本项目的（构建时打了这个标签）、且没有任何容器在用的旧镜像；正在跑的这一版不会被动到。
    # 清理失败不改变部署结论，只记一条警告。
    if ! docker image prune --all --force --filter "label=acuven.project=acuven_ai_api" >/dev/null; then
        log "WARNING: could not prune old images"
    fi
    exit 0
fi

log "deploy of $SHA is not healthy"
docker compose ps

if [ -z "$PREVIOUS" ]; then
    # 首次部署没有回滚目标：保留现场给人排查，不把栈停掉。
    die "nothing to roll back to; leaving the stack up for inspection"
fi

log "rolling back to $PREVIOUS"
export AI_API_IMAGE="$PREVIOUS"
docker compose up -d --no-build || die "rollback failed; manual intervention required"
if wait_for_health; then
    # 回滚成功也以非零退出：这个提交没能上线，部署结论必须是失败。
    die "rolled back to $PREVIOUS; the deploy of $SHA failed"
fi
die "rollback did not become healthy; manual intervention required"
