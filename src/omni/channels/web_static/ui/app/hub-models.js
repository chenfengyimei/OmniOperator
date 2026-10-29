// 模型配置：槽位卡 + 三步向导（选供应商 → 选模型 → 填 Key）。
// 设计约定：
// - 供应商/模型用卡片而不是下拉框：一眼看清"哪家有 Key、哪个模型贵、哪个能看图"。
// - Key 不存浏览器，直接写后端 .env（/api/system/model-key），填完就能测。
// - getModelSlotValue() 仍是 saveHub 的唯一取值口，返回 `${prefix}/${model}`。
import {$,esc} from './dom.js?v=20261030d';
import {api} from './api.js?v=20261030d';

const MODEL_SLOTS=[
  {kind:'chat',title:'日常对话',desc:'平时跟它说话用这个，便宜量大',vision:false},
  {kind:'vision',title:'看截图',desc:'让它看懂图片和界面，要选支持看图的模型',vision:true},
  {kind:'review',title:'聊完复盘',desc:'留着不动就跟着日常对话走',vision:false,optional:true},
];
// color 只用于品牌色块；docs 是申请 Key 的入口；free 表示本机模型不需要 Key
const PROVIDERS=[
  {id:'deepseek',label:'DeepSeek',color:'#4D6BFE',envKey:'DEEPSEEK_API_KEY',
   docs:'https://platform.deepseek.com/api_keys',models:[
    {id:'deepseek-chat',label:'DeepSeek Chat',desc:'聊天够用、按量便宜',vision:false,tag:'推荐 · 便宜',tone:'green'},
    {id:'deepseek-reasoner',label:'DeepSeek Reasoner',desc:'会一步步推理，慢一点也贵一点',vision:false,tag:'推理 · 贵一点',tone:'amber'},
  ]},
  {id:'qwen',label:'通义千问（阿里）',color:'#7C3AED',envKey:'DASHSCOPE_API_KEY',litellmProvider:'dashscope',
   docs:'https://bailian.console.aliyun.com/',models:[
    {id:'qwen-max',label:'Qwen Max',desc:'综合最强的一档',vision:false},
    {id:'qwen-plus',label:'Qwen Plus',desc:'平衡档，日常够用',vision:false,tag:'便宜',tone:'green'},
    {id:'qwen-turbo',label:'Qwen Turbo',desc:'最便宜的档位',vision:false},
    {id:'qwen-vl-max',label:'Qwen VL Max',desc:'能看图的版本',vision:true,tag:'视觉',tone:'violet'},
  ]},
  {id:'moonshot',label:'Moonshot Kimi',color:'#2563EB',envKey:'MOONSHOT_API_KEY',
   docs:'https://platform.moonshot.cn/console/api-keys',models:[
    {id:'moonshot-v1-8k',label:'Kimi 8K',desc:'短上下文，最便宜',vision:false},
    {id:'moonshot-v1-32k',label:'Kimi 32K',desc:'长文档友好',vision:false},
  ]},
  {id:'anthropic',label:'Anthropic Claude',color:'#D97757',envKey:'ANTHROPIC_API_KEY',
   docs:'https://console.anthropic.com/settings/keys',models:[
    {id:'claude-sonnet-4-5',label:'Claude Sonnet 4.5',desc:'视觉和工具都强',vision:true,tag:'视觉',tone:'violet'},
    {id:'claude-3-5-sonnet',label:'Claude 3.5 Sonnet',desc:'上一代主力',vision:true},
    {id:'claude-3-haiku',label:'Claude 3 Haiku',desc:'轻快便宜',vision:false},
  ]},
  {id:'openai',label:'OpenAI GPT',color:'#10A37F',envKey:'OPENAI_API_KEY',
   docs:'https://platform.openai.com/api-keys',models:[
    {id:'gpt-4o',label:'GPT-4o',desc:'视觉 + 工具',vision:true,tag:'视觉',tone:'violet'},
    {id:'gpt-4o-mini',label:'GPT-4o mini',desc:'便宜档',vision:false,tag:'便宜',tone:'green'},
    {id:'o3-mini',label:'o3-mini',desc:'推理型',vision:false,tag:'推理',tone:'amber'},
  ]},
  {id:'ollama',label:'Ollama（本机跑）',color:'#94A3B8',envKey:'',litellmProvider:'ollama',free:true,models:[
    {id:'qwen2.5:7b',label:'Qwen2.5 7B',desc:'本机跑得动，不花钱',vision:false,tag:'免费',tone:'green'},
    {id:'qwen2.5:14b',label:'Qwen2.5 14B',desc:'更强，但更吃内存',vision:false},
    {id:'llama3.1:8b',label:'Llama 3.1 8B',desc:'英文场景友好',vision:false},
  ]},
  {id:'custom',label:'其它 / 中转站',color:'#3B4557',envKey:'',custom:true,models:[]},
];
// modelState[kind] = {providerId, modelId, modelStr, custom}
let modelState={};
let activeSlot='chat';
let envKeys={};      // KEY_NAME → {configured, masked}
let keyEyeOn=false;

