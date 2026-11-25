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
    # Explicit non-blocking paths. Values are stable machine-readable labels used
    # by schedulers/diagnostics; they never weaken content or identity checks.
    defer: tuple = ()
    # Performance hints for orchestration (parallelism / late-bound work).
    optimization: tuple = ()
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
        description="先从原卷卷头识别来源、年份、地区、来源类型；Word 原生提取，每份 PDF 必须整卷 OCR 并保存 JSON/Markdown；保留表格、图片、边界和位置证据，OCR 未完成时提取不通过。",
    ),
    Stage(
        id="structure_gate",
        title="结构自动确认",
        kind=GATE,
        depends_on=("extract",),
        produces=("01_source/structure.confirmed.json", "07_review/structure-gate.json"),
        docs=("references/structure.md", "docs/workflow-regressions.md"),
        description="只负责把原卷拆成结构化单元并填入字段（题号/题型位置/题干/选项/材料/所属大题题组），不做阻断校验：题号覆盖、材料归属、题干保真等疑点一律记为结构告警留痕并继续下游；验证集中在最终格式闸门。只有完全拆不出任何单元时本阶段才无法交付。",
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
        id="answers",
        title="答案填充",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble", "types"),
        produces=("01_source_ans/answers.json",),
        docs=("references/stages/01-extract.md",),
        optional=True,
        defer=("missing_optional_input",),
        optimization=("parallel", "content_only"),
        description="逐题号提取原答案；没有答案来源时生成覆盖全部确认题号、带明确来源证据的‘无答案’决定，不猜、不漏项。",
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
        defer=("missing_optional_input", "external_wait"),
        optimization=("parallel", "late_external"),
        description="只消费已交付题型与确认结构，按真实听力单元切段并绑定版本；无音频才跳过。",
    ),
    Stage(
        id="explanations",
        title="打标签：解析与文章大意",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble", "types"),
        produces=("04_knowledge/explanations.json",),
        docs=("docs/content-labeling.md",),
        optional=True,
        defer=("missing_optional_input",),
        optimization=("parallel", "content_only"),
        description="有原解析/大意时按Word权威来源逐字匹配并记录绑定；确认不存在时对全部目标填‘无’，不生成。",
    ),
    Stage(
        id="knowledge",
        title="打标签：知识点标签匹配",
        kind=AGENT,
        depends_on=("structure_gate", "preassemble", "types", "answers"),
        produces=("04_knowledge/knowledge.json",),
        docs=("docs/knowledge-tagging.md",),
        optimization=("parallel", "content_only"),
        description="消费已验收题型与结构化答案，交知识点目录标签（完成句子等以答案反推考点）；下游在汇总时统一合并。",
    ),
    Stage(
        id="content_snapshot",
        title="画线前内容快照",
        kind=COMMAND,
        depends_on=("preassemble",),
        produces=("06_batch/content.snapshot.json",),
        command=("{python}", "-m", "workflow.content_snapshot", "--batch-dir", "{batch_dir}"),
        docs=("docs/pipeline.md",),
        description="在答案、解析、知识点并行填充期间保留前置原文快照；只复制已确认内容，不替代后续内容校验。",
    ),
    Stage(
        id="meta",
        title="难度与分类",
        kind=COMMAND,
        depends_on=("content_snapshot",),
        produces=("06_batch/batch_meta.base.json",),
        command=("scripts/05-meta/assign_meta.py", "--batch", "{content_snapshot}",
                 "--out", "{batch_meta_base}", "--report"),
        docs=("references/stages/05-meta.md",),
        description="从画线前的来源快照生成元数据视图；内容标签仍由并行阶段完成后在最终汇总固化。",
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
        defer=("missing_optional_input", "late_format"),
        optimization=("late_bound", "format_deferred"),
        description="从 docx 字符格式补 <u>…</u>；无 docx 批次跳过。",
    ),
    Stage(
        id="assemble",
        title="填充汇总与固化",
        kind=AGENT,
        depends_on=("preassemble", "answers", "types", "knowledge", "explanations", "underlines"),
        produces=("06_batch/batch.json",),
        docs=("references/stages/06-upload.md",),
        optimization=("late_bound", "content_only"),
        description="在画线标记后固化答案、解析、知识点和题型；显式消费画线后的 batch_meta.json，避免旧汇总覆盖原卷标记。听力仍不作依赖。",
    ),
    Stage(
        id="validate",
        title="本地自动体检",
        kind=GATE,
        # Validation must consume the fully assembled post-underlines batch;
        # depending only on underlines allowed a pre-assemble validation race.
        depends_on=("assemble",),
        produces=("07_review/validation-report.json",),
        docs=("references/stages/06-upload.md", "references/constraints.md"),
        defer=("external_wait", "late_format"),
        optimization=("content_only", "format_deferred"),
        description="全部只读检查；不触碰 SSH、媒体部署或上传接口。",
    ),
    Stage(
        id="review",
        title="一审全量盲判+标签合并",
        kind=AGENT,
        # 非听力审核分片在 validate 通过后即可与 audio 并行；仅含听力的分片等待 audio，
        # 最终由 format_gate 统一等待 review_gate 与 audio 全部完成。
        depends_on=("validate",),
        produces=("07_review/review-rounds.json",
                  "07_review/content-review.json", "07_review/metadata-review.json",
                  "07_review/manual-review-queue.json", "07_review/unchecked.json"),
        docs=("references/review-workflow.md", "codex-skills/mudu-question-review/SKILL.md"),
        description="执行一审全量独立盲判并配合确定性知识点标签合并（新批次不再启动二审，兼容保留历史二审记录与待裁决差异）；绑定真实分轮证据，只有具体冲突或不确定项进人工队列。",
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
        depends_on=("review_gate", "audio"),
        produces=("07_review/upload-format.json",),
        command=("{python}", "-m", "workflow.format_gate", "--batch-dir", "{batch_dir}"),
        docs=("docs/pipeline.md", "docs/upload-api.md"),
        defer=("external_wait",),
        optimization=("late_bound", "format_boundary"),
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

# Contract-level vocabulary.  A deferred path is an explicit scheduling state,
# never a successful content result and never a substitute for a hard validator.
DEFER_REASONS = {
    "missing_optional_input": "optional input is absent; mark skipped and continue",
    "external_wait": "external media/model evidence is pending; keep stage pending",
    "late_format": "format-only work belongs at the final upload boundary",
}


def defer_reasons(stage_id: str) -> tuple[str, ...]:
    """Return declared non-blocking/deferred paths for a stage."""
    stage = STAGE_BY_ID.get(stage_id)
    return tuple(stage.defer) if stage else ()


def optimization_hints(stage_id: str) -> tuple[str, ...]:
    """Return orchestration hints without changing validation semantics."""
    stage = STAGE_BY_ID.get(stage_id)
    return tuple(stage.optimization) if stage else ()


def stage_metadata(stage_id: str) -> dict:
    """Stable metadata projection for schedulers, diagnostics and UI consumers."""
    stage = STAGE_BY_ID.get(stage_id)
    if stage is None:
        return {}
    return {
        "optional": bool(stage.optional),
        "defer": list(stage.defer),
        "optimization": list(stage.optimization),
        "depends_on": list(stage.depends_on),
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
