// 设置中心「服务与运行」：状态总览 / 通道与登录 / 调度统计 / 日志。
// 故意不放"启动/停止"：服务由守护默认拉起并自动重启，这里只留"重启一次"和
// "清理旧记录"这两个确实需要人判断的动作。
import {$,esc} from './dom.js?v=20261101m21';
import {api} from './api.js?v=20261101m21';

const STATE_TEXT={verified:'运行中',stale:'记录失效',absent:'未启动',
                  blocked_unverified:'旧进程占位',corrupt:'记录损坏'};

export async function loadServicesHub(){
  await refresh();
  loadLog();
}

async function refresh(){
  const msg=$('#svc-summary-msg');
  try{
    const d=await api('/api/system/services/summary');
    renderList(d);renderNapcat(d);renderStats(d);renderSupervisor(d);
    if(msg)msg.textContent='';
    return d;
  }catch(e){
    if(msg)msg.textContent='读取失败：'+e.message;
    return null;
  }
}

function _badge(row){
  if(row.state==='blocked_unverified')return['bad','需处理'];
  if(!row.running)return row.skipped?['','不必启动']:['bad','没在跑'];
  // NapCat 是第三方进程，没有自报快照；其余"在跑但没上报"就是卡住了
  if(row.name!=='napcat'&&row.desired==='running'&&!row.fresh)return['warn','无响应'];
  return['ok','在线'];
}

function renderList(d){
  const box=$('#svc-status-list');
  if(!box)return;
  const rows=d.services||[];
  if(!rows.length){box.innerHTML='<div class="muted">没有可管理的服务</div>';return}
  box.innerHTML=rows.map(s=>{
    const[tone,label]=_badge(s);
    const short=String(s.label||s.name).replace(/（[^）]*）/g,'').trim();
    const bits=[];
    if(s.running&&s.pid)bits.push('pid '+s.pid);
    if(s.detail)bits.push(s.detail);
    if(s.restarts)bits.push('重启 '+s.restarts+' 次');
    if(s.last_exit&&s.last_exit.detail)bits.push('上次失败：'+s.last_exit.detail);
    if(s.skipped)bits.push(s.skipped);
    if(s.state&&s.state!=='verified'&&STATE_TEXT[s.state]&&s.state!=='blocked_unverified')bits.push(STATE_TEXT[s.state]);
    const action=s.state==='blocked_unverified'
      ?`<button type="button" class="btn small" data-action="services:cleanup" data-name="${esc(s.name)}" data-pid="${s.pid||''}">我已手工结束，重试</button>`
      :`<button type="button" class="btn small" data-action="services:restart" data-name="${esc(s.name)}">重启</button>`;
    return `<div class="svc-row" data-state="${tone||'ok'}">`+
      `<span class="svc-badge ${tone||'ok'}">${esc(label)}</span>`+
      `<span class="svc-main"><span class="svc-title">${esc(short)}</span>`+
      `<div class="svc-meta">${esc(bits.join(' · ')||'—')}</div></span>`+
      action+`</div>`;
  }).join('');
}

function renderNapcat(d){
  const line=$('#svc-napcat-line');
  if(!line)return;
  const n=d.napcat||{};
  if(!n.installed){
    line.textContent='这台机器上没找到 NapCat：把 system.napcat_dir 指到 napcat.bat 所在目录后重启工作台';
    return;
  }
  line.textContent=n.logged_in
    ?`已登录（${n.uin||'号未知'}）`
    :'还没登录——点右边打开二维码，用手机 QQ 扫一下确认';
}

function renderStats(d){
  const byName=Object.fromEntries((d.services||[]).map(s=>[s.name,s]));
  const hb=byName['omni-heartbeat']||{},qq=byName['omni-qq']||{};
  const hs=hb.stats||{},qs=qq.stats||{};
  const hbLine=$('#svc-stats-heartbeat'),qqLine=$('#svc-stats-qq');
  if(hbLine){
    hbLine.textContent=hb.running
      ?`心跳：已跳 ${hs.ticks??0} 次${hs.last_tick_at?'，最近 '+hs.last_tick_at:''}；累计触发 ${hs.fired_total??0} 次；在册任务 ${hs.jobs??0} 个`+(hb.fresh?'':'（快照已过期，可能卡住了）')
      :'心跳：未在运行';
  }
  if(qqLine){
    qqLine.textContent=qq.running
      ?`QQ：${qs.connected?'已连上 NapCat':'未连上 NapCat'}；收到 ${qs.received??0} 条，已处理 ${qs.handled??0} 条，队列中 ${qs.pending??0} 条`+(qs.last_message_at?'，最近 '+qs.last_message_at:'')
      :'QQ：未在运行';
  }
}

function renderSupervisor(d){
  const line=$('#svc-supervisor-line');
  if(!line)return;
  const s=d.supervisor||{};
  if(!s.supervised){
    line.textContent=s.mode==='standby'
      ?`守护：另一个工作台实例在管（${s.holder||'未知'}），这个窗口只读`
      :'守护：这个实例不管理本机服务（隔离/测试实例）';
    return;
  }
  const interval=Math.round((s.policy&&s.policy.interval_s)||10);
  line.textContent=`守护：每 ${interval} 秒检查一次`+(s.last_error?'；最近错误：'+s.last_error:'');
}

async function loadLog(){
  const sel=$('#svc-log-name'),out=$('#sec-services-log');
  if(!sel||!out)return;
  out.textContent='读取中…';
  try{
    const d=await api('/api/system/logs/'+encodeURIComponent(sel.value)+'?lines=120');
    out.textContent=d.log||'（暂无日志）';
  }catch(e){out.textContent='读取失败：'+e.message}
}

const logSelect=$('#svc-log-name');
if(logSelect)logSelect.addEventListener('change',loadLog);

export const actions={
  'services:refresh':()=>refresh(),
  'services:log-refresh':()=>loadLog(),
  'services:restart':async el=>{
    const name=el.dataset.name;
    const label=el.textContent;
    el.disabled=true;el.textContent='重启中…';
    try{
      await api(`/api/system/services/${encodeURIComponent(name)}/restart`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
      OmniUI.notify(`${name} 已重启`,'ok');
    }catch(e){OmniUI.notify(e.message,'error')}
    finally{el.disabled=false;el.textContent=label;refresh()}
  },
  'services:cleanup':async el=>{
    const name=el.dataset.name;
    const pid=Number(el.dataset.pid)||null;
    // 只删记录、不杀进程：用户必须自己确认那个旧进程已经处理过
    if(!await OmniUI.ask({title:'清理启动记录',
      message:`确认 pid=${pid||'未知'} 的旧进程已经结束？\n\n这里只删记录，不会替你结束进程。如果它其实还活着，删完守护会再起一个，你就有两个了。`,
      confirmLabel:'只删记录',danger:true}))return;
    try{
      await api('/api/system/services/cleanup',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name,pid,confirm:true})});
      OmniUI.notify('记录已清理，守护下一轮会重新拉起','ok');
    }catch(e){OmniUI.notify(e.message,'error')}
    refresh();
  },
};