function providerById(id){return PROVIDERS.find(p=>p.id===id)}
function slotByKind(kind){return MODEL_SLOTS.find(s=>s.kind===kind)}
function parseModelStr(s){
  const raw=(s||'').trim();
  if(!raw)return {providerId:'',modelId:'',custom:false};
  const parts=raw.split('/'), head=parts[0], tail=parts.slice(1).join('/');
  const prov=PROVIDERS.find(p=>!p.custom&&(p.id===head||(p.litellmProvider&&p.litellmProvider===head)));
  if(!prov)return {providerId:'',modelId:'',custom:true};
  const model=prov.models.find(m=>m.id===tail);
  return {providerId:prov.id,modelId:model?model.id:tail,custom:false};
}
export function renderModelSlots(currentModels){
  currentModels=currentModels||{};
  modelState={};
  MODEL_SLOTS.forEach(slot=>{
    const raw=(currentModels[slot.kind]||'').trim();
    modelState[slot.kind]=Object.assign({modelStr:raw},parseModelStr(raw));
  });
  if(!slotByKind(activeSlot))activeSlot=MODEL_SLOTS[0].kind;
  renderSlotCards();
  renderWizard();
}
function keyConfigured(prov){
  if(!prov)return false;
  if(prov.free)return true;
  return !!(prov.envKey&&envKeys[prov.envKey]&&envKeys[prov.envKey].configured);
}
function slotBadge(kind){
  const slot=slotByKind(kind), st=modelState[kind]||{}, prov=providerById(st.providerId);
  if(!prov)return {cls:'off',text:st.custom&&st.modelStr?'自定义':(slot.optional?'跟随对话':'未配置')};
  if(!st.modelStr)return {cls:'warn',text:'没选模型'};
  return keyConfigured(prov)?{cls:'ok',text:'已就绪'}:{cls:'warn',text:'待填 Key'};
}
function slotFoot(kind){
  const st=modelState[kind]||{}, prov=providerById(st.providerId);
  if(!prov)return {cls:'',text:st.custom&&st.modelStr?'自己填的模型':'点这张卡开始配置'};
  if(prov.free)return {cls:'ok',text:'本机运行，不需要 Key'};
  const k=envKeys[prov.envKey];
  if(k&&k.configured)return {cls:'ok',text:'Key 已填 '+k.masked};
  return {cls:'warn',text:'还差一个 Key'};
}
function renderSlotCards(){
  const box=$('#model-slots'); if(!box)return;
  box.innerHTML=MODEL_SLOTS.map(slot=>{
    const st=modelState[slot.kind]||{}, prov=providerById(st.providerId);
    const model=prov&&st.modelId?prov.models.find(m=>m.id===st.modelId):null;
    const badge=slotBadge(slot.kind), foot=slotFoot(slot.kind);
    const title=prov?prov.label:(st.custom&&st.modelStr?'自定义':'还没选供应商');
    const sub=model?`${model.label} · ${model.desc}`:(st.custom&&st.modelStr?st.modelStr:slot.desc);
    return `<div class="slot-card ${slot.kind===activeSlot?'active':''}" onclick="selectModelSlot('${slot.kind}')">`+
      `<div class="sc-head"><span class="sc-name">${esc(slot.title)}</span>`+
      `<span class="sc-badge ${badge.cls}">${badge.text}</span></div>`+
      `<div class="sc-provider">${esc(title)}</div>`+
      `<div class="sc-model">${esc(sub)}</div>`+
      `<div class="sc-foot ${foot.cls}">${esc(foot.text)}</div>`+
      `</div>`;
  }).join('');
}
const KEY_ICON='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="7.5" cy="15" r="3.6" stroke="#8b95a5" stroke-width="1.8"/><path d="M10.1 12.4L19 3.5M16.6 5.9L19.6 8.9M13.9 8.6L16.4 11.1" stroke="#8b95a5" stroke-width="1.8" stroke-linecap="round"/></svg>';

