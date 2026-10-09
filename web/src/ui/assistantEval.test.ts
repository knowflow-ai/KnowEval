import { describe, expect, it } from "vitest";

import { buildPicks, diffParams, sharedKbs, splitChats, testsetsForAssistants } from "./assistantEval";
import type { TestsetOption } from "./logic";

const chat = (id: string, kbs: string[]) => ({ id, name: id, model: "", kbs: kbs.map((k) => ({ id: k, name: `库${k}` })) });
const ts = (key: string, systemId: string | null, kbs: string[], usable = true): TestsetOption => ({
  key, kind: "build", label: key, questions: 10, docs: 1, usable,
  source_kb: systemId ? { system_id: systemId, system: "KnowFlow", kbs: kbs.map((k) => ({ id: k, name: k })) } : null,
});

describe("测已有助手：哪些测试集能用", () => {
  it("只列题目出自助手挂的知识库的（同一系统、库都在助手里）；别的测试集文档不在助手里，不列", () => {
    const sets = [ts("a", "s1", ["kb1"]), ts("b", "s1", ["kb1", "kb9"]), ts("c", "s2", ["kb1"]), ts("d", null, []), ts("e", "s1", ["kb2"], false)];
    expect(testsetsForAssistants(sets, "s1", [chat("c1", ["kb1", "kb2"])]).map((t) => t.key)).toEqual(["a"]);   // e 还没出完题
  });
  it("多个助手对比：题目的库要在每个助手里", () => {
    const sets = [ts("a", "s1", ["kb1"]), ts("b", "s1", ["kb2"])];
    expect(testsetsForAssistants(sets, "s1", [chat("c1", ["kb1", "kb2"]), chat("c2", ["kb1"])]).map((t) => t.key)).toEqual(["a"]);
  });
  it("还没选助手时没有匹配", () => {
    expect(testsetsForAssistants([ts("a", "s1", ["kb1"])], "s1", [])).toEqual([]);
  });
});

describe("从助手的知识库出题", () => {
  it("选的助手挂的知识库一样才能共用一套题", () => {
    expect(sharedKbs([chat("c1", ["kb1", "kb2"]), chat("c2", ["kb2", "kb1"])])?.map((k) => k.id)).toEqual(["kb1", "kb2"]);
    expect(sharedKbs([chat("c1", ["kb1"]), chat("c2", ["kb2"])])).toBeNull();
    expect(sharedKbs([chat("c1", [])])).toBeNull();
    expect(sharedKbs([])).toBeNull();
  });
});

describe("助手列表", () => {
  const list = [chat("销售助手", ["kb1"]), chat("knoweval-demo-1", ["kb2"]), chat("售后", ["kb1"]), chat("knoweval-core-2", ["kb3"])];
  it("用户自己的助手在前；评测台建的临时助手（knoweval- 开头）单独放，默认折叠", () => {
    const r = splitChats(list, "");
    expect(r.mine.map((c) => c.id)).toEqual(["销售助手", "售后"]);
    expect(r.temp.map((c) => c.id)).toEqual(["knoweval-demo-1", "knoweval-core-2"]);
  });
  it("按名字或知识库名搜，不分大小写", () => {
    expect(splitChats(list, "售").mine.map((c) => c.id)).toEqual(["销售助手", "售后"]);
    expect(splitChats(list, "库KB3").temp.map((c) => c.id)).toEqual(["knoweval-core-2"]);
    expect(splitChats(list, "DEMO").temp.map((c) => c.id)).toEqual(["knoweval-demo-1"]);
  });
});

describe("参数对照的勾选项", () => {
  const c1 = chat("c1", ["kb1"]);
  it("基准助手在前，每组对照带上名字和要改的参数", () => {
    const picks = buildPicks("s1", [c1], [
      { name: "SAG atomic", json: '{"prompt": {"sag_search_strategy": "atomic"}}' },
      { name: "", json: '{"prompt": {"top_n": 16}}' },
    ]);
    expect(picks).toEqual([
      { system_id: "s1", chat_id: "c1", chat_name: "c1" },
      { system_id: "s1", chat_id: "c1", chat_name: "c1", variant: { name: "SAG atomic", overrides: { prompt: { sag_search_strategy: "atomic" } } } },
      { system_id: "s1", chat_id: "c1", chat_name: "c1", variant: { name: "对照 2", overrides: { prompt: { top_n: 16 } } } },
    ]);
  });
  it("参数写错、为空，或者改了知识库，都说清楚是哪一组", () => {
    expect(() => buildPicks("s1", [c1], [{ name: "坏", json: '{"prompt": ' }])).toThrow(/坏/);
    expect(() => buildPicks("s1", [c1], [{ name: "空", json: "" }])).toThrow(/空/);
    expect(() => buildPicks("s1", [c1], [{ name: "换库", json: '{"dataset_ids": ["x"]}' }])).toThrow(/知识库/);
  });
  it("选了多个助手时不带对照（对照只对一个基准助手）", () => {
    expect(buildPicks("s1", [c1, chat("c2", ["kb1"])], [{ name: "x", json: '{"prompt": {"top_n": 3}}' }])).toHaveLength(2);
  });
});

describe("参数对照：预填基准的全部参数，只把改过的当对照", () => {
  const base = { top_k: 1024, llm: { model_name: "qwen", max_tokens: 512 }, prompt: { top_n: 8, sag_search_strategy: "multi", variables: [{ key: "knowledge" }] } };
  it("只留改过的项；嵌套的只留改过的那一层；数组整体比", () => {
    const edited = { ...base, llm: { model_name: "qwen", max_tokens: 4096 }, prompt: { ...base.prompt, sag_search_strategy: "atomic" } };
    expect(diffParams(edited, base)).toEqual({ llm: { max_tokens: 4096 }, prompt: { sag_search_strategy: "atomic" } });
    expect(diffParams(base, base)).toEqual({});
    expect(diffParams({ ...base, prompt: { ...base.prompt, rerank_model: "bge" } }, base)).toEqual({ prompt: { rerank_model: "bge" } });
  });
  it("组装勾选项时用改动当对照；什么都没改就提示", () => {
    const c1 = chat("c1", ["kb1"]);
    const edited = JSON.stringify({ ...base, prompt: { ...base.prompt, top_n: 16 } });
    const picks = buildPicks("s1", [c1], [{ name: "多召回", json: edited }], base);
    expect((picks[1] as any).variant).toEqual({ name: "多召回", overrides: { prompt: { top_n: 16 } } });
    expect(() => buildPicks("s1", [c1], [{ name: "没改", json: JSON.stringify(base) }], base)).toThrow(/没改/);
  });
});
