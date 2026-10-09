import { Card, Col, Descriptions, Empty, Input, List, Row, Segmented, Select, Space, Switch, Tag, Typography } from "antd";
import { useMemo, useState } from "react";

import { QTYPE_NAME, VERDICT_LABEL, VERDICT_TAG, filterQuestions, flippedIds, outcome, outcomeLabel, plainText, type QuestionFilter } from "../data";
import type { Question, ReportData, RunRecord, Verdict } from "../types";
import { DocLink } from "./docLink";

function VerdictTag({ v, q }: { v: Verdict; q: Question }) {
  return <Tag color={VERDICT_TAG[outcome(q, v)]}>{outcomeLabel(q, v)}</Tag>;
}

function Detail({ q, data, flipped }: { q: Question; data: ReportData; flipped: boolean }) {
  const repeats = Object.keys(data.records);
  const [repeat, setRepeat] = useState(repeats[0]);
  const rec: RunRecord | undefined = data.records[repeat]?.[q.id];
  const review = data.reviews.find((r) => r.qid === q.id && String(r.repeat) === repeat);
  const matched = new Set(rec?.judgement.citation?.matches.map((m) => m.citation) ?? []);

  return (
    <Card size="small">
      <Space size={8} wrap style={{ marginBottom: 8 }}>
        <Typography.Text type="secondary" code>{q.id}</Typography.Text>
        {q.qtype.map((t) => <Tag key={t}>{t} {QTYPE_NAME[t] ?? ""}</Tag>)}
        {q.hops > 1 && <Typography.Text type="secondary">{q.hops} 跳</Typography.Text>}
        {flipped && <Tag bordered={false} color="warning">各遍不一致</Tag>}
      </Space>
      <Typography.Title level={4} style={{ marginTop: 0 }}>{q.question}</Typography.Title>

      <Row gutter={24}>
        <Col xs={24} lg={11}>
          <Typography.Text type="secondary">标准答案</Typography.Text>
          <Typography.Paragraph strong style={{ fontSize: 16 }}>
            {q.answer ? `${q.answer}${q.unit ?? ""}` : "（无答案题：应当说不知道）"}
          </Typography.Paragraph>
          <Typography.Text type="secondary">标准证据</Typography.Text>
          <List size="small" dataSource={q.evidence} locale={{ emptyText: "无" }} renderItem={(e, i) => (
            <List.Item style={{ display: "block" }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {i + 1}. <DocLink target={{ name: e.doc, page: e.page, quote: e.quote }}>{e.doc}{e.page ? ` · 第 ${e.page}${e.page_end ? `–${e.page_end}` : ""} 页` : ""}</DocLink> · {e.modality}
              </Typography.Text>
              <div><Typography.Text mark>{e.quote}</Typography.Text></div>
            </List.Item>
          )} />
        </Col>
        <Col xs={24} lg={13}>
          <Space style={{ marginBottom: 8 }}>
            <Typography.Text type="secondary">系统回答</Typography.Text>
            {repeats.length > 1 && (
              <Segmented size="small" value={repeat} onChange={(v) => setRepeat(String(v))}
                options={repeats.map((r) => ({ value: r, label: `第 ${r} 遍 · ${data.records[r]?.[q.id] ? outcomeLabel(q, data.records[r][q.id].judgement.verdict) : "—"}` }))} />
            )}
          </Space>
          {rec ? (
            <>
              <Card size="small" style={{ background: "#fafafa" }}>
                {rec.answer.error ? <Typography.Text type="danger">{rec.answer.error}</Typography.Text>
                  : <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: 0 }}>{rec.answer.text}</Typography.Paragraph>}
              </Card>
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>引用核对</Typography.Text>
              <List size="small" dataSource={rec.answer.citations} locale={{ emptyText: "没有引用" }} renderItem={(c, i) => (
                <List.Item>
                  <div style={{ width: "100%", minWidth: 0 }}>
                    <Space>
                      <Typography.Text code>[{i + 1}]</Typography.Text>
                      <DocLink target={{ name: c.doc, page: c.page, quote: plainText(c.text) }}>{c.doc}{c.page ? ` · 第 ${c.page} 页` : ""}</DocLink>
                      {rec.judgement.citation && (matched.has(i) ? <Tag color="blue">对得上</Tag> : <Tag>对不上</Tag>)}
                    </Space>
                    <div style={{ color: "rgba(0,0,0,0.45)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={plainText(c.text)}>{plainText(c.text)}</div>
                  </div>
                </List.Item>
              )} />
            </>
          ) : <Empty description="这一遍没有这道题的结果" />}
        </Col>
      </Row>

      {rec && (
        <Descriptions size="small" column={1} bordered style={{ marginTop: 16 }} items={[
          { key: "v", label: "判定", children: <Space><VerdictTag v={rec.judgement.verdict} q={q} />
            <Typography.Text type="secondary">{rec.judgement.method === "llm" ? "裁判模型判定" : "未经模型（接口出错或回答为空）"}</Typography.Text>
            {rec.judgement.needs_review && <Tag color="warning">待人工复核</Tag>}</Space> },
          { key: "r", label: "判定依据", children: rec.judgement.reason },
          ...(rec.judgement.attribution ? [{ key: "a", label: "错题出在哪",
            children: rec.judgement.attribution === "retrieval" ? "检索没找到：标准证据没有进入检索上下文" : "找到了没答对：证据都在上下文里，模型没答对" }] : []),
          ...(review ? [{ key: "h", label: "人工复核",
            children: <Space><VerdictTag v={review.verdict} q={q} /><Typography.Text>{review.by}</Typography.Text><Typography.Text type="secondary">{review.reason}</Typography.Text></Space> }] : []),
          { key: "t", label: "耗时", children: rec.answer.latency_ms !== null ? `${rec.answer.latency_ms} ms` : "—" },
        ]} />
      )}
    </Card>
  );
}

export default function Questions({ data }: { data: ReportData }) {
  const rep = data.records["1"] ?? {};
  const flipped = useMemo(() => flippedIds(data.records), [data]);
  const [f, setF] = useState<QuestionFilter>({ verdict: "ALL", qtype: "ALL", onlyFlipped: false, onlyReview: false, search: "" });
  const list = filterQuestions(data.questions, rep, flipped, f);
  const [selected, setSelected] = useState<string | null>(null);
  const current = data.questions.find((q) => q.id === (selected ?? list[0]?.id));
  const qtypes = Array.from(new Set(data.questions.flatMap((q) => q.qtype))).sort();
  const count = (v: Verdict) => data.questions.filter((q) => rep[q.id] && outcome(q, rep[q.id].judgement.verdict) === v).length;

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        <Segmented value={f.verdict} onChange={(v) => setF({ ...f, verdict: v as QuestionFilter["verdict"] })} options={[
          { value: "ALL", label: `全部 ${data.questions.length}` },
          ...(["CORRECT", "ABSTAIN", "WRONG", "ERROR"] as Verdict[]).map((v) => ({ value: v, label: `${VERDICT_LABEL[v]} ${count(v)}` })),
        ]} />
        <Select style={{ width: 160 }} value={f.qtype} onChange={(v) => setF({ ...f, qtype: v })}
          options={[{ value: "ALL", label: "全部题型" }, ...qtypes.map((t) => ({ value: t, label: `${t} ${QTYPE_NAME[t] ?? ""}` }))]} />
        <Space><Switch size="small" checked={f.onlyFlipped} onChange={(v) => setF({ ...f, onlyFlipped: v })} />各遍不一致 {flipped.size}</Space>
        <Space><Switch size="small" checked={f.onlyReview} onChange={(v) => setF({ ...f, onlyReview: v })} />待人工复核</Space>
        <Input.Search allowClear placeholder="搜索题目、题号、文档名" style={{ width: 260 }} onSearch={(v) => setF({ ...f, search: v })} />
      </Space>
      <Row gutter={16}>
        <Col xs={24} lg={8}>
          <Card size="small" styles={{ body: { padding: 0, maxHeight: "75vh", overflow: "auto" } }}>
            <List dataSource={list} locale={{ emptyText: "没有符合条件的题" }} renderItem={(q) => {
              const r = rep[q.id];
              const active = q.id === current?.id;
              return (
                <List.Item onClick={() => setSelected(q.id)} style={{ cursor: "pointer", padding: "10px 16px", background: active ? "#e6f4ff" : undefined, borderLeft: active ? "3px solid #1677ff" : "3px solid transparent" }}>
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Space size={6} wrap>
                      <Typography.Text type="secondary" code>{q.id}</Typography.Text>
                      {q.qtype.map((t) => <Tag key={t} style={{ marginInlineEnd: 0 }}>{t}</Tag>)}
                      {flipped.has(q.id) && <Tag bordered={false} color="warning">各遍不一致</Tag>}
                      {r && <VerdictTag v={r.judgement.verdict} q={q} />}
                    </Space>
                    <Typography.Text>{q.question}</Typography.Text>
                  </Space>
                </List.Item>
              );
            }} />
          </Card>
        </Col>
        <Col xs={24} lg={16}>
          {current ? <Detail key={current.id} q={current} data={data} flipped={flipped.has(current.id)} /> : <Empty />}
        </Col>
      </Row>
    </Space>
  );
}
