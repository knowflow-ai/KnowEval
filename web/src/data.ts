// 报告页面用到的纯数据计算，单独测试（data.test.ts）

import type { Question, Rate, RunRecord, Verdict } from "./types";

export const VERDICT_LABEL: Record<Verdict, string> = {
  CORRECT: "答对",
  ABSTAIN: "坦白不知道",
  WRONG: "自信答错",
  ERROR: "出错",
};

// antd Tag 颜色：蓝＝答对，默认灰＝不知道，橙＝答错，红＝出错（靠明暗区分，不只靠色相）
export const VERDICT_TAG: Record<Verdict, string> = {
  CORRECT: "blue",
  ABSTAIN: "default",
  WRONG: "orange",
  ERROR: "red",
};

/** 无答案题说「找不到」是期望的行为，在题目列表里算答对，标签写「正确拒答」；有答案的题说找不到才是「坦白不知道」 */
export function outcome(q: Question, v: Verdict): Verdict {
  return v === "ABSTAIN" && isNoAnswer(q) ? "CORRECT" : v;
}

export function outcomeLabel(q: Question, v: Verdict): string {
  return v === "ABSTAIN" && isNoAnswer(q) ? "正确拒答" : VERDICT_LABEL[v];
}

export const QTYPE_NAME: Record<string, string> = {
  F: "单跳事实", M: "跨文档多跳", T: "表格数值", C: "计算统计", X: "比较", D: "时间推理",
  V: "图表图片", L: "长文档定位", K: "信息冲突", N: "无答案", I: "图文答案", G: "全局关系",
};

export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function ciText(rate: Rate): string {
  if (!rate.ci) return "—";
  return `${pct(rate.ci[0])}–${pct(rate.ci[1])}`;
}

/** 区间条的位置（百分比）：lo、宽度、点估计 */
export function ciBar(rate: Rate): { lo: number; width: number; point: number } | null {
  if (!rate.ci || rate.value === null) return null;
  const lo = rate.ci[0] * 100;
  return { lo, width: rate.ci[1] * 100 - lo, point: rate.value * 100 };
}

export function isNoAnswer(q: Question): boolean {
  return q.qtype.includes("N");
}

/** 各遍之间判定不一致的题号 */
export function flippedIds(records: Record<string, Record<string, RunRecord>>): Set<string> {
  const reps = Object.values(records);
  const out = new Set<string>();
  if (reps.length < 2) return out;
  for (const qid of Object.keys(reps[0])) {
    const verdicts = new Set(reps.map((r) => r[qid]?.judgement.verdict));
    if (verdicts.size > 1) out.add(qid);
  }
  return out;
}

export function quantile(sorted: number[], q: number): number | null {
  if (!sorted.length) return null;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1));
  return sorted[idx];
}

/** 每题耗时的中位数与 P90（毫秒），只统计有耗时的回答 */
export function latencyStats(rep: Record<string, RunRecord>): { median: number | null; p90: number | null } {
  const xs = Object.values(rep)
    .map((r) => r.answer.latency_ms)
    .filter((x): x is number => typeof x === "number")
    .sort((a, b) => a - b);
  return { median: quantile(xs, 0.5), p90: quantile(xs, 0.9) };
}

/** 判定分布：有答案的题与无答案的题分开 */
export function verdictCounts(questions: Question[], rep: Record<string, RunRecord>) {
  const blank = (): Record<Verdict, number> => ({ CORRECT: 0, ABSTAIN: 0, WRONG: 0, ERROR: 0 });
  const answerable = blank();
  const noAnswer = blank();
  for (const q of questions) {
    const v = rep[q.id]?.judgement.verdict;
    if (!v) continue;
    (isNoAnswer(q) ? noAnswer : answerable)[v] += 1;
  }
  return { answerable, noAnswer };
}

export interface QuestionFilter {
  verdict: Verdict | "ALL";
  qtype: string | "ALL";
  onlyFlipped: boolean;
  onlyReview: boolean;
  search: string;
}

export function filterQuestions(
  questions: Question[],
  rep: Record<string, RunRecord>,
  flipped: Set<string>,
  f: QuestionFilter,
): Question[] {
  const needle = f.search.trim().toLowerCase();
  return questions.filter((q) => {
    const r = rep[q.id];
    if (!r) return false;
    if (f.verdict !== "ALL" && outcome(q, r.judgement.verdict) !== f.verdict) return false;
    if (f.qtype !== "ALL" && !q.qtype.includes(f.qtype)) return false;
    if (f.onlyFlipped && !flipped.has(q.id)) return false;
    if (f.onlyReview && !r.judgement.needs_review) return false;
    if (needle) {
      const hay = `${q.id} ${q.question} ${q.evidence.map((e) => e.doc).join(" ")}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

/** 按准确率给热力格上色：antd 蓝色色板 8 阶，深色格用白字 */
const BLUE = ["#e6f4ff", "#bae0ff", "#91caff", "#69b1ff", "#4096ff", "#1677ff", "#0958d9", "#003eb3"];
export function heatColor(v: number | null): { bg: string; fg: string } {
  if (v === null) return { bg: "#fafafa", fg: "rgba(0,0,0,0.45)" };
  const i = Math.min(7, Math.floor(v * 8));
  return { bg: BLUE[i], fg: i >= 5 ? "#fff" : "rgba(0,0,0,0.88)" };
}

/** 引用分块可能是 HTML（MinerU、smart 分块的表格是 <table>），展示前去掉标签、压缩空白 */
export function plainText(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
}
