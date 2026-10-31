import {$} from '/ui/app/dom.js?v=20261031m16';
const UI=window.OmniUI;let serial='',token='',frozen=false,epoch=0,busy=false,live=false,detecting=false,previewReading=false;
const api=UI.request;
function status(text){$('#status').textContent=text}
function renderDeviceStats(){
  const cells=[
    ['设备连接',serial?'已选择':'未选择',serial?'smartphone':'phonelink_off',serial||'先检测并选择目标设备',!!serial],
    ['观察令牌',token?(frozen?'已锁帧':'有效'):'未取得',token?'verified':'shield',token?'单次有效，动作后失效':'刷新 UI 树或锁定操作帧',!!token],
    ['实时预览',live?'连接中':'未开启',live?'live_tv':'videocam_off',live?'预览不授权动作':'实时画面只读',live],
    ['操作状态',busy?'执行中':'空闲',busy?'hourglass_top':'check_circle',busy?'等待设备回读':'不会自动重试动作',!busy]
  ];
  const stats=document.getElementById('android-stats');
  if(stats)stats.innerHTML=cells.map(([label,value,icon,note,on])=>
    `<div class="metric-card ${on?'is-on':''}"><span class="metric-icon"><span class="material-icons">${icon}</span></span><div><span>${label}</span><b>${value}</b><small>${note}</small></div></div>`).join('');
  const meta=document.getElementById('device-meta');
  if(meta)meta.textContent=serial?`目标设备 · ${serial}`:'未选择设备';
}
function controls(){
  const ready=!!serial&&!busy&&!detecting,observed=ready&&!!token;
  for(const id of ['live','snapshot','observe','apps'])$('#'+id).disabled=!ready;
  for(const node of document.querySelectorAll('[data-key],[data-direction]'))node.disabled=!observed;
  for(const id of ['tap','long'])$('#'+id).disabled=!observed||$('#elements').value==='';
  $('#type').disabled=!observed||!$('#text').value;$('#launch').disabled=!observed||!$('#packages').value;
  $('#detect').disabled=detecting||busy;$('#devices').disabled=busy||detecting;$('#stop-live').disabled=!live;
  $('#observation-state').textContent=busy?'操作中…':token?(frozen?'完整帧观察 · 单次有效':'UI 树观察 · 单次有效'):live?'实时预览 · 只读':'未取得有效观察';
  renderDeviceStats();
}
function base(){if(!serial)throw Error('请先选择已授权设备');return '/api/android/devices/'+encodeURIComponent(serial)}
function invalidate(){token='';frozen=false;controls()}
function view(data){
  if(data.observation)token=data.observation;if(data.tree)$('#tree').textContent=data.tree;
  if(data.elements){$('#elements').replaceChildren();for(const item of data.elements){const o=UI.element('option','['+item.idx+'] '+(item.text||item.content_desc||item.resource_id||item.label||'元素'));o.value=item.idx;$('#elements').append(o)}}controls();
}
function stopPreview(){live=false;$('#screen').removeAttribute('src');$('#screen').hidden=true;controls()}
$('#devices').onchange=()=>{
  stopPreview();serial=$('#devices').value;epoch++;busy=false;invalidate();$('#tree').textContent='已切换设备，请重新观察。';$('#frame-info').textContent='坐标操作前先锁定当前帧。';
  $('#elements').replaceChildren();$('#packages').replaceChildren();status(serial?'已选择 '+serial+'；请先刷新树或锁定操作帧':'请选择已授权设备');controls();
};
$('#detect').onclick=async()=>{
  if(detecting||busy)return;detecting=true;controls();status('正在检测本地 ADB 设备…');
  try{
    const data=await api('/api/android/devices');$('#devices').replaceChildren();
    const empty=UI.element('option','请选择目标设备');empty.value='';$('#devices').append(empty);
    for(const device of data.devices){const option=UI.element('option',device.serial+' · '+device.state);option.value=device.serial;option.disabled=device.state!=='device';$('#devices').append(option)}
    const ready=data.devices.filter(row=>row.state==='device');if(ready.length===1)$('#devices').value=ready[0].serial;$('#devices').onchange();
    if(!ready.length)status('没有已授权设备；unauthorized 请在手机确认 USB 调试，offline 请检查连接后重新检测');
  }catch(error){status(error.message)}finally{detecting=false;controls()}
};
async function capture(pixels){
  if(busy)return;const version=epoch;try{
    const path=base();busy=true;stopPreview();invalidate();status(pixels?'正在锁定完整操作帧…':'正在读取 UI 树…');
    const data=await api(path+(pixels?'/snapshot':'/observe'));if(version!==epoch)return;view(data);
    if(pixels){$('#screen').src=data.image;$('#screen').hidden=false;frozen=true;$('#frame-info').textContent='锁定帧 · '+data.width+'×'+data.height+' · '+new Date().toLocaleTimeString()}
    status(pixels?'已锁帧，单次有效；画面变化时操作会拒绝':'UI 观察已更新，坐标操作需重新锁帧');
  }catch(error){if(version===epoch)status(error.message)}finally{if(version===epoch){busy=false;controls()}}
}
$('#snapshot').onclick=()=>capture(true);$('#observe').onclick=()=>capture(false);
async function action(kind,params={}){
  if(busy)return;if(!token){status('请先刷新树或锁定操作帧');return}
  const version=epoch,observation=token;try{
    const path=base();busy=true;invalidate();$('#frame-info').textContent='动作前的画面已失效；再次点击画面前请重新锁帧。';status('正在操作 '+serial+'，请等待回读…');
    const data=await api(path+'/actions',{method:'POST',body:JSON.stringify({action:kind,observation,...params})});if(version!==epoch)return;
    view(data);status(data.detail||'操作已返回，请核查回读结果');
  }catch(error){if(version===epoch)status(error.message+'；若已下发则效果可能未知，请重新观察，勿重复旧动作')}
  finally{if(version===epoch){busy=false;controls()}}
}
document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>action('key',{keycode:b.dataset.key}));
document.querySelectorAll('[data-direction]').forEach(b=>b.onclick=()=>action('swipe',{direction:b.dataset.direction}));
$('#tap').onclick=()=>{if($('#elements').value!=='')action('tap_element',{idx:Number($('#elements').value)})};
$('#long').onclick=()=>{if($('#elements').value!=='')action('long_press',{idx:Number($('#elements').value),duration_ms:600})};
$('#type').onclick=()=>{const text=$('#text').value;if(text)action('type_text',{text})};
$('#text').oninput=controls;$('#elements').onchange=controls;$('#packages').onchange=controls;
$('#apps').onclick=async()=>{
  if(busy)return;const version=epoch;try{
    const path=base();busy=true;controls();const data=await api(path+'/apps');if(version!==epoch)return;
    $('#packages').replaceChildren();const empty=UI.element('option','选择允许启动的应用');empty.value='';$('#packages').append(empty);
    for(const app of data.apps){const option=UI.element('option',app.package+(app.available===false?'（不可启动）':''));option.value=app.package;option.disabled=app.available===false;$('#packages').append(option)}
    if(!data.apps.length)status('应用白名单为空；请由用户在 android.allowed_apps 配置包名后重启');
  }catch(error){if(version===epoch)status(error.message)}finally{if(version===epoch){busy=false;controls()}}
};
$('#launch').onclick=async()=>{
  const pkg=$('#packages').value,device=serial,version=epoch,observation=token;if(!pkg||!observation)return;
  if(!await UI.ask({title:'启动白名单应用',message:'在设备 '+device+' 启动 '+pkg+'？',confirmLabel:'启动应用'}))return;
  if(version!==epoch||observation!==token){UI.notify('设备或观察已变化，请重新观察后操作');return}action('launch_app',{package:pkg});
};
$('#screen').onclick=e=>{
  if(busy)return;const image=e.target;if(!frozen||!token){status('请先锁定当前操作帧；实时预览不能授权坐标动作');return}
  const r=image.getBoundingClientRect(),scale=Math.min(r.width/image.naturalWidth,r.height/image.naturalHeight),w=image.naturalWidth*scale,h=image.naturalHeight*scale,x=e.clientX-r.left-(r.width-w)/2,y=e.clientY-r.top-(r.height-h)/2;
  if(x<0||y<0||x>=w||y>=h)return;action('tap_scaled',{x:Math.floor(x/scale),y:Math.floor(y/scale),stream_w:image.naturalWidth,stream_h:image.naturalHeight});
};
$('#live').onclick=()=>{
  if(busy)return;try{const path=base();invalidate();live=true;$('#screen').hidden=false;$('#screen').src=path+'/stream?'+Date.now();$('#frame-info').textContent='预览连接中…';status('实时预览不授权动作；点击画面前请锁定操作帧');controls()}catch(error){status(error.message)}
};
$('#stop-live').onclick=()=>{stopPreview();invalidate();status('预览已停止')};
$('#screen').onerror=()=>{if(live){stopPreview();status('预览中断，请检查设备后手动重新开启；不会重放动作')}else if(frozen){invalidate();status('完整帧加载失败，请重新锁帧')}};
const previewTimer=setInterval(async()=>{
  if(!live||!serial||previewReading)return;const version=epoch;previewReading=true;
  try{
    const data=await api(base()+'/stream-state');if(version!==epoch||!live)return;
    $('#frame-info').textContent='帧 '+data.seq+' · 画面年龄 '+(data.frame_age_s??'未知')+' 秒 · 采集 '+(data.duration_ms??'未知')+' ms'+(data.frame_age_s>2?' · 画面较旧，请重新锁帧':'');
    if(data.error){stopPreview();status('预览错误：'+data.error+'；检查设备后手动重新开启')}
  }catch(error){if(version===epoch&&live)status(error.message)}finally{previewReading=false}
},1000);
window.addEventListener('beforeunload',()=>{live=false;epoch++;clearInterval(previewTimer);$('#screen').removeAttribute('src')});controls();
