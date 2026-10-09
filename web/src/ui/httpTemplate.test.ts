import { describe, expect, it } from "vitest";

import { DEFAULT_HTTP_FORM, EMPTY_HTTP_FORM, checkHttpForm, fromHttpConfig, toHttpConfig, toProbeConfig } from "./httpTemplate";

/** 接上建库、上传接口的自研系统（默认是只接问答接口） */
const AUTO = { ...DEFAULT_HTTP_FORM, mode: "auto" as const };
/** 示例服务那种：自动入库、业务码、正文标记引用 */
const FULL = { ...AUTO, successPath: "$.code", successEquals: "0", errorMessagePath: "$.message",
  statusDone: ["done"], statusFailed: ["failed"], citationMode: "marker" as const, contextsPath: "$.data.chunks[*]" };

describe("自研系统接入表单", () => {
  it("默认值拼出自动入库的完整配置", () => {
    const cfg = toHttpConfig(AUTO) as any;
    expect(cfg.headers).toEqual({ Authorization: "Bearer {{api_key}}" });
    expect(cfg.ingest.create_kb).toEqual({ method: "POST", path: "/api/kb", body: { name: "{{kb_name}}" }, id: "$.data.id" });
    expect(cfg.ingest.upload).toEqual({ path: "/api/kb/{{kb_id}}/documents", file_field: "file", doc_id: "$.data.id" });
    expect(cfg.ingest.status.done).toEqual(["done", "completed"]);
    expect(cfg.ask.answer).toBe("$.data.answer");
    expect(cfg.kb_id).toBeUndefined();
    expect(cfg.success).toBeUndefined();
  });

  it("手工入库只带知识库编号，不带入库步骤", () => {
    const cfg = toHttpConfig({ ...DEFAULT_HTTP_FORM, mode: "manual", kbId: " kb-42 " }) as any;
    expect(cfg.kb_id).toBe("kb-42");
    expect(cfg.ingest).toBeUndefined();
    expect(() => toHttpConfig({ ...DEFAULT_HTTP_FORM, mode: "manual", kbId: "" })).toThrow("知识库编号");   // 提问里用到了 {{kb_id}}
    // 只接问答接口、提问里不需要库编号：不用填
    const askOnly = toHttpConfig({ ...DEFAULT_HTTP_FORM, kbId: "", askBody: '{"query": "{{question}}"}' }) as any;
    expect(askOnly.kb_id).toBe("default");
    expect(askOnly.ingest).toBeUndefined();
  });

  it("业务码按类型还原，状态轮询需要文档编号", () => {
    const cfg = toHttpConfig({ ...AUTO, successPath: "$.code", successEquals: "0" }) as any;
    expect(cfg.success).toEqual({ path: "$.code", equals: 0 });
    expect(() => toHttpConfig({ ...AUTO, uploadDocIdPath: "" })).toThrow("文档编号");
    const noStatus = toHttpConfig({ ...AUTO, statusEnabled: false, uploadDocIdPath: "" }) as any;
    expect(noStatus.ingest.status).toBeUndefined();
  });

  it("引用两种取法", () => {
    const list = toHttpConfig({ ...AUTO, citationMode: "list", citationPage: "$.page" }) as any;
    expect(list.ask.citations).toBe("$.data.references[*]");
    expect(list.ask.citation_fields).toEqual({ doc: "$.document_name", text: "$.content", page: "$.page" });
    expect(() => toHttpConfig({ ...AUTO, citationMode: "marker" })).toThrow("检索片段");
    const marker = toHttpConfig({ ...AUTO, citationMode: "marker", contextsPath: "$.data.chunks[*]", markerBase: 1 }) as any;
    expect(marker.ask.citation_marker).toBe("\\s*\\[ID:(\\d+)\\]");
    expect(marker.ask.marker_index_base).toBe(1);
    expect(marker.ask.contexts).toBe("$.data.chunks[*]");
  });

  it("JSON 写错时指出是哪一项", () => {
    expect(() => toHttpConfig({ ...DEFAULT_HTTP_FORM, askBody: "{query: x}" })).toThrow("提问 · 请求体");
  });
});

describe("保存前检查", () => {
  it("提问接口要贴过 curl、点过答案；开了自动入库，建库和上传也要", () => {
    expect(checkHttpForm(EMPTY_HTTP_FORM)).toContain("提问接口的 curl");
    expect(checkHttpForm({ ...EMPTY_HTTP_FORM, askPath: "/ask" })).toContain("答案");
    const ask = { ...EMPTY_HTTP_FORM, askPath: "/ask", askAnswerPath: "$.a" };
    expect(checkHttpForm(ask)).toBeNull();
    expect(checkHttpForm({ ...ask, mode: "auto" })).toContain("建库接口");
    expect(checkHttpForm({ ...ask, mode: "auto", createPath: "/kb", createIdPath: "$.id", uploadPath: "/up" })).toBeNull();
  });
});

describe("试调配置", () => {
  it("表单没填完也能拼出各步（手工入库没填编号、状态接口关着）", () => {
    const cfg = toProbeConfig({ ...DEFAULT_HTTP_FORM, mode: "manual", kbId: "", statusEnabled: false, uploadDocIdPath: "" }) as any;
    expect(cfg.ask.path).toBe("/api/chat");
    expect(cfg.ingest.create_kb.path).toBe("/api/kb");
    expect(cfg.ingest.status.method).toBe("GET");
  });
  it("JSON 写错照样指出是哪一项", () => {
    expect(() => toProbeConfig({ ...DEFAULT_HTTP_FORM, createBody: "{x" })).toThrow("新建知识库 · 请求体");
  });
});

describe("从配置还原表单（早先用配置文件接入、没存表单的系统）", () => {
  const roundTrip = (f: typeof DEFAULT_HTTP_FORM) => toHttpConfig(fromHttpConfig(toHttpConfig(f) as any));
  it("自动入库 + 正文标记引用：还原后再生成的配置不变", () => {
    expect(roundTrip(FULL)).toEqual(toHttpConfig(FULL));
  });
  it("手工入库 + 引用列表", () => {
    const f = { ...DEFAULT_HTTP_FORM, mode: "manual" as const, kbId: "kb-1", citationMode: "list" as const, citationPage: "$.page", contextsPath: "$.data.ctx[*]" };
    expect(roundTrip(f)).toEqual(toHttpConfig(f));
  });
  it("以前用 OpenAI 兼容预设接入的，还原成普通的提问请求", () => {
    const old = { kb_id: "openai-compatible", ask: { path: "/v1/chat/completions", answer: "$.choices[0].message.content",
      body: { model: "m1", messages: [{ role: "user", content: "{{question}}" }], stream: false } } };
    const back = fromHttpConfig(old);
    expect(JSON.parse(back.askBody).model).toBe("m1");
    expect((toHttpConfig(back) as any).ask).toEqual({ method: "POST", ...old.ask });
  });
  it("configs/http.example.yaml 那种手写配置也能还原", () => {
    const back = fromHttpConfig({ kb_id: "your-kb-id", ask: { path: "/api/chat", body: { query: "{{question}}" }, answer: "$.data.answer" } });
    expect(back.mode).toBe("manual");
    expect(back.kbId).toBe("your-kb-id");
    expect(back.authHeader).toBe("");
    expect(back.askAnswerPath).toBe("$.data.answer");
  });
});
