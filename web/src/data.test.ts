import { ciBar, filterQuestions, outcomeLabel, flippedIds, heatColor, latencyStats, pct, plainText, verdictCounts } from "./data";
import type { Question, RunRecord } from "./types";

const q = (id: string, qtype = ["F"]): Question => ({
  id, domain: "d", qtype, question: `问题 ${id}`, answer: "1", answer_type: "number",
  evidence: [{ doc: `${id}.pdf`, page: 1, modality: "text", quote: "x" }], hops: 1,
});
const rec = (verdict: RunRecord["judgement"]["verdict"], latency: number | null = 100, review = false): RunRecord => ({
  answer: { text: "a", citations: [], contexts: null, latency_ms: latency, error: null },
  judgement: { verdict, reason: "", method: "rule", needs_review: review, citation: null, attribution: null },
});

test("pct and ciBar", () => {
  expect(pct(0.7175)).toBe("71.8%");
  expect(pct(null)).toBe("—");
  const bar = ciBar({ k: 1, n: 2, value: 0.5, ci: [0.1, 0.9] })!;
  expect(bar.lo).toBeCloseTo(10);
  expect(bar.width).toBeCloseTo(80);
  expect(ciBar({ k: 0, n: 0, value: null, ci: null })).toBeNull();
});

test("flippedIds finds questions whose verdict differs between repeats", () => {
  const flips = flippedIds({ "1": { a: rec("CORRECT"), b: rec("WRONG") }, "2": { a: rec("CORRECT"), b: rec("CORRECT") } });
  expect([...flips]).toEqual(["b"]);
  expect(flippedIds({ "1": { a: rec("CORRECT") } }).size).toBe(0);
});

test("latencyStats ignores missing latency", () => {
  const s = latencyStats({ a: rec("CORRECT", 100), b: rec("CORRECT", 300), c: rec("ERROR", null), d: rec("CORRECT", 200) });
  expect(s.median).toBe(200);
  expect(s.p90).toBe(300);
});

test("verdictCounts separates no-answer questions", () => {
  const qs = [q("a"), q("b"), q("n", ["N"])];
  const c = verdictCounts(qs, { a: rec("CORRECT"), b: rec("ERROR"), n: rec("ABSTAIN") });
  expect(c.answerable.CORRECT).toBe(1);
  expect(c.answerable.ERROR).toBe(1);
  expect(c.noAnswer.ABSTAIN).toBe(1);
});

test("filterQuestions combines verdict, qtype, flip, review and search", () => {
  const qs = [q("a"), q("b", ["T"]), q("c")];
  const rep = { a: rec("CORRECT"), b: rec("WRONG", 1, true), c: rec("WRONG") };
  const base = { verdict: "ALL" as const, qtype: "ALL", onlyFlipped: false, onlyReview: false, search: "" };
  expect(filterQuestions(qs, rep, new Set(), { ...base, verdict: "WRONG" }).map((x) => x.id)).toEqual(["b", "c"]);
  expect(filterQuestions(qs, rep, new Set(), { ...base, qtype: "T" }).map((x) => x.id)).toEqual(["b"]);
  expect(filterQuestions(qs, rep, new Set(["c"]), { ...base, onlyFlipped: true }).map((x) => x.id)).toEqual(["c"]);
  expect(filterQuestions(qs, rep, new Set(), { ...base, onlyReview: true }).map((x) => x.id)).toEqual(["b"]);
  expect(filterQuestions(qs, rep, new Set(), { ...base, search: "c.pdf" }).map((x) => x.id)).toEqual(["c"]);
});

test("a refusal on a no-answer question is listed as correct, not as an unknown", () => {
  const qs = [q("a"), q("n", ["N"])];
  const rep = { a: rec("ABSTAIN"), n: rec("ABSTAIN") };
  const base = { verdict: "ALL" as const, qtype: "ALL", onlyFlipped: false, onlyReview: false, search: "" };
  expect(filterQuestions(qs, rep, new Set(), { ...base, verdict: "ABSTAIN" }).map((x) => x.id)).toEqual(["a"]);
  expect(filterQuestions(qs, rep, new Set(), { ...base, verdict: "CORRECT" }).map((x) => x.id)).toEqual(["n"]);
  expect(outcomeLabel(qs[1], "ABSTAIN")).toBe("正确拒答");
  expect(outcomeLabel(qs[0], "ABSTAIN")).toBe("坦白不知道");
});

test("heatColor uses dark text on light cells", () => {
  expect(heatColor(0.1).fg).toBe("rgba(0,0,0,0.88)");
  expect(heatColor(0.95).fg).toBe("#fff");
  expect(heatColor(null).bg).toBe("#fafafa");
});

test("plainText strips tags from table chunks", () => {
  expect(plainText("# 手册\n<table><tr><td>v2.0</td><td>45 ms</td></tr></table>")).toBe("# 手册 v2.0 45 ms");
});
