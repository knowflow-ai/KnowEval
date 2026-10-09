import { Alert, Button, Card, Input, Space, Typography } from "antd";

import type { VariantDraft } from "../assistantEval";
import JsonArea from "./JsonArea";

// 读不到基准助手的参数时，退回只写要改的项
const FALLBACK = JSON.stringify({ prompt: { sag_search_strategy: "atomic" } }, null, 2);

// 常改的参数（建助手接口的写法，和系统页「建助手参数」一致）
const COMMON: [string, string][] = [
  ["prompt.sag_search_strategy", "SAG 检索策略：vector 快 / multi 精确 / atomic 多跳"],
  ["prompt.use_colpali", "ColPali 检索；同时要把 llm.model_name 换成视觉模型"],
  ["prompt.top_n", "给模型看的片段数"],
  ["prompt.similarity_threshold", "相似度阈值"],
  ["prompt.vector_similarity_weight", "向量相似度的权重（其余是关键词）"],
  ["prompt.rerank_model", "重排模型"],
  ["prompt.prompt", "系统提示词（要保留 {knowledge}）"],
  ["llm.model_name / llm.max_tokens", "对话模型、最长回答"],
];

/**
 * 参数对照：以选中的助手为基准，每组只写要改的参数。评测时评测台复制基准助手、改上这几项建临时助手，
 * 和基准问同一套题，问完删掉，不碰原来的助手。
 */
export default function VariantsCard({ base, baseParams, baseParamsError, value, onChange }: {
  base: string;
  /** 基准助手现在的全部参数（建助手接口的写法）；null 表示还在读 */
  baseParams: Record<string, unknown> | null;
  baseParamsError: string;
  value: VariantDraft[];
  onChange: (v: VariantDraft[]) => void;
}) {
  const loading = !baseParams && !baseParamsError;
  const template = baseParams ? JSON.stringify(baseParams, null, 2) : FALLBACK;
  const update = (i: number, patch: Partial<VariantDraft>) => onChange(value.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  return (
    <Card size="small">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          以「{base}」为基准。每组预填的是它现在的全部参数，直接改你想对照的值，没改的保持和基准一样；
          评测台复制这个助手、改上这几项，建一个临时助手和它问同一套题，问完就删，不碰原来的助手。
          分块方式、ColPali 解析、知识库的 SAG 开关这类知识库层面的设置要重新解析，请用「完整测试」。
        </Typography.Text>
        {baseParamsError && <Alert type="warning" showIcon message={`读不到「${base}」现在的参数：${baseParamsError}`}
          description="每组只写要改的参数即可，没写的保持和基准一样。" />}
        {value.map((v, i) => (
          // 用序号当 key：组的增删都在末尾或按位置，没有排序
          // eslint-disable-next-line react/no-array-index-key
          <div key={i} style={{ padding: 12, background: "#fafafa", borderRadius: 6 }}>
            <Space style={{ marginBottom: 8 }}>
              <Input value={v.name} placeholder={`对照 ${i + 1}`} onChange={(e) => update(i, { name: e.target.value })} style={{ width: 240 }} />
              <Button size="small" type="text" danger onClick={() => onChange(value.filter((_, j) => j !== i))}>删除</Button>
            </Space>
            <JsonArea value={v.json} onChange={(json) => update(i, { json })} />
          </div>
        ))}
        <Button type="dashed" loading={loading} onClick={() => onChange([...value, { name: "", json: template }])}>+ 加一组对照</Button>
        <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
          常改的参数：
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {COMMON.map(([k, d]) => <li key={k}><Typography.Text code style={{ fontSize: 12 }}>{k}</Typography.Text>　{d}</li>)}
          </ul>
        </div>
      </Space>
    </Card>
  );
}
