#!/usr/bin/env bash
# 备份 KnowEval 的数据：workspace/（系统接入、设置、测试集、评测结果）和 .env（含 API Key，注意保管）。
#   ./backup.sh            备份到 backups/knoweval-时间.tar.gz
#   ./backup.sh /some/dir  备份到指定目录
set -euo pipefail
cd "$(dirname "$0")"

DEST="${1:-backups}"
mkdir -p "$DEST"
FILE="$DEST/knoweval-$(date +%Y%m%d-%H%M%S).tar.gz"
ITEMS=(workspace)
[ -f .env ] && ITEMS+=(.env)
tar -czf "$FILE" "${ITEMS[@]}"
echo "已备份到 $FILE（含 .env 里的 API Key，请妥善保管）"
echo "恢复：./stop.sh && tar -xzf $FILE && ./start.sh"
