import type { EvalPick } from "./api";
import { type TestsetOption, parseJsonObject } from "./logic";

/** 系统里的一个助手（/api/systems/{id}/chats 的一项） */
export interface Chat {
  id: string;
  name: string;
  model: string;
  kbs: { id: string; name: string; documents?: number | null }[];
}

/**
 * 测已有助手时能用的测试集：题目出自这个系统、且出题的知识库都挂在每个选中的助手上。
 * 别的测试集（上传文档、导入题目、官方数据集）文档不一定在助手的库里，不列，要测走「完整测试」。还没出完题的不列。
 */
export function testsetsForAssistants(testsets: TestsetOption[], systemId: string, chats: Chat[]): TestsetOption[] {
  if (!chats.length) return [];
  return testsets.filter((t) => t.usable && !!t.source_kb && t.source_kb.system_id === systemId && t.source_kb.kbs.length > 0
    && chats.every((c) => t.source_kb!.kbs.every((k) => c.kbs.some((ck) => ck.id === k.id))));
}

/** 选中的助手挂的知识库完全一样时，返回这组库（可以从它们出一套题给所有助手用），否则 null */
export function sharedKbs(chats: Chat[]): Chat["kbs"] | null {
  if (!chats.length || !chats[0].kbs.length) return null;
  const key = (c: Chat) => c.kbs.map((k) => k.id).sort().join(",");
  if (!chats.every((c) => key(c) === key(chats[0]))) return null;
  return [...chats[0].kbs].sort((a, b) => a.id.localeCompare(b.id));
}

/** 评测台以前建库、试问时建的临时助手（名字以 knoweval- 开头） */
export const isTempChat = (c: Chat) => c.name.startsWith("knoweval-");

/** 助手列表：按名字或挂的知识库名搜索；用户自己的助手和评测台建的临时助手分开 */
export function splitChats(chats: Chat[], query: string) {
  const q = query.trim().toLowerCase();
  const hit = (c: Chat) => !q || [c.name, ...c.kbs.map((k) => k.name)].some((t) => t.toLowerCase().includes(q));
  const shown = chats.filter(hit);
  return { mine: shown.filter((c) => !isTempChat(c)), temp: shown.filter(isTempChat) };
}

/** 一组参数对照的草稿：名字 + 要改的参数（JSON 文本） */
export interface VariantDraft {
  name: string;
  json: string;
}

const isPlainObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);

/** 改过的参数：和基准比，只留值不同（或新加）的项；嵌套对象逐层比，数组整体比。删掉的项不算改动（按基准原值） */
export function diffParams(edited: Record<string, unknown>, base: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(edited)) {
    const b = base[k];
    if (isPlainObject(v) && isPlainObject(b)) {
      const inner = diffParams(v, b);
      if (Object.keys(inner).length) out[k] = inner;
    } else if (JSON.stringify(v) !== JSON.stringify(b)) {
      out[k] = v;
    }
  }
  return out;
}

/**
 * 组装参评勾选项：每个选中的助手问一次；只选了一个助手时，再加上它的参数对照（复制它、只改这几项参数）。
 * 给了 baseParams（基准助手现在的全部参数）时，每组填的是完整参数，只把改过的项当对照。
 * 参数写错、什么都没改，或者想改挂的知识库，抛出带组名的错误给页面提示。
 */
export function buildPicks(systemId: string, chosen: Chat[], variants: VariantDraft[],
  baseParams?: Record<string, unknown> | null): EvalPick[] {
  const picks: EvalPick[] = chosen.map((c) => ({ system_id: systemId, chat_id: c.id, chat_name: c.name }));
  if (chosen.length !== 1) return picks;
  const base = picks[0];
  return [...picks, ...variants.map((v, i) => {
    const name = v.name.trim() || `对照 ${i + 1}`;
    let edited: Record<string, unknown>;
    try {
      edited = parseJsonObject(v.json);
    } catch (e: any) {
      throw new Error(`参数对照「${name}」的参数不是合法的 JSON 对象：${e.message ?? e}`);
    }
    const overrides = baseParams ? diffParams(edited, baseParams) : edited;
    if (!Object.keys(overrides).length) {
      throw new Error(baseParams ? `参数对照「${name}」没改任何参数，和基准一样` : `参数对照「${name}」还没写要改的参数`);
    }
    if ("dataset_ids" in overrides || "kb_ids" in overrides) {
      throw new Error(`参数对照「${name}」不能改助手挂的知识库：要换知识库请另选助手，或用「完整测试」`);
    }
    return { ...base, variant: { name, overrides } };
  })];
}