function renderWizard(){
  const box=$('#model-wizard'); if(!box)return;
  const slot=slotByKind(activeSlot)||MODEL_SLOTS[0], kind=slot.kind;
  const st=modelState[kind]||{}, prov=providerById(st.providerId);
  const ready=keyConfigured(prov)&&!!st.modelId;
  const cur=!prov?1:(!st.modelId?2:(ready?4:3));
  const stepCls=n=>n<cur?'done':(n===cur?'active':'');

  const provCards=PROVIDERS.map(p=>{
    let sub='', tone='';
    if(p.custom){sub='自己填地址和模型'}
    else if(p.free){sub=`${p.models.length} 个模型 · 不用 Key`}
    else{
      const k=envKeys[p.envKey];
      sub=`${p.models.length} 个模型 · `+(k&&k.configured?('Key 已填 '+k.masked):'还没填 Key');
      if(k&&k.configured)tone='ok';
    }
    return `<div class="prov-card ${p.id===st.providerId?'active':''}" onclick="pickProvider('${p.id}')">`+
      `<div class="prov-logo" style="background:${p.color}"></div>`+
      `<div class="prov-meta"><div class="prov-name">${esc(p.label)}</div>`+
      `<div class="prov-sub ${tone}">${esc(sub)}</div></div></div>`;
  }).join('');

  let modelHtml='';
  if(prov&&prov.custom){
    modelHtml=`<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">自己填模型</span>`+
      `<span class="ws-note">要写成「供应商/型号」，比如 openai/gpt-4o</span></div>`+
      `<div class="key-row"><div class="key-box"><input id="mk-custom" type="text" autocomplete="off" `+
      `placeholder="供应商/型号" value="${esc(st.modelStr||'')}" oninput="setCustomModel(this.value)"></div></div></div>`;
  }else if(prov){
    modelHtml=`<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">用哪个模型干活</span>`+
      `<span class="ws-note">选完就是这张卡以后用的模型</span></div><div class="model-list">`+
      prov.models.map(m=>{
        const tag=m.tag?`<span class="mc-tag ${m.tone||'gray'}">${esc(m.tag)}</span>`:'';
        return `<div class="model-card ${m.id===st.modelId?'active':''}" onclick="pickModel('${m.id}')">`+
          `<div class="mc-main"><div class="mc-name">${esc(m.label)}</div>`+
          `<div class="mc-desc">${esc(m.desc)}</div></div>${tag}</div>`;
      }).join('')+`</div></div>`;
  }

  let keyHtml='';
  if(prov&&prov.free){
    keyHtml=`<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">不需要 Key</span>`+
      `<span class="ws-note">本机 Ollama 直接跑，前提是本机已经拉过这个模型</span></div></div>`;
  }else if(prov&&!prov.custom){
    const k=envKeys[prov.envKey];
    const stateHtml=k&&k.configured
      ?`<span class="kn-ok">已填好 ${esc(k.masked)}</span>`
      :`<span class="kn-warn">还差一个 Key 才能用</span>`;
    const placeholder=k&&k.configured?`已填好 ${k.masked}，要换就重新粘贴`:`把 API Key 粘在这里`;
    keyHtml=`<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">把 Key 粘进来</span>`+
      `<span class="ws-note">只存在这台电脑上，保存后立刻生效，不会上传到任何地方</span></div>`+
      `<div class="key-row"><div class="key-box"><span class="kb-icon">${KEY_ICON}</span>`+
      `<input id="mk-input" type="${keyEyeOn?'text':'password'}" autocomplete="off" placeholder="${esc(placeholder)}">`+
      `<button class="kb-eye" onclick="toggleKeyEye()">${keyEyeOn?'隐藏':'显示'}</button></div>`+
      `<button class="key-btn" onclick="pasteModelKey()">粘贴</button>`+
      `<button class="key-btn primary" onclick="saveModelKey()">保存并生效</button></div>`+
      `<div class="key-note"><span id="mk-state">${stateHtml}</span>`+
      (prov.docs?`<a href="${prov.docs}" target="_blank" rel="noopener">没有 Key？去官网申请</a>`:'')+
      `<span>填一次就行，别的槽位选同一家会自动沿用</span></div></div>`;
  }

  box.innerHTML=`<div class="wiz">`+
    `<div class="wiz-head"><span class="wiz-title">配置向导 · ${esc(slot.title)}</span>`+
    `<span class="wiz-hint">${esc(slot.desc)}</span></div>`+
    `<div class="stepper">`+
      `<div class="step ${stepCls(1)}"><span class="step-no">1</span>选供应商</div>`+
      `<div class="step ${stepCls(2)}"><span class="step-no">2</span>选模型</div>`+
      `<div class="step ${stepCls(3)}"><span class="step-no">3</span>填 Key</div>`+
    `</div>`+
    `<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">选一个供应商</span>`+
    `<span class="ws-note">拿不准就先用 DeepSeek：便宜、够用、申请就能拿到 Key</span></div>`+
    `<div class="prov-grid">${provCards}</div></div>`+
    modelHtml+keyHtml+
    `<div class="model-footer"><span class="mf-label">当前在用</span>`+
    `<code id="mk-model">${esc(st.modelStr||'（还没选）')}</code><span style="flex:1"></span>`+
    `<button class="key-btn" onclick="testModelSlot()">测一下通不通</button>`+
    `<div class="mf-out" id="mk-out"></div>`+
    `<div class="key-note" style="margin-top:0"><span>选好之后点下面「保存模型设置」，改动才会真正生效。</span></div></div>`+
  `</div>`;
}

