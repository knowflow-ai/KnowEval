import { Button, Space, Steps, Tooltip } from "antd";
import { type ReactNode, useEffect, useState } from "react";

import { go } from "../UiApp";

export interface WizardStep {
  key: string;
  title: string;
  /** 步骤条上这一步下面的小字：已经选了什么，回头看一眼就知道 */
  description?: string;
  content: ReactNode;
  /** 这一步填完了才能往下走 */
  ready: boolean;
  /** 没填完时「下一步」按钮上的提示 */
  hint?: string;
}

export interface WizardFinish {
  label: string;
  disabled: boolean;
  loading: boolean;
  onClick: () => void;
}

/**
 * 新建评测的分步向导：顶部步骤条，一次只看一步，底部固定「上一步 / 下一步」，最后一步是开始按钮。
 * 前面的步骤没填完，后面的步骤点不进去。
 */
export default function EvalWizard({ steps, finish }: { steps: WizardStep[]; finish: WizardFinish }) {
  const [current, setCurrent] = useState(0);
  const last = steps.length - 1;
  useEffect(() => {
    if (current > last) setCurrent(last);
  }, [current, last]);
  const reachable = (i: number) => steps.slice(0, i).every((s) => s.ready);
  const step = steps[Math.min(current, last)];

  return (
    <>
      <Steps size="small" current={current} style={{ marginBottom: 20 }}
        onChange={(i) => reachable(i) && setCurrent(i)}
        items={steps.map((s, i) => ({ title: s.title, description: s.description, disabled: !reachable(i) }))} />
      {/* 内容区是一块固定高度的面板、内部滚动：切换步骤时整块不忽高忽低，底部按钮也一直在同一个位置。
          步骤内容最外层的卡片去掉边框，直接铺在面板里 */}
      <style>{`.ke-wizard-body > .ant-card { border: none; box-shadow: none; background: transparent; }
        .ke-wizard-body > .ant-card > .ant-card-body { padding: 0; }`}</style>
      <div className="ke-wizard-body" style={{ height: "calc(100vh - 374px)", minHeight: 320, overflowY: "auto", padding: 20,
        background: "#fff", border: "1px solid #f0f0f0", borderRadius: 8 }}>
        {step.content}
      </div>
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid #e8e8e8", display: "flex", justifyContent: "space-between" }}>
        <Button onClick={() => go({ page: "evals" })}>取消</Button>
        <Space>
          {current > 0 && <Button onClick={() => setCurrent(current - 1)}>上一步</Button>}
          {current < last ? (
            <Tooltip title={step.ready ? undefined : step.hint}>
              <Button type="primary" disabled={!step.ready} onClick={() => setCurrent(current + 1)}>下一步</Button>
            </Tooltip>
          ) : (
            <Button type="primary" disabled={finish.disabled} loading={finish.loading} onClick={finish.onClick}>{finish.label}</Button>
          )}
        </Space>
      </div>
    </>
  );
}
