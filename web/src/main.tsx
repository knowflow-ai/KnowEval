import { App as AntApp, ConfigProvider, Empty } from "antd";
import zhCN from "antd/locale/zh_CN";
import React from "react";
import { createRoot } from "react-dom/client";

import CompareView from "./components/CompareView";
import ReportView from "./components/ReportView";
import UiApp from "./ui/UiApp";
import type { KnowEvalData } from "./types";

function readData(): KnowEvalData | null {
  const text = document.getElementById("knoweval-data")?.textContent ?? "";
  // 不写出完整占位符：Python 端按数据标签精确替换，但代码里少一处同样的字符串更稳妥
  if (!text || text.trim().startsWith("__KNOWEVAL_")) return null;
  return JSON.parse(text) as KnowEvalData;
}

function App() {
  const data = readData();
  if (!data) return <Empty style={{ marginTop: 120 }} description="这个文件里没有评测数据，请用 knoweval report --html 生成" />;
  if (data.kind === "ui") return <AntApp><UiApp api={data.api} /></AntApp>;
  return data.kind === "compare" ? <CompareView data={data} /> : <ReportView data={data} />;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#1677ff" } }}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
