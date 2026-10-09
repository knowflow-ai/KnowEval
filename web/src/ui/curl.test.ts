import { describe, expect, it } from "vitest";

import { importCurl, parseCurl, shellWords, splitUrl } from "./curl";
import { DEFAULT_HTTP_FORM, fromHttpConfig, toHttpConfig } from "./httpTemplate";

describe("解析 curl", () => {
  it("多行续行、单双引号", () => {
    const c = parseCurl(`curl -X POST https://rag.example.com/api/chat \\
      -H "Authorization: Bearer sk-abc" -H 'Content-Type: application/json' \\
      -d '{"query": "年假有几天", "stream": false}'`);
    expect(c.method).toBe("POST");
    expect(c.url).toBe("https://rag.example.com/api/chat");
    expect(c.headers).toEqual([["Authorization", "Bearer sk-abc"], ["Content-Type", "application/json"]]);
    expect(JSON.parse(c.body!)).toEqual({ query: "年假有几天", stream: false });
  });

  it("浏览器「复制为 cURL」：--data-raw、$'...' 转义、没写 -X 时有请求体就是 POST", () => {
    const c = parseCurl(`curl 'http://10.0.0.8:8000/v1/ask' -H 'accept: */*' -H 'sec-ch-ua: "Chromium"' --data-raw $'{"q":"it\\'s ok"}' --compressed`);
    expect(c.method).toBe("POST");
    expect(JSON.parse(c.body!)).toEqual({ q: "it's ok" });
  });

  it("-F 上传与 --json", () => {
    const up = parseCurl(`curl -X POST http://x/api/kb/123/docs -H "X-API-Key: k1" -F "file=@/tmp/a.pdf" -F "auto_parse=true"`);
    expect(up.form).toEqual([["file", "@/tmp/a.pdf"], ["auto_parse", "true"]]);
    const j = parseCurl(`curl --json '{"a":1}' http://x/api`);
    expect(j.headers).toContainEqual(["Content-Type", "application/json"]);
    expect(j.method).toBe("POST");
  });

  it("不是 curl 或没地址时说清楚", () => {
    expect(() => parseCurl("wget http://x")).toThrow("没认出 curl 命令");
    expect(() => parseCurl("curl -X POST")).toThrow("没有请求地址");
  });

  it("切词保留引号里的空格", () => {
    expect(shellWords(`curl -H "A: b c" 'd e'`)).toEqual(["curl", "-H", "A: b c", "d e"]);
  });
});

describe("curl 映射成表单", () => {
  const ask = `curl -X POST https://rag.example.com/api/chat -H "Authorization: Bearer sk-abc" -H "X-Tenant: t1" -H "User-Agent: PostmanRuntime" -d '{"query": "年假有几天", "kb": "hr", "stream": true}'`;

  it("提问：密钥换成占位符、按示例问题定位题目、stream 改成 false、拆出服务地址", () => {
    const r = importCurl(ask, "ask", { sample: "年假有几天" });
    expect(r.baseUrl).toBe("https://rag.example.com");
    expect(r.fields).toMatchObject({ askMethod: "POST", askPath: "/api/chat", authHeader: "Authorization", authValue: "Bearer {{api_key}}" });
    expect(JSON.parse(r.fields.askBody!)).toEqual({ query: "{{question}}", kb: "hr", stream: false });
    expect(JSON.parse(r.fields.extraHeaders!)).toEqual({ "X-Tenant": "t1" });   // UA 这类噪音不带
    expect(JSON.stringify(r)).not.toContain("sk-abc");                          // 密钥原文不进表单
  });

  it("不给示例问题时按常见字段名猜；对话格式取最后一条", () => {
    const r = importCurl(`curl http://x/v1/chat -d '{"messages":[{"role":"system","content":"你是助手"},{"role":"user","content":"你好"}]}'`, "ask");
    expect(JSON.parse(r.fields.askBody!).messages[1].content).toBe("{{question}}");
    expect(JSON.parse(r.fields.askBody!).messages[0].content).toBe("你是助手");
    const miss = importCurl(`curl http://x/v1/chat -d '{"foo":"bar"}'`, "ask");
    expect(miss.warnings.join("")).toContain("没认出问题放在哪个字段");
  });

  it("服务地址已填且是前缀时沿用，接口路径只取剩下的部分", () => {
    expect(splitUrl("https://a.com/rag/api/chat?x=1", "https://a.com/rag")).toEqual({ baseUrl: "https://a.com/rag", path: "/api/chat?x=1" });
    expect(splitUrl("https://a.com/rag/api/chat", "https://b.com")).toEqual({ baseUrl: "https://a.com", path: "/rag/api/chat" });
  });

  it("建库、上传、查进度：库名、文件字段、编号换成占位符", () => {
    const c = importCurl(`curl -X POST http://x/api/kb -H "X-API-Key: k1" -d '{"name":"测试库","lang":"zh"}'`, "create_kb");
    expect(JSON.parse(c.fields.createBody!)).toEqual({ name: "{{kb_name}}", lang: "zh" });
    expect(c.fields.authValue).toBe("{{api_key}}");
    const u = importCurl(`curl -X POST http://x/api/kb/kb-777/docs -F "file=@a.pdf" -F "auto_parse=true"`, "upload", { kbId: "kb-777" });
    expect(u.fields).toMatchObject({ uploadPath: "/api/kb/{{kb_id}}/docs", uploadFileField: "file", uploadForm: '{\n  "auto_parse": "true"\n}' });
    const s = importCurl(`curl http://x/api/kb/kb-777/docs/d-9`, "status", { kbId: "kb-777", docId: "d-9" });
    expect(s.fields.statusPath).toBe("/api/kb/{{kb_id}}/docs/{{doc_id}}");
  });

  it("额外请求头进配置，回填时还原", () => {
    const form = { ...DEFAULT_HTTP_FORM, mode: "manual" as const, kbId: "k", extraHeaders: '{"X-Tenant":"t1"}' };
    const cfg = toHttpConfig(form) as { headers: Record<string, string> };
    expect(cfg.headers).toEqual({ Authorization: "Bearer {{api_key}}", "X-Tenant": "t1" });
    expect(JSON.parse(fromHttpConfig(cfg).extraHeaders)).toEqual({ "X-Tenant": "t1" });
  });
});
