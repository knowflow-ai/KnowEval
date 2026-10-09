// 自研系统接入表单 ↔ 通用 HTTP 适配器配置（knoweval/adapters/http.py 读的 http 字段）
// 页面上每个接口贴一条 curl（curl.ts 解析成这些字段），再试调点选取值；这里把它拼成适配器要的结构。
// 纯函数，httpTemplate.test.ts 覆盖。

import { parseJsonObject } from "./logic";

export type IngestMode = "auto" | "manual";
export type CitationMode = "none" | "list" | "marker";

export interface HttpForm {
  /** curl 里的示例问题，试问时默认用它 */
  askSample: string;
  errorMessagePath: string;
  mode: IngestMode;
  authHeader: string;
  authValue: string;
  /** 认证头之外还要带的请求头（JSON 对象），如租户编号 */
  extraHeaders: string;
  successPath: string;
  successEquals: string;
  kbId: string;
  createMethod: string;
  createPath: string;
  createBody: string;
  createIdPath: string;
  uploadPath: string;
  uploadFileField: string;
  uploadForm: string;
  uploadDocIdPath: string;
  statusEnabled: boolean;
  statusPath: string;
  statusStatePath: string;
  statusDone: string[];
  statusFailed: string[];
  statusErrorPath: string;
  askMethod: string;
  askPath: string;
  askBody: string;
  askAnswerPath: string;
  citationMode: CitationMode;
  citationsPath: string;
  citationDoc: string;
  citationPage: string;
  citationText: string;
  markerRegex: string;
  markerBase: number;
  contextsPath: string;
  contextDoc: string;
  contextText: string;
}

export const DEFAULT_HTTP_FORM: HttpForm = {
  askSample: "",
  errorMessagePath: "",
  mode: "manual",
  authHeader: "Authorization",
  authValue: "Bearer {{api_key}}",
  extraHeaders: "",
  successPath: "",
  successEquals: "0",
  kbId: "",
  createMethod: "POST",
  createPath: "/api/kb",
  createBody: '{\n  "name": "{{kb_name}}"\n}',
  createIdPath: "$.data.id",
  uploadPath: "/api/kb/{{kb_id}}/documents",
  uploadFileField: "file",
  uploadForm: "",
  uploadDocIdPath: "$.data.id",
  statusEnabled: true,
  statusPath: "/api/kb/{{kb_id}}/documents/{{doc_id}}",
  statusStatePath: "$.data.status",
  statusDone: ["done", "completed"],
  statusFailed: ["failed", "error"],
  statusErrorPath: "",
  askMethod: "POST",
  askPath: "/api/chat",
  askBody: '{\n  "query": "{{question}}",\n  "kb_id": "{{kb_id}}"\n}',
  askAnswerPath: "$.data.answer",
  citationMode: "none",
  citationsPath: "$.data.references[*]",
  citationDoc: "$.document_name",
  citationPage: "",
  citationText: "$.content",
  markerRegex: "\\s*\\[ID:(\\d+)\\]",
  markerBase: 0,
  contextsPath: "",
  contextDoc: "$.document_name",
  contextText: "$.content",
};

/** 新接入的自研系统：什么都没填，等贴 curl */
export const EMPTY_HTTP_FORM: HttpForm = {
  ...DEFAULT_HTTP_FORM,
  authHeader: "", authValue: "", askPath: "", askBody: "", askAnswerPath: "",
  createPath: "", createBody: "", createIdPath: "", uploadPath: "", uploadDocIdPath: "",
  statusEnabled: false, statusPath: "", statusStatePath: "", citationsPath: "",
};

/** 保存前检查：提问接口必须贴过 curl 并点过答案；开了自动入库，建库和上传也要贴过 */
export function checkHttpForm(f: HttpForm): string | null {
  if (!f.askPath.trim()) return "先粘贴提问接口的 curl";
  if (!f.askAnswerPath.trim()) return "点「试问一题」，在返回结果里点一下答案所在的字段";
  if (f.mode === "auto") {
    if (!f.createPath.trim()) return "开了自动入库：粘贴建库接口的 curl，或关掉自动入库";
    if (!f.createIdPath.trim()) return "试调建库，在返回结果里点一下新库编号";
    if (!f.uploadPath.trim()) return "开了自动入库：粘贴上传接口的 curl";
    if (f.statusEnabled && !f.statusStatePath.trim()) return "试调查进度，在返回结果里点一下状态字段";
  }
  return null;
}

