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
    # 角色只声明插入位置；字段/层级从消费者的现有契约生成，不在 Markdown 复制键集或题型清单。
    replacements = {}
    if stage == "answers":
        from .agent_tasks import ANSWERS_ALLOWED, EXAMPLE_REQUIRED
        required = EXAMPLE_REQUIRED[stage]
        optional = tuple(key for key in ANSWERS_ALLOWED if key not in required)
        keys = lambda values: "、".join(f"`{key}`" for key in values)
        replacements["answers_fields"] = (
            f"- 产物必填字段：{keys(required)}；可选字段：{keys(optional)}。只写这些字段。"
            "`missing` 记录确认缺失的答案，不得省略；即使没有缺失也返回空对象。")
    elif stage == "knowledge":
        from workbench.taxonomy import WHOLE_UNIT_TYPES
        replacements["knowledge_hierarchy"] = (
            "- [ ] 整组题型（" + "、".join(sorted(WHOLE_UNIT_TYPES))
            + "）只在组级打标；每个确认子题仍须覆盖，子题 `knowledge=[]`，不逐题打设题角度或其他标签。")
    for name, value in replacements.items():
        token = "{{" + name + "}}"
        if role["body"].count(token) != 1:
            raise RoleError(f"{path.name}: 必须且只能声明一次契约占位符 {token}")
        role["body"] = role["body"].replace(token, value)
    if re.search(r"\{\{[a-z_]+\}\}", role["body"]):
        raise RoleError(f"{path.name}: 未知角色契约占位符")
    return role


def section(stage: str, artifact: str | None = None, round_id: str | None = None) -> str:
    """拼进任务提示词的角色段落；审核阶段额外标注本轮具体职责。"""
    role = load(stage, artifact)
    if stage == "review" and round_id:
        # Generic review writes agent-review.json (findings/checked_units/unchecked).
        # Semantic rounds have a different artifact and envelope; never append
        # that incompatible output contract to a round prompt.
        duty = {"labels": "本轮为一审：全量逐单元语义复核，不得抽样代替。",
                "sample": "本轮为二审：只核对计划 TARGET；整组题型按整组核对，独立单题按小题，不扩写到未抽中目标。"}
        return ("\n\n[子 Agent] " + role["title"]
                + "\n本轮职责：" + duty.get(round_id, "本轮职责以 ROUND 行为准。")
                + "\n权限边界：仅返回本轮 review_round.checks 判断；不改产物、不放行、不伪造审批。"
                + "\n脑子检查：每个目标均有真实证据和独立 assessment；根对象包含 status、artifacts、notes、findings 四个同级键，findings=[]。"
                + "不得输出 `checked_units` 或 `unchecked`。"
                + "\n审核差异是产出，不是执行故障：独立填写 assessment，由程序比较原标签并保留队列及 rounds.errors；"
                + "不得要求上游知识点重判，不得改正式标签、自动 resolve/dismiss、迎合旧判断或反复重审直到差异消失。"
                + "盲审隐藏标签不等于正式标签缺失，不能猜原标签。"
                + "知识点与基本差异（题型/子类/答案/解析/原文）照实记录；难度/来源/分类/选题/词数不进审核差异。"
                + "图片/音频能力限制只作 informational，但不能据此过滤已经比较出的真实标签差异。"
                + "\n出问题怎么办：只对 JSON/键集/覆盖/引证/绑定失败修复本次响应或补齐未执行目标，"
                + "不改语义判断来减少差异，不重跑已合格目标；当前审核未完成不得靠旧完成标记背书。"
                + "差异不阻止本地格式检查或用户手动上传，也不授权自动上传/发布；质量仍未放行。\n")
    return ("\n\n[子 Agent] " + role["title"] + "\n允许写入的产物：" + role["artifact"] + "\n" + role["body"])


def header(stage: str) -> str:
    """提示词头部的机器可读角色标记，供路由脚本按角色选模型。"""
    return f"Agent: {load(stage)['id']}\n"


def catalog() -> list[dict]:
    """按阶段顺序列出全部内置角色，供设置界面/文档展示。"""
    return [load(path.stem) for path in sorted(ROLES_DIR.glob("*.md"))]
