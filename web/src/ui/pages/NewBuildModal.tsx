import { Alert, App, Collapse, Form, Input, Modal, Radio, Select, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import { useEffect, useState } from "react";

import { type CreateBuildBody, type UiSystem, api, downloadUrl } from "../api";
import { encodeFiles } from "../files";
import { MIX_ORDER } from "../mix";
import { go } from "../UiApp";
import MixEditor from "./MixEditor";

const DOC_ACCEPT = ".pdf,.docx,.pptx,.xlsx,.md,.markdown,.txt,.html,.htm";

type DocSource = "upload" | "kb";

/**
 * 新建测试集：两件事。文档从哪来（上传，或从 KnowFlow/RAGFlow 知识库取）；题目怎么来（导入自己的题、让模型出，可以都要）。
 * 只导入题、不传文档也行：那就只按答案判对错，不查引用。
 */
export default function NewBuildModal({ open, onClose, defaultMix }: { open: boolean; onClose: () => void; defaultMix: Record<string, number> }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const docSource: DocSource = Form.useWatch("doc_source", form) ?? "upload";
  const systemId: string | undefined = Form.useWatch("system_id", form);
  const mixValues: Record<string, number> | undefined = Form.useWatch("mix", form);
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [table, setTable] = useState<UploadFile[]>([]);
  const [systems, setSystems] = useState<UiSystem[]>([]);
  const [kbs, setKbs] = useState<{ id: string; name: string; documents: number }[]>([]);
  const [kbError, setKbError] = useState("");
  const [busy, setBusy] = useState(false);
  const kbSystems = systems.filter((s) => s.adapter !== "http");
  const total = MIX_ORDER.reduce((n, t) => n + Number(mixValues?.[t] ?? defaultMix[t] ?? 0), 0);
  const generate = total > 0;

  useEffect(() => {
    if (open) api.systems().then(setSystems).catch(() => setSystems([]));
  }, [open]);

  useEffect(() => {
    setKbs([]);
    setKbError("");
    form.setFieldValue("kb_ids", []);
    if (systemId) api.systemKbs(systemId).then(setKbs).catch((e) => setKbError(String(e.message ?? e)));
  }, [systemId, form]);

  /** 按选择拼出接口要的请求；缺东西时返回提示 */
  const request = async (v: Record<string, any>): Promise<CreateBuildBody | string> => {
    const mix = Object.fromEntries(MIX_ORDER.map((t) => [t, Number(v.mix?.[t] ?? 0)]));
    const myQuestions = table.length ? { table: (await encodeFiles(table))[0] } : {};
    const common = { name: v.name, ...myQuestions };
    if (docSource === "kb") {
      if (!generate && !table.length) return "让模型出题，或导入自己的题目";
      const kbNames = Object.fromEntries(kbs.filter((k) => v.kb_ids.includes(k.id)).map((k) => [k.id, k.name]));
      return { ...common, source: "kb", mix, system_id: v.system_id, kb_ids: v.kb_ids, kb_names: kbNames };
    }
    if (generate && !files.length) return table.length ? "让模型出题要先上传文档；只用自己的题，把题型配比全部填 0" : "上传文档，模型才能出题";
    if (!files.length && !table.length) return "上传文档或导入题目";
    const docs = await encodeFiles(files);
    // 只有自己的题（不让模型出）：按导入处理，文档全部带上
    if (!generate && table.length) return { ...common, source: "import", mix: {}, files: docs };
    return { ...common, source: "upload", mix, files: docs };
  };

  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      const body = await request(v);
      if (typeof body === "string") { message.warning(body); return; }
      const meta = await api.createBuild(body);
      message.success(generate ? (table.length ? "已开始导入你的题目，并让模型补题" : "已开始出题") : table.length ? "已开始导入题目" : "已开始导入文档");
      onClose();
      go({ page: "build", id: meta.id });
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="新建测试集" open={open} onCancel={onClose} onOk={submit} cancelText="取消" confirmLoading={busy} width={720} destroyOnClose
      okText={generate ? "开始出题" : table.length ? "导入" : "建测试集"} afterClose={() => { setFiles([]); setTable([]); }}>
      <Form form={form} layout="vertical" initialValues={{ doc_source: "upload", mix: defaultMix }}>
        <Form.Item name="name" label="测试集名称" rules={[{ required: true, message: "起个名字，在测试集列表和评测结果里显示" }]}>
          <Input placeholder="如 产品手册测试集" />
        </Form.Item>

        <Form.Item name="doc_source" label="① 文档" style={{ marginBottom: 12 }}>
          <Radio.Group optionType="button" options={[
            { value: "upload", label: "上传文档" },
            { value: "kb", label: "从 KnowFlow/RAGFlow 知识库取" },
          ]} />
        </Form.Item>
        {docSource === "upload" ? (
          <Form.Item extra="出题以原文为准，证据引文会逐字核对。只导入自己的题时可以不传，那就只按答案判对错、不查引用。">
            <Upload.Dragger multiple fileList={files} beforeUpload={() => false} onChange={({ fileList }) => setFiles(fileList)} accept={DOC_ACCEPT}>
              <p style={{ margin: "8px 0" }}>把文档拖到这里，或点击选择</p>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>PDF、Word、PPT、Excel、Markdown、TXT、HTML（扫描版 PDF 没有文字层，出不了题）</Typography.Text>
            </Upload.Dragger>
          </Form.Item>
        ) : (
          <>
            {kbSystems.length === 0 && <Alert type="info" showIcon style={{ marginBottom: 12 }} message="还没有接入 KnowFlow 或 RAGFlow 系统，先到「系统」页接入。" />}
            <Form.Item name="system_id" label="系统" rules={[{ required: true, message: "选一个系统" }]}>
              <Select options={kbSystems.map((s) => ({ value: s.id, label: s.name }))} placeholder="选系统" />
            </Form.Item>
            <Form.Item name="kb_ids" label="知识库（可多选）" rules={[{ required: true, type: "array", min: 1, message: "至少选一个知识库" }]}
              extra={kbError || "下载这些库里的原始文档来出题，不改动知识库。出完题，「测已有助手」会把这套题配给挂了这些库的助手。"}
              validateStatus={kbError ? "error" : undefined}>
              <Select mode="multiple" showSearch optionFilterProp="label" placeholder="选一个或多个知识库"
                options={kbs.map((k) => ({ value: k.id, label: `${k.name}（${k.documents} 份文档）` }))} />
            </Form.Item>
          </>
        )}

        <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>② 题目（两种可以都要，报告里按来源分开统计）</Typography.Text>
        <Form.Item label="导入我的题目（可选，CSV 或 JSONL）" style={{ marginBottom: 12 }}
          extra={<>一行一题：题目、答案必填（单位直接写在答案里；答案空着＝文档里没有答案，系统应当说不知道），文档、原文可选。
            <Typography.Link href={downloadUrl("/builds/import-template.csv")} style={{ marginLeft: 6 }}>下载模板</Typography.Link></>}>
          <Upload maxCount={1} fileList={table} beforeUpload={() => false} onChange={({ fileList }) => setTable(fileList.slice(-1))} accept=".csv,.jsonl,.ndjson">
            <a>选择文件</a>
          </Upload>
        </Form.Item>
        <Collapse size="small" items={[{
          key: "mix", forceRender: true,
          label: generate ? `让模型出题：约 ${total} 道（展开调题型配比）` : "不让模型出题（展开调题型配比）",
          children: (
            <>
              <Form.Item name="mix" noStyle><MixEditor /></Form.Item>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "8px 0 0" }}>
                全部填 0 就不调模型：只用你导入的题，或建好后在测试集页手动加题。模型出的题和你的题重复的会被去掉。
              </Typography.Paragraph>
            </>
          ),
        }]} />
      </Form>
    </Modal>
  );
}
