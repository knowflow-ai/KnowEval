#!/usr/bin/env bash
# 更新 KnowEval：拉取最新代码和对应版本的镜像并重启。workspace/ 里的数据不受影响。
#   ./update.sh          拉取发布好的镜像
#   ./update.sh --build  从源码构建镜像
set -euo pipefail
cd "$(dirname "$0")"

if [ -d .git ]; then
  git pull --ff-only
else
  echo "不是 git 仓库，跳过拉取代码：请先手动更新源码"
fi

# docker-compose.yml 要读 .env：以前没生成过的补一份
[ -f .env ] || cp .env.example .env

PROFILE=()
if docker compose --profile demo ps --services --status running 2>/dev/null | grep -q '^demo-rag$'; then
  PROFILE=(--profile demo)
fi
if [ "${1:-}" = "--build" ]; then
  docker compose build knoweval
elif ! docker compose pull knoweval; then
  echo "拉取镜像失败，改为从源码构建……"
  docker compose build knoweval
fi
docker compose ${PROFILE[@]+"${PROFILE[@]}"} up -d
echo "已更新并重启：http://127.0.0.1:${KNOWEVAL_PORT:-8780}"
