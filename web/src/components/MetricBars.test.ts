import { describe, expect, it } from "vitest";

import { seriesColor, shortLabels } from "./MetricBars";

describe("指标卡", () => {
  it("同一系统的配置只显示配置名", () => {
    expect(shortLabels(["KnowFlow-本地：smart", "KnowFlow-本地：naive"])).toEqual(["smart", "naive"]);
  });
  it("不同系统保留全名", () => {
    expect(shortLabels(["KnowFlow：smart", "RAGFlow：默认"])).toEqual(["KnowFlow：smart", "RAGFlow：默认"]);
    expect(shortLabels(["只有一个"])).toEqual(["只有一个"]);
  });
  it("颜色按固定顺序取，超出的归为灰色而不是循环", () => {
    expect(seriesColor(0)).toBe("#1677ff");
    expect(seriesColor(4)).toBe("#8c8c8c");
  });
});
