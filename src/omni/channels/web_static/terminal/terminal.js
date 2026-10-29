/* 人工终端：仅回放输出，不在恢复时重发键盘输入。 */
'use strict';
const $=s=>document.querySelector(s), views=new Map();let active='',leaving=false;
async function api(path,options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'请求失败');return d}
function status(text){$('#state').textContent=text}
function send(message){const v=views.get(active);if(v&&v.ws&&v.ws.readyState===1)v.ws.send(JSON.stringify({v:2,...message}))}
function fit(v){try{v.fit.fit()}catch(e){status('终端尺寸调整失败')}}
function select(id){active=id;sessionStorage.setItem('omni-terminal-active',id);for(const [key,v]of views){v.screen.hidden=key!==id;v.button.classList.toggle('active',key===id)}const v=views.get(id);if(v){fit(v);v.term.focus();status(v.row.label+' · '+v.row.status)}}
function connect(v){
  if(leaving||v.stopped)return;
  const protocol=location.protocol==='https:'?'wss':'ws';
  v.ws=new WebSocket(`${protocol}://${location.host}/api/terminal/sessions/${v.row.id}/ws?after=${v.after}`);
  v.ws.onopen=()=>{if(active===v.row.id)status('已连接 · 输入会立即在本机执行');fit(v)};
  v.ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='output'){if(m.seq>v.after){v.term.write(m.data);v.after=m.seq}}else if(m.type==='gap')v.term.writeln('\r\n[较早输出已超过缓存，存在缺失]');else if(m.type==='exit'){v.stopped=true;v.row.status=m.status;v.term.writeln(`\r\n[${m.status} · 退出码 ${m.exit_code??'未知'}]`);if(active===v.row.id)status('终端已结束，可新建会话')}};
  v.ws.onclose=()=>{if(!v.stopped&&!leaving){if(active===v.row.id)status('连接断开，正在恢复；输入不会重发');v.timer=setTimeout(()=>connect(v),1500)}};
}
function add(row){
  if(views.has(row.id))return;
  const screen=document.createElement('div');screen.className='screen';screen.hidden=true;$('#screens').append(screen);
  const button=document.createElement('button');button.textContent=row.label;button.onclick=()=>select(row.id);$('#tabs').append(button);
  const term=new Terminal({fontFamily:'Consolas, monospace',fontSize:13,scrollback:4000,theme:{background:'#0e1116',foreground:'#dbe7ee'}}),fitAddon=new FitAddon.FitAddon();term.loadAddon(fitAddon);term.open(screen);
  const v={row,screen,button,term,fit:fitAddon,after:0,stopped:false};views.set(row.id,v);
  term.onData(data=>{if(active===row.id)send({type:'input',data})});
  term.onResize(({cols,rows})=>{if(v.ws&&v.ws.readyState===1&&cols>=20&&rows>=5)v.ws.send(JSON.stringify({v:2,type:'resize',cols:Math.min(500,cols),rows:Math.min(200,rows)}))});
  screen.addEventListener('paste',e=>{const data=e.clipboardData?.getData('text/plain');if(data==null)return;e.preventDefault();e.stopImmediatePropagation();if(!/[\r\n]/.test(data)||confirm('粘贴多行会执行命令，确认粘贴？'))send({type:'input',data})},true);
  connect(v);
}
$('#create').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const row=await api('/api/terminal/sessions',{method:'POST',body:JSON.stringify({shell:$('#shell').value,cwd:$('#cwd').value})});add(row);select(row.id)}catch(e){status(e.message)}finally{button.disabled=false}};
$('#interrupt').onclick=()=>send({type:'input',data:'\x03'});
$('#copy').onclick=async()=>{try{await navigator.clipboard.writeText(views.get(active)?.term.getSelection()||'')}catch(e){status('复制失败，请使用系统复制快捷键')}};
$('#paste').onclick=async()=>{try{const data=await navigator.clipboard.readText();if(!/[\r\n]/.test(data)||confirm('粘贴多行会执行命令，确认粘贴？'))send({type:'input',data})}catch(e){status('无法读取剪贴板，请在终端中粘贴')}};
$('#clear').onclick=()=>views.get(active)?.term.clear();
$('#rename').onclick=async()=>{const v=views.get(active);if(!v)return;const label=prompt('终端名称',v.row.label);if(!label)return;try{v.row=await api('/api/terminal/sessions/'+active,{method:'PATCH',body:JSON.stringify({label})});v.button.textContent=v.row.label}catch(e){status(e.message)}};
$('#end').onclick=async()=>{const v=views.get(active);if(!v||!confirm('结束此终端及其进程？'))return;try{await api('/api/terminal/sessions/'+active,{method:'DELETE'});v.stopped=true;clearTimeout(v.timer);v.ws?.close();status('结束请求已完成')}catch(e){status(e.message)}};
window.addEventListener('resize',()=>{const v=views.get(active);if(v)fit(v)});
window.addEventListener('beforeunload',()=>{leaving=true;for(const v of views.values()){clearTimeout(v.timer);v.ws?.close()}});
(async()=>{try{const options=await api('/api/terminal/options');for(const name of options.shells){const o=document.createElement('option');o.value=o.textContent=name;$('#shell').append(o)}$('#cwd').value=options.roots[0]||'';const data=await api('/api/terminal/sessions');for(const row of data.sessions)add(row);const saved=sessionStorage.getItem('omni-terminal-active');if(views.size)select(views.has(saved)?saved:views.keys().next().value);else status('选择 Shell 和目录后新建。命令在本机用户权限下执行。')}catch(e){status(e.message+'；请返回工作台完成配对')}})();
