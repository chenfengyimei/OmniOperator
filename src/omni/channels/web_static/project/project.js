'use strict';
const $ = selector => document.querySelector(selector);
const state = {project:null, tools:[], extensions:null, view:'modules', query:'', category:''};
const views = {
  modules:['浏览整个 Agent','按职责查找模块、源码、测试和说明文档。'],
  apis:['接口与内部调用','对话、模型、工具、终端和扩展共用哪些入口。'],
  tools:['当前工具能力','本进程已注册的真实工具、参数与审批要求。'],
  extensions:['插件与扩展','区分内置工具、Python 插件、技能数据和进程外 MCP。']
};
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el; }
function badge(text, cls='') { return node('span',text,'badge '+cls); }
function button(text, action, cls='outline') { const el=node('button',text,cls); el.addEventListener('click',action); return el; }
function matches(value) { return !state.query || JSON.stringify(value).toLowerCase().includes(state.query); }
function empty(text='没有匹配项，试试其他搜索词或分类。') { $('#content').append(node('div',text,'empty')); }
async function get(url) {
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store'});
  if(response.status===401) throw new Error('请先返回对话工作台，用配对链接登录后再打开此页。');
  if(!response.ok) throw new Error(`读取失败（${response.status}），请稍后刷新。`);
  return response.json();
}
function show(title, content) { $('#detail-title').textContent=title; $('#detail-body').replaceChildren(content); if(!$('#detail').open)$('#detail').showModal(); }
async function copy(text) { try { await navigator.clipboard.writeText(text); $('#status').textContent='已复制到剪贴板。'; } catch { show('复制内容',node('pre',text)); } }
async function documentView(name) { try { const data=await get('/api/workbench/docs/'+encodeURIComponent(name)); show(name,node('pre',data.text)); } catch(error) { $('#status').textContent=error.message; } }
function locationText(source) { return source.path ? `${source.path}:${source.line}` : `${source.source}:${source.line}`; }
function moduleDetail(module) {
  const box=node('div'); box.append(node('p',module.purpose));
  const actions=node('div',undefined,'actions');
  module.docs.forEach(path=>actions.append(button(path.split('/').pop(),()=>documentView(path.split('/').pop())))); box.append(actions);
  if(module.dependencies.length){box.append(node('h3','内部模块依赖','section-title'));const dependencies=node('div',undefined,'actions');module.dependencies.forEach(id=>{const item=state.project.modules.find(row=>row.id===id);if(item)dependencies.append(button(item.title,()=>moduleDetail(item)));});box.append(dependencies);}
  box.append(node('h3','源码与公共符号','section-title'));
  module.files.forEach(path=>{
    const file=state.project.files.find(item=>item.path===path); const row=node('details',undefined,'file');
    row.append(node('summary',path)); row.append(node('p',file.summary));
    row.append(button('复制路径',()=>copy(path)));
    if(file.symbols.length)row.append(node('pre',file.symbols.map(symbol=>`${symbol.line}  ${symbol.kind} ${symbol.name}${symbol.parameters?'('+symbol.parameters.join(', ')+')':''}`).join('\n')));
    if(file.imports.length)row.append(node('pre','内部依赖\n'+file.imports.join('\n'))); box.append(row);
  });
  box.append(node('h3','对应测试','section-title'),node('pre',module.tests.join('\n')||'通过关联模块的集成测试验证。'));
  show(module.title,box);
}
function renderModules() {
  const rows=state.project.modules.filter(item=>(!state.category||item.category===state.category)&&matches({...item, symbols:state.project.files.filter(file=>file.module===item.id).flatMap(file=>file.symbols.map(symbol=>symbol.name))}));
  if(!rows.length)return empty(); const grid=node('div',undefined,'cards');
  rows.forEach(item=>{ const card=button('',()=>moduleDetail(item),'card'); card.append(badge(item.category),node('h3',item.title),node('p',item.purpose),node('div',`${item.files.length} 文件 · ${item.lines.toLocaleString()} 行`,'meta')); grid.append(card); }); $('#content').append(grid);
}
function apiDetail(api) {
  const box=node('div'); box.append(badge(api.method),badge(api.scope==='community'?'插件社区服务器':'工作台'),node('pre',`${api.method} ${api.path}\n${api.handler}\n${api.source}:${api.line}`));
  const annotations=api.annotations||{}; const models=state.project.request_models.filter(model=>Object.values(annotations).some(type=>type.includes(model.name)));
  if(models.length)models.forEach(model=>box.append(node('h3',model.name,'section-title'),node('pre',JSON.stringify(model.fields,null,2))));
  else box.append(node('pre',JSON.stringify(annotations,null,2)));
  box.append(node('p',api.scope==='community'?'这是独立插件社区服务的接口。':'此接口复用工作台配对身份；写操作还受 Origin/CSRF 和各领域权限检查。'));
  box.append(button('复制接口定位',()=>copy(`${api.method} ${api.path}\n${api.source}:${api.line}`))); show(api.method+' '+api.path,box);
}
function renderApis() {
  const flows=state.project.flows.filter(matches);
  if(!state.category||state.category==='flows')flows.forEach(flow=>{const row=node('section',undefined,'flow'); row.append(node('h3',flow.title),node('p',flow.summary)); const steps=node('div',undefined,'steps'); flow.steps.forEach((step,index)=>{if(index)steps.append(node('span','→','arrow')); const b=button(step.symbol,()=>copy(`${step.source}:${step.line}`),'step'); b.title=`${step.source}:${step.line}`;steps.append(b);});row.append(steps);$('#content').append(row);});
  const rows=state.project.endpoints.filter(item=>(!state.category||state.category===item.scope)&&matches(item));
  if(!rows.length) {if(!$('#content').children.length)empty();return;}
  $('#content').append(node('h3',`接口目录 · ${rows.length}`,'section-title'));
  const wrap=node('div',undefined,'table-wrap'), table=node('table'), head=node('thead'), header=node('tr'); ['方法','路径','处理函数 / 源码','服务'].forEach(text=>header.append(node('th',text)));head.append(header);table.append(head);const body=node('tbody');
  rows.forEach(api=>{const row=node('tr'), method=node('td'), path=node('td'), handler=node('td');method.append(badge(api.method));path.append(button(api.path,()=>apiDetail(api),'link-button'));handler.append(node('div',api.handler),node('small',`${api.source}:${api.line}`));row.append(method,path,handler,node('td',api.scope==='community'?'插件社区':'工作台'));body.append(row);});table.append(body);wrap.append(table);$('#content').append(wrap);
}
function toolDetail(tool) {
  const box=node('div'); box.append(node('p',tool.description),badge(tool.toolset),badge(tool.risk,tool.risk),badge(tool.requires_human?'必须人工批准':'按审批策略执行'),badge(tool.read_only?'可并发只读':'保持执行顺序'));
  box.append(node('h3','参数 JSON Schema','section-title'),node('pre',JSON.stringify(tool.parameters,null,2)));
  const contract=JSON.stringify({type:'function',function:{name:tool.name,parameters:tool.parameters}},null,2);
  box.append(button('复制调用规格',()=>copy(contract)));
  if(tool.source)box.append(node('pre',locationText(tool.source)));else box.append(node('p','动态注册工具：源码不在静态内置声明中，以所属插件或扩展为准。'));
  show(tool.name,box);
}
function renderTools() {
  $('#content').append(node('div','此处展示当前进程已注册的能力。SAFE 表示审批等级，read_only 才表示可并发只读；本页查看规格不会执行工具。','note'));
  const rows=state.tools.filter(tool=>(!state.category||tool.toolset===state.category)&&matches(tool)); if(!rows.length)return empty();
  const grid=node('div',undefined,'cards');rows.forEach(tool=>{const card=button('',()=>toolDetail(tool),'card');card.append(badge(tool.toolset),badge(tool.risk,tool.risk),node('h3',tool.name),node('p',tool.description),node('div',tool.requires_human?'人工确认后执行':tool.read_only?'只读 · 支持同批并发':'按顺序执行','meta'));grid.append(card);});$('#content').append(grid);
}
function renderExtensions() {
  const grid=node('div',undefined,'cards'); state.extensions.contracts.filter(item=>(!state.category||item.kind===state.category)&&matches(item)).forEach(item=>{const card=node('section',undefined,'card');card.append(badge(item.kind),node('h3',item.title),node('p',item.entry),node('div',item.action,'meta'),button('复制接入说明',()=>copy(`${item.title}\n${item.entry}\n${item.action}`)));grid.append(card);});if(grid.children.length)$('#content').append(grid);
  const plugins=state.extensions.plugins.filter(matches), pending=state.extensions.pending.filter(matches);
  $('#content').append(node('h3','本机插件状态','section-title'));
  if(!plugins.length&&!pending.length)$('#content').append(node('div','当前没有已安装或待激活插件。使用对话中的 plugin_install 安装到隔离区，再审阅并人工批准激活。','empty'));
  [...plugins.map(item=>({...item,status:item.enabled?'已启用':'已停用'})),...pending.map(item=>({...item,status:'隔离区 · 待人工激活'}))].forEach(item=>{const box=node('section',undefined,'flow');box.append(badge(item.status),node('h3',`${item.name||item.id} · ${item.version||''}`),node('p',item.description||item.id));if(item.tools)box.append(node('pre',item.tools.join('\n')));$('#content').append(box);});
  $('#content').append(node('div','插件是人工信任的进程内代码；技能是提示和参考数据；MCP 是受配置与权限限制的外部进程。本页只查询状态，激活和发布继续走现有人工确认链。','note'));
}
function render() {
  if(!state.project)return;
  $('#view-title').textContent=views[state.view][0];$('#view-description').textContent=views[state.view][1];
  $('#content').replaceChildren();({modules:renderModules,apis:renderApis,tools:renderTools,extensions:renderExtensions})[state.view]();
}
function choose(view) {
  state.view=view;state.category='';document.querySelectorAll('[data-view]').forEach(el=>el.setAttribute('aria-selected',String(el.dataset.view===view)));
  const options=view==='modules'?[...new Set(state.project?.modules.map(item=>item.category)||[])].map(value=>[value,value]):view==='tools'?[...new Set(state.tools.map(item=>item.toolset))].map(value=>[value,value]):view==='apis'?[['flows','调用链'],['workbench','工作台接口'],['community','插件社区接口']]:state.extensions?.contracts.map(item=>[item.kind,item.title])||[];
  $('#category').replaceChildren(new Option('全部分类',''),...options.map(([value,label])=>new Option(label,value)));render();
}
async function load() {
  $('#refresh').disabled=true;$('#status').textContent='正在读取项目索引与本进程能力…';
  try {
    const [project,tools,extensions]=await Promise.all([get('/api/workbench/project'),get('/api/workbench/tools'),get('/api/workbench/extensions')]);
    state.project=project;state.tools=tools.items;state.extensions=extensions;
    $('#stats').replaceChildren(...[[project.modules.length,'职责模块'],[project.files.length,'源码与界面文件'],[project.endpoints.length,'声明接口'],[tools.items.length,'当前注册工具']].map(([value,label])=>{const box=node('div',undefined,'stat');box.append(node('strong',String(value)),node('span',label));return box;}));
    $('#status').textContent=project.warnings.length?'索引有待修复定位：'+project.warnings.join('；'):'索引就绪 · '+project.source_sha256.slice(0,12)+' · 工具状态为当前工作台进程';choose(state.view);
  } catch(error) { $('#status').textContent=error.message; }
  finally { $('#refresh').disabled=false; }
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>choose(el.dataset.view)));
document.querySelectorAll('[data-doc]').forEach(el=>el.addEventListener('click',()=>documentView(el.dataset.doc)));
$('#search').addEventListener('input',event=>{state.query=event.target.value.trim().toLowerCase();render();});
$('#category').addEventListener('change',event=>{state.category=event.target.value;render();});
$('#close-detail').addEventListener('click',()=>$('#detail').close());$('#detail').addEventListener('click',event=>{if(event.target===$('#detail'))$('#detail').close();});
$('#refresh').addEventListener('click',load);load();
