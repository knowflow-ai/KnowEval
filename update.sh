#!/usr/bin/env bash
# 更新 KnowEval：拉取最新代码、重新构建镜像并重启。workspace/ 里的数据不受影响。
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
docker compose ${PROFILE[@]+"${PROFILE[@]}"} up -d --build
echo "已更新并重启：http://127.0.0.1:${KNOWEVAL_PORT:-8780}"
