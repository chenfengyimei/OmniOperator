// 设置中心：全屏沉浸式骨架（开合/Esc/分组导航/图标）+ 表单回填 + 每页独立保存。
// 每页独立保存的原因：以前只有一个"保存全部设置"，任一页填错都会让整次提交 400，
// 用户看到的是"保存失败"却不知道是哪一页的问题。
import {$,esc} from './dom.js?v=20261031m15';
import {api} from './api.js?v=20261031m15';
import {loadState} from './header.js?v=20261031m15';
import {loadEnvKeys,renderModelSlots,getModelSlotValue} from './hub-models.js?v=20261031m15';
import {renderPersonaPresets} from './hub-persona.js?v=20261031m15';
import {loadCron} from './hub-cron.js?v=20261031m15';
import {loadUsage} from './hub-usage.js?v=20261031m15';
import {loadMcp} from './hub-mcp.js?v=20261031m15';
import {loadContext} from './hub-context.js?v=20261031m15';
import {loadMemoryHub} from './hub-memory.js?v=20261031m15';
import {loadSkillsHub} from './hub-skills.js?v=20261031m15';

export async function openHub(go=null){
  $('#hub').classList.remove('hidden');$('#hub').setAttribute('aria-hidden','false');
  try{await loadHubConfig();loadCookies();loadCron();loadUsage();loadMcp()}catch(e){$('#hub-msg').textContent=e.message}
  const firstCur=document.querySelector('.hub-nav a.active');
  switchHub(go||(firstCur?firstCur.dataset.go:'sec-persona'));
};

function closeHub(){
  $('#hub').classList.add('hidden');
  $('#hub').setAttribute('aria-hidden','true');
  document.querySelector('.hub-open')?.focus?.();
}
/* 内嵌分区：首次进入才赋 src（懒加载）——一次性拉起三个 iframe 会同时建起
   WebSocket 长连与轮询，开页就把后台资源全占上了。 */
const EMBED_PAGES=['sec-terminal','sec-android','sec-storage'];
function mountEmbed(go){
  if(!EMBED_PAGES.includes(go))return;
  const frame=document.getElementById(go)?.querySelector('iframe[data-embed-page]');
  if(frame&&!frame.getAttribute('src'))frame.src=frame.dataset.src;
}
/* iframe 高度自适应：子页用 postMessage 上报内容高度，宿主据此写 iframe 高度。
   消息必须校验 origin（只收同源）且带本协议标记，避免被别的 frame 打扰。
   收不到消息时用 CSS 固定高度兜底（.embed-shell）。 */
