/* 记忆系统控制器：设置中心与旧页面共享六页签，查询、事件和状态限定在挂载根内。
   全部数据来自 /api/memory-system/*，读取一律分页；动态文本全部转义。 */
import {esc} from '/ui/app/dom.js?v=20261031m4';
export function mountMemory(root){
const $=selector=>root.querySelector(selector);
const UI=window.OmniUI;

const KIND_LABEL={daily:'每日',weekly:'每周',monthly:'每月'};
const ENTRY_GROUP_LABEL={archive:'常驻档案（稳定特征）',resident:'常驻记忆（进行中的事）',event:'临时事件（可过期）'};
const NOTE_STATUS={done:'已完成',running:'整理中',failed:'待重试'};

let view='overview';
let dialogueLoaded=false,sessionLoaded=false,notesLoaded=false,peopleLoaded=false,settingsLoaded=false;
let dState={day:null,session:null,after:0,more:false};
let sState={day:null,session:null,run:'',after:0,more:false};
let noteKind='daily',notePeriod=null;
let selectedPerson=null;

// ---------------- 视图切换 ----------------
for(const button of root.querySelectorAll('.view-tabs button')){
  button.onclick=()=>switchView(button.dataset.view);
}
function switchView(next){
  view=next;
  for(const button of root.querySelectorAll('.view-tabs button'))
    button.setAttribute('aria-selected',String(button.dataset.view===next));
  for(const node of root.querySelectorAll('.memory-view'))
    node.hidden=node.id!==next+'-view';
  if(next==='overview')loadOverview();
  else if(next==='dialogue'&&!dialogueLoaded){dialogueLoaded=true;loadDialogueDays();}
  else if(next==='session'&&!sessionLoaded){sessionLoaded=true;loadSessionDays();}
  else if(next==='notes'&&!notesLoaded){notesLoaded=true;loadNotes();}
  else if(next==='people'&&!peopleLoaded){peopleLoaded=true;loadPeople();}
  else if(next==='settings'&&!settingsLoaded){settingsLoaded=true;loadSettings();}
}

// ---------------- 总览 ----------------
async function loadOverview(){
  try{
    const data=await UI.request('/api/memory-system/overview');
    const m=data.modules;
    const cards=[
      ['对话全量',`${m.dialogue.entries} 条`,`${m.dialogue.days} 天 · ${m.dialogue.sessions} 会话 · ${m.dialogue.chars} 字`],
      ['会话全量',`${m.session_log.entries} 条`,`含 ${m.session_log.tools} 个工具步 · ${m.session_log.runs} 次运行`],
      ['每日笔记',`${m.journal.daily.done} 篇`,`待重试 ${m.journal.daily.failed} · 整理中 ${m.journal.daily.running}`],
      ['核心人物',`${m.people.people} 人`,`账号 ${m.people.accounts} 个`],
    ];
    $('#overview-metrics').innerHTML=cards.map(([label,value,note])=>
      `<div class="metric-card"><span class="metric-icon"><span class="material-icons">memory</span></span><div><span>${esc(label)}</span><b>${esc(value)}</b><small>${esc(note)}</small></div></div>`).join('');
    $('#journal-state').textContent=data.journal_enabled
      ?`自动整理已启用：每天 ${data.journal_config.daily_time} 整理当天（错过自动补，每轮最多补 ${data.journal_config.max_catchup} 天）。`
      :'自动整理未启用（设置中心「记忆系统 → 整理设置」可开启）。';
    const chips=$('#latest-notes');
    chips.replaceChildren();
    for(const kind of ['daily','weekly','monthly']){
      const note=data.latest_notes[kind];
      const chip=UI.element('div',null,'note-chip');
      chip.append(UI.element('b',`${KIND_LABEL[kind]}笔记`));
      chip.append(UI.element('span',note?`${note.period_key} · ${NOTE_STATUS[note.status]||note.status} · ${note.chars} 字`:'还没有'));
      chips.append(chip);
    }
    const runs=await UI.request('/api/memory-system/runs?limit=10');
    renderRunList($('#overview-runs'),runs.runs);
  }catch(error){$('#journal-state').textContent='读取失败：'+error.message;UI.notify(error.message,'error')}
}
$('#overview-reload').onclick=()=>loadOverview();

function renderRunList(root,runs){
  root.replaceChildren();
  if(!runs.length){root.append(UI.element('p','还没有整理记录。','empty'));return}
  for(const run of runs){
    const row=UI.element('div',null,'run-row');
    row.append(UI.element('b',`${run.kind} ${run.period_key}`));
    row.append(UI.element('span',(run.ok?'成功':'失败')+' · '+UI.date(run.created_at)));
    if(run.detail)row.append(UI.element('span',run.detail.slice(0,80)));
    root.append(row);
  }
}

// ---------------- 对话全量（M1） ----------------
async function loadDialogueDays(){
  try{
    const data=await UI.request('/api/memory-system/dialogue/days');
    const root=$('#dialogue-days');
    root.replaceChildren();
    if(!data.days.length){root.append(UI.element('p','还没有对话记录。','empty'));return}
    for(const day of data.days){
      const button=UI.element('button');
      button.append(UI.element('strong',day.day));
      button.append(UI.element('small',`${day.sessions} 会话 · ${day.entries} 条 · ${day.chars} 字`));
      button.onclick=()=>selectDialogueDay(day.day);
      root.append(button);
    }
    selectDialogueDay(data.days[0].day);
  }catch(error){UI.notify(error.message,'error')}
}
function selectDialogueDay(day){
  dState={day,session:null,after:0,more:false};
  for(const button of $('#dialogue-days').children)button.classList.toggle('active',button.querySelector('strong').textContent===day);
  loadDialogueSessions(day);
  loadDialogueEntries();
}
async function loadDialogueSessions(day){
  try{
    const data=await UI.request(`/api/memory-system/dialogue/sessions?day=${encodeURIComponent(day)}`);
    const root=$('#dialogue-sessions');
    root.replaceChildren();
    const all=UI.element('button','全部会话');
    all.classList.add('active');
    all.onclick=()=>{dState.session=null;dState.after=0;for(const node of root.children)node.classList.remove('active');all.classList.add('active');loadDialogueEntries()};
    root.append(all);
    for(const session of data.sessions){
      const button=UI.element('button',`#${session.session_id} ${session.title}`);
      button.onclick=()=>{dState.session=session.session_id;dState.after=0;for(const node of root.children)node.classList.remove('active');button.classList.add('active');loadDialogueEntries()};
      root.append(button);
    }
  }catch(error){UI.notify(error.message,'error')}
}
async function loadDialogueEntries(){
  const params=new URLSearchParams({day:dState.day,after:String(dState.after),limit:'60'});
  if(dState.session!=null)params.set('session_id',String(dState.session));
  try{
    const data=await UI.request('/api/memory-system/dialogue/entries?'+params);
    const root=$('#dialogue-entries');
    if(!dState.after)root.replaceChildren();
    for(const entry of data.entries)root.append(entryNode(entry));
    if(!data.entries.length&&!dState.after)root.append(UI.element('p','这一天没有对话记录。','empty'));
    dState.more=data.has_more;
    if(data.entries.length)dState.after=data.entries[data.entries.length-1].id;
    $('#dialogue-more').hidden=!dState.more;
  }catch(error){UI.notify(error.message,'error')}
}
$('#dialogue-more').onclick=()=>loadDialogueEntries();
$('#dialogue-search-form').onsubmit=async event=>{
  event.preventDefault();
  const query=$('#dialogue-search-input').value.trim();
  if(!query)return;
  try{
    const data=await UI.request('/api/memory-system/dialogue/search?q='+encodeURIComponent(query));
    const root=$('#dialogue-search-results');
    root.hidden=false;
    root.replaceChildren();
    if(!data.hits.length){root.append(UI.element('p',`没有找到与「${query}」相关的内容。`,'empty'));return}
    for(const hit of data.hits){
      const item=UI.element('li');
      const button=UI.element('button',`${hit.day} · 会话${hit.session_id}`);
      button.onclick=()=>{switchView('dialogue');selectDialogueDay(hit.day)};
      item.append(button,document.createTextNode(` ${hit.role==='user'?'用户':'助手'}：${hit.content.slice(0,120)}`));
      root.append(item);
    }
  }catch(error){UI.notify(error.message,'error')}
};

// ---------------- 会话全量（M2） ----------------
async function loadSessionDays(){
  try{
    const data=await UI.request('/api/memory-system/session-log/days');
    const root=$('#session-days');
    root.replaceChildren();
    if(!data.days.length){root.append(UI.element('p','还没有会话记录。','empty'));return}
    for(const day of data.days){
      const button=UI.element('button');
      button.append(UI.element('strong',day.day));
      button.append(UI.element('small',`${day.sessions} 会话 · ${day.entries} 条`));
      button.onclick=()=>selectSessionDay(day.day);
      root.append(button);
    }
    selectSessionDay(data.days[0].day);
  }catch(error){UI.notify(error.message,'error')}
}
function selectSessionDay(day){
  sState={day,session:null,run:sState.run,after:0,more:false};
  for(const button of $('#session-days').children)button.classList.toggle('active',button.querySelector('strong').textContent===day);
  loadSessionEntries();
}
$('#session-run-filter').oninput=()=>{
  const value=$('#session-run-filter').value.trim();
  if(value===sState.run)return;
  sState.run=value;sState.after=0;loadSessionEntries();
};
async function loadSessionEntries(){
  const params=new URLSearchParams({day:sState.day,after:String(sState.after),limit:'80'});
  if(sState.session!=null)params.set('session_id',String(sState.session));
  if(sState.run)params.set('run_id',sState.run);
  try{
    const data=await UI.request('/api/memory-system/session-log/entries?'+params);
    const root=$('#session-entries');
    if(!sState.after)root.replaceChildren();
    for(const entry of data.entries)root.append(entryNode(entry));
    if(!data.entries.length&&!sState.after)root.append(UI.element('p','没有匹配的记录（试试清空运行过滤）。','empty'));
    sState.more=data.has_more;
    if(data.entries.length)sState.after=data.entries[data.entries.length-1].id;
    $('#session-more').hidden=!sState.more;
  }catch(error){UI.notify(error.message,'error')}
}
$('#session-more').onclick=()=>loadSessionEntries();
$('#session-search-form').onsubmit=async event=>{
  event.preventDefault();
  const query=$('#session-search-input').value.trim();
  if(!query)return;
  try{
    const data=await UI.request('/api/memory-system/session-log/search?q='+encodeURIComponent(query));
    const root=$('#session-search-results');
    root.hidden=false;
    root.replaceChildren();
    if(!data.hits.length){root.append(UI.element('p',`没有找到与「${query}」相关的内容。`,'empty'));return}
    for(const hit of data.hits){
      const item=UI.element('li');
      const button=UI.element('button',hit.day);
      button.onclick=()=>{switchView('session');selectSessionDay(hit.day)};
      item.append(button,document.createTextNode(hit.kind==='tool'
        ?` 工具 ${hit.tool_name}：${hit.tool_result.slice(0,100)}`
        :` ${hit.content.slice(0,100)}`));
      root.append(item);
    }
  }catch(error){UI.notify(error.message,'error')}
};

function entryNode(entry){
  const node=UI.element('div',null,'entry');
  const head=UI.element('span',null,'entry-head');
  head.textContent=`#${entry.id} ${String(entry.created_at||'').slice(11,19)}`;
  node.append(head);
  if(entry.kind==='tool'){
    node.classList.add('kind-tool');
    node.append(document.createTextNode(` 工具 ${entry.tool_name}（${entry.tool_status||'未知状态'}）：${entry.tool_result||''}`));
    if(entry.tool_args){
      const args=UI.element('div',null,'entry-args');
      args.textContent=`参数：${entry.tool_args.slice(0,400)}`;
      node.append(document.createElement('br'),args);
    }
  }else if(entry.kind==='system'){
    node.classList.add('kind-system');
    node.append(document.createTextNode(' '+entry.content));
  }else{
    if(entry.role==='user')node.classList.add('role-user');
    node.append(document.createTextNode(` ${entry.role==='user'?'用户':'助手'}：${entry.content}`));
  }
  return node;
}

// ---------------- 笔记（M3–M5） ----------------
for(const button of root.querySelectorAll('.kind-tabs button')){
  button.onclick=()=>{
    noteKind=button.dataset.kind;
    for(const node of root.querySelectorAll('.kind-tabs button'))
      node.setAttribute('aria-selected',String(node.dataset.kind===noteKind));
    loadNotes();
  };
}
async function loadNotes(){
  try{
    const data=await UI.request(`/api/memory-system/notes?kind=${noteKind}&limit=200`);
    const root=$('#notes-list');
    root.replaceChildren();
    if(!data.notes.length){
      root.append(UI.element('p',`还没有${KIND_LABEL[noteKind]}笔记（每天结束自动整理）。`,'empty'));
      $('#note-content').textContent='';
      $('#note-title').textContent='选择一篇笔记阅读';
      $('#note-retry').hidden=true;
      return;
    }
    for(const note of data.notes){
      const button=UI.element('button');
      button.append(UI.element('strong',note.period_key));
      button.append(UI.element('small',`${NOTE_STATUS[note.status]||note.status} · ${note.chars} 字 · ${UI.date(note.finished_at)}`));
      button.onclick=()=>selectNote(note.period_key);
      root.append(button);
    }
    if(!data.notes.some(note=>note.period_key===notePeriod))notePeriod=data.notes[0].period_key;
    selectNote(notePeriod);
  }catch(error){UI.notify(error.message,'error')}
}
async function selectNote(period){
  notePeriod=period;
  for(const button of $('#notes-list').children)
    button.classList.toggle('active',button.querySelector('strong').textContent===period);
  try{
    const note=await UI.request(`/api/memory-system/notes/${noteKind}/${encodeURIComponent(period)}`);
    $('#note-title').textContent=`${KIND_LABEL[noteKind]}笔记 · ${note.period_key}`;
    $('#note-meta').textContent=`${NOTE_STATUS[note.status]||note.status}${note.model?` · ${note.model}`:''}${note.finished_at?` · ${UI.date(note.finished_at)}`:''}${note.error?` · ${note.error}`:''}`;
    $('#note-content').textContent=note.content||'（空）';
    $('#note-retry').hidden=note.status==='running';
    const runs=await UI.request(`/api/memory-system/runs?limit=50`);
    renderRunList($('#note-runs'),runs.runs.filter(run=>run.kind===noteKind&&run.period_key===period));
  }catch(error){UI.notify(error.message,'error')}
}
$('#note-retry').onclick=async()=>{
  try{
    const result=await UI.request(`/api/memory-system/notes/${noteKind}/${encodeURIComponent(notePeriod)}/retry`,{method:'POST'});
    UI.notify(result.hint,'info');
    loadNotes();
  }catch(error){UI.notify(error.message,'error')}
};

// ---------------- 人物（M6） ----------------
$('#person-new').onclick=async()=>{
  const name=await UI.ask({title:'新建人物',inputLabel:'名字',confirmLabel:'创建'});
  if(!name)return;
  try{
    await UI.request('/api/memory-system/people',{method:'POST',body:JSON.stringify({name})});
    UI.notify('人物已创建','info');
    loadPeople();
  }catch(error){UI.notify(error.message,'error')}
};
async function loadPeople(){
  try{
    const data=await UI.request('/api/memory-system/people');
    const root=$('#people-list');
    root.replaceChildren();
    if(!data.people.length){root.append(UI.element('p','还没有人物记录。在对话里让小鱼记住某人，或点右上角新建。','empty'));return}
    for(const person of data.people){
      const button=UI.element('button');
      button.dataset.id=person.id;
      button.append(UI.element('strong',person.name));
      button.append(UI.element('small',`${IMPORTANCE_LABEL[person.importance]||person.importance} · ${person.accounts} 账号 · ${person.entries} 条`));
      button.onclick=()=>selectPerson(person.id);
      root.append(button);
    }
    if(!data.people.some(person=>person.id===selectedPerson))selectedPerson=data.people[0].id;
    selectPerson(selectedPerson);
  }catch(error){UI.notify(error.message,'error')}
}
const IMPORTANCE_LABEL={owner:'主人',important:'重要',known:'认识'};
async function selectPerson(personId){
  selectedPerson=personId;
  for(const button of $('#people-list').children)
    button.classList.toggle('active',Number(button.dataset.id)===personId);
  try{
    const person=await UI.request(`/api/memory-system/people/${personId}`);
    for(const button of $('#people-list').children)
      button.classList.toggle('active',Number(button.dataset.id)===person.id);
    renderPerson(person);
  }catch(error){UI.notify(error.message,'error')}
}
function renderPerson(person){
  const root=$('#person-detail');
  root.replaceChildren();

  const head=UI.element('div');
  const title=UI.element('strong',person.name);
  head.append(title,UI.element('span',` #${person.id} · ${IMPORTANCE_LABEL[person.importance]||person.importance}`,'hint'));
  const del=UI.element('button',null,'small');
  del.style.marginLeft='auto';
  del.append(UI.element('span','delete','material-icons'),document.createTextNode('删除人物'));
  del.onclick=async()=>{
    const ok=await UI.ask({title:'删除人物',message:`删除「${person.name}」及其全部账号与记忆条目？此操作不可撤销。`,confirmLabel:'删除',danger:true});
    if(!ok)return;
    try{
      await UI.request(`/api/memory-system/people/${person.id}`,{method:'DELETE'});
      UI.notify('人物已删除','info');
      selectedPerson=null;loadPeople();
    }catch(error){UI.notify(error.message,'error')}
  };
  head.append(del);
  root.append(head);

  // 基本信息
  const info=UI.element('div',null,'entry-group');
  info.style.marginTop='8px';
  const nameInput=UI.element('input');nameInput.value=person.name;nameInput.maxLength=60;
  const noteInput=UI.element('input');noteInput.value=person.note;noteInput.maxLength=2000;noteInput.placeholder='备注';
  const importance=UI.element('select');
  for(const [value,label] of Object.entries(IMPORTANCE_LABEL)){
    const option=UI.element('option',label);option.value=value;importance.append(option);
  }
  importance.value=person.importance;
  const save=UI.element('button',null,'small');
  save.append(UI.element('span','save','material-icons'),document.createTextNode('保存'));
  save.onclick=async()=>{
    try{
      await UI.request(`/api/memory-system/people/${person.id}`,{method:'PUT',body:JSON.stringify({name:nameInput.value,importance:importance.value,note:noteInput.value})});
      UI.notify('人物已更新','info');loadPeople();
    }catch(error){UI.notify(error.message,'error')}
  };
  const rows=UI.element('div',null,'field-grid');
  const nameField=UI.element('label','名字');nameField.append(nameInput);
  const impField=UI.element('label','重要度');impField.append(importance);
  const noteField=UI.element('label','备注');noteField.append(noteInput);
  rows.append(nameField,impField,noteField);
  info.append(rows,save);
  root.append(info);

  // 账号
  const accountsPanel=UI.element('div');
  accountsPanel.append(UI.element('h3','账号（私聊注入的钥匙）'));
  const chips=UI.element('div',null,'person-accounts');
  for(const account of person.accounts){
    const chip=UI.element('span',null,'person-account');
    chip.append(document.createTextNode(`${account.channel}:${account.value}${account.label?`（${account.label}）`:''}`));
    const remove=UI.element('button');remove.title='解除账号';
    remove.append(UI.element('span','close','material-icons'));
    remove.onclick=async()=>{
      try{
        await UI.request(`/api/memory-system/people/${person.id}/accounts/${account.id}`,{method:'DELETE'});
        selectPerson(person.id);
      }catch(error){UI.notify(error.message,'error')}
    };
    chip.append(remove);
    chips.append(chip);
  }
  if(!person.accounts.length)chips.append(UI.element('span','（未登记账号）','hint'));
  const channel=UI.element('select');
  for(const value of ['qq','bilibili'])channel.append(UI.element('option',value));
  const value=UI.element('input');value.placeholder='账号（数字）';value.maxLength=20;
  const link=UI.element('button',null,'small');
  link.append(UI.element('span','add_link','material-icons'),document.createTextNode('登记账号'));
  link.onclick=async()=>{
    try{
      await UI.request(`/api/memory-system/people/${person.id}/accounts`,{method:'POST',body:JSON.stringify({channel:channel.value,value:value.value.trim()})});
      selectPerson(person.id);
    }catch(error){UI.notify(error.message,'error')}
  };
  const linkRow=UI.element('div',null,'session-chips');
  linkRow.append(channel,value,link);
  accountsPanel.append(chips,linkRow);
  root.append(accountsPanel);

  // 三类条目
  const groups=UI.element('div',null,'entry-groups');
  for(const kind of ['archive','resident','event']){
    const group=UI.element('div',null,'entry-group');
    group.append(UI.element('h3',ENTRY_GROUP_LABEL[kind]));
    const items=person.entries.filter(entry=>entry.entry_kind===kind);
    for(const entry of items){
      const item=UI.element('div',null,'entry-item');
      item.append(UI.element('span',(entry.content)+(entry.expires_at?`（${String(entry.expires_at).slice(0,16)} 过期）`:'')));
      const edit=UI.element('button',null,'mini');edit.title='编辑';
      edit.append(UI.element('span','edit','material-icons'));
      edit.onclick=async()=>{
        const next=await UI.ask({title:'编辑条目',inputLabel:'内容',input:entry.content,confirmLabel:'保存'});
        if(next==null)return;
        try{
          await UI.request(`/api/memory-system/people/${person.id}/entries/${entry.id}`,{method:'PUT',body:JSON.stringify({entry_kind:kind,content:next})});
          selectPerson(person.id);
        }catch(error){UI.notify(error.message,'error')}
      };
      const remove=UI.element('button',null,'mini');remove.title='删除';
      remove.append(UI.element('span','delete','material-icons'));
      remove.onclick=async()=>{
        try{
          await UI.request(`/api/memory-system/people/${person.id}/entries/${entry.id}`,{method:'DELETE'});
          selectPerson(person.id);
        }catch(error){UI.notify(error.message,'error')}
      };
      item.append(edit,remove);
      group.append(item);
    }
    if(!items.length)group.append(UI.element('p','（还没有这类条目）','hint'));
    const content=UI.element('input');content.placeholder='一句话（自包含）';content.maxLength=kind==='archive'?2000:1200;
    const add=UI.element('button',null,'small');
    add.append(UI.element('span','add','material-icons'),document.createTextNode('添加'));
    add.onclick=async()=>{
      const body={entry_kind:kind,content:content.value.trim()};
      if(kind==='event'){
        const expires=await UI.ask({title:'过期时间（可选）',message:'ISO 时间（如 2026-11-05T09:00:00+08:00）；取消则长期保留',confirmLabel:'长期保留'});
        if(expires)body.expires_at=expires;
      }
      try{
        await UI.request(`/api/memory-system/people/${person.id}/entries`,{method:'POST',body:JSON.stringify(body)});
        selectPerson(person.id);
      }catch(error){UI.notify(error.message,'error')}
    };
    const addRow=UI.element('div',null,'session-chips');
    addRow.append(content,add);
    group.append(addRow);
    groups.append(group);
  }
  root.append(groups);
}

// ---------------- 整理设置 ----------------
async function loadSettings(){
  try{
    const cfg=await UI.request('/api/system/config');
    $('#cfg-journal-enabled').checked=cfg.journal.enabled;
    $('#cfg-journal-daily-time').value=cfg.journal.daily_time;
    $('#cfg-journal-steps').value=cfg.journal.steps;
    $('#cfg-journal-max-catchup').value=cfg.journal.max_catchup;
    $('#cfg-journal-max-input-chars').value=cfg.journal.max_input_chars;
    $('#cfg-journal-max-note-chars').value=cfg.journal.max_note_chars;
    $('#cfg-journal-daily-prompt').value=cfg.journal.daily_prompt;
    $('#cfg-journal-weekly-prompt').value=cfg.journal.weekly_prompt;
    $('#cfg-journal-monthly-prompt').value=cfg.journal.monthly_prompt;
    $('#cfg-transcript-max-entry').value=cfg.transcript.max_entry_chars;
    $('#cfg-transcript-max-args').value=cfg.transcript.max_tool_args_chars;
    $('#cfg-transcript-max-result').value=cfg.transcript.max_tool_result_chars;
    $('#cfg-transcript-retention').value=cfg.transcript.retention_days;
    $('#cfg-transcript-dialogue-retention').value=cfg.transcript.dialogue_retention_days;
    $('#cfg-people-card-chars').value=cfg.people.person_card_chars;
    $('#cfg-people-archive').value=cfg.people.archive_max_chars;
    $('#cfg-people-resident').value=cfg.people.resident_max_chars;
    $('#cfg-people-event').value=cfg.people.event_max_chars;
  }catch(error){UI.notify(error.message,'error')}
}
$('#journal-form').onsubmit=async event=>{
  event.preventDefault();
  const number=id=>Number($('#'+id).value);
  // 多行块（压缩规则）按行数组提交——与设置中心人设的保存口径一致；
  // 尾部空行要去掉，否则每存一次 YAML 多行块就多一行空白
  const promptLines=id=>{
    const lines=$('#'+id).value.replace(/\r\n/g,'\n').split('\n');
    while(lines.length>1&&!lines[lines.length-1].trim())lines.pop();
    return lines;
  };
  const updates={
    journal:{
      enabled:$('#cfg-journal-enabled').checked,
      daily_time:$('#cfg-journal-daily-time').value.trim(),
      steps:number('cfg-journal-steps'),
      max_catchup:number('cfg-journal-max-catchup'),
      max_input_chars:number('cfg-journal-max-input-chars'),
      max_note_chars:number('cfg-journal-max-note-chars'),
      daily_prompt:promptLines('cfg-journal-daily-prompt'),
      weekly_prompt:promptLines('cfg-journal-weekly-prompt'),
      monthly_prompt:promptLines('cfg-journal-monthly-prompt'),
    },
    transcript:{
      max_entry_chars:number('cfg-transcript-max-entry'),
      max_tool_args_chars:number('cfg-transcript-max-args'),
      max_tool_result_chars:number('cfg-transcript-max-result'),
      retention_days:number('cfg-transcript-retention'),
      dialogue_retention_days:number('cfg-transcript-dialogue-retention'),
    },
    people:{
      person_card_chars:number('cfg-people-card-chars'),
      archive_max_chars:number('cfg-people-archive'),
      resident_max_chars:number('cfg-people-resident'),
      event_max_chars:number('cfg-people-event'),
    },
  };
  try{
    const result=await UI.request('/api/system/config',{method:'POST',body:JSON.stringify({updates})});
    $('#settings-msg').textContent=result.hint;
    UI.notify('记忆系统设置已保存','info');
  }catch(error){$('#settings-msg').textContent='';UI.notify(error.message,'error')}
};

// ---------------- 启动 ----------------
loadOverview();

return {refresh(){if(view==='overview')loadOverview()}};
}

