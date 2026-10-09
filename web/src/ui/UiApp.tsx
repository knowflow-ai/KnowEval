import { Layout, Menu, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { setApiBase } from "./api";
import { type Route, parseRoute, routeHash } from "./logic";
import BuildDetailPage from "./pages/BuildDetailPage";
import DatasetDetailPage from "./pages/DatasetDetailPage";
import DatasetsPage from "./pages/DatasetsPage";
import EvalDetailPage from "./pages/EvalDetailPage";
import EvalListPage from "./pages/EvalListPage";
import NewEvalPage from "./pages/NewEvalPage";
import SettingsPage from "./pages/SettingsPage";
import SystemsPage from "./pages/SystemsPage";

export function go(r: Route): void {
  window.location.hash = routeHash(r);
}

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

const NAV: { key: string; label: string; route: Route }[] = [
  { key: "evals", label: "评测", route: { page: "evals" } },
  { key: "systems", label: "系统", route: { page: "systems" } },
  { key: "datasets", label: "测试集", route: { page: "datasets" } },
  { key: "settings", label: "设置", route: { page: "settings" } },
];

function navKey(r: Route): string {
  if (r.page === "new" || r.page === "eval") return "evals";
  return r.page === "dataset" || r.page === "build" ? "datasets" : r.page;
}

export default function UiApp({ api }: { api: string }) {
  setApiBase(api);
  const route = useRoute();
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", padding: "0 24px", display: "flex", alignItems: "center", gap: 24 }}>
        <a href="#/" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Typography.Text strong style={{ fontSize: 16 }}>KnowEval</Typography.Text>
          <Tag color="blue" style={{ marginInlineEnd: 0 }}>评测台</Tag>
        </a>
        <Menu mode="horizontal" selectedKeys={[navKey(route)]} style={{ flex: 1, borderBottom: "none", minWidth: 0 }}
          items={NAV.map((n) => ({ key: n.key, label: n.label, onClick: () => go(n.route) }))} />
      </Layout.Header>
      <Layout.Content style={{ padding: "24px 24px 64px", maxWidth: 1200, width: "100%", margin: "0 auto", boxSizing: "border-box" }}>
        {route.page === "evals" && <EvalListPage />}
        {route.page === "new" && <NewEvalPage key={route.testset ?? ""} preselect={route.testset} />}
        {route.page === "eval" && <EvalDetailPage id={route.id} />}
        {route.page === "systems" && <SystemsPage />}
        {route.page === "datasets" && <DatasetsPage />}
        {route.page === "dataset" && <DatasetDetailPage path={route.path} />}
        {route.page === "build" && <BuildDetailPage id={route.id} />}
        {route.page === "settings" && <SettingsPage />}
      </Layout.Content>
    </Layout>
  );
}
