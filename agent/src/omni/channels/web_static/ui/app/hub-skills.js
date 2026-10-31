// 设置中心「技能」域：只读统计 + 技能库管理 + 待审阅 + 外来包 + 技能设置。
// 统计请求是只读的；所有写入仍走既有 SkillStore 锁/CAS/linter 与 SkillInbox 隔离链。
import {$,esc,relTime} from './dom.js?v=20261031m16';
import {api} from './api.js?v=20261031m16';
import {openModal} from './modals.js?v=20261031m16';
import {loadSkills} from './skills.js?v=20261031m16';

const FIELD_IDS={
  history_keep:'cfg-skills-history-keep',
  max_write_chars:'cfg-skills-write-chars',
};
const FIELD_LABELS={
  skills: {'history_keep':'修订保留数','max_write_chars':'agent 写入上限'},
};
const SOURCE_LABELS={
  skill_create:'创建',skill_patch:'按节修订',skill_rewrite:'全文重写',skill_rollback:'回滚',
  skill_import:'外来包激活',skill_archive:'归档',skill_restore:'恢复',
  workbench:'工作台编辑','detected-change':'外部改动快照',
};
const TEMPLATE=`## When to Use

（什么时候应该用这个技能；写清楚触发场景与不适用场景。）

## Procedure

1. 第一步做什么；
2. 第二步做什么；
3. 最后如何收尾。

## Pitfalls

- 容易踩的坑，以及绕开的办法。

## Verification

- 用什么证据确认做对了（回读、计数、字段比对等）。
`;

let overview=null,overviewVersion=0,overviewLoading=false;
let filterText='';
let settingsRevision=null,settingsDirty=false,settingsLoading=false,settingsSaving=false,settingsVersion=0;