function fields(doc: string, text: string, page?: string): Record<string, string> {
  const out: Record<string, string> = { doc, text };
  if (page) out.page = page;
  return out;
}

function successValue(raw: string): string | number | boolean {
  const t = raw.trim();
  if (t === "true" || t === "false") return t === "true";
  return t !== "" && !Number.isNaN(Number(t)) ? Number(t) : t;
}

/** 表单 → 适配器配置。JSON 输入框写错会抛出带说明的错误，由页面提示 */
export function toHttpConfig(f: HttpForm): Record<string, unknown> {
  const json = (label: string, text: string) => {
    try {
      return parseJsonObject(text);
    } catch (e: any) {
      throw new Error(`「${label}」不是合法的 JSON 对象：${e.message ?? e}`);
    }
  };
  const cfg: Record<string, unknown> = {};
  const headers = { ...json("额外请求头", f.extraHeaders ?? ""), ...(f.authHeader.trim() ? { [f.authHeader.trim()]: f.authValue } : {}) };
  if (Object.keys(headers).length) cfg.headers = headers;
  if (f.successPath.trim()) cfg.success = { path: f.successPath.trim(), equals: successValue(f.successEquals) };
  if (f.errorMessagePath?.trim()) cfg.error_message = f.errorMessagePath.trim();

  if (f.mode === "manual") {
    // 只有问答接口：编号只在提问请求里写了 {{kb_id}} 时才用得上
    if (!f.kbId.trim() && `${f.askPath}${f.askBody}`.includes("{{kb_id}}")) {
      throw new Error("提问请求里用到了 {{kb_id}}，要填知识库编号");
    }
    cfg.kb_id = f.kbId.trim() || "default";
  } else {
    const ingest: Record<string, unknown> = {
      create_kb: { method: f.createMethod, path: f.createPath, body: json("新建知识库 · 请求体", f.createBody), id: f.createIdPath },
      upload: {
        path: f.uploadPath, file_field: f.uploadFileField || "file",
        ...(f.uploadForm.trim() ? { form: json("上传文档 · 附带表单字段", f.uploadForm) } : {}),
        ...(f.uploadDocIdPath.trim() ? { doc_id: f.uploadDocIdPath.trim() } : {}),
      },
    };
    if (f.statusEnabled) {
      if (!f.uploadDocIdPath.trim()) throw new Error("要查解析进度，上传接口必须能返回文档编号（填「文档编号在返回结果里的位置」）");
      ingest.status = {
        path: f.statusPath, state: f.statusStatePath, done: f.statusDone, failed: f.statusFailed,
        ...(f.statusErrorPath.trim() ? { error: f.statusErrorPath.trim() } : {}),
      };
    }
    cfg.ingest = ingest;
  }

  const ask: Record<string, unknown> = {
    method: f.askMethod, path: f.askPath, body: json("提问 · 请求体", f.askBody), answer: f.askAnswerPath,
  };
  if (f.citationMode === "list") {
    ask.citations = f.citationsPath;
    ask.citation_fields = fields(f.citationDoc, f.citationText, f.citationPage);
  }
  if (f.citationMode === "marker") {
    if (!f.contextsPath.trim()) throw new Error("按正文标记取引用时，必须填检索片段列表的位置，标记里的编号要对回这些片段");
    ask.citation_marker = f.markerRegex;
    ask.marker_index_base = f.markerBase;
  }
  if (f.contextsPath.trim()) {
    ask.contexts = f.contextsPath.trim();
    ask.context_fields = fields(f.contextDoc, f.contextText);
  }
  cfg.ask = ask;
  return cfg;
}

