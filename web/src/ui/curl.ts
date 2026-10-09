// 粘贴 curl 填接入表单：解析 curl 命令（Postman、Apifox、浏览器「复制为 cURL」导出的都认），
// 再按是哪个接口（提问、建库、上传、查进度）映射成表单字段。纯函数，curl.test.ts 覆盖。

import type { HttpForm } from "./httpTemplate";

export interface ParsedCurl {
  method: string;
  url: string;
  headers: [string, string][];
  body: string | null;
  form: [string, string][];   // -F 字段；文件字段的值以 @ 开头
}

/** 按 shell 规则切词：单引号、双引号、$'...'、反斜杠续行与转义 */
export function shellWords(text: string): string[] {
  const src = text.replace(/\\\r?\n/g, " ").replace(/\^\r?\n/g, " ");
  const out: string[] = [];
  let cur = "";
  let has = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (/\s/.test(c)) {
      if (has) { out.push(cur); cur = ""; has = false; }
      continue;
    }
    has = true;
    if (c === "'") {
      const end = src.indexOf("'", i + 1);
      cur += src.slice(i + 1, end < 0 ? src.length : end);
      i = end < 0 ? src.length : end;
    } else if (c === "$" && src[i + 1] === "'") {
      let j = i + 2;
      for (; j < src.length && src[j] !== "'"; j++) {
        if (src[j] === "\\" && j + 1 < src.length) {
          const n = src[++j];
          cur += n === "n" ? "\n" : n === "t" ? "\t" : n === "r" ? "\r" : n;
        } else cur += src[j];
      }
      i = j;
    } else if (c === '"') {
      let j = i + 1;
      for (; j < src.length && src[j] !== '"'; j++) {
        if (src[j] === "\\" && j + 1 < src.length && '"\\$`'.includes(src[j + 1])) cur += src[++j];
        else cur += src[j];
      }
      i = j;
    } else if (c === "\\" && i + 1 < src.length) {
      cur += src[++i];
    } else {
      cur += c;
    }
  }
  if (has) out.push(cur);
  return out;
}

const DATA_FLAGS = new Set(["-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json"]);
const VALUE_FLAGS = new Set(["-u", "--user", "-o", "--output", "-A", "--user-agent", "-e", "--referer", "-b", "--cookie",
  "--connect-timeout", "-m", "--max-time", "--proxy", "-x", "--cert", "--key", "--cacert"]);

export function parseCurl(text: string): ParsedCurl {
  const words = shellWords(text.trim());
  if (!words.length || !/^curl(\.exe)?$/i.test(words[0].split("/").pop() ?? "")) {
    throw new Error("没认出 curl 命令：请粘贴以 curl 开头的完整命令");
  }
  let method = "";
  let url = "";
  const headers: [string, string][] = [];
  const data: string[] = [];
  const form: [string, string][] = [];
  let json = false;
  for (let i = 1; i < words.length; i++) {
    const w = words[i];
    const next = () => words[++i] ?? "";
    if (w === "-X" || w === "--request") method = next().toUpperCase();
    else if (w.startsWith("-X") && w.length > 2) method = w.slice(2).toUpperCase();
    else if (w === "-H" || w === "--header") {
      const h = next();
      const at = h.indexOf(":");
      if (at > 0) headers.push([h.slice(0, at).trim(), h.slice(at + 1).trim()]);
    } else if (DATA_FLAGS.has(w)) {
      if (w === "--json") json = true;
      data.push(next());
    } else if (w.startsWith("--data") && w.includes("=")) data.push(w.slice(w.indexOf("=") + 1));
    else if (w === "-F" || w === "--form" || w === "--form-string") {
      const f = next();
      const at = f.indexOf("=");
      if (at > 0) form.push([f.slice(0, at), f.slice(at + 1)]);
    } else if (w === "--url") url = next();
    else if (VALUE_FLAGS.has(w)) next();
    else if (!w.startsWith("-") && !url) url = w;
  }
  if (!url) throw new Error("curl 命令里没有请求地址");
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  if (json && !headers.some(([k]) => k.toLowerCase() === "content-type")) headers.push(["Content-Type", "application/json"]);
  const body = data.length ? data.join("&") : null;
  return { method: method || (body || form.length ? "POST" : "GET"), url, headers, body, form };
}

