import { Button, Card, Col, Drawer, Empty, List, Row, Space, Tag, Typography } from "antd";

import { QTYPE_NAME, VERDICT_TAG, outcome, outcomeLabel, plainText } from "../data";
import type { Question, RunRecord } from "../types";
import { DocLink } from "./docLink";

/** 一个参评项对这道题的回答：判定、回答原文、判定依据、引用核对 */
function SystemAnswer({ name, q, rec }: { name: string; q: Question; rec?: RunRecord }) {
  if (!rec) return <Card size="small" title={name}><Empty description="没有这道题的结果" /></Card>;
  const matched = new Set(rec.judgement.citation?.matches.map((m) => m.citation) ?? []);
  return (
    <Card size="small" title={<Typography.Text ellipsis={{ tooltip: name }}>{name}</Typography.Text>}
      extra={<Tag color={VERDICT_TAG[outcome(q, rec.judgement.verdict)]} style={{ marginInlineEnd: 0 }}>{outcomeLabel(q, rec.judgement.verdict)}</Tag>}>
      <div style={{ background: "#fafafa", padding: "8px 12px", borderRadius: 6, maxHeight: 240, overflow: "auto" }}>
        {rec.answer.error ? <Typography.Text type="danger">{rec.answer.error}</Typography.Text>
          : <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: 0 }}>{rec.answer.text}</Typography.Paragraph>}
      </div>
      <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>判定依据</Typography.Text>
      <Typography.Paragraph style={{ marginBottom: 8 }}>
        {rec.judgement.reason || "—"}
        {rec.judgement.attribution && (
          <Typography.Text type="secondary">
            （{rec.judgement.attribution === "retrieval" ? "检索没找到标准证据" : "证据检索到了，模型没答对"}）
          </Typography.Text>
        )}
      </Typography.Paragraph>
      <Typography.Text type="secondary">引用核对 · {rec.answer.citations.length} 条{rec.answer.latency_ms !== null ? ` · ${rec.answer.latency_ms} ms` : ""}</Typography.Text>
      <List size="small" dataSource={rec.answer.citations} locale={{ emptyText: "没有引用" }} renderItem={(c, i) => (
        <List.Item style={{ padding: "4px 0" }}>
          <div style={{ width: "100%", minWidth: 0 }}>
            <Space size={4}>
              <Typography.Text code>[{i + 1}]</Typography.Text>
              <DocLink target={{ name: c.doc, page: c.page, quote: plainText(c.text) }}>{c.doc}{c.page ? ` · 第 ${c.page} 页` : ""}</DocLink>
              {rec.judgement.citation && (matched.has(i) ? <Tag color="blue">对得上</Tag> : <Tag>对不上</Tag>)}
            </Space>
            <div style={{ color: "rgba(0,0,0,0.45)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={plainText(c.text)}>{plainText(c.text)}</div>
          </div>
        </List.Item>
      )} />
    </Card>
  );
}

/**
 * 对比里的单题详情（右侧抽屉）：上面是题目、标准答案与证据，下面各参评项并排（第 1 遍，和逐题对照的颜色块一致）。
 * ids 是当前分组里的题，用来上一题、下一题。
 */
export default function CompareDetail({ q, ids, names, records, onPick, onClose }: {
  q?: Question; ids: string[]; names: string[]; records: Record<string, RunRecord>[];
  onPick: (qid: string) => void; onClose: () => void;
}) {
  const span = names.length === 3 ? 8 : 12;
  const at = q ? ids.indexOf(q.id) : -1;
  return (
    <Drawer open={!!q} onClose={onClose} width="min(1100px, 92vw)" destroyOnClose
      title={q && <Space size={8} wrap>
        <Typography.Text type="secondary" code>{q.id}</Typography.Text>
        {q.qtype.map((t) => <Tag key={t}>{t} {QTYPE_NAME[t] ?? ""}</Tag>)}
        <Typography.Text type="secondary" style={{ fontWeight: "normal", fontSize: 12 }}>各项第 1 遍的结果</Typography.Text>
      </Space>}
      extra={at >= 0 && <Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{at + 1} / {ids.length}</Typography.Text>
        <Button size="small" disabled={at <= 0} onClick={() => onPick(ids[at - 1])}>上一题</Button>
        <Button size="small" disabled={at >= ids.length - 1} onClick={() => onPick(ids[at + 1])}>下一题</Button>
      </Space>}>
      {q && <Body q={q} span={span} names={names} records={records} />}
    </Drawer>
  );
}

function Body({ q, span, names, records }: { q: Question; span: number; names: string[]; records: Record<string, RunRecord>[] }) {
  return (
    <>
      <Typography.Title level={5} style={{ marginTop: 0 }}>{q.question}</Typography.Title>
      <Typography.Paragraph style={{ marginBottom: 4 }}>
        <Typography.Text type="secondary">标准答案：</Typography.Text>
        <Typography.Text strong>{q.answer ? `${q.answer}${q.unit ? ` ${q.unit}` : ""}` : "（无答案题：应当说不知道）"}</Typography.Text>
      </Typography.Paragraph>
      {q.evidence.map((e, i) => (
        <Typography.Paragraph key={i} style={{ marginBottom: 4, fontSize: 13 }}>
          <Typography.Text type="secondary">证据 {i + 1} · </Typography.Text>
          <DocLink target={{ name: e.doc, page: e.page, quote: e.quote }}>{e.doc}{e.page ? ` · 第 ${e.page} 页` : ""}</DocLink>
          <Typography.Text type="secondary">：</Typography.Text>
          <Typography.Text mark>{e.quote}</Typography.Text>
        </Typography.Paragraph>
      ))}
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        {names.map((n, i) => (
          <Col key={n + i} xs={24} md={12} xl={span}><SystemAnswer name={n} q={q} rec={records[i]?.[q.id]} /></Col>
        ))}
      </Row>
    </>
  );
}