/** 试调用的配置：不要求整张表填完（比如还没想好入库方式也能先试问），只把各步按当前填写拼出来 */
export function toProbeConfig(f: HttpForm): Record<string, unknown> {
  const json = (label: string, text: string) => {
    try {
      return parseJsonObject(text);
    } catch (e: any) {
      throw new Error(`「${label}」不是合法的 JSON 对象：${e.message ?? e}`);
    }
  };
  const cfg: Record<string, unknown> = {};
  const headers = { ...json("额外请求头", f.extraHeaders ?? ""), ...(f.authHeader.trim() ? { [f.authHeader.trim()]: f.authValue } : {}) };
  if (Object.keys(headers).length) cfg.headers = headers;
  cfg.ingest = {
    create_kb: { method: f.createMethod, path: f.createPath, body: json("新建知识库 · 请求体", f.createBody) },
    upload: { path: f.uploadPath, file_field: f.uploadFileField || "file", ...(f.uploadForm.trim() ? { form: json("上传文档 · 附带表单字段", f.uploadForm) } : {}) },
    status: { method: "GET", path: f.statusPath },
  };
  cfg.ask = { method: f.askMethod, path: f.askPath, body: json("提问 · 请求体", f.askBody) };
  return cfg;
}

const str = (v: unknown, fallback = "") => (typeof v === "string" ? v : fallback);
const jsonText = (v: unknown) => (v && typeof v === "object" && Object.keys(v as object).length ? JSON.stringify(v, null, 2) : "");

/** 适配器配置 → 表单。只用于没存表单原始填写的系统（早先用配置文件接入的）；尽量还原，认不出的留默认值 */
export function fromHttpConfig(cfg: Record<string, any>): HttpForm {
  const f: HttpForm = { ...DEFAULT_HTTP_FORM };
  const headers = cfg.headers && typeof cfg.headers === "object" ? Object.entries(cfg.headers as Record<string, string>) : [];
  const auth = headers.find(([k]) => /^(authorization|x-api-key|api-key|apikey|x-auth-token|x-token|token|access-token)$/i.test(k)) ?? headers[0];
  if (auth) [f.authHeader, f.authValue] = [auth[0], String(auth[1])];
  else [f.authHeader, f.authValue] = ["", ""];
  const extra = headers.filter((h) => h !== auth);
  f.extraHeaders = extra.length ? JSON.stringify(Object.fromEntries(extra), null, 2) : "";
  if (cfg.success?.path) [f.successPath, f.successEquals] = [cfg.success.path, String(cfg.success.equals ?? "")];
  f.errorMessagePath = str(cfg.error_message);
  const ask = cfg.ask ?? {};
  const body = ask.body ?? {};
  if (cfg.ingest) {
    const { create_kb: c = {}, upload: u = {}, status: st } = cfg.ingest;
    Object.assign(f, {
      mode: "auto",
      createMethod: str(c.method, "POST"), createPath: str(c.path), createBody: jsonText(c.body), createIdPath: str(c.id),
      uploadPath: str(u.path), uploadFileField: str(u.file_field, "file"), uploadForm: jsonText(u.form), uploadDocIdPath: str(u.doc_id),
      statusEnabled: !!st,
    });
    if (st) {
      Object.assign(f, { statusPath: str(st.path), statusStatePath: str(st.state),
        statusDone: [].concat(st.done ?? []).map(String), statusFailed: [].concat(st.failed ?? []).map(String), statusErrorPath: str(st.error) });
    }
  } else {
    Object.assign(f, { mode: "manual", kbId: str(cfg.kb_id) });
  }
  Object.assign(f, { askMethod: str(ask.method, "POST"), askPath: str(ask.path), askBody: jsonText(ask.body), askAnswerPath: str(ask.answer) });
  if (ask.citation_marker) {
    Object.assign(f, { citationMode: "marker", markerRegex: ask.citation_marker, markerBase: Number(ask.marker_index_base ?? 0) });
  } else if (ask.citations) {
    const cf = ask.citation_fields ?? {};
    Object.assign(f, { citationMode: "list", citationsPath: ask.citations, citationDoc: str(cf.doc, "$.doc"), citationText: str(cf.text, "$.text"), citationPage: str(cf.page) });
  } else {
    f.citationMode = "none";
  }
  if (ask.contexts) {
    const xf = ask.context_fields ?? {};
    Object.assign(f, { contextsPath: ask.contexts, contextDoc: str(xf.doc, "$.doc"), contextText: str(xf.text, "$.text") });
  } else {
    f.contextsPath = "";
  }
  return f;
}
