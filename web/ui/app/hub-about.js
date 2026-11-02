// 关于：版本、更新准备、人工确认和有界重连；远端说明始终按文本呈现。
import { $ } from './dom.js?v=20261102u2';
import { api } from './api.js?v=20261102u2';
import { openModal, closeModal } from './modals.js?v=20261102u2';

let snapshot = null,
  operation = null,
  timer = null,
  epoch = 0,
  reconnectUntil = 0;
const finished = new Set(['succeeded', 'failed', 'rolled_back', 'cancelled', 'recovery_required']);
const labels = {
  preparing: '正在准备更新',
  ready: '更新已准备，请确认安装',
  waiting_idle: '等待任务完成',
  stopping: '正在关闭服务',
  snapshotting: '正在保存恢复点',
  applying: '正在更新源码',
  environment: '正在准备运行环境',
  rehearsing: '正在演练数据升级',
  starting: '正在重启工作台',
  succeeded: '更新成功',
  rolling_back: '正在恢复旧版本',
  rolled_back: '已恢复升级前版本',
  cancelled: '本次更新已取消',
  failed: '准备更新失败',
  recovery_required: '恢复需要处理，请运行 start.bat',
};

function render() {
  if (!snapshot) return;
  const v = snapshot.version,
    c = snapshot.check;
  $('#about-version').textContent =
    `当前版本 ${v.version} · ${v.installation === 'git' ? 'Git 源码安装' : v.installation === 'archive' ? '源码包安装' : '安装基线待确认'}`;
  $('#about-source').textContent =
    `提交 ${v.commit?.slice(0, 12) || '未知'}${v.disk_changed ? ' · 磁盘源码与运行版本不同，请先重启' : ''}${v.dirty?.length ? ' · 有本地修改' : ''}`;
  $('#update-channel').value = snapshot.policy.channel;
  $('#update-auto').checked = snapshot.policy.auto_check;
  $('#update-status').textContent = c?.message || '尚未检查更新';
  const time = c?.checked_at ? new Date(c.checked_at * 1000).toLocaleString() : '';
  $('#update-details').textContent = [
    time && `上次检查：${time}`,
    c?.candidate && `可用版本：${c.candidate.version || c.candidate.commit.slice(0, 12)}`,
    c?.blockers?.length && '本地修改需要先保存',
  ]
    .filter(Boolean)
    .join(' · ');
  const blockers = c?.blockers?.length ? c.blockers : v.dirty || [];
  $('#update-blockers').hidden = !blockers.length;
  $('#update-blockers').textContent = blockers.length ? '以下本地修改需要先保存：\n' + blockers.join('\n') : '';
  $('#update-notes').textContent = c?.candidate?.notes || '';
  $('#update-prepare').disabled =
    c?.status !== 'available' || Boolean(c?.blockers?.length) || Boolean(operation && !finished.has(operation.phase));
  $('#update-install').disabled = operation?.phase !== 'ready' || !v.automatic_install;
  if (operation)
    $('#update-progress').textContent =
      `${labels[operation.phase] || operation.phase}：${operation.message || ''}${operation.error_message ? ' · 原因：' + operation.error_message : ''}`;
}

async function poll() {
  if (!operation || finished.has(operation.phase) || operation.phase === 'ready') return;
  const selected = operation.id,
    request = epoch;
  try {
    const result = await api('/api/system/updates/operations/' + selected);
    if (epoch !== request || operation?.id !== selected) return;
    if (result.phase === 'succeeded') {
      const version = await api('/api/system/version');
      if (version.starting) throw new Error('等待普通工作台启动');
      if (epoch !== request || operation?.id !== selected) return;
    }
    operation = result;
    render();
    if (result.phase === 'succeeded') {
      const token = 'omni-update-reloaded:' + result.id;
      if (!sessionStorage.getItem(token)) {
        sessionStorage.setItem(token, '1');
        location.reload();
        return;
      }
    }
    if (finished.has(result.phase) || result.phase === 'ready') return;
  } catch (e) {
    if (epoch !== request) return;
    $('#update-progress').textContent = '工作台正在重启或恢复，等待重新连接…';
    if (Date.now() > reconnectUntil) {
      $('#update-progress').textContent = '连接尚未恢复，请运行 start.bat 查看恢复结果。';
      return;
    }
  }
  timer = setTimeout(poll, 2000);
}

export async function loadAbout() {
  const request = ++epoch;
  clearTimeout(timer);
  const result = await api('/api/system/updates');
  if (request !== epoch) return;
  snapshot = result;
  operation = null;
  if (result.last_operation) operation = await api('/api/system/updates/operations/' + result.last_operation.id);
  if (operation?.phase === 'succeeded' && result.version.starting) operation = { ...operation, phase: 'starting' };
  render();
  if (operation && !finished.has(operation.phase) && operation.phase !== 'ready') {
    reconnectUntil = Date.now() + 30 * 60 * 1000;
    poll();
  }
}

export function stopAbout() {
  epoch++;
  clearTimeout(timer);
}

async function policy() {
  try {
    await api('/api/system/updates/policy', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: $('#update-channel').value, auto_check: $('#update-auto').checked }),
    });
    await loadAbout();
  } catch (e) {
    $('#update-status').textContent = e.message;
  }
}

async function check() {
  $('#update-check').disabled = true;
  try {
    await api('/api/system/updates/check', { method: 'POST' });
    await loadAbout();
  } catch (e) {
    $('#update-status').textContent = e.message;
  } finally {
    $('#update-check').disabled = false;
  }
}

async function prepare() {
  $('#update-prepare').disabled = true;
  try {
    operation = await api('/api/system/updates/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ candidate_id: snapshot.check.candidate.id }),
    });
    render();
    reconnectUntil = Date.now() + 30 * 60 * 1000;
    poll();
  } catch (e) {
    $('#update-progress').textContent = e.message;
    $('#update-prepare').disabled = false;
  }
}

async function install() {
  closeModal('update-confirm');
  $('#update-install').disabled = true;
  try {
    operation = await api('/api/system/updates/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation_id: operation.id, confirm: true }),
    });
    render();
    reconnectUntil = Date.now() + 30 * 60 * 1000;
    poll();
  } catch (e) {
    render();
    $('#update-progress').textContent = e.message;
  }
}

export const actions = {
  'updates:policy': () => policy(),
  'updates:check': () => check(),
  'updates:prepare': () => prepare(),
  'updates:confirm': () => {
    if (operation?.phase !== 'ready') return;
    $('#update-confirm-target').textContent =
      `目标版本：${operation.target.version || operation.target.commit.slice(0, 12)}`;
    openModal('update-confirm');
  },
  'updates:install': () => install(),
};
