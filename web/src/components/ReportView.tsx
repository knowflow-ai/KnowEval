import { Layout, Space, Tabs, Tag, Typography } from "antd";

import type { ReportData } from "../types";
import Overview from "./Overview";
import Questions from "./Questions";
import RunInfo from "./RunInfo";

export default function ReportView({ data, embedded = false }: { data: ReportData; embedded?: boolean }) {
  const meta = data.meta;
  const system = meta.system?.adapter ?? data.name;
  const tabs = (
    <Tabs
      items={[
        { key: "overview", label: "总览", children: <Overview data={data} /> },
        { key: "questions", label: "逐题明细", children: <Questions data={data} /> },
        { key: "run", label: "运行信息", children: <RunInfo data={data} /> },
      ]}
    />
  );
  if (embedded) return tabs;
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", padding: "0 24px", height: "auto", lineHeight: "normal" }}>
        <Space size="middle" wrap style={{ minHeight: 56 }}>
          <Typography.Text strong style={{ fontSize: 16 }}>KnowEval</Typography.Text>
          <Tag>报告</Tag>
          <Typography.Text>{data.name}</Typography.Text>
          <Typography.Text type="secondary">
            {system} · {meta.dataset?.ref} · 跑了 {meta.repeats} 遍{meta.subset ? ` · 子集 ${meta.subset}` : ""}
            {meta.manual_ingest ? " · 文档由被测系统自行导入" : ""}
          </Typography.Text>
        </Space>
      </Layout.Header>
      <Layout.Content style={{ padding: "16px 24px 48px", maxWidth: 1400, width: "100%", margin: "0 auto" }}>
        {tabs}
      </Layout.Content>
    </Layout>
  );
}
