// 技能面板：列表 + 变更待审阅（D18）+ 编辑器（读原文带指纹 → 保存 CAS → 历史/差异/回滚）。
import {$,esc} from './dom.js?v=20261030d';
import {api} from './api.js?v=20261030d';
import {openModal} from './modals.js?v=20261030d';
import {setTabBadge} from './tabs.js?v=20261030d';

export async function loadSkills(){
  const d=await api('/api/skills');
  setTabBadge('skills',d.skills.length);
  $('#skills-list').innerHTML=d.skills.length?d.skills.map(s=>
    `<li class="skill-item" data-open="0"><b>${esc(s.name)}</b> <span class="s-id">v${esc(s.version)}</span>`+
    `<button class="svc-btn" style="margin-left:6px" onclick="toggleSkill('${esc(s.name)}',this.closest('li'))">查看</button>`+
    `<button class="svc-btn" onclick="editSkill('${esc(s.name)}')">编辑</button>`+
    `<div class="skill-desc">${esc(s.description)}</div></li>`).join(''):'<li class="empty">还没有技能（agent 完成多步任务后会自动沉淀）</li>';
  loadSkillPending();
}
// ---------- 技能变更待审阅（D18）：agent/review 自动改的修订 ----------
async function loadSkillPending(){
  try{
    const d=await api('/api/skills/pending');
    const bar=$('#skill-pending-bar');if(!bar)return;
    if(!d.count){bar.classList.add('hidden');bar.innerHTML='';return}
    bar.classList.remove('hidden');
    bar.innerHTML=`<span>⚠ <b>${d.count}</b> 条技能变更待审阅（小鱼自动改的）</span>`+
      `<span style="flex:1"></span><button class="svc-btn" onclick="openReview()">审阅</button>`;
  }catch(e){/* 旁路信息，不打断技能页 */}
}
window.openReview=async()=>{
  openModal('review-modal');
  $('#review-diff').textContent='';
  try{
    const d=await api('/api/skills/pending');
    $('#review-list').innerHTML=d.items.length?d.items.map(it=>
      `<div class="cron-row"><span class="c-name">${esc(it.name)} · rev ${it.rev}</span>`+
      `<span class="c-sched">v${it.version==null?'—':it.version} · ${esc(it.summary||'无摘要')} · ${esc((it.created_at||'').slice(5,16))} · ${esc(it.author)}${it.applied?'':'（未落盘）'}</span>`+
      `<button class="svc-btn" onclick="reviewDiff('${esc(it.name)}',${it.rev})">差异</button>`+
      (it.base_rev!=null?`<button class="svc-btn" onclick="reviewRollback('${esc(it.name)}',${it.rev},${it.base_rev})">回滚这条</button>`:'')+
      `<button class="svc-btn" onclick="reviewAck('${esc(it.name)}',${it.rev})">知道了</button></div>`).join('')
      :'<div class="empty">没有待审阅的变更</div>';
  }catch(e){$('#review-list').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
};
window.reviewDiff=async(name,rev)=>{
  try{
    const d=await api(`/api/skills/pending/${encodeURIComponent(name)}/diff?rev=${rev}`);
    $('#review-diff').textContent=(d.diff||'（无差异）')+(d.truncated?'\n…（差异过长已截断）':'');
  }catch(e){OmniUI.notify(e.message,'error')}
};
window.reviewAck=async(name,rev)=>{
  try{
    await api('/api/skills/'+encodeURIComponent(name)+'/ack',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({rev})});
    openReview();loadSkillPending();
  }catch(e){OmniUI.notify(e.message,'error')}
};
window.reviewAckAll=async()=>{
  try{await api('/api/skills/ack-all',{method:'POST'});openReview();loadSkillPending()}
  catch(e){OmniUI.notify(e.message,'error')}
};
window.reviewRollback=async(name,rev,baseRev)=>{
  if(!await OmniUI.ask({title:'回滚技能修订',message:`回滚「${name}」到 rev ${baseRev}（撤销 rev ${rev} 这次修改）？当前内容会先存进历史，回滚是新修订、可再撤销。`,confirmLabel:'回滚',danger:true}))return;
  try{
    const cur=await api('/api/skills/'+encodeURIComponent(name)); // 现指纹：冲突就拒绝，不覆盖
    await api('/api/skills/'+encodeURIComponent(name)+'/rollback',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rev:baseRev,expected_hash:cur.hash})});
    await api('/api/skills/'+encodeURIComponent(name)+'/ack',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({rev})});
    openReview();loadSkills();
  }catch(e){OmniUI.notify(e.message,'error')}
};
window.toggleSkill=async(name,el)=>{
  const body=el.querySelector('.skill-body');
  if(body){body.remove();el.dataset.open='0';return}
  try{
    const d=await api('/api/skills/'+encodeURIComponent(name));
    const pre=document.createElement('div');pre.className='skill-body';pre.textContent=d.content;
    el.appendChild(pre);el.dataset.open='1';
  }catch(e){OmniUI.notify(e.message,'error')}
};

