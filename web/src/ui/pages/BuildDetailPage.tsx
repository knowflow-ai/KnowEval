import { Alert, App, Button, Card, Empty, Popconfirm, Progress, Segmented, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { QTYPE_NAME, fmtTime } from "../../data";
import { type AfterReady, type BuildDetail, type BuildItem, type BuildStatus, type BuildSummary, api, downloadUrl } from "../api";
import { go } from "../UiApp";
import { QuestionDetail } from "./DatasetDetailPage";
import DocViewer from "./DocViewer";
import ItemEditor from "./ItemEditor";
import AddQuestions from "./AddQuestions";
import PageHead from "./PageHead";

export const BUILD_STATUS: Record<BuildStatus, { text: string; color: string }> = {
  importing: { text: "取文档中", color: "processing" },
  generating: { text: "出题中", color: "processing" },
  verifying: { text: "自动校验中", color: "processing" },
  ready: { text: "可用", color: "success" },
  failed: { text: "失败", color: "error" },
};

const ITEM_STATUS: Record<BuildItem["status"], { text: string; color: string }> = {
  generated: { text: "待校验", color: "default" },
  verified: { text: "可用", color: "green" },
  approved: { text: "可用", color: "green" },
  edited: { text: "已修改", color: "green" },
  rejected_auto: { text: "自动打回", color: "orange" },
  rejected: { text: "已删除", color: "default" },
};

/** 文档来源说明：上传几份，或取自哪个系统的哪几个知识库 */
export function sourceText(d: BuildSummary): string {
  if (d.source.kind === "import") return `导入 ${d.source.rows ?? 0} 行题目${d.source.files ? `、${d.source.files} 份文档` : ""}`;
  if (d.source.kind !== "kb") return `上传 ${d.source.files ?? 0} 份`;
  const kbs = d.source.kbs;
  if (!kbs?.length) {
    const names = (d.source.kb_ids ?? []).map((k) => d.source.kb_names?.[k] ?? k);
    return `取自 ${d.source.system}：${names.join("、") || "知识库"}`;
  }
  return `取自 ${d.source.system}：${kbs.map((k) => `${k.name}（${k.kept}/${k.documents}）`).join("、")}`;
}

type Filter = "passed" | "rejected_auto" | "deleted" | "all";
const inFilter = (it: BuildItem, f: Filter) =>
  f === "all" || (f === "passed" ? ["verified", "approved", "edited"].includes(it.status)
    : f === "deleted" ? it.status === "rejected" : it.status === "rejected_auto");

/** 为某次评测出的题（从助手的知识库出题）：出完自动开始评测，这里显示进展 */
function AfterReadyNote({ plan, stalled, failed }: { plan: AfterReady; stalled: boolean; failed: boolean }) {
  const who = plan.picks.map((p) => ("chat_name" in p ? p.chat_name : "")).filter(Boolean).join("、") || "选中的助手";
  if (failed) {
    return <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`出题失败，没有开始评测：${who}`}
      description="看上面的失败原因；处理好后可以回到「新建评测」重新出题。" />;
  }
  if (plan.eval_id && plan.note) {
    return <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={plan.note}
      action={<Button size="small" type="primary" onClick={() => go({ page: "eval", id: plan.eval_id! })}>去评测页</Button>} />;
  }
  if (plan.eval_id) {
    return <Alert type="success" showIcon style={{ marginBottom: 16 }} message={`题目出完了，已自动开始评测：${who}`}
      action={<Button size="small" type="primary" onClick={() => go({ page: "eval", id: plan.eval_id! })}>查看评测</Button>} />;
  }
  if (plan.error) {
    return <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={plan.error}
      description="题目本身可以用：处理好后点右上角「评测」手动开始。" />;
  }
  if (stalled) {
    // 出题时评测台重启过：出题线程没了，自动评测也就没开
    return <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="没能自动开始评测（出题期间评测台重启过）"
      description="题目可以用：点右上角「评测」手动开始。" />;
  }
  return <Alert type="info" showIcon style={{ marginBottom: 16 }} message={`出完题会自动开始评测：${who}`}
    description="出题和自动校验要几分钟，可以先离开这个页面，评测开始后在「评测」页能看到。" />;
}

