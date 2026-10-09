// 评测台后端接口（knoweval/ui/server.py）的类型与请求封装

import type { CompareData, ReportData } from "../types";

export type Adapter = "knowflow" | "ragflow" | "http";
export type RunStatus = "created" | "pending" | "running" | "finished" | "failed" | "stopped";

export interface UiConfig {
  id: string;
  name: string;
  dataset: Record<string, unknown>;
  chat: Record<string, unknown>;
  /** 测已有助手时临时生成（参评项的配置快照里才有）：直接问这个助手，设置以它为准 */
  existing_chat?: { id: string; name: string } | null;
  /** 这份配置用自己的 API Key（环境变量名），不填用系统的 */
  api_key_env?: string | null;
}

export interface UiSystem {
  id: string;
  name: string;
  adapter: Adapter;
  base_url: string;
  api_key_env: string;
  version: string | null;
  image_base_url: string | null;
  http: Record<string, unknown>;
  http_form?: Record<string, unknown>;
  configs: UiConfig[];
}

export interface JudgeSettings {
  base_url: string;
  model: string;
  api_key_env: string;
  disable_thinking: boolean;
}

export interface DatasetInfo {
  ref: string;
  name: string;
  version: string;
  path: string;
  license: string;
  docs: number;
  questions: number;
  formats: string[];
  domains: Record<string, number>;
  qtypes: Record<string, number>;
  /** 用到这个数据集的评测名 */
  used_by: string[];
  /** 题目出自哪个系统的哪些知识库；不是从知识库出的题为空 */
  source_kb: SourceKb | null;
}

/** 参评勾选项：存好的配置，或直接选的已有助手 */
export type EvalPick = { system_id: string; config_id: string }
  | { system_id: string; chat_id: string; chat_name: string; variant?: { name: string; overrides: Record<string, unknown> } };

/** 出完题要开始的评测；开始后记下 eval_id，失败记下 error */
export interface AfterReady {
  picks: EvalPick[];
  repeats?: number;
  concurrency?: number;
  name?: string;
  eval_id?: string | null;
  /** 评测建好了但当时没能开始（如已有评测在跑） */
  note?: string | null;
  error?: string | null;
}

/** 从知识库出的题：出自哪个系统的哪些库。「测已有助手」按它把测试集配给挂了这些库的助手 */
export interface SourceKb {
  system_id: string;
  system: string;
  kbs: { id: string; name: string }[];
}

/** 试问检查的结果：suspect 表示多半文档没导入或接口不对，skipped 表示要新建知识库、没法先问 */
export interface PreflightResult {
  name: string;
  status: "ok" | "suspect" | "skipped";
  message: string;
  asked?: number;
  abstained?: number;
  errors?: number;
  /** 试问的题全部接口报错：照样跑只会得到一轮全错 */
  all_errors?: boolean;
  samples?: { question: string; answer: string; gold: string }[];
}

export interface DatasetQuestion {
  id: string;
  domain: string;
  qtype: string[];
  question: string;
  answer: string;
  answer_type: string;
  unit?: string | null;
  tolerance?: number | null;
  answer_aliases?: string[];
  answer_distractors?: string[];
  evidence: { doc: string; page: number | null; quote: string; modality?: string }[];
  stage?: string | null;
  traps?: string[];
  n_kind?: string | null;
  hops?: number;
}

export interface DatasetDetail extends Omit<DatasetInfo, "questions"> {
  files: { name: string; sha256: string; format: string; domain?: string; source_url?: string | null; present: boolean; redistribute?: boolean }[];
  questions: DatasetQuestion[];
}

export type BuildStatus = "importing" | "generating" | "verifying" | "ready" | "failed";

export interface BuildCounts {
  docs: number;
  candidates: number;
  passed: number;
  rejected_auto: number;
  deleted: number;
  confirmed: number;
  pending_verify: number;
}

export interface BuildSummary extends BuildCounts {
  id: string;
  name: string;
  version: string;
  source: { kind: "upload" | "kb" | "import"; files?: number; rows?: number; system?: string; system_id?: string; kb_ids?: string[]; kb_names?: Record<string, string>;
    kbs?: { id: string; name: string; documents: number; kept: number }[] };
  mix: Record<string, number>;
  status: BuildStatus;
  error: string | null;
  skipped: string[];
  created_at: string;
  source_kb?: SourceKb | null;
  /** 出完题要开始的评测（从助手的知识库出题时） */
  after_ready?: AfterReady | null;
  /** 上一轮校验时模型调用失败、还没校验完的题数 */
  verify_errors?: number;
}