/** 浏览器、Postman 自带的请求头，对接口没有意义，不带进模板 */
const NOISE = /^(content-type|accept|accept-encoding|accept-language|user-agent|connection|cache-control|pragma|origin|referer|cookie|content-length|host|priority|dnt|postman-token|upgrade-insecure-requests|sec-.*|x-requested-with)$/i;
const AUTH = /^(authorization|x-api-key|api-key|apikey|x-auth-token|x-token|token|access-token)$/i;

export interface CurlImport {
  /** 要写进表单的字段（tpl 下的） */
  fields: Partial<HttpForm>;
  /** 服务地址：请求地址去掉接口路径后的部分 */
  baseUrl: string;
  /** 给用户看的说明：识别到了什么、替换了什么、还要手动改什么 */
  notes: string[];
  warnings: string[];
}

type Step = "ask" | "create_kb" | "upload" | "status";

/** 请求地址拆成服务地址 + 接口路径；服务地址已经填了且是它的前缀，就沿用 */
export function splitUrl(url: string, currentBase: string): { baseUrl: string; path: string } {
  const base = currentBase.trim().replace(/\/+$/, "");
  if (base && url.startsWith(base) && (url.length === base.length || "/?".includes(url[base.length]))) {
    return { baseUrl: base, path: url.slice(base.length) || "/" };
  }
  const u = new URL(url);
  return { baseUrl: u.origin, path: `${u.pathname}${u.search}` || "/" };
}

const QUESTION_KEYS = ["question", "query", "q", "prompt", "input", "message", "content", "text", "msg", "user_input"];
const KB_NAME_KEYS = ["name", "kb_name", "dataset_name", "title"];

/** 在请求体里找到某个字符串值，换成占位符；返回替换后的对象和命中的位置 */
function replaceValue(obj: unknown, match: (key: string, value: string) => boolean, placeholder: string, at = "$"): { value: unknown; hit: string | null } {
  if (typeof obj === "string") return { value: obj, hit: null };
  if (Array.isArray(obj)) {
    // 对话格式取最后一条（用户的提问）
    for (let i = obj.length - 1; i >= 0; i--) {
      const r = replaceValue(obj[i], match, placeholder, `${at}[${i}]`);
      if (r.hit) return { value: obj.map((x, j) => (j === i ? r.value : x)), hit: r.hit };
    }
    return { value: obj, hit: null };
  }
  if (obj && typeof obj === "object") {
    const entries = Object.entries(obj as Record<string, unknown>);
    for (const [k, v] of entries) {
      if (typeof v === "string" && match(k, v)) return { value: { ...(obj as object), [k]: placeholder }, hit: `${at}.${k}` };
    }
    for (const [k, v] of entries) {
      const r = replaceValue(v, match, placeholder, `${at}.${k}`);
      if (r.hit) return { value: { ...(obj as object), [k]: r.value }, hit: r.hit };
    }
  }
  return { value: obj, hit: null };
}

function forceNoStream(obj: unknown): { value: unknown; changed: boolean } {
  if (obj && typeof obj === "object" && !Array.isArray(obj) && (obj as Record<string, unknown>).stream === true) {
    return { value: { ...(obj as object), stream: false }, changed: true };
  }
  return { value: obj, changed: false };
}

/** 地址里出现的库编号、文档编号换成占位符 */
function placeIds(path: string, ids: { kbId?: string; docId?: string }): { path: string; replaced: string[] } {
  let out = path;
  const replaced: string[] = [];
  if (ids.kbId && out.includes(ids.kbId)) { out = out.split(ids.kbId).join("{{kb_id}}"); replaced.push("库编号"); }
  if (ids.docId && out.includes(ids.docId)) { out = out.split(ids.docId).join("{{doc_id}}"); replaced.push("文档编号"); }
  return { path: out, replaced };
}

/**
 * 把一条 curl 映射成某个接口的表单字段。
 * sample：curl 里那句示例问题（提问）或示例库名（建库）；不给就按常见字段名猜。
 * ids：试调建库、上传拿到的编号，地址里出现就换成占位符。
 */
