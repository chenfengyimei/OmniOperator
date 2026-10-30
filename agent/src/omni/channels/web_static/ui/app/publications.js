// 发布草稿面板：B站写操作的产出（评论/私信草稿），人工核查后按状态流转。
import {$,esc} from './dom.js?v=20261031m3';
import {api} from './api.js?v=20261031m3';

let publicationRows=new Map();
export async function loadPublications(){
  try{
    const d=await api('/api/publications');publicationRows=new Map(d.items.map(x=>[x.id,x]));
    const labels={draft:'待审批',sending:'发送中或中断待查',unknown:'结果未知',sent:'已发送',not_sent:'人工确认未发送',blocked:'已阻止',discarded:'已丢弃'};
    $('#publication-list').innerHTML=(!d.enabled?'<div class="hint">B站写操作未启用；可查看和整理已有草稿。</div>':'')+d.items.map(x=>
      `<div class="card" style="padding:10px;border-bottom:1px solid var(--border)"><div>${esc(x.kind==='comment'?'评论':'私信')} · 账号 ${esc(x.account)} · ${esc(labels[x.status]||x.status)}</div>`+
      `<div style="overflow-wrap:anywhere">${esc(x.target)}</div><textarea id="pub-${esc(x.id)}" style="width:100%;min-height:90px" ${x.status==='draft'?'':'readonly'}>${esc(x.content)}</textarea>`+
      (x.status==='draft'?`<button class="btn" data-action="publications:edit" data-id="${esc(x.id)}">保存修改</button>`:'')+
      (['draft','blocked','not_sent'].includes(x.status)?`<button class="btn" data-action="publications:resolve" data-id="${esc(x.id)}" data-verdict="discarded">丢弃</button>`:'')+
      (['unknown','sending'].includes(x.status)?`<button class="btn" data-action="publications:resolve" data-id="${esc(x.id)}" data-verdict="sent">已核实发送成功</button><button class="btn" data-action="publications:resolve" data-id="${esc(x.id)}" data-verdict="not_sent">已核实未发送</button>`:'')+
      `<div class="hint">${esc(x.error||'')}${x.evidence?'<br>'+esc(x.evidence):''}</div></div>`).join('');
  }catch(e){$('#publication-list').textContent=e.message}
};
async function editPublication(id) {
  const row=publicationRows.get(id);
  try{await api(`/api/publications/${id}/edit`,{method:'POST',body:JSON.stringify({expected_hash:row.content_hash,content:$('#pub-'+id).value})});await loadPublications()}catch(e){OmniUI.notify(e.message,'error')}
}
async function resolvePublication(id,verdict) {
  const row=publicationRows.get(id);
  const prompt=verdict==='discarded'?'丢弃此草稿？':verdict==='sent'?'已在目标网站核实该账号发布了此内容？':'已在目标网站核实该账号没有发布此内容？如需重发，必须重新准备草稿并审批。';
  if(!await OmniUI.ask({title:'人工核查草稿',message:prompt+'\n账号：'+row.account+'\n目标：'+row.target+'\n内容：'+row.content,confirmLabel:'确认',danger:true}))return;
  try{await api(`/api/publications/${id}/resolve`,{method:'POST',body:JSON.stringify({expected_hash:row.content_hash,verdict})});await loadPublications()}catch(e){OmniUI.notify(e.message,'error')}
}

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'publications:refresh':()=>loadPublications(),
  'publications:edit':el=>editPublication(el.dataset.id),
  'publications:resolve':el=>resolvePublication(el.dataset.id,el.dataset.verdict),
};
