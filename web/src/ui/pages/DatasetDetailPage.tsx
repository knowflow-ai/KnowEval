import { Alert, Button, Card, Input, Select, Skeleton, Space, Table, Tabs, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { QTYPE_NAME } from "../../data";
import { type DatasetDetail, type DatasetQuestion, api, downloadUrl } from "../api";
import { go } from "../UiApp";
import DocViewer from "./DocViewer";
import PageHead from "./PageHead";


function options(values: (string | null | undefined)[], label: (v: string) => string) {
  return Array.from(new Set(values.filter((v): v is string => !!v))).sort().map((v) => ({ value: v, label: label(v) }));
}

export function QuestionDetail({ q, onOpen }: { q: DatasetQuestion; onOpen: (doc: string, quote: string) => void }) {
  return (
    <div style={{ padding: "4px 8px" }}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <div><Typography.Text type="secondary">问题：</Typography.Text>{q.question}</div>
        <div>
          <Typography.Text type="secondary">标准答案：</Typography.Text>
          {q.answer ? <Typography.Text strong>{q.answer}{q.unit ?? ""}</Typography.Text> : <Typography.Text>（无答案题：文档里没有答案，应当说不知道）</Typography.Text>}
          {!!q.answer_aliases?.length && <Typography.Text type="secondary">　也算对：{q.answer_aliases.join("、")}</Typography.Text>}
        </div>
        {q.evidence.map((ev, i) => (
          <div key={i} style={{ background: "#fafafa", borderRadius: 6, padding: "8px 12px" }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>证据 {i + 1} · </Typography.Text>
            <Typography.Link style={{ fontSize: 12 }} onClick={() => onOpen(ev.doc, ev.quote)}>{ev.doc}{ev.page ? ` 第 ${ev.page} 页` : ""}（查看原文）</Typography.Link>
            <div style={{ fontSize: 13, marginTop: 4 }}>{ev.quote}</div>
          </div>
        ))}
      </Space>
    </div>
  );
}

export default function DatasetDetailPage({ path }: { path: string }) {
  const [data, setData] = useState<DatasetDetail | null>(null);
  const [error, setError] = useState("");
  const [domain, setDomain] = useState<string | undefined>();
  const [qtype, setQtype] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [viewer, setViewer] = useState<{ name: string; quote?: string } | null>(null);

  const load = useCallback(() => api.datasetDetail(path).then(setData).catch((e) => setError(String(e.message ?? e))), [path]);
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => (data?.questions ?? []).filter((q) =>
    (!domain || q.domain === domain) && (!qtype || q.qtype[0] === qtype) &&
    (!keyword || q.question.includes(keyword) || q.answer.includes(keyword) || q.id.includes(keyword))), [data, domain, qtype, keyword]);

  if (error) return <Alert type="error" showIcon message={error} />;
  if (!data) return <Skeleton active />;
  const qs = data.questions;

  return (
    <>
      <PageHead title={<Space><span>{data.name}</span><Tag>官方</Tag></Space>}
        desc={`${data.docs} 份文档 · ${data.questions.length} 题 · 许可：${data.license}`}
        extra={<>
          <Button onClick={() => go({ page: "datasets" })}>返回</Button>
          <Button href={downloadUrl(`/datasets/docs.zip?path=${encodeURIComponent(path)}`)}>下载文档</Button>
          <Button type="primary" disabled={!data.questions.length} onClick={() => go({ page: "new", testset: `dataset:${path}` })}>评测</Button>
        </>} />
      <Card>
        <Tabs items={[
          {
            key: "questions", label: `题目 · ${qs.length}`, children: (
              <>
                <Space wrap style={{ marginBottom: 12 }}>
                  <Select allowClear placeholder="领域" style={{ width: 140 }} value={domain} onChange={setDomain}
                    options={options(qs.map((q) => q.domain), (v) => v)} />
                  <Select allowClear placeholder="题型" style={{ width: 160 }} value={qtype} onChange={setQtype}
                    options={options(qs.map((q) => q.qtype[0]), (v) => `${v} ${QTYPE_NAME[v] ?? ""}`)} />
                  <Input.Search allowClear placeholder="搜题干、答案或题号" style={{ width: 240 }} onSearch={setKeyword} onChange={(e) => !e.target.value && setKeyword("")} />
                  <Typography.Text type="secondary">共 {rows.length} 题</Typography.Text>
                </Space>
                <Table<DatasetQuestion> size="small" rowKey="id" dataSource={rows} pagination={{ pageSize: 20, showSizeChanger: false }}
                  expandable={{ expandedRowRender: (q) => <QuestionDetail q={q} onOpen={(name, quote) => setViewer({ name, quote })} />, expandRowByClick: true }}
                  columns={[
                    { title: "题号", dataIndex: "id", width: 110 },
                    { title: "领域", dataIndex: "domain", width: 90 },
                    { title: "题型", width: 110, render: (_, q) => <Tag>{q.qtype[0]} {QTYPE_NAME[q.qtype[0]] ?? ""}</Tag> },
                    { title: "题目", dataIndex: "question", ellipsis: true },
                    { title: "标准答案", width: 200, ellipsis: true, render: (_, q) => q.answer ? `${q.answer}${q.unit ?? ""}` : <Typography.Text type="secondary">无答案</Typography.Text> },
                  ]} />
              </>
            ),
          },
          {
            key: "docs", label: `文档 · ${data.files.length}`, children: (
              <Table size="small" rowKey="name" dataSource={data.files} pagination={false}
                columns={[
                  { title: "文档", dataIndex: "name", render: (name: string, f) => f.present
                    ? <Typography.Link onClick={() => setViewer({ name })}>{name}</Typography.Link>
                    : <Space><span>{name}</span><Tag>本地没有</Tag></Space> },
                  { title: "领域", dataIndex: "domain", width: 100, render: (v) => v ?? "—" },
                  { title: "格式", dataIndex: "format", width: 70 },
                  { title: "题目引用", width: 90, render: (_, f) => qs.filter((q) => q.evidence.some((e) => e.doc === f.name)).length },
                  { title: "来源", dataIndex: "source_url", width: 220, ellipsis: true,
                    render: (u: string | null | undefined, f) => u
                      ? <Space size={4}><Typography.Link href={u} target="_blank" ellipsis style={{ maxWidth: 170 }}>{u}</Typography.Link>{f.redistribute === false && <Tag>只给清单</Tag>}</Space>
                      : <Typography.Text type="secondary">自生成</Typography.Text> },
                ]} />
            ),
          },
        ]} />
      </Card>
      <DocViewer path={path} name={viewer?.name ?? null} quote={viewer?.quote} onClose={() => setViewer(null)} />
    </>
  );
}
