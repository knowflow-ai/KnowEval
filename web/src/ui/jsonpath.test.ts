import { describe, expect, it } from "vitest";

import { evalPath, listPathOf, pathOf, preview } from "./jsonpath";

const resp = {
  code: 0,
  data: {
    answer: "端口是 9400 [ID:0]",
    chunks: [{ document_name: "a.md", content: "片段一" }, { document_name: "b.md", content: "片段二" }],
  },
  choices: [{ message: { content: "OpenAI 风格的回答" } }],
};

describe("取值路径", () => {
  it("点选的字段生成路径", () => {
    expect(pathOf(["data", "answer"])).toBe("$.data.answer");
    expect(pathOf(["choices", 0, "message", "content"])).toBe("$.choices[0].message.content");
    expect(pathOf(["数据"])).toBeNull();
  });

  it("列表类目标点到哪里都取列表本身", () => {
    expect(listPathOf(["data", "chunks", 1, "content"], resp)).toBe("$.data.chunks[*]");
    expect(listPathOf(["data", "chunks"], resp)).toBe("$.data.chunks[*]");
    expect(listPathOf(["data", "answer"], resp)).toBeNull();
  });

  it("按路径取值，与后端子集一致", () => {
    expect(evalPath(resp, "$.data.answer")).toBe("端口是 9400 [ID:0]");
    expect(evalPath(resp, "$.choices[0].message.content")).toBe("OpenAI 风格的回答");
    expect(evalPath(resp, "$.data.chunks[*].document_name")).toEqual(["a.md", "b.md"]);
    expect(evalPath(resp, "$.data.missing")).toBeUndefined();
    expect(evalPath(resp, "data.answer")).toBeUndefined();
    expect(evalPath(resp.data.chunks[0], "$.content")).toBe("片段一");
  });

  it("预览截断", () => {
    expect(preview(undefined)).toBe("（取不到）");
    expect(preview("一".repeat(100), 10)).toBe(`${"一".repeat(10)}…`);
  });
});
