'use strict';
const $=s=>document.querySelector(s),UI=window.OmniUI,pending=new Set();
let servers=[],version=0,reading=null,loading=false;
const api=UI.request;
function begin(name){
  const current=++version;reading?.abort();reading=new AbortController();
  $('#confirm')?.remove();$('#catalog').replaceChildren();$('#detail-title').textContent=name;$('#detail').textContent='正在读取…';
  return {current,signal:reading.signal};
}
function detail(name,data,current){
  if(current!==version)return;$('#confirm')?.remove();$('#detail-title').textContent=name;$('#detail').textContent=typeof data==='string'?data:JSON.stringify(data,null,2);
}
function render(){
  $('#servers').replaceChildren();
  if(!servers.length){$('#servers').append(UI.element('div','尚未配置本地 MCP。请在 config.yaml 的 mcp.servers 中配置可信服务、环境变量引用及工具权限，再重启使用该配置的入口。','ui-empty'));return}
  for(const row of servers){
    const card=UI.element('article',null,'server');card.append(UI.element('h2',row.name),UI.badge(row.state));
    card.append(UI.element('p','等待 '+(row.waiting??0)+' / '+(row.max_waiting??8)+' · 目录 '+(UI.labels[row.schema_state]||row.schema_state)+' · 代际 '+(row.generation??0)));
    if(row.configuration_matches===false)card.append(UI.element('p','配置已变化：新调用已停止，请重启原入口。','configuration-warning'));
    if(row.last_error)card.append(UI.element('p',row.last_error,'configuration-warning'));
    const actions=UI.element('div',null,'server-actions');
    for(const [label,action,method]of [['诊断','health','GET'],['日志','logs','GET'],['检查连接','connect','POST'],['断开','disconnect','POST'],['重置连接','reset','POST']]){
      const button=UI.element('button',label);button.disabled=(method==='POST'&&(!row.enabled||pending.has(row.name)));
      button.onclick=()=>operate(row.name,action,method);actions.append(button);
    }
    const tools=UI.element('button','查看完整目录与授权');tools.disabled=!row.enabled||pending.has(row.name);tools.onclick=()=>catalog(row.name);actions.append(tools);card.append(actions);$('#servers').append(card);
  }
}
async function load(){
  if(loading)return;loading=true;$('#refresh').disabled=true;
  try{const data=await api('/api/mcp/overview');servers=data.servers;render();$('#status').textContent=servers.length+' 个配置服务 · 状态读取不启动服务'}
  catch(error){$('#status').textContent=error.message}finally{loading=false;$('#refresh').disabled=false}
}
async function operate(name,action,method){
  if(method==='POST'&&pending.has(name))return;
  if(['disconnect','reset'].includes(action)&&!await UI.ask({title:(action==='reset'?'重置':'断开')+' '+name,message:'可能中断正在进行的调用。已经派发的写操作效果仍需核查，不会自动重放。',confirmLabel:'继续',danger:true}))return;
  const request=begin(name);if(method==='POST'){pending.add(name);render()}
  try{const data=await api('/api/mcp/'+encodeURIComponent(name)+'/'+action,{method,...(method==='GET'?{signal:request.signal}:{})});detail(name,data,request.current)}
  catch(error){if(error.name!=='AbortError')detail(name,error.message,request.current)}
  finally{if(method==='POST'){pending.delete(name);await load()}}
}
async function catalog(name){
  const request=begin(name);let offset=0,rows=[],fingerprint='',confirmed=false,total=0,pages=0;
  try{
    do{
      if(++pages>6)throw Error('目录超出本地复查上限，请检查服务');
      const data=await api('/api/mcp/'+encodeURIComponent(name)+'/catalog?offset='+offset+'&limit=50',{signal:request.signal});
      if(request.current!==version)return;
      if(fingerprint&&fingerprint!==data.fingerprint)throw Error('目录在分页期间变化，请重新读取');
      fingerprint=data.fingerprint;confirmed=data.confirmed;total=data.total;
      if(!Number.isInteger(total)||total<0||total>256)throw Error('目录数量无效或超出上限');
      rows.push(...data.tools);offset=rows.length;if(!data.tools.length&&offset<total)throw Error('目录分页不完整');
    }while(offset<total);
    if(rows.length!==total)throw Error('目录数量不一致，请重新读取');
    detail(name+' · 完整目录',{fingerprint,confirmed,total},request.current);
    for(const tool of rows){
      const item=UI.element('details',null,'catalog-tool'),heading=UI.element('summary',tool.name);
      item.append(heading,UI.element('p',tool.description||'没有描述'),UI.element('pre',JSON.stringify(tool,null,2)));$('#catalog').append(item);
    }
    if(!rows.length)$('#catalog').append(UI.element('p','此服务未提供工具。','ui-empty'));
    if(!confirmed&&fingerprint){
      const button=UI.element('button','已复查完整目录及只读白名单，确认当前指纹','ui-primary');button.id='confirm';$('#catalog').append(button);
      button.onclick=async()=>{
        if(request.current!==version||pending.has(name))return;
        if(!await UI.ask({title:'确认 '+name+' 工具目录',message:'只恢复用户已配置的只读白名单。请复查上方全部 '+total+' 项工具和当前指纹：\n'+fingerprint,confirmLabel:'确认此目录'}))return;
        if(request.current!==version)return;pending.add(name);button.disabled=true;render();
        try{await api('/api/mcp/'+encodeURIComponent(name)+'/confirm-schema',{method:'POST',body:JSON.stringify({fingerprint})});if(request.current===version){button.remove();UI.notify('目录指纹已确认')}}
        catch(error){detail(name,error.message,request.current)}
        finally{pending.delete(name);button.disabled=false;load()}
      };
    }
    load();
  }catch(error){if(error.name!=='AbortError')detail(name,error.message,request.current)}
}
$('#refresh').onclick=load;window.addEventListener('beforeunload',()=>{version++;reading?.abort()});load();
