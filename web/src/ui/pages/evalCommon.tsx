import { Alert, App, Button, Card, Descriptions, Input, InputNumber, Modal, Space, Typography } from "antd";
import { type ReactNode, useState } from "react";

import { type EvalPick, type PreflightResult, api } from "../api";
import { go } from "../UiApp";

/** 新建评测两条路共用的运行设置 */
export interface RunOpts {
  name: string;
  repeats: number;
  concurrency: number;
}

export const DEFAULT_RUN: RunOpts = { name: "", repeats: 2, concurrency: 4 };

/** 运行设置：评测名称、遍数、并发（新建评测最后一步直接展开） */
export function RunFields({ opts, onChange }: { opts: RunOpts; onChange: (o: RunOpts) => void }) {
  const { name, repeats, concurrency } = opts;
  return (
    <Space size={32} wrap align="end">
      <div>
        <div style={{ marginBottom: 6 }}><label htmlFor="ev-name">评测名称（可不填）</label></div>
        <Input id="ev-name" value={name} onChange={(e) => onChange({ ...opts, name: e.target.value })} placeholder="默认按提问对象生成" style={{ width: 280 }} />
      </div>
      <div>
        <div style={{ marginBottom: 6 }}><label htmlFor="ev-rep">每项跑几遍</label></div>
        <InputNumber id="ev-rep" min={1} max={5} value={repeats} onChange={(v) => onChange({ ...opts, repeats: v ?? 2 })} />
      </div>
      <div>
        <div style={{ marginBottom: 6 }}><label htmlFor="ev-conc">提问并发</label></div>
        <InputNumber id="ev-conc" min={1} max={16} value={concurrency} onChange={(v) => onChange({ ...opts, concurrency: v ?? 4 })} />
      </div>
      <Typography.Text type="secondary">跑两遍才能量出噪声底线，把真差异和模型抖动分开。</Typography.Text>
    </Space>
  );
}

/** 最后一步「确认开始」：汇总前面选的，加上运行设置 */
export function ConfirmStep({ rows, opts, onChange, children }: {
  rows: { label: string; value: ReactNode }[];
  opts: RunOpts;
  onChange: (o: RunOpts) => void;
  children?: ReactNode;
}) {
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title="确认要跑的评测">
        <Descriptions size="small" column={1} items={rows.map((r) => ({ key: r.label, label: r.label, children: r.value }))} />
      </Card>
      <Card size="small" title="运行设置">
        <RunFields opts={opts} onChange={onChange} />
      </Card>
      {children}
    </Space>
  );
}

export type TestsetRef = { build_id: string } | { dataset_path: string };

/**
 * 开始评测：先试问几道题，多数回答找不到或接口报错时弹窗让用户确认，再建评测并跳到详情页。
 * 返回 launch 函数、忙碌状态，以及要渲染的确认弹窗。
 */
export function useLaunch() {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [suspects, setSuspects] = useState<{ results: PreflightResult[]; run: () => void } | null>(null);

  const start = async (testset: TestsetRef, picks: EvalPick[], opts: RunOpts) => {
    setBusy(true);
    try {
      const meta = await api.createEval({ ...testset, picks, name: opts.name.trim(), repeats: opts.repeats, concurrency: opts.concurrency });
      message.success("评测已开始");
      go({ page: "eval", id: meta.id });
    } catch (e: any) {
      message.error(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const launch = async (testset: TestsetRef, picks: EvalPick[], opts: RunOpts) => {
    setChecking(true);
    try {
      const { results } = await api.preflight({ ...testset, picks });
      const bad = results.filter((r) => r.status === "suspect");
      if (bad.length) {
        setSuspects({ results: bad, run: () => start(testset, picks, opts) });
        return;
      }
    } catch (e: any) {
      message.error(e.message ?? String(e));
      return;
    } finally {
      setChecking(false);
    }
    await start(testset, picks, opts);
  };

  // 全部接口报错时照样跑只会得到一轮全错：「仍然开始」降成不显眼的危险操作
  const allErrors = !!suspects?.results.length && suspects.results.every((r) => r.all_errors);
  const modal = (
    <Modal open={!!suspects} title="试问检查发现问题" width={720} onCancel={() => setSuspects(null)}
      footer={[
        <Button key="go" danger={allErrors} type={allErrors ? "text" : "default"}
          onClick={() => { const run = suspects!.run; setSuspects(null); run(); }}>仍然开始</Button>,
        <Button key="fix" type="primary" onClick={() => setSuspects(null)}>先去处理</Button>,
      ]}>
      <Typography.Paragraph type="secondary">
        {allErrors
          ? "开跑前抽了几道有答案的题问了一下，全部接口报错。照样跑每道题都会报错，先按下面的原因处理。"
          : "开跑前抽了几道有答案的题问了一下，下面这些回答大多是「找不到」或接口报错。照样跑会得到一个虚低的分数。"}
      </Typography.Paragraph>
      <Space direction="vertical" style={{ width: "100%" }}>
        {suspects?.results.map((r) => (
          <Alert key={r.name} type="warning" showIcon message={r.name} description={<>
            <div>{r.message}</div>
            {!!r.samples?.length && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {r.samples.map((x) => <li key={x.question}><b>{x.question}</b>　→　{x.answer || "（空）"}<Typography.Text type="secondary">　标准答案：{x.gold}</Typography.Text></li>)}
              </ul>
            )}
          </>} />
        ))}
      </Space>
    </Modal>
  );

  return { launch, busy: busy || checking, checking, modal };
}
