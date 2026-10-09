# KnowEval 评测台镜像：前端在第一阶段构建，打进 Python 包；运行时只有一个 Python 进程。
# 构建：./start.sh --build（或 docker compose build）；发布的镜像在 Docker Hub：knowflowai/knoweval

# ---------- 第一阶段：构建前端（单文件 HTML，评测台和报告共用） ----------
FROM node:20-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# copy-template 会把产物写进 ../src/knoweval/web/report.html，这里只要 dist/index.html
RUN mkdir -p ../src/knoweval/web && npm run build

# ---------- 第二阶段：运行环境 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # 容器里监听所有网卡；宿主机能不能访问由 docker-compose.yml 的端口映射决定（默认只映射到 127.0.0.1）
    KNOWEVAL_HOST=0.0.0.0 \
    KNOWEVAL_PORT=8780 \
    KNOWEVAL_WORKSPACE=/workspace

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=web /build/web/dist/index.html ./src/knoweval/web/report.html
RUN pip install --no-cache-dir . && rm -rf /root/.cache

# 自带的公开数据集与演示服务；首次启动时数据集拷进工作区
COPY datasets/ /app/datasets/
COPY examples/ /app/examples/
COPY configs/ /app/configs/
COPY docker/entrypoint.sh /usr/local/bin/knoweval-entrypoint
RUN chmod +x /usr/local/bin/knoweval-entrypoint \
    && useradd --create-home --uid 1000 knoweval \
    && mkdir -p /workspace && chown knoweval:knoweval /workspace

USER knoweval
VOLUME ["/workspace"]
EXPOSE 8780

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"KNOWEVAL_PORT\",\"8780\")}/api/health', timeout=4)" || exit 1

ENTRYPOINT ["knoweval-entrypoint"]
CMD ["ui"]
