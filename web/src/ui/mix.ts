/** 出题的题型配比：哪些题型能自动生成、各是什么意思，以及配比的合计 */

export const MIX_ORDER = ["F", "T", "L", "C", "M", "K", "N"];

export const MIX_HINT: Record<string, string> = {
  F: "原文里的一个具体事实",
  T: "表格里的某个数",
  L: "长文档靠后的细节",
  C: "要算一下的题，答案由程序算",
  M: "要同时用到两份文档",
  K: "需要同一文件的新旧两版（文件名只差年份或版本号），没有就会出不来",
  N: "文档里没有答案，应当说不知道",
};

/** 按 MIX_ORDER 整理成整数配比（没填的算 0） */
export function normalizeMix(mix: Record<string, unknown> | undefined): Record<string, number> {
  return Object.fromEntries(MIX_ORDER.map((t) => [t, Math.max(0, Math.floor(Number(mix?.[t] ?? 0)) || 0)]));
}

export function mixTotal(mix: Record<string, unknown> | undefined): number {
  return Object.values(normalizeMix(mix)).reduce((a, b) => a + b, 0);
}
