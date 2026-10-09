import { Alert, App, Button, Card, Col, Empty, Form, Input, Modal, Popconfirm, Radio, Row, Select, Skeleton, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import { type Adapter, type UiConfig, type UiSystem, api } from "../api";
import { DEFAULT_HTTP_FORM, EMPTY_HTTP_FORM, type HttpForm, checkHttpForm, fromHttpConfig, toHttpConfig } from "../httpTemplate";
import { ADAPTER_LABEL, configSummary, parseJsonObject } from "../logic";
import { configFormInitial } from "../paramDefaults";
import HttpTemplateFields from "./HttpTemplateFields";
import JsonArea from "./JsonArea";
import PageHead from "./PageHead";

const KEY_ENV: Record<Adapter, string> = { knowflow: "KNOWFLOW_API_KEY", ragflow: "RAGFLOW_API_KEY", http: "MY_RAG_API_KEY" };
const EDIT_NOTE = "修改只影响之后新建的评测；已建的评测在创建时就把配置存了快照，不受影响。";

/** 自研系统回填表单：优先用保存时存下的原始填写；早先用配置文件接入的没有，就从配置还原 */
function initialTemplate(s: UiSystem | null): HttpForm {
  if (!s || s.adapter !== "http") return EMPTY_HTTP_FORM;
  const saved = (s.http_form ?? {}) as Partial<HttpForm> & { preset?: string };
  // 以前用「标准接口」「OpenAI 兼容」预设接入的：预设已去掉，从实际配置还原成普通请求
  if (Object.keys(saved).length && (!saved.preset || saved.preset === "custom")) return { ...DEFAULT_HTTP_FORM, ...saved };
  return fromHttpConfig(s.http as Record<string, any>);
}

/** 接入或修改系统：editing 为空是接入，有值是修改（类型不能改） */
function SystemModal({ open, editing, onClose, onDone }: {
  open: boolean;
  editing: UiSystem | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const adapter: Adapter = Form.useWatch("adapter", form) ?? editing?.adapter ?? "knowflow";
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      const raw: HttpForm = { ...DEFAULT_HTTP_FORM, ...form.getFieldValue("tpl") };
      // 点选了引用片段列表就按列表取引用；以前配的正文标记保留
      const tpl: HttpForm = { ...raw, citationMode: raw.citationMode === "marker" ? "marker" : raw.citationsPath ? "list" : "none" };
      if (adapter === "http") {
        const problem = checkHttpForm(tpl);
        if (problem) throw new Error(problem);
      }
      const payload = {
        name: v.name, base_url: v.base_url, api_key_env: v.api_key_env,
        version: v.version || null, image_base_url: v.image_base_url || null,
        http: adapter === "http" ? toHttpConfig(tpl) : {},
        http_form: adapter === "http" ? (tpl as unknown as Record<string, unknown>) : {},
      };
      if (editing) await api.updateSystem(editing.id, payload);
      else await api.addSystem({ ...payload, adapter });
      message.success(editing ? "已保存" : "已接入");
      onDone();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const initial = editing
    ? { adapter: editing.adapter, name: editing.name, base_url: editing.base_url, api_key_env: editing.api_key_env,
        version: editing.version ?? "", image_base_url: editing.image_base_url ?? "", tpl: initialTemplate(editing) }
    : { adapter: "knowflow", api_key_env: "KNOWFLOW_API_KEY", base_url: "http://localhost:9380", tpl: EMPTY_HTTP_FORM };

  return (
    <Modal title={editing ? `修改系统 · ${editing.name}` : "接入系统"} open={open} onCancel={onClose} onOk={submit}
      okText={editing ? "保存" : "接入"} cancelText="取消" confirmLoading={busy} width={adapter === "http" ? 1000 : 720} destroyOnClose>
      {editing && <Alert type="info" showIcon message={EDIT_NOTE} style={{ marginBottom: 16 }} />}
      <Form key={editing?.id ?? "new"} form={form} layout="vertical" initialValues={initial}
        onValuesChange={(changed) => {
          // 换类型时把还停在上一种默认值上的密钥变量名一起换掉，用户改过的不动
          if (!changed.adapter) return;
          const current = form.getFieldValue("api_key_env");
          if (Object.values(KEY_ENV).includes(current)) form.setFieldValue("api_key_env", KEY_ENV[changed.adapter as Adapter]);
        }}>
        <Form.Item name="adapter" label="类型" tooltip={editing ? "类型不能修改，换类型请新接入一个系统" : undefined}>
          <Radio.Group optionType="button" disabled={!!editing}
            options={(Object.keys(ADAPTER_LABEL) as Adapter[]).map((a) => ({ value: a, label: ADAPTER_LABEL[a] }))} />
        </Form.Item>
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item name="name" label="名称" rules={[{ required: true, message: "起个名字，评测里用它区分系统" }]}>
              <Input placeholder="如 自研问答-测试环境" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8} style={adapter === "http" ? { display: "none" } : undefined}>
            <Form.Item name="base_url" label="服务地址" rules={[{ required: adapter !== "http" }]}>
              <Input />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="api_key_env" label="API Key 环境变量" rules={[{ required: true }]} tooltip="密钥只从环境变量读，不写进任何文件">
              <Input />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="version" label="版本（可不填）" tooltip="写进结果，方便日后对照">
              <Input />
            </Form.Item>
          </Col>
          <Col xs={24} md={16} style={adapter === "http" ? { display: "none" } : undefined}>
            <Form.Item name="image_base_url" label="图片地址前缀（可不填）"
              tooltip="回答里相对地址的图片从哪里下载；KnowFlow 的 /minio 图片走网页端口，例如 http://localhost">
              <Input />
            </Form.Item>
          </Col>
        </Row>
        {adapter === "http" && <HttpTemplateFields form={form} />}
      </Form>
    </Modal>
  );
}


/** 添加或修改配置：config 为空是添加 */
function ConfigModal({ target, onClose, onDone }: {
  target: { system: UiSystem; config: UiConfig | null } | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const system = target?.system;
  const editing = target?.config ?? null;
  const isRagflowLike = system?.adapter !== "http";

  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      const body = { name: v.name, dataset: parseJsonObject(v.dataset ?? ""), chat: parseJsonObject(v.chat ?? ""),
        api_key_env: v.api_key_env?.trim() || null };
      if (editing) await api.updateConfig(system!.id, editing.id, body);
      else await api.addConfig(system!.id, body);
      message.success(editing ? "已保存" : "已添加配置");
      onDone();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  // 新建配置时把不填也会生效的默认参数全写出来，用户照着改
  const initial = system ? configFormInitial(system, editing) : {};
  const httpIngest = !!(system?.http as Record<string, unknown> | undefined)?.ingest;

  return (
    <Modal title={editing ? `修改配置 · ${editing.name}` : `为 ${system?.name ?? ""} 添加配置`} open={!!target} onCancel={onClose} onOk={submit}
      okText={editing ? "保存" : "添加"} cancelText="取消" confirmLoading={busy} width={720} destroyOnClose
      afterOpenChange={(open) => open && form.resetFields()}>
      <Typography.Paragraph type="secondary">{isRagflowLike
        ? "一份配置就是一组建库参数（分块方式、嵌入模型等）和助手参数，起个名字。「完整测试」时按它新建知识库和助手，勾几份就比几种建库方式。评测台不理解这些参数，只原样传过去、原样记录。测已有助手不用加配置，在新建评测里直接选助手。"
        : "一份配置就是一组接口参数，起个名字。参数深合并进请求体；要放进路径或请求头的，在接口模板里写 {{chat.参数名}}、{{dataset.参数名}}。评测台不理解这些参数，只原样传过去、原样记录。"}</Typography.Paragraph>
      {editing && <Alert type="info" showIcon message={EDIT_NOTE} style={{ marginBottom: 16 }} />}
      <Form key={editing?.id ?? "new"} form={form} layout="vertical" initialValues={initial}>
        <Form.Item name="name" label="配置名称" rules={[{ required: true, message: "起个名字，例如 MinerU + smart" }]}>
          <Input />
        </Form.Item>
        {isRagflowLike && (
          <>
            <Form.Item name="dataset" label="建库参数（JSON）"
              extra={system?.adapter === "knowflow" ? "KnowFlow 建库必须给 embedding_model；分块方式写法如 smart、parent_child（下划线）" : undefined}>
              <JsonArea />
            </Form.Item>
            <Form.Item name="chat" label="建助手参数（JSON）" extra="预填的是不改时的默认值。ColPali 要把 llm.model_name 换成视觉模型；SAG 还要在建库参数里打开 parser_config.sag.use_sag">
              <JsonArea />
            </Form.Item>
          </>
        )}
        {!isRagflowLike && (
          <>
            {httpIngest && (
              <Form.Item name="dataset" label="建库参数（JSON，可不填）" extra="合并进建库请求体，例如分块大小、解析方式">
                <JsonArea />
              </Form.Item>
            )}
            <Form.Item name="chat" label="问答参数（JSON）" extra='合并进每次提问的请求体，例如 {"top_k": 10, "rerank": true}'>
              <JsonArea />
            </Form.Item>
          </>
        )}
        {/* KnowFlow/RAGFlow 一把 Key 能看到账号下所有助手和知识库，用系统的就够；自研系统可能一个应用一把 Key */}
        {!isRagflowLike && (
          <Form.Item name="api_key_env" label="API Key 环境变量（可不填）"
            extra={`不填用系统的 ${system?.api_key_env ?? ""}。一个应用一把 Key 的系统（如 Dify），换应用就填另一个环境变量名，Key 写在 .env 里`}>
            <Input placeholder={system?.api_key_env} />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}

/** 自研系统的接口摘要 */
function HttpSummary({ system }: { system: UiSystem }) {
  const http = system.http as { ask?: { method?: string; path?: string; answer?: string; citations?: string }; ingest?: unknown };
  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
      <Typography.Text style={{ fontSize: 13 }}>
        提问接口：<Typography.Text code>{http.ask?.method ?? "POST"} {http.ask?.path}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>　答案取自 {http.ask?.answer}{http.ask?.citations ? `，引用取自 ${http.ask.citations}` : ""}</Typography.Text>
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {http.ingest ? "接了建库、上传接口：评测台可以自动入库" : "只接了问答接口：测试集的文档需要自己导入对方系统"}
      </Typography.Text>
    </div>
  );
}

export default function SystemsPage() {
  const { message } = App.useApp();
  const [systems, setSystems] = useState<UiSystem[] | null>(null);
  const [error, setError] = useState("");
  const [systemModal, setSystemModal] = useState<{ editing: UiSystem | null } | null>(null);
  const [configTarget, setConfigTarget] = useState<{ system: UiSystem; config: UiConfig | null } | null>(null);

  const load = useCallback(() => api.systems().then(setSystems).catch((e) => setError(String(e.message ?? e))), []);
  useEffect(() => { load(); }, [load]);
  // 起了演示服务（Docker --profile demo）时，可以一键接入演示系统先体验
  const [demo, setDemo] = useState<{ available: boolean; url?: string } | null>(null);
  useEffect(() => { api.demoInfo().then(setDemo).catch(() => setDemo(null)); }, []);
  const setupDemo = () => run(() => api.setupDemo(), "已接入演示系统：新建评测 → 完整测试，选 demo-md 测试集和「演示 RAG」");
  const demoMissing = !!demo?.available && !!systems && !systems.some((s) => s.name === "演示 RAG");

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      if (ok) message.success(ok);
      load();
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  const test = async (s: UiSystem) => {
    try {
      const r = await api.testSystem(s.id);
      r.ok ? message.success(r.message) : message.warning(r.message);
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  return (
    <>
      <PageHead title="系统"
        desc="接入要评测的系统。评测台只认它的问答接口（和可选的建库上传接口），系统内部怎么解析、检索、用什么模型，都在对方自己那边配。一个系统可以存多份配置，评测时勾选比较。"
        extra={<Space>
          {demoMissing && <Button onClick={setupDemo}>一键接入演示系统</Button>}
          <Button type="primary" onClick={() => setSystemModal({ editing: null })}>接入系统</Button>
        </Space>} />
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      {systems === null ? <Skeleton active /> : systems.length === 0 ? (
        <Card><Empty description={demoMissing ? "还没有接入任何系统。没有自己的 RAG 系统，可以先接入内置的演示系统把流程跑一遍" : "还没有接入任何系统"}>
          <Space>
            {demoMissing && <Button onClick={setupDemo}>一键接入演示系统</Button>}
            <Button type="primary" onClick={() => setSystemModal({ editing: null })}>接入系统</Button>
          </Space>
        </Empty></Card>
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {systems.map((s) => (
            <Card key={s.id} size="small"
              title={<Space wrap><span>{s.name}</span><Tag>{ADAPTER_LABEL[s.adapter]}</Tag>{s.version && <Typography.Text type="secondary">v{s.version}</Typography.Text>}</Space>}
              extra={<Space>
                <Button size="small" onClick={() => test(s)}>测试连通性</Button>
                <Button size="small" onClick={() => setSystemModal({ editing: s })}>修改</Button>
                <Popconfirm title={`删除系统 ${s.name}？`} description="已建的评测不受影响（配置在建评测时已快照）。" okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                  onConfirm={() => run(() => api.deleteSystem(s.id), "已删除")}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>{s.base_url} · API Key 环境变量 {s.api_key_env}</Typography.Text>
              {s.adapter === "http" && <HttpSummary system={s} />}
              <div style={{ marginTop: 8 }}>
                {s.configs.length === 0 && <Typography.Text type="secondary">{s.adapter === "http"
                  ? "还没有配置：评测时原样用接口模板。想比较不同参数（如 top_k、是否重排），加几份配置。"
                  : "还没有建库配置。测已有助手不需要配置，新建评测时直接选助手；想让评测台建库、比较分块方式时再加。"}</Typography.Text>}
                {s.configs.map((c) => (
                  <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", padding: "8px 0", borderBottom: "1px solid #f0f0f0" }}>
                    <Typography.Text strong style={{ minWidth: 180 }}>{c.name}</Typography.Text>
                    <Typography.Text type="secondary" style={{ flex: "1 1 300px", fontSize: 13 }}>{configSummary(c)}</Typography.Text>
                    <Space size={0}>
                      <Button size="small" type="text" onClick={() => setConfigTarget({ system: s, config: c })}>修改</Button>
                      <Popconfirm title={`删除配置 ${c.name}？`} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                        onConfirm={() => run(() => api.deleteConfig(s.id, c.id), "已删除")}>
                        <Button size="small" type="text" danger>删除</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                ))}
              </div>
              <Button size="small" type="dashed" style={{ marginTop: 12 }} onClick={() => setConfigTarget({ system: s, config: null })}>+ 添加{s.adapter === "http" ? "配置" : "建库配置"}</Button>
            </Card>
          ))}
        </Space>
      )}
      <SystemModal open={!!systemModal} editing={systemModal?.editing ?? null}
        onClose={() => setSystemModal(null)} onDone={() => { setSystemModal(null); load(); }} />
      <ConfigModal target={configTarget} onClose={() => setConfigTarget(null)} onDone={() => { setConfigTarget(null); load(); }} />
    </>
  );
}
