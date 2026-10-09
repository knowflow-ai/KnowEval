#!/usr/bin/env bash
# 启动 KnowEval 评测台（Docker）
#   ./start.sh        启动评测台
#   ./start.sh demo   同时启动内置的演示 RAG 服务（没有自己的 RAG 系统也能先跑通一遍）
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "没有找到 docker：先安装 Docker（https://docs.docker.com/get-docker/）" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "没有找到 docker compose 插件：升级 Docker Desktop，或安装 Compose v2" >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已从 .env.example 生成 .env：判分要用的大模型 Key（KNOWEVAL_LLM_API_KEY）和被测系统的 Key 写在里面"
fi
mkdir -p workspace
# 容器里以 uid 1000 运行：Linux 上用 root 启动时，把工作区交给它，免得容器写不进去
if [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" = "0" ]; then
  chown -R 1000:1000 workspace
fi

PROFILE=()
if [ "${1:-}" = "demo" ]; then
  PROFILE=(--profile demo)
fi

docker compose ${PROFILE[@]+"${PROFILE[@]}"} up -d --build

PORT="${KNOWEVAL_PORT:-8780}"
echo -n "等评测台启动"
for _ in $(seq 1 60); do
  if curl -fs "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo
    echo "评测台已启动：http://127.0.0.1:${PORT}"
    echo "下一步：在「设置」页填判分模型的地址和模型名（Key 写在 .env 的 KNOWEVAL_LLM_API_KEY），再到「系统」页接入被测系统。"
    if [ "${1:-}" = "demo" ]; then
      echo "体验：「系统」页点「一键接入演示系统」，再「新建评测 → 完整测试」选 demo-md 测试集。"
    fi
    echo "查看日志：docker compose logs -f knoweval    停止：./stop.sh"
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo
echo "评测台没有按时启动，看看日志：docker compose logs knoweval" >&2
exit 1
