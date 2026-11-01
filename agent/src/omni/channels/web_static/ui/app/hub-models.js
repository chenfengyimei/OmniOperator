// 模型配置：槽位卡 + 三步向导（选供应商 → 选模型 → 填 Key）+ 思考开关。
// 设计约定：
// - 供应商/模型用卡片而不是下拉框：一眼看清"哪家有 Key、哪个模型贵、哪个能看图"。
// - Key 不存浏览器，直接写后端 .env（/api/system/model-key），填完就能测。
// - getModelSlotValue() / getReasoningSlotValue() 是 saveHub 的唯一取值口。
// - **只要槽位里有字符串就按"已选"渲染**：认不出的型号显示原文，而不是退化成
//   "还没选"。以前认不出的型号会掉回槽位说明文案，看起来像没选，于是用户再点一次
//   供应商卡——而那次点击会把已存的型号重置成内置列表第一个，配置就这么被"选"没了。
import {$,esc} from './dom.js?v=20261031m19';
import {api} from './api.js?v=20261031m19';

const MODEL_SLOTS=[
  {kind:'chat',title:'日常对话',desc:'平时跟它说话用这个，便宜量大',vision:false},
  // 留空 = 跟随日常对话：DeepSeek V4.1 Flash 这类模型本身就能看图，
  // 硬要求单配一个视觉模型只会把人挡在门外。
  {kind:'vision',title:'看截图',desc:'让它看懂图片和界面；留空就跟随日常对话',vision:true,optional:true},
  {kind:'review',title:'聊完复盘',desc:'留着不动就跟着日常对话走',vision:false,optional:true},
  // 能力档位（D28）：每档可选自己的模型；留空都跟随「日常对话」。
  {kind:'router',title:'能力判定',desc:'判断这一回合用哪一档能力，只要输出一个 JSON，建议用便宜的小模型',vision:false,optional:true},
  {kind:'chat_mode',title:'聊天档位',desc:'纯聊天回合用的模型，留空跟随日常对话',vision:false,optional:true},
  {kind:'retrieval_mode',title:'检索档位',desc:'只读检索回合用的模型，留空跟随日常对话',vision:false,optional:true},
  {kind:'agent_mode',title:'完整档位',desc:'完整能力回合用的模型，留空跟随日常对话',vision:false,optional:true},
];
// 思考档位（D30）：auto 表示一个参数都不发，沿用供应商默认。
const REASONING_LEVELS=[
  {id:'auto',label:'跟随供应商',desc:'不额外要求，用供应商默认（DeepSeek V4 默认就开着思考）'},
  {id:'off',label:'关闭',desc:'直接答，最快最省'},
  {id:'low',label:'低',desc:'想一步就答'},
  {id:'medium',label:'中',desc:'想一下再答'},
  {id:'high',label:'高',desc:'认真想，慢一点也贵一点'},
  {id:'max',label:'最高',desc:'能想多久想多久，最慢最贵'},
];
// color 只用于品牌色块；docs 是申请 Key 的入口；free 表示本机模型不需要 Key；
// reasoning 表示这家听得懂「思考开关/强度」（网关只对这些供应商下发参数，见
// llm/gateway.py 的 reasoning_params）；retired 是已下线的旧型号（还能选，但不推荐）。
const PROVIDERS=[
  {id:'deepseek',label:'DeepSeek',color:'#4D6BFE',envKey:'DEEPSEEK_API_KEY',reasoning:true,
   docs:'https://platform.deepseek.com/api_keys',models:[
    {id:'deepseek-flash',label:'DeepSeek V4.1 Flash',desc:'1M 上下文、能看图，便宜量大',vision:true,tag:'推荐 · 便宜',tone:'green'},
    {id:'deepseek-v4-pro',label:'DeepSeek V4 Pro',desc:'更重的推理，贵几倍',vision:false,tag:'推理 · 贵一点',tone:'amber'},
    {id:'deepseek-chat',label:'DeepSeek Chat（旧名）',desc:'2026-07-24 已下线，请求会转到 Flash',vision:false,tag:'旧名',tone:'gray',retired:true},
    {id:'deepseek-reasoner',label:'DeepSeek Reasoner（旧名）',desc:'旧名字，现在由 Flash 的思考模式承接',vision:false,tag:'旧名',tone:'gray',retired:true},
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
    {id:'claude-opus-4-8',label:'Claude Opus 4.8',desc:'最强的一档，贵',vision:true,tag:'最强',tone:'violet'},
    {id:'claude-sonnet-4-6',label:'Claude Sonnet 4.6',desc:'速度和智能的平衡点',vision:true,tag:'视觉 · 推荐',tone:'green'},
    {id:'claude-haiku-4-5',label:'Claude Haiku 4.5',desc:'轻快便宜',vision:true},
  ]},
  {id:'openai',label:'OpenAI GPT',color:'#10A37F',envKey:'OPENAI_API_KEY',reasoning:true,
   docs:'https://platform.openai.com/api-keys',models:[
    {id:'gpt-5.5',label:'GPT-5.5',desc:'旗舰推理 + 工具，最贵',vision:true,tag:'最强',tone:'violet'},
    {id:'gpt-5.4',label:'GPT-5.4',desc:'便宜一档的旗舰',vision:true},
    {id:'gpt-5.4-mini',label:'GPT-5.4 mini',desc:'便宜档，够日常',vision:true,tag:'便宜',tone:'green'},
  ]},
  {id:'ollama',label:'Ollama（本机跑）',color:'#94A3B8',envKey:'',litellmProvider:'ollama',free:true,models:[
    {id:'qwen2.5:7b',label:'Qwen2.5 7B',desc:'本机跑得动，不花钱',vision:false,tag:'免费',tone:'green'},
    {id:'qwen2.5:14b',label:'Qwen2.5 14B',desc:'更强，但更吃内存',vision:false},
    {id:'llama3.1:8b',label:'Llama 3.1 8B',desc:'英文场景友好',vision:false},
  ]},
  {id:'custom',label:'其它 / 中转站',color:'#3B4557',envKey:'',custom:true,models:[]},
];
// modelState[kind] = {providerId, modelId, modelStr, custom, customOpen}
let modelState={};
// reasoningState[kind] = auto / off / low / medium / high / max
let reasoningState={};
let activeSlot='chat';
let envKeys={};      // KEY_NAME → {configured, masked}
let keyEyeOn=false;

