// 上下文设置：只读统计与配置分开加载；保存只影响下次启动，不触发模型调用。
import {$,esc} from './dom.js?v=20261031m13';
import {api} from './api.js?v=20261031m13';
import {S} from './state.js?v=20261031m13';

let revision=null,dirty=false,saving=false,settingsLoading=false,statsVersion=0,settingsVersion=0;
const number=value=>value==null?'未上报':Number(value).toLocaleString('zh-CN');
const fields={history:'limits.max_history_messages',steps:'limits.max_agent_steps',
  enabled:'compression.enabled',trigger:'compression.trigger_chars',recent:'compression.keep_recent',minimum:'compression.min_messages',
  resident:'limits.resident_memory_chars',daily:'memory.max_writes_per_day',fact:'memory.max_fact_chars',
  recall:'memory.recall_prefix',queue:'review.queue_depth',newmessages:'review.min_messages'};
const labels={'limits.max_history_messages':'历史窗口','limits.max_agent_steps':'工具步数',
  'compression.enabled':'自动压缩','compression.trigger_chars':'压缩触发预算',
  'compression.keep_recent':'保留消息数','compression.min_messages':'最低消息数',
  'limits.resident_memory_chars':'常驻笔记限额','memory.max_writes_per_day':'每日记忆写入限额',
  'memory.max_fact_chars':'单条事实限额','memory.recall_prefix':'检索数据标识',
  'review.queue_depth':'整理队列容量','review.min_messages':'整理所需新消息'};