// ---------- 技能编辑器（D18） ----------
let skillEditing=null; // {name, hash}：保存必须带读取时的指纹，别人改过就 409
window.editSkill=async(name)=>{
  try{
    const d=await api('/api/skills/'+encodeURIComponent(name));
    skillEditing={name,hash:d.hash};
    $('#skill-editor').value=d.content;
    $('#skill-editor-title').textContent='编辑技能：'+name+'（'+(d.references||[]).length+' 个参考文件不受影响）';
    $('#skill-msg').textContent='';$('#skill-msg').style.color='#34d399';
    $('#skill-history').innerHTML='';$('#skill-diff').textContent='';
    openModal('skill-modal');
  }catch(e){OmniUI.notify(e.message,'error')}
};
window.saveSkill=async()=>{
  if(!skillEditing)return;
  try{
    const d=await api('/api/skills/'+encodeURIComponent(skillEditing.name)+'/save',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({content:$('#skill-editor').value,expected_hash:skillEditing.hash})});
    skillEditing.hash=d.hash; // 保存成功后指纹更新，可继续编辑
    $('#skill-msg').textContent='✅ '+d.message;
    loadSkills();
  }catch(e){$('#skill-msg').style.color='#f87171';$('#skill-msg').textContent='❌ '+e.message}
};
window.reloadSkill=()=>{if(skillEditing)editSkill(skillEditing.name)};
window.loadSkillHistory=async()=>{
  if(!skillEditing)return;
  try{
    const d=await api('/api/skills/'+encodeURIComponent(skillEditing.name)+'/history');
    $('#skill-history').innerHTML=d.revisions.length?d.revisions.map(r=>
      `<div class="cron-row"><span class="c-name">rev ${r.rev}</span>`+
      `<span class="c-sched">v${r.version==null?'—':r.version} · ${esc(r.summary||'无摘要')} · ${esc((r.created_at||'').slice(5,16))} · ${esc(r.author||'?')}</span>`+
      `<button class="svc-btn" onclick="skillDiff(${r.rev})">差异</button>`+
      `<button class="svc-btn" onclick="skillRollback(${r.rev})">回滚</button></div>`).join('')
      :'<div class="empty">还没有历史修订</div>';
  }catch(e){OmniUI.notify(e.message,'error')}
};
window.skillDiff=async(rev)=>{
  if(!skillEditing)return;
  try{
    const d=await api(`/api/skills/${encodeURIComponent(skillEditing.name)}/diff?rev=${rev}`);
    $('#skill-diff').textContent=d.diff||'（与当前内容没有差异）';
  }catch(e){OmniUI.notify(e.message,'error')}
};
window.skillRollback=async(rev)=>{
  if(!skillEditing)return;
  if(!await OmniUI.ask({title:'回滚技能',message:'回滚到 rev '+rev+'？当前内容会先存进历史，回滚是产生新修订，还能再回滚回来。',confirmLabel:'回滚',danger:true}))return;
  try{
    const d=await api('/api/skills/'+encodeURIComponent(skillEditing.name)+'/rollback',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rev,expected_hash:skillEditing.hash})});
    $('#skill-msg').style.color='#34d399';$('#skill-msg').textContent='✅ '+d.message;
    await editSkill(skillEditing.name); // 内容与指纹都变了，重载编辑器
    loadSkillHistory();loadSkills();
  }catch(e){$('#skill-msg').style.color='#f87171';$('#skill-msg').textContent='❌ '+e.message}
};
