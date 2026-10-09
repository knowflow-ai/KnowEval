import { Input } from "antd";

import { formatJson } from "../logic";

/** 所有填 JSON 的输入框：等宽字体，随内容变高，离开时排成一行一个参数（写错的原样留着，保存时再提示） */
export default function JsonArea({ id, value, onChange, placeholder, minRows = 3 }: {
  id?: string;
  value?: string;
  onChange?: (v: string) => void;
  placeholder?: string;
  minRows?: number;
}) {
  return (
    <Input.TextArea id={id} value={value} placeholder={placeholder} onChange={(e) => onChange?.(e.target.value)}
      onBlur={() => value !== undefined && onChange?.(formatJson(value))}
      autoSize={{ minRows, maxRows: 20 }} style={{ fontFamily: "monospace", fontSize: 12 }} />
  );
}
