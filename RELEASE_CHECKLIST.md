# 发布检查清单

合入 `main`、打版本号之前过一遍。能自动查的都在 `./check-before-publish.sh` 里，先跑它。

## 自动检查

- [ ] `./check-before-publish.sh` 全部通过：后端与前端测试、前端产物是最新的、没有本机工作区文件和 `.env`、
      不随仓库分发的文档没进仓库、没有疑似密钥、Docker 镜像能构建

## 手动检查

### 全新环境跑通

- [ ] 在一台干净的机器（或新目录）`git clone` 后执行 `./start.sh demo`，按 README「5 分钟上手」走完，能出报告
- [ ] 接一个真实的 KnowFlow，走一遍「测已有助手」（含一组参数对照），跑完后 KnowFlow 里没有残留的 `knoweval-` 临时助手
- [ ] 从助手的知识库出题（含一份 Word 文档），出完自动开始评测
- [ ] 接一个真实的 RAGFlow，至少跑通一种评测路线

### 文档

- [ ] README 的命令、截图和当前版本一致
- [ ] CHANGELOG 写了这个版本的变化，日期已填
- [ ] 版本号已更新：`pyproject.toml`、`src/knoweval/__init__.py`、`docker-compose.yml` 里的默认镜像标签（三处一致）

### 数据集

- [ ] 新增或修改的数据集，`manifest.json` 写明许可；版权不允许分发的文档标了 `"redistribute": false`，只给下载脚本
- [ ] 题目文件带金丝雀字符串

### 镜像

- [ ] 用要发布的那个提交构建镜像，推到 Docker Hub：`knowflowai/knoweval:<版本号>` 和 `latest`
- [ ] 在干净的机器上 `./start.sh demo` 能直接拉到这个镜像并启动（不走源码构建）

### 合入

- [ ] `dev` 上的改动全部合入 `main`，`main` 上打版本标签（如 `v0.1.0`）