window.addEventListener('message',event=>{
  if(event.origin!==location.origin)return;
  const data=event.data;
  if(!data||data.omni!=='embed-height')return;
  const frame=document.querySelector('iframe[data-embed-page="'+data.id+'"]');
  if(!frame)return;
  const min=Number(frame.dataset.minHeight||360);
  const max=Number(frame.dataset.maxHeight||1600);
  const h=Math.min(max,Math.max(min,Number(data.h)||min));
  frame.style.height=h+'px';
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]')&&!$('#hub').classList.contains('hidden'))closeHub()});
export function switchHub(go){
  document.querySelectorAll('.hub-nav a').forEach(x=>{
    const on=x.dataset.go===go;x.classList.toggle('active',on);
    if(on)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');
  });
  document.querySelectorAll('.hub-card').forEach(c=>c.classList.add('hidden'));
  const target=document.querySelector(`.hub-nav a[data-go="${go}"]`);
  const el=$('#'+go);
  if(el){
    el.classList.remove('hidden');
    if(go==='sec-cron')loadCron();
    if(go==='sec-usage')loadUsage();
    if(go==='sec-context')loadContext();
    if(go==='sec-memory-system')loadMemoryHub();
    if(go==='sec-skills')loadSkillsHub();
    if(go==='sec-mcp')loadMcp();
    mountEmbed(go);
    el.scrollIntoView({behavior:'smooth'});
    if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      el.animate([{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'cubic-bezier(.2,.8,.2,1)'});
    }
  }
}
// 只拦截「分区跳转」锚点（带 data-go）；不带 data-go 的是独立页直链（如 /storage/），
// 走浏览器默认行为新标签打开——此前全拦导致 /storage/ 独立页从工作台永远打不开。
document.querySelectorAll('.hub-nav a[data-go]').forEach(a=>a.onclick=e=>{e.preventDefault();switchHub(a.dataset.go)});
// 侧栏分区搜索：20 个分区在 768px 高的屏上要滚，输入即筛更省事。
// 组标题在组内无可见条目时一并隐藏——否则会出现一串空标题，看着像坏了。
const navSearch=document.querySelector('#hub-nav-search');
if(navSearch){
  const navLinks=[...document.querySelectorAll('.hub-nav a[data-go]')];
  const allLinks=[...document.querySelectorAll('.hub-nav a')];
  const navGroups=[...document.querySelectorAll('.hub-nav .hub-group')];
  const empty=document.createElement('div');
  empty.className='hub-nav-empty';empty.textContent='没有匹配的分区';empty.hidden=true;
  document.querySelector('.hub-nav').append(empty);
  navSearch.addEventListener('input',()=>{
    const q=navSearch.value.trim().toLowerCase();
    let visible=0;
    for(const link of allLinks){
      const hit=!q||link.textContent.toLowerCase().includes(q)||(link.dataset.go||'').toLowerCase().includes(q);
      link.hidden=!hit;
      // 独立页直链（无 data-go）不计入分区数——它是快捷方式，不是分区
      if(hit&&link.hasAttribute('data-go'))visible++;
    }
    for(const group of navGroups){
      let next=group.nextElementSibling,has=false;
      while(next&&next.tagName!=='DIV'){
        if(next.matches('a[data-go]')&&!next.hidden)has=true;
        next=next.nextElementSibling;
      }
      group.hidden=!has;
    }
    empty.hidden=visible>0;
  });
}
// 左侧导航图标：纯装饰，拿不到图标就跳过（不影响点击）
(function(){
  const icons={
    'sec-context':'<path d="M4 6h16M4 12h16M4 18h16"/>',
    'sec-persona':'<circle cx="12" cy="8.2" r="3.4"/><path d="M5 19.6a7 7 0 0 1 14 0"/>',
    'sec-models':'<rect x="7.2" y="7.2" width="9.6" height="9.6" rx="2.2"/><path d="M10.2 3.6v3.2M13.8 3.6v3.2M10.2 17.2v3.2M13.8 17.2v3.2M3.6 10.2h3.2M3.6 13.8h3.2M17.2 10.2h3.2M17.2 13.8h3.2"/>',
    'sec-memory':'<ellipse cx="12" cy="6.2" rx="7" ry="3"/><path d="M5 6.2v5.8c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9V6.2M5 12v5.6c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9V12"/>',
    'sec-skills':'<path d="M12 3.4l2.5 5.1 5.6.8-4 4 .9 5.6-5-2.7-5 2.7.9-5.6-4-4 5.6-.8z"/>',
    'sec-media':'<circle cx="12" cy="12" r="8.4"/><path d="M10.6 9.2l4 2.8-4 2.8V9.2z"/>',
    'sec-browser':'<circle cx="12" cy="12" r="8.4"/><path d="M3.6 12h16.8M12 3.6c2.3 2.3 3.5 5.3 3.5 8.4s-1.2 6.1-3.5 8.4C9.7 18.1 8.5 15.1 8.5 12S9.7 5.7 12 3.6z"/>',
    'sec-qq':'<path d="M20.4 12c0 3.7-3.7 6.7-8.4 6.7-1 0-1.9-.1-2.8-.3l-3.6 1.4 1.1-3.1C4.6 15.6 3.6 13.9 3.6 12c0-3.7 3.7-6.7 8.4-6.7S20.4 8.3 20.4 12z"/>',
    'sec-cron':'<circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12h3.3"/>',
    'sec-storage':'<path d="M3.8 7.4h6l1.8 2h8.6v9.2H3.8z"/><path d="M3.8 7.4V5.6h6l1.8 1.8"/>',
    'sec-memory-system':'<ellipse cx="12" cy="6.2" rx="7" ry="3"/><path d="M5 6.2v5.8c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9V6.2"/><path d="M5 12v5.6c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9V12"/><path d="M12 9.4v2.2M10.9 10.5h2.2"/>',
    'sec-usage':'<path d="M5.4 19.2v-7.6M10.2 19.2V5.8M15 19.2v-5.2M19.8 19.2v-8.4"/>',
    'sec-mcp':'<circle cx="12" cy="5.6" r="2.2"/><circle cx="6.2" cy="18" r="2.2"/><circle cx="17.8" cy="18" r="2.2"/><path d="M12 7.8v2.6M10.3 12.4l-2.3 3.9M13.7 12.4l2.3 3.9"/>',
    'sec-about':'<circle cx="12" cy="12" r="8.4"/><path d="M12 11.2v5.2"/><circle cx="12" cy="8.2" r="1.05" fill="currentColor" stroke="none"/>'+'',
    // 工具与设备组：终端/安卓/任务/MCP 服务/能力诊断/项目索引
    'sec-terminal':'<rect x="3.4" y="4.6" width="17.2" height="14.8" rx="2.2"/><path d="M7.4 9.6l2.8 2.8-2.8 2.8M12.8 15.4h4.2"/>',
    'sec-android':'<rect x="6.6" y="2.8" width="10.8" height="18.4" rx="2.4"/><path d="M10.2 18.4h3.6"/><path d="M9.4 6.2h5.2"/>',
    'sec-jobs':'<path d="M3.6 7h10.6M3.6 12h10.6M3.6 17h10.6"/><circle cx="18.4" cy="7" r="1.7"/><circle cx="18.4" cy="12" r="1.7"/><circle cx="18.4" cy="17" r="1.7"/>',
    'sec-mcp-services':'<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.8M12 18.4v2.8M2.8 12h2.8M18.4 12h2.8M5.5 5.5l1.9 1.9M16.6 16.6l1.9 1.9M18.5 5.5l-1.9 1.9M7.4 16.6l-1.9 1.9"/>',
    'sec-capabilities':'<path d="M4 15.6l4.6-4.6 3 3L20 5.6"/><path d="M15.4 5.6H20v4.6"/>',
    'sec-project':'<rect x="3.6" y="4.4" width="7" height="7" rx="1.4"/><rect x="13.4" y="4.4" width="7" height="7" rx="1.4"/><rect x="3.6" y="14.4" width="7" height="7" rx="1.4"/><rect x="13.4" y="14.4" width="7" height="7" rx="1.4"/>',
  };
  document.querySelectorAll('.hub-nav a').forEach(function(a){
    const id=a.dataset.go, body=icons[id];
    if(!body)return;
    a.insertAdjacentHTML('afterbegin',
      '<svg class="ni" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+body+'</svg>');
  });
})();

// ---------- QQ 主动提醒的「今天先别烦我」开关 ----------
$('#qq-proactive-stop').onclick=async()=>{
  try{const r=await api('/api/system/qq-policy/stop',{method:'POST'});
    $('#cfg-qq-proactive').checked=false;$('#qq-policy-status').textContent=r.hint;
  }catch(e){$('#qq-policy-status').textContent=e.message;}
};

async function loadHubConfig(){
  const d=await api('/api/system/config');
  $('#cfg-persona-name').value=d.persona.name;
  $('#cfg-persona-prompt').value=d.persona.system_prompt;
  await loadEnvKeys();  // 先拿密钥状态：模型卡片才知道该显示"已配 / 还没填"
  renderModelSlots(d.models);
  // 能力档位（D28）：默认档位与是否允许本回合升档
  if(d.mode_routing){
    $('#cfg-mode-default').value=d.mode_routing.default_mode||'auto';
    $('#cfg-mode-escalation').checked=d.mode_routing.allow_escalation!==false;
  }
  try{renderPersonaPresets(d.persona.name)}catch(e){console.warn('人设预设渲染失败',e)}
  $('#cfg-review-enabled').checked=d.review.enabled;
  $('#cfg-review-max-messages').value=d.review.max_messages;
  $('#cfg-review-steps').value=d.review.steps;
  $('#cfg-approval-mode').value=d.approval.mode;
  $('#cfg-asr-model').value=d.asr.model;
  $('#cfg-asr-device').value=d.asr.device;
  $('#cfg-asr-compute-type').value=d.asr.compute_type;
  $('#cfg-qq-enabled').checked=d.qq.enabled;
  $('#cfg-qq-ws-url').value=d.qq.ws_url;
  $('#cfg-qq-users').value=d.qq.allowed_users.join(', ');
  $('#cfg-qq-groups').value=d.qq.allowed_groups.join(', ');
  $('#cfg-qq-delay').value=d.qq.reply_delay_s.join(', ');
  $('#cfg-qq-reply').checked=d.qq.group_reply_to_bot;
  $('#cfg-qq-proactive').checked=d.qq.proactive_enabled;
  $('#cfg-qq-owner').value=d.qq.owner_qq;
  $('#cfg-qq-limit').value=d.qq.proactive_daily_limit;
  $('#cfg-qq-quiet-start').value=d.qq.quiet_start;
  $('#cfg-qq-quiet-end').value=d.qq.quiet_end;
  $('#cfg-qq-timezone').value=d.qq.timezone;
  try{const p=await api('/api/system/qq-policy');$('#qq-policy-status').textContent=p.reason+'；今日尝试 '+(p.used??'—')+'/'+p.limit;}catch(e){$('#qq-policy-status').textContent=e.message;}
  $('#cfg-browser-headless').checked=d.browser.headless;
  $('#cfg-browser-slowmo').value=d.browser.slow_mo_ms;
  $('#cfg-browser-budget').value=d.browser.max_writes_per_day;
  $('#cfg-browser-approval-timeout').value=d.browser.approval_timeout_s;
}
async function loadCookies(){
  try{
    const d=await api('/api/system/bili-cookies');
    // 用户只关心「能不能用」和「缺哪一项」，不要一上来就摆三个英文键名
    $('#bili-cookie-status').innerHTML=d.configured
      ?'<span style="color:#34d399">✅ 凭证已就绪，可以搬视频了</span>'
      :'<span style="color:#fbbf24">还没给凭证——现在只能看公开内容，字幕和音频拿不到</span>'+
       (d.keys.SESSDATA?'':'<br><span style="color:var(--dim)">缺少 SESSDATA（必需）</span>');
  }catch(e){$('#bili-cookie-status').textContent=e.message}
}
async function saveBiliCookies() {
  try{
    const d=await api('/api/system/bili-cookies',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sessdata:$('#cfg-bili-sessdata').value,bili_jct:$('#cfg-bili-jct').value,buvid3:$('#cfg-bili-buvid3').value})});
    $('#bili-cookie-status').textContent='✅ 已保存，正在生效';
    loadCookies();
  }catch(e){OmniUI.notify(e.message,'error')}
}

