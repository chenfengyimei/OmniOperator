'use strict';
const $=s=>document.querySelector(s),UI=window.OmniUI;
const labels={workspace:'工作目录',files:'文件目录',documents:'资料目录（模型只读）',memory:'长期记忆与档案目录',skills:'技能目录',artifacts:'产物与截图',cache:'缓存目录',logs:'日志目录',backups:'备份目录'};
const appLabels={data_root:'全局业务存储根',workspace:'默认工作目录',agents_root:'Agent 空间池',source_root:'程序源码（固定）',catalog:'目录配置（固定）',run:'服务身份（固定）',service_logs:'服务启动日志（固定）'};
const appKeys=['data_root','workspace','agents_root'];
function describeApplication(value){return Object.entries(value||{}).map(([name,target])=>(appLabels[name]||name)+' → '+target).join('\n')}
let state=null,draft=null,selected='',dirty=false,saving=false,listing=false,path='',nextOffset=null,viewVersion=0,previewRequest=null,fileBusy=false;
function status(message){$('#status').textContent=message}
function markDirty(){dirty=true;$('#dirty').hidden=false}
function settingsControls(){for(const node of document.querySelectorAll('#settings-view input,#settings-view button'))node.disabled=saving;$('#reload').disabled=saving}
function catalog(data){return {version:data.version,active:data.active,application:structuredClone(data.application||{}),agents:structuredClone(data.agents)}}
function collect(){if(!draft||!selected)return;for(const name of appKeys)draft.application[name]=$('#app-'+name).value.trim();const agent=draft.agents.find(row=>row.id===selected);agent.name=$('#agent-name').value.trim();agent.home=$('#agent-home').value.trim();agent.directories={};for(const name of Object.keys(labels))agent.directories[name]=$('#dir-'+name).value.trim();if($('#agent-default').checked)draft.active=selected}
function renderInventory(){
  const root=$('#storage-inventory');root.replaceChildren();
  for(const row of state.inventory||[]){const item=UI.element('article',null,'inventory-row');item.setAttribute('role','listitem');item.append(UI.element('small',row.category),UI.element('h3',row.label),UI.element('code',row.path),UI.element('p',row.contents),UI.element('small',row.access));root.append(item)}
  if(!root.children.length)root.append(UI.element('p','此运行态尚未提供存储索引，请重新加载。','empty'));
}
function renderAgents(){
  $('#agents').replaceChildren();
  for(const agent of draft.agents){const button=UI.element('button');button.classList.toggle('active',agent.id===selected);button.append(UI.element('strong',agent.name),UI.element('small',agent.id+(agent.id===draft.active?' · 下次默认':'')));button.onclick=()=>{collect();selected=agent.id;renderAgents();renderEditor()};$('#agents').append(button)}
}
function renderEditor(){
  const agent=draft.agents.find(row=>row.id===selected);$('#editor-title').textContent=agent.name+' · '+agent.id;$('#agent-name').value=agent.name;$('#agent-home').value=agent.home||'';$('#agent-home').readOnly=agent.id==='default';
  $('#agent-home').placeholder=agent.id==='default'?'继承全局业务存储根':'留空使用 Agent 空间池 / '+agent.id;$('#directory-fields').replaceChildren();
  for(const [name,label]of Object.entries(labels)){const item=UI.element('label',label),input=UI.element('input');input.id='dir-'+name;input.value=agent.directories?.[name]||'';input.placeholder='留空自动分类到 '+name+'/';input.oninput=markDirty;item.append(input);$('#directory-fields').append(item)}
  $('#agent-default').checked=draft.active===selected;$('#layout-preview').textContent='修改后点击“预览有效目录”，不会创建目录。';
}
async function load(){
  if(saving)return;$('#reload').disabled=true;
  try{
    state=await UI.request('/api/storage');draft=catalog(state);selected=draft.agents.some(row=>row.id===selected)?selected:state.running.id;dirty=false;$('#dirty').hidden=true;
    for(const name of appKeys)$('#app-'+name).value=draft.application[name]||'';
    $('#application-paths').textContent=describeApplication(state.running_application);renderInventory();
    $('#running').textContent='当前运行：'+state.running.name+'（'+state.running.id+'） · 下次默认：'+draft.active+(state.restart_required?' · 配置已变化，需重启相应入口':' · 目录已与当前运行一致');
    $('#file-owner').textContent=state.running.name+' 的公开文件区';status('目录配置：'+state.catalog_path);renderAgents();renderEditor();
    $('#effective-paths').textContent=Object.entries(state.runtime_paths).map(([name,target])=>name+' → '+target).join('\n');
  }catch(error){status(error.message);$('#storage-inventory').replaceChildren(UI.element('p','目录读取失败：'+error.message+'。请使用重新加载重试。','empty'));UI.notify(error.message,'error')}finally{$('#reload').disabled=false}
}
$('#new-agent').onsubmit=e=>{
  e.preventDefault();if(!draft)return;collect();const id=$('#new-id').value.trim(),name=$('#new-name').value.trim();
  if(draft.agents.some(row=>row.id===id)){UI.notify('该标识已存在，请使用唯一标识','error');return}
  if(draft.agents.length>=32){UI.notify('Agent 数量已达 32 个上限','error');return}
  draft.agents.push({id,name,home:'',directories:{}});selected=id;markDirty();renderAgents();renderEditor();$('#new-agent').reset();
};
$('#agent-name').oninput=markDirty;$('#agent-home').oninput=markDirty;$('#agent-default').onchange=()=>{if(!$('#agent-default').checked&&draft.active===selected){$('#agent-default').checked=true;UI.notify('请选择另一个 Agent，并将它设为默认');return}collect();markDirty();renderAgents()};
for(const name of appKeys)$('#app-'+name).oninput=markDirty;
async function preview(){collect();const data=await UI.request('/api/storage/preview',{method:'POST',body:JSON.stringify({revision:state.revision,catalog:draft})});const layout=data.resolved.find(row=>row.id===selected);$('#layout-preview').textContent='全局目录\n'+describeApplication(data.application_resolved)+'\n\n'+layout.name+'（'+layout.id+'）\n'+layout.home+'\n'+Object.entries(layout.directories).map(([name,target])=>'  '+labels[name]+' → '+target).join('\n');return data}
$('#preview').onclick=async()=>{try{await preview();status('目录校验通过；未创建或移动文件')}catch(error){status(error.message)}};
$('#directory-form').onsubmit=async e=>{
  e.preventDefault();if(saving||!state)return;saving=true;settingsControls();
  try{
    await preview();const body=JSON.stringify({revision:state.revision,catalog:draft,acknowledge:true});
    if(!await UI.ask({title:'保存项目与 Agent 目录设置',message:$('#layout-preview').textContent+'\n\n下次默认 Agent：'+draft.active+'。\n保存只影响下次启动，当前回合与文件区保持原目标。旧数据不会自动搬移、合并或删除，新目录可能使用空记忆库。请先备份并准备需要的数据副本；全局变更需重启所有受影响入口。',confirmLabel:'保存下次启动设置'}))return;
    await UI.request('/api/storage',{method:'PUT',body});dirty=false;UI.notify('设置已保存；重启相应入口后使用新目录');
  }catch(error){status(error.message);UI.notify(error.message,'error')}
  finally{saving=false;settingsControls();if(!dirty)await load()}
};
$('#reload').onclick=async()=>{if(dirty&&!await UI.ask({title:'重新加载设置',message:'放弃本页尚未保存的目录修改？',confirmLabel:'重新加载',danger:true}))return;load()};
$('#backup-current').onclick=async()=>{if(!state)return;const button=$('#backup-current');if(button.disabled)return;if(!await UI.ask({title:'备份 '+state.running.name,message:'备份当前运行空间的记忆、状态、技能、文件与资料，并校验副本。配置凭据默认脱敏；不自动搬运工作区项目、Cookie 或浏览器登录态。',confirmLabel:'生成备份'}))return;button.disabled=true;try{const report=await UI.request('/api/system/backup',{method:'POST',timeoutMs:0});status('备份已校验：'+report.dest);UI.notify('备份已生成并校验')}catch(error){status(error.message)}finally{button.disabled=false}};
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{for(const name of ['settings','files','inventory'])$('#'+name+'-view').hidden=name!==button.dataset.view;document.querySelectorAll('[data-view]').forEach(node=>node.setAttribute('aria-selected',String(node===button)));if(button.dataset.view==='files'&&state)loadFiles()});
function fileControls(){for(const id of ['scope','up','refresh-files','mkdir','upload-file'])$('#'+id).disabled=fileBusy;$('#upload').disabled=fileBusy||!$('#upload-file').files.length;$('#up').disabled=fileBusy||!path;$('#more').disabled=listing||fileBusy}
function resetFileView(){viewVersion++;previewRequest?.abort();path='';nextOffset=null;$('#file-preview').textContent='';$('#preview-title').textContent='文件预览';$('#upload-file').value='';loadFiles()}
function breadcrumbs(){const box=$('#breadcrumbs');box.replaceChildren();const root=UI.element('button','根目录');root.onclick=()=>{path='';viewVersion++;loadFiles()};box.append(root);let partial='';for(const name of path.split('/').filter(Boolean)){partial+=(partial?'/':'')+name;const target=partial,button=UI.element('button',name);button.onclick=()=>{path=target;viewVersion++;loadFiles()};box.append(UI.element('span','/'),button)}}
async function loadFiles(more=false){
  if(!state||listing||fileBusy)return;listing=true;fileControls();const version=viewVersion,scope=$('#scope').value,here=path;
  if(!more){$('#file-list').replaceChildren();nextOffset=null}breadcrumbs();$('#file-status').textContent='读取目录…';
  try{
    const data=await UI.request('/api/storage/files?'+new URLSearchParams({scope,path:here,offset:more?nextOffset||0:0}));if(version!==viewVersion||scope!==$('#scope').value||here!==path)return;
    $('#file-list .ui-empty')?.remove();
    for(const row of data.entries){const button=UI.element('button',null,'file-row'),target=(here?here+'/':'')+row.name;button.append(UI.element('span',row.directory?'▸':'·'),UI.element('strong',row.name),UI.element('small',row.directory?'文件夹':row.size+' B'));button.onclick=()=>{if(fileBusy)return;if(row.directory){path=target;viewVersion++;loadFiles()}else showFile(scope,target)};$('#file-list').append(button)}
    nextOffset=data.next_offset;$('#more').hidden=nextOffset==null;$('#file-status').textContent=(data.missing?'目录尚未创建，可上传文件或新建目录':$('#file-list').children.length+' 项已显示')+' · '+state.public_roots[scope];
    if(!$('#file-list').children.length)$('#file-list').append(UI.element('div','当前目录为空','ui-empty'));
  }catch(error){if(version===viewVersion)$('#file-status').textContent=error.message}finally{listing=false;fileControls();if(version!==viewVersion)loadFiles()}
}
async function showFile(scope,target){
  previewRequest?.abort();previewRequest=new AbortController();const controller=previewRequest,version=viewVersion;$('#preview-title').textContent=target;$('#file-preview').textContent='读取预览…';
  try{const data=await UI.request('/api/storage/preview-file?'+new URLSearchParams({scope,path:target}),{signal:controller.signal});if(controller!==previewRequest||version!==viewVersion)return;$('#preview-meta').textContent=data.size+' B'+(data.truncated?' · 仅展示前 16 KiB':'');$('#file-preview').textContent=data.binary?'此文件为二进制，仅展示元数据；不会解码或执行。':data.text}
  catch(error){if(controller===previewRequest&&error.name!=='AbortError')$('#file-preview').textContent=error.message}
}
async function mutateFile(kind){
  if(fileBusy||!state)return;const scope=$('#scope').value,here=path,version=viewVersion,owner=state.running.name;let target,file;
  if(kind==='folder'){const name=await UI.ask({title:'在 '+owner+' 新建目录',message:labels[scope]+' / '+here,input:'新目录',confirmLabel:'创建目录'});if(!name)return;target=(here?here+'/':'')+name}
  else{file=$('#upload-file').files[0];if(!file)return;if(file.size>state.max_upload_bytes){UI.notify('单文件不能超过 8 MiB','error');return}target=(here?here+'/':'')+file.name;if(!await UI.ask({title:'上传到 '+owner,message:labels[scope]+' / '+target+'\n同名文件不会被覆盖；内容不会自动执行或加载到模型。',confirmLabel:'上传文件'}))return}
  if(version!==viewVersion||scope!==$('#scope').value){UI.notify('文件区已变化，操作已取消');return}fileBusy=true;fileControls();
  try{await UI.request(kind==='folder'?'/api/storage/folders':'/api/storage/upload?'+new URLSearchParams({scope,path:target}),kind==='folder'?{method:'POST',body:JSON.stringify({scope,path:target})}:{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}});UI.notify(kind==='folder'?'目录已创建':'文件已保存');$('#upload-file').value=''}
  catch(error){UI.notify(error.message,'error');$('#file-status').textContent=error.message}finally{fileBusy=false;fileControls();loadFiles()}
}
$('#scope').onchange=resetFileView;$('#up').onclick=()=>{path=path.split('/').slice(0,-1).join('/');viewVersion++;loadFiles()};$('#refresh-files').onclick=()=>loadFiles();$('#more').onclick=()=>loadFiles(true);$('#mkdir').onclick=()=>mutateFile('folder');$('#upload').onclick=()=>mutateFile('upload');$('#upload-file').onchange=fileControls;
window.addEventListener('beforeunload',event=>{previewRequest?.abort();if(dirty){event.preventDefault();event.returnValue=''}});fileControls();load();
