import type { Adapter, UiConfig, UiSystem } from "./api";

/*
 * 新建配置时参数框里预填的默认值：不填时被测系统实际用的就是这些，写出来用户才知道能改什么、怎么写。
 * 以 KnowFlow 源码为准：助手表默认值见 api/db/db_models.py 的 Dialog，建助手接口见 api/apps/sdk/chat.py，
 * 建库的 parser_config 见 api/utils/validation_utils.py 的 ParserConfig。
 */

// 建助手接口不传提示词时用的默认系统提示词（原样照抄，含换行与缩进）
const SDK_SYSTEM_PROMPT =
  "You are an intelligent assistant. Please summarize the content of the knowledge base to answer the question. "
  + "Please list the data in the knowledge base and answer in detail. When all knowledge base content is irrelevant to the question, "
  + "your answer must include the sentence \"The answer you are looking for is not found in the knowledge base!\" "
  + "Answers need to consider chat history.\n      Here is the knowledge base:\n      {knowledge}\n      The above is the knowledge base.";

export function defaultChatParams(adapter: Adapter): Record<string, unknown> {
  if (adapter === "http") return {};
  const knowflow = adapter === "knowflow";
  return {
    // model_name 为 null 时用租户的默认对话模型；用 ColPali 时必须换成视觉模型
    llm: { model_name: null, temperature: 0.1, top_p: 0.3, presence_penalty: 0.4, frequency_penalty: 0.7, max_tokens: 512 },
    prompt: {
      similarity_threshold: 0.2,
      vector_similarity_weight: 0.3,
      top_n: 6,
      top_k: 1024,
      rerank_model: "",
      ...(knowflow ? { use_colpali: false, sag_search_strategy: "multi" } : {}),
      use_kg: false,
      reasoning: false,
      toc_enhance: false,
      keyword: false,
      cross_languages: [],
      refine_multiturn: true,
      show_quote: true,
      empty_response: "Sorry! No relevant content was found in the knowledge base!",
      prompt: SDK_SYSTEM_PROMPT,
    },
    ...(knowflow ? { vlm_top_n: 3 } : {}),
  };
}

export function defaultDatasetParams(adapter: Adapter): Record<string, unknown> {
  if (adapter === "http") return {};
  const knowflow = adapter === "knowflow";
  return {
    chunk_method: knowflow ? "smart" : "naive",
    // KnowFlow 建库必须写明嵌入模型
    ...(knowflow ? { embedding_model: "BAAI/bge-m3@SILICONFLOW" } : {}),
    parser_config: {
      layout_recognize: knowflow ? "MinerU" : "DeepDOC",
      chunk_token_num: 512,
      auto_keywords: 0,
      auto_questions: 0,
      html4excel: false,
      raptor: { use_raptor: false },
      graphrag: { use_graphrag: false },
      ...(knowflow ? { sag: { use_sag: false } } : {}),
    },
  };
}

const pretty = (v: Record<string, unknown> | undefined) => (v && Object.keys(v).length ? JSON.stringify(v, null, 2) : "");

/** 配置弹窗的初始值：新建时预填默认参数；修改时原样显示存的参数，建助手参数为空（等于全用默认）时也显示默认值 */
export function configFormInitial(system: Pick<UiSystem, "adapter">, editing: UiConfig | null) {
  if (!editing) {
    // 系统页只建建库配置；测已有助手在新建评测里直接选助手
    return { dataset: pretty(defaultDatasetParams(system.adapter)), chat: pretty(defaultChatParams(system.adapter)) };
  }
  const chat = Object.keys(editing.chat ?? {}).length ? editing.chat : defaultChatParams(system.adapter);
  return { name: editing.name, dataset: pretty(editing.dataset), chat: pretty(chat), api_key_env: editing.api_key_env ?? "" };
}
