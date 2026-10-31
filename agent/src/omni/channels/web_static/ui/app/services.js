// 系统控制台：服务面板（启停/状态）+ NapCat 扫码弹窗（含轮询定时器生命周期）+ 诊断/备份。
import {$,esc} from './dom.js?v=20261031m15';
import {api} from './api.js?v=20261031m15';
import {openModal,closeModal,registerCloseHook} from './modals.js?v=20261031m15';

let QR_TIMER=null;

export async function loadServices(){
  try{
    const d=await api('/api/system/overview');
    const running=d.services.filter(s=>s.running).length;
    $('#service-summary').textContent=d.services.length?`${running}/${d.services.length} 运行中`:'无可管理服务';
    $('#services').innerHTML=d.services.length?d.services.map(s=>{
      // 状态词前置：detail 可能是「已登录 uin」这类与进程无关的持久化凭据信息，
      // 不带运行中/未启动前缀时会与摘要「0/3 运行中」互相打架
      const stateText=s.running?'运行中':'未启动';
      const detail=s.detail?`${stateText} · ${s.detail}`:stateText;
      const shortName=String(s.label||s.name).replace(/（[^）]*）/g,'').trim();
      return `<div class="svc">`+
        `<span class="svc-dot ${s.running?'on':(s.name==='napcat'&&!d.napcat.logged_in?'warn':'')}"></span>`+
        `<span class="svc-body"><span class="svc-name" title="${esc(s.label)}">${esc(shortName)}</span><span class="svc-detail" title="${esc(detail)}">${esc(detail)}</span></span>`+
        `<button class="svc-act ${s.running?'':'warn'}" ${s.running&&!s.can_stop?'disabled title="这个不是小鱼自己启动的，不敢替你关"':''} data-action="services:toggle" data-name="${esc(s.name)}" data-op="${s.running?'stop':'start'}">${s.running?'停止':'启动'}</button>`+
        (s.name==='napcat'&&!d.napcat.logged_in?`<button class="svc-act warn" data-action="services:qr-open">扫码</button>`:'')+
        `</div>`;
    }).join(''):'<div class="empty">没有可管理的服务</div>';
  }catch(e){$('#service-summary').textContent='读取失败';$('#services').innerHTML='<div class="empty">状态获取失败</div>'}
}
async function svcAction(name,action) {
  try{
    const body=(action==='start'&&name==='napcat')?{qq:''}:{};
    await api(`/api/system/services/${name}/${action}`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    setTimeout(loadServices,800);
  }catch(e){OmniUI.notify(e.message,'error')}
}
async function showDoctor() {
  $('#doctor-result').textContent='诊断中…';
  try{const report=await api('/api/system/doctor');
    $('#doctor-result').textContent=report.checks.map(c=>`${c.state} · ${c.name}：${c.detail}`).join('\n');
  }catch(e){$('#doctor-result').textContent=e.message;}
}
async function createBackup() {
  $('#doctor-result').textContent='正在创建备份…';
  try{const report=await api('/api/system/backup',{method:'POST'});
    $('#doctor-result').textContent=`备份已创建：${report.dest}\n共 ${report.entries.length} 项。配置密钥已脱敏，浏览器登录态未包含。`;
  }catch(e){$('#doctor-result').textContent=e.message;}
}
function openQR() {
  openModal('qr-modal');
  loadQR();
  if(!QR_TIMER)QR_TIMER=setInterval(loadQR,5000);
}
async function loadQR() {
  try{
    const d=await api('/api/system/napcat/qr');
    if(d.logged_in){
      $('#qr-status').textContent='✅ 已登录'+(d.uin?('（'+d.uin+'）'):'');
      setTimeout(()=>closeModal('qr-modal'),1500);
      loadServices();
      return;
    }
    if(d.available){$('#qr-status').textContent='等待扫码…';$('#qr-img').src=d.image}
    else{$('#qr-status').textContent='二维码还没生成（NapCat 可能未启动，先在服务面板启动它）'}
  }catch(e){$('#qr-status').textContent='获取失败：'+e.message}
}
// 关闭扫码弹窗必须清轮询定时器：closeModal 的钩子 + 原生 Esc 的 close 事件都要接住
const clearQRTimer=()=>{if(QR_TIMER){clearInterval(QR_TIMER);QR_TIMER=null}};
registerCloseHook('qr-modal',clearQRTimer);
$('#qr-modal').addEventListener('close',clearQRTimer);

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'services:toggle':el=>svcAction(el.dataset.name,el.dataset.op),
  'services:qr-open':()=>openQR(),
  'services:qr-refresh':()=>loadQR(),
  'services:doctor':()=>showDoctor(),
  'services:backup':()=>createBackup(),
};
