import { Alert, Button, Input, Space, Typography } from "antd";
import type { FormInstance } from "antd";
import { useState } from "react";

import { importCurl } from "../curl";
import { DEFAULT_HTTP_FORM, type HttpForm } from "../httpTemplate";
import { formatJson } from "../logic";

export type CurlStep = "ask" | "create_kb" | "upload" | "status";

const PLACEHOLDER: Record<CurlStep, string> = {
  ask: `curl -X POST https://rag.example.com/api/chat \\
  -H "Authorization: Bearer sk-xxxx" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "年假有几天", "stream": false}'`,
  create_kb: `curl -X POST https://rag.example.com/api/kb -H "Authorization: Bearer sk-xxxx" -d '{"name": "测试库"}'`,
  upload: `curl -X POST https://rag.example.com/api/kb/kb-123/documents -H "Authorization: Bearer sk-xxxx" -F "file=@a.pdf"`,
  status: `curl https://rag.example.com/api/kb/kb-123/documents/doc-456 -H "Authorization: Bearer sk-xxxx"`,
};
const SAMPLE: Partial<Record<CurlStep, string>> = { ask: "curl 里的那句问题（可不填，不填按 query、question、messages 等常见字段猜）", create_kb: "curl 里的那个库名（可不填，不填按 name 等字段猜）" };
const PATH: Record<CurlStep, keyof HttpForm> = { ask: "askPath", create_kb: "createPath", upload: "uploadPath", status: "statusPath" };
const METHOD: Partial<Record<CurlStep, keyof HttpForm>> = { ask: "askMethod", create_kb: "createMethod" };
const BODY: Partial<Record<CurlStep, keyof HttpForm>> = { ask: "askBody", create_kb: "createBody" };

/** 当前这个接口解析出来的请求（编辑已接入的系统时也能看到） */
function Current({ tpl, step, baseUrl }: { tpl: HttpForm; step: CurlStep; baseUrl: string }) {
  const path = String(tpl[PATH[step]] ?? "");
  if (!path) return null;
  const method = METHOD[step] ? String(tpl[METHOD[step]!]) : step === "upload" ? "POST" : "GET";
  const body = BODY[step] ? String(tpl[BODY[step]!] ?? "") : step === "upload" ? `multipart，文件字段 ${tpl.uploadFileField}` : "";
  return (
    <div style={{ background: "#fafafa", borderRadius: 6, padding: "8px 12px", fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}>
      <div>{method} {baseUrl}{path}</div>
      {tpl.authHeader && <div>{tpl.authHeader}: {tpl.authValue}</div>}
      {body && <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{formatJson(body)}</pre>}
    </div>
  );
}

/** 贴一条能跑通的 curl（Postman、Apifox、浏览器「复制为 cURL」都行），解析后填进这个接口的配置 */
export default function CurlBox({ form, step, ids }: { form: FormInstance; step: CurlStep; ids?: { kbId?: string; docId?: string } }) {
  const tpl: HttpForm = { ...DEFAULT_HTTP_FORM, ...form.getFieldValue("tpl") };
  const baseUrl: string = form.getFieldValue("base_url") ?? "";
  const [editing, setEditing] = useState(!tpl[PATH[step]]);
  const [text, setText] = useState("");
  const [sample, setSample] = useState("");
  const [notes, setNotes] = useState<{ notes: string[]; warnings: string[] } | null>(null);
  const [error, setError] = useState("");

  const apply = () => {
    try {
      const r = importCurl(text, step, { currentBase: baseUrl, sample, ...ids });
      form.setFieldsValue({ base_url: r.baseUrl, tpl: { ...tpl, ...r.fields, ...(step === "ask" && sample.trim() ? { askSample: sample.trim() } : {}) } });
      setNotes({ notes: r.notes, warnings: r.warnings });
      setError("");
      setEditing(false);
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  };

  if (!editing) {
    return (
      <Space direction="vertical" style={{ width: "100%" }}>
        <Current tpl={tpl} step={step} baseUrl={baseUrl} />
        {notes && !!notes.notes.length && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{notes.notes.join("；")}</Typography.Text>}
        {notes && !!notes.warnings.length && <Alert type="warning" showIcon message={notes.warnings.join("；")} />}
        <Button size="small" onClick={() => setEditing(true)}>重新粘贴 curl</Button>
      </Space>
    );
  }
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Input.TextArea rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder={PLACEHOLDER[step]}
        style={{ fontFamily: "monospace", fontSize: 12 }} />
      {SAMPLE[step] && <Input value={sample} onChange={(e) => setSample(e.target.value)} placeholder={SAMPLE[step]} />}
      {error && <Alert type="error" showIcon message={error} />}
      <Space>
        <Button type="primary" size="small" disabled={!text.trim()} onClick={apply}>解析</Button>
        {!!tpl[PATH[step]] && <Button size="small" onClick={() => setEditing(false)}>取消</Button>}
      </Space>
    </Space>
  );
}
