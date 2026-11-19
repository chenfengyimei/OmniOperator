// 服务在线状态：左下角一行 + 顶部告警条 + NapCat 扫码弹窗。
// 启停不在这里——心跳调度/QQ 通道/NapCat 由守护默认拉起、崩了自动重启，
// 唯一需要人动手的是扫码登录，所以这里只剩"读状态"和"扫码"两件事。
import { $, esc } from './dom.js?v=20261103u6';
import { api } from './api.js?v=20261103u6';
import { openModal, closeModal, registerCloseHook } from './modals.js?v=20261103u6';

let QR_TIMER = null;
// 「本次忽略」只压住同一类告警 30 分钟：压太久了坏掉也看不见
const ALERT_MUTE_MS = 30 * 60 * 1000;
let ALERT_MUTED = { code: '', until: 0 };
let ALERT_CURRENT = '';

export async function refreshServiceHealth() {
  if (document.visibilityState === 'hidden') return; // 后台标签页不轮询
  try {
    const d = await api('/api/system/services/summary');
    renderStrip(d);
    renderAlert(d);
  } catch (e) {
    const text = $('#svc-strip-text');
    if (text) text.textContent = '服务状态读取失败';
  }
}

function worstAlert(d) {
  const alerts = d.alerts || [];
  return alerts.find((a) => a.level === 'error') || alerts.find((a) => a.level === 'warn') || null;
}

function renderStrip(d) {
  const dot = $('#svc-dot'),
    text = $('#svc-strip-text');
  if (!dot || !text) return;
  const top = worstAlert(d);
  const counts = d.counts || { online: 0, total: 0 };
  // 一个都没在跑时不能亮绿灯：没有告警只说明"没有该跑却没跑的"（比如 autostart 关着），
  // 绿点会让人以为一切正常。这种情况退成灰点，只陈述"0/N 在线"。
  const idle = counts.total > 0 && counts.online === 0;
  dot.className = 'svc-dot' + (top ? (top.level === 'error' ? ' bad' : ' warn') : idle ? '' : ' on');
  if (top) {
    text.textContent = `${top.text} · ${counts.online}/${counts.total} 在线`;
    return;
  }
  const heartbeat = (d.services || []).find((s) => s.name === 'omni-heartbeat') || {};
  const age = heartbeat.state_age_s;
  text.textContent =
    `${counts.online}/${counts.total} 在线` + (typeof age === 'number' ? ` · 心跳 ${Math.round(age)} 秒前` : '');
}

function renderAlert(d) {
  const bar = $('#svc-alert');
  if (!bar) return;
  const top = worstAlert(d);
  if (!top) {
    bar.classList.add('hidden');
    return;
  }
  if (ALERT_MUTED.code === top.code && Date.now() < ALERT_MUTED.until) {
    bar.classList.add('hidden');
    return;
  }
  bar.dataset.level = top.level;
  ALERT_CURRENT = top.code;
  $('#svc-alert-text').textContent = top.text;
  bar.classList.remove('hidden');
}

async function showDoctor() {
  $('#doctor-result').textContent = '诊断中…';
  try {
    const report = await api('/api/system/doctor');
    $('#doctor-result').textContent = report.checks.map((c) => `${c.state} · ${c.name}：${c.detail}`).join('\n');
  } catch (e) {
    $('#doctor-result').textContent = e.message;
  }
}
async function createBackup() {
  $('#doctor-result').textContent = '正在创建备份…';
  try {
    const report = await api('/api/system/backup', { method: 'POST' });
    $('#doctor-result').textContent =
      `备份已创建：${report.dest}\n共 ${report.entries.length} 项。配置密钥已脱敏，浏览器登录态未包含。`;
  } catch (e) {
    $('#doctor-result').textContent = e.message;
  }
}

function openQR() {
  openModal('qr-modal');
  loadQR();
  if (!QR_TIMER) QR_TIMER = setInterval(loadQR, 5000);
}
async function loadQR() {
  try {
    const d = await api('/api/system/napcat/qr');
    if (d.logged_in) {
      $('#qr-status').textContent = '✅ 已登录' + (d.uin ? '（' + esc(d.uin) + '）' : '');
      setTimeout(() => closeModal('qr-modal'), 1500);
      refreshServiceHealth();
      return;
    }
    if (d.available) {
      $('#qr-status').textContent = '等待扫码…';
      $('#qr-img').src = d.image;
    } else {
      $('#qr-status').textContent = '二维码还没生成（NapCat 可能正在启动，几秒后再看）';
    }
  } catch (e) {
    $('#qr-status').textContent = '获取失败：' + e.message;
  }
}
// 关闭扫码弹窗必须清轮询定时器：closeModal 的钩子 + 原生 Esc 的 close 事件都要接住
const clearQRTimer = () => {
  if (QR_TIMER) {
    clearInterval(QR_TIMER);
    QR_TIMER = null;
  }
};
registerCloseHook('qr-modal', clearQRTimer);
$('#qr-modal').addEventListener('close', clearQRTimer);

// data-action 注册表（main.js 合并后统一分发）
export const actions = {
  'services:alert-dismiss': () => {
    ALERT_MUTED = { code: ALERT_CURRENT, until: Date.now() + ALERT_MUTE_MS };
    $('#svc-alert')?.classList.add('hidden');
  },
  'services:qr-open': () => openQR(),
  'services:qr-refresh': () => loadQR(),
  'services:doctor': () => showDoctor(),
  'services:backup': () => createBackup(),
};
