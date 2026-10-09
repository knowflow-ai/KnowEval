import { Alert, App, Button, Col, Collapse, Drawer, Empty, Form, Input, InputNumber, Row, Select, Skeleton, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import { QTYPE_NAME } from "../../data";
import { type BuildItem, api } from "../api";
import { findQuote } from "../logic";

/** 人工能出的题型；需要图片证据的 V、I 表单暂不支持 */
const QTYPES = ["F", "T", "L", "C", "M", "X", "D", "K", "G", "N"];
/** 答案以数字开头（可带单位）就是数值题：后端按同样的规则推断类型，只有数值题才有「允许误差」 */
const looksNumeric = (answer?: string) => /^\s*[-+]?\d[\d,]*(\.\d+)?(\s*\D.*)?$/.test(answer ?? "") && !/^\d{4}(-\d{2}){0,2}$/.test((answer ?? "").trim());

type Evidence = { doc: string; quote: string };

interface FormValues {
  qtype: string;
  question: string;
  answer?: string;
  tolerance?: number;
  answer_aliases?: string[];
}

/** 原文：按原样显示，划选一段文字后可以设为证据；已选的证据高亮第一处 */
function DocPane({ buildId, docs, evidence, onPick }: {
  buildId: string;
  docs: string[];
  evidence: Evidence[];
  onPick: (ev: Evidence) => void;
}) {
  const [doc, setDoc] = useState(docs[0]);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!doc) return;
    setText(null);
    setError("");
    setSelection("");
    api.buildDoc(buildId, doc).then((d) => setText(d.text ?? "")).catch((e) => setError(String(e.message ?? e)));
  }, [buildId, doc]);

  const readSelection = () => {
    const sel = window.getSelection();
    const inside = sel && box.current && sel.anchorNode && box.current.contains(sel.anchorNode);
    setSelection(inside ? sel.toString().trim() : "");
  };

  const marked = evidence.find((e) => e.doc === doc);
  const hit = text && marked ? findQuote(text, marked.quote) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select value={doc} onChange={setDoc} style={{ minWidth: 260 }} showSearch
          options={docs.map((d) => ({ value: d, label: d }))} />
        <Button type="primary" disabled={!selection} onClick={() => { onPick({ doc, quote: selection }); setSelection(""); window.getSelection()?.removeAllRanges(); }}>
          把选中的文字设为证据
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {selection ? `已选 ${selection.length} 字` : "在下面的原文里拖动鼠标选中答案所在的那段话"}
        </Typography.Text>
      </Space>
      {error && <Alert type="error" showIcon message={error} />}
      <div ref={box} onMouseUp={readSelection}
        style={{ flex: 1, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, padding: "12px 16px", background: "#fff" }}>
        {text === null && !error ? <Skeleton active /> : (
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.8, margin: 0 }}>
            {hit ? (
              <>
                {text!.slice(0, hit[0])}
                <mark style={{ background: "#ffe58f", padding: "0 2px" }}>{text!.slice(hit[0], hit[1])}</mark>
                {text!.slice(hit[1])}
              </>
            ) : text}
          </Typography.Paragraph>
        )}
      </div>
    </div>
  );
}