window.selectModelSlot=kind=>{
  activeSlot=kind; keyEyeOn=false;
  renderSlotCards(); renderWizard();
};
window.pickProvider=provId=>{
  const kind=activeSlot, slot=slotByKind(kind)||MODEL_SLOTS[0], st=modelState[kind]||{};
  const prov=providerById(provId);
  if(!prov)return;
  if(prov.custom){
    modelState[kind]={providerId:provId,modelId:'',modelStr:st.custom?(st.modelStr||''):'',custom:true};
  }else{
    const def=(slot.vision&&prov.models.find(m=>m.vision))||prov.models[0];
    const prefix=prov.litellmProvider||prov.id;
    modelState[kind]={providerId:provId,modelId:def?def.id:'',modelStr:def?`${prefix}/${def.id}`:'',custom:false};
  }
  keyEyeOn=false;
  renderSlotCards(); renderWizard();
};
window.pickModel=modelId=>{
  const kind=activeSlot, st=modelState[kind]||{}, prov=providerById(st.providerId);
  if(!prov)return;
  const prefix=prov.litellmProvider||prov.id;
  modelState[kind]=Object.assign({},st,{modelId,modelStr:`${prefix}/${modelId}`,custom:false});
  renderSlotCards(); renderWizard();
};
window.setCustomModel=value=>{
  const kind=activeSlot;
  modelState[kind]=Object.assign({},modelState[kind],{modelStr:(value||'').trim(),custom:true});
  renderSlotCards();
  const code=$('#mk-model'); if(code)code.textContent=modelState[kind].modelStr||'（未设置）';
};
window.toggleKeyEye=()=>{
  const input=$('#mk-input'); if(!input)return;
  keyEyeOn=!keyEyeOn;
  input.type=keyEyeOn?'text':'password';
  const btn=document.querySelector('.key-box .kb-eye');
  if(btn)btn.textContent=keyEyeOn?'隐藏':'显示';
};
function setKeyState(text,tone){
  const el=$('#mk-state'); if(!el)return;
  el.className=tone==='ok'?'kn-ok':(tone==='warn'?'kn-warn':'');
  el.textContent=text;
}
window.pasteModelKey=async()=>{
  const input=$('#mk-input'); if(!input)return;
  try{
    const text=await navigator.clipboard.readText();
    input.value=(text||'').trim();
    setKeyState('已粘贴 '+input.value.length+' 个字符，点「保存并生效」','ok');
  }catch(e){
    input.focus();
    setKeyState('浏览器不让读剪贴板，点输入框按 Ctrl+V 粘贴','warn');
  }
};
window.saveModelKey=async()=>{
  const kind=activeSlot, st=modelState[kind]||{}, prov=providerById(st.providerId);
  if(!prov||!prov.envKey){setKeyState('这家不需要 Key','warn');return}
  const input=$('#mk-input');
  const value=(input&&input.value||'').trim();
  if(!value){setKeyState('先粘一个 Key 进来','warn');return}
  setKeyState('保存中…','');
  try{
    const d=await api('/api/system/model-key',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:prov.envKey,value})});
    envKeys[prov.envKey]={configured:!!d.configured,masked:d.masked||''};
    if(input)input.value='';
    renderSlotCards(); renderWizard();
    setKeyState('已存好并立即生效：'+(d.masked||''),'ok');
  }catch(e){setKeyState('保存失败：'+e.message,'warn')}
};
export function getModelSlotValue(kind){
  const st=modelState[kind];
  return st&&st.modelStr?st.modelStr:'';
}
window.testModelSlot=async()=>{
  const kind=activeSlot, st=modelState[kind]||{};
  const out=$('#mk-out');
  if(!st.modelStr){
    if(out){out.className='mf-out err';out.textContent='先选好供应商和模型'}
    return;
  }
  if(out){out.className='mf-out';out.textContent='测试中…'}
  try{
    const d=await api('/api/system/model-test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:st.modelStr})});
    const cost=(typeof d.cost_micro==='number'&&d.cost_micro>0)
      ?`，花销 $${(d.cost_micro/1e6).toFixed(6)}`:'';
    if(out){out.className='mf-out ok';out.textContent='连通成功'+cost+'：'+(d.reply||'（空回复）')}
  }catch(e){
    if(out){out.className='mf-out err';out.textContent='不通：'+(e.message||'未知错误')}
  }
};
export async function loadEnvKeys(){
  try{
    const d=await api('/api/system/model-keys');
    envKeys=(d&&d.keys)||{};
  }catch(e){envKeys={}}
}
