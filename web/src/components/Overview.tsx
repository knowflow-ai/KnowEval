import { Alert, Card, Col, List, Row, Space, Statistic, Table, Tag, Typography } from "antd";

import { QTYPE_NAME, VERDICT_LABEL, ciText, latencyStats, pct, verdictCounts } from "../data";
import type { Rate, ReportData, Summary, Verdict } from "../types";
import CiBar from "./CiBar";

const METRICS: { key: keyof Summary; label: string; better: "高" | "低"; def: string }[] = [
  { key: "accuracy", label: "准确率", better: "高", def: "答对 ÷ 有答案的题（出错的题也在分母里）" },
  { key: "confident_wrong", label: "自信错答率", better: "低", def: "给了结论的题里，结论错了的比例" },
  { key: "abstain_correct", label: "拒答正确率", better: "高", def: "文档里没有答案的题，说了不知道的比例" },
  { key: "over_abstain", label: "过度拒答率", better: "低", def: "明明能答，却说不知道的比例" },
];

const SEG_COLOR: Record<Verdict, string> = { CORRECT: "#1677ff", ABSTAIN: "#d9d9d9", WRONG: "#fa8c16", ERROR: "#ff4d4f" };

function MetricCard({ label, better, def, rate }: { label: string; better: string; def: string; rate: Rate }) {
  const warn = better === "低";
  return (
    <Card size="small">
      <Statistic title={<Space>{label}<Typography.Text type="secondary" style={{ fontSize: 12 }}>越{better}越好</Typography.Text></Space>}
        value={rate.value === null ? "—" : (rate.value * 100).toFixed(1)} suffix={rate.value === null ? "" : "%"} />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>95% 区间 {ciText(rate)} · n={rate.n}</Typography.Text>
      <div style={{ marginTop: 8 }}>
        <CiBar rate={rate} color={warn ? "#d4380d" : "#1677ff"} band={warn ? "#ffd591" : "#bae0ff"} />
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "8px 0 0" }}>{def}</Typography.Paragraph>
    </Card>
  );
}

function DistributionBar({ counts, label }: { counts: Record<Verdict, number>; label: string }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return null;
  return (
    <Row align="middle" gutter={12} style={{ marginBottom: 8 }}>
      <Col flex="120px"><Typography.Text>{label} <Typography.Text type="secondary">{total}</Typography.Text></Typography.Text></Col>
      <Col flex="auto">
        <div style={{ display: "flex", height: 24, borderRadius: 4, overflow: "hidden" }}>
          {(Object.keys(counts) as Verdict[]).filter((v) => counts[v]).map((v) => (
            <div key={v} title={`${VERDICT_LABEL[v]} ${counts[v]}`}
              style={{ width: `${(counts[v] / total) * 100}%`, background: SEG_COLOR[v], color: v === "CORRECT" || v === "ERROR" ? "#fff" : "rgba(0,0,0,0.88)", fontSize: 12, display: "flex", alignItems: "center", paddingLeft: 6, whiteSpace: "nowrap", overflow: "hidden" }}>
              {VERDICT_LABEL[v]} {counts[v]}
            </div>
          ))}
        </div>
      </Col>
    </Row>
  );
}

const GROUP_NAME: Record<string, string> = { hops: "跳数", modality: "证据形态", cross_page: "是否跨页", format: "文档格式", origin: "题目来源" };