/** 人工出题 / 改题：左边填题目和答案，右边看原文、划选证据 */
export default function ItemEditor({ buildId, docs, item, open, onClose, onSaved }: {
  buildId: string;
  docs: string[];
  /** 为空是新加一道题 */
  item: BuildItem | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [saving, setSaving] = useState(false);
  const qtype = Form.useWatch("qtype", form);
  const answerValue: string | undefined = Form.useWatch("answer", form);
  const noAnswer = qtype === "N";

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    if (item) {
      form.setFieldsValue({
        // 表单里没有单独的单位：拼回答案里显示，保存时后端再拆开（不会丢单位）
        qtype: item.qtype[0], question: item.question, answer: item.unit ? `${item.answer} ${item.unit}` : item.answer,
        tolerance: item.tolerance ?? undefined, answer_aliases: item.answer_aliases ?? [],
      });
      setEvidence(item.evidence.map((e) => ({ doc: e.doc, quote: e.quote })));
    } else {
      setEvidence([]);
    }
  }, [open, item, form]);

  const save = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const body = { ...v, evidence: noAnswer ? [] : evidence };
      await api.saveBuildItem(buildId, item?.id ?? null, body);
      message.success(item ? "已保存修改" : "已加入这道题");
      onSaved();
      onClose();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer open={open} onClose={onClose} width="min(1200px, 96vw)" destroyOnClose
      title={item ? `修改 ${item.id}` : "手动加题"}
      extra={<Space><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={save}>保存</Button></Space>}
      styles={{ body: { paddingBottom: 16 } }}>
      <Row gutter={24} style={{ height: "100%" }}>
        <Col span={10} style={{ height: "100%", overflow: "auto" }}>
          <Form form={form} layout="vertical" initialValues={{ qtype: "F" }}>
            <Form.Item name="qtype" label="题型" rules={[{ required: true }]}>
              <Select options={QTYPES.map((t) => ({ value: t, label: `${t} ${QTYPE_NAME[t] ?? ""}` }))} />
            </Form.Item>
            <Form.Item name="question" label="题目" rules={[{ required: true, whitespace: true, message: "写下要问的问题" }]}>
              <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} placeholder="按真实用户的问法写，不必照抄原文措辞" />
            </Form.Item>
            {noAnswer ? (
              <Alert type="info" showIcon style={{ marginBottom: 16 }}
                message="无答案题：文档里找不到答案，系统应当说不知道。不用填答案和证据。" />
            ) : (
              <>
                <Form.Item name="answer" label="标准答案" rules={[{ required: true, whitespace: true, message: "填标准答案" }]}
                  extra="单位直接写在答案里，如「10 天」「358.6 亿元」；日期写成 2024、2024-03 或 2024-03-31。不同写法、全称简称、单位换算，判分的模型都认得">
                  <Input />
                </Form.Item>
                {/* 判分交给裁判模型后，这几项一般不用填；只有要明确误差、或有裁判不可能知道的说法（内部代号）时才用 */}
                {/* 默认折叠；这道题原本就填了误差或其它说法时才展开 */}
                <Collapse size="small" ghost style={{ marginBottom: 12 }} defaultActiveKey={
                  item?.tolerance || item?.answer_aliases?.length ? ["advanced"] : []} items={[{
                  key: "advanced", label: <Typography.Text type="secondary">高级：允许误差、其它算对的说法</Typography.Text>,
                  forceRender: true,
                  children: (
                    <>
                      {looksNumeric(answerValue) && (
                        <Form.Item name="tolerance" label="允许误差" extra="和标准答案相差不超过这个数就算对，不填为 0（比如答「约 35 亿」算不算对）">
                          <InputNumber min={0} style={{ width: "100%" }} />
                        </Form.Item>
                      )}
                      <Form.Item name="answer_aliases" label="其它算对的说法" extra="一般不用填。只有判分模型不可能知道的说法才需要，比如内部代号、产品旧名。输入后回车，可以填多个">
                        <Select mode="tags" open={false} tokenSeparators={["，", ","]} placeholder="如 内部代号" />
                      </Form.Item>
                    </>
                  ),
                }]} />
                <Typography.Text strong>证据</Typography.Text>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "2px 0 8px" }}>
                  在右边原文里选中答案所在的那段话，点「把选中的文字设为证据」。保存时会逐字核对。要用到几份文档就加几条。
                </Typography.Paragraph>
                {evidence.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有证据" style={{ margin: "8px 0" }} /> : (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    {evidence.map((ev, i) => (
                      <div key={`${ev.doc}-${i}`} style={{ background: "#fafafa", borderRadius: 6, padding: "8px 12px" }}>
                        <Space style={{ width: "100%", justifyContent: "space-between" }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>证据 {i + 1} · {ev.doc}</Typography.Text>
                          <Button size="small" type="link" danger onClick={() => setEvidence(evidence.filter((_, j) => j !== i))}>移除</Button>
                        </Space>
                        <div style={{ fontSize: 13 }}>{ev.quote}</div>
                      </div>
                    ))}
                  </Space>
                )}
              </>
            )}
          </Form>
        </Col>
        <Col span={14} style={{ height: "calc(100vh - 140px)" }}>
          {docs.length ? (
            <DocPane buildId={buildId} docs={docs} evidence={evidence}
              onPick={(ev) => setEvidence(evidence.some((e) => e.doc === ev.doc && e.quote === ev.quote) ? evidence : [...evidence, ev])} />
          ) : <Empty description="这个测试集没有文档" />}
        </Col>
      </Row>
    </Drawer>
  );
}
