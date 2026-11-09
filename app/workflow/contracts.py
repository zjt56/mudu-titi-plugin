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
        id="preassemble",
        title="前置批次组装（含题型）",
        kind=AGENT,
        depends_on=("structure_gate",),
        produces=("06_batch/batch.base.json", "03_types/types.json"),
        docs=("docs/pipeline.md", "references/stages/03-type-map.md", "references/type-subtypes.md"),
        description="切题后按来源建立同一份批次，并在本阶段结合题干、选项、共享材料判定目录题型、子类、词数与完整性；来源对象和题型均验收后才交付答案、听力和知识点。后续只填字段，不重新组题。",
    ),
    Stage(
        id="answers",
        title="答案填充",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble"),
        produces=("01_source_ans/answers.json",),
        docs=("references/stages/01-extract.md",),
        optional=True,
        description="逐题号提取原答案；没有答案来源时生成覆盖全部确认题号、带明确来源证据的‘无答案’决定，不猜、不漏项。",
    ),
    Stage(
        id="types",
        title="组装题型交接校验（兼容）",
        kind=COMMAND,
        depends_on=("structure_gate", "preassemble"),
        produces=("03_types/types.json",),
        command=("{python}", "-m", "workflow.preassembly", "check", "--batch-dir", "{batch_dir}"),
        docs=("references/stages/03-type-map.md", "references/type-subtypes.md"),
        description="只校验前置组装已产出的题型，不调用模型、不重复判型；保留 types 标识兼容历史任务和局部题型修复。不完整结果不能交付依赖阶段。",
    ),
    Stage(
        id="audio",
        title="听力切段与音频填充",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble", "types"),
        requires=("audio",),
        produces=("02_audio/audio_map.json",),
        docs=("references/stages/02-audio.md", "references/audio-splitting.md"),
        optional=True,
        description="只消费已交付题型与确认结构，按真实听力单元切段并绑定版本；无音频才跳过。",
    ),
    Stage(
        id="explanations",
        title="打标签：解析与文章大意",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble", "types", "answers"),
        produces=("04_knowledge/explanations.json",),
        docs=("docs/content-labeling.md",),
        optional=True,
        description="有原解析/大意时按Word权威来源逐字匹配并记录绑定；确认不存在时对全部目标填‘无’，不生成。",
    ),
    Stage(
        id="knowledge",
        title="打标签：知识点与教材匹配",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble", "types"),
        produces=("04_knowledge/knowledge.json",),
        docs=("docs/knowledge-tagging.md",),
        description="知识点只交目录标签与教材匹配信息；阶段验证独立于答案/解析，下游仅在汇总时合并。",
    ),
    Stage(
        id="assemble",
        title="填充汇总与固化",
        kind=AGENT,
        # 组装提示词与导入都会读取 explanations（解析/大意），必须显式依赖，
        # 否则重跑解析时组装不会失效，batch.json 会留着旧解析。
        #
        # 听力（audio）刻意不在这里依赖：听力切段/转写耗时最长，而组装本身不读
        # 音频内容（音频只由 audio 阶段写入 attachments，缺映射时按“待部署媒体”记录）。
        # 因此听力与解析/知识点/组装可以并行推进；只有内容审核（review）必须等听力
        # 完成，见 review 阶段对 audio 的显式依赖。
        # 前置对象可能在标签完成后被重新校验并判冲突，不能只依赖标签的旧完成态。
        depends_on=("preassemble", "answers", "types", "knowledge", "explanations"),
        produces=("06_batch/batch.json",),
        docs=("references/stages/06-upload.md",),
        description="固化前置批次已填字段并执行原有保真与格式校验；沿用既有题目身份、题干、选项和分组，不重新组题。旧批次仍支持原有确定性合并路径。通关才登记正式填充完成。不等待听力，听力完成后由审核阶段另行消费。",
    ),
    Stage(
        id="meta",
        title="难度与分类",
        kind=COMMAND,
        depends_on=("assemble",),
        produces=("06_batch/batch_meta.base.json",),
        command=("scripts/05-meta/assign_meta.py", "--batch", "{batch_json}",
                 "--out", "{batch_meta_base}", "--report"),
        docs=("references/stages/05-meta.md",),
        description="组装阶段已接收题型、知识点与难度结果；此步骤仅做确定性元数据归一与缺省补全。",
    ),
    Stage(
        id="underlines",
        title="画线标记",
        kind=COMMAND,
        depends_on=("meta",),
        produces=("06_batch/batch_meta.json",),
        command=("scripts/06-upload/mark_underline.py", "--batch", "{batch_meta_base}",
                 "--out", "{batch_meta}", "--docx", "{docx}", "--apply"),
        docs=("references/constraints.md",),
        optional=True,
        description="从 docx 字符格式补 <u>…</u>；无 docx 批次跳过。",
    ),
    Stage(
        id="validate",
        title="本地自动体检",
        kind=GATE,
        depends_on=("underlines",),
        produces=("07_review/validation-report.json",),
        docs=("references/stages/06-upload.md", "references/constraints.md"),
        description="全部只读检查；不触碰 SSH、媒体部署或上传接口。",
    ),
    Stage(
        id="review",
        title="两轮审核（一审全量+二审抽20%）",
        kind=AGENT,
        # 内容审核必须等听力完成：审核要读 02_audio/full-transcript.json 核对听力语义，
        # 听力未完成时审核不完整（这是唯一需要等待听力完成的下游节点）。
        depends_on=("validate", "audio"),
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
        id="format_gate",
        title="最终上传格式检查",
        kind=COMMAND,
        depends_on=("review_gate",),
        produces=("07_review/upload-format.json",),
        command=("{python}", "-m", "workflow.format_gate", "--batch-dir", "{batch_dir}"),
        docs=("docs/pipeline.md", "docs/upload-api.md"),
        description="所有内容填充与审核结束后，本地检查最终批次是否满足上传字段与格式要求；不调用模型、不访问网络、不提交题库。",
    ),
    Stage(
        id="upload_gate",
        title="上传人工确认",
        kind=HUMAN,
        depends_on=("format_gate",),
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
