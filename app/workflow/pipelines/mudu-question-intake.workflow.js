// 录题多代理工作流（harness workflow 工具）
//
// 用法：用 workflow 工具调用本脚本体，args 传 {"batch_dir": "<批次绝对路径>"}。
// 阶段：结构解析 → 并行标注（标注 + 独立审核）→ 冲突汇总。
//
// 产物：
//   <batch_dir>/candidates/<unit_id>.json   每个单元的标注候选
//   返回值中的 summary.blocking / summary.human_queue 可直接转成
//   07_review/content-review.json 与 07_review/manual-review-queue.json。
//
// 边界：子代理只写 candidates/，不写 06_batch、不写 manifest、不上传。

const DIR = args.batch_dir;
const TEXT = `${DIR}/01_source/正文.txt`;
const ANSWERS = `${DIR}/01_source_ans/answers.json`;

const STRUCT = {
  type: "object",
  additionalProperties: false,
  required: ["units", "notes"],
  properties: {
    units: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["unit_id", "kind", "section", "stem"],
        properties: {
          unit_id: { type: "string" },
          kind: { type: "string", enum: ["standalone", "group"] },
          section: { type: "string" },
          stem: { type: "string" }
        }
      }
    },
    notes: { type: "array", items: { type: "string" } }
  }
};

const CLASSIFY = {
  type: "object",
  additionalProperties: false,
  required: ["unit_id", "type", "answer", "knowledge", "difficulty", "evidence", "issues"],
  properties: {
    unit_id: { type: "string" },
    type: { type: "string" },
    subtype: { type: "string" },
    answer: { type: "string" },
    knowledge: { type: "array", items: { type: "string" } },
    difficulty: { type: "string" },
    evidence: { type: "array", items: { type: "string" } },
    issues: { type: "array", items: { type: "string" } }
  }
};

// 子智能体1：解析与文章大意（独立于标注，单独一次调用）。
const CONTENT = {
  type: "object", additionalProperties: false,
  required: ["unit_id", "explanation", "articleSummary", "subquestions", "evidence"],
  properties: {
    unit_id: { type: "string" },
    explanation: { type: "string" },
    articleSummary: { type: "string" },
    subquestions: { type: "array", items: { type: "object", additionalProperties: false,
      required: ["number", "explanation", "evidence"],
      properties: { number: { type: "string" }, explanation: { type: "string" },
                    evidence: { type: "array", items: { type: "string" } } } } },
    evidence: { type: "array", items: { type: "string" } }
  }
};

const REVIEW = {
  type: "object",
  additionalProperties: false,
  required: ["unit_id", "verdict", "findings"],
  properties: {
    unit_id: { type: "string" },
    verdict: { type: "string", enum: ["ok", "suspect", "error"] },
    findings: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["field", "severity", "reason", "evidence"],
        properties: {
          field: { type: "string" },
          severity: { type: "string", enum: ["blocker", "major", "minor"] },
          reason: { type: "string" },
          evidence: { type: "array", items: { type: "string" } }
        }
      }
    }
  }
};

const SUMMARY = {
  type: "object",
  additionalProperties: false,
  required: ["blocking", "human_queue", "note"],
  properties: {
    blocking: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["unit_id", "field", "reason"],
        properties: {
          unit_id: { type: "string" },
          field: { type: "string" },
          reason: { type: "string" }
        }
      }
    },
    human_queue: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["unit_id", "field", "reason"],
        properties: {
          unit_id: { type: "string" },
          field: { type: "string" },
          reason: { type: "string" }
        }
      }
    },
    note: { type: "string" }
  }
};

phase("结构解析");
log(`批次目录：${DIR}`);

const structure = await agent(
  `你是木牍英语题库的结构解析器。\n` +
  `只读文件：${TEXT}\n` +
  `任务：按原卷真实结构输出单元清单。\n` +
  `规则：\n` +
  `1. 一个单元 = 一道独立题，或一个题组（共享材料 + 多道子题）。阅读文章带多个小题时必须合成一个 group 单元。\n` +
  `2. unit_id 用 LOCAL-01、LOCAL-02 递增，稳定唯一。\n` +
  `3. stem 写该单元题干首句（题组写材料首句），保留原样，不改写、不翻译。\n` +
  `4. section 写所属大题标题，如「一、单项填空」。\n` +
  `5. 不要补造题目，不要推断答案，不确定的写进 notes。`,
  { schema: STRUCT, label: "结构解析", phase: "结构解析" }
);

if (!structure) {
  return { error: "结构解析失败，未产出单元清单", batch_dir: DIR };
}

log(`解析出 ${structure.units.length} 个单元`);

phase("并行标注");

