# -*- coding: utf-8 -*-
"""自动化任务工作流：阶段契约。

每个阶段声明的依赖、输入、产物和执行类型，是调度、放行与失效重跑的唯一依据。
修改阶段顺序或产物路径时只改这里，不要在各处硬编码。
"""
from __future__ import annotations

from dataclasses import dataclass

AGENT = "agent"      # 由代理/人完成，工作流只记录状态与产物
COMMAND = "command"  # 由确定性脚本完成，工作流负责执行
GATE = "gate"        # 只读检查，产出报告，不修改题目
HUMAN = "human"      # 必须人工决定，工作流不得自动放行


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    kind: str
    depends_on: tuple = ()
    requires: tuple = ()
    produces: tuple = ()
    command: tuple = ()
    docs: tuple = ()
    optional: bool = False
    description: str = ""


# inputs 键名：source / answers / audio / docx
STAGES: tuple[Stage, ...] = (
    Stage(
        id="extract",
        title="原卷复合提取",
        kind=COMMAND,
        requires=("source",),
        produces=("01_source/正文.txt", "01_source/structure.json", "01_source/extraction.json",
                  "01_source/metadata.json", "07_review/extraction-report.json"),
        command=("workflow/extract.py", "--batch-dir", "{batch_dir}"),
        docs=("docs/extraction.md", "docs/structure.md"),
        description="先从原卷卷头识别来源、年份、地区、来源类型；Word 原生提取，PDF 逐页选择文字层或 OCR；保留表格、图片、边界和位置证据。",
    ),
    Stage(
        id="structure_gate",
        title="结构自动确认",
        kind=GATE,
        depends_on=("extract",),
        produces=("01_source/structure.confirmed.json", "07_review/structure-gate.json"),
        docs=("references/structure.md", "docs/workflow-regressions.md"),
        description="只保留提取文本、题号线索、材料和媒体证据；题型、题目分片、题组边界与分层交由模型结合上下文判断。缺失或不确定项记录警告，不阻断后续阶段。",
    ),
    Stage(
        id="answers",
        title="答案提取",
        kind=AGENT,
        depends_on=("structure_gate",),
        produces=("01_source_ans/answers.json",),
        docs=("references/stages/01-extract.md",),
        description="子智能体4：有答案就提取答案并保留来源；没有答案则输出空映射和说明，不猜测、不阻断后续流程。",
    ),
    Stage(
        id="audio",
        title="听力切段",
        kind=AGENT,
        depends_on=("structure_gate",),
        requires=("audio",),
        produces=("02_audio/audio_map.json",),
        docs=("references/stages/02-audio.md", "references/audio-splitting.md"),
        optional=True,
        description="按确认后的真实听力单元切段，例如6–7与8–10分开；核对每段起止及回听证据，无音频才跳过。",
    ),
    Stage(
        id="types",
        title="题型与子类",
        kind=AGENT,
        depends_on=("structure_gate",),
        produces=("03_types/types.json",),
        docs=("references/stages/03-type-map.md", "references/type-subtypes.md"),
        description="子智能体3：判定主类与子类（只能取当前目录值）、统计题目词数并判断题目内容是否完整；冲突转人工。",
    ),
    Stage(
        id="explanations",
        title="打标签：解析与文章大意",
        kind=AGENT,
        depends_on=("types",),
        produces=("04_knowledge/explanations.json",),
        docs=("docs/content-labeling.md",),
        description="子智能体1：有原解析/文章大意就逐字提取；没有则填「无」，不生成、不阻断流程。",
    ),
    Stage(
        id="knowledge",
        title="打标签：知识点与教材匹配",
        kind=AGENT,
        depends_on=("types",),
        produces=("04_knowledge/knowledge.json",),
        docs=("docs/knowledge-tagging.md",),
        description="子智能体2：知识点打标与教材知识点匹配；解析与文章大意沿用子智能体1的产物，只做逐字搬运，不生成、不改写。（模型只打知识点，可与解析智能体并行；解析在导入时注入）",
    ),
    Stage(
        id="assemble",
        title="批次组装",
        kind=AGENT,
        # 组装提示词与导入都会读取 explanations（解析/大意），必须显式依赖，
        # 否则重跑解析时组装不会失效，batch.json 会留着旧解析。
        depends_on=("audio", "types", "knowledge", "explanations"),
        produces=("06_batch/batch.json",),
        docs=("references/stages/06-upload.md",),
        description="主流程唯一写入口，合并全部上游产物为正式批次。",
    ),
    Stage(
        id="meta",
        title="难度与分类",
        kind=COMMAND,
        depends_on=("assemble",),
        produces=("06_batch/batch_meta.json",),
        command=("scripts/05-meta/assign_meta.py", "--batch", "{batch_json}",
                 "--out", "{batch_meta}", "--report"),
        docs=("references/stages/05-meta.md",),
        description="按可解释特征定难度与分类；特征不足留空待人工。",
    ),
    Stage(
        id="underlines",
        title="画线标记",
        kind=COMMAND,
        depends_on=("meta",),
        requires=("docx",),
        produces=("06_batch/batch_meta.json",),
        command=("scripts/06-upload/mark_underline.py", "--batch", "{batch_meta}",
                 "--docx", "{docx}", "--apply"),
        docs=("references/constraints.md",),
        optional=True,
        description="从 docx 字符格式补 <u>…</u>；无 docx 批次跳过。",
    ),
    Stage(
        id="validate",
        title="本地自动体检",
        kind=GATE,
        depends_on=("meta",),
        produces=("07_review/validation-report.json",),
        docs=("references/stages/06-upload.md", "references/constraints.md"),
        description="全部只读检查；不触碰 SSH、媒体部署或上传接口。",
    ),
    Stage(
        id="review",
        title="两轮审核（一审全量+二审抽20%）",
        kind=AGENT,
        depends_on=("validate",),
        produces=("07_review/review-rounds.json",
                  "07_review/content-review.json", "07_review/metadata-review.json",
                  "07_review/manual-review-queue.json", "07_review/unchecked.json"),
        docs=("references/review-workflow.md", "codex-skills/mudu-question-review/SKILL.md"),
        description="分别执行标签重判与抽样复核（整组题型按整组抽检、只做组级核对）；绑定真实分轮证据，只有具体冲突或不确定项进人工队列。",
    ),
    Stage(
        id="review_gate",
        title="审核放行判断",
        kind=GATE,
        depends_on=("review",),
        produces=("07_review/review-gate.json",),
        docs=("references/review-workflow.md",),
        description="阻断项为零、人工队列为空、必检项无 unchecked 才放行。",
    ),
    Stage(
        id="upload_gate",
        title="上传人工确认",
        kind=HUMAN,
        depends_on=("review_gate",),
        docs=("references/stages/06-upload.md", "references/constraints.md"),
        description="人工确认批次、来源、目标、查重和预检；批准绑定最终本地文件与冻结 API 请求，未确认不得提交。",
    ),
    Stage(
        id="upload",
        title="写入上传暂存",
        kind=COMMAND,
        depends_on=("upload_gate",),
        produces=("07_upload/receipt.json",),
        command=("{python}", "-m", "workflow.runner", "upload", "--batch-dir", "{batch_dir}", "--yes"),
        docs=("docs/upload-api.md",),
        description="提交已批准的冻结请求到上传暂存；远端编辑人工确认后才生成草稿，发布仍须人工完成。",
    ),
    Stage(
        id="publish_gate",
        title="发布人工确认",
        kind=HUMAN,
        depends_on=("upload",),
        docs=("references/constraints.md",),
        description="人工逐题确认并发布；工作流永不自动执行。",
    ),
)

