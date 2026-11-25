"""内置子 Agent 角色：每个阶段一个显式角色，提示词与规则随插件分发。

设计约束：
- 角色文件只补充"身份/权限/自检"，不改变任何阶段的 JSON 契约；
- 角色与阶段一一对应，artifact 必须与 ARTIFACTS 完全一致，防止提示词与校验器分叉；
- 文件缺失或字段不匹配时直接报错，绝不静默退回通用提示词。
"""
from __future__ import annotations

import re
from pathlib import Path

from .shared_rubric import GROUP_MATERIAL_RULE

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
        from .workflow_constants import ANSWERS_ALLOWED, EXAMPLE_REQUIRED
        required = EXAMPLE_REQUIRED[stage]
        optional = tuple(key for key in ANSWERS_ALLOWED if key not in required)
        keys = lambda values: "、".join(f"`{key}`" for key in values)
        replacements["answers_fields"] = (
            f"- 产物必填字段：{keys(required)}；可选字段：{keys(optional)}。只写这些字段。"
            "`missing` 记录确认缺失的答案，不得省略；即使没有缺失也返回空对象。")
    elif stage == "knowledge":
        from workbench.taxonomy import WHOLE_UNIT_TYPES
        replacements["knowledge_hierarchy"] = (
            "- [ ] 只要是组题（含 " + "、".join(sorted(WHOLE_UNIT_TYPES))
            + " 等整组题型及其他任意组题），标签一律只打在组题级，不在子题中呈现；每个确认子题仍须覆盖且子题 `knowledge=[]`。"
            + "其中阅读理解的设题角度虽标在组级，但须逐小题分析每道小题的问法后去重汇总到组级。")
    for name, value in replacements.items():
        token = "{{" + name + "}}"
        if role["body"].count(token) != 1:
            raise RoleError(f"{path.name}: 必须且只能声明一次契约占位符 {token}")
        role["body"] = role["body"].replace(token, value)
    if re.search(r"\{\{[a-z_]+\}\}", role["body"]):
        raise RoleError(f"{path.name}: 未知角色契约占位符")
    return role


_NO_AP_FILL_RULE_TYPES = frozenset(("完成句子", "短文填空", "选词填空", "教材语篇填空", "其他"))
_LEXICAL_GRAMMAR_RULE_TYPES = frozenset((
    "单词拼写", "单项填空", "完成句子", "短文填空", "选词填空", "教材语篇填空", "其他",
))


