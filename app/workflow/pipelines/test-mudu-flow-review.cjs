const fs = require('node:fs');
const assert = require('node:assert/strict');
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const script = fs.readFileSync(require('node:path').join(__dirname, 'mudu-flow-review.workflow.js'), 'utf8');
const run = new AsyncFunction('args','agent','phase','pipeline','parallel',script);
const dims = ['内容完整','题干与题目匹配','缺题漏题及选项完整','错行上下题错位','图片完整且正确引用','按题型结构批次合并'];
async function scenario(failure) {
  const calls = [];
  const agent = async (_prompt, opts) => {
    calls.push(opts.label);
    if (opts.label === failure) return null;
    if (opts.label === '来源识别') return {status:'pass',evidence:['source'],issues:[]};
    if (opts.label === '结构解析') return {units:[{unit_id:'U1',kind:'standalone',section:'A',stem:'Q'}],notes:[]};
    if (opts.label === '小题清单') return {status:'pass',evidence:['whole paper'],issues:[],questions:Array.from({length:6},(_,i)=>({unit_id:'U1',question_id:String(i+1),evidence:['source '+(i+1)]}))};
    if (opts.label.startsWith('结构 ')) return {unit_id:'U1',checks:dims.map(d => ({dimension:d,status:failure === 'structure' && d === dims[0] ? 'fail':'pass',evidence:['original'],issues:[]})),rework_candidate:'核对题号'};
    if (/^(type|knowledge|answer|explanations) /.test(opts.label)) return {unit_id:'U1',status:'proposed',value:'{}',evidence:['original'],issues:[]};
    return {unit_id:'U1',verdict:failure === 'conflict' && opts.label.startsWith('二审') ? 'uncertain':'ok',findings:[],evidence:['original']};
  };
  const result = await run({batch_dir:'test'},agent,()=>{},async (items,fn)=>Promise.all(items.map((item,i)=>fn(null,item,i).catch(()=>null))),async tasks=>Promise.all(tasks.map(t=>t().catch(()=>null))));
  return {result,calls};
}
(async()=>{
  let t=await scenario(); assert.equal(t.result.status,'human_review_required'); assert.deepEqual(t.result.units[0].reviews.map(r=>r.stage),['AI审核','一审','二审']); assert.equal(t.result.third_review.population,6); assert.equal(t.result.third_review.sample_count,2); assert.deepEqual(t.result.third_review.items.map(x=>x.question.question_id),['1','4']); assert.equal(t.result.human_queue.length,1);
  t=await scenario('来源识别'); assert.equal(t.result.status,'blocked'); assert.equal(t.calls.length,1);
  t=await scenario('structure'); assert.equal(t.result.units[0].tags,null); assert(t.result.units[0].rework_candidate); assert(t.result.human_queue.some(h=>h.stage==='structure'));
  t=await scenario('knowledge U1'); assert(t.result.human_queue.some(h=>h.stage==='knowledge')); assert.equal(t.result.units[0].reviews.length,0);
  t=await scenario('explanations U1'); assert(t.result.human_queue.some(h=>h.stage==='explanations')); assert.equal(t.result.units[0].reviews.length,0);
  t=await scenario('conflict'); assert(t.result.human_queue.some(h=>h.stage==='二审')); assert(!t.calls.includes('三审 U1'));
  t=await scenario('小题清单'); assert.equal(t.result.status,'blocked'); assert(t.result.human_queue.some(h=>h.stage==='inventory'));
  console.log('7 scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
