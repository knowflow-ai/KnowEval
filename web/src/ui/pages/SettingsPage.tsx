import { Alert, App, Button, Card, Form, Input, Skeleton, Space, Switch, Typography } from "antd";
import { useEffect, useState } from "react";

import { type JudgeSettings, api } from "../api";
import PageHead from "./PageHead";

export default function SettingsPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm<JudgeSettings>();
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.settings().then((s) => { form.setFieldsValue(s); setLoaded(true); }).catch((e) => setError(String(e.message ?? e)));
  }, [form]);

  const save = async () => {
    setBusy(true);
    try {
      await api.saveSettings(await form.validateFields());
      message.success("已保存");
    } catch (e: any) {
      if (e?.message) message.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    try {
      await api.saveSettings(await form.validateFields());
      const r = await api.testJudge();
      r.ok ? message.success(r.message) : message.warning(r.message);
    } catch (e: any) {
      if (e?.message) message.error(e.message);
    }
  };

  return (
    <>
      <PageHead title="设置" desc="评测台自己要用的东西在这里。被测系统的内部设置不在这里，那是对方的事。" />
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Card size="small" title="评测台的模型（评测台唯一要配的模型）">
        <Typography.Paragraph type="secondary">
          两个用处：评测时给每道题判分（答对、答错、拒答全部由它判定，数值的单位、量级、写法差异也由它判断）；新建测试集时出题和自动校验。必须配置，没配不能开始评测。命令行 knoweval run、knoweval build 也用这里的设置。
        </Typography.Paragraph>
        {!loaded ? <Skeleton active /> : (
          <Form form={form} layout="vertical" style={{ maxWidth: 640 }}>
            <Form.Item name="base_url" label="接口地址（OpenAI 兼容）">
              <Input placeholder="https://api.deepseek.com" />
            </Form.Item>
            <Form.Item name="model" label="模型名">
              <Input placeholder="deepseek-chat" />
            </Form.Item>
            <Form.Item name="api_key_env" label="API Key 环境变量" rules={[{ required: true }]} extra="密钥写在工作区的 .env 里（如 KNOWEVAL_LLM_API_KEY=...），只从环境变量读，不写进任何文件或结果">
              <Input />
            </Form.Item>
            <Form.Item name="disable_thinking" label="关闭思考" valuePropName="checked"
              extra="建议打开。会思考的模型（如 Qwen3）开着思考，一次调用要二三十秒，关掉后不到 1 秒；本来就不思考的模型（如 DeepSeek-V3）不受影响。少数接口不认这个参数会报错，那时关掉即可。">
              <Switch />
            </Form.Item>
            <Space>
              <Button onClick={test}>测试调用</Button>
              <Button type="primary" loading={busy} onClick={save}>保存</Button>
            </Space>
          </Form>
        )}
      </Card>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16, fontSize: 13 }}>
        裁判模型与判分提示词版本会写进每次运行的 run.json。横向比较时裁判要是同一个，判分口径才一致。
      </Typography.Paragraph>
    </>
  );
}
