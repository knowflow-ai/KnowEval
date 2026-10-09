#!/bin/sh
# 容器入口：首次启动时把自带的公开数据集拷进工作区（已有的同名数据集不覆盖），然后启动评测台。
# 也可以跑其它命令，例如：docker compose run --rm knoweval knoweval run --help
set -e

WS="${KNOWEVAL_WORKSPACE:-/workspace}"
# 容器里以 uid 1000 运行；宿主机上的 workspace/ 属于别的用户（比如用 root 建的）时写不进去，说清楚怎么修
if ! touch "$WS/.write-test" 2>/dev/null; then
  echo "工作区 $WS 没有写权限（容器以 uid $(id -u) 运行）。在宿主机上执行：sudo chown -R $(id -u):$(id -g) workspace，再重新启动" >&2
  exit 1
fi
rm -f "$WS/.write-test"
mkdir -p "$WS/datasets"
for d in /app/datasets/*/; do
  name=$(basename "$d")
  if [ ! -e "$WS/datasets/$name" ]; then
    cp -r "$d" "$WS/datasets/$name"
    rm -rf "$WS/datasets/$name/build"   # 数据集的构建脚本留在 /app 里，不放进工作区
  fi
done

# 所有命令都在工作区里跑：命令行读工作区的 settings.json（判分模型）和 .env
cd "$WS"
if [ "$1" = "ui" ]; then
  shift
  exec knoweval ui "$WS" "$@"
fi
exec "$@"
