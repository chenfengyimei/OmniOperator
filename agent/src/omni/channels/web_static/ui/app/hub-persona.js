// 人设预设方案（localStorage 持久化，可增删改；市场导入的整套设定也落在这里，
// 用 `market-<id>` 前缀区分来源，见 hub-market.js）。
import {$,esc} from './dom.js?v=20261101m21';

const PRESET_KEY='omni_persona_presets';
const SEED_PRESETS=[
  {id:'seed-xiaoyu',name:'小鱼',desc:'默认人设，稳重专业',prompt:
`你是 OmniOperator（昵称小鱼），一个运行在用户电脑上的个人智能体。
当前时间：{now}
风格：稳重、专业、简洁。先给结论再展开，不油滑、不废话。
能用工具核实的事先核实，不编造信息；危险操作先说明再做。
被问到偏好、约定、重要结论时主动记住（remember），相关提问先回顾记忆（recall）再回答。`},
  {id:'seed-dafeiyu',name:'大肥鱼',desc:'慵懒幽默的老朋友',prompt:
`你是 OmniOperator（昵称大肥鱼），一个慵懒又靠谱、爱开玩笑的个人智能体。
当前时间：{now}
风格：像老朋友一样轻松幽默，偶尔摸鱼式吐槽，但办事绝不含糊。
能用工具核实的事先核实，不编造信息；危险操作先说明再做。
被问到偏好、约定、重要结论时主动记住（remember），相关提问先回顾记忆（recall）再回答。`},
  {id:'seed-qwen',name:'Qwen 小姐',desc:'温柔耐心的贴心助理',prompt:
`你是 OmniOperator（昵称Qwen小姐），一个温柔、耐心、细心的智能助理。
当前时间：{now}
风格：语气亲切温和，多替用户着想，解释耐心，善用比喻把复杂讲简单。
能用工具核实的事先核实，不编造信息；危险操作先说明再做。
被问到偏好、约定、重要结论时主动记住（remember），相关提问先回顾记忆（recall）再回答。`},
  {id:'seed-worker',name:'办事员',desc:'高效直接的结果导向',prompt:
`你是 OmniOperator（昵称办事员），一个高效、直接、结果导向的工作型智能体。
当前时间：{now}
风格：三句话内给结论，直接给可执行方案；少寒暄、不绕弯。
能用工具核实的事先核实，不编造信息；危险操作先说明再做。
被问到偏好、约定、重要结论时主动记住（remember），相关提问先回顾记忆（recall）再回答。`},
];
export function loadPresets(){
  try{const raw=localStorage.getItem(PRESET_KEY);
    if(raw){const arr=JSON.parse(raw); if(Array.isArray(arr)&&arr.length)return arr;}
  }catch(e){}
  localStorage.setItem(PRESET_KEY,JSON.stringify(SEED_PRESETS));
  return JSON.parse(JSON.stringify(SEED_PRESETS));
}
export function savePresets(arr){localStorage.setItem(PRESET_KEY,JSON.stringify(arr))}
export function presetTip(msg,ok=true){
  const t=$('#preset-tip'); if(!t)return;
  t.textContent=msg; t.style.color=ok?'#34d399':'#f87171';
}
export function renderPersonaPresets(activeName){
  const box=$('#persona-presets'); if(!box)return;
  const presets=loadPresets();
  if(!presets.length){box.innerHTML='<div class="preset-empty">还没有预设方案，点上方「新建预设」或「存为预设」添加</div>';return}
  box.innerHTML=presets.map(p=>{
    const active=p.name===activeName?'active':'';
    return `<div class="preset-row ${active}">`+
      `<span class="p-name">${esc(p.name)}</span>`+
      `<span class="p-desc" title="${esc(p.desc)}">${esc(p.desc)}</span>`+
      `<span class="p-actions">`+
      `<button class="btn" data-action="persona:preset-apply" data-id="${p.id}">应用</button>`+
      `<button class="btn" data-action="persona:preset-edit" data-id="${p.id}">编辑</button>`+
      `<button class="btn danger" data-action="persona:preset-del" data-id="${p.id}">删除</button>`+
      `</span></div>`;
  }).join('');
}
function findPreset(id){return loadPresets().find(p=>p.id===id)}
function genPresetId(){return 'p-'+Date.now().toString(36)+Math.random().toString(36).slice(2,6)}
// 把一套设定填进表单（预设「应用」与市场「导入」共用这一个落点：
// 填进去 ≠ 生效，真正写配置仍由用户点「保存人设」）
export function applyPersonaFields(name,prompt){
  const nameInput=$('#cfg-persona-name'),promptInput=$('#cfg-persona-prompt');
  if(!nameInput||!promptInput)return;
  nameInput.value=name;
  promptInput.value=String(prompt).trim();
  renderPersonaPresets(name);
}
function applyPreset(id) {
  const p=findPreset(id); if(!p)return;
  applyPersonaFields(p.name,p.prompt);
  presetTip(`已填入「${p.name}」。点下面「保存人设」生效。`);
}
async function deletePreset(id) {
  const p=findPreset(id); if(!p)return;
  if(!await OmniUI.ask({title:'删除预设',message:`删除预设「${p.name}」？此操作不可撤销。`,confirmLabel:'删除',danger:true}))return;
  savePresets(loadPresets().filter(x=>x.id!==id));
  renderPersonaPresets($('#cfg-persona-name').value);
  presetTip('已删除「'+p.name+'」');
}
async function saveCurrentAsPreset() {
  const name=$('#cfg-persona-name').value.trim();
  const prompt=$('#cfg-persona-prompt').value.trim();
  if(!name||!prompt){presetTip('昵称和提示词都不能为空',false);return}
  const desc=(await OmniUI.ask({title:'预设描述',input:`${name}的自定义设定`,inputLabel:'一句描述',confirmLabel:'确定'}))||`${name}的自定义设定`;
  const presets=loadPresets();
  const existing=presets.find(p=>p.name===name);
  if(existing){
    if(!await OmniUI.ask({title:'覆盖同名预设',message:`已有同名预设「${name}」，是否覆盖？`,confirmLabel:'覆盖'}))return;
    existing.prompt=prompt; existing.desc=desc;
  }else{
    presets.push({id:genPresetId(),name,desc,prompt});
  }
  savePresets(presets);
  renderPersonaPresets(name);
  presetTip('已存为预设「'+name+'」');
}
function newPreset() { return openPresetEditor(null); }
function editPreset(id) {const p=findPreset(id); if(p)openPresetEditor(p)}
function openPresetEditor(preset){
  const isEdit=!!preset;
  const box=document.createElement('dialog');
  box.className='modal';
  box.innerHTML=`<div class="modal-box" style="width:min(620px,94vw)">
    <div class="modal-title"><span>${isEdit?'编辑预设':'新建预设'}</span><button class="modal-x" data-action="persona:dialog-close">✕</button></div>
    <div class="form-row"><label>名称<input type="text" id="pe-name" value="${esc(preset?preset.name:'')}"></label></div>
    <div class="form-row"><label>描述<input type="text" id="pe-desc" value="${esc(preset?preset.desc:'')}"></label></div>
    <div class="form-row"><label>行为设定（{now} 会被替换为当前时间）<textarea id="pe-prompt" style="min-height:220px;font:12px/1.6 Consolas,monospace;white-space:pre-wrap">${esc(preset?preset.prompt:'')}</textarea></label></div>
    <div id="pe-msg" class="test-out" style="min-height:1.2em"></div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="hub-save" style="padding:7px 20px;font-size:13px" data-action="persona:preset-confirm" data-id="${esc(preset?preset.id:'')}" data-edit="${isEdit}">${isEdit?'保存修改':'创建'}</button>
      <button class="hub-close" style="padding:7px 16px;font-size:13px" data-action="persona:dialog-close">取消</button>
    </div></div>`;
  document.body.appendChild(box);
  box.showModal();
}
function confirmPresetEdit(id,isEdit) {
  const name=$('#pe-name').value.trim();
  const desc=$('#pe-desc').value.trim();
  const prompt=$('#pe-prompt').value.trim();
  const msg=$('#pe-msg');
  if(!name){msg.textContent='名称不能为空';msg.style.color='#f87171';return}
  if(!prompt){msg.textContent='行为设定不能为空';msg.style.color='#f87171';return}
  const presets=loadPresets();
  if(!isEdit){
    if(presets.some(p=>p.name===name)){msg.textContent='已有同名预设，请改名';msg.style.color='#f87171';return}
    presets.push({id:genPresetId(),name,desc:desc||`${name}的自定义设定`,prompt});
  }else{
    const p=presets.find(x=>x.id===id); if(!p){msg.textContent='找不到原预设';msg.style.color='#f87171';return}
    if(presets.some(x=>x.name===name&&x.id!==id)){msg.textContent='已有同名预设，请改名';msg.style.color='#f87171';return}
    p.name=name; p.desc=desc||`${name}的自定义设定`; p.prompt=prompt;
  }
  savePresets(presets);
  document.querySelector('dialog.modal[open]')?.close();
  renderPersonaPresets($('#cfg-persona-name').value);
  presetTip(isEdit?'已更新「'+name+'」':'已创建「'+name+'」');
}

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'persona:preset-apply':el=>applyPreset(el.dataset.id),
  'persona:preset-edit':el=>editPreset(el.dataset.id),
  'persona:preset-del':el=>deletePreset(el.dataset.id),
  'persona:preset-new':()=>newPreset(),
  'persona:preset-save-current':()=>saveCurrentAsPreset(),
  'persona:dialog-close':el=>el.closest('dialog').close(),
  'persona:preset-confirm':el=>confirmPresetEdit(el.dataset.id,el.dataset.edit==='true'),
};
