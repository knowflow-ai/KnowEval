// 评测台的纯逻辑：路由、状态文案、配置摘要、进度百分比（logic.test.ts 覆盖）

import type { Adapter, BuildSummary, DatasetInfo, Progress, RunStatus, SourceKb, UiConfig } from "./api";

export type Route =
  | { page: "evals" }
  | { page: "new"; testset?: string }
  | { page: "eval"; id: string }
  | { page: "systems" }
  | { page: "datasets" }
  | { page: "dataset"; path: string }
  | { page: "build"; id: string }
  | { page: "settings" };

/** 用 hash 路由：页面是后端吐出的单文件，没有服务端路由可依赖 */
export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "new") return parts[1] ? { page: "new", testset: decodeURIComponent(parts[1]) } : { page: "new" };
  if (parts[0] === "evals" && parts[1]) return { page: "eval", id: decodeURIComponent(parts[1]) };
  if (parts[0] === "systems") return { page: "systems" };
  if (parts[0] === "datasets" && parts[1]) return { page: "dataset", path: decodeURIComponent(parts[1]) };
  if (parts[0] === "builds" && parts[1]) return { page: "build", id: decodeURIComponent(parts[1]) };
  if (parts[0] === "datasets") return { page: "datasets" };
  if (parts[0] === "settings") return { page: "settings" };
  return { page: "evals" };
}

export function routeHash(r: Route): string {
  if (r.page === "eval") return `#/evals/${encodeURIComponent(r.id)}`;
  if (r.page === "dataset") return `#/datasets/${encodeURIComponent(r.path)}`;
  if (r.page === "build") return `#/builds/${encodeURIComponent(r.id)}`;
  if (r.page === "new" && r.testset) return `#/new/${encodeURIComponent(r.testset)}`;
  return r.page === "evals" ? "#/" : `#/${r.page}`;
}

/** 在原文里找证据引文的位置：先找原样，再忽略空白找，最后退到前 12 个字（md 表格、换行常让引文与原文空白不一致） */
export function findQuote(text: string, quote: string): [number, number] | null {
  if (!quote) return null;
  const exact = text.indexOf(quote);
  if (exact >= 0) return [exact, exact + quote.length];
  const squash = (x: string) => x.replace(/\s+/g, "");
  const target = squash(quote);
  if (target) {
    // 建一张「去空白后的下标 → 原文下标」表，再在去空白的文本里找
    const map: number[] = [];
    let flat = "";
    for (let i = 0; i < text.length; i++) {
      if (!/\s/.test(text[i])) { map.push(i); flat += text[i]; }
    }
    const at = flat.indexOf(target);
    if (at >= 0) return [map[at], map[at + target.length - 1] + 1];
  }
  const head = quote.slice(0, 12);
  const loose = head ? text.indexOf(head) : -1;
  return loose >= 0 ? [loose, loose + head.length] : null;
}

/**
 * 预览原文时定位引文：整段 → 其中最长的一句（系统引用的分块常加了标题、去了换行）→ 引用的那一页（PDF 文本里有「--- 第 N 页 ---」）。
 * note 说明退到了哪一步；都找不到返回 null。
 */
