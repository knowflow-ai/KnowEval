import { Col, InputNumber, Row, Tooltip, Typography } from "antd";

import { QTYPE_NAME } from "../../data";
import { MIX_HINT, MIX_ORDER, normalizeMix } from "../mix";

/** 题型配比：每种题型出几道。新建测试集、从助手的知识库出题共用；可直接放进 Form.Item（value / onChange） */
export default function MixEditor({ value, onChange }: { value?: Record<string, number>; onChange?: (v: Record<string, number>) => void }) {
  const mix = normalizeMix(value);
  return (
    <>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "0 0 8px" }}>
        每类会多出约一半，抵消自动校验打回的题；出完可以在页面上删掉不好的，也可以自己加题。
      </Typography.Paragraph>
      <Row gutter={[16, 8]}>
        {MIX_ORDER.map((t) => (
          <Col xs={12} md={8} key={t}>
            <Tooltip title={MIX_HINT[t]}>
              <div style={{ fontSize: 13, marginBottom: 4 }}>{t} {QTYPE_NAME[t] ?? ""}</div>
            </Tooltip>
            <InputNumber min={0} max={50} value={mix[t]} style={{ width: "100%" }}
              onChange={(n) => onChange?.({ ...mix, [t]: Number(n ?? 0) })} />
          </Col>
        ))}
      </Row>
    </>
  );
}
