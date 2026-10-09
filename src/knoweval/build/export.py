"""导出数据集：人工通过（approved / edited）的候选题 → questions.jsonl + manifest.json + 用到的文档。"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from knoweval.build.project import Project
from knoweval.schema import Question



# 候选题的来历 → 正式题目的来源（评测台导入的是 "import"，手动加的是 "human"，其余是模型出的）
_ORIGIN = {"import": "imported", "human": "manual"}

def export_dataset(project: Project, out: Path, all_docs: bool = False,
                   statuses: tuple[str, ...] = ("approved", "edited")) -> Path:
    """all_docs：项目里的文档全部带上（导入的题目往往没有引文，只按引文挑会把文档丢光）。
    statuses：哪些状态的候选题进数据集；评测台不要求人工复核，通过自动校验（verified）的也算。"""
    out = Path(out)
    accepted = [c for c in project.candidates if c.status in statuses]
    if not accepted:
        raise ValueError("没有人工通过的候选题，先在复核界面通过或修改")
    questions = [Question.model_validate({**c.model_dump(include=set(Question.model_fields)), "domain": project.meta["name"],
                                          "origin": _ORIGIN.get(c.generator, "generated")})
                 for c in accepted]
    used = sorted({d.name for d in project.docs} if all_docs else
                  {ev.doc for q in questions for ev in q.evidence} | {c.checks.get("anchor") for c in accepted if c.checks.get("anchor")})
    (out / "docs").mkdir(parents=True, exist_ok=True)
    files = []
    for name in used:
        shutil.copyfile(project.root / "docs" / name, out / "docs" / name)
        entry = next(d for d in project.docs if d.name == name)
        files.append({"name": name, "sha256": hashlib.sha256((out / "docs" / name).read_bytes()).hexdigest(), "format": entry.format})
    (out / "questions.jsonl").write_text("".join(q.model_dump_json() + "\n" for q in questions), encoding="utf-8")
    manifest = {"name": project.meta["name"], "version": project.meta["version"], "license": project.meta["license"],
                "canary": f"KNOWEVAL-CANARY-{uuid.uuid4().hex[:8]}-{project.meta['name']}-{project.meta['version']}", "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
