// 取值路径的前端实现：与后端 knoweval/adapters/http.py 的 jsonpath 子集一致（$.a.b、$.a[0]、$.a[*]、$.a[*].b）。
// 接入表单用它把「点选的字段」变成路径，并预览路径当前取到什么。jsonpath.test.ts 覆盖。

export type Seg = string | number;

const KEY = /^[A-Za-z_][\w-]*$/;

/** 点选的字段 → 路径。键名不合后端的写法（比如中文、带点）时返回 null，页面提示改不了 */
export function pathOf(segs: Seg[]): string | null {
  let out = "$";
  for (const s of segs) {
    if (typeof s === "number") out += `[${s}]`;
    else if (s === "*") out += "[*]";
    else if (KEY.test(s)) out += `.${s}`;
    else return null;
  }
  return out;
}

/** 列表类目标（引用列表、检索片段列表）：点到列表里的任何位置，都取到那个列表本身，记成 [*] */
export function listPathOf(segs: Seg[], data: unknown): string | null {
  let node: unknown = data;
  for (let i = 0; i < segs.length; i++) {
    if (Array.isArray(node)) return pathOf([...segs.slice(0, i), "*"]);
    node = (node as any)?.[segs[i] as any];
  }
  return Array.isArray(node) ? pathOf([...segs, "*"]) : null;
}

function parse(path: string): Seg[] | null {
  if (!path.startsWith("$")) return null;
  const segs: Seg[] = [];
  const re = /\.([A-Za-z_][\w-]*)|\[(\d+|\*)\]/g;
  let m: RegExpExecArray | null;
  let consumed = 1;
  while ((m = re.exec(path.slice(1))) !== null) {
    if (m.index + 1 !== consumed) return null;
    consumed += m[0].length;
    segs.push(m[1] !== undefined ? m[1] : m[2] === "*" ? "*" : Number(m[2]));
  }
  return consumed === path.length ? segs : null;
}

/** 按路径取值：取不到返回 undefined；路径里有 [*] 时返回列表 */
export function evalPath(data: unknown, path: string): unknown {
  const segs = parse(path.trim());
  if (!segs) return undefined;
  let nodes: unknown[] = [data];
  let fanned = false;
  for (const s of segs) {
    const next: unknown[] = [];
    for (const n of nodes) {
      if (s === "*") {
        fanned = true;
        if (Array.isArray(n)) next.push(...n);
      } else if (typeof s === "number") {
        if (Array.isArray(n) && s < n.length) next.push(n[s]);
      } else if (n && typeof n === "object" && !Array.isArray(n) && s in (n as object)) {
        next.push((n as any)[s]);
      }
    }
    nodes = next;
  }
  if (fanned) return nodes;
  return nodes.length ? nodes[0] : undefined;
}

/** 预览用的短文本 */
export function preview(v: unknown, limit = 80): string {
  if (v === undefined) return "（取不到）";
  const text = typeof v === "string" ? v : JSON.stringify(v);
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}
