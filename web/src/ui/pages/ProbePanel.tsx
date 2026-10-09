import { Alert, App, Button, Input, Radio, Space, Tag, Typography } from "antd";
import type { FormInstance } from "antd";
import { useState } from "react";

import { api } from "../api";
import { parseJsonObject } from "../logic";
import { type HttpForm, toProbeConfig } from "../httpTemplate";
import { type Seg, evalPath, listPathOf, pathOf, preview } from "../jsonpath";
import JsonArea from "./JsonArea";
import JsonTree from "./JsonTree";

export type ProbeStep = "create_kb" | "upload" | "status" | "ask";

export interface PickTarget {
  field: keyof HttpForm;
  label: string;
  /** value：取一个值；list：取一个列表（记成 [*]）；item：列表里每一项的子字段，相对于 parent 指向的列表 */
  kind: "value" | "list" | "item";
  parent?: keyof HttpForm;
}

export interface ProbeContext {
  kbId: string;
  docId: string;
  setKbId: (v: string) => void;
  setDocId: (v: string) => void;
}

const STEP_LABEL: Record<ProbeStep, string> = {
  create_kb: "试调建库", upload: "试调上传", status: "试调查进度", ask: "试问一题",
};

/** 试调一步：真实调一次对方接口，返回结果展开成树，点哪个字段就把它的位置填进选中的目标字段 */
export default function ProbePanel({ form, step, targets, ctx, warning }: {
  form: FormInstance;
  step: ProbeStep;
  targets: PickTarget[];
  ctx: ProbeContext;
  warning?: string;
}) {
  const { message } = App.useApp();
  const [question, setQuestion] = useState(form.getFieldValue(["tpl", "askSample"]) || "这个系统里有哪些文档？");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [target, setTarget] = useState<keyof HttpForm>(targets[0].field);
  const tpl: HttpForm = form.getFieldValue("tpl");
  const [paramsText, setParamsText] = useState("");
  // 模板里引用了参数包（试问看 {{chat.x}}，其余几步看 {{dataset.x}}）时，试调要给这些参数填值
  const pack = step === "ask" ? "chat" : "dataset";
  let usesParams = false;
  try {
    usesParams = new RegExp(`\\{\\{\\s*${pack}\\.`).test(JSON.stringify(toProbeConfig(tpl)));
  } catch {
    usesParams = false;
  }

  const run = async () => {
    let http: Record<string, unknown>;
    try {
      http = toProbeConfig(tpl);
    } catch (e: any) {
      message.error(e.message ?? String(e));
      return;
    }
    let params: Record<string, unknown>;
    try {
      params = parseJsonObject(paramsText);
    } catch (e: any) {
      message.error(`试调参数：${e.message ?? String(e)}`);
      return;
    }
    setBusy(true);
    try {
      const values = { question, kb_id: tpl.mode === "manual" ? tpl.kbId : ctx.kbId, doc_id: ctx.docId };
      const r = await api.probeSystem({ base_url: form.getFieldValue("base_url"), api_key_env: form.getFieldValue("api_key_env"), http, step, values, params });
      setResult(r);
      // 建库、上传拿到的编号，留给后面几步试调用
      if (r.json && step === "create_kb" && tpl.createIdPath) {
        const id = evalPath(r.json, tpl.createIdPath);
        if (id !== undefined && typeof id !== "object") ctx.setKbId(String(id));
      }
      if (r.json && step === "upload" && tpl.uploadDocIdPath) {
        const id = evalPath(r.json, tpl.uploadDocIdPath);
        if (id !== undefined && typeof id !== "object") ctx.setDocId(String(id));
      }
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const current = targets.find((t) => t.field === target)!;
  const listPath = current.kind === "item" && current.parent ? String(tpl[current.parent] ?? "") : "";
  const listData = result?.json && listPath ? evalPath(result.json, listPath) : undefined;
  const firstItem = Array.isArray(listData) ? listData[0] : undefined;

  const pick = (segs: Seg[]) => {
    const path = current.kind === "list" ? listPathOf(segs, result.json) : pathOf(segs);
    if (!path) {
      message.warning(current.kind === "list" ? "这里不在列表里：引用、检索片段要点到一个列表上" : "这个键名含有特殊字符，只能手写路径");
      return;
    }
    form.setFieldValue(["tpl", current.field], path);
    message.success(`「${current.label}」已填为 ${path}`);
  };

  return (
    <div style={{ background: "#fafafa", border: "1px dashed #d9d9d9", borderRadius: 8, padding: 12, marginTop: 4 }}>
      <Space wrap>
        {step === "ask" && <Input value={question} onChange={(e) => setQuestion(e.target.value)} style={{ width: 320 }} placeholder="输入一个问题" />}
        <Button onClick={run} loading={busy}>{STEP_LABEL[step]}</Button>
        {step !== "create_kb" && tpl.mode !== "manual" && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            库编号 {ctx.kbId || "（先试调建库）"}{step === "status" ? ` · 文档编号 ${ctx.docId || "（先试调上传）"}` : ""}
          </Typography.Text>
        )}
      </Space>
      {usesParams && (
        <div style={{ marginTop: 8 }}>
          <JsonArea value={paramsText} onChange={setParamsText} minRows={2}
            placeholder={`试调参数（JSON）：模板里引用了 {{${pack}.x}}，这里填一份${step === "ask" ? "问答" : "建库"}参数，例如\n{\n  "app_id": "..."\n}`} />
        </div>
      )}
      {warning && <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)", marginTop: 6 }}>{warning}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          <Space size={8} style={{ marginBottom: 6 }}>
            <Tag color={result.ok ? "success" : "error"}>{result.status_code ?? "失败"}</Tag>
            <Typography.Text code style={{ fontSize: 12 }}>{result.request}</Typography.Text>
          </Space>
          {result.error && <Alert type="error" showIcon message={result.error} description={result.text} style={{ marginBottom: 8 }} />}
          {result.json !== undefined && (
            <>
              <div style={{ marginBottom: 6 }}>
                <Typography.Text style={{ fontSize: 12 }}>点选后填入：</Typography.Text>
                <Radio.Group size="small" value={target} onChange={(e) => setTarget(e.target.value)} optionType="button"
                  options={targets.map((t) => ({ value: t.field, label: t.label }))} />
              </div>
              <div style={{ background: "#fff", border: "1px solid #f0f0f0", borderRadius: 6, padding: 8, maxHeight: 320, overflow: "auto", fontFamily: "monospace", fontSize: 12 }}>
                {current.kind === "item"
                  ? (firstItem !== undefined
                    ? <JsonTree data={firstItem} onPick={pick} />
                    : <Typography.Text type="secondary">先点选上面的列表位置，这里会展开列表里的第一项</Typography.Text>)
                  : <JsonTree data={result.json} onPick={pick} />}
              </div>
              <div style={{ marginTop: 8, fontSize: 12 }}>
                {targets.map((t) => {
                  const path = String(form.getFieldValue(["tpl", t.field]) ?? "");
                  const base = t.kind === "item" ? firstItem : result.json;
                  return (
                    <div key={t.field}>
                      <Typography.Text type="secondary">{t.label}：</Typography.Text>
                      <Typography.Text code>{path || "（未填）"}</Typography.Text>
                      <Typography.Text type="secondary"> → </Typography.Text>
                      <span>{path && base !== undefined ? preview(evalPath(base, path)) : "—"}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
