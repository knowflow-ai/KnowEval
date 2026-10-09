import { Alert, App, Button, Card, Popconfirm, Progress as ProgressBar, Segmented, Skeleton, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import CompareView from "../../components/CompareView";
import ReportView from "../../components/ReportView";
import { DocPreviewContext, type DocTarget } from "../../components/docLink";
import { fmtTime, pct } from "../../data";
import type { CompareData, ReportData } from "../../types";
import { type EvalDetail, type Participant, type Progress, api } from "../api";
import { STATUS_COLOR, STATUS_TEXT, configSummary, currentRound, progressPercent } from "../logic";
import EvalDocViewer from "./EvalDocViewer";
import { go } from "../UiApp";
import { DELETE_EVAL_NOTE } from "./EvalListPage";
import PageHead from "./PageHead";

function ParticipantProgress({ p, prog }: { p: Participant; prog?: Progress }) {
  const ingest = prog?.ingest;
  const round = prog ? currentRound(prog) : null;
  const failedDocs = ingest?.failed ?? [];
  return (
    <Card size="small" title={<Space wrap><span>{p.name}</span><Tag color={STATUS_COLOR[p.status]}>{STATUS_TEXT[p.status]}</Tag></Space>}
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>{configSummary({ dataset: p.config.dataset ?? {}, chat: p.config.chat ?? {}, existing_chat: p.config.existing_chat, chat_overrides: p.config.chat_overrides })}</Typography.Text>}>
      <ProgressBar percent={progressPercent(prog)} status={p.status === "failed" ? "exception" : p.status === "finished" ? "success" : "active"} />
      <Space size={24} wrap style={{ marginTop: 4, fontSize: 13 }}>
        <Typography.Text type="secondary">
          {p.config.existing_chat ? "① 核对已有助手" : "① 建库上传"}：{ingest ? (ingest.manual ? "文档由被测系统自行导入 · 不可复现" : ingest.existing ? `已核对 ${ingest.total} 份文档都在库里` : `${ingest.ok} / ${ingest.total}`) : p.status === "running" ? "进行中" : "未完成"}
        </Typography.Text>
        {round && ingest && (
          <Typography.Text type="secondary">② 第 {round.repeat} 遍：已提问 {round.asked} / {prog!.total}，已判分 {round.judged}</Typography.Text>
        )}
        {prog?.summary && <Typography.Text>答对 <b>{pct(prog.summary.accuracy)}</b> · 自信错答 {pct(prog.summary.confident_wrong)}</Typography.Text>}
      </Space>
      {failedDocs.length > 0 && (
        <Alert type="warning" showIcon style={{ marginTop: 12 }} message={`${failedDocs.length} 份文档没进库`}
          description={failedDocs.map((d) => `${d.name}：${(d.error ?? "").slice(-200)}`).join("\n")} />
      )}
      {p.error && <Alert type={p.status === "stopped" ? "info" : "error"} showIcon style={{ marginTop: 12 }} message={p.error} />}
    </Card>
  );
}

function Results({ detail }: { detail: EvalDetail }) {
  const done = detail.participants.filter((p) => detail.progress[p.id]?.summary);
  const [view, setView] = useState<string>("__compare");
  const [data, setData] = useState<ReportData | CompareData | null>(null);
  const [error, setError] = useState("");
  const [doc, setDoc] = useState<DocTarget | null>(null);
  const key = done.map((p) => p.id).join(",");

  // 只跑完一份时没有「对比」可选，直接看那份的单报告
  const effective = done.length > 1 ? view : done[0]?.id ?? "";
  useEffect(() => {
    if (!key) return;  // 还没有任何配置跑完，不去请求（后端会回 400）
    setData(null);
    setError("");
    api.evalReport(detail.id, effective === "__compare" ? undefined : effective).then(setData).catch((e) => setError(String(e.message ?? e)));
  }, [detail.id, effective, key]);

  if (!done.length) return null;
  const options = [
    ...(done.length > 1 ? [{ value: "__compare", label: "对比" }] : []),
    ...done.map((p) => ({ value: p.id, label: p.name })),
  ];
  return (
    <Card title="结果" size="small" style={{ marginTop: 16 }}
      extra={options.length > 1 && <Segmented size="small" value={effective} onChange={(v) => setView(String(v))} options={options} />}>
      <DocPreviewContext.Provider value={setDoc}>
        {error ? <Alert type="error" showIcon message={error} /> : !data ? <Skeleton active /> :
          data.kind === "compare" ? <CompareView data={data} embedded /> : <ReportView data={data as ReportData} embedded />}
      </DocPreviewContext.Provider>
      <EvalDocViewer evalId={detail.id} target={doc} onClose={() => setDoc(null)} />
    </Card>
  );
}

export default function EvalDetailPage({ id }: { id: string }) {
  const { message } = App.useApp();
  const [detail, setDetail] = useState<EvalDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => api.evalDetail(id).then(setDetail).catch((e) => setError(String(e.message ?? e))), [id]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      message.success(ok);
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  if (error && !detail) return <Alert type="error" showIcon message={error} />;
  if (!detail) return <Skeleton active />;
  const canResume = !detail.running && detail.status !== "finished";

  return (
    <>
      <PageHead
        title={<Space wrap><span>{detail.name}</span><Tag color={STATUS_COLOR[detail.status]}>{STATUS_TEXT[detail.status]}</Tag></Space>}
        desc={`${detail.dataset_ref} · ${detail.participants.length} 个参评项 · 各跑 ${detail.repeats} 遍 · 创建于 ${fmtTime(detail.created_at)}`}
        extra={<>
          {detail.running && (
            <Popconfirm title="中止这次评测？" description="已答完的题会保留，重新开始从断点续跑。" okText="中止" cancelText="取消"
              onConfirm={() => act(() => api.stopEval(id), "已请求中止")}>
              <Button danger>中止评测</Button>
            </Popconfirm>
          )}
          {!detail.running && (
            <Popconfirm title="删除这次评测？" description={DELETE_EVAL_NOTE} okText="删除" cancelText="取消"
              okButtonProps={{ danger: true }} overlayStyle={{ maxWidth: 360 }}
              onConfirm={async () => {
                try {
                  await api.deleteEval(id);
                  message.success("已删除");
                  go({ page: "evals" });
                } catch (e: any) {
                  message.error(e.message ?? String(e));
                }
              }}>
              <Button danger>删除</Button>
            </Popconfirm>
          )}
          {canResume && <Button type="primary" onClick={() => act(() => api.startEval(id), "已重新开始，会从断点续跑")}>
            {detail.status === "created" ? "开始" : "继续运行"}
          </Button>}
        </>} />
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {detail.participants.map((p) => <ParticipantProgress key={p.id} p={p} prog={detail.progress[p.id]} />)}
      </Space>
      {detail.running && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, fontSize: 13 }}>
          每答完一题就写入检查点。进程挂了或手动中止，重新开始会从断点续跑，已完成的题不重问。
        </Typography.Paragraph>
      )}
      <Results detail={detail} />
    </>
  );
}
