// 与 Python 端 knoweval.report_html 组装的数据一一对应

export type Verdict = "CORRECT" | "ABSTAIN" | "WRONG" | "ERROR";

export interface Rate {
  k: number;
  n: number;
  value: number | null;
  ci: [number, number] | null;
}

export interface Summary {
  accuracy: Rate;
  confident_wrong: Rate;
  abstain_correct: Rate;
  over_abstain: Rate;
  citation_verifiable: Rate;
  citation_precision: Rate;
  image_hit: Rate;
  image_misfit: Rate;
  error_count: number;
  needs_review: number;
  total: number;
  attribution: Record<string, number>;
  by_qtype: Record<string, Summary>;
  groups: Record<string, Record<string, Summary>>;
}

export interface Evidence {
  doc: string;
  page: number | null;
  page_end?: number | null;
  modality: string;
  quote: string;
}

export interface Question {
  id: string;
  domain: string;
  qtype: string[];
  question: string;
  answer: string;
  answer_type: string;
  unit?: string | null;
  tolerance?: number | null;
  evidence: Evidence[];
  hops: number;
}

export interface Citation {
  doc: string;
  page: number | null;
  text: string;
}

export interface Answer {
  text: string | null;
  raw_text?: string | null;
  citations: Citation[];
  contexts: Citation[] | null;
  latency_ms: number | null;
  error: string | null;
}

export interface Judgement {
  verdict: Verdict;
  reason: string;
  method: "rule" | "llm";
  needs_review: boolean;
  citation: { verifiable: boolean; matched: number; total: number; matches: { citation: number; evidence: number; coverage: number }[] } | null;
  attribution: "retrieval" | "generation" | null;
}

export interface RunRecord {
  answer: Answer;
  judgement: Judgement;
}

export interface Review {
  qid: string;
  repeat: number;
  verdict: Verdict;
  machine_verdict: Verdict;
  reason: string;
  by: string;
  at: string;
}

export interface ReportData {
  kind: "report";
  name: string;
  meta: Record<string, any>;
  summary: { repeats: Summary[]; noise: string[]; ingest?: any; repeats_reviewed?: Summary[]; human_review?: { reviewed: number; agreed: number; agreement: number | null } };
  questions: Question[];
  records: Record<string, Record<string, RunRecord>>;
  reviews: Review[];
  docs: { name: string; format: string }[];
}

export interface CompareSystem {
  name: string;
  accuracy: Rate;
  confident_wrong: Rate;
  abstain_correct: Rate;
  over_abstain: Rate;
  citation_verifiable: Rate;
  by_qtype: Record<string, Rate>;
  noise: number;
}

export interface CompareData {
  kind: "compare";
  compare: {
    systems: CompareSystem[];
    pairs: { first: string; second: string; only_first: number; only_second: number; p_value: number; significant: boolean }[];
    groups: Record<string, string[]>;
    matrix: Record<string, Verdict[]>;
  };
  questions: Question[];
  metas: Record<string, any>[];
  /** 每个参评项第 1 遍的逐题记录，顺序同 compare.systems；旧数据没有 */
  records?: Record<string, RunRecord>[];
}

export interface UiData {
  kind: "ui";
  api: string;
}

export type KnowEvalData = ReportData | CompareData | UiData;
