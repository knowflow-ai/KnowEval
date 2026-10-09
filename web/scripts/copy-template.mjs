// 把打包出的单文件 HTML 放进 Python 包，pip 安装的用户不需要 Node 就能生成报告
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(here, "../../src/knoweval/web/report.html");
mkdirSync(dirname(target), { recursive: true });
copyFileSync(resolve(here, "../dist/index.html"), target);
console.log(`模板已写入 ${target}`);
