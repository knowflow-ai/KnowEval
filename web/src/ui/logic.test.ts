import { describe, expect, it, vi } from "vitest";

import type { BuildSummary } from "./api";
import { configSummary, currentRound, findQuote, formatJson, parseJsonObject, parseRoute, progressPercent, routeHash, testsetOptions, testsetParam, locateQuote } from "./logic";
import { fmtTime } from "../data";

describe("路由", () => {
  it("解析与生成互逆", () => {
    for (const hash of ["#/", "#/new", "#/evals/ev-1a2b3c", "#/systems", "#/datasets", "#/settings", "#/datasets/datasets%2Fcore-md", "#/builds/bd-1a2b3c"]) {
      expect(routeHash(parseRoute(hash))).toBe(hash);
    }
  });
  it("未知路径回首页", () => {
    expect(parseRoute("#/whatever")).toEqual({ page: "evals" });
    expect(parseRoute("")).toEqual({ page: "evals" });
  });
});

describe("配置摘要", () => {
  it("原样列出接口参数，嵌套用点号", () => {
    expect(configSummary({ dataset: { chunk_method: "smart", parser_config: { layout_recognize: "MinerU" } }, chat: {} }))
      .toBe("chunk_method=smart · parser_config.layout_recognize=MinerU");
  });
  it("用了自己的 API Key 时列出环境变量名", () => {
    expect(configSummary({ dataset: {}, chat: { top_k: 10 }, api_key_env: "APP_B_KEY" })).toBe("chat.top_k=10 · API Key=APP_B_KEY");
  });
  it("已有助手：设置以系统里的助手为准", () => {
    expect(configSummary({ dataset: {}, chat: {}, existing_chat: { id: "c1", name: "线上助手" } }))
      .toBe("已有助手：线上助手（设置以系统里的助手为准，开跑时记下配置快照）");
  });
  it("参数对照：写明以哪个助手为基准、改了哪几项", () => {
    expect(configSummary({ dataset: {}, chat: {}, existing_chat: { id: "c1", name: "TEST" }, chat_overrides: { prompt: { sag_search_strategy: "atomic" } } }))
      .toBe("以助手 TEST 为基准，改了 prompt.sag_search_strategy=atomic（临时助手，问完删除）");
  });
  it("没有参数时说明用系统默认", () => {
    expect(configSummary({ dataset: {}, chat: {} })).toBe("使用系统默认设置");
  });
});

describe("进度", () => {
  const base = { ingest: { manual: false, total: 3, ok: 3, failed: [] }, total: 10, summary: null };
  it("还没建库上传时给一点起步", () => {
    expect(progressPercent({ ...base, ingest: null, phase: "ingest", rounds: [{ repeat: 1, asked: 0, judged: 0 }] })).toBe(5);
  });
  it("建库后按判完的题数推进，两遍各占一半", () => {
    const p = { ...base, phase: "ask" as const, rounds: [{ repeat: 1, asked: 10, judged: 10 }, { repeat: 2, asked: 5, judged: 0 }] };
    expect(progressPercent(p)).toBe(60);
    expect(currentRound(p).repeat).toBe(2);
  });
  it("完成就是 100", () => {
    expect(progressPercent({ ...base, phase: "done", rounds: [{ repeat: 1, asked: 10, judged: 10 }] })).toBe(100);
  });
});

describe("JSON 参数", () => {
  it("空串是空对象，非对象报错", () => {
    expect(parseJsonObject("  ")).toEqual({});
    expect(parseJsonObject('{"a": 1}')).toEqual({ a: 1 });
    expect(() => parseJsonObject("[1]")).toThrow("JSON 对象");
  });
});

describe("证据定位", () => {
  const text = "第一段。\n\n| 项目 | 2024 年 |\n|---|---|\n| 营业收入 | 189,163 |\n\n结尾。";
  it("原样找到", () => {
    expect(findQuote(text, "第一段")).toEqual([0, 3]);
  });
  it("忽略空白也能找到（表格里的竖线与空格）", () => {
    const r = findQuote(text, "|营业收入|189,163|")!;
    expect(text.slice(r[0], r[1]).replace(/\s+/g, "")).toBe("|营业收入|189,163|");
  });
  it("找不到返回 null", () => {
    expect(findQuote(text, "完全不存在的句子")).toBeNull();
    expect(findQuote(text, "")).toBeNull();
  });
  it("数据集路径作为一段编码进 hash", () => {
    expect(parseRoute("#/datasets/datasets%2Fcore-md")).toEqual({ page: "dataset", path: "datasets/core-md" });
  });
});