export default function Overview({ data }: { data: ReportData }) {
  const s = data.summary.repeats[0];
  const rep = data.records["1"] ?? {};
  const dist = verdictCounts(data.questions, rep);
  const lat = latencyStats(rep);
  const failedDocs = (data.summary.ingest?.docs ?? []).filter((d: any) => !d.ok);
  const reviewed = data.summary.repeats_reviewed?.[0];
  const hr = data.summary.human_review;

  const qtypeRows = Object.entries(s.by_qtype).map(([t, x]) => ({ key: t, t, x }));
  const layoutRows = Object.entries(s.groups ?? {}).flatMap(([g, buckets]) =>
    Object.entries(buckets).map(([k, x]) => ({ key: `${g}-${k}`, g: GROUP_NAME[g] ?? g, k, x })));

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {s.needs_review > 0 && (
        <Alert type="warning" showIcon message={`有 ${s.needs_review} 题没有配置裁判模型、判不了分，暂按自信答错计；配好评测台的模型后重新判分`} />
      )}
      {hr && (
        <Alert type="info" showIcon
          message={`人工复核 ${hr.reviewed} 题，与机器判定一致 ${hr.agreed} 题，一致率 ${pct(hr.agreement)}；改判后准确率 ${pct(reviewed?.accuracy.value)}`} />
      )}
      <Row gutter={[16, 16]}>
        {METRICS.map((m) => (
          <Col key={m.key} xs={24} sm={12} xl={6}><MetricCard label={m.label} better={m.better} def={m.def} rate={s[m.key] as Rate} /></Col>
        ))}
      </Row>

      <Card title="判定分布" size="small" extra={<Typography.Text type="secondary">不合成总分；出错的题计入分母，不会被剔除</Typography.Text>}>
        <DistributionBar label="有答案的题" counts={dist.answerable} />
        <DistributionBar label="无答案的题" counts={dist.noAnswer} />
      </Card>

      <Row gutter={16}>
        <Col xs={24} xl={16}>
          <Card title="按题型" size="small" extra={<Typography.Text type="secondary">浅色段是 95% 区间；N 行是拒答正确率</Typography.Text>}>
            <Table size="small" pagination={false} dataSource={qtypeRows} columns={[
              { title: "题型", render: (_, r) => <Space><Tag>{r.t}</Tag>{QTYPE_NAME[r.t] ?? r.t}</Space> },
              { title: "题数", align: "right", render: (_, r) => r.x.total },
              { title: "准确率", width: "35%", render: (_, r) => { const rate = r.t === "N" ? r.x.abstain_correct : r.x.accuracy; return <Space style={{ width: "100%" }}><div style={{ flex: 1, minWidth: 140 }}><CiBar rate={rate} /></div>{pct(rate.value)}</Space>; } },
              { title: "自信错答率", align: "right", render: (_, r) => pct(r.x.confident_wrong.value) },
              { title: "引用可核对率", align: "right", render: (_, r) => pct(r.x.citation_verifiable.value) },
            ]} />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Card title="引用核对" size="small">
              <Statistic title="引用可核对率" value={pct(s.citation_verifiable.value)} valueStyle={{ fontSize: 24 }} />
              <Typography.Text type="secondary">答对的题里，至少一条引用对得上标准证据：{s.citation_verifiable.k}/{s.citation_verifiable.n}</Typography.Text>
              <Statistic title="引用精确率" value={pct(s.citation_precision.value)} valueStyle={{ fontSize: 24 }} style={{ marginTop: 12 }} />
              <Typography.Text type="secondary">全部引用里对得上的：{s.citation_precision.k}/{s.citation_precision.n}</Typography.Text>
            </Card>
            <Card title="错题出在哪" size="small">
              {Object.keys(s.attribution ?? {}).length ? (
                <Space direction="vertical">
                  <Typography.Text>检索没找到：{s.attribution.retrieval ?? 0}</Typography.Text>
                  <Typography.Text>找到了没答对：{s.attribution.generation ?? 0}</Typography.Text>
                </Space>
              ) : <Typography.Text type="secondary">被测系统没有返回检索上下文，无法归因</Typography.Text>}
            </Card>
            <Card title="运行质量" size="small">
              <List size="small" split={false} dataSource={[
                `噪声底线：${data.summary.noise.length} 题两遍结果不同`,
                `每题耗时：中位 ${lat.median ?? "—"} ms · P90 ${lat.p90 ?? "—"} ms`,
                `出错：${s.error_count} 题`,
                `入库：${(data.summary.ingest?.docs ?? []).length - failedDocs.length} / ${(data.summary.ingest?.docs ?? []).length} 份成功`,
                ...failedDocs.map((d: any) => `失败：${d.name}（${d.error ?? ""}）`),
              ]} renderItem={(t) => <List.Item style={{ padding: "4px 0" }}>{t}</List.Item>} />
            </Card>
          </Space>
        </Col>
      </Row>

      {layoutRows.length > 0 && (
        <Card title="版面分项" size="small" extra={<Typography.Text type="secondary">无答案题不计入</Typography.Text>}>
          <Table size="small" pagination={false} dataSource={layoutRows} columns={[
            { title: "分组", dataIndex: "g" },
            { title: "取值", dataIndex: "k" },
            { title: "题数", align: "right", render: (_, r) => r.x.total },
            { title: "准确率", width: "40%", render: (_, r) => <Space style={{ width: "100%" }}><div style={{ flex: 1, minWidth: 140 }}><CiBar rate={r.x.accuracy} /></div>{pct(r.x.accuracy.value)}</Space> },
          ]} />
        </Card>
      )}
    </Space>
  );
}
