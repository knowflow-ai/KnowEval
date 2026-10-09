import { describe, expect, it } from "vitest";

import { mixTotal, normalizeMix } from "./mix";

describe("题型配比", () => {
  it("没填的算 0，小数、负数、乱填的都规整成非负整数", () => {
    expect(normalizeMix({ F: 6, T: "4", L: 2.7, C: -1, M: "x" })).toEqual({ F: 6, T: 4, L: 2, C: 0, M: 0, K: 0, N: 0 });
  });
  it("合计", () => {
    expect(mixTotal({ F: 6, T: 4, N: 3 })).toBe(13);
    expect(mixTotal(undefined)).toBe(0);
  });
});
