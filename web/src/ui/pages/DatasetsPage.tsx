import { Alert, App, Button, Card, Empty, Popconfirm, Skeleton, Space, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { type BuildSummary, type DatasetInfo, api } from "../api";
import { go } from "../UiApp";
import { BUILD_STATUS, sourceText } from "./BuildDetailPage";
import NewBuildModal from "./NewBuildModal";
import PageHead from "./PageHead";

/** 列表里的一行：评测台出的测试集，或现成的数据集（官方数据集） */
interface Row {
  key: string;
  name: string;
  official: boolean;
  source: string;
  questions: number;
  status?: BuildSummary["status"];
  usable: boolean;
  open: () => void;
  evaluate: () => void;
  remove: () => Promise<void>;
  removeNote: string;
}

export default function DatasetsPage() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<DatasetInfo[] | null>(null);
  const [error, setError] = useState("");
  const [builds, setBuilds] = useState<BuildSummary[]>([]);
  const [defaultMix, setDefaultMix] = useState<Record<string, number>>({});
  const [creating, setCreating] = useState(false);
  const load = () => {
    api.datasets().then(setRows).catch((e) => setError(String(e.message ?? e)));
    api.builds().then((b) => { setBuilds(b.builds); setDefaultMix(b.default_mix); }).catch(() => setBuilds([]));
  };
  useEffect(load, []);

  const run = async (action: () => Promise<unknown>, done: string) => {
    try {
      await action();
      message.success(done);
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  const list: Row[] = [
    ...builds.map((b) => ({
      key: b.id, name: b.name, official: false, source: sourceText(b), questions: b.passed, status: b.status,
      usable: b.status === "ready" && b.passed > 0,
      open: () => go({ page: "build", id: b.id }),
      evaluate: () => go({ page: "new", testset: `build:${b.id}` }),
      remove: () => run(() => api.deleteBuild(b.id), "已删除"),
      removeNote: "题目和上传的文档会一起删掉，不能恢复。已有评测各自存了题目快照，不受影响。",
    })),
    ...(rows ?? []).map((d) => ({
      key: d.path, name: d.name, official: true, source: `${d.docs} 份文档（${d.formats.join(" / ")}）`, questions: d.questions,
      usable: d.questions > 0,
      open: () => go({ page: "dataset", path: d.path }),
      evaluate: () => go({ page: "new", testset: `dataset:${d.path}` }),
      remove: () => run(async () => {
        const r = await api.deleteDataset(d.path);
        if (r.evals_deleted) message.info(`用到它的 ${r.evals_deleted} 个评测也一并删除了`);
      }, "已删除"),
      removeNote: d.used_by.length
        ? `题目和文档会一起删掉，不能恢复。有 ${d.used_by.length} 个评测用到它（${d.used_by.join("、")}），评测结果也会一并删除。`
        : "题目和文档会一起删掉，不能恢复。",
    })),
  ];

  return (
    <>
      <PageHead title="测试集" desc="测试集就是题目加文档：用自己的文档出题、导入已有题目，或用官方数据集。出完就能评测，评测时会把当时的题目存一份快照。"
        extra={<Button type="primary" onClick={() => setCreating(true)}>新建测试集</Button>} />
      <NewBuildModal open={creating} onClose={() => setCreating(false)} defaultMix={defaultMix} />
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      {rows === null ? <Skeleton active /> : !list.length ? (
        <Card><Empty description="还没有测试集"><Button type="primary" onClick={() => setCreating(true)}>新建测试集</Button></Empty></Card>
      ) : (
        <Card size="small">
          <Table<Row> size="middle" rowKey="key" dataSource={list} pagination={false}
            onRow={(r) => ({ onClick: r.open, style: { cursor: "pointer" } })}
            columns={[
              { title: "测试集", render: (_, r) => (
                <Space size={6} wrap>
                  <Typography.Text strong>{r.name}</Typography.Text>
                  {r.official && <Tag>官方</Tag>}
                  {r.status && r.status !== "ready" && <Tag color={BUILD_STATUS[r.status].color}>{BUILD_STATUS[r.status].text}</Tag>}
                </Space>
              ) },
              { title: "文档与题目来源", render: (_, r) => <Typography.Text type="secondary">{r.source}</Typography.Text> },
              { title: "可用题数", width: 100, align: "right", dataIndex: "questions" },
              { title: "", width: 190, align: "right", render: (_, r) => (
                <Space size={0} onClick={(e) => e.stopPropagation()}>
                  <Button type="link" disabled={!r.usable} onClick={r.evaluate}>评测</Button>
                  <Button type="link" onClick={r.open}>查看</Button>
                  <Popconfirm title={`删除测试集「${r.name}」？`} description={r.removeNote} okText="删除" cancelText="取消"
                    okButtonProps={{ danger: true }} onConfirm={r.remove} overlayStyle={{ maxWidth: 360 }}>
                    <Button type="link" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ) },
            ]} />
        </Card>
      )}
    </>
  );
}