def knowledge_judgment_rules(types=None) -> str:
    """Shared semantic selection policy for production labels and blind assessments.

    When ``types`` is omitted/empty (or contains an unknown type), returns the
    full canonical 6-point policy. When scoped to known partition types, keeps
    only the clauses applicable to those types' allowed dimensions.
    """
    type_set = None
    allowed_roots = None
    has_listening = True
    if types:
        cleaned = {str(t).strip() for t in types if isinstance(t, str) and str(t).strip()}
        if cleaned:
            from workbench import taxonomy
            known = set(taxonomy.types())
            if cleaned <= known:
                type_set = frozenset(cleaned)
                roots = set()
                for ty in type_set:
                    for uk in (None, "group", "standalone"):
                        for prefix in (*taxonomy._allowed_prefixes(ty, unit_kind=uk),
                                       *taxonomy.exact_knowledge_paths(ty, unit_kind=uk)):
                            roots.add(prefix.split("/", 1)[0])
                allowed_roots = frozenset(roots)
                has_listening = any(ty.startswith("听力") for ty in type_set)

    parts = [
        "\n【共用知识点判定口径】\n",
        "1. 先确定题型和层级（整题 group vs 单题 standalone），再选完整目录叶子。题型表 dims（及 by_unit_kind）是允许范围；"
        "组级仅把允许的主题、语篇体裁作为必带维度（其他题型除外），其余维度按实际考点逐项判断。历史参考中的宽泛举例与本段冲突时以本段为准。\n",
    ]
    if type_set is None or bool(type_set & _NO_AP_FILL_RULE_TYPES):
        parts.append(
            "完成句子必须「以答案推考点」：先核对空格/划线处实际标准答案（词形、短语、句式或从句），由答案反推词汇与语法叶子，严禁拿题干非空已有内容打标；不打答案考点词条、不挂主题或语篇体裁。"
            "选词填空按形态区分：整题（group，如短文/多句词框）只要答案考点（目录 knowledge=[]，不打体裁、主题或目录词汇/语法，由已验收答案自动派生并在一审填写 answer_points）；"
            "单题（standalone，如单句选词）删除主题与语篇体裁，只按实际考点打词汇、语法标签（不打答案考点词条）。"
            "短文填空和教材语篇填空按材料打体裁、主题，再按组内实际考点打词汇、语法标签（不把答案词作为答案考点）。\n"
        )
    if type_set is None or bool(type_set & {"听力信息转换", "其他"}):
        parts.append(
            "听力信息转换只保留「主题 + 语篇体裁 + 答案考点」：目录 knowledge 仅填主题与语篇体裁（不挂设题角度/听力，也不从目录选词汇/语法），"
            "答案考点走独立派生与一审 answer_points 字段。\n"
        )
    if type_set is None or bool(type_set & {"听力关键词语选择", "其他"}):
        parts.append(
            "听力关键词语选择是唯一精确路径例外：knowledge 只填原目录节点『语音/基本读音』，该节点虽有子级也直接选，"
            "不选任何子类或其他语音、词汇、语法标签。答案考点走原有独立字段或派生链路，不填入 knowledge。按确认的单元层级提交。\n"
        )
    parts.append(
        "2. 细致核对、拒绝粗糙套话：每个叶子必须先定位题干、空格标准答案、全文主旨或具体设问证据。能用一个更具体的叶子解释作答原因时，不再加描述同一原因的泛项；"
        "有不同证据支持的独立考点须逐项保留，不可遗漏并列核心考点，也不要因某词出现在干扰项、背景里或解析顺带提到就打标。\n"
        "选点前先独立说明正确答案靠哪条规则或哪句原文得出。对拟加的第二个知识点逐个核对：它是否解释了另一个实际作答步骤？"
        "例如选出 There be 句型时，错误选项中含 This 或 That 不构成指示代词考点。\n"
    )
    if type_set is None or bool(type_set & _LEXICAL_GRAMMAR_RULE_TYPES):
        parts.append(
            "3. 单词拼写凭词义提示写出一个词、未要求辨别备选词时，优先用目录的『词汇/拼写与词形』。"
            "题干明确要求变形、构词或辨音，或空前后语法线索限定了词形，才追加对应的具体叶子；"
            "例如一般现在时的第三人称单数主语要求 kisses，可加第三人称单数形式变化。词性本身不证明词义辨析，"
            "答案词恰好带词缀也不证明考派生。单项填空若由明确语法规则决定（如钟点前用 at），优先用具体语法叶子；"
            "只有正确与错误选项需要依靠词义区别才能判断时才标相应词义辨析。\n"
        )
    if allowed_roots is None or bool(allowed_roots & {"主题", "语篇体裁"}):
        item4 = []
        if allowed_roots is None or "主题" in allowed_roots:
            item4.append(
                "主题取完整材料或相关听力对话的主话题，地点、职业和单个名词可为背景线索。只有独立且有实质内容的第二主题才并列。"
                "先通读全文并用一句话概括材料主要在讲什么，再选叶子。多个主题都说得通时，优先选能直接解释材料核心事件、主要目的或中心观点的那一个。"
                "例如全文围绕一个纪念日的由来和纪念含义，可取文化习俗；只有材料主要讲社会状况如何变化时，才取社会发展。"
            )
            if has_listening:
                item4.append(
                    "听力只给出递尺子等单一动作、无法证明上课或校园情境时，不能仅凭物品推断学校生活。"
                )
        if allowed_roots is None or "语篇体裁" in allowed_roots:
            item4.append(
                "语篇体裁必须通读全文判断，严禁只看首段或其中一小段：综合文章开头引入、主体展开方式与结尾落点判定全文主要写作功能——"
                "例如首段用故事/经历引入、后文大段科普介绍或阐释原理的，全文应判说明文而非记叙文；首段引出现象、后文展开观点论证的，应判议论文。"
                "介绍事物特征用介绍类短文，阐释原因、原理或道理用事理阐释；出现某人物不等于人物传记。"
            )
        if item4:
            parts.append("4. " + "".join(item4) + "\n")
    if allowed_roots is None or "设题角度" in allowed_roots:
        item5 = (
            "5. 设题角度按得到答案的方法判断。原文或转写可直接定位答案是细节理解，需结合线索推出未明说的信息才是推理；"
            "例如直接说出地点按细节理解，凭环境线索才能判断发生地才是地点推断。"
            "「阅读理解」组题的设题角度仍标在组题级 knowledge（子题 knowledge=[]），但具体标签必须来源于逐小题的问法分析："
            "先逐一分析每道小题（如五道小题 Q1–Q5）的设问方式与解题路径，确定每小题对应的设题角度/阅读/* 叶子，再去重汇总标在组题级 knowledge，严禁笼统只打泛化角度或漏标个别小题角度。"
            "其余整组题型（任务型阅读、读写综合、听力长对话理解、听力短文理解）同样按各子题独立核对角度后在组级去重汇总，子题 knowledge=[]。"
            "优先选能够准确描述解题任务的具体角度。例如推断文章来源有专用的文章出处标签且题型允许时，用文章出处；"
            "不要再为同一道小题追加泛化的推理判断。组内其他题确实考一般推理时，组级仍保留推理判断。"
        )
        if has_listening:
            item5 += "听力可用材料不足时不得用选项场景或转写占位冒充完整对话。"
        parts.append(item5 + "\n")
    parts.append(
        "6. 事实依据使用当前题目的完整正文、共享材料、选项和能唯一关联的原答案、原解析或听力文字。"
        "逐题认真审读后再落标，禁止仅凭首句/题干表层词草率打标；证据缺失时明确指出判断局限，避免用题型提示强凑可选标签或自造目录项。\n"
    )
    return "".join(parts)


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
                + "\n权限边界：仅返回本轮 review_round.checks，不得输出 `checked_units` 或 `unchecked`；不改产物、不放行、不自动上传/发布。"
                + "\n审核差异是产出，不是执行故障：独立填写 assessment，不得要求上游知识点重判、不得改正式标签或迎合旧判断。"
                + "补修 JSON/键集/覆盖/引证/绑定失败时不得改动语义判断来减少差异。\n")
    return ("\n\n[子 Agent] " + role["title"] + "\n允许写入的产物：" + role["artifact"] + "\n" + role["body"]
            + ("\n\n" + GROUP_MATERIAL_RULE if stage in {"preassemble", "types", "assemble"} else "")
            + (knowledge_judgment_rules() if stage == "knowledge" else ""))


def header(stage: str) -> str:
    """提示词头部的机器可读角色标记，供路由脚本按角色选模型。"""
    return f"Agent: {load(stage)['id']}\n"


def catalog() -> list[dict]:
    """按阶段顺序列出全部内置角色，供设置界面/文档展示。"""
    return [load(path.stem) for path in sorted(ROLES_DIR.glob("*.md"))]