function setMessage(text,tone=''){
  const node=$('#context-save-msg');node.textContent=text;node.className='save-msg'+(tone?' '+tone:'');
}
function contextError(error){
  if(error.status===404&&['Not Found','未找到内容或接口，请确认服务已更新'].includes(error.message)){
    return '当前服务未提供上下文接口。请重启工作台服务，再刷新页面重试。';
  }
  return error.message||'请求失败，请重试。';
}
function renderStatsError(message){
  $('#context-session').textContent='当前会话统计暂不可用';
  $('#context-metrics').innerHTML='<div class="empty-rich" role="alert"><strong>会话统计读取失败</strong><p>'+esc(message)+'</p><button class="btn" data-action="context:refresh">重试统计</button></div>';
  $('#context-breakdown').replaceChildren();$('#context-estimate-note').textContent='';
  $('#context-cache').innerHTML='<div class="empty-rich"><strong>缓存统计暂不可用</strong><p>尚未取得缓存统计，重试后更新。本页不会调用模型。</p></div>';
  $('#context-prefix').textContent='固定前缀信息暂不可用。';
  $('#context-system-prompt').textContent='当前运行提示词暂不可用。';
}
function lock(){
  $('#context-controls').disabled=saving||settingsLoading||revision==null;
  $('#context-save').disabled=saving||settingsLoading||revision==null;
  $('#context-reload-settings').disabled=saving||settingsLoading;
}
function thresholdHint(){
  const chars=Number($('#cfg-context-trigger').value);
  $('#context-trigger-note').textContent=Number.isFinite(chars)&&chars>0?'约 '+number(Math.max(1,Math.floor(chars/2)))+' tokens；预算越低，越早整理旧对话。':'填写压缩触发预算。';
}
function renderSettings(data,fill){
  revision=data.revision;
  if(fill){
    for(const [id,key]of Object.entries(fields)){
      const [section,name]=key.split('.'),node=$('#cfg-context-'+id),value=data.saved[section][name];
      if(node.type==='checkbox')node.checked=value;else node.value=value;
    }
    dirty=false;thresholdHint();
  }
  $('#context-pending').textContent=data.restart_required?'已保存的配置与当前运行不同，重启相应入口后生效。':'已保存配置与当前运行一致。';
  $('#context-running-settings').replaceChildren();
  for(const key of Object.values(fields)){
    const [section,name]=key.split('.'),running=data.running[section][name],saved=data.saved[section][name];
    const item=document.createElement('div');item.className='context-setting-row';
    const fmt=value=>typeof value==='boolean'?(value?'开启':'关闭'):number(value);
    item.innerHTML=`<span>${esc(labels[key])}</span><span>运行 ${esc(fmt(running))}</span><span>保存 ${esc(fmt(saved))}</span>`;
    $('#context-running-settings').append(item);
  }
  lock();
}
async function loadSettings({force=false}={}){
  if(saving||settingsLoading||(!force&&dirty))return;
  if(dirty&&!await OmniUI.ask({title:'重新读取上下文配置',message:'放弃这页尚未保存的修改并读取配置文件？',confirmLabel:'重新读取'}))return;
  const version=++settingsVersion;
  settingsLoading=true;lock();
  setMessage('正在读取配置…');
  try{
    const data=await api('/api/context/settings');if(version!==settingsVersion)return;
    renderSettings(data,true);setMessage('配置已读取；保存后需重启相应入口。');
  }catch(error){if(version===settingsVersion){const message=contextError(error);setMessage('读取失败：'+message,'err');OmniUI.notify(message,'error')}}
  finally{if(version===settingsVersion){settingsLoading=false;lock()}}
}
function metric(label,value,note){return `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`}
function renderOverview(data){
  const context=data.context,cache=data.cache;
  $('#context-session').textContent='会话 #'+data.session_id+' · '+data.model;
  $('#context-metrics').innerHTML=metric('窗口估算',number(context.used_tokens)+' tokens',context.max_tokens?'声明上限 '+number(context.max_tokens)+' tokens':'模型未声明上下文上限')+
    metric('保留历史',number(context.history_count)+' 条','会话共有 '+number(context.message_count)+' 条消息')+
    metric('回合快照',number(context.snapshot_count)+' 份',number(context.legacy_user_count)+' 条旧用户消息没有快照')+
    metric('持久摘要',context.summary?'第 '+context.summary.version+' 版':'尚未生成',context.summary?'覆盖消息 #'+context.summary.from_msg_id+'–#'+context.summary.to_msg_id:'历史尚未形成有效摘要');
  $('#context-breakdown').innerHTML=context.segments.map(segment=>{
    const percent=context.used_tokens?Math.min(100,100*segment.tokens/context.used_tokens):0;
    return `<div class="context-segment"><span>${esc(segment.label)}</span><meter min="0" max="100" value="${percent}">${percent.toFixed(1)}%</meter><b>${number(segment.tokens)}</b></div>`;
  }).join('');
  $('#context-estimate-note').textContent=context.view_note;
  $('#context-system-prompt').textContent=context.system_prompt;
  $('#context-prefix').textContent='固定提示 '+context.stable_prefix.system_chars+' 字符 · '+context.stable_prefix.tool_count+' 个工具\nSystem SHA-256: '+context.stable_prefix.system_sha256+'\nTools SHA-256: '+context.stable_prefix.tools_sha256;
  if(!cache.sample_count){
    $('#context-cache').innerHTML='<div class="empty-rich"><span class="material-icons">insights</span><strong>还没有模型调用样本</strong><p>正常聊天后在这里查看；本页刷新不会请求模型。</p></div>';
  }else{
    const ratio=cache.hit_percent==null?'未知':cache.hit_percent.toFixed(1)+'%';
    $('#context-cache').innerHTML='<div class="context-metrics">'+metric('缓存命中率',ratio,'基于 '+cache.ratio_sample_count+' 条完整上报')+
      metric('读取缓存',number(cache.cached_tokens)+' tokens','未上报保持未知')+
      metric('写入缓存',number(cache.creation_tokens)+' tokens','采用供应商上报')+'</div>'+
      `<p class="hint">最近 ${cache.sample_count} 条样本中，${cache.reported_count} 条上报缓存读取。${esc(cache.note)}${cache.has_more?' 此处仅展示最近 '+cache.limit+' 条。':''}</p>`;
  }
}
async function loadStats(){
  const version=++statsVersion,session=S.currentSession;
  $('#context-refresh').disabled=true;$('#context-stats-status').textContent='正在读取会话统计…';
  try{
    const data=await api('/api/context'+(session==null?'':'?session_id='+encodeURIComponent(session)));
    if(version!==statsVersion)return;
    if(session!==S.currentSession){loadStats();return}
    renderOverview(data);$('#context-stats-status').textContent='已更新 · '+new Date().toLocaleTimeString('zh-CN');
  }catch(error){if(version===statsVersion){if(session!==S.currentSession){loadStats();return}const message=contextError(error);renderStatsError(message);$('#context-stats-status').textContent='统计读取失败：'+message;OmniUI.notify(message,'error')}}
  finally{if(version===statsVersion)$('#context-refresh').disabled=false}
}
export function loadContext(){loadStats();loadSettings()}

async function save(){
  if(saving||revision==null||!$('#context-form').reportValidity())return;
  const updates={limits:{},compression:{},memory:{},review:{}};
  for(const [id,key]of Object.entries(fields)){
    const [section,name]=key.split('.'),node=$('#cfg-context-'+id);
    updates[section][name]=node.type==='checkbox'?node.checked:Number(node.value);
  }
  saving=true;lock();setMessage('正在保存上下文设置…');
  try{
    const data=await api('/api/context/settings',{method:'PUT',body:JSON.stringify({revision,updates})});
    renderSettings(data,true);setMessage('已保存；重启相应入口后使用新设置。','ok');OmniUI.notify('上下文设置已保存，重启相应入口后生效');
  }catch(error){setMessage('保存失败：'+error.message,'err');OmniUI.notify(error.message,'error')}
  finally{saving=false;lock()}
}
$('#context-form').addEventListener('submit',event=>{event.preventDefault();save()});
$('#context-form').addEventListener('input',()=>{dirty=true;thresholdHint();setMessage('有尚未保存的修改。')});
export const actions={
  'context:refresh':()=>loadStats(),
  'context:reload-settings':()=>loadSettings({force:true}),
};
