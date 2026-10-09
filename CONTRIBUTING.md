# 参与贡献

欢迎提 Issue 和 PR：报 bug、补数据集、接入新的被测系统、改进出题和判分，都很需要。

## 报问题

请附上：

- 怎么复现（用的哪个数据集、哪种评测路线、被测系统是什么）；
- 期望的结果和实际的结果；
- 评测详情页「运行信息」里的内容，或者运行目录里的 `run.json`（里面只有环境变量名，没有 Key）。

被测系统那边的问题（比如 KnowFlow 的嵌入模型没配置）不在这里修，但如果评测台给的提示不够清楚，也欢迎提。

## 本地开发

```bash
# 后端（Python 3.11+）
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest

# 前端（Node 20+）
cd web
npm ci
npm test
npm run build        # 产物写进 src/knoweval/web/report.html，评测台和报告共用这一个文件
```

改了前端一定要 `npm run build` 并把 `src/knoweval/web/report.html` 一起提交，Python 包和 Docker 镜像用的都是它。

本地跑评测台：在一个工作区目录里执行 `knoweval ui`（默认 <http://127.0.0.1:8780>）。测试会在临时目录里运行，不会读你本机的 `.env` 和设置。

## 约定

- **先写测试**：新功能和修 bug 都先写一个会失败的测试，再改代码。
- **失败也是结果**：被测系统超时、报错都要如实记录并计入分母，不能从统计里悄悄消失。
- **被测系统是黑盒**：只通过适配器的 `ingest` / `ask` 打交道，不读它的数据库和内部分块。
- **出题不用被测系统的解析器**：参考解析独立于 MinerU、DeepDOC 等，免得题目偏向某个系统。
- **密钥不落盘**：API Key 只从环境变量读，写进任何配置或结果文件前都要去掉。
- 界面文案和注释用中文，说清楚「为什么」。

## 提交信息

```
<类型>: <一句话说明>

<可选的详细说明>
```

类型：`feat`、`fix`、`refactor`、`docs`、`test`、`chore`、`perf`、`ci`。

## 数据集

贡献文档和题目前请确认许可：可以公开分发的才进仓库；版权不允许分发的（比如上市公司年报），只提供下载清单和脚本，在 `manifest.json` 里标 `"redistribute": false`。题目格式见 [评测规范](docs/spec/eval-spec-v0.1.md) 和 [数据集设计](docs/spec/dataset-design-v0.1.md)。

## 许可

提交即表示你同意以 [Apache License 2.0](LICENSE) 发布你的贡献。
