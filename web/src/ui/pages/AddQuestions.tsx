import { App, Button, Modal, Space, Tooltip, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import { useEffect, useState } from "react";

import { api, downloadUrl } from "../api";
import { encodeFiles } from "../files";
import { mixTotal, normalizeMix } from "../mix";
import MixEditor from "./MixEditor";

/**
 * 已有测试集继续加题：再导入一批（CSV/JSONL），或者让模型按配比补一批。
 * 补的题只校验新出的；和已有题重复的会被打回（导入的、手动加的优先保留）。没有文档的测试集不能让模型补题。
 */
export default function AddQuestions({ buildId, hasDocs, onDone }: { buildId: string; hasDocs: boolean; onDone: () => void }) {
  const { message } = App.useApp();
  const [importing, setImporting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [table, setTable] = useState<UploadFile[]>([]);
  const [mix, setMix] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (generating) api.builds().then((b) => setMix(normalizeMix(b.default_mix))).catch(() => undefined);
  }, [generating]);

  const doImport = async () => {
    if (!table.length) { message.warning("先选题目文件"); return; }
    setBusy(true);
    try {
      const meta = await api.importMore(buildId, (await encodeFiles(table))[0]);
      const r = meta.import_report;
      message.success(`已导入${r?.problems?.length ? `，有 ${r.problems.length} 行用不了，见页面上的说明` : ""}`);
      setImporting(false);
      setTable([]);
      onDone();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const doGenerate = async () => {
    setBusy(true);
    try {
      await api.generateMore(buildId, normalizeMix(mix));
      message.success("已开始补题，出完会自动校验新出的题");
      setGenerating(false);
      onDone();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button onClick={() => setImporting(true)}>导入题目</Button>
      <Tooltip title={hasDocs ? undefined : "这套题没有文档，模型没法出题：只导入了题目的测试集请在新建时上传文档"}>
        <Button disabled={!hasDocs} onClick={() => setGenerating(true)}>让模型补题</Button>
      </Tooltip>

      <Modal title="导入题目" open={importing} onCancel={() => setImporting(false)} onOk={doImport} okText="导入" cancelText="取消"
        confirmLoading={busy} destroyOnClose>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            追加到这套测试集里，原来的题不动。一行一题：题目、答案必填（单位直接写在答案里；答案空着＝文档里没有答案），文档、原文可选；
            带了原文的会到这套题的文档里逐字核对。
            <Typography.Link href={downloadUrl("/builds/import-template.csv")} style={{ marginLeft: 6 }}>下载模板</Typography.Link>
          </Typography.Text>
          <Upload maxCount={1} fileList={table} beforeUpload={() => false} accept=".csv,.jsonl,.ndjson"
            onChange={({ fileList }) => setTable(fileList.slice(-1))}>
            <Button>选择题目文件（CSV 或 JSONL）</Button>
          </Upload>
        </Space>
      </Modal>

      <Modal title="让模型补题" open={generating} onCancel={() => setGenerating(false)} onOk={doGenerate} cancelText="取消"
        okText={`补约 ${mixTotal(mix)} 道`} okButtonProps={{ disabled: !mixTotal(mix) }} confirmLoading={busy} width={640} destroyOnClose>
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          按题型配比从这套题的文档里再出一批，出完只校验新出的题；和已有题重复的会被打回，你导入、手动加、改过的题都不动。
        </Typography.Paragraph>
        <MixEditor value={mix} onChange={setMix} />
      </Modal>
    </>
  );
}