export default function BuildDetailPage({ id }: { id: string }) {
  const { message } = App.useApp();
  const [d, setD] = useState<BuildDetail | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("passed");
  const [viewer, setViewer] = useState<{ name: string; quote?: string } | null>(null);
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [batching, setBatching] = useState(false);
  const [editing, setEditing] = useState<{ item: BuildItem | null } | null>(null);
  const loadDoc = useCallback((name: string) => api.buildDoc(id, name), [id]);

  const load = useCallback(() => api.buildDetail(id).then(setD).catch((e) => setError(String(e.message ?? e))), [id]);
  const busy = d && ["importing", "generating", "verifying"].includes(d.status);
  // 出完题要自动开始的评测：状态刚变成就绪时评测还在建，多等一轮拿到评测编号
  const plan = d?.after_ready;
  const awaitingEval = !!plan && !plan.eval_id && !plan.error && (!!busy || !!d?.running);
  const polling = busy || awaitingEval;
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!polling) return undefined;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [polling, load]);

  const act = async (cid: string, action: "delete" | "restore") => {
    try {
      await api.reviewBuildItem(id, cid, action);
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  const ACTION_TEXT = { delete: "删除", restore: "恢复" } as const;
  const batch = async (action: "delete" | "restore") => {
    setBatching(true);
    try {
      const r = await api.batchBuildItems(id, selected.map(String), action);
      const skipped = r.skipped.length ? `，跳过 ${r.skipped.length} 道（${Array.from(new Set(r.skipped.map((x) => x.reason))).join("；")}）` : "";
      (r.skipped.length ? message.warning : message.success)(`已${ACTION_TEXT[action]} ${r.done} 道${skipped}`);
      setSelected([]);
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBatching(false);
    }
  };

  const stop = async () => {
    try {
      await api.stopBuild(id);
      message.success("正在停止，当前这一步做完就停");
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  const reverify = async () => {
    try {
      await api.reverifyBuild(id);
      message.success("已开始重新校验");
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  const removeBuild = async () => {
    try {
      await api.deleteBuild(id);
      message.success("已删除");
      go({ page: "datasets" });
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  const editable = d?.status === "ready";
  const rows = useMemo(() => (d?.items ?? []).filter((it) => inFilter(it, filter)), [d, filter]);
  if (error && !d) return <Alert type="error" showIcon message={error} />;
  if (!d) return <Skeleton active />;

  const verified = d.candidates - d.pending_verify;
  const percent = d.status === "importing" ? 5 : d.status === "generating"
    ? Math.round(10 + (d.candidates / Math.max(1, d.target)) * 50)
    : d.status === "verifying" ? Math.round(60 + (verified / Math.max(1, d.candidates)) * 40) : 100;

  return (
    <>
      <PageHead
        title={<Space wrap><span>{d.name}</span><Tag color={BUILD_STATUS[d.status].color}>{BUILD_STATUS[d.status].text}</Tag></Space>}
        desc={`${sourceText(d)} · ${d.docs} 份文档进入出题 · 创建于 ${fmtTime(d.created_at)}`}
        extra={<>
          <Button onClick={() => go({ page: "datasets" })}>返回</Button>
          {(busy || d.running) && (
            <Popconfirm title="停止出题？" description="已经出的候选题留着；停下后测试集标成失败，可以删除或重新出题。"
              okText="停止" cancelText="不停" okButtonProps={{ danger: true }} onConfirm={stop} overlayStyle={{ maxWidth: 320 }}>
              <Button danger>停止出题</Button>
            </Popconfirm>
          )}
          <Popconfirm title={`删除测试集「${d.name}」？`} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
            description="题目和上传的文档会一起删掉，不能恢复。已有评测各自存了题目快照，不受影响。"
            onConfirm={removeBuild} overlayStyle={{ maxWidth: 360 }}>
            <Button danger>删除</Button>
          </Popconfirm>
          {d.docs > 0 && <Tooltip title="只接了问答接口的自研系统：下载后自己导入系统，再到「完整测试」里选它的问答接口">
            <Button href={downloadUrl(`/builds/${d.id}/docs.zip`)}>下载文档</Button>
          </Tooltip>}
          {editable && <AddQuestions buildId={d.id} hasDocs={d.docs > 0} onDone={load} />}
          {editable && <Button onClick={() => setEditing({ item: null })}>手动加题</Button>}
          {editable && <Button type="primary" disabled={!d.passed} onClick={() => go({ page: "new", testset: `build:${d.id}` })}>评测</Button>}
        </>} />

      {d.status === "failed" && <Alert type="error" showIcon style={{ marginBottom: 16 }} message="生成失败" description={d.error} />}
      {d.status === "ready" && d.error && <Alert type="error" showIcon style={{ marginBottom: 16 }} message={d.error} />}
      {d.status === "ready" && !!d.verify_errors && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message={`${d.verify_errors} 道题校验时模型调用失败（多半是超时），还在待校验`}
          description="其它题已经校验完。模型恢复后点「重新校验」，只补这几道。"
          action={<Button size="small" type="primary" onClick={reverify}>重新校验</Button>} />
      )}
      {plan && <AfterReadyNote plan={plan} failed={d.status === "failed"}
        stalled={!awaitingEval && !plan.eval_id && !plan.error && d.status !== "failed"} />}
      {d.import_report && !!d.import_report.problems.length && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message={`导入了 ${d.import_report.imported} 题，${d.import_report.problems.length} 行用不了`}
          description={<ul style={{ margin: 0, paddingLeft: 18, maxHeight: 160, overflow: "auto" }}>
            {d.import_report.problems.map((p) => <li key={p.row}>第 {p.row} 行：{p.reason}</li>)}
          </ul>} />
      )}
      {!!d.skipped.length && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`跳过了 ${d.skipped.length} 份不支持的文档`} description={d.skipped.join("、")} />}

      {busy && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Progress percent={percent} status="active" />
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {d.status === "importing" && "正在取文档、做参考解析…"}
            {d.status === "generating" && `大模型出题中：已出 ${d.candidates} / 约 ${d.target} 道`}
            {d.status === "verifying" && `自动校验中（引文逐字核对、闭卷过滤、无答案查证）：${verified} / ${d.candidates}`}
          </Typography.Text>
        </Card>
      )}

      {editable && !d.candidates && (
        <Card>
          <Empty description="还没有题目。点右上角「手动加题」，对照原文自己出题。">
            <Button type="primary" onClick={() => setEditing({ item: null })}>手动加题</Button>
          </Empty>
        </Card>
      )}
      {!!d.candidates && (
        <Card size="small">
          <Space wrap style={{ marginBottom: 12 }}>
            <Segmented value={filter} onChange={(v) => { setFilter(v as Filter); setSelected([]); }} options={[
              { value: "passed", label: `可用 · ${d.passed}` },
              { value: "rejected_auto", label: `自动打回 · ${d.rejected_auto}` },
              { value: "deleted", label: `已删除 · ${d.deleted}` },
              { value: "all", label: `全部 · ${d.candidates}` },
            ]} />
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              可用的题都会参加评测；评测开始时存一份题目快照，之后再改题不影响已有评测。
            </Typography.Text>
          </Space>
          {editable && (
            <Space wrap style={{ marginBottom: 12, minHeight: 32 }}>
              <Typography.Text type="secondary">{selected.length ? `已选 ${selected.length} 道` : "勾选题目后可以批量操作；表头的勾选框可选本页或当前筛选下的全部"}</Typography.Text>
              {!!selected.length && (
                <>
                  <Popconfirm title={`删除选中的 ${selected.length} 道题？`} description="删掉的题不参加评测，之后可以恢复。" okText="删除" cancelText="取消"
                    okButtonProps={{ danger: true }} onConfirm={() => batch("delete")}>
                    <Button size="small" danger loading={batching}>批量删除</Button>
                  </Popconfirm>
                  {filter !== "passed" && <Button size="small" loading={batching} onClick={() => batch("restore")}>批量恢复</Button>}
                  <Button size="small" type="link" onClick={() => setSelected([])}>取消选择</Button>
                </>
              )}
            </Space>
          )}
          <Table<BuildItem> size="small" rowKey="id" dataSource={rows} pagination={{ pageSize: 20, showSizeChanger: false }}
            rowSelection={!editable ? undefined : {
              selectedRowKeys: selected,
              onChange: setSelected,
              selections: [Table.SELECTION_ALL, Table.SELECTION_INVERT, Table.SELECTION_NONE],
              getCheckboxProps: (q) => ({ disabled: q.status === "rejected_auto" }),
            }}
            expandable={{ expandedRowRender: (q) => (
              <>
                <QuestionDetail q={q} onOpen={(name, quote) => setViewer({ name, quote })} />
                {!!q.notes.length && <Alert type="warning" showIcon style={{ margin: "4px 8px" }} message={q.notes.join("；")} />}
              </>
            ) }}
            columns={[
              { title: "题号", dataIndex: "id", width: 90 },
              { title: "题型", width: 110, render: (_, q) => <Tag>{q.qtype[0]} {QTYPE_NAME[q.qtype[0]] ?? ""}</Tag> },
              { title: "题目", dataIndex: "question", ellipsis: true },
              { title: "标准答案", width: 180, ellipsis: true, render: (_, q) => q.answer ? `${q.answer}${q.unit ?? ""}` : <Typography.Text type="secondary">无答案</Typography.Text> },
              { title: "状态", width: 110, render: (_, q) => {
                const tag = q.generator === "human"
                  ? <Tag color="purple">人工出题</Tag>
                  : <Tag color={ITEM_STATUS[q.status].color}>{ITEM_STATUS[q.status].text}</Tag>;
                return q.status === "rejected_auto" && q.notes.length ? <Tooltip title={q.notes.join("；")}>{tag}</Tooltip> : tag;
              } },
              { title: "操作", width: 170, render: (_, q) => !editable ? null : (
                <Space size={0} onClick={(e) => e.stopPropagation()}>
                  {q.status !== "rejected" && <Button size="small" type="link" onClick={() => setEditing({ item: q })}>修改</Button>}
                  {(q.status === "verified" || q.status === "approved") && (
                    <Button size="small" type="link" danger onClick={() => act(q.id, "delete")}>删除</Button>
                  )}
                  {(q.status === "rejected" || q.status === "rejected_auto") && (
                    <Button size="small" type="link" onClick={() => act(q.id, "restore")}>恢复</Button>
                  )}
                </Space>
              ) },
            ]} />
        </Card>
      )}
      <ItemEditor buildId={id} docs={d.docs_list.map((x) => x.name)} item={editing?.item ?? null} open={!!editing}
        onClose={() => setEditing(null)} onSaved={load} />
      <DocViewer load={loadDoc} name={viewer?.name ?? null} quote={viewer?.quote} onClose={() => setViewer(null)} />
    </>
  );
}
