'use strict';const $=s=>document.querySelector(s);let busy=false;
async function api(path){const r=await fetch(path),d=await r.json();if(!r.ok)throw Error(d.detail||'请求失败');return d}
const states={ok:'通过',warning:'需检查',error:'错误',info:'信息'};
async function load(){
 if(busy)return;busy=true;$('#refresh').disabled=true;
 try{
  const d=await api('/api/capabilities');
  $('#status').textContent=`只读诊断：${states[d.doctor.status]||d.doctor.status} · ${new Date(d.doctor.generated_at).toLocaleString()} · ${d.closing?'运行态正在关闭':'未请求关闭'}`;
  const ready=d.mcp.filter(s=>s.state==='ready').length,failed=d.mcp.filter(s=>s.state==='failed').length;
  const previews=d.android.previews.reduce((total,s)=>total+s.clients,0);
  const summaries=[
   ['工具注册',`已注册 ${d.tools.total} 项工具，分属 ${Object.keys(d.tools.by_toolset).length} 个能力组`,d.tools],
   ['人工终端',`${d.terminal.available?'可用':'不可用'} · 活跃 ${d.terminal.active}/${d.terminal.limit} 个`+(d.terminal.closing?' · 正在关闭':''),d.terminal],
   ['后台命令（最近 100 项）',Object.entries(d.command_jobs.statuses).map(([state,count])=>`${state} ${count} 项`).join(' · ')||'暂无任务；启动任务仍需人工批准',d.command_jobs],
   ['本地 MCP',`配置 ${d.mcp.length} 项 · 已连接 ${ready} 项 · 失败 ${failed} 项`,d.mcp],
   ['安卓与共享看屏',`${d.android.adb_found?'已找到 ADB':'未找到 ADB'} · 预览连接 ${previews} 个 · 最近观察 ${d.android.contexts} 台（不代表当前连接）`,d.android]
  ];
  $('#cards').replaceChildren();
  for(const [name,summary,value]of summaries){const card=document.createElement('article');card.className='card';const title=document.createElement('h2');title.textContent=name;const text=document.createElement('p');text.textContent=summary;const details=document.createElement('details'),label=document.createElement('summary'),content=document.createElement('pre');label.textContent='查看诊断数据';content.textContent=JSON.stringify(value,null,2);details.append(label,content);card.append(title,text,details);$('#cards').append(card)}
  $('#checks').replaceChildren();for(const check of d.doctor.checks){const p=document.createElement('p');p.dataset.state=check.state;p.textContent=`${states[check.state]||check.state} · ${check.name} · ${check.detail}`;$('#checks').append(p)}
 }catch(e){$('#status').textContent=e.message}finally{busy=false;$('#refresh').disabled=false}
}
$('#refresh').onclick=load;$('#probe-phone').onclick=async()=>{try{$('#probe').textContent=JSON.stringify(await api('/api/android/devices'),null,2)}catch(e){$('#probe').textContent=e.message}};load();
