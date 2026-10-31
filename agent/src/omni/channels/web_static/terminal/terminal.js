/* 人工终端：恢复只回放输出，任何异步输入始终绑定原标签及连接。 */
import {$} from '/ui/app/dom.js?v=20261031m11';
const UI=window.OmniUI,views=new Map();let active='',leaving=false,creating=false,available=false;
const api=UI.request;
function status(text){$('#state').textContent=text}
function controls(){
  const count=document.getElementById('terminal-count');if(count)count.textContent=String(views.size);
  const v=views.get(active),connected=v?.ws?.readyState===1&&!v.stopped&&v.row.status==='running';
  for(const id of ['interrupt','paste'])$('#'+id).disabled=!connected;
  for(const id of ['copy','clear','rename'])$('#'+id).disabled=!v;
  $('#end').disabled=!v||v.stopped||v.ending;$('#reconnect').disabled=!v||v.stopped||connected;
  $('#create button').disabled=creating||!available;$('#empty').hidden=views.size>0;
}
function send(v,message){
  if(!v||active!==v.row.id||v.stopped||v.ws?.readyState!==1){status('终端未连接，输入未发送');return}
  const text=JSON.stringify({v:2,...message});if(new TextEncoder().encode(text).length>16384){status('单次输入超过 16 KiB，请分段粘贴');return}v.ws.send(text);
}
function fit(v){try{v.fit.fit()}catch{status('终端尺寸调整失败')}}
function select(id){
  active=id;sessionStorage.setItem('omni-terminal-active',id);
  for(const [key,v]of views){v.screen.hidden=key!==id;v.button.classList.toggle('active',key===id);v.button.setAttribute('aria-selected',String(key===id))}
  const v=views.get(id);if(v){fit(v);v.term.focus();status(v.row.label+' · '+(v.message||UI.labels[v.row.status]||v.row.status))}controls();
}
function message(v,text){v.message=text;if(active===v.row.id)status(text);controls()}
function connect(v){
  if(leaving||v.stopped)return;clearTimeout(v.timer);const previous=v.ws;v.ws=null;previous?.close();
  const protocol=location.protocol==='https:'?'wss':'ws';
  const socket=new WebSocket(protocol+'://'+location.host+'/api/terminal/sessions/'+v.row.id+'/ws?after='+v.after);v.ws=socket;
  message(v,'正在连接 '+v.row.label+'…');
  socket.onopen=()=>{if(v.ws!==socket)return;v.attempt=0;message(v,'已连接 · 输入会立即在本机执行');fit(v)};
  socket.onmessage=e=>{
    if(v.ws!==socket)return;let m;try{m=JSON.parse(e.data)}catch{message(v,'收到无法解析的终端消息');return}
    if(m.type==='output'){if(m.seq>v.after){v.term.write(m.data);v.after=m.seq}}
    else if(m.type==='gap')v.term.writeln('\r\n[较早输出已超过缓存，存在缺失]');
    else if(m.type==='error'){v.stopped=true;message(v,m.message)}
    else if(m.type==='exit'){v.stopped=true;v.row.status=m.status;v.term.writeln('\r\n['+m.status+' · 退出码 '+(m.exit_code??'未知')+']');message(v,'终端已结束 · 退出码 '+(m.exit_code??'未知')+' · 可新建会话')}
  };
  socket.onclose=()=>{
    if(v.ws!==socket||v.stopped||leaving)return;
    if(v.attempt>=5){message(v,'恢复连接未成功，请检查配对或服务后点击“重新连接”');return}
    const delay=[1000,2000,4000,8000,15000][v.attempt++];
    message(v,'连接断开，'+delay/1000+' 秒后恢复；输入不会重发');v.timer=setTimeout(()=>connect(v),delay);
  };
  socket.onerror=()=>{if(v.ws===socket)message(v,'终端连接异常，请检查工作台配对与服务状态')};
}
async function paste(v,socket,data){
  if(!v||v!==views.get(active)||v.ws!==socket||socket?.readyState!==1){UI.notify('终端已切换或连接已变化，粘贴已取消');return}
  if(/[\r\n]/.test(data)&&!await UI.ask({title:'确认多行粘贴',message:'多行文本可能立即执行命令。\n\n'+data.slice(0,1200)+(data.length>1200?'\n…（预览已截断）':''),confirmLabel:'粘贴到 '+v.row.label,danger:true}))return;
  if(v!==views.get(active)||v.ws!==socket||socket.readyState!==1){UI.notify('操作目标已变化，粘贴已取消');return}send(v,{type:'input',data});
}
function add(row){
  if(views.has(row.id))return;
  document.querySelector('.terminal-welcome')?.remove();
  const screen=UI.element('div',null,'screen');screen.hidden=true;$('#screens').append(screen);
  const button=UI.element('button',row.label);button.type='button';button.setAttribute('role','tab');button.onclick=()=>select(row.id);$('#tabs').append(button);
  const term=new Terminal({fontFamily:'Consolas, monospace',fontSize:13,scrollback:4000,theme:{background:'#0e1116',foreground:'#dbe7ee'}}),fitAddon=new FitAddon.FitAddon();term.loadAddon(fitAddon);term.open(screen);
  const v={row,screen,button,term,fit:fitAddon,after:0,attempt:0,stopped:false};views.set(row.id,v);
  term.onData(data=>{if(active===row.id)send(v,{type:'input',data})});
  term.onResize(({cols,rows})=>{if(v.ws?.readyState===1&&cols>=20&&rows>=5)v.ws.send(JSON.stringify({v:2,type:'resize',cols:Math.min(500,cols),rows:Math.min(200,rows)}))});
  screen.addEventListener('paste',e=>{const data=e.clipboardData?.getData('text/plain');if(data==null)return;e.preventDefault();e.stopImmediatePropagation();paste(v,v.ws,data)},true);
  connect(v); // 已退出会话也连接一次，回放保留的输出及退出码。
}
$('#create').onsubmit=async e=>{
  e.preventDefault();if(creating||!available)return;creating=true;controls();
  try{const row=await api('/api/terminal/sessions',{method:'POST',body:JSON.stringify({shell:$('#shell').value,cwd:$('#cwd').value})});add(row);select(row.id)}
  catch(error){status(error.message)}finally{creating=false;controls()}
};
$('#interrupt').onclick=()=>send(views.get(active),{type:'input',data:'\x03'});
$('#copy').onclick=async()=>{try{const text=views.get(active)?.term.getSelection();if(!text){UI.notify('请先在终端选中文本');return}await navigator.clipboard.writeText(text);UI.notify('已复制选区')}catch{status('复制失败，请使用系统复制快捷键')}};
$('#paste').onclick=async()=>{const v=views.get(active),socket=v?.ws;try{await paste(v,socket,await navigator.clipboard.readText())}catch{status('无法读取剪贴板，请在终端中粘贴')}};
$('#clear').onclick=()=>views.get(active)?.term.clear();
$('#rename').onclick=async()=>{
  const v=views.get(active);if(!v)return;const label=await UI.ask({title:'重命名 '+v.row.label,input:v.row.label,confirmLabel:'保存名称'});if(!label)return;
  try{v.row=await api('/api/terminal/sessions/'+v.row.id,{method:'PATCH',body:JSON.stringify({label})});v.button.textContent=v.row.label;if(active===v.row.id)select(v.row.id)}catch(error){UI.notify(error.message,'error')}
};
$('#end').onclick=async()=>{
  const v=views.get(active);if(!v||v.ending||!await UI.ask({title:'结束 '+v.row.label,message:'结束此终端及其进程？请先保存需要保留的工作。',confirmLabel:'结束终端',danger:true}))return;
  v.ending=true;controls();try{await api('/api/terminal/sessions/'+v.row.id,{method:'DELETE'});v.stopped=true;clearTimeout(v.timer);v.ws?.close();message(v,'结束请求已完成；进程状态请以服务回读为准')}
  catch(error){UI.notify(error.message,'error')}finally{v.ending=false;controls()}
};
$('#reconnect').onclick=()=>{const v=views.get(active);if(v){v.attempt=0;connect(v)}};
window.addEventListener('resize',()=>{const v=views.get(active);if(v)fit(v)});
document.addEventListener('keydown',event=>{if(event.ctrlKey&&event.shiftKey&&event.key.toLowerCase()==='t'){event.preventDefault();document.querySelector('#create button')?.click()}});
window.addEventListener('beforeunload',()=>{leaving=true;for(const v of views.values()){clearTimeout(v.timer);v.ws?.close()}});
(async()=>{
  controls();try{
    const options=await api('/api/terminal/options');available=options.available!==false&&options.shells.length>0;
    for(const name of options.shells){const o=UI.element('option',name);o.value=name;$('#shell').append(o)}$('#cwd').value=options.roots[0]||'';
    const data=await api('/api/terminal/sessions');for(const row of data.sessions)add(row);
    const saved=sessionStorage.getItem('omni-terminal-active');if(views.size)select(views.has(saved)?saved:views.keys().next().value);
    else status(available?'选择 Shell 和目录后新建。命令在本机用户权限下执行。':'终端依赖不可用或没有可用 Shell，请在能力总览查看诊断');
  }catch(error){status(error.message+'；请检查工作台配对与服务')}finally{controls()}
})();