export function importCurl(text: string, step: Step, opts: { currentBase?: string; sample?: string; kbId?: string; docId?: string } = {}): CurlImport {
  const c = parseCurl(text);
  const notes: string[] = [];
  const warnings: string[] = [];
  const { baseUrl, path: rawPath } = splitUrl(c.url, opts.currentBase ?? "");
  const { path, replaced } = placeIds(rawPath, opts);
  if (replaced.length) notes.push(`地址里的${replaced.join("、")}已换成占位符`);
  const fields: Partial<HttpForm> = {};

  const auth = c.headers.find(([k]) => AUTH.test(k));
  if (auth) {
    const [name, value] = auth;
    const bearer = /^bearer\s+/i.test(value);
    fields.authHeader = name;
    fields.authValue = bearer ? "Bearer {{api_key}}" : "{{api_key}}";
    notes.push(`认证头 ${name} 的密钥已换成 {{api_key}}：密钥不会保存，运行评测台前把它设到「API Key 环境变量」里`);
  }
  const extra = c.headers.filter(([k]) => !NOISE.test(k) && !AUTH.test(k));
  if (extra.length) {
    fields.extraHeaders = JSON.stringify(Object.fromEntries(extra), null, 2);
    notes.push(`另外带上请求头：${extra.map(([k]) => k).join("、")}`);
  }

  let body: unknown = null;
  if (c.body != null) {
    try {
      body = JSON.parse(c.body);
    } catch {
      warnings.push("请求体不是 JSON，评测台目前只支持 JSON 请求体，请手动改写");
    }
  }

  if (step === "ask") {
    fields.askMethod = c.method;
    fields.askPath = path;
    if (body !== null) {
      const sample = opts.sample?.trim();
      const r = replaceValue(body, sample ? (_k, v) => v.trim() === sample : (k) => QUESTION_KEYS.includes(k.toLowerCase()), "{{question}}");
      const s = forceNoStream(r.value);
      fields.askBody = JSON.stringify(s.value, null, 2);
      if (r.hit) notes.push(`题目放在请求体的 ${r.hit}`);
      else warnings.push(sample ? `请求体里没找到「${sample}」，请在请求体里把问题那一处改成 {{question}}` : "没认出问题放在哪个字段，填一下 curl 里的示例问题，或在请求体里把问题改成 {{question}}");
      if (s.changed) notes.push("请求体里的 stream 已改成 false：评测台按一次性返回读答案");
    } else {
      fields.askBody = "";
      warnings.push("这条 curl 没有请求体，请确认问题是怎么传过去的");
    }
  } else if (step === "create_kb") {
    fields.createMethod = c.method;
    fields.createPath = path;
    if (body !== null) {
      const sample = opts.sample?.trim();
      const r = replaceValue(body, sample ? (_k, v) => v.trim() === sample : (k) => KB_NAME_KEYS.includes(k.toLowerCase()), "{{kb_name}}");
      fields.createBody = JSON.stringify(r.value, null, 2);
      if (r.hit) notes.push(`新库名称放在请求体的 ${r.hit}，每次评测会用新名字`);
      else warnings.push("没认出库名字段，请在请求体里把库名改成 {{kb_name}}");
    }
  } else if (step === "upload") {
    fields.uploadPath = path;
    const file = c.form.find(([, v]) => v.startsWith("@"));
    const rest = c.form.filter(([, v]) => !v.startsWith("@"));
    if (file) {
      fields.uploadFileField = file[0];
      notes.push(`文件字段是 ${file[0]}`);
    } else {
      warnings.push("curl 里没有 -F 字段=@文件，评测台按 multipart 上传，请确认文件字段名");
    }
    fields.uploadForm = rest.length ? JSON.stringify(Object.fromEntries(rest), null, 2) : "";
    if (!path.includes("{{kb_id}}")) warnings.push("如果地址里带着库编号，请把它改成 {{kb_id}}（先试调建库，再粘贴会自动替换）");
  } else {
    fields.statusPath = path;
    if (c.method !== "GET") warnings.push(`查进度目前只支持 GET，这条是 ${c.method}`);
    if (!path.includes("{{kb_id}}") && !path.includes("{{doc_id}}")) warnings.push("请把地址里的库编号、文档编号改成 {{kb_id}}、{{doc_id}}（先试调建库和上传，再粘贴会自动替换）");
  }
  return { fields, baseUrl, notes, warnings };
}
