import {merges,datasets} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const state={selected:null,side:'left',filter:'',expanded:null,custom:null};
const canEdit=()=>['admin','analyst'].includes(session.user.role);
const isAdmin=()=>session.user.role==='admin';
const SIDE_SOURCE={base:'底稿',left:'第一队',right:'第二队',both:'两队一致',merged:'已合并',unresolved:'未裁决',decision:'负责人裁决',left_add:'第一队新增',right_add:'第二队新增',both_add:'两队同增',decision_add:'裁决新增',deleted_both:'两处均删',deleted_left:'第一队删除',deleted_right:'第二队删除'};

function value(v){if(v===null||v===undefined)return '<span class="muted">（无）</span>';if(typeof v==='object')return '<code>'+esc(JSON.stringify(v))+'</code>';return esc(fmt(v));}
function fieldRows(record,fields,missing='（无此段）'){if(!record)return '<p class="empty small-empty">'+esc(missing)+'</p>';return '<dl class="kv">'+fields.map(f=>'<dt>'+esc(f.label)+'</dt><dd>'+value(record[f.key])+'</dd>').join('')+'</dl>';}

/* ---------- 列表 ---------- */
export async function mergePage(){
 const [list,allDatasets]=await Promise.all([merges.list(),datasets.list()]);
 return heading('06 / MERGE PROOF','可合并的校对副本','两队分头校对同一批管段，系统记住共同底稿并逐段合并；同项异改、删改相撞和同号新增异文交负责人裁决。','<a class="button" href="#/datasets">查看原始批次 ↗</a>')+
 '<div class="merge-grid"><section class="panel"><div class="section-title"><h2>开启两队合校</h2><span>共同底稿不可变</span></div>'+
 (canEdit()?'<form id="merge-form"><label>合校名称<input name="name" required minlength="2" placeholder="例如：11 月夜间流量复检"></label><label>共同底稿批次<select name="base_dataset_id" required>'+allDatasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label><div class="parameter-grid"><label>第一队名称<input name="left_label" value="甲队" maxlength="100"></label><label>第二队名称<input name="right_label" value="乙队" maxlength="100"></label></div><button type="submit" class="primary" '+(allDatasets.length?'':'disabled')+'>创建两份校对副本 ↗</button><p class="hint">原批次保持不变；合校结案后才生成可试验的新批次。</p></form>':'<p class="empty">观察员只可查看合校记录。</p>')+
 '</section><section><div class="section-title"><h2>合校会次</h2><span>'+list.length+' 次</span></div><div class="schedule-list">'+
 list.map(s=>'<article><div><strong>'+esc(s.name)+'</strong><p>'+esc(s.left.label)+' 与 '+esc(s.right.label)+' · 底稿 '+s.base_rows+' 段 · '+(s.status==='closed'?'已结案':'进行中')+'</p><small>'+(s.status==='closed'?'结案于 '+date(s.closed_at):(s.open_conflicts||0)+' 项待裁决 · '+(s.conflict_total||0)+' 项差异待核对')+'</small></div><a class="button small" href="#/merge/'+s.id+'">进入合校 ↗</a></article>').join('')+
 (!list.length?'<p class="empty">还没有合校会次。左侧选择一批已复核管段作为共同底稿。</p>':'')+'</div></section></div>';
}

/* ---------- 详情 ---------- */
export async function mergeDetail(id){
 if(!id)return mergePage();
 const d=await merges.get(id),s=d.session,copies=d.copies||[],fields=(d.fields||[]).filter(f=>f.key!=='segment_id');
 const left=copies.find(c=>c.side==='left'),right=copies.find(c=>c.side==='right');
 workspace.merge=d;
 const open=d.conflicts.filter(c=>c.status==='open'),decided=d.conflicts.filter(c=>c.status==='decided'),resolvedCount=d.conflicts.filter(c=>c.status==='resolved').length;
 const baseMap=Object.fromEntries(d.base_records.map(r=>[r.segment_id,r]));
 const traceMap=Object.fromEntries((d.merge.trace||[]).map(t=>[t.segment_id,t]));
 const closed=s.status==='closed';
 const filter=state.filter.trim().toLowerCase();
 const shown=(d.merge.records||[]).filter(r=>!filter||String(r.segment_id).toLowerCase().includes(filter));

 const header=heading('06 / MERGE PROOF',s.name,'共同底稿 '+d.base_records.length+' 段 · SHA256 '+s.base_checksum.slice(0,18)+'… · '+(closed?'已结案，新批次已生成':'逐段合并两队修订'),'<a class="button" href="#/merges">返回合校列表 ↗</a>')+
 '<div class="merge-status"><span class="status '+(closed?'completed':'running')+'">'+(closed?'已结案':'校对中')+'</span><span>'+esc(left.label)+' v'+left.version+(left.confirmed_version===left.version&&left.confirmed_version!==null?' · 已确认':' · 未确认')+'</span><span>'+esc(right.label)+' v'+right.version+(right.confirmed_version===right.version&&right.confirmed_version!==null?' · 已确认':' · 未确认')+'</span><span>未裁决 '+open.length+' 项</span><span>已裁决 '+decided.length+' 项</span>'+(resolvedCount?'<span>来源消解 '+resolvedCount+' 项</span>':'')+'<span>合校后 '+d.merge.rows+' 段</span>'+(closed?'<a class="text-link" href="#/workbench">用新批次发起试验 ↗</a>':'')+'</div>';

 if(closed){
   return header+'<section class="panel closed-panel"><div class="section-title"><h2>结案与批次</h2><span>结案与批次一起生效</span></div><div class="two-columns"><div><dl class="kv"><dt>新批次</dt><dd>合校批次 · '+esc(s.name)+'（编号 '+s.output_dataset_id+'）</dd><dt>内容指纹</dt><dd><code>'+esc(d.merge.checksum)+'</code></dd><dt>结案人</dt><dd>'+esc(s.closed_by||'—')+'</dd><dt>结案时间</dt><dd>'+date(s.closed_at)+'</dd></dl><p class="hint">原批次、原试验和原简报均保留；仅新增可试验的合校批次。重试结案不会重复成批。</p></div><div><div class="section-title"><h2>裁决与来源追溯</h2></div>'+decisionList(d)+'</div></div></section>'+mergedTable(d,fields,traceMap,shown,filter,true);
 }

 return header+
 (d.merge.issues.length?'<div class="warning-block"><strong>整批尚未通过可试验校验</strong><ul>'+d.merge.issues.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul></div>':'<div class="ok-block">整批校验通过：冲突已全部裁决，两侧副本已确认，可结案生成可试验批次。</div>')+
 '<section class="panel conflict-panel"><div class="section-title"><h2>负责人裁决</h2><span>同项异改 / 删改相撞 / 同号新增异文</span></div>'+
 (d.conflicts.length?'<div class="conflict-list">'+d.conflicts.map(c=>conflictCard(c,fields,closed)).join('')+'</div>':'<p class="empty">当前没有冲突项。两队改动可自动逐段合并。</p>')+'</section>'+
 '<div class="merge-grid copies-grid">'+copyPanel(left,fields,baseMap,d)+copyPanel(right,fields,baseMap,d)+'</div>'+
 editorPanel(d,fields,baseMap)+
 mergedTable(d,fields,traceMap,shown,filter,false)+
 '<section class="panel close-panel"><div class="section-title"><h2>结案并生成新批次</h2><span>冲突清零且整批有效才可执行</span></div><div class="close-row"><div><p>结案将同时写入可试验批次与结案记录；若执行中断，重试沿用同一次合并，不会重复成批。</p><p class="hint">新批次名称：合校批次 · '+esc(s.name)+'　内容指纹：<code>'+esc(d.merge.checksum.slice(0,24))+'…</code></p></div><button class="primary" id="close-merge" '+(!isAdmin()||open.length||!d.merge.valid?'disabled':'')+'>结案并生成批次 ↗</button></div></section>';
}

function conflictCard(c,fields,closed){
 const decided=c.status==='decided';
 const resolved=c.status==='resolved';
 const invalidated=(c.history||[]).filter(h=>h.action==='invalidated').length;
 return '<article class="conflict '+(decided?'decided':resolved?'resolved':'')+'" data-conflict="'+c.id+'">'+
 '<header><div><span class="conflict-kind">'+esc(c.kind_label)+'</span><strong>'+esc(c.segment_id)+' · '+esc(c.field_label)+'</strong></div><span class="status '+(decided?'completed':resolved?'running':'failed')+'">'+(decided?'已裁决':resolved?'来源已一致':'待裁决')+'</span></header>'+
 '<div class="three-way"><div><span>共同底稿</span>'+(c.field==='*'?fieldRows(c.base,fields,'底稿不含此段（两队同号新增）'):value(c.base))+'</div><div><span>第一队</span><span class="rev">来源修订 '+c.left_rev+'</span>'+(c.field==='*'?fieldRows(c.left,fields,'第一队已删除或未收录此段'):value(c.left))+'</div><div><span>第二队</span><span class="rev">来源修订 '+c.right_rev+'</span>'+(c.field==='*'?fieldRows(c.right,fields,'第二队已删除或未收录此段'):value(c.right))+'</div></div>'+
 (decided?'<p class="decision-line">裁决：'+esc(decisionText(c))+' · '+esc(c.decided_by||'')+' · '+date(c.decided_at)+'</p>'+((c.history||[]).length?'<p class="hint">裁决记录：'+c.history.map(h=>esc(historyText(h))).join('；')+'</p>':''):'')+
 (resolved?'<p class="hint">两侧来源已一致，此项自动消解，无需裁决。</p>':'')+
 (invalidated&&!decided?'<p class="hint">原裁决因来源再次改动而作废，需重新核对。'+esc(historyText((c.history||[]).filter(h=>h.action==='invalidated').slice(-1)[0]))+'</p>':'')+
 (!decided&&!resolved&&!closed&&isAdmin()?'<div class="decide-actions"><button class="button small" data-decide="left" data-id="'+c.id+'">'+(c.kind==='delete_edit'?'采用第一队（'+(c.left===null?'删除':'保留')+'）':'采用第一队')+'</button><button class="button small" data-decide="right" data-id="'+c.id+'">'+(c.kind==='delete_edit'?'采用第二队（'+(c.right===null?'删除':'保留')+'）':'采用第二队')+'</button>'+(c.kind==='delete_edit'?'<button class="button small" data-decide="delete" data-id="'+c.id+'">统一删除</button>':'')+'<button class="button small" data-decide="custom" data-id="'+c.id+'">'+(c.field==='*'?'自定义整段':'自定义值')+'</button></div>':'')+
 '</article>';
}

function decisionText(c){
 const d=c.decision||{};
 if(c.field==='*'){
   if(d.choice==='delete')return '维持删除';
   if(d.choice==='custom')return '自定义整段：'+JSON.stringify(d.values);
   if(d.choice==='left')return c.left===null?'采用第一队（删除此段）':'采用第一队（保留此段）';
   if(d.choice==='right')return c.right===null?'采用第二队（删除此段）':'采用第二队（保留此段）';
   return '—';
 }
 if(d.choice==='left')return '采用第一队：'+fmt(c.left);
 if(d.choice==='right')return '采用第二队：'+fmt(c.right);
 if(d.choice==='custom')return '自定义：'+fmt((d.values||{})[c.field]);
 return '—';
}
function historyText(h){
 if(h.action==='invalidated')return '原裁决作废（'+(h.reason==='source_changed'?'来源再改动':'两侧已一致')+'）';
 if(h.action==='redecided')return '重新裁决';
 if(h.action==='reopened')return '两侧再次分歧，重新核对';
 return h.action;
}

function copyPanel(copy,fields,baseMap,d){
 const filter=state.filter.trim().toLowerCase();
 const records=copy.records.filter(r=>!filter||String(r.segment_id).toLowerCase().includes(filter));
 const revs=copy.row_revs||{};
 return '<section class="panel copy-panel"><div class="section-title"><h2>'+esc(copy.label)+' 校对副本</h2><span>版本 v'+copy.version+(copy.confirmed_version===copy.version&&copy.confirmed_version!==null?' · 已确认':'')+'</span></div>'+
 '<div class="copy-toolbar"><input class="search copy-filter" placeholder="筛选管段编号" value="'+esc(state.filter)+'"><button class="button small" data-confirm="'+copy.side+'" '+(!canEdit()||copy.confirmed_version===copy.version&&copy.confirmed_version!==null?'disabled':'')+'>确认当前版本</button></div>'+
 '<p class="hint">点击管段行可编辑；每段保存后修订号增加。保存、删除或新增都会让确认失效。</p>'+
 '<div class="table-wrap copy-table"><table><thead><tr><th>管段</th><th>修订</th><th>状态</th><th>关键值</th></tr></thead><tbody>'+
 records.map(r=>{const sid=r.segment_id,rev=revs[sid]||0,base=baseMap[sid];const changed=rev>0;const missing=!base;
 return '<tr class="copy-row '+(state.selected===sid&&state.side===copy.side?'selected':'')+(changed?' changed':'')+'" data-select="'+esc(sid)+'" data-side="'+copy.side+'"><td>'+esc(sid)+'</td><td>'+(changed?'r'+rev:'—')+'</td><td>'+(missing?'新增':(changed?'已改':'底稿'))+'</td><td>'+value(r.night_flow)+' / '+value(r.acoustic_db)+'</td></tr>';}).join('')+
 '</tbody></table></div>'+
 (canEdit()?'<form class="add-segment" data-add="'+copy.side+'"><div class="section-title"><h3>新增管段</h3></div><div class="add-grid"><label>管段编号<input name="segment_id" placeholder="NC-281" required></label>'+fields.map(f=>'<label>'+esc(f.label)+'<input name="'+esc(f.key)+'" type="number" step="any" min="0" required></label>').join('')+'</div><button type="submit" class="button small">加入本队副本</button></form>':'')+
 '</section>';
}

function editorPanel(d,fields,baseMap){
 const sid=state.selected;if(!sid)return '<section class="panel editor-panel"><p class="empty">在上方任一副本中点击管段，即可编辑该队副本并追查来源。</p></section>';
 const copy=d.copies.find(c=>c.side===state.side);
 const record=copy.records.find(r=>r.segment_id===sid)||null;
 const base=baseMap[sid]||null;
 const trace=(d.merge.trace||[]).find(t=>t.segment_id===sid);
 const edits=(d.edits||[]).filter(e=>e.side===state.side&&e.segment_id===sid);
 const canWrite=canEdit();
 return '<section class="panel editor-panel"><div class="section-title"><h2>编辑 · '+esc(copy.label)+' · '+esc(sid)+'</h2><span>副本版本 v'+copy.version+' · 该段修订 r'+((copy.row_revs||{})[sid]||0)+'</span></div><div class="editor-grid"><div>'+
 (record?'<form id="segment-form"><div class="parameter-grid">'+fields.map(f=>'<label>'+esc(f.label)+(base?'<small>底稿 '+value(base[f.key])+'</small>':'')+'<input name="'+esc(f.key)+'" type="number" step="any" min="0" value="'+esc(record[f.key])+'" required></label>').join('')+'</div><div class="editor-actions"><button type="submit" class="primary small" '+(canWrite?'':'disabled')+'>保存改动</button><button type="button" class="button small" data-delete="'+esc(sid)+'" '+(canWrite?'':'disabled')+'>删除此段</button></div></form>':'<p class="empty small-empty">该段已被本队删除。'+(base?'<button class="button small" data-restore="'+esc(sid)+'" '+(canWrite?'':'disabled')+'>恢复为底稿</button>':'')+'</p>')+
 '</div><div class="editor-side"><h3>逐项来源</h3>'+
 (trace?'<p class="hint">合并动作：'+esc(trace.action)+' · 来源：'+esc(SIDE_SOURCE[trace.source]||trace.source)+'</p><dl class="kv">'+(trace.fields||[]).map(f=>'<dt>'+esc(f.label)+'</dt><dd>合校 '+value(f.value)+' <span class="muted">← '+esc(SIDE_SOURCE[f.source]||f.source)+'（底稿 '+value(f.base)+' / 一队 '+value(f.left)+' / 二队 '+value(f.right)+'）</span></dd>').join('')+'</dl>':'<p class="hint">该段未进入合校结果。</p>')+
 '<h3>本队改动日志</h3>'+(edits.length?'<ul class="edit-log">'+edits.map(e=>'<li><span>'+esc(e.action)+' · '+esc(e.field_label)+'</span><small>'+value(e.old_value)+' → '+value(e.new_value)+' · '+esc(e.actor)+' · '+date(e.at)+'</small></li>').join('')+'</ul>':'<p class="hint">本队尚未改动此段。</p>')+
 '</div></div></section>';
}

function mergedTable(d,fields,traceMap,shown,filter,closed){
 const records=shown;
 return '<section class="panel merged-panel"><div class="section-title"><h2>合校结果预览</h2><span>'+records.length+' 段 · 指纹 '+(d.merge.checksum||'').slice(0,16)+'…</span></div>'+
 '<div class="copy-toolbar"><input class="search" id="merge-filter" placeholder="筛选管段编号" value="'+esc(state.filter)+'"><span class="hint">点击管段查看逐字段来源。</span></div>'+
 '<div class="table-wrap"><table><thead><tr><th>管段</th>'+fields.map(f=>'<th>'+esc(f.label)+'</th>').join('')+'<th>合并来源</th></tr></thead><tbody>'+
 records.map(r=>{const t=traceMap[r.segment_id]||{};const bad=t.source==='unresolved';const detail=state.expanded===r.segment_id;
 return '<tr class="merge-row '+(bad?'unresolved':'')+'" data-trace="'+esc(r.segment_id)+'"><td><strong>'+esc(r.segment_id)+'</strong></td>'+fields.map(f=>'<td>'+value(r[f.key])+'</td>').join('')+'<td><span class="source-tag">'+esc(SIDE_SOURCE[t.source]||t.source||'—')+'</span></td></tr>'+
 (detail?'<tr class="trace-row"><td colspan="'+(fields.length+2)+'"><div class="trace-grid">'+(fields.map(f=>{const tf=(t.fields||[]).find(x=>x.field===f.key)||{};return '<div><span>'+esc(f.label)+'</span><strong>'+value(tf.value)+'</strong><small>'+esc(SIDE_SOURCE[tf.source]||tf.source||'—')+' · 底稿 '+value(tf.base)+' / 一队 '+value(tf.left)+' / 二队 '+value(tf.right)+'</small></div>';}).join(''))+'</div></td></tr>':'');}).join('')+
 '</tbody></table></div></section>';
}

function decisionList(d){
 const decided=d.conflicts.filter(c=>c.status==='decided');
 if(!decided.length)return '<p class="hint">本次合校没有需要裁决的冲突。</p>';
 return '<ul class="edit-log">'+decided.map(c=>'<li><span>'+esc(c.segment_id)+' · '+esc(c.kind_label)+' · '+esc(c.field_label)+'</span><small>'+esc(decisionText(c))+' · '+esc(c.decided_by||'')+' · '+date(c.decided_at)+'</small></li>').join('')+'</ul>';
}

/* ---------- 交互 ---------- */
export function bindMerges(refresh,id){
 const go=async(fn)=>{try{await fn();await refresh();}catch(e){notify(e.message,true);}};
 const createForm=document.querySelector('#merge-form');
 if(createForm)submit(createForm,async f=>{
   const s=await merges.create({name:f.get('name'),base_dataset_id:Number(f.get('base_dataset_id')),left_label:f.get('left_label'),right_label:f.get('right_label')});
   state.selected=null;notify('已建立两份校对副本');location.hash='#/merge/'+s.id;
 });
 if(!id)return;
 const detail=workspace.merge;
 document.querySelectorAll('.copy-filter,#merge-filter').forEach(input=>input.addEventListener('input',e=>{
   const q=e.target.value.trim().toLowerCase();
   state.filter=e.target.value;
   const scope=e.target.closest('.copy-panel')||document.querySelector('.merged-panel');
   scope?.querySelectorAll('tbody tr').forEach(row=>{
     const sid=(row.dataset.select||row.dataset.trace||'').toLowerCase();
     row.style.display=!q||sid.includes(q)?'':'none';
   });
 }));
 document.querySelectorAll('[data-trace]').forEach(row=>row.onclick=()=>{state.expanded=state.expanded===row.dataset.trace?null:row.dataset.trace;refresh();});
 document.querySelectorAll('[data-select]').forEach(row=>row.onclick=()=>{state.selected=row.dataset.select;state.side=row.dataset.side;refresh();});
 document.querySelectorAll('[data-confirm]').forEach(button=>button.onclick=()=>{
   const side=button.dataset.confirm,copy=detail.copies.find(c=>c.side===side);
   go(async()=>{await merges.confirm(id,side,copy.version);notify('已确认 '+copy.label+' 当前版本');});
 });
 document.querySelectorAll('[data-decide]').forEach(button=>button.onclick=()=>{
   const cid=Number(button.dataset.id),choice=button.dataset.decide;
   if(choice==='custom'){state.custom=cid;refresh();return;}
   const c=detail.conflicts.find(x=>x.id===cid);
   go(async()=>{await merges.decide(id,cid,{choice,left_rev:c.left_rev,right_rev:c.right_rev});notify('裁决已记录');});
 });
 document.querySelectorAll('[data-cancel-custom]').forEach(b=>b.onclick=()=>{state.custom=null;refresh();});
 const form=document.querySelector('#segment-form');
 if(form){
   const sid=state.selected,side=state.side,copy=detail.copies.find(c=>c.side===side),record=copy.records.find(r=>r.segment_id===sid);
   form.onsubmit=e=>{e.preventDefault();const values={};for(const pair of new FormData(form).entries())values[pair[0]]=Number(pair[1]);
     const changed={};for(const [k,v] of Object.entries(values))if(v!==record[k])changed[k]=v;
     if(!Object.keys(changed).length){notify('没有字段改动');return;}
     go(async()=>{await merges.updateSegment(id,side,sid,copy.version,changed);notify('已保存并记录来源修订');});};
 }
 document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{
   const copy=detail.copies.find(c=>c.side===state.side);
   go(async()=>{await merges.deleteSegment(id,state.side,b.dataset.delete,copy.version);notify('已从本队副本删除');});
 });
 document.querySelectorAll('[data-restore]').forEach(b=>b.onclick=()=>{
   const copy=detail.copies.find(c=>c.side===state.side);
   go(async()=>{await merges.restoreSegment(id,state.side,b.dataset.restore,copy.version);notify('已恢复底稿内容');});
 });
 document.querySelectorAll('[data-add]').forEach(f=>{f.onsubmit=e=>{
   e.preventDefault();const side=f.dataset.add,copy=detail.copies.find(c=>c.side===side);const fd=new FormData(f);
   const record={segment_id:String(fd.get('segment_id')||'').trim()};
   for(const p of (detail.fields||[])){if(p.key!=='segment_id'){const v=fd.get(p.key);if(v!==null&&v!=='')record[p.key]=Number(v);}}
   go(async()=>{await merges.addSegment(id,side,copy.version,record);state.selected=record.segment_id;state.side=side;notify('已加入本队副本');});
 };});
 document.querySelector('#close-merge')?.addEventListener('click',()=>go(async()=>{
   await merges.close(id);state.selected=null;notify('已结案并生成可试验批次');location.hash='#/merge/'+id;
 }));
 // 自定义裁决内联表单
 if(state.custom){
   const c=detail.conflicts.find(x=>x.id===state.custom);
   const host=document.querySelector('[data-conflict="'+state.custom+'"]');
   if(c&&host){
     const fields=(detail.fields||[]).filter(f=>f.key!=='segment_id');
     const body=c.field==='*'?fields.map(f=>'<label>'+esc(f.label)+'<input name="v-'+esc(f.key)+'" type="number" step="any" min="0" required></label>').join(''):'<label>'+esc(c.field_label)+'<input name="v-'+esc(c.field)+'" type="number" step="any" min="0" required></label>';
     host.insertAdjacentHTML('beforeend','<form class="custom-form" data-decision-form="'+c.id+'"><input type="hidden" name="choice" value="custom">'+body+'<label>裁决说明（可选）<input name="note" maxlength="500" placeholder="记录采用该值的业务理由"></label><div class="editor-actions"><button type="submit" class="primary small">保存自定义裁决</button><button type="button" class="button small" data-cancel-custom>取消</button></div></form>');
     host.querySelector('[data-cancel-custom]').onclick=()=>{state.custom=null;refresh();};
     host.querySelector('form').onsubmit=e=>{
       e.preventDefault();const fd=new FormData(e.target);const values={};
       for(const pair of fd.entries())if(pair[0].startsWith('v-'))values[pair[0].slice(2)]=pair[1];
       go(async()=>{await merges.decide(id,c.id,{choice:'custom',values,note:fd.get('note')||'',left_rev:c.left_rev,right_rev:c.right_rev});state.custom=null;notify('裁决已记录');});
     };
   }
 }
}
