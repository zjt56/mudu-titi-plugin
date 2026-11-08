"""内置子 Agent 角色：每个阶段一个显式角色，提示词与规则随插件分发。

设计约束：
- 角色文件只补充"身份/权限/自检"，不改变任何阶段的 JSON 契约；
- 角色与阶段一一对应，artifact 必须与 ARTIFACTS 完全一致，防止提示词与校验器分叉；
- 文件缺失或字段不匹配时直接报错，绝不静默退回通用提示词。
"""
from __future__ import annotations

import re
from pathlib import Path

ROLES_DIR = Path(__file__).resolve().parent.parent / "prompts" / "roles"
REQUIRED = ("stage", "artifact")
_HEAD = re.compile(r"^#\s*角色[:：]\s*(?P<title>.+?)\s*$", re.M)
_FIELD = re.compile(r"^(?P<key>[a-z_]+):\s*(?P<value>.+?)\s*$", re.M)


class RoleError(ValueError):
    """角色定义缺失或与阶段契约不一致。"""


def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    head = _HEAD.search(text)
    if not head:
        raise RoleError(f"{path.name}: 缺少 '# 角色：<名称>' 标题")
    fields = {match.group("key"): match.group("value") for match in _FIELD.finditer(text)}
    missing = [key for key in REQUIRED if not fields.get(key)]
    if missing:
        raise RoleError(f"{path.name}: 缺少字段 {', '.join(missing)}")
    body = text.split("\n\n", 1)[1].strip() if "\n\n" in text else ""
    return {"id": path.stem, "title": head.group("title").strip(),
            "stage": fields["stage"].strip(), "artifact": fields["artifact"].strip(),
            "body": body}


def load(stage: str, artifact: str | None = None) -> dict:
    """读取某个阶段的角色；artifact 给定时必须完全一致。"""
    path = ROLES_DIR / f"{stage}.md"
    if not path.is_file():
        raise RoleError(f"缺少角色定义：{path.name}")
    role = _parse(path)
    if role["stage"] != stage:
        raise RoleError(f"{path.name}: stage 声明为 {role['stage']}，与文件名的阶段 {stage} 不一致")
    if artifact is not None and role["artifact"] != artifact:
        raise RoleError(f"{path.name}: artifact 声明为 {role['artifact']}，与阶段产物 {artifact} 不一致")
    return role


def section(stage: str, artifact: str | None = None, round_id: str | None = None) -> str:
    """拼进任务提示词的角色段落；审核阶段额外标注本轮具体职责。"""
    role = load(stage, artifact)
    text = ("\n\n[子 Agent] " + role["title"] + "\n允许写入的产物：" + role["artifact"] + "\n" + role["body"])
    if stage == "review" and round_id:
        duty = {"labels": "本轮为一审：全量逐单元语义复核，不得抽样代替。",
                "sample": "本轮为二审：按抽样单元整组核对，结论只覆盖被抽到的小题。"}
        text += "\n本轮职责：" + duty.get(round_id, "本轮职责以 ROUND 行为准。")
    return text


def header(stage: str) -> str:
    """提示词头部的机器可读角色标记，供路由脚本按角色选模型。"""
    return f"Agent: {load(stage)['id']}\n"


def catalog() -> list[dict]:
    """按阶段顺序列出全部内置角色，供设置界面/文档展示。"""
    return [load(path.stem) for path in sorted(ROLES_DIR.glob("*.md"))]