export interface BuildItem extends DatasetQuestion {
  status: "generated" | "verified" | "rejected_auto" | "approved" | "edited" | "rejected";
  generator: string;
  notes: string[];
  checks: Record<string, any>;
}

export interface BuildDetail extends BuildSummary {
  target: number;
  import_report: { rows: number; imported: number; problems: { row: number; reason: string }[] } | null;
  running: boolean;
  docs_list: { name: string; format: string }[];
  items: BuildItem[];
}

export interface Participant {
  id: string;
  name: string;
  system_name: string;
  config: Record<string, any>;
  status: RunStatus;
  error: string | null;
}

export interface EvalMeta {
  id: string;
  name: string;
  dataset_ref: string;
  dataset_path: string;
  repeats: number;
  concurrency: number;
  participants: Participant[];
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface EvalRow extends EvalMeta {
  headline: Record<string, number | null>;
}

export interface Progress {
  phase: "ingest" | "ask" | "done";
  ingest: { manual: boolean; existing?: boolean; total: number; ok: number; failed: { name: string; error: string | null }[] } | null;
  rounds: { repeat: number; asked: number; judged: number }[];
  total: number;
  summary: { accuracy: number | null; confident_wrong: number | null; noise: number } | null;
}

export interface EvalDetail extends EvalMeta {
  progress: Record<string, Progress>;
  running: boolean;
}

export class ApiError extends Error {}

let base = "/api";

/** 浏览器直接下载的地址（文档包、题目模板） */
/** 评测所用文档解析后的全文；pages 为真时文本里有「--- 第 N 页 ---」分隔 */
export interface EvalDoc {
  name: string;
  format: string;
  pages: boolean;
  text: string;
  truncated: boolean;
}

/** 新建测试集：source 是 upload（上传文档出题）、import（只用自己的题）、kb（从知识库取文档） */
export interface CreateBuildBody {
  name: string;
  mix: Record<string, number>;
  source: "upload" | "kb" | "import";
  files?: { name: string; content: string }[];
  table?: { name: string; content: string };
  system_id?: string;
  kb_ids?: string[];
  kb_names?: Record<string, string>;
  after_ready?: AfterReady;
}

export function downloadUrl(path: string): string {
  return `${base}${path}`;
}

export function setApiBase(api: string): void {
  base = api.replace(/\/$/, "");
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    throw new ApiError(`服务返回了非 JSON 内容（HTTP ${res.status}）`);
  }
  if (!res.ok) throw new ApiError(data?.error ?? `请求失败（HTTP ${res.status}）`);
  return data as T;
}