const num=value=>Number(value||0).toLocaleString('zh-CN');
const bytes=value=>{
  const n=Number(value||0);
  if(n<1024)return n+' B';
  if(n<1024*1024)return (n/1024).toFixed(1)+' KB';
  return (n/(1024*1024)).toFixed(1)+' MB';
};
const when=value=>value?String(value).slice(5,16).replace('T',' '):'—';
const sourceLabel=source=>SOURCE_LABELS[source]||source||'未知';
const metric=(label,value,note)=>`<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;

export function loadSkillsHub(){
  loadOverview();
  loadSettings();
}

// ---------------------------------------------------------------- 总览

async function loadOverview(){
  if(overviewLoading)return;
  const version=++overviewVersion;
  overviewLoading=true;
  const button=$('#skills-hub-refresh');if(button)button.disabled=true;
  const summary=$('#skills-hub-summary');if(summary)summary.textContent='正在读取技能库…';
  try{
    const data=await api('/api/skills/overview');
    if(version!==overviewVersion)return;
    overview=data;
    renderOverview(data);
  }catch(error){
    if(version===overviewVersion)renderOverviewError(error);
  }finally{
    if(version===overviewVersion){
      overviewLoading=false;
      if(button)button.disabled=false;
    }
  }
}

function renderOverview(data){
  const totals=data.totals||{},pending=data.pending||{count:0,items:[]};
  $('#skills-hub-summary').textContent=
    `${num(totals.skills)} 个技能 · ${num(totals.archived)} 个归档 · ${num((data.revisions||{}).total)} 条修订 · 待审阅 ${num(pending.count)}`;
  $('#skills-hub-metrics').innerHTML=metric('技能',num(totals.skills),`另有 ${num(totals.archived)} 个归档`) +
    metric('待审阅',num(pending.count),'agent/review 自动改的修订') +
    metric('修订快照',num((data.revisions||{}).total),`最近 ${when((data.revisions||{}).latest_at)}`) +
    metric('参考文件',num(totals.references),'按需读取，不执行') +
    metric('技能库体积',bytes(Number(totals.skill_bytes||0)+Number(totals.reference_bytes||0)),`历史另占 ${bytes(totals.history_bytes)}`) +
    metric('隔离区',num(data.quarantine_count),`收件箱 ${num(data.inbox_count)} 个待导入`) +
    (totals.invalid?metric('需要修复',num(totals.invalid),'格式损坏，可归档或重写'):'');
  $('#skills-hub-activity-total').textContent=`${num((data.revisions||{}).total)} 条`;
  renderActivity(data.activity||[]);
  renderSources(data.revisions||{});
  renderList();
  renderPending(pending);
  renderInbox(data);
  $('#skills-hub-count').textContent=`${num(totals.skills)} 个`;
  $('#skills-hub-archived-count').textContent=num(totals.archived);
  $('#skills-hub-inbox-count').textContent=`收件箱 ${num(data.inbox_count)} · 隔离区 ${num(data.quarantine_count)}`;
}

function renderOverviewError(error){
  const stale=error.status===404;
  $('#skills-hub-summary').textContent='技能统计读取失败';
  $('#skills-hub-metrics').innerHTML=
    `<div class="empty-rich"><span class="material-icons">error_outline</span><strong>技能统计暂时读不到</strong>`+
    `<p>${esc(stale?'工作台服务还是旧版本：静态页面已更新，但后端缺少技能统计接口。请关闭「OmniOperator 工作台服务」窗口后重新运行 start.bat。':error.message)}</p>`+
    `<button class="btn" data-action="skills-hub:refresh">重试</button></div>`;
  OmniUI.notify(stale?'技能统计接口不存在：请重启工作台服务':'技能统计读取失败：'+error.message,'error');
}

function renderActivity(activity){
  const total=activity.reduce((sum,item)=>sum+Number(item.count||0),0);
  if(!total){
    $('#skills-hub-activity').innerHTML=
      '<div class="empty-rich"><span class="material-icons">timeline</span><strong>最近 14 天还没有修订</strong><p>小鱼创建、修改或回滚技能后，这里会出现活动柱。</p></div>';
    return;
  }
  const max=Math.max(1,...activity.map(item=>Number(item.count||0)));
  $('#skills-hub-activity').innerHTML=activity.map(item=>{
    const count=Number(item.count||0),height=count?Math.max(6,Math.round(count/max*100)):2;
    return `<div class="skills-activity-bar${count?' has-value':''}" role="img" aria-label="${esc(item.date)}：${count} 条修订" title="${esc(item.date)}：${count} 条修订">`+
      `<span style="height:${height}%"></span><small>${esc(item.date.slice(5))}</small></div>`;
  }).join('');
}

function renderSources(revisions){
  const sources=Object.entries(revisions.by_source||{});
  const authors=Object.entries(revisions.by_author||{});
  if(!sources.length){$('#skills-hub-sources').innerHTML='';return}
  $('#skills-hub-sources').innerHTML=
    `<div class="skills-source-row">${sources.map(([name,count])=>`<span class="chip">${esc(sourceLabel(name))} ${num(count)}</span>`).join('')}</div>`+
    `<p class="hint">作者：${authors.map(([name,count])=>`${esc(name)} ${num(count)}`).join(' · ')}</p>`;
}

function renderList(){
  const list=$('#skills-hub-list');if(!list)return;
  const skills=(overview&&overview.skills)||[];
  const needle=filterText.trim().toLowerCase();
  const filtered=needle?skills.filter(item=>String(item.name).toLowerCase().includes(needle)||String(item.description||'').toLowerCase().includes(needle)):skills;
  $('#skills-hub-filter-note').textContent=needle?`${filtered.length}/${skills.length} 个匹配`:'';
  if(!skills.length){
    list.innerHTML='<div class="empty-rich"><span class="material-icons">auto_awesome</span><strong>技能库还是空的</strong><p>小鱼完成多步任务后会自动沉淀技能，也可以点右上角「新建技能」手动写一个。</p><button class="btn" data-action="skills-hub:new">新建技能</button></div>';
    renderArchived();
    return;
  }
  if(!filtered.length){
    list.innerHTML='<div class="empty"><span>没有匹配的技能</span></div>';
    renderArchived();
    return;
  }
  list.innerHTML=filtered.map(item=>{
    const badges=[
      `<span class="ui-badge">v${esc(item.version||'?')}</span>`,
      item.pending?`<span class="ui-badge warn">待审阅 ${num(item.pending)}</span>`:'',
      item.valid?'':'<span class="ui-badge danger">结构损坏</span>',
      item.valid&&!item.name_matches?'<span class="ui-badge warn">名字与目录不一致</span>':'',
      item.valid&&(item.sections_missing||[]).length?`<span class="ui-badge warn">缺 ${item.sections_missing.length} 节</span>`:'',
    ].filter(Boolean).join(' ');
    return `<div class="skills-hub-item${item.pending?' has-pending':''}">`+
      `<div class="skills-hub-item-main"><div class="skills-hub-item-title"><b>${esc(item.name)}</b>${badges}</div>`+
      `<p>${esc(item.description||'（没有描述）')}</p>`+
      `<div class="skills-hub-item-meta">${num(item.body_chars)} 字 · ${num(item.references)} 个参考文件 · ${num(item.revision_count)} 条修订 · 更新 ${esc(relTime(item.updated_at))}</div></div>`+
      `<div class="skills-hub-item-actions">`+
      `<button class="btn small" data-action="skills:edit" data-name="${esc(item.name)}">编辑</button>`+
      `<button class="btn small danger" data-action="skills-hub:archive" data-name="${esc(item.name)}" data-hash="${esc(item.hash||'')}">归档</button>`+
      `</div></div>`;
  }).join('');
  renderArchived();
}

function renderArchived(){
  const box=$('#skills-hub-archived');if(!box)return;
  const items=(overview&&overview.archived)||[];
  if(!items.length){box.innerHTML='<p class="hint">还没有归档技能。归档会把技能移出技能库，但保留历史与副本，可随时恢复。</p>';return}
  box.innerHTML=items.map(item=>`<div class="skills-hub-item is-archived">`+
    `<div class="skills-hub-item-main"><div class="skills-hub-item-title"><b>${esc(item.name)}</b><span class="ui-badge">v${esc(item.version||'?')}</span>`+
    `${item.valid?'':'<span class="ui-badge danger">结构损坏</span>'}</div>`+
    `<p>${esc(item.description||'（没有描述）')}</p>`+
    `<div class="skills-hub-item-meta">归档于 ${esc(when(item.archived_at))} · ${num(item.revision_count)} 条修订${item.archive_reason?' · '+esc(item.archive_reason):''}</div></div>`+
    `<div class="skills-hub-item-actions"><button class="btn small" data-action="skills-hub:restore" data-name="${esc(item.name)}">恢复</button></div>`+
    `</div>`).join('');
}

function renderPending(pending){
  const box=$('#skills-hub-pending');if(!box)return;
  const items=(pending.items||[]).slice(0,6);
  if(!pending.count){
    box.innerHTML='<div class="empty-rich"><span class="material-icons">task_alt</span><strong>没有待审阅的变更</strong><p>小鱼自动改技能后，修订会先出现在这里；你可以看差异、回滚或标记已读。</p></div>';
    return;
  }
  box.innerHTML=`<p class="hint">共 ${num(pending.count)} 条。技能改动不会改变风险等级或工具权限，但值得看一眼。</p>`+
    items.map(item=>`<div class="skills-pending-row"><div><b>${esc(item.name)}</b> <span class="s-id">rev ${num(item.rev)}</span>`+
      `<p>${esc(item.summary||'无摘要')} · ${esc(sourceLabel(item.source))} · ${esc(when(item.created_at))}${item.applied?'':' · 未落盘'}</p></div></div>`).join('')+
    (pending.truncated?'<p class="hint">只展示最近 20 条；完整列表在「打开审阅」里。</p>':'');
}

// ---------------------------------------------------------------- 外来包

function renderInbox(data){
  const zips=data.inbox||[],quarantine=data.quarantine||[];
  const box=$('#skills-hub-inbox');
  if(!zips.length){
    box.innerHTML='<p class="hint">收件箱为空。上传 zip 后会先列在这里，点「导入」才进入隔离区。</p>';
  }else{
    box.innerHTML=`<h4>收件箱（${num(zips.length)}）</h4>`+zips.map(item=>`<div class="skills-hub-item">`+
      `<div class="skills-hub-item-main"><div class="skills-hub-item-title"><b>${esc(item.name)}</b><span class="ui-badge">${bytes(item.bytes)}</span></div>`+
      `<div class="skills-hub-item-meta">放进收件箱 ${esc(when(item.modified_at))}</div></div>`+
      `<div class="skills-hub-item-actions"><button class="btn small primary" data-action="skills-hub:inbox-import" data-name="${esc(item.name)}">导入</button>`+
      `<button class="btn small danger" data-action="skills-hub:inbox-remove" data-name="${esc(item.name)}">移除</button></div>`+
      `</div>`).join('');
  }
  const qbox=$('#skills-hub-quarantine');
  if(!quarantine.length){
    qbox.innerHTML='<p class="hint">隔离区为空。导入后的技能包会在这里显示完整文件清单，确认后才可激活。</p>';
    return;
  }
  qbox.innerHTML=`<h4>隔离区待激活（${num(quarantine.length)}）</h4>`+quarantine.map(item=>{
    const files=(item.files||[]).map(file=>`<li><code>${esc(file.path)}</code><span>${bytes(file.bytes)}</span><small>sha256 ${esc(String(file.sha256||'').slice(0,12))}</small></li>`).join('');
    const badges=[
      `<span class="ui-badge">v${esc(item.version||'?')}</span>`,
      item.manifest_missing?'<span class="ui-badge danger">清单缺失</span>':'',
      item.valid?'':'<span class="ui-badge danger">SKILL.md 损坏</span>',
    ].filter(Boolean).join(' ');
    return `<div class="skills-quarantine-card">`+
      `<div class="skills-hub-item-title"><b>${esc(item.name)}</b>${badges}</div>`+
      `<p>${esc(item.description||'（没有描述）')}</p>`+
      `<div class="skills-hub-item-meta">来源 zip：${esc(item.source_zip||'未知')} · 导入于 ${esc(when(item.imported_at))} · ${num((item.files||[]).length)} 个文件</div>`+
      `<details class="skills-manifest"><summary>查看文件清单与指纹</summary><ul>${files||'<li>清单缺失，请重新导入</li>'}</ul></details>`+
      `<div class="skills-hub-item-actions"><button class="btn small primary" data-action="skills-hub:quarantine-activate" data-name="${esc(item.name)}" ${item.can_activate?'':'disabled'}>激活到技能库</button>`+
      `<button class="btn small danger" data-action="skills-hub:quarantine-discard" data-name="${esc(item.name)}">丢弃</button></div>`+
      `</div>`;
  }).join('');
}

// ---------------------------------------------------------------- 设置

async function loadSettings({force=false}={}){
  if(settingsSaving||settingsLoading||(!force&&settingsDirty))return;
  if(settingsDirty&&force&&!await OmniUI.ask({title:'重新读取技能配置',message:'放弃这页尚未保存的修改并读取配置文件？',confirmLabel:'重新读取'}))return;
  const version=++settingsVersion;
  settingsLoading=true;lockSettings();
  setSettingsMessage('正在读取配置…');
  try{
    const data=await api('/api/skills/settings');
    if(version!==settingsVersion)return;
    renderSettings(data,true);
    setSettingsMessage('配置已读取；保存后需重启相应入口。');
  }catch(error){
    if(version===settingsVersion){
      setSettingsMessage('读取失败：'+error.message,'err');
      OmniUI.notify(error.message,'error');
    }
  }finally{
    if(version===settingsVersion){settingsLoading=false;lockSettings()}
  }
}

function renderSettings(data,fill){
  settingsRevision=data.revision;
  if(fill){
    for(const [key,id]of Object.entries(FIELD_IDS)){
      const node=$('#'+id);
      if(node)node.value=data.saved.skills[key];
    }
    settingsDirty=false;
  }
  $('#skills-hub-settings-status').textContent=data.restart_required?'已保存配置与当前运行不同，重启相应入口后生效。':'已保存配置与当前运行一致。';
  const running=(data.running||{}).skills||{},saved=(data.saved||{}).skills||{};
  $('#skills-hub-settings-running').innerHTML=Object.keys(FIELD_IDS).map(key=>{
    const label=FIELD_LABELS.skills[key];
    return `<div class="skills-setting-row"><span>${esc(label)}</span><span>运行 ${esc(running[key])}</span><span>保存 ${esc(saved[key])}</span></div>`;
  }).join('');
  lockSettings();
}

function lockSettings(){
  const disabled=settingsSaving||settingsLoading||settingsRevision==null;
  const controls=$('#skills-hub-settings-controls');if(controls)controls.disabled=disabled;
  const save=$('#skills-hub-settings-save');if(save)save.disabled=disabled;
  const reload=$('#skills-hub-settings-reload');if(reload)reload.disabled=settingsSaving||settingsLoading;
}

function setSettingsMessage(text,tone=''){
  const node=$('#skills-hub-settings-msg');if(!node)return;
  node.textContent=text;node.className='save-msg'+(tone?' '+tone:'');
}

async function saveSettings(){
  if(settingsSaving||settingsRevision==null||!$('#skills-hub-settings-form').reportValidity())return;
  const updates={skills:{}};
  for(const [key,id]of Object.entries(FIELD_IDS))updates.skills[key]=Number($('#'+id).value);
  settingsSaving=true;lockSettings();setSettingsMessage('正在保存技能设置…');
  try{
    const data=await api('/api/skills/settings',{method:'PUT',body:JSON.stringify({revision:settingsRevision,updates})});
    renderSettings(data,true);
    setSettingsMessage('已保存；重启相应入口后使用新设置。','ok');
    OmniUI.notify('技能设置已保存，重启相应入口后生效');
  }catch(error){
    setSettingsMessage('保存失败：'+error.message,'err');
    OmniUI.notify(error.message,'error');
  }finally{
    settingsSaving=false;lockSettings();
  }
}

// ---------------------------------------------------------------- 动作

async function archiveSkill(name,hash){
  if(!await OmniUI.ask({title:'归档技能',message:`归档「${name}」？\n它会从技能库和 skills_list 移除，但内容保留在 .archive/ 与修订历史里，可随时恢复。`,confirmLabel:'归档',danger:true}))return;
  try{
    await api(`/api/skills/${encodeURIComponent(name)}/archive`,{method:'POST',body:JSON.stringify({expected_hash:hash||null})});
    OmniUI.notify(`已归档 ${name}`);
    await Promise.all([loadOverview(),loadSkills()]);
  }catch(error){OmniUI.notify(error.message,'error');if(error.status===409)loadOverview()}
}

async function restoreSkill(name){
  if(!await OmniUI.ask({title:'恢复技能',message:`把「${name}」恢复回技能库？同名技能已存在时会拒绝覆盖。`,confirmLabel:'恢复'}))return;
  try{
    await api(`/api/skills/archive/${encodeURIComponent(name)}/restore`,{method:'POST'});
    OmniUI.notify(`已恢复 ${name}`);
    await Promise.all([loadOverview(),loadSkills()]);
  }catch(error){OmniUI.notify(error.message,'error')}
}

async function importPackage(name){
  try{
    const data=await api(`/api/skills/inbox/${encodeURIComponent(name)}/import`,{method:'POST'});
    OmniUI.notify(data.message.split('\n')[0]);
    await loadOverview();
  }catch(error){OmniUI.notify(error.message,'error')}
}

async function removePackage(name){
  if(!await OmniUI.ask({title:'移除技能包',message:`从收件箱移除「${name}」？不会影响隔离区与正式技能库。`,confirmLabel:'移除',danger:true}))return;
  try{
    await api(`/api/skills/inbox/${encodeURIComponent(name)}`,{method:'DELETE'});
    OmniUI.notify(`已移除 ${name}`);
    await loadOverview();
  }catch(error){OmniUI.notify(error.message,'error')}
}

async function activatePackage(name){
  const item=((overview&&overview.quarantine)||[]).find(row=>row.name===name);
  const count=item?(item.files||[]).length:0;
  if(!await OmniUI.ask({title:'激活外来技能',message:`把「${name}」激活进正式技能库？\n来源 zip：${item?.source_zip||'未知'}\n文件数：${count}\n激活后它会出现在 skills_list，小鱼可能按其中的流程行动。请确认你已看过文件清单。`,confirmLabel:'激活'}))return;
  try{
    const data=await api(`/api/skills/quarantine/${encodeURIComponent(name)}/activate`,{method:'POST'});
    OmniUI.notify(data.message.split('\n')[0]);
    await Promise.all([loadOverview(),loadSkills()]);
  }catch(error){OmniUI.notify(error.message,'error');loadOverview()}
}

async function discardPackage(name){
  if(!await OmniUI.ask({title:'丢弃隔离区技能',message:`丢弃隔离区里的「${name}」？正式技能库不受影响，但隔离区内容会被删除。`,confirmLabel:'丢弃',danger:true}))return;
  try{
    await api(`/api/skills/quarantine/${encodeURIComponent(name)}/discard`,{method:'POST'});
    OmniUI.notify(`已丢弃 ${name}`);
    await loadOverview();
  }catch(error){OmniUI.notify(error.message,'error')}
}

async function uploadPackage(file,input){
  if(!file)return;
  const limit=(overview&&overview.limits&&overview.limits.max_zip_bytes)||4*1024*1024;
  if(file.size>limit){OmniUI.notify(`压缩包超过 ${bytes(limit)} 上限`,'error');input.value='';return}
  const message=$('#skills-hub-import-msg');if(message)message.textContent='正在上传…';
  try{
    const response=await fetch('/api/skills/inbox/upload?filename='+encodeURIComponent(file.name),{
      method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/zip'},body:file,
    });
    let data;try{data=await response.json()}catch{data={}}
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'上传失败');
    if(message)message.textContent=data.message||'已上传';
    OmniUI.notify((data.message||'已上传').split('\n')[0]);
    await loadOverview();
  }catch(error){
    if(message)message.textContent='上传失败：'+error.message;
    OmniUI.notify(error.message,'error');
  }finally{input.value=''}
}

function openNewSkill(){
  $('#skill-new-name').value='';
  $('#skill-new-description').value='';
  $('#skill-new-content').value=TEMPLATE;
  $('#skill-new-msg').textContent='';
  openModal('skill-new-modal');
  setTimeout(()=>$('#skill-new-name')?.focus(),60);
}

async function fillTemplate(force=false){
  const content=$('#skill-new-content');
  if(!force&&content.value.trim()&&content.value.trim()!==TEMPLATE.trim()){
    if(!await OmniUI.ask({title:'填入四节模板',message:'当前正文会被模板覆盖；已写好的内容不会保留。继续吗？',confirmLabel:'覆盖'}))return;
  }
  content.value=TEMPLATE;
}

async function createSkill(event){
  event.preventDefault();
  const name=$('#skill-new-name').value.trim();
  const description=$('#skill-new-description').value.trim();
  const content=$('#skill-new-content').value;
  const message=$('#skill-new-msg');
  if(!/^[a-z0-9][a-z0-9-]{2,39}$/.test(name)){message.textContent='技能名需为 3–40 位小写字母/数字/连字符';return}
  if(!description||description.length>60){message.textContent='描述必须非空且 ≤60 字';return}
  if(content.trim().length<50){message.textContent='正文太短（<50 字），请写成完整流程';return}
  const submit=$('#skill-new-submit');submit.disabled=true;message.textContent='正在创建…';
  try{
    const data=await api('/api/skills',{method:'POST',body:JSON.stringify({name,description,content})});
    message.textContent='✅ '+(data.message||'已创建');
    OmniUI.notify(`已创建技能 ${name}`);
    setTimeout(()=>$('#skill-new-modal').close(),350);
    await Promise.all([loadOverview(),loadSkills()]);
  }catch(error){
    message.textContent='创建失败：'+error.message;
  }finally{submit.disabled=false}
}

// ---------------------------------------------------------------- 绑定

$('#skills-hub-settings-form')?.addEventListener('submit',event=>{event.preventDefault();saveSettings()});
$('#skills-hub-settings-form')?.addEventListener('input',()=>{settingsDirty=true;setSettingsMessage('有尚未保存的修改。')});
$('#skill-new-form')?.addEventListener('submit',createSkill);
$('#skills-hub-search')?.addEventListener('input',event=>{filterText=event.target.value;renderList()});
$('#skills-hub-upload')?.addEventListener('change',event=>uploadPackage(event.target.files?.[0],event.target));

export const actions={
  'skills-hub:refresh':()=>loadOverview(),
  'skills-hub:new':()=>openNewSkill(),
  'skills-hub:template':()=>fillTemplate(),
  'skills-hub:archive':el=>archiveSkill(el.dataset.name,el.dataset.hash),
  'skills-hub:restore':el=>restoreSkill(el.dataset.name),
  'skills-hub:inbox-import':el=>importPackage(el.dataset.name),
  'skills-hub:inbox-remove':el=>removePackage(el.dataset.name),
  'skills-hub:quarantine-activate':el=>activatePackage(el.dataset.name),
  'skills-hub:quarantine-discard':el=>discardPackage(el.dataset.name),
  'skills-hub:reload-settings':()=>loadSettings({force:true}),
};
