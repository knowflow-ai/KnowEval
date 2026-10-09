import { Alert, Drawer, Skeleton, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { findQuote } from "../logic";

/** 原文预览：按原样显示文本，证据引文高亮并滚动到可见处 */
type DocLoader = (name: string) => Promise<{ name: string; text: string | null; truncated?: boolean; message?: string }>;

export default function DocViewer({ path, load, name, quote, onClose }: {
  path?: string;
  /** 自定义取原文的方式（构建中的测试集用）；不给就按数据集取 */
  load?: DocLoader;
  name: string | null;
  quote?: string;
  onClose: () => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const mark = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!name) return;
    setText(null);
    setNote("");
    setError("");
    (load ? load(name) : api.datasetDoc(path ?? "", name)).then((d) => {
      if (d.text === null) setNote(d.message ?? "无法预览");
      else {
        setText(d.text);
        if (d.truncated) setNote("文档很长，只显示了前 200 万字");
      }
    }).catch((e) => setError(String(e.message ?? e)));
  }, [path, name, load]);

  const hit = text && quote ? findQuote(text, quote) : null;
  useEffect(() => {
    mark.current?.scrollIntoView({ block: "center" });
  }, [text, quote]);

  return (
    <Drawer open={!!name} onClose={onClose} width={860} title={name} destroyOnClose>
      {error && <Alert type="error" showIcon message={error} />}
      {note && <Alert type="info" showIcon message={note} style={{ marginBottom: 12 }} />}
      {quote && text && !hit && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="原文里没有逐字找到这条引文" description={quote} />}
      {!text && !error && !note && <Skeleton active />}
      {text && (
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.8, margin: 0 }}>
          {hit ? (
            <>
              {text.slice(0, hit[0])}
              <mark ref={mark} style={{ background: "#ffe58f", padding: "0 2px" }}>{text.slice(hit[0], hit[1])}</mark>
              {text.slice(hit[1])}
            </>
          ) : text}
        </Typography.Paragraph>
      )}
    </Drawer>
  );
}
