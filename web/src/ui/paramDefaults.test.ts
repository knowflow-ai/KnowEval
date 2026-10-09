import { describe, expect, it } from "vitest";

import { configFormInitial, defaultChatParams, defaultDatasetParams } from "./paramDefaults";

describe("建助手参数的默认值", () => {
  it("KnowFlow：把不填时实际生效的参数都写出来，含 ColPali、SAG 策略", () => {
    const c = defaultChatParams("knowflow") as any;
    // 不传 llm 时 KnowFlow 助手表的默认值，max_tokens 只有 512，长回答会被截断
    expect(c.llm).toEqual({ model_name: null, temperature: 0.1, top_p: 0.3, presence_penalty: 0.4, frequency_penalty: 0.7, max_tokens: 512 });
    expect(c.prompt).toMatchObject({ similarity_threshold: 0.2, vector_similarity_weight: 0.3, top_n: 6, top_k: 1024, rerank_model: "",
      use_colpali: false, sag_search_strategy: "multi", use_kg: false, reasoning: false, keyword: false, show_quote: true });
    expect(c.prompt.prompt).toContain("{knowledge}");
    expect(c.vlm_top_n).toBe(3);
  });
  it("RAGFlow 没有 ColPali 和 SAG", () => {
    const c = defaultChatParams("ragflow") as any;
    expect(c.prompt.use_colpali).toBeUndefined();
    expect(c.prompt.sag_search_strategy).toBeUndefined();
    expect(c.vlm_top_n).toBeUndefined();
  });
  it("自研系统没有默认参数", () => {
    expect(defaultChatParams("http")).toEqual({});
  });
});

describe("建库参数的默认值", () => {
  it("KnowFlow 写明嵌入模型、分块与增强开关，含 SAG", () => {
    const d = defaultDatasetParams("knowflow") as any;
    expect(d).toMatchObject({ chunk_method: "smart", embedding_model: "BAAI/bge-m3@SILICONFLOW" });
    expect(d.parser_config).toMatchObject({ layout_recognize: "MinerU", chunk_token_num: 512, auto_keywords: 0, auto_questions: 0,
      raptor: { use_raptor: false }, graphrag: { use_graphrag: false }, sag: { use_sag: false } });
  });
  it("RAGFlow 没有 SAG", () => {
    expect((defaultDatasetParams("ragflow") as any).parser_config.sag).toBeUndefined();
  });
});

describe("配置表单的初始值", () => {
  const kf = { adapter: "knowflow" as const };
  it("新建：系统页只建建库配置（测已有助手在新建评测里直接选），建库、建助手参数都预填默认值", () => {
    const v = configFormInitial(kf, null);
    expect(JSON.parse(v.chat)).toEqual(defaultChatParams("knowflow"));
    expect(JSON.parse(v.dataset)).toEqual(defaultDatasetParams("knowflow"));
    expect(v.chat).toContain('\n  "llm": {');   // 一行一个参数
  });
  it("修改：存的参数原样显示；建助手参数为空（等于全用默认）时显示默认值", () => {
    const saved = { id: "c", name: "smart", dataset: { chunk_method: "smart" }, chat: {} };
    const v = configFormInitial(kf, saved);
    expect(JSON.parse(v.dataset)).toEqual({ chunk_method: "smart" });
    expect(JSON.parse(v.chat)).toEqual(defaultChatParams("knowflow"));
    const own = configFormInitial(kf, { ...saved, chat: { prompt: { top_n: 3 } } });
    expect(JSON.parse(own.chat)).toEqual({ prompt: { top_n: 3 } });
  });
  it("自研系统：没有默认参数，参数框留空", () => {
    expect(configFormInitial({ adapter: "http" }, null)).toMatchObject({ chat: "", dataset: "" });
  });
});
