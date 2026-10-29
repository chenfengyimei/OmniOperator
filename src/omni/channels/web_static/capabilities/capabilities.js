import {$,esc} from '/ui/app/dom.js?v=20261030g';
const UI=window.OmniUI;
const api=UI.request;
const states={ok:'通过',warning:'需检查',error:'错误',info:'信息'};
let busy=false;
function toneFor(state){return state==='ready'?'ready':state==='warning'?'warning':'failed'}
function stateLabel(state){return state==='ready'?'可用':state==='warning'?'需检查':'不可用'}
async function load(){
  if(busy)return;busy=true;$('#refresh').disabled=true;
  $('#status').textContent='正在读取只读诊断…';
  try{
    const d=await api('/api/capabilities');
    const ready=d.mcp.filter(s=>s.state==='ready').length,failed=d.mcp.filter(s=>s.state==='failed').length;
    const previews=d.android.previews.reduce((total,s)=>total+s.clients,0);
    const jobTotal=Object.values(d.command_jobs.statuses||{}).reduce((a,n)=>a+Number(n||0),0);
    $('#status').textContent=`只读诊断：${states[d.doctor.status]||d.doctor.status} · ${new Date(d.doctor.generated_at).toLocaleString()} · ${d.closing?'运行态正在关闭':'未请求关闭'}`;
    const summaries=[
      {key:'tools',name:'工具注册',icon:'extension',metric:d.tools.total,unit:'项工具',detail:`分属 ${Object.keys(d.tools.by_toolset).length} 个能力组`,state:'ready',value:d.tools},
      {key:'terminal',name:'人工终端',icon:'terminal',metric:d.terminal.active,unit:`/ ${d.terminal.limit} 会话`,detail:d.terminal.available?'终端可用，输入在本机用户权限下执行':'当前没有可用 Shell',state:d.terminal.available?'ready':'warning',value:d.terminal},
      {key:'jobs',name:'后台命令',icon:'checklist',metric:jobTotal,unit:'项最近任务',detail:Object.entries(d.command_jobs.statuses||{}).map(([k,v])=>`${UI.labels[k]||k} ${v}`).join(' · ')||'暂无任务记录',state:'ready',value:d.command_jobs},
      {key:'mcp',name:'本地 MCP',icon:'network_check',metric:ready,unit:`/ ${d.mcp.length} 已连接`,detail:failed?`${failed} 项连接失败，需要检查配置`:'配置读取不会启动外部服务',state:failed?'warning':'ready',value:d.mcp},
      {key:'android',name:'安卓与共享看屏',icon:'smartphone',metric:d.android.contexts,unit:'台最近观察',detail:`${d.android.adb_found?'已找到 ADB':'未找到 ADB'} · 预览连接 ${previews} 个`,state:d.android.adb_found?'ready':'warning',value:d.android}
    ];
    $('#cards').innerHTML=summaries.map(item=>
      `<article class="card cap-card" data-state="${toneFor(item.state)}">`+
        `<div class="cap-card-top"><span class="cap-icon"><span class="material-icons">${item.icon}</span></span><span class="cap-state"><i></i>${stateLabel(item.state)}</span></div>`+
        `<h3>${esc(item.name)}</h3>`+
        `<div class="cap-metric"><b>${esc(item.metric)}</b><span>${esc(item.unit)}</span></div>`+
        `<p>${esc(item.detail)}</p>`+
        `<details><summary><span class="material-icons">data_object</span>查看诊断数据</summary><pre>${esc(JSON.stringify(item.value,null,2))}</pre></details>`+
      `</article>`).join('');
    $('#check-count').textContent=`${d.doctor.checks.length} 项`;
    $('#checks').innerHTML=d.doctor.checks.length?d.doctor.checks.map(check=>
      `<div class="check-row-item" data-state="${esc(check.state)}">`+
        `<span class="check-indicator"><span class="material-icons">${check.state==='ok'?'check_circle':check.state==='warning'?'warning':'error'}</span></span>`+
        `<div><strong>${esc(check.name)}</strong><p>${esc(check.detail)}</p></div>`+
        `<span class="check-label">${esc(states[check.state]||check.state)}</span>`+
      `</div>`).join(''):'<div class="empty">没有返回检查项</div>';
  }catch(e){
    $('#status').textContent=e.message;
    $('#cards').innerHTML='<div class="empty-rich"><span class="material-icons">cloud_off</span><h3>能力快照读取失败</h3><p>请检查工作台配对与服务状态后重试。</p></div>';
  }finally{busy=false;$('#refresh').disabled=false}
}
$('#refresh').onclick=load;
$('#probe-phone').onclick=async()=>{
  const button=$('#probe-phone');if(button.disabled)return;button.disabled=true;
  $('#probe').textContent='正在检查本地手机连接…';
  try{$('#probe').textContent=JSON.stringify(await api('/api/android/devices'),null,2)}
  catch(e){$('#probe').textContent=e.message}
  finally{button.disabled=false}
};
load();
