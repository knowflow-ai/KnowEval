import { Alert, App, Button, Card, Empty, List, Popconfirm, Skeleton, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { fmtTime, pct } from "../../data";
import { type EvalRow, api } from "../api";
import { STATUS_COLOR, STATUS_TEXT } from "../logic";
import { go } from "../UiApp";
import PageHead from "./PageHead";

function headlineText(row: EvalRow): string {
  const vals = row.participants.map((p) => row.headline[p.id]).filter((v): v is number => v !== null && v !== undefined);
  if (!vals.length) return "";
  return vals.map((v) => pct(v)).join(" vs ");
}

/** 删除评测的确认说明：结果连同题目快照一起删，测试集和知识库不动 */
export const DELETE_EVAL_NOTE = "评测结果和这次的题目快照一起删掉，不能恢复。用到的测试集、评测台建的知识库都不受影响。";

export default function EvalListPage() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<EvalRow[] | null>(null);
  const [error, setError] = useState("");

  const remove = async (id: string) => {
    try {
      await api.deleteEval(id);
      message.success("已删除");
      setRows((prev) => prev?.filter((r) => r.id !== id) ?? null);
    } catch (e: any) {
      message.error(e.message ?? String(e));
    }
  };

  useEffect(() => {
    let alive = true;
    const load = () => api.state().then((s) => alive && setRows(s.evals)).catch((e) => alive && setError(String(e.message ?? e)));
    load();
    const timer = window.setInterval(load, 5000);  // 有评测在跑时列表上的状态要跟着变
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  return (
    <>
      <PageHead title="评测" desc="一次评测 = 一套题 + 几个参评项（已有助手、建库配置或自研系统的接口）。跑完直接出对比：谁答得准、谁在自信地答错、差距是不是噪声。"
        extra={<Button type="primary" onClick={() => go({ page: "new" })}>新建评测</Button>} />
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Card styles={{ body: { padding: rows?.length ? 0 : 24 } }}>
        {rows === null ? <Skeleton active /> : rows.length === 0 ? (
          <Empty description="还没有评测">
            <Space>
              <Button onClick={() => go({ page: "systems" })}>先接入系统</Button>
              <Button type="primary" onClick={() => go({ page: "new" })}>新建评测</Button>
            </Space>
          </Empty>
        ) : (
          <List dataSource={rows} renderItem={(row) => (
            <List.Item style={{ padding: "16px 24px", cursor: "pointer" }} onClick={() => go({ page: "eval", id: row.id })}
              extra={<div style={{ textAlign: "right" }}>
                <Typography.Text strong>{headlineText(row) || "—"}</Typography.Text>
                <Space size={12} onClick={(e) => e.stopPropagation()}>
                  {row.status !== "running" && (
                    <Popconfirm title={`删除评测「${row.name}」？`} description={DELETE_EVAL_NOTE} okText="删除" cancelText="取消"
                      okButtonProps={{ danger: true }} onConfirm={() => remove(row.id)} overlayStyle={{ maxWidth: 360 }}>
                      <Typography.Link type="danger">删除</Typography.Link>
                    </Popconfirm>
                  )}
                  <Typography.Link onClick={() => go({ page: "eval", id: row.id })}>{row.status === "running" ? "查看进度 →" : "查看结果 →"}</Typography.Link>
                </Space>
              </div>}>
              <List.Item.Meta
                title={<Space wrap><span>{row.name}</span><Tag color={STATUS_COLOR[row.status]}>{STATUS_TEXT[row.status]}</Tag></Space>}
                description={`${row.dataset_ref} · ${row.participants.length} 个参评项 · 各跑 ${row.repeats} 遍 · ${fmtTime(row.created_at)}`} />
            </List.Item>
          )} />
        )}
      </Card>
    </>
  );
}