export function locateQuote(text: string, quote: string, page?: number | null): { start: number; end: number; note?: string } | null {
  const whole = findQuote(text, quote);
  if (whole) return { start: whole[0], end: whole[1] };
  const pieces = quote.split(/[\n。；;！？!?|#]+/).map((x) => x.trim()).filter((x) => x.length >= 10).sort((a, b) => b.length - a.length);
  for (const piece of pieces) {
    const at = findQuote(text, piece);
    if (at) return { start: at[0], end: at[1], note: "整段没有逐字找到（分块时可能改过格式），高亮的是其中一句" };
  }
  const marker = page ? `--- 第 ${page} 页 ---` : "";
  const at = marker ? text.indexOf(marker) : -1;
  if (at < 0) return null;
  return { start: at, end: at + marker.length, note: quote ? `引文没有逐字找到，已定位到第 ${page} 页` : undefined };
}

export const STATUS_TEXT: Record<RunStatus, string> = {
  created: "未开始",
  pending: "排队中",
  running: "运行中",
  finished: "已完成",
  failed: "失败",
  stopped: "已中止",
};

export const STATUS_COLOR: Record<RunStatus, string> = {
  created: "default",
  pending: "default",
  running: "processing",
  finished: "success",
  failed: "error",
  stopped: "warning",
};

export const ADAPTER_LABEL: Record<Adapter, string> = {
  knowflow: "KnowFlow",
  ragflow: "RAGFlow",
  http: "自研系统（粘贴 curl）",
};

/** 参数摊平成 a.b=值 的列表 */
export function flatParams(obj: Record<string, unknown> | undefined, prefix = ""): string[] {
  return Object.entries(obj ?? {}).flatMap(([k, v]) => (v !== null && typeof v === "object" && !Array.isArray(v)
    ? flatParams(v as Record<string, unknown>, `${prefix}${k}.`)
    : [`${prefix}${k}=${Array.isArray(v) ? v.join(",") : String(v)}`]));
}

/** 配置的接口参数摘要：原样列出 key=value，评测台不解释它们 */
export function configSummary(cfg: Pick<UiConfig, "dataset" | "chat"> & Partial<Pick<UiConfig, "existing_chat" | "api_key_env">>
  & { chat_overrides?: Record<string, unknown> | null }): string {
  if (cfg.existing_chat && cfg.chat_overrides && Object.keys(cfg.chat_overrides).length) {
    return `以助手 ${cfg.existing_chat.name || cfg.existing_chat.id} 为基准，改了 ${flatParams(cfg.chat_overrides).join(" · ")}（临时助手，问完删除）`;
  }
  if (cfg.existing_chat) {
    return `已有助手：${cfg.existing_chat.name || cfg.existing_chat.id}（设置以系统里的助手为准，开跑时记下配置快照）`;
  }
  const flat = [...flatParams(cfg.dataset), ...flatParams(cfg.chat, "chat.")];
  if (cfg.api_key_env) flat.push(`API Key=${cfg.api_key_env}`);
  return flat.length ? flat.join(" · ") : "使用系统默认设置";
}

/** 进度条：建库上传占 20%，提问与判分按题数占剩下的 80% */
export function progressPercent(p: Progress | undefined): number {
  if (!p) return 0;
  if (p.phase === "done") return 100;
  if (!p.ingest) return 5;
  const totalWork = p.total * p.rounds.length;
  const judged = p.rounds.reduce((a, r) => a + Math.min(r.judged, p.total), 0);
  return Math.round(20 + (totalWork ? (judged / totalWork) * 80 : 0));
}

/** 正在提问的那一遍；全部判完返回最后一遍 */
export function currentRound(p: Progress): { repeat: number; asked: number; judged: number } {
  return p.rounds.find((r) => r.judged < p.total) ?? p.rounds[p.rounds.length - 1];
}

/** 把用户填的 JSON 文本解析成对象；空串视为 {} */
/** 参数 JSON 排成一行一个参数；空的或还没写对的原样返回，保存时再报错 */
export function formatJson(text: string): string {
  if (!text.trim()) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function parseJsonObject(text: string): Record<string, unknown> {
  const t = text.trim();
  if (!t) return {};
  const v = JSON.parse(t);
  if (v === null || typeof v !== "object" || Array.isArray(v)) throw new Error("需要一个 JSON 对象，例如 {\"chunk_method\": \"smart\"}");
  return v as Record<string, unknown>;
}

/** 测试集列表里的一项：评测台出的（构建）或现成的数据集（如官方数据集） */
export interface TestsetOption {
  key: string;                 // build:<id> 或 dataset:<目录>
  kind: "build" | "dataset";
  label: string;
  questions: number;
  /** 文档份数：只导入了题目的测试集没有文档，完整测试建不了库 */
  docs: number;
  source_kb: SourceKb | null;
  usable: boolean;             // 出完题、有可用的题才能评测
}

/** 合并测试集：评测台出的在前，官方数据集在后 */
export function testsetOptions(datasets: DatasetInfo[], builds: BuildSummary[]): TestsetOption[] {
  return [
    ...builds.map((b) => ({ key: `build:${b.id}`, kind: "build" as const, label: b.name, questions: b.passed, docs: b.docs ?? 0,
      source_kb: b.source_kb ?? null, usable: b.status === "ready" && b.passed > 0 })),
    ...datasets.map((d) => ({ key: `dataset:${d.path}`, kind: "dataset" as const, label: d.name,
      questions: d.questions, docs: d.docs, source_kb: d.source_kb, usable: d.questions > 0 })),
  ];
}

/** 评测接口要的测试集参数 */
export function testsetParam(key: string): { build_id: string } | { dataset_path: string } {
  return key.startsWith("build:") ? { build_id: key.slice(6) } : { dataset_path: key.slice(8) };
}