const rows = await pipeline(
  structure.units,
  async (_prev, unit) => agent(
    `你在给一道题做标注候选。\n` +
    `原卷：${TEXT}\n答案：${ANSWERS}\n` +
    `目标单元：${JSON.stringify(unit)}\n` +
    `要求：\n` +
    `1. 先读原卷，定位该单元原文。\n` +
    `2. type 取英语主类名称（如「单项填空」「阅读理解」），不要自造目录里没有的类型。\n` +
    `3. knowledge 只能写知识库目录里的叶子节点；有子节点的中间节点不得直接写入。\n` +
    `4. answer 从答案文件取对应题号；取不到写空串并在 issues 说明。\n` +
    `5. difficulty 只能是 容易 / 适中 / 困难；证据不足写空串。\n` +
    `6. evidence 写原文行或答案文件键，便于复核。\n` +
    `7. 不生成解析或文章大意，交给独立的内容子智能体。\n` +
    `8. 把结果同时写入 ${DIR}/candidates/${unit.unit_id}.json（目录不存在就创建，内容与返回结构一致）。\n` +
    `不要上传，不要修改 06_batch，不要写 manifest。`,
    { schema: CLASSIFY, label: `标注 ${unit.unit_id}`, phase: "并行标注" }
  ),
  async (classified, unit) => {
    if (!classified) return { unit_id: unit.unit_id, classified: null, content: null };
    const content = await agent(
      `你是打标签阶段独立的解析与文章大意子智能体（子智能体1）。原卷：${TEXT}；答案：${ANSWERS}；单元：${JSON.stringify(unit)}；已标注信息：${JSON.stringify(classified)}。` +
      `只做四件事：按真实题号、章节、材料匹配原解析；确认缺失后生成解析；匹配所属篇章的原文章大意；阅读/任务型/读写综合/完形/语法选择无大意时生成2–3句中文。` +
      `先查原文，有则逐字保留，不得用AI替代；生成内容必须以（AI生成，待校对）结尾，并说明来源定位与推导依据。题组各子题分别写解析，大意只在组级写一次。` +
      `重复A/B不是唯一标识，边界不确定时status=needs_review，不生成代替。听力不生成内容解析。不得改答案、知识点、难度、结构或任何文件。`,
      { schema: CONTENT, label: `解析与大意 ${unit.unit_id}`, phase: "并行标注" }
    );
    return { unit_id: unit.unit_id, classified, content };
  },
  async (candidate, unit) => {
    const classified = candidate && candidate.classified;
    const content = candidate && candidate.content;
    if (!classified) {
      return { unit_id: unit.unit_id, classified: null, content, review: null };
    }
    const review = await agent(
      `你是独立审核者，只读。\n` +
      `原卷：${TEXT}\n答案：${ANSWERS}\n` +
      `单元：${JSON.stringify(unit)}\n标注结果：${JSON.stringify(classified)}\n解析与大意候选：${JSON.stringify(content)}\n` +
      `任务：核对标注是否与原卷、答案一致。\n` +
      `判定：\n` +
      `- verdict=ok：全部可复核且正确。\n` +
      `- verdict=suspect：有疑点但无法确定错。\n` +
      `- verdict=error：明确与原卷或答案冲突。\n` +
      `findings 每条给 field、severity（blocker/major/minor）、reason、evidence。\n` +
      `只报告，不修改标注，不写候选文件，不上传。`,
      { schema: REVIEW, label: `审核 ${unit.unit_id}`, phase: "并行标注" }
    );
    return { unit_id: unit.unit_id, classified, content, review };
  }
);

const okRows = rows.filter((r) => r && r.classified);
const findings = [];
for (const row of okRows) {
  const review = row.review;
  if (!review) continue;
  for (const f of review.findings || []) {
    findings.push({
      unit_id: String(row.unit_id || ""),
      field: String(f.field || ""),
      severity: String(f.severity || ""),
      reason: String(f.reason || ""),
      evidence: (f.evidence || []).map((e) => String(e))
    });
  }
}

phase("汇总");

let summary;
if (findings.length > 0) {
  const merged = await agent(
    `把下面的审核发现分成两类：必须人工处理的 blocking，和需要人确认但不阻断的 human_queue。\n` +
    `发现：${JSON.stringify(findings)}\n` +
    `规则：severity=blocker 一律进 blocking；major 若涉及答案、题型、知识点归属也进 blocking；其余进 human_queue。\n` +
    `note 写一句总体结论。不要修改任何题目数据。`,
    { schema: SUMMARY, label: "冲突汇总", phase: "汇总" }
  );
  summary = merged || { blocking: [], human_queue: [], note: "汇总失败，保留原始发现" };
} else {
  summary = { blocking: [], human_queue: [], note: "审核未发现问题" };
}

return {
  batch_dir: DIR,
  unit_count: structure.units.length,
  units: structure.units.map((u) => ({
    unit_id: String(u.unit_id),
    kind: String(u.kind),
    section: String(u.section),
    stem: String(u.stem)
  })),
  annotations: okRows.map((r) => ({
    unit_id: String(r.unit_id),
    type: String(r.classified.type || ""),
    subtype: String(r.classified.subtype || ""),
    answer: String(r.classified.answer || ""),
    content: r.content || null,
    knowledge: (r.classified.knowledge || []).map((k) => String(k)),
    difficulty: String(r.classified.difficulty || ""),
    issues: (r.classified.issues || []).map((i) => String(i)),
    verdict: r.review ? String(r.review.verdict) : "missing"
  })),
  review_missing_units: rows.filter((r) => !r || !r.review).map((r) => (r ? String(r.unit_id) : "unknown")),
  findings,
  summary,
  structural_notes: (structure.notes || []).map((n) => String(n))
};