function providerById(id){return PROVIDERS.find(p=>p.id===id)}
function slotByKind(kind){return MODEL_SLOTS.find(s=>s.kind===kind)}
function modelPrefix(prov){return prov.litellmProvider||prov.id}
function liveModels(prov){return prov.models.filter(m=>!m.retired)}
function isManualModel(prov,modelId){
  if(!prov||!modelId)return false;
  return !liveModels(prov).some(m=>m.id===modelId)&&!prov.models.some(m=>m.id===modelId&&m.retired);
}
function isRetiredModel(prov,modelId){
  return !!(prov&&prov.models.some(m=>m.id===modelId&&m.retired));
}
function parseModelStr(s){
  const raw=(s||'').trim();
  if(!raw)return {providerId:'',modelId:'',custom:false};
  const parts=raw.split('/'), head=parts[0], tail=parts.slice(1).join('/');
  const prov=PROVIDERS.find(p=>!p.custom&&(p.id===head||(p.litellmProvider&&p.litellmProvider===head)));
  // 供应商认得就认供应商（型号认不认都算"已选"）；供应商都不认得就整串留着当自定义。
  if(!prov)return {providerId:'',modelId:'',custom:true};
  return {providerId:prov.id,modelId:tail,custom:false};
}
export function renderModelSlots(currentModels,currentReasoning){
  currentModels=currentModels||{};
  currentReasoning=currentReasoning||{};
  modelState={};
  reasoningState={};
  MODEL_SLOTS.forEach(slot=>{
    const raw=(currentModels[slot.kind]||'').trim();
    const parsed=parseModelStr(raw);
    modelState[slot.kind]=Object.assign({modelStr:raw,customOpen:false},parsed);
    reasoningState[slot.kind]=currentReasoning[slot.kind]||'auto';
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
  if(!st.modelStr)return {cls:'off',text:slot.optional?'跟随对话':'没选模型'};
  if(isRetiredModel(prov,st.modelId))return {cls:'warn',text:'已下线型号'};
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
// 槽位卡副标题：认得型号就写型号名，认不得就写原始模型串——绝不退化成说明文案，
// 否则"已选"和"没选"在界面上长得一模一样。
function slotSubtitle(kind){
  const slot=slotByKind(kind), st=modelState[kind]||{}, prov=providerById(st.providerId);
  if(!st.modelStr)return slot.desc;
  if(!prov)return st.modelStr;
  const model=prov.models.find(m=>m.id===st.modelId);
  if(model)return `${model.label} · ${model.desc}`;
  return st.modelStr;
}
function renderSlotCards(){
  const box=$('#model-slots'); if(!box)return;
  box.innerHTML=MODEL_SLOTS.map(slot=>{
    const st=modelState[slot.kind]||{}, prov=providerById(st.providerId);
    const badge=slotBadge(slot.kind), foot=slotFoot(slot.kind);
    const title=prov?prov.label:(st.custom&&st.modelStr?'自定义':'还没选供应商');
    return `<div class="slot-card ${slot.kind===activeSlot?'active':''}" data-action="models:select-slot" data-kind="${slot.kind}">`+
      `<div class="sc-head"><span class="sc-name">${esc(slot.title)}</span>`+
      `<span class="sc-badge ${badge.cls}">${badge.text}</span></div>`+
      `<div class="sc-provider">${esc(title)}</div>`+
      `<div class="sc-model">${esc(slotSubtitle(slot.kind))}</div>`+
      `<div class="sc-foot ${foot.cls}">${esc(foot.text)}</div>`+
      `</div>`;
  }).join('');
}
const KEY_ICON='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="7.5" cy="15" r="3.6" stroke="#8b95a5" stroke-width="1.8"/><path d="M10.1 12.4L19 3.5M16.6 5.9L19.6 8.9M13.9 8.6L16.4 11.1" stroke="#8b95a5" stroke-width="1.8" stroke-linecap="round"/></svg>';

function renderReasoning(prov){
  if(!prov)return '';
  const cur=reasoningState[activeSlot]||'auto';
  const chosen=REASONING_LEVELS.find(l=>l.id===cur)||REASONING_LEVELS[0];
  const head=`<div class="wiz-sec-head"><span class="ws-title">思考开关</span>`;
  if(!prov.reasoning){
    return `<div class="wiz-sec">${head}<span class="ws-note">这家暂时不支持在这里调——`+
      `只有 DeepSeek / OpenAI 听得懂这个参数，对别人发只会被忽略或直接报错</span></div>`;
  }
  const btns=REASONING_LEVELS.map(l=>{
    // DeepSeek 官方映射表里没有 medium，按 high 服务；界面上直说，不装作有中间档。
    const note=(l.id==='medium'&&prov.id==='deepseek')?'（这家按「高」服务）':'';
    return `<button class="rsg-btn ${cur===l.id?'active':''}" data-action="models:set-reasoning" `+
      `data-level="${l.id}" title="${esc(l.desc)}">${esc(l.label)}${note}</button>`;
  }).join('');
  return `<div class="wiz-sec">${head}<span class="ws-note">${esc(chosen.desc)}</span></div>`+
    `<div class="rsg">${btns}</div>`+
    `<div class="key-note" style="margin-top:8px"><span>思考也算输出 token：档位越高越慢越贵。`+
    `换模型不会重置这一档。</span></div>`;
}

function renderWizard(){
  const box=$('#model-wizard'); if(!box)return;
  const slot=slotByKind(activeSlot)||MODEL_SLOTS[0], kind=slot.kind;
  const st=modelState[kind]||{}, prov=providerById(st.providerId);
  const ready=keyConfigured(prov)&&!!st.modelId;
  const cur=!prov?1:(!st.modelStr?2:(ready?4:3));
  const stepCls=n=>n<cur?'done':(n===cur?'active':'');

  const provCards=PROVIDERS.map(p=>{
    let sub='', tone='';
    if(p.custom){sub='自己填地址和模型'}
    else if(p.free){sub=`${liveModels(p).length} 个模型 · 不用 Key`}
    else{
      const k=envKeys[p.envKey];
      sub=`${liveModels(p).length} 个模型 · `+(k&&k.configured?('Key 已填 '+k.masked):'还没填 Key');
      if(k&&k.configured)tone='ok';
    }
    return `<div class="prov-card ${p.id===st.providerId?'active':''}" data-action="models:pick-provider" data-prov="${p.id}">`+
      `<div class="prov-logo" style="background:${p.color}"></div>`+
      `<div class="prov-meta"><div class="prov-name">${esc(p.label)}</div>`+
      `<div class="prov-sub ${tone}">${esc(sub)}</div></div></div>`;
  }).join('');

  let modelHtml='';
  if(prov&&prov.custom){
    modelHtml=`<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">自己填模型</span>`+
      `<span class="ws-note">要写成「供应商/型号」，比如 openai/gpt-4.1</span></div>`+
      `<div class="key-row"><div class="key-box"><input id="mk-custom" type="text" autocomplete="off" `+
      `placeholder="供应商/型号" value="${esc(st.modelStr||'')}" data-action="models:set-custom"></div></div></div>`;
  }else if(prov){
    const manual=isManualModel(prov,st.modelId);
    const openInput=manual||st.customOpen;
    const cards=prov.models.map(m=>{
      const tag=m.tag?`<span class="mc-tag ${m.tone||'gray'}">${esc(m.tag)}</span>`:'';
      const on=!openInput&&m.id===st.modelId;
      return `<div class="model-card ${on?'active':''} ${m.retired?'retired':''}" data-action="models:pick-model" data-model="${m.id}">`+
        `<div class="mc-main"><div class="mc-name">${esc(m.label)}</div>`+
        `<div class="mc-desc">${esc(m.desc)}</div></div>${tag}</div>`;
    }).join('');
    const manualCard=`<div class="model-card ${openInput?'active':''}" data-action="models:toggle-custom">`+
      `<div class="mc-main"><div class="mc-name">自己填型号</div>`+
      `<div class="mc-desc">列表里没有就手输，比如新出的型号、中转站私有名字</div></div>`+
      `<span class="mc-tag gray">手输</span></div>`;
    const input=openInput?`<div class="key-row" style="margin-top:8px"><div class="key-box">`+
      `<input id="mk-model-custom" type="text" autocomplete="off" `+
      `placeholder="只填型号，例如 deepseek-v4-pro" value="${esc(manual?st.modelId:'')}" `+
      `data-action="models:set-custom-model"></div></div>`:'';
    modelHtml=`<div class="wiz-sec"><div class="wiz-sec-head"><span class="ws-title">用哪个模型干活</span>`+
      `<span class="ws-note">选完就是这张卡以后用的模型</span></div><div class="model-list">`+
      cards+manualCard+`</div>`+input+`</div>`;
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
      `<button class="kb-eye" data-action="models:key-eye">${keyEyeOn?'隐藏':'显示'}</button></div>`+
      `<button class="btn" data-action="models:key-paste">粘贴</button>`+
      `<button class="btn primary" data-action="models:key-save">保存并生效</button></div>`+
      `<div class="key-note"><span id="mk-state">${stateHtml}</span>`+
      (prov.docs?`<a href="${prov.docs}" target="_blank" rel="noopener">没有 Key？去官网申请</a>`:'')+
      `<span>填一次就行，别的槽位选同一家会自动沿用</span></div></div>`;
  }

  // 可清空的槽位：留空 = 跟随日常对话（chat 本身不能清空，它是兜底）
  const canClear=!!(st.modelStr&&(slot.optional||slot.kind==='vision'));
  const clearBtn=canClear
    ?`<button class="btn" data-action="models:clear-slot">清空（跟随日常对话）</button>`:'';
  const retiredNote=isRetiredModel(prov,st.modelId)
    ?`<div class="key-note"><span class="kn-warn">这个型号已经下线，请求会转到新模型上；建议换成列表里的新名字。</span></div>`:'';

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
    modelHtml+renderReasoning(prov)+keyHtml+retiredNote+
    `<div class="model-footer"><span class="mf-label">当前在用</span>`+
    `<code id="mk-model">${esc(st.modelStr||'（还没选）')}</code><span style="flex:1"></span>`+
    clearBtn+
    `<button class="btn" data-action="models:test">测一下通不通</button>`+
    `<div class="mf-out" id="mk-out"></div>`+
    `<div class="key-note" style="margin-top:0"><span>选好之后点下面「保存模型设置」，改动才会真正生效。</span></div></div>`+
  `</div>`;
}

function selectModelSlot(kind) {
  activeSlot=kind; keyEyeOn=false;
  renderSlotCards(); renderWizard();
}
function pickProvider(provId) {
  const kind=activeSlot, slot=slotByKind(kind)||MODEL_SLOTS[0], st=modelState[kind]||{};
  const prov=providerById(provId);
  if(!prov)return;
  // 已经就是这家、而且已经选过型号：这一下只是"确认我看对了"，不能把型号重置成
  // 列表第一个——那是"选过又变没选"最隐蔽的一种成因。
  if(st.providerId===provId&&st.modelStr){renderSlotCards();renderWizard();return}
  if(prov.custom){
    modelState[kind]={providerId:provId,modelId:'',modelStr:st.custom?(st.modelStr||''):'',custom:true,customOpen:false};
  }else{
    const pool=liveModels(prov).length?liveModels(prov):prov.models;
    const def=(slot.vision&&pool.find(m=>m.vision))||pool[0];
    modelState[kind]={providerId:provId,modelId:def?def.id:'',modelStr:def?`${modelPrefix(prov)}/${def.id}`:'',custom:false,customOpen:false};
  }
  keyEyeOn=false;
  renderSlotCards(); renderWizard();
}
function pickModel(modelId) {
  const kind=activeSlot, st=modelState[kind]||{}, prov=providerById(st.providerId);
  if(!prov)return;
  modelState[kind]=Object.assign({},st,{modelId,modelStr:`${modelPrefix(prov)}/${modelId}`,custom:false,customOpen:false});
  renderSlotCards(); renderWizard();
}
function toggleCustomModel() {
  const st=modelState[activeSlot]||{};
  st.customOpen=!st.customOpen;
  renderSlotCards(); renderWizard();
  const input=$('#mk-model-custom');
  if(st.customOpen&&input)input.focus();
}
function setCustomModel(value) {
  let text=(value||'').trim();
  const st=modelState[activeSlot]||{}, prov=providerById(st.providerId);
  if(prov&&!prov.custom&&!text.includes('/'))text=`${modelPrefix(prov)}/${text}`;
  modelState[activeSlot]=Object.assign({},st,{modelStr:text,custom:!!(prov&&prov.custom)});
  renderSlotCards();
  const code=$('#mk-model'); if(code)code.textContent=text||'（未设置）';
}
function setCustomModelName(value) {
  // 供应商下"自己填型号"：只存型号部分，前缀由供应商决定（换供应商不串味）
  const st=modelState[activeSlot]||{}, prov=providerById(st.providerId);
  if(!prov)return;
  const tail=(value||'').trim();
  modelState[activeSlot]=Object.assign({},st,{modelId:tail,modelStr:tail?`${modelPrefix(prov)}/${tail}`:'',customOpen:true});
  renderSlotCards();
  const code=$('#mk-model'); if(code)code.textContent=modelState[activeSlot].modelStr||'（未设置）';
}
function clearSlot() {
  const kind=activeSlot, slot=slotByKind(kind);
  if(!slot||slot.kind==='chat')return;
  modelState[kind]={providerId:'',modelId:'',modelStr:'',custom:false,customOpen:false};
  renderSlotCards(); renderWizard();
}
function setReasoningLevel(level) {
  reasoningState[activeSlot]=level;
  renderWizard();
}
function toggleKeyEye() {
  const input=$('#mk-input'); if(!input)return;
  keyEyeOn=!keyEyeOn;
  input.type=keyEyeOn?'text':'password';
  const btn=document.querySelector('.key-box .kb-eye');
  if(btn)btn.textContent=keyEyeOn?'隐藏':'显示';
}
function setKeyState(text,tone){
  const el=$('#mk-state'); if(!el)return;
  el.className=tone==='ok'?'kn-ok':(tone==='warn'?'kn-warn':'');
  el.textContent=text;
}
async function pasteModelKey() {
  const input=$('#mk-input'); if(!input)return;
  try{
    const text=await navigator.clipboard.readText();
    input.value=(text||'').trim();
    setKeyState('已粘贴 '+input.value.length+' 个字符，点「保存并生效」','ok');
  }catch(e){
    input.focus();
    setKeyState('浏览器不让读剪贴板，点输入框按 Ctrl+V 粘贴','warn');
  }
}
async function saveModelKey() {
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
}
export function getModelSlotValue(kind){
  const st=modelState[kind];
  return st&&st.modelStr?st.modelStr:'';
}
export function getReasoningSlotValue(kind){
  return reasoningState[kind]||'auto';
}
async function testModelSlot() {
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
}
export async function loadEnvKeys(){
  try{
    const d=await api('/api/system/model-keys');
    envKeys=(d&&d.keys)||{};
  }catch(e){envKeys={}}
}
// 保存之后回读服务端再渲染：页面上的值必须等于文件里的值。
// 只重画模型面板（不整页刷新），免得把用户正在填的其它分区内容冲掉。
export async function refreshModelSlots(){
  const d=await api('/api/system/config');
  await loadEnvKeys();
  renderModelSlots(d.models,d.reasoning);
  return d;
}

// data-action 注册表（main.js 合并后统一分发；set-custom 是文本输入，走 input 事件）
export const actions={
  'models:select-slot':el=>selectModelSlot(el.dataset.kind),
  'models:pick-provider':el=>pickProvider(el.dataset.prov),
  'models:pick-model':el=>pickModel(el.dataset.model),
  'models:toggle-custom':()=>toggleCustomModel(),
  'models:set-custom':el=>setCustomModel(el.value),
  'models:set-custom-model':el=>setCustomModelName(el.value),
  'models:clear-slot':()=>clearSlot(),
  'models:set-reasoning':el=>setReasoningLevel(el.dataset.level),
  'models:key-eye':()=>toggleKeyEye(),
  'models:key-paste':()=>pasteModelKey(),
  'models:key-save':()=>saveModelKey(),
  'models:test':()=>testModelSlot(),
};
