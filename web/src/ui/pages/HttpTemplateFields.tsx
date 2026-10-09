import { Card, Form, Select, Space, Switch, Typography } from "antd";
import type { FormInstance } from "antd";
import { useState } from "react";

import type { HttpForm } from "../httpTemplate";
import CurlBox from "./CurlImport";
import ProbePanel, { type ProbeContext } from "./ProbePanel";

const P = (k: keyof HttpForm) => ["tpl", k];

function Step({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title={<Space><span>{title}</span><Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>{desc}</Typography.Text></Space>}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>{children}</Space>
    </Card>
  );
}

/** 自研系统接入：每个接口贴一条 curl，再真实调一次，在返回结果里点出要取的字段 */
export default function HttpTemplateFields({ form }: { form: FormInstance }) {
  // tpl 里大多数字段没有输入框（由 curl 解析和点选写入），要 preserve 才监听得到
  const tpl: HttpForm | undefined = Form.useWatch("tpl", { form, preserve: true });
  const [kbId, setKbId] = useState("");
  const [docId, setDocId] = useState("");
  const ctx: ProbeContext = { kbId, docId, setKbId, setDocId };
  const auto = tpl?.mode === "auto";

  return (
    <>
      <Typography.Paragraph type="secondary">
        从 Postman、Apifox 或浏览器开发者工具复制出能跑通的 curl 贴进来。密钥会换成占位符、不会保存，运行评测台前把它设到上面填的环境变量里。
      </Typography.Paragraph>

      <Step title="提问接口" desc="必填。每道题都应是新会话，不要带对话历史">
        <CurlBox form={form} step="ask" ids={{ kbId, docId }} />
        {tpl?.askPath && (
          <ProbePanel form={form} step="ask" ctx={ctx} targets={[
            { field: "askAnswerPath", label: "答案（必选）", kind: "value" },
            { field: "citationsPath", label: "引用片段列表（可选）", kind: "list" },
            { field: "citationDoc", label: "片段·文档名", kind: "item", parent: "citationsPath" },
            { field: "citationText", label: "片段·正文", kind: "item", parent: "citationsPath" },
          ]} />
        )}
      </Step>

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space align="start">
          <Form.Item name={P("mode")} noStyle getValueProps={(v) => ({ checked: v === "auto" })} getValueFromEvent={(on: boolean) => (on ? "auto" : "manual")}>
            <Switch />
          </Form.Item>
          <div>
            <Typography.Text>对方还有建库、上传接口（可选）</Typography.Text>
            <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>
              开了之后评测台可以替你把测试集的文档自动入库；不开就自己把文档导入对方系统（测试集页可以下载文档包）。
            </Typography.Text></div>
          </div>
        </Space>
      </Card>

      {auto && (
        <>
          <Step title="建库接口" desc="每次入库建一个新库">
            <CurlBox form={form} step="create_kb" />
            {tpl?.createPath && (
              <ProbePanel form={form} step="create_kb" ctx={ctx} warning="会真的在对方系统里建一个名为 knoweval-probe 的库，试完可以在对方那边删掉。"
                targets={[{ field: "createIdPath", label: "新库编号（必选）", kind: "value" }]} />
            )}
          </Step>
          <Step title="上传接口" desc="每份文档发一次 multipart 请求">
            <CurlBox form={form} step="upload" ids={{ kbId, docId }} />
            {tpl?.uploadPath && (
              <ProbePanel form={form} step="upload" ctx={ctx} warning="上传一份很小的测试文档（knoweval-probe.md）到刚才试调建出的库。"
                targets={[{ field: "uploadDocIdPath", label: "文档编号（查进度时要用）", kind: "value" }]} />
            )}
          </Step>
          <Step title="查解析进度接口" desc="可选。对方是异步解析时要接，否则可能还没解析完就开始提问">
            <Form.Item name={P("statusEnabled")} valuePropName="checked" noStyle><Switch size="small" /></Form.Item>
            {tpl?.statusEnabled && (
              <>
                <CurlBox form={form} step="status" ids={{ kbId, docId }} />
                {tpl?.statusPath && (
                  <>
                    <ProbePanel form={form} step="status" ctx={ctx} targets={[{ field: "statusStatePath", label: "状态字段（必选）", kind: "value" }]} />
                    <Space wrap>
                      <Form.Item name={P("statusDone")} label="哪些值算解析完成" style={{ marginBottom: 0, minWidth: 260 }}><Select mode="tags" tokenSeparators={[",", "，"]} /></Form.Item>
                      <Form.Item name={P("statusFailed")} label="哪些值算解析失败" style={{ marginBottom: 0, minWidth: 260 }}><Select mode="tags" tokenSeparators={[",", "，"]} /></Form.Item>
                    </Space>
                  </>
                )}
              </>
            )}
          </Step>
        </>
      )}
    </>
  );
}
