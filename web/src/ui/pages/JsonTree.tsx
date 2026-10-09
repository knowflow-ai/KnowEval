import { Typography } from "antd";
import { useState } from "react";

import type { Seg } from "../jsonpath";

const MAX_ITEMS = 3;

function Leaf({ value }: { value: unknown }) {
  const text = typeof value === "string" ? `"${value.length > 120 ? `${value.slice(0, 120)}…` : value}"` : String(value);
  const color = typeof value === "string" ? "#389e0d" : typeof value === "number" ? "#1677ff" : "rgba(0,0,0,0.45)";
  return <span style={{ color }}>{text}</span>;
}

/** 可点选的 JSON 树：点任一键名或数值，把它的位置交给 onPick。列表只展开前 3 项 */
export default function JsonTree({ data, onPick, segs = [], name, depth = 0 }: {
  data: unknown;
  onPick: (segs: Seg[], value: unknown) => void;
  segs?: Seg[];
  name?: Seg;
  depth?: number;
}) {
  const [open, setOpen] = useState(depth < 4);
  const isObj = data !== null && typeof data === "object";
  const label = name === undefined ? null : (
    <button type="button" onClick={() => onPick(segs, data)} title="点选：把这个位置填进当前要填的字段"
      style={{ border: "none", background: "#e6f4ff", color: "#0958d9", borderRadius: 4, padding: "0 4px", cursor: "pointer", font: "inherit", marginRight: 4 }}>
      {typeof name === "number" ? `[${name}]` : name}
    </button>
  );
  if (!isObj) {
    return <div style={{ paddingLeft: depth ? 16 : 0, lineHeight: "22px" }}>{label}<Leaf value={data} /></div>;
  }
  const entries: [Seg, unknown][] = Array.isArray(data) ? data.slice(0, MAX_ITEMS).map((v, i) => [i, v]) : Object.entries(data as object);
  const total = Array.isArray(data) ? data.length : entries.length;
  return (
    <div style={{ paddingLeft: depth ? 16 : 0, lineHeight: "22px" }}>
      <span>
        {label}
        <Typography.Link onClick={() => setOpen(!open)} style={{ fontSize: 12 }}>
          {Array.isArray(data) ? `列表 · ${total} 项` : `对象 · ${total} 个字段`} {open ? "收起" : "展开"}
        </Typography.Link>
      </span>
      {open && entries.map(([k, v]) => <JsonTree key={String(k)} data={v} onPick={onPick} segs={[...segs, k]} name={k} depth={depth + 1} />)}
      {open && Array.isArray(data) && data.length > MAX_ITEMS && (
        <div style={{ paddingLeft: 16, color: "rgba(0,0,0,0.45)", fontSize: 12 }}>…还有 {data.length - MAX_ITEMS} 项，结构相同</div>
      )}
    </div>
  );
}
