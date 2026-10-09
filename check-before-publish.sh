#!/usr/bin/env bash
# 发布前检查（合入 main、打版本前跑一遍）：测试、前端产物是否最新、有没有混进本机文件和密钥、镜像能不能构建。
#   ./check-before-publish.sh          全部检查
#   ./check-before-publish.sh --quick  跳过 Docker 构建
set -uo pipefail
cd "$(dirname "$0")"

FAILED=0
ok()   { echo "  ✓ $1"; }
bad()  { echo "  ✗ $1"; FAILED=1; }
step() { echo; echo "[$1]"; }

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

step "后端测试"
if "$PY" -m pytest >/tmp/knoweval-pytest.log 2>&1; then ok "$(grep -E "passed|failed" /tmp/knoweval-pytest.log | tail -1)"; else bad "pytest 失败，见 /tmp/knoweval-pytest.log"; fi

step "前端测试与构建"
if (cd web && npm test --silent >/tmp/knoweval-vitest.log 2>&1); then ok "vitest 通过"; else bad "vitest 失败，见 /tmp/knoweval-vitest.log"; fi
BEFORE=$(shasum -a 256 src/knoweval/web/report.html | cut -d' ' -f1)
if (cd web && npm run build >/tmp/knoweval-build.log 2>&1); then ok "前端构建通过"; else bad "前端构建失败，见 /tmp/knoweval-build.log"; fi
AFTER=$(shasum -a 256 src/knoweval/web/report.html | cut -d' ' -f1)
if [ "$BEFORE" = "$AFTER" ]; then
  ok "src/knoweval/web/report.html 和前端源码一致"
else
  bad "src/knoweval/web/report.html 之前不是最新的（已重新构建）：把它一起提交"
fi

step "不该进仓库的文件"
LEAKED=$(git ls-files | grep -E '(^|/)\.env$|^(settings|systems)\.json$|^(evals|builds|workspace|runs)/|^\.cache/' || true)
if [ -z "$LEAKED" ]; then ok "没有本机工作区文件、.env"; else bad "这些文件不该被跟踪：$LEAKED"; fi
NOT_REDIST=$("$PY" - <<'PYEOF'
import json, pathlib, subprocess
tracked = set(subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n"))
for m in pathlib.Path("datasets").glob("*/manifest.json"):
    for f in json.loads(m.read_text(encoding="utf-8"))["files"]:
        path = f"{m.parent}/docs/{f['name']}"
        if f.get("redistribute") is False and path in tracked:
            print(path)
PYEOF
)
if [ -z "$NOT_REDIST" ]; then ok "不随仓库分发的文档都没有进仓库"; else bad "这些文档标了不分发却被跟踪：$NOT_REDIST"; fi

step "密钥"
SECRETS=$(git grep -nIE '(sk-[A-Za-z0-9]{20,}|ragflow-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' -- ':!src/knoweval/web/report.html' || true)
if [ -z "$SECRETS" ]; then ok "没有发现疑似密钥"; else bad "疑似密钥：$SECRETS"; fi
if grep -qE '^[A-Z_]*KEY=.+' .env.example; then bad ".env.example 里填了 Key 的值"; else ok ".env.example 只有变量名"; fi

if [ "${1:-}" != "--quick" ]; then
  step "Docker 镜像"
  if docker compose build >/tmp/knoweval-docker.log 2>&1; then ok "镜像构建通过"; else bad "镜像构建失败，见 /tmp/knoweval-docker.log"; fi
fi

echo
if [ "$FAILED" -eq 0 ]; then echo "全部通过"; else echo "有检查没通过"; exit 1; fi
