import { Alert, Card, Col, Layout, Radio, Row, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";

import { QTYPE_NAME, VERDICT_LABEL, heatColor, pct } from "../data";
import type { CompareData, Rate, Verdict } from "../types";
import CompareDetail from "./CompareDetail";
import MetricBars, { type MetricDef, shortLabels } from "./MetricBars";

const METRICS: MetricDef[] = [
  { key: "accuracy", label: "准确率", higherIsBetter: true, def: "答对 ÷ 有答案的题（出错的题也在分母里）" },
  { key: "confident_wrong", label: "自信错答率", higherIsBetter: false, def: "给了结论的题里，结论错了的比例" },
  { key: "abstain_correct", label: "拒答正确率", higherIsBetter: true, def: "文档里没有答案的题，说了不知道的比例" },
  { key: "over_abstain", label: "过度拒答率", higherIsBetter: false, def: "明明能答，却说不知道的比例" },
];
const VERDICT_BG: Record<Verdict, string> = { CORRECT: "#1677ff", ABSTAIN: "#d9d9d9", WRONG: "#fa8c16", ERROR: "#ff4d4f" };
const GROUP_LABEL = (k: string) => (k === "all_correct" ? "全都答对" : k === "none_correct" ? "全都答错" : `只有 ${k.slice(5)} 答对`);

export default function CompareView({ data, embedded = false }: { data: CompareData; embedded?: boolean }) {
  const { systems, pairs, groups, matrix } = data.compare;
  const groupKeys = Object.keys(groups);
  const [group, setGroup] = useState(groupKeys[0]);
  const [picked, setPicked] = useState<string | null>(null);
  const qtypes = Array.from(new Set(systems.flatMap((s) => Object.keys(s.by_qtype)))).sort();
  const qmap = Object.fromEntries(data.questions.map((q) => [q.id, q]));
  const detailQ = picked ? qmap[picked] : undefined;
  const labels = shortLabels(systems.map((s) => s.name));

  const body = (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <MetricBars metrics={METRICS} systems={systems.map((s) => ({ name: s.name, rates: s as unknown as Record<string, Rate> }))} />
            <Space direction="vertical" style={{ marginTop: 12, width: "100%" }}>
              {pairs.map((p) => (
                <Alert key={`${p.first}-${p.second}`} type={p.significant ? "success" : "info"} showIcon
                  message={`${p.first} 对 ${p.second}：只有前者做对 ${p.only_first} 题，只有后者做对 ${p.only_second} 题，p=${p.p_value.toFixed(3)}，${p.significant ? "差异显著" : "差异不显著"}`}
                  description={p.significant
                    ? "两边都对或都错的题不计，只比结论不同的那些题（配对 McNemar 检验）。差距大到不太可能是运气。"
                    : "两边都对或都错的题不计，只比结论不同的那些题（配对 McNemar 检验）。这点差距可能只是运气，先别据此下结论；题多一些才看得清。"} />
              ))}
            </Space>
          </div>

          <Row gutter={16}>
            <Col xs={24} xl={12}>
              <Card title="按题型的准确率" size="small">
                <Table size="small" pagination={false} rowKey="t" dataSource={qtypes.map((t) => ({ t }))} columns={[
                  { title: "题型", render: (_, r) => `${r.t} ${QTYPE_NAME[r.t] ?? ""}` },
                  ...systems.map((s, i) => ({
                    title: labels[i], align: "center" as const,
                    onCell: (r: { t: string }) => { const c = heatColor(s.by_qtype[r.t]?.value ?? null); return { style: { background: c.bg, color: c.fg } }; },
                    render: (_: unknown, r: { t: string }) => pct(s.by_qtype[r.t]?.value),
                  })),
                ]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card title="逐题对照" size="small" extra={<Typography.Text type="secondary">无答案题以说不知道为做对</Typography.Text>}>
                {data.records && <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "0 0 8px" }}>点一行，在右侧看这道题各项的回答、判定依据和引用</Typography.Paragraph>}
                <Radio.Group size="small" optionType="button" value={group} onChange={(e) => setGroup(String(e.target.value))}
                  options={groupKeys.map((k) => ({ value: k, label: `${GROUP_LABEL(k)} · ${groups[k].length}` }))}
                  style={{ marginBottom: 12, display: "flex", flexWrap: "wrap", rowGap: 8 }} />
                <Table size="small" pagination={{ pageSize: 10 }} rowKey="qid"
                  onRow={(r) => ({ onClick: () => setPicked(r.qid), style: { cursor: data.records ? "pointer" : undefined } })}
                  rowClassName={(r) => (r.qid === picked ? "ant-table-row-selected" : "")}
                  dataSource={(groups[group] ?? []).map((qid) => ({ qid }))} columns={[
                  { title: "题号", dataIndex: "qid", width: 100 },
                  { title: "题目", ellipsis: true, render: (_, r) => qmap[r.qid]?.question },
                  ...systems.map((s, i) => ({
                    title: <Typography.Text ellipsis={{ tooltip: s.name }} style={{ maxWidth: 96 }}>{labels[i]}</Typography.Text>,
                    width: 112, align: "center" as const,
                    render: (_: unknown, r: { qid: string }) => {
                      const v = matrix[r.qid]?.[i];
                      return v ? <div title={VERDICT_LABEL[v]} style={{ height: 18, borderRadius: 3, background: VERDICT_BG[v] }} /> : "—";
                    },
                  })),
                ]} />
              </Card>
            </Col>
          </Row>
          {data.records && (
            <CompareDetail q={detailQ} ids={groups[group] ?? []} names={systems.map((s) => s.name)} records={data.records}
              onPick={setPicked} onClose={() => setPicked(null)} />
          )}
        </Space>
  );
  if (embedded) return body;
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", padding: "0 24px" }}>
        <Space size="middle">
          <Typography.Text strong style={{ fontSize: 16 }}>KnowEval</Typography.Text>
          <Tag>对比</Tag>
          <Typography.Text>{systems.length} 个运行 · {data.metas[0]?.dataset?.ref}</Typography.Text>
          <Typography.Text type="success">可比：题目与裁判一致</Typography.Text>
        </Space>
      </Layout.Header>
      <Layout.Content style={{ padding: "16px 24px 48px", maxWidth: 1400, width: "100%", margin: "0 auto" }}>
        {body}
      </Layout.Content>
    </Layout>
  );
}
