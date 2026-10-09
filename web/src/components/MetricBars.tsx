import { Card, Col, Row, Space, Tooltip, Typography } from "antd";

import { ciText, pct } from "../data";
import type { Rate } from "../types";

/** 系统配色：固定顺序取色、不循环（已过 dataviz 校验：色盲区分 ΔE≥18，正常视力 ≥27）；
 *  橙、青对白底对比不足 3:1，所以每行都直接写名字和数值，不靠颜色识别 */
export const SERIES_COLORS = ["#1677ff", "#fa8c16", "#13c2c2", "#722ed1"];
const OTHER = "#8c8c8c";

export function seriesColor(i: number): string {
  return SERIES_COLORS[i] ?? OTHER;
}

export interface MetricDef {
  key: string;
  label: string;
  higherIsBetter: boolean;
  def: string;
}

interface Series {
  name: string;
  label: string;
  rate: Rate;
  color: string;
}

/** 名字共享「系统名：」前缀时只显示后半段（配置名），否则区分点会被截掉；完整名字留在悬停提示里 */
export function shortLabels(names: string[]): string[] {
  if (names.length < 2) return names;
  const heads = names.map((n) => (n.includes("：") ? n.slice(0, n.indexOf("：") + 1) : ""));
  const shared = heads[0] && heads.every((h) => h === heads[0]) ? heads[0] : "";
  return names.map((n) => (shared ? n.slice(shared.length) : n));
}

function bestIndex(series: Series[], higherIsBetter: boolean): number | null {
  const vals = series.map((s) => s.rate.value);
  if (vals.some((v) => v === null) || new Set(vals).size < 2) return null;
  const nums = vals as number[];
  const target = higherIsBetter ? Math.max(...nums) : Math.min(...nums);
  return nums.indexOf(target);
}

/** 一行：实心条从 0 画到比率；数值与名字用文字色，颜色只做身份标记。区间不画在条上，只放进悬停提示 */
function BarRow({ s, best }: { s: Series; best: boolean }) {
  const v = s.rate.value;
  return (
    <Tooltip title={`${s.name}：${pct(v)}（${s.rate.k}/${s.rate.n} 题）。按题量估算，真实水平大致在 ${ciText(s.rate)} 之间`}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 180px) minmax(0, 1fr) 92px", alignItems: "center", gap: 12, padding: "6px 0" }}>
        <Space size={6} style={{ minWidth: 0 }}>
          <span aria-hidden style={{ width: 10, height: 10, borderRadius: 2, background: s.color, flex: "none", display: "inline-block" }} />
          <Typography.Text ellipsis style={{ maxWidth: 160 }}>{s.label}</Typography.Text>
        </Space>
        <div style={{ position: "relative", height: 14 }} aria-hidden>
          <div style={{ position: "absolute", inset: "2px 0", background: "#f5f5f5", borderRadius: 4 }} />
          {[25, 50, 75].map((g) => (
            <div key={g} style={{ position: "absolute", left: `${g}%`, top: 0, bottom: 0, width: 1, background: "#ffffff" }} />
          ))}
          {v !== null && (
            <div style={{ position: "absolute", left: 0, top: 2, bottom: 2, width: `${Math.max(v * 100, 0.6)}%`, background: s.color, borderRadius: "0 4px 4px 0", opacity: 0.85 }} />
          )}
        </div>
        <div style={{ textAlign: "right", lineHeight: 1.2 }}>
          <Typography.Text strong={best} style={{ fontSize: 16 }}>{pct(v)}</Typography.Text>
          {best && <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>最好</Typography.Text>}
          <div><Typography.Text type="secondary" style={{ fontSize: 11 }}>{s.rate.k}/{s.rate.n} 题</Typography.Text></div>
        </div>
      </div>
    </Tooltip>
  );
}

function Axis() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 180px) minmax(0, 1fr) 92px", gap: 12 }} aria-hidden>
      <span />
      <div style={{ position: "relative", height: 16, fontSize: 11, color: "rgba(0,0,0,0.45)" }}>
        <span style={{ position: "absolute", left: 0 }}>0</span>
        <span style={{ position: "absolute", left: "50%", transform: "translateX(-50%)" }}>50%</span>
        <span style={{ position: "absolute", right: 0 }}>100%</span>
      </div>
      <span />
    </div>
  );
}

/** 指标卡片网格：每个指标一张卡，卡内每个系统一行 */
export default function MetricBars({ metrics, systems }: {
  metrics: MetricDef[];
  systems: { name: string; rates: Record<string, Rate> }[];
}) {
  return (
    <Row gutter={[16, 16]}>
      {metrics.map((m) => {
        const labels = shortLabels(systems.map((s) => s.name));
        const series = systems.map((s, i) => ({ name: s.name, label: labels[i], rate: s.rates[m.key], color: seriesColor(i) }));
        const best = bestIndex(series, m.higherIsBetter);
        return (
          <Col key={m.key} xs={24} lg={12}>
            <Card size="small" style={{ height: "100%" }}
              title={<Space size={8}><span>{m.label}</span><Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>{m.higherIsBetter ? "越高越好" : "越低越好"}</Typography.Text></Space>}>
              {series.map((s, i) => <BarRow key={s.name} s={s} best={best === i} />)}
              <Axis />
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "6px 0 0" }}>{m.def}</Typography.Paragraph>
            </Card>
          </Col>
        );
      })}
    </Row>
  );
}
