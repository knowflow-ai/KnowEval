import { Space, Typography } from "antd";
import type { ReactNode } from "react";

export default function PageHead({ title, desc, extra }: { title: ReactNode; desc?: ReactNode; extra?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
      <div style={{ flex: "1 1 420px" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>{title}</Typography.Title>
        {desc && <Typography.Paragraph type="secondary" style={{ margin: "4px 0 0" }}>{desc}</Typography.Paragraph>}
      </div>
      {extra && <Space>{extra}</Space>}
    </div>
  );
}
