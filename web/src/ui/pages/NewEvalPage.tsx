import { Alert, Card, Radio, Skeleton, Space, Typography } from "antd";
import { useEffect, useState } from "react";

import { type UiSystem, api } from "../api";
import { type TestsetOption, testsetOptions } from "../logic";
import AssistantEval from "./AssistantEval";
import FullEval from "./FullEval";
import type { WizardStep } from "./EvalWizard";
import PageHead from "./PageHead";

type Mode = "assistant" | "full";

/**
 * 新建评测：先选测什么。
 * - 测已有助手（KnowFlow/RAGFlow）：助手里已经有知识库和文档，选助手，题目跟着助手的知识库走；
 * - 完整测试：没有助手，或想比较建库方式：选一套带文档的题，评测台建知识库和助手（或问自研系统的接口）。
 */
export default function NewEvalPage({ preselect }: { preselect?: string }) {
  const [testsets, setTestsets] = useState<TestsetOption[] | null>(null);
  const [systems, setSystems] = useState<UiSystem[] | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [defaultMix, setDefaultMix] = useState<Record<string, number>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.datasets(), api.builds(), api.systems()]).then(([d, b, s]) => {
      const opts = testsetOptions(d, b.builds).filter((o) => o.usable);
      setTestsets(opts);
      setDefaultMix(b.default_mix);
      setSystems(s);
      // 从测试集页点「评测」进来：题目出自知识库的去测已有助手（问挂了这些库的助手），别的走完整测试；
      // 直接新建时，接了 KnowFlow/RAGFlow 就默认测已有助手
      const pre = preselect ? opts.find((o) => o.key === preselect) : undefined;
      const hasKbSystem = s.some((x) => x.adapter !== "http");
      setMode(pre ? (pre.source_kb && hasKbSystem ? "assistant" : "full") : hasKbSystem ? "assistant" : "full");
    }).catch((e) => setError(String(e.message ?? e)));
  }, [preselect]);

  if (error) return <Alert type="error" showIcon message={error} />;
  if (!testsets || !systems || !mode) return <Skeleton active />;

  // 第一步「测什么」：两条路共用，选完往下走的步骤按选择不同
  const modeStep: WizardStep = {
    key: "mode", title: "测什么", description: mode === "assistant" ? "已有助手" : "完整测试", ready: true,
    content: (
      <Card size="small">
        <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} style={{ width: "100%" }}>
          <Space direction="vertical" size={16}>
            <Radio value="assistant">
              <Typography.Text strong>测已有助手</Typography.Text>
              <div style={{ color: "rgba(0,0,0,0.45)", fontSize: 13 }}>
                KnowFlow/RAGFlow 里已经配好的助手，知识库和文档都在里面；题目出自它的知识库，还能对它做参数对照
              </div>
            </Radio>
            <Radio value="full">
              <Typography.Text strong>完整测试</Typography.Text>
              <div style={{ color: "rgba(0,0,0,0.45)", fontSize: 13 }}>
                还没有助手，或想比较分块、解析等建库方式：选一套带文档的题，评测台建知识库和助手；自研系统也走这里
              </div>
            </Radio>
          </Space>
        </Radio.Group>
      </Card>
    ),
  };

  return (
    <>
      <PageHead title="新建评测" desc="按步骤选好测什么、问谁、用哪些题，最后确认开始。开始前会先试问几道题，确认题目的文档在被测系统里。" />
      {mode === "assistant"
        ? <AssistantEval key="assistant" testsets={testsets} systems={systems} defaultMix={defaultMix} preselect={preselect} modeStep={modeStep} />
        : <FullEval key="full" testsets={testsets.filter((t) => t.docs > 0)} systems={systems} preselect={preselect} modeStep={modeStep} />}
    </>
  );
}
