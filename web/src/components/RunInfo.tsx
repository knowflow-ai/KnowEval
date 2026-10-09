import { Card, Descriptions, Table, Typography } from "antd";

import { fmtTime } from "../data";
import type { ReportData } from "../types";

/** run.json 全文：任何结果都能追溯到当时的题目、系统配置、裁判与代码版本 */
export default function RunInfo({ data }: { data: ReportData }) {
  const m = data.meta;
  return (
    <>
      <Descriptions bordered size="small" column={1} items={[
        { key: "ds", label: "题目", children: `${m.dataset?.ref}（内容哈希 ${String(m.dataset?.content_hash ?? "").slice(0, 16)}…）` },
        { key: "sys", label: "被测系统", children: <Typography.Text code>{JSON.stringify(m.system)}</Typography.Text> },
        // 被测系统此刻的实际配置（如 KnowFlow 助手的模型、ColPali、SAG 策略），开跑时抓的
        ...(m.system_snapshot ? [{ key: "snap", label: "配置快照", children: m.system_snapshot.error
          ? <Typography.Text type="warning">{m.system_snapshot.error}</Typography.Text>
          : <>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>指纹 {String(m.system_snapshot.hash).slice(0, 12)}（两次运行指纹不同，说明被测配置变过）</Typography.Text>
              <pre style={{ margin: "6px 0 0", maxHeight: 320, overflow: "auto", fontSize: 12, whiteSpace: "pre-wrap" }}>
                {JSON.stringify(m.system_snapshot.config, null, 2)}
              </pre>
            </> }] : []),
        { key: "judge", label: "裁判模型", children: m.judge ?? "未配置（判不了分，全部标记为待复核）" },
        { key: "rep", label: "遍数", children: m.repeats },
        { key: "subset", label: "子集", children: m.subset ?? "全部" },
        { key: "ver", label: "版本", children: Object.entries(m.versions ?? {}).map(([k, v]) => `${k} ${v}`).join("，") },
        { key: "time", label: "时间", children: `${m.started_at ? fmtTime(m.started_at) : ""} → ${m.finished_at ? fmtTime(m.finished_at) : "未完成"}` },
        { key: "canary", label: "金丝雀字符串", children: m.dataset?.canary },
      ]} />
      {!!data.summary.ingest?.docs?.length && <Card title="入库" size="small" style={{ marginTop: 16 }}>
        <Table size="small" pagination={false} rowKey="name" dataSource={data.summary.ingest?.docs ?? []} columns={[
          { title: "文档", dataIndex: "name" },
          { title: "结果", render: (_, d: any) => (d.ok ? "成功" : "失败") },
          { title: "耗时（秒）", dataIndex: "seconds", align: "right" },
          { title: "失败原因", dataIndex: "error" },
        ]} />
      </Card>}
    </>
  );
}
