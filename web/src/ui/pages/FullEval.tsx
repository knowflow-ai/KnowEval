import { Button, Card, Checkbox, Empty, Radio, Space, Typography } from "antd";
import { useState } from "react";

import type { EvalPick, UiConfig, UiSystem } from "../api";
import { type TestsetOption, configSummary, testsetParam } from "../logic";
import { go } from "../UiApp";
import { ConfirmStep, DEFAULT_RUN, useLaunch } from "./evalCommon";
import EvalWizard, { type WizardStep } from "./EvalWizard";

const pickKey = (systemId: string, configId: string) => `${systemId}/${configId}`;
/** 没加配置的系统：直接用它的问答接口（后端同名常量） */
const DEFAULT_ENTRY = "__default__";

/** 一个对比项评测时会发生什么 */
function entryEffect(sys: UiSystem, c?: UiConfig): string {
  if (sys.adapter !== "http") return `新建知识库和助手，上传这套文档 · ${c ? configSummary(c) : ""}`;
  const how = (sys.http as Record<string, unknown>)?.ingest ? "按入库接口新建知识库并上传这套文档" : "直接问它的问答接口：文档要已经导入它的系统";
  return c ? `${how} · ${configSummary(c)}` : how;
}

/** 完整测试：选一套带文档的题，评测台按建库配置建知识库和助手（或问自研系统的接口），从入库到回答整条链路一起测 */
export default function FullEval({ testsets, systems, preselect, modeStep }: {
  testsets: TestsetOption[];
  systems: UiSystem[];
  preselect?: string;
  /** 第一步「测什么」：由页面给出，两条路共用 */
  modeStep: WizardStep;
}) {
  const first = testsets.find((o) => o.key === preselect) ?? testsets[0];
  const [testsetKey, setTestsetKey] = useState(first?.key ?? "");
  const [picked, setPicked] = useState<string[]>([]);
  const [opts, setOpts] = useState(DEFAULT_RUN);
  const { launch, busy, checking, modal } = useLaunch();

  const testset = testsets.find((t) => t.key === testsetKey);
  // 每个系统的建库配置；自研系统没加配置时只有一项「问答接口」，原样用接口模板。已有助手走「测已有助手」，这里不列
  const entries = systems.flatMap((sys) => {
    if (sys.adapter === "http" && !sys.configs.length) {
      return [{ sys, items: [{ key: pickKey(sys.id, DEFAULT_ENTRY), label: "问答接口", effect: entryEffect(sys) }] }];
    }
    const cfgs = sys.configs;
    return cfgs.length ? [{ sys, items: cfgs.map((c) => ({ key: pickKey(sys.id, c.id), label: c.name, effect: entryEffect(sys, c) })) }] : [];
  });

  const chooseTestset = (key: string) => setTestsetKey(key);
  const toggle = (key: string, on: boolean) => setPicked((prev) => (on ? [...prev, key] : prev.filter((k) => k !== key)));
  const submit = () => {
    const picks: EvalPick[] = picked.map((k) => { const [system_id, config_id] = k.split("/"); return { system_id, config_id }; });
    launch(testsetParam(testsetKey), picks, opts);
  };

  const pickedLabels = picked.map((k) => {
    const item = entries.flatMap((e) => e.items.map((it) => ({ ...it, sys: e.sys }))).find((it) => it.key === k);
    return item ? `${item.sys.name}：${item.label}` : k;
  });
  const testsetStep = (
  <Card size="small">
    {testsets.length === 0 ? <Empty description="还没有带文档的测试集（只导入了题目的测试集没有文档，建不了库）"><Button onClick={() => go({ page: "datasets" })}>去新建测试集</Button></Empty> : (
      <Radio.Group value={testsetKey} onChange={(e) => chooseTestset(e.target.value)} style={{ width: "100%" }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          {testsets.map((t) => (
            <Radio key={t.key} value={t.key}>
              <Typography.Text strong>{t.label}</Typography.Text>
              <Typography.Text type="secondary" style={{ marginLeft: 8 }}>{t.questions} 题{t.kind === "dataset" ? " · 官方" : ""}</Typography.Text>
            </Radio>
          ))}
        </Space>
      </Radio.Group>
    )}
  </Card>
  );
  const buildStep = (
  <Card size="small">
    {entries.length === 0 ? (
      <Empty description="还没有建库配置"><Button onClick={() => go({ page: "systems" })}>去「系统」页加配置</Button></Empty>
    ) : (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          每份建库配置新建一个知识库和助手，把这套文档传进去（同一份配置跑过一次会复用那个库），适合比较分块方式、解析方式；
          只接了问答接口的自研系统直接提问，开始前会先试问几道题，确认文档已经导入。勾两个以上就是对比，每个独立跑完全部题目。
        </Typography.Text>
        {entries.map(({ sys, items }) => (
          <div key={sys.id}>
            <Typography.Text strong>{sys.name}</Typography.Text>
            <Space direction="vertical" style={{ display: "flex", marginTop: 6 }}>
              {items.map((it) => (
                <Checkbox key={it.key} checked={picked.includes(it.key)} onChange={(e) => toggle(it.key, e.target.checked)}>
                  <Typography.Text>{it.label}</Typography.Text>
                  <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 13 }}>{it.effect}</Typography.Text>
                </Checkbox>
              ))}
            </Space>
          </div>
        ))}
        <Typography.Link onClick={() => go({ page: "systems" })}>没有想要的？去「系统」页接入或加建库配置 →</Typography.Link>
      </Space>
    )}
  </Card>
  );
  const confirmStep = (
    <ConfirmStep opts={opts} onChange={setOpts} rows={[
      { label: "题目", value: testset ? `${testset.label}（${testset.questions} 题）` : "" },
      { label: "提问对象", value: <Space direction="vertical" size={2}>{pickedLabels.map((l) => <span key={l}>{l}</span>)}</Space> },
      { label: "开始前", value: "先试问几道题，回答大多报错或找不到时提醒你；要新建知识库的项入库完成后才提问" },
    ]} />
  );

  const steps: WizardStep[] = [
    modeStep,
    { key: "testset", title: "选测试集", description: testset?.label, content: testsetStep, ready: !!testsetKey,
      hint: "先选一套测试集" },
    { key: "build", title: "选建库方式", description: picked.length ? `已选 ${picked.length} 项` : undefined, content: buildStep,
      ready: picked.length > 0, hint: "至少选一项" },
    { key: "confirm", title: "确认开始", content: confirmStep, ready: true },
  ];

  return (
    <>
      <EvalWizard steps={steps} finish={{ label: checking ? "试问检查中…" : `开始评测${picked.length > 1 ? `（${picked.length} 项对比）` : ""}`,
        loading: busy, disabled: !testsetKey || picked.length === 0, onClick: submit }} />
      {modal}
    </>
  );
}
