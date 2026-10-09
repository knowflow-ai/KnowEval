import { Typography } from "antd";
import { type ReactNode, createContext, useContext } from "react";

/** 要看的那份文档：page 是系统引用或标准证据的页码，quote 用来在文本里高亮 */
export interface DocTarget {
  name: string;
  page?: number | null;
  quote?: string;
}

/**
 * 报告组件既在评测台里用，也打包进单文件 HTML 报告。评测台提供打开文档的方法；
 * 单文件报告没有后端，不提供，文档名就只显示成普通文字。
 */
export const DocPreviewContext = createContext<((t: DocTarget) => void) | null>(null);

export function DocLink({ target, children }: { target: DocTarget; children: ReactNode }) {
  const open = useContext(DocPreviewContext);
  if (!open) return <>{children}</>;
  return (
    <Typography.Link title="预览原文" onClick={(e) => { e.stopPropagation(); open(target); }}>{children}</Typography.Link>
  );
}