// 同一份界面和控制器供设置中心与旧书签页面使用；只在用户进入分区时加载。
const mounts=new WeakMap();
export async function loadMemory(root){
  if(mounts.has(root)){const controller=await mounts.get(root);controller?.refresh();return controller}
  const request=Promise.resolve().then(async()=>{
    root.setAttribute('aria-busy','true');
    root.replaceChildren(window.OmniUI.element('p','正在读取记忆系统…','empty'));
    try{
      const response=await fetch('/memory/panel.html?v=20261031m4',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});
      if(!response.ok)throw Error('记忆界面资源读取失败（'+response.status+'），请重试。');
      const template=document.createElement('template');template.innerHTML=await response.text();
      if(!template.content.querySelector('.memory-surface .view-tabs'))throw Error('记忆界面资源不完整，请刷新工作台后重试。');
      root.replaceChildren(template.content);
      return mountMemory(root);
    }catch(error){
      mounts.delete(root);
      const message=window.OmniUI.element('p','读取失败：'+error.message,'hint');message.setAttribute('role','alert');
      const retry=window.OmniUI.element('button','重新读取记忆系统','btn');retry.type='button';retry.onclick=()=>loadMemory(root);
      root.replaceChildren(message,retry);return null;
    }finally{root.setAttribute('aria-busy','false')}
  });
  mounts.set(root,request);return request;
}