describe("测试集来源说明", async () => {
  const { sourceText } = await import("./pages/BuildDetailPage");
  const base: Omit<BuildSummary, "source"> = { id: "bd-1", name: "x", version: "0.1", mix: {}, status: "ready", error: null, skipped: [],
    created_at: "", docs: 0, candidates: 0, passed: 0, rejected_auto: 0, deleted: 0, confirmed: 0, pending_verify: 0 };
  it("上传与多个知识库", () => {
    expect(sourceText({ ...base, source: { kind: "upload", files: 3 } })).toBe("上传 3 份");
    // 还没取完文档：用建测试集时记下的库名，没有就显示库 id
    expect(sourceText({ ...base, source: { kind: "kb", system: "KF", kb_ids: ["a", "b"], kb_names: { a: "库A" } } })).toBe("取自 KF：库A、b");
    expect(sourceText({ ...base, source: { kind: "kb", system: "KF", kb_ids: ["a", "b"],
      kbs: [{ id: "a", name: "库A", documents: 2, kept: 2 }, { id: "b", name: "库B", documents: 3, kept: 2 }] } }))
      .toBe("取自 KF：库A（2/2）、库B（2/3）");
  });
});

describe("参评项的配置摘要", () => {
  it("已有助手写明以它为准；参数对照写明改了哪几项", () => {
    expect(configSummary({ dataset: {}, chat: {}, existing_chat: { id: "c1", name: "TEST" } })).toContain("已有助手：TEST");
    expect(configSummary({ dataset: {}, chat: {}, existing_chat: { id: "c1", name: "TEST" }, chat_overrides: { prompt: { top_n: 16 } } }))
      .toContain("改了 prompt.top_n=16");
  });
});

describe("测试集列表", () => {
  const ds = (path: string, questions = 10) => ({ path, ref: path.split("/")[1], name: path.split("/")[1].split("@")[0], questions, docs: 3, source_kb: null }) as any;
  const bd = (id: string, status: string, passed: number) =>
    ({ id, name: id, version: "0.1", status, passed, docs: id === "b" ? 0 : 2, source_kb: null }) as any;

  it("评测台出的在前；出完题、有题才能评测", () => {
    const opts = testsetOptions([ds("datasets/core-md")], [bd("a", "ready", 5), bd("b", "generating", 0)]);
    expect(opts.map((o) => o.key)).toEqual(["build:a", "build:b", "dataset:datasets/core-md"]);
    expect(opts.map((o) => o.usable)).toEqual([true, false, true]);
    expect(opts.map((o) => o.label)).toEqual(["a", "b", "core-md"]);
    expect(opts.map((o) => o.docs)).toEqual([2, 0, 3]);   // 完整测试要建库，只列带文档的   // 评测台里改题不升版本，名字不带版本号
  });

  it("评测参数与预选路由", () => {
    expect(testsetParam("build:bd-1")).toEqual({ build_id: "bd-1" });
    expect(testsetParam("dataset:datasets/core-md")).toEqual({ dataset_path: "datasets/core-md" });
    const r = { page: "new" as const, testset: "dataset:datasets/产品@0.1" };
    expect(parseRoute(routeHash(r))).toEqual(r);
  });
});

describe("参数 JSON 排版", () => {
  it("一行一个参数，嵌套的缩进", () => {
    expect(formatJson('{"chunk_method": "smart", "parser_config": {"layout_recognize": "MinerU"}}'))
      .toBe('{\n  "chunk_method": "smart",\n  "parser_config": {\n    "layout_recognize": "MinerU"\n  }\n}');
  });
  it("空的和写错的原样留着，交给保存时校验", () => {
    expect(formatJson("")).toBe("");
    expect(formatJson('{"a": ')).toBe('{"a": ');
  });
});

describe("locateQuote", () => {
  const text = "--- 第 1 页 ---\n封面\n\n--- 第 2 页 ---\n样本：无付费的微短剧用户N=476，于2024年5月通过艾瑞调研获得。\n其他内容";

  it("整段找得到就高亮整段", () => {
    const hit = locateQuote(text, "样本：无付费的微短剧用户N=476", 2)!;
    expect(text.slice(hit.start, hit.end)).toBe("样本：无付费的微短剧用户N=476");
    expect(hit.note).toBeUndefined();
  });

  it("系统引用的分块被改过时，退到其中最长的一句", () => {
    const hit = locateQuote(text, "## 付费明细\n于2024年5月通过艾瑞调研获得。", 2)!;
    expect(text.slice(hit.start, hit.end)).toBe("于2024年5月通过艾瑞调研获得");
    expect(hit.note).toContain("其中一句");
  });

  it("一句都找不到就定位到引用的那一页", () => {
    const hit = locateQuote(text, "完全不相干的一段话，原文里没有", 2)!;
    expect(text.slice(hit.start, hit.end)).toBe("--- 第 2 页 ---");
    expect(hit.note).toContain("第 2 页");
  });

  it("没有页码也找不到就是 null", () => {
    expect(locateQuote(text, "完全不相干的一段话，原文里没有", null)).toBeNull();
  });
});

describe("时间显示", () => {
  it("后端存的是 UTC，页面按浏览器本地时区显示到分钟", () => {
    vi.stubEnv("TZ", "Asia/Shanghai");
    try {
      expect(fmtTime("2026-10-09T05:31:12.5+00:00")).toBe("2026-10-09 13:31");
      expect(fmtTime("2026-10-08T23:59:00+00:00")).toBe("2026-10-09 07:59");   // 跨天
    } finally {
      vi.unstubAllEnvs();
    }
    expect(fmtTime("不是时间")).toBe("不是时间");
  });
});
