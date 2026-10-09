import { Alert, App, Button, Card, Collapse, Empty, Input, Radio, Select, Skeleton, Space, Table, Tag, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import { useEffect, useRef, useState } from "react";

import { type EvalPick, type UiSystem, api, downloadUrl } from "../api";
import { encodeFiles } from "../files";
import { type Chat, type VariantDraft, buildPicks, diffParams, sharedKbs, splitChats, testsetsForAssistants } from "../assistantEval";
import { ADAPTER_LABEL, type TestsetOption, flatParams, parseJsonObject, testsetParam } from "../logic";
import { mixTotal, normalizeMix } from "../mix";
import { go } from "../UiApp";
import { ConfirmStep, DEFAULT_RUN, useLaunch } from "./evalCommon";
import EvalWizard, { type WizardStep } from "./EvalWizard";
import MixEditor from "./MixEditor";
import VariantsCard from "./VariantsCard";

const GENERATE = "__generate__";

/**
 * 测已有助手：助手里已经有知识库和文档，问的就是用户在 KnowFlow/RAGFlow 里配好的那个助手。
 * 题目跟着助手走：出自它的知识库的测试集排在前面；没有就从它的知识库出题，出完自动开始评测。
 */
export default function AssistantEval({ testsets, systems, defaultMix, preselect, modeStep }: {
  testsets: TestsetOption[];
  systems: UiSystem[];
  defaultMix: Record<string, number>;
  /** 从测试集页点「评测」进来：选好它的系统、挂了它的知识库的助手和这套题 */
  preselect?: string;
  /** 第一步「测什么」：由页面给出，两条路共用 */
  modeStep: WizardStep;
}) {
  const { message } = App.useApp();
  const candidates = systems.filter((s) => s.adapter !== "http");
  const pre = testsets.find((t) => t.key === preselect && t.source_kb && candidates.some((s) => s.id === t.source_kb!.system_id));
  const [systemId, setSystemId] = useState(pre?.source_kb?.system_id ?? candidates[0]?.id ?? "");
  const [chats, setChats] = useState<Chat[] | null>(null);
  const [chatError, setChatError] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  const [opts, setOpts] = useState(DEFAULT_RUN);
  const [mix, setMix] = useState(() => normalizeMix(defaultMix));
  // 出新题时同时导入自己的题目（CSV/JSONL）：先导入，再按配比让模型补题，放在同一套里
  const [myTable, setMyTable] = useState<UploadFile[]>([]);
  const canGenerate = mixTotal(mix) > 0 || myTable.length > 0;
  const [creating, setCreating] = useState(false);
  const [probing, setProbing] = useState(false);
  // 出题前试问助手发现的问题（接口报错等）：列在按钮上方，不开始出题
  const [broken, setBroken] = useState<{ name: string; message: string }[]>([]);
  // 参数对照：只选了一个助手时，以它为基准、每组只改几项参数
  const [variants, setVariants] = useState<VariantDraft[]>([]);
  const { launch, busy, checking, modal } = useLaunch();

  useEffect(() => {
    if (!systemId) return;
    let alive = true;
    setChats(null);
    setChatError("");
    setPicked([]);
    setSource("");
    setBroken([]);
    setVariants([]);
    api.systemChats(systemId)
      .then((list) => {
        if (!alive) return;
        setChats(list);
        // 预选的那套题：先勾上第一个挂了它全部知识库的助手
        const kbs = pre?.source_kb?.system_id === systemId ? pre.source_kb.kbs : [];
        const fit = kbs.length ? list.find((c) => kbs.every((k) => c.kbs.some((ck) => ck.id === k.id))) : undefined;
        if (fit) setPicked([fit.id]);
      })
      .catch((e: any) => alive && setChatError(e.message ?? String(e)));
    return () => {
      alive = false;
    };
  }, [systemId]);

  const chosen = (chats ?? []).filter((c) => picked.includes(c.id));
  const system = candidates.find((s) => s.id === systemId);
  // 参数对照是写给某一个基准助手的：换了基准（不是暂时多选）就清掉，免得套到另一个助手上
  const baseId = chosen.length === 1 ? chosen[0].id : null;
  const lastBase = useRef<string | null>(null);
  useEffect(() => {
    if (baseId && lastBase.current && baseId !== lastBase.current) setVariants([]);
    if (baseId) lastBase.current = baseId;
  }, [baseId]);
  // 基准助手现在的全部参数：每组对照预填它，改哪项就是对照哪项
  const [baseParams, setBaseParams] = useState<Record<string, unknown> | null>(null);
  const [baseParamsError, setBaseParamsError] = useState("");
  useEffect(() => {
    setBaseParams(null);
    setBaseParamsError("");
    if (!baseId) return undefined;
    let alive = true;
    api.chatParams(systemId, baseId)
      .then((p) => alive && setBaseParams(p))
      .catch((e: any) => alive && setBaseParamsError(e.message ?? String(e)));
    return () => {
      alive = false;
    };
  }, [systemId, baseId]);
  const matched = testsetsForAssistants(testsets, systemId, chosen);
  const shared = sharedKbs(chosen);
  // 换了助手后，默认选第一套匹配的题；没有就从助手的知识库出题
  const fallback = (matched.find((t) => t.key === preselect) ?? matched[0])?.key ?? (shared ? GENERATE : "");
  const valid = source === GENERATE ? !!shared : matched.some((t) => t.key === source);
  const effective = valid ? source : fallback;

  if (!candidates.length) {
    const empty = (
      <Card size="small">
        <Empty description="还没有接入 KnowFlow 或 RAGFlow">
          <Button onClick={() => go({ page: "systems" })}>去「系统」页接入</Button>
        </Empty>
      </Card>
    );
    return <EvalWizard steps={[modeStep, { key: "chat", title: "选助手", content: empty, ready: false, hint: "先接入系统" }]}
      finish={{ label: "开始评测", disabled: true, loading: false, onClick: () => undefined }} />;
  }

  // 参评勾选项：选中的助手，加上（只选一个助手时）它的参数对照；参数写错就提示，不往下走
  const makePicks = (): EvalPick[] | null => {
    try {
      return buildPicks(systemId, chosen, variants, baseParams);
    } catch (e: any) {
      message.error(e.message ?? String(e));
      return null;
    }
  };
  const entryCount = chosen.length + (chosen.length === 1 ? variants.length : 0);
  const generate = async () => {
    const picks = makePicks();
    if (!shared || !picks) return;
    setBroken([]);
    setProbing(true);
    try {
      // 先问助手一句：接口报错（如嵌入模型没了）当场发现，不白等几分钟出题
      const { results } = await api.probeAssistants(picks);
      const bad = results.filter((r) => !r.ok);
      if (bad.length) {
        setBroken(bad);
        return;
      }
    } catch (e: any) {
      message.error(e.message ?? String(e));
      return;
    } finally {
      setProbing(false);
    }
    setCreating(true);
    try {
      const name = `${chosen.map((c) => c.name).join("、")}的题目`.replace(/[\\/]/g, "-");
      const meta = await api.createBuild({
        source: "kb", name, mix: normalizeMix(mix), system_id: systemId, kb_ids: shared.map((k) => k.id),
        ...(myTable.length ? { table: (await encodeFiles(myTable))[0] } : {}),
        kb_names: Object.fromEntries(shared.map((k) => [k.id, k.name])),
        after_ready: { picks, repeats: opts.repeats, concurrency: opts.concurrency, name: opts.name.trim() },
      });
      message.success("已开始从助手的知识库出题，出完自动开始评测");
      go({ page: "build", id: meta.id });
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setCreating(false);
    }
  };
  const submit = () => {
    if (effective === GENERATE) return generate();
    const picks = makePicks();
    return picks ? launch(testsetParam(effective), picks, opts) : undefined;
  };

  const { mine, temp } = splitChats(chats ?? [], query);
  const toggleChat = (id: string, on: boolean) => {
    // 换了助手，题目重新按匹配情况默认选，免得还选着上一组助手的题
    setSource("");
    setBroken([]);
    setPicked((prev) => (on ? [...prev, id] : prev.filter((x) => x !== id)));
  };
  // 助手表：名字、挂的知识库（逐个列出、带文档数）、对话模型；没挂知识库的答不了题，不能选
  const chatTable = (rows: Chat[]) => (
    <Table<Chat> size="small" rowKey="id" pagination={false} dataSource={rows}
      rowSelection={{
        selectedRowKeys: picked,
        onSelect: (c, on) => toggleChat(c.id, on),
        hideSelectAll: true,
        getCheckboxProps: (c) => ({ disabled: !c.kbs.length }),
      }}
      // 点整行也能选；点的是勾选框本身时交给 rowSelection，别切两次
      onRow={(c) => ({
        onClick: (e) => {
          if (!c.kbs.length || (e.target as HTMLElement).closest(".ant-table-selection-column")) return;
          toggleChat(c.id, !picked.includes(c.id));
        },
        style: { cursor: c.kbs.length ? "pointer" : "not-allowed" },
      })}
      columns={[
        { title: "助手", dataIndex: "name", width: 220, render: (n: string) => <Typography.Text strong>{n}</Typography.Text> },
        { title: "挂的知识库", render: (_, c) => (c.kbs.length ? (
          <Space size={[4, 4]} wrap>
            {c.kbs.map((k) => (
              <Tag key={k.id} color="blue" style={{ marginInlineEnd: 0 }}>
                {k.name || k.id}{typeof k.documents === "number" ? ` · ${k.documents} 份文档` : ""}
              </Tag>
            ))}
          </Space>
        ) : <Tag color="error">没有挂知识库，没法答题</Tag>) },
        { title: "对话模型", dataIndex: "model", width: 280, ellipsis: true,
          render: (m: string) => <Typography.Text type="secondary" style={{ fontSize: 13 }}>{m || "—"}</Typography.Text> },
      ]} />
  );
  const pickedTemp = temp.filter((c) => picked.includes(c.id)).length;

  const testsetRadio = (t: TestsetOption) => (
    <Radio key={t.key} value={t.key}>
      <Typography.Text strong>{t.label}</Typography.Text>
      <Typography.Text type="secondary" style={{ marginLeft: 8 }}>{t.questions} 题</Typography.Text>
    </Radio>
  );

  const chooseStep = (
    <Card size="small">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {/* 助手属于哪个系统要一直看得见：只接了一个系统也显示，不悄悄替用户选 */}
        <Space wrap>
          <Typography.Text strong>系统</Typography.Text>
          <Select value={systemId} onChange={setSystemId} style={{ minWidth: 320 }}
            options={candidates.map((s) => ({ value: s.id, label: `${s.name}（${ADAPTER_LABEL[s.adapter]} · ${s.base_url}）` }))} />
          <Typography.Link onClick={() => go({ page: "systems" })}>接入更多系统 →</Typography.Link>
        </Space>
        {chatError ? <Alert type="error" showIcon message={`取助手列表失败：${chatError}`} />
          : chats === null ? <Skeleton active paragraph={{ rows: 2 }} />
          : chats.length === 0 ? <Alert type="info" showIcon message="这个系统里还没有助手"
              description="先去 KnowFlow 里建一个挂好知识库的助手；或者改用「完整测试」，由评测台建知识库和助手。" />
          : (
            <Space direction="vertical" style={{ display: "flex" }}>
              {chats.length > 8 && (
                <Input.Search allowClear placeholder="按助手名或知识库名搜索" value={query} onChange={(e) => setQuery(e.target.value)}
                  style={{ maxWidth: 360 }} />
              )}
              {mine.length > 0 && chatTable(mine)}
              {!mine.length && !temp.length && <Typography.Text type="secondary">没有名字或知识库匹配「{query}」的助手</Typography.Text>}
              {temp.length > 0 && (
                <Collapse size="small" ghost defaultActiveKey={mine.length ? [] : ["temp"]} items={[{
                  key: "temp",
                  label: <Typography.Text type="secondary">评测台以前建的临时助手（{temp.length} 个，默认参数，一般不用测）{pickedTemp ? `，已选 ${pickedTemp} 个` : ""}</Typography.Text>,
                  children: chatTable(temp),
                }]} />
              )}
            </Space>
          )}
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          问的就是你配好的助手：模型、提示词、ColPali、SAG 等都以它为准，评测台不建、不改。勾两个以上就是对比；
          只勾一个时，下一步之后还可以给它加参数对照。
        </Typography.Text>
      </Space>
    </Card>
  );

  const questionsStep = (
    <Card size="small">
      <Radio.Group value={effective} onChange={(e) => setSource(e.target.value)} style={{ width: "100%" }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          {matched.map(testsetRadio)}
          <Radio value={GENERATE} disabled={!shared}>
            <Typography.Text strong>从助手的知识库出一套新题</Typography.Text>
            <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 13 }}>{shared
              ? `取 ${shared.map((k) => k.name || k.id).join("、")} 的文档，按下面的题型配比出约 ${mixTotal(mix)} 道题并自动校验，可以同时导入你自己的题，出完自动开始评测（要几分钟）`
              : "选的助手挂的知识库不一样，没法共用一套题：分开评测，或改用「完整测试」"}</Typography.Text>
          </Radio>
          {effective === GENERATE && (
            <div style={{ marginLeft: 24, padding: 12, background: "#fafafa", borderRadius: 6 }}>
              <MixEditor value={mix} onChange={setMix} />
              <div style={{ marginTop: 12 }}>
                <Typography.Text strong style={{ fontSize: 13 }}>同时导入我的题目（可选，CSV 或 JSONL）</Typography.Text>
                <Typography.Link href={downloadUrl("/builds/import-template.csv")} style={{ marginLeft: 8, fontSize: 12 }}>下载模板</Typography.Link>
                <div style={{ fontSize: 12, color: "rgba(0,0,0,0.45)", margin: "2px 0 6px" }}>
                  先导入你的题，再让模型补题，放在同一套里；模型补的题和你的重复的会去掉。只用你的题就把上面的配比全填 0。报告按题目来源分开统计。
                </div>
                <Upload maxCount={1} fileList={myTable} beforeUpload={() => false} accept=".csv,.jsonl,.ndjson"
                  onChange={({ fileList }) => setMyTable(fileList.slice(-1))}>
                  <Button size="small">选择题目文件</Button>
                </Upload>
              </div>
              {!canGenerate && <Typography.Text type="danger" style={{ fontSize: 12 }}>至少出一道题，或者导入你的题目</Typography.Text>}
            </div>
          )}
        </Space>
      </Radio.Group>
    </Card>
  );

  const variantsStep = chosen.length === 1
    ? <VariantsCard base={chosen[0].name} baseParams={baseParams} baseParamsError={baseParamsError} value={variants} onChange={setVariants} />
    : <Alert type="info" showIcon message="参数对照只在选了一个助手时可用"
        description="参数对照以一个助手为基准、只改几项参数。现在选了多个助手，这次直接对比这几个助手；想做参数对照，回到「选助手」只留一个。" />;

  const usingSet = matched.find((t) => t.key === effective);
  // 确认页上每组对照改了什么（参数写错的先不报，开始时再拦）
  const variantChange = (v: VariantDraft) => {
    try {
      const edited = parseJsonObject(v.json);
      const changed = flatParams(baseParams ? diffParams(edited, baseParams) : edited);
      return changed.length ? `改了 ${changed.join(" · ")}` : "没改任何参数";
    } catch {
      return "参数不是合法的 JSON";
    }
  };
  const confirmStep = (
    <ConfirmStep opts={opts} onChange={setOpts} rows={[
      { label: "系统", value: system ? `${system.name}（${ADAPTER_LABEL[system.adapter]} · ${system.base_url}）` : "" },
      { label: "提问对象", value: (
        <Space direction="vertical" size={2}>
          {chosen.map((c) => <span key={c.id}>{c.name}<Typography.Text type="secondary">（已有助手，原样问）</Typography.Text></span>)}
          {chosen.length === 1 && variants.map((v, i) => (
            // eslint-disable-next-line react/no-array-index-key
            <span key={i}>{chosen[0].name} · {v.name.trim() || `对照 ${i + 1}`}
              <Typography.Text type="secondary">（参数对照：{variantChange(v)}；临时助手，问完删除）</Typography.Text></span>
          ))}
        </Space>
      ) },
      { label: "题目", value: effective === GENERATE
        ? [myTable.length ? `导入你的题目（${myTable[0].name}）` : "",
           mixTotal(mix) ? `从 ${shared?.map((k) => k.name || k.id).join("、")} 出新题约 ${mixTotal(mix)} 道` : ""].filter(Boolean).join(" + ") + "，出完自动开始"
        : `${usingSet?.label}（${usingSet?.questions} 题）` },
      { label: "开始前", value: effective === GENERATE ? "先试问一次每个提问对象，接口报错就不出题" : "先试问几道题，回答大多报错或找不到时提醒你" },
    ]}>
      {broken.length > 0 && (
        <Alert type="error" showIcon message="试问助手出错，没有开始出题" closable onClose={() => setBroken([])}
          description={<>
            <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
              {broken.map((b) => <li key={b.name}><b>{b.name}</b>：{b.message}</li>)}
            </ul>
            <div>这是被测系统那边的问题（比如知识库用的嵌入模型被删了、对话模型下线了），在 KnowFlow 里处理好再来。</div>
          </>} />
      )}
    </ConfirmStep>
  );

  const steps: WizardStep[] = [
    modeStep,
    { key: "chat", title: "选助手", description: `${system?.name ?? ""}${chosen.length ? ` · 已选 ${chosen.length} 个` : ""}`, content: chooseStep,
      ready: chosen.length > 0, hint: "至少选一个助手" },
    { key: "questions", title: "选题目", description: !chosen.length ? undefined : effective === GENERATE
      ? `${myTable.length ? "我的题 + " : ""}出新题约 ${mixTotal(mix)} 道` : usingSet?.label,
      content: questionsStep, ready: !!effective && (effective !== GENERATE || canGenerate),
      hint: !shared && !matched.length ? "选的助手没有能共用的题" : "至少出一道题" },
    { key: "variants", title: "参数对照", description: chosen.length === 1 && variants.length ? `${variants.length} 组` : "可选",
      content: variantsStep, ready: true },
    { key: "confirm", title: "确认开始", content: confirmStep, ready: true },
  ];
  const finishLabel = checking ? "试问检查中…" : probing ? "试问助手中…" : effective === GENERATE ? "出题并评测"
    : `开始评测${entryCount > 1 ? `（${entryCount} 项对比）` : ""}`;

  return (
    <>
      <EvalWizard steps={steps} finish={{ label: finishLabel, loading: busy || creating || probing,
        disabled: !chosen.length || !effective || (effective === GENERATE && !canGenerate), onClick: submit }} />
      {modal}
    </>
  );
}
