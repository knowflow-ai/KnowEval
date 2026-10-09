import { Alert, Button, Drawer, Segmented, Skeleton, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import type { DocTarget } from "../../components/docLink";
import { type EvalDoc, api, downloadUrl } from "../api";
import { locateQuote } from "../logic";

/**
 * 评测结果里预览原文：PDF 直接用浏览器打开并跳到引用的页；另有解析后的文本，标准证据、系统引用的原文在里面高亮。
 * 系统引用的片段常被分块改动过（去了换行、加了标题），整段找不到时退到其中最长的一句，再不行就定位到那一页。
 */
export default function EvalDocViewer({ evalId, target, onClose }: { evalId: string; target: DocTarget | null; onClose: () => void }) {
  const [doc, setDoc] = useState<EvalDoc | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"file" | "text">("text");
  const mark = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!target) return;
    setDoc(null);
    setError("");
    api.evalDoc(evalId, target.name).then((d) => {
      setDoc(d);
      setTab(d.format === "pdf" ? "file" : "text");
    }).catch((e) => setError(String(e.message ?? e)));
  }, [evalId, target]);

  const hit = doc && target ? locateQuote(doc.text, target.quote ?? "", doc.pages ? target.page : null) : null;
  useEffect(() => {
    if (tab === "text") mark.current?.scrollIntoView({ block: "center" });
  }, [doc, tab, hit?.start]);

  const fileUrl = target ? downloadUrl(`/evals/${evalId}/doc/file?name=${encodeURIComponent(target.name)}`) : "";
  return (
    <Drawer open={!!target} onClose={onClose} width="min(1000px, 92vw)" destroyOnClose
      title={target && <Space size={8} wrap>{target.name}{target.page ? <Typography.Text type="secondary">第 {target.page} 页</Typography.Text> : null}</Space>}
      extra={doc && <Space>
        {doc.format === "pdf" && <Segmented size="small" value={tab} onChange={(v) => setTab(v as "file" | "text")}
          options={[{ value: "file", label: "原文件" }, { value: "text", label: "文本 · 高亮引文" }]} />}
        <Button size="small" href={fileUrl} target="_blank">新窗口打开</Button>
      </Space>}>
      {error && <Alert type="error" showIcon message={error} />}
      {!doc && !error && <Skeleton active />}
      {doc && tab === "file" && (
        <iframe key={`${target?.name}-${target?.page}`} title={doc.name} src={`${fileUrl}#page=${target?.page ?? 1}`}
          style={{ width: "100%", height: "calc(100vh - 120px)", border: 0 }} />
      )}
      {doc && tab === "text" && (
        <>
          {doc.truncated && <Alert type="info" showIcon style={{ marginBottom: 12 }} message="文档很长，只显示了前 200 万字" />}
          {hit?.note && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={hit.note} />}
          {target?.quote && !hit && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="原文里没有找到这段引文" description={target.quote} />}
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.8, margin: 0 }}>
            {hit ? (
              <>
                {doc.text.slice(0, hit.start)}
                <mark ref={mark} style={{ background: "#ffe58f", padding: "0 2px" }}>{doc.text.slice(hit.start, hit.end)}</mark>
                {doc.text.slice(hit.end)}
              </>
            ) : doc.text}
          </Typography.Paragraph>
        </>
      )}
    </Drawer>
  );
}
