// Harness workflow script body. Candidates only; no file writes or uploads.
const dir = args && args.batch_dir;
if (typeof dir !== "string" || !dir.trim()) return {status:"blocked", reason:"batch_dir required", units:[]};
const strings = {type:"array", items:{type:"string"}};
const unitSchema = {type:"object", additionalProperties:false, required:["unit_id","kind","section","stem"], properties:{unit_id:{type:"string"}, kind:{type:"string",enum:["standalone","group"]}, section:{type:"string"}, stem:{type:"string"}}};
const assessment = {type:"object", additionalProperties:false, required:["status","evidence","issues"], properties:{status:{type:"string",enum:["pass","fail","uncertain"]}, evidence:strings, issues:strings}};
const candidate = {type:"object", additionalProperties:false, required:["unit_id","status","value","evidence","issues"], properties:{unit_id:{type:"string"}, status:{type:"string",enum:["proposed","uncertain"]}, value:{type:"string"}, evidence:strings, issues:strings}};
const review = {type:"object", additionalProperties:false, required:["unit_id","verdict","findings","evidence"], properties:{unit_id:{type:"string"}, verdict:{type:"string",enum:["ok","error","uncertain"]}, findings:strings, evidence:strings}};
const rules = "只读原件和目录；不写文件，不调用上传、发布、修改或远程接口。输出仅为候选，不能读取或无法核实须说明，不得猜测。";
const holds = [];
function hold(unit_id, stage, reason) { holds.push({unit_id, stage, reason, status:"open"}); }
function passed(a) { return a && a.status === "pass" && a.evidence.length > 0 && a.issues.length === 0; }
phase("来源与结构");
const source = await agent(`识别批次 ${dir} 的题源、年份、地区、版本及原卷、答案、媒体是否可定位；核查来源冲突。来源缺失或冲突不得 pass。${rules}`, {schema:assessment,label:"来源识别",phase:"来源与结构"});
if (!passed(source)) { hold("","source","来源失败、不确定、缺证据或存在问题"); return {batch_dir:dir,status:"blocked",source,units:[],human_queue:holds}; }
const structureSchema = {type:"object",additionalProperties:false,required:["units","notes"],properties:{units:{type:"array",items:unitSchema},notes:strings}};
const structure = await agent(`读取 ${dir}/01_source/正文.txt 和原卷，识别完整单元及题组；保留题号和原卷结构。无法读取或识别记 notes。${rules}`, {schema:structureSchema,label:"结构解析",phase:"来源与结构"});
if (!structure || !structure.units.length || structure.notes.length || structure.units.some(u => !u.unit_id.trim()) || new Set(structure.units.map(u => u.unit_id)).size !== structure.units.length) {
  hold("","structure","结构解析失败、为空、有未决说明或单元 ID 不唯一"); return {batch_dir:dir,status:"blocked",source,structure,units:[],human_queue:holds};
}
const dimensions = ["内容完整","题干与题目匹配","缺题漏题及选项完整","错行上下题错位","图片完整且正确引用","按题型结构批次合并"];
const inventorySchema = {type:"object",additionalProperties:false,required:["status","evidence","issues","questions"],properties:{status:{type:"string",enum:["pass","fail","uncertain"]},evidence:strings,issues:strings,questions:{type:"array",items:{type:"object",additionalProperties:false,required:["unit_id","question_id","evidence"],properties:{unit_id:{type:"string"},question_id:{type:"string"},evidence:strings}}}}};
const inventory = await agent(`根据原卷逐小题建立完整清单，将每道小题映射到结构单元，包括独立题本身；每条提供原卷定位证据，并以整卷题号范围及覆盖证据证明无遗漏。无法确认完整性必须 uncertain。单元清单：${JSON.stringify(structure.units)}。${rules}`,{schema:inventorySchema,label:"小题清单",phase:"来源与结构"});
const questions = inventory && inventory.questions;
if (!passed(inventory) || !questions.length || questions.some(q => !q.question_id.trim() || !q.evidence.length || !structure.units.some(u => u.unit_id === q.unit_id)) || new Set(questions.map(q => q.question_id)).size !== questions.length || structure.units.some(u => !questions.some(q => q.unit_id === u.unit_id))) {
  hold("","inventory","小题清单缺失、不完整、重复、未映射或缺证据，不能抽样"); return {batch_dir:dir,status:"blocked",source,structure,inventory,units:[],human_queue:holds};
}
const check = {type:"object",additionalProperties:false,required:["dimension","status","evidence","issues"],properties:{dimension:{type:"string",enum:dimensions},status:{type:"string",enum:["pass","fail","uncertain"]},evidence:strings,issues:strings}};
const structural = {type:"object",additionalProperties:false,required:["unit_id","checks","rework_candidate"],properties:{unit_id:{type:"string"},checks:{type:"array",items:check},rework_candidate:{type:"string"}}};
phase("逐题结构审核");
const structureRows = await pipeline(structure.units, async (_prev, unit) => ({unit,result:await agent(`逐题核对 ${JSON.stringify(unit)}，对六项逐项给状态、原件证据和问题：${dimensions.join("、")}。不适用也需证据；不确定不是通过。失败或不确定时 rework_candidate 写待人工复核的修改建议。${rules}`,{schema:structural,label:`结构 ${unit.unit_id}`,phase:"逐题结构审核"})}));
phase("四路并行打标与审核");
const rows = await pipeline(structureRows, async (_prev, row, index) => {
  const unit = structure.units[index];
  const s = row && row.result;
  const valid = s && s.unit_id === unit.unit_id && s.checks.length === dimensions.length && dimensions.every(d => s.checks.filter(c => c.dimension === d && passed(c)).length === 1);
  if (!valid) { hold(unit.unit_id,"structure","六项审核失败、缺失、重复、无证据或存在疑点；返工后重审"); return {unit,structure_review:s || null,rework_candidate:s && s.rework_candidate || "待人工定位后提出返工候选",tags:null,reviews:[]}; }
  const branches = [["knowledge","子智能体2（知识点/教材）：知识点只写目录叶子及 ID，教材对应须有证据；不生成、不改写解析与大意；value JSON 含 knowledge、knowledgeIds，子题 knowledge 与定位证据"],["type","子智能体3（题型/词数/内容完整）：题型子类仅选当前目录合法值；填写该单元自身题干、材料与选项的英文词数；题干与材料不匹配、选项缺失或内容被截断时 contentComplete=false；value JSON 含 type、subtypes、words、contentComplete 与证据"],["answer","子智能体4（答案/解析整理/难度）：以原件答案为准，逐题号提取；难度依据可观察特征（长度、句式、选项设计、考查点）判定并根据不足不得猜；不生成解析或文章大意；value JSON 含 answers、difficulty 与判断依据"],["explanations","子智能体1（解析/大意）：按真实题号、章节、材料边界逐字匹配原解析与原【文章大意】；用户规则：来源没有时不再使用 AI 生成，直接跳过，对应字段填「无」，不撰写、不推断、不加来源标记；归属不明给 uncertain；听力不写内容解析；不得改写答案、知识点或结构；value JSON 含 explanation、articleSummary、各子题 explanation 与定位证据"],];
  const values = await parallel(branches.map(([name,task]) => () => agent(`单元 ${JSON.stringify(unit)}；来源 ${JSON.stringify(source)}；任务：${task}。value 返回可追溯候选的 JSON 字符串，证据或目录缺失则 uncertain。${rules}`,{schema:candidate,label:`${name} ${unit.unit_id}`,phase:"四路并行打标与审核"})));
  const tags = {};
  branches.forEach(([name],i) => { const v=values[i]; tags[name]=v || null; if (!v || v.unit_id !== unit.unit_id || v.status !== "proposed" || !v.value.trim() || !v.evidence.length || v.issues.length) hold(unit.unit_id,name,"打标失败、不确定、无证据或有问题"); });
  if (holds.some(h => h.unit_id === unit.unit_id)) return {unit,structure_review:s,rework_candidate:"",tags,reviews:[]};
  const reviews = [];
  for (const stage of ["AI审核","一审","二审"]) {
    const r = await agent(`独立执行${stage}，核对原件、结构六项、四路候选及此前发现：${JSON.stringify({unit,structure:s,tags,reviews})}。检查冲突、证据、AI生成和未实际查看的图片音频；有疑点 verdict=uncertain，明确错误为 error，全部有证据才 ok。${rules}`,{schema:review,label:`${stage} ${unit.unit_id}`,phase:"四路并行打标与审核"});
    reviews.push({stage,result:r || null});
    if (!r || r.unit_id !== unit.unit_id || r.verdict !== "ok" || !r.evidence.length || r.findings.length) { hold(unit.unit_id,stage,"审核失败、冲突、未核实、无证据或有发现；需人工处理并重审"); break; }
  }
  return {unit,structure_review:s,rework_candidate:"",tags,reviews};
});
for (let i=0;i<rows.length;i++) if (!rows[i]) hold(structure.units[i].unit_id,"pipeline","代理或编排异常，结果缺失");
// Deterministic evenly spaced sample over source inventory; retain full identities and evidence.
const sampleCount = Math.ceil(questions.length * 0.2);
const sample = Array.from({length:sampleCount}, (_,i) => questions[Math.floor(i * questions.length / sampleCount)]);
const thirdReview = [];
for (const q of sample) {
  const row = rows.find(r => r && r.unit.unit_id === q.unit_id);
  if (!row || row.reviews.length !== 3 || holds.some(h => h.unit_id === q.unit_id)) {
    hold(q.unit_id,"三审",`抽样小题 ${q.question_id} 前序未完成`); thirdReview.push({question:q,result:null}); continue;
  }
  const r = await agent(`三审仅核查抽中小题 ${JSON.stringify(q)}；根据原卷核对候选和前序审核：${JSON.stringify(row)}。必须提供此小题可追溯证据；疑点为 uncertain，错误为 error。${rules}`,{schema:review,label:`三审 ${q.question_id}`,phase:"四路并行打标与审核"});
  thirdReview.push({question:q,result:r || null});
  if (!r || r.unit_id !== q.unit_id || r.verdict !== "ok" || !r.evidence.length || r.findings.length) hold(q.unit_id,"三审",`抽样小题 ${q.question_id} 审核失败或有疑点`);
}
hold("","human","人工确认结构、冲突和必要的图像音频检查；AI审核不等于人工审核");
return {batch_dir:dir,status:"human_review_required",source,inventory,units:rows,third_review:{population:questions.length,sample_count:sampleCount,method:"原卷清单顺序等间距向下取整",items:thirdReview},human_queue:holds,note:"仅候选，不代表审核闸门通过；不上传、不发布、不修改正式批次。"};
