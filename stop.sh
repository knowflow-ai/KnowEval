#!/usr/bin/env bash
# 停止 KnowEval 评测台（含演示服务）。workspace/ 里的数据不受影响。
set -euo pipefail
cd "$(dirname "$0")"
docker compose --profile demo down
echo "已停止。数据还在 workspace/ 里，./start.sh 重新启动。"
