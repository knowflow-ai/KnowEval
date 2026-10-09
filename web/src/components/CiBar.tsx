import { Tooltip } from "antd";

import { ciBar, ciText, pct } from "../data";
import type { Rate } from "../types";

/** 区间条：浅色段是 95% 区间，竖线是点估计。题少的分项区间宽，提醒读者别只看点 */
export default function CiBar({ rate, color = "#1677ff", band = "#bae0ff" }: { rate: Rate; color?: string; band?: string }) {
  const bar = ciBar(rate);
  return (
    <Tooltip title={`${pct(rate.value)}，95% 区间 ${ciText(rate)}，${rate.k}/${rate.n}`}>
      <div style={{ position: "relative", height: 14, minWidth: 120 }} aria-hidden>
        <div style={{ position: "absolute", left: 0, right: 0, top: 6, height: 2, background: "#f0f0f0" }} />
        {bar && (
          <>
            <div style={{ position: "absolute", top: 3, height: 8, borderRadius: 4, background: band, left: `${bar.lo}%`, width: `${bar.width}%` }} />
            <div style={{ position: "absolute", top: 0, width: 2, height: 14, background: color, left: `${bar.point}%` }} />
          </>
        )}
      </div>
    </Tooltip>
  );
}