// 每页独立保存：只提交本页涉及的配置段。
const SAVE_SCOPES={
  persona:['persona'],
  models:['models','mode_routing'],
  memory:['review','approval'],
  media:['asr'],
  browser:['browser'],
  qq:['qq'],
};
function collectHubUpdates(){
  // 提示词按行提交前必须去掉尾部空行：textarea 的值通常以换行结尾，
  // split('\n') 会多出一个空串，写进 YAML 多行块就是一行空白——
  // 每存一次就多一行，越存越长。
  const promptLines=$('#cfg-persona-prompt').value.replace(/\r\n/g,'\n').split('\n');
  while(promptLines.length>1&&!promptLines[promptLines.length-1].trim())promptLines.pop();
  return {
    persona:{name:$('#cfg-persona-name').value.trim(),
             system_prompt:promptLines},
    models:{chat:getModelSlotValue('chat'),vision:getModelSlotValue('vision'),
            review:getModelSlotValue('review'),
            // 能力档位（D28）：各档模型与路由判定模型，留空即跟随日常对话
            router:getModelSlotValue('router'),
            chat_mode:getModelSlotValue('chat_mode'),
            retrieval_mode:getModelSlotValue('retrieval_mode'),
            agent_mode:getModelSlotValue('agent_mode')},
    mode_routing:{default_mode:$('#cfg-mode-default').value,
                  allow_escalation:$('#cfg-mode-escalation').checked},
    review:{enabled:$('#cfg-review-enabled').checked,
            max_messages:parseInt($('#cfg-review-max-messages').value)||12,
            steps:parseInt($('#cfg-review-steps').value)||4},
    approval:{mode:$('#cfg-approval-mode').value},
    asr:{model:$('#cfg-asr-model').value,device:$('#cfg-asr-device').value,
         compute_type:$('#cfg-asr-compute-type').value},
    qq:{enabled:$('#cfg-qq-enabled').checked,ws_url:$('#cfg-qq-ws-url').value.trim(),
        access_token:$('#cfg-qq-token').value,
        allowed_users:$('#cfg-qq-users').value.split(/[,，\s]+/).map(x=>parseInt(x)).filter(Number.isInteger),
        allowed_groups:$('#cfg-qq-groups').value.split(/[,，\s]+/).map(x=>parseInt(x)).filter(Number.isInteger),
        reply_delay_s:$('#cfg-qq-delay').value.split(/[,，\s]+/).map(parseFloat).filter(x=>!isNaN(x)),
        group_reply_to_bot:$('#cfg-qq-reply').checked,proactive_enabled:$('#cfg-qq-proactive').checked,
        owner_qq:Number($('#cfg-qq-owner').value),proactive_daily_limit:Number($('#cfg-qq-limit').value),
        quiet_start:$('#cfg-qq-quiet-start').value,quiet_end:$('#cfg-qq-quiet-end').value,
        timezone:$('#cfg-qq-timezone').value.trim()||'Asia/Shanghai'},
    browser:{headless:$('#cfg-browser-headless').checked,
             slow_mo_ms:parseInt($('#cfg-browser-slowmo').value)||120,
             max_writes_per_day:parseInt($('#cfg-browser-budget').value)||50,
             approval_timeout_s:parseFloat($('#cfg-browser-approval-timeout').value)||180}
  };
}
// 提交前先在本地拦住"一看就填错了"的情况，省一次 400 往返和无谓的困惑
function hubPrecheck(updates){
  if(updates.persona&&!updates.persona.name)return '「它叫什么」不能空着';
  if(updates.models&&!updates.models.chat)return '日常对话还没选模型';
  if(updates.qq){
    if(!updates.qq.ws_url)return 'QQ 连接地址不能空着';
    if(updates.qq.reply_delay_s.length!==2)return '回复间隔要填两个数字，比如 1.0, 3.0';
  }
  return '';
}
function setHubMsg(scope,text,tone){
  const el=scope?$('#save-msg-'+scope):$('#hub-msg');
  if(!el)return;
  el.textContent=text;
  el.className='save-msg'+(tone?' '+tone:'');
  if(scope)el.style.color='';
}
async function saveHub(scope,btn) {
  const all=collectHubUpdates();
  const keys=SAVE_SCOPES[scope];
  const updates={};
  if(keys)keys.forEach(k=>{if(k in all)updates[k]=all[k]});
  else Object.assign(updates,all);
  if(!Object.keys(updates).length)return;

  const bad=hubPrecheck(updates);
  if(bad){setHubMsg(scope,'⚠ '+bad,'err');return}

  const label=btn?btn.textContent:'';
  if(btn){btn.disabled=true;btn.textContent='保存中…'}
  setHubMsg(scope,'正在保存…','');
  try{
    await api('/api/system/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({updates})});
    const extra='（重启相应入口后生效）';
    setHubMsg(scope,'✅ 已保存 '+new Date().toLocaleTimeString()+extra,'ok');
    loadState();
  }catch(e){
    setHubMsg(scope,'❌ 保存失败：'+(e.message||'未知原因'),'err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent=label}
  }
}

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'hub:open':el=>openHub(el.dataset.section),
  'hub:section':el=>switchHub(el.dataset.section),
  'hub:close':()=>closeHub(),
  'hub:save':el=>saveHub(el.dataset.scope,el),
  'hub:bili-cookies-save':()=>saveBiliCookies(),
};