export const api = {
  state: () => request<{ running: string | null; evals: EvalRow[] }>("GET", "/state"),
  datasets: () => request<DatasetInfo[]>("GET", "/datasets"),
  datasetDetail: (path: string) => request<DatasetDetail>("GET", `/datasets/detail?path=${encodeURIComponent(path)}`),
  evalDoc: (id: string, name: string) => request<EvalDoc>("GET", `/evals/${id}/doc?name=${encodeURIComponent(name)}`),
  datasetDoc: (path: string, name: string) =>
    request<{ name: string; text: string | null; truncated?: boolean; message?: string }>(
      "GET", `/datasets/doc?path=${encodeURIComponent(path)}&name=${encodeURIComponent(name)}`),
  systems: () => request<UiSystem[]>("GET", "/systems"),
  addSystem: (s: Partial<UiSystem>) => request<UiSystem>("POST", "/systems", s),
  deleteSystem: (id: string) => request<{ ok: true }>("DELETE", `/systems/${id}`),
  updateSystem: (id: string, s: Partial<UiSystem>) => request<UiSystem>("PUT", `/systems/${id}`, s),
  updateConfig: (systemId: string, configId: string, c: Partial<UiConfig>) =>
    request<UiConfig>("PUT", `/systems/${systemId}/configs/${configId}`, c),
  testSystem: (id: string) => request<{ ok: boolean; message: string }>("POST", `/systems/${id}/test`, {}),
  probeSystem: (body: { base_url: string; api_key_env: string; http: Record<string, unknown>; step: string; values: Record<string, string>; params?: Record<string, unknown> }) =>
    request<{ ok: boolean; request: string; status_code?: number; json?: unknown; error?: string; text?: string }>("POST", "/systems/probe", body),
  addConfig: (systemId: string, c: Partial<UiConfig>) => request<UiConfig>("POST", `/systems/${systemId}/configs`, c),
  deleteConfig: (systemId: string, configId: string) =>
    request<{ ok: true }>("DELETE", `/systems/${systemId}/configs/${configId}`),
  systemChats: (systemId: string) =>
    request<{ id: string; name: string; model: string; kbs: { id: string; name: string; documents?: number | null }[] }[]>("GET", `/systems/${systemId}/chats`),
  demoInfo: () => request<{ available: boolean; url?: string }>("GET", "/demo"),
  setupDemo: () => request<UiSystem>("POST", "/demo/setup", {}),
  chatParams: (systemId: string, chatId: string) =>
    request<Record<string, unknown>>("GET", `/systems/${systemId}/chats/${encodeURIComponent(chatId)}/params`),
  systemKbs: (systemId: string) => request<{ id: string; name: string; documents: number }[]>("GET", `/systems/${systemId}/kbs`),
  builds: () => request<{ running: string | null; builds: BuildSummary[]; default_mix: Record<string, number> }>("GET", "/builds"),
  buildDetail: (id: string) => request<BuildDetail>("GET", `/builds/${id}`),
  createBuild: (body: CreateBuildBody) => request<BuildSummary>("POST", "/builds", body),
  probeAssistants: (picks: EvalPick[]) =>
    request<{ results: { name: string; ok: boolean; message: string }[] }>("POST", "/evals/probe-assistants", { picks }),
  importMore: (id: string, table: { name: string; content: string }) => request<BuildSummary & { import_report?: BuildDetail["import_report"] }>("POST", `/builds/${id}/import`, { table }),
  generateMore: (id: string, mix: Record<string, number>) => request<BuildSummary>("POST", `/builds/${id}/generate`, { mix }),
  stopBuild: (id: string) => request<{ ok: boolean }>("POST", `/builds/${id}/stop`, {}),
  reverifyBuild: (id: string) => request<BuildSummary>("POST", `/builds/${id}/verify`, {}),
  preflight: (body: { dataset_path?: string; build_id?: string; picks: EvalPick[] }) =>
    request<{ results: PreflightResult[] }>("POST", "/evals/preflight", body),
  reviewBuildItem: (id: string, cid: string, action: "delete" | "restore" | "confirm") =>
    request<BuildItem>("POST", `/builds/${id}/items/${cid}/${action}`, {}),
  batchBuildItems: (id: string, ids: string[], action: "delete" | "restore" | "confirm") =>
    request<{ done: number; skipped: { id: string; reason: string }[] }>("POST", `/builds/${id}/items/batch`, { ids, action }),
  saveBuildItem: (id: string, cid: string | null, body: Record<string, unknown>) =>
    cid ? request<BuildItem>("PUT", `/builds/${id}/items/${cid}`, body) : request<BuildItem>("POST", `/builds/${id}/items`, body),
  deleteDataset: (path: string) => request<{ ok: true; evals_deleted: number }>("DELETE", `/datasets?path=${encodeURIComponent(path)}`),
  deleteBuild: (id: string) => request<{ ok: true }>("DELETE", `/builds/${id}`),
  buildDoc: (id: string, name: string) =>
    request<{ name: string; text: string | null; truncated?: boolean; message?: string }>("GET", `/builds/${id}/doc?name=${encodeURIComponent(name)}`),
  settings: () => request<JudgeSettings>("GET", "/settings"),
  saveSettings: (s: JudgeSettings) => request<JudgeSettings>("PUT", "/settings", s),
  testJudge: () => request<{ ok: boolean; message: string }>("POST", "/settings/test", {}),
  createEval: (body: { dataset_path?: string; build_id?: string; picks: EvalPick[]; name?: string; repeats: number; concurrency: number }) =>
    request<EvalMeta>("POST", "/evals", body),
  evalDetail: (id: string) => request<EvalDetail>("GET", `/evals/${id}`),
  startEval: (id: string) => request<EvalDetail>("POST", `/evals/${id}/start`, {}),
  stopEval: (id: string) => request<{ ok: true }>("POST", `/evals/${id}/stop`, {}),
  deleteEval: (id: string) => request<{ ok: true }>("DELETE", `/evals/${id}`),
  evalReport: (id: string, participant?: string) =>
    request<ReportData | CompareData>("GET", `/evals/${id}/report${participant ? `?participant=${encodeURIComponent(participant)}` : ""}`),
};