STAGE_BY_ID = {s.id: s for s in STAGES}
STAGE_ORDER = tuple(s.id for s in STAGES)

# 阶段产物变化时，需要失效的直接下游（由 depends_on 自动推导，这里只声明跨阶段影响）
EXTRA_INVALIDATION = {
    "extract": ("structure_gate", "types", "knowledge", "assemble", "meta", "validate", "review"),
    "structure_gate": ("types", "knowledge", "assemble", "meta", "validate", "review"),
    "types": ("knowledge", "assemble", "meta", "validate", "review"),
    "knowledge": ("assemble", "meta", "validate", "review"),
    "assemble": ("meta", "underlines", "validate", "review"),
    "meta": ("underlines", "validate", "review"),
}


def downstream_of(stage_id: str) -> tuple[str, ...]:
    """返回该阶段全部下游（含间接下游），按阶段顺序。"""
    seen: set[str] = set()
    frontier = [stage_id]
    while frontier:
        current = frontier.pop()
        for stage in STAGES:
            if current in stage.depends_on and stage.id not in seen:
                seen.add(stage.id)
                frontier.append(stage.id)
    for extra in EXTRA_INVALIDATION.get(stage_id, ()):
        seen.add(extra)
    return tuple(s for s in STAGE_ORDER if s in seen)


def upstream_of(stage_id: str) -> tuple[str, ...]:
    """返回全部上游（含间接），按阶段顺序。"""
    seen: set[str] = set()
    frontier = [stage_id]
    while frontier:
        current = frontier.pop()
        stage = STAGE_BY_ID.get(current)
        if not stage:
            continue
        for dep in stage.depends_on:
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return tuple(s for s in STAGE_ORDER if s in seen)
