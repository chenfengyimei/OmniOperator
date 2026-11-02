// 定时任务面板：任务列表/执行历史/待投递 outbox + 编辑表单（带 revision 乐观锁）。
import { $, esc } from './dom.js?v=20261102u2';
import { api } from './api.js?v=20261102u2';

let cronJobs = []; // 任务列表缓存（编辑表单要回填 revision 与全量字段）
export async function loadCron() {
  try {
    const d = await api('/api/system/cron');
    cronJobs = d.jobs;
    $('#cron-list').innerHTML = d.jobs.length
      ? d.jobs
          .map(
            (j) =>
              `<div class="cron-row"><span class="c-name">${esc(j.name)}</span>` +
              `<span class="c-sched">${esc(j.schedule)} · 下次 ${esc((j.next_run_at || '待排').slice(5, 16))}` +
              `${j.deliver_to_qq ? ' · 发到 QQ ' + esc(String(j.deliver_to_qq)) : ''}</span>` +
              `<span style="color:${j.enabled ? '#34d399' : 'var(--dim)'};font-size:11.5px">${j.enabled ? '在跑' : '已停'}</span>` +
              `<button class="btn" data-action="cron:edit" data-name="${esc(j.name)}">改一改</button>` +
              `<button class="btn" data-action="cron:toggle" data-name="${esc(j.name)}" data-enabled="${!j.enabled}">${j.enabled ? '先停掉' : '开起来'}</button>` +
              `<button class="btn" data-action="cron:delete" data-name="${esc(j.name)}">删掉</button></div>`,
          )
          .join('')
      : '<div class="empty">还没有定时任务。想让小鱼每天定点干活，上面填一下就行，或者直接跟它说。</div>';
  } catch (e) {
    $('#cron-list').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
  }
  try {
    const h = await api('/api/system/cron/history');
    $('#cron-history').innerHTML = h.history.length
      ? h.history
          .map(
            (x) =>
              `<div class="cron-row"><span class="c-name">${esc(x.job)}</span>` +
              `<span class="c-sched">${esc((x.started_at || '').slice(5, 16))} · 用了 ${x.duration_s} 秒 · ${x.ok ? '✅ 成了' : '❌ 没成'}</span>` +
              `<span style="color:var(--dim);font-size:11.5px;flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((x.result || '').slice(0, 60))}</span></div>`,
          )
          .join('')
      : '<div class="empty">还没跑过</div>';
  } catch (e) {
    $('#cron-history').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
  }
  try {
    const b = await api('/api/system/cron/outbox');
    $('#cron-outbox').innerHTML = b.items.length
      ? b.items
          .map(
            (x) =>
              `<div class="cron-row"><span class="c-name">${esc(x.job)} → QQ ${x.target_qq}</span>` +
              `<span class="c-sched">${outboxStateText(x)} · ${esc((x.updated_at || '').slice(5, 16))}${x.attempts ? ` · 已试 ${x.attempts} 次` : ''}</span>` +
              (['unknown', 'dead', 'discarded'].includes(x.status)
                ? `<button class="btn" data-action="cron:outbox-retry" data-id="${x.id}">重新排队</button>`
                : '') +
              (['pending', 'unknown', 'dead'].includes(x.status)
                ? `<button class="btn" data-action="cron:outbox-discard" data-id="${x.id}">丢弃</button>`
                : '') +
              `</div>` +
              (x.preview || x.last_error
                ? `<div class="hint" style="margin:2px 0 8px">${esc(x.preview || '')}${x.last_error ? ` <span style="color:#f87171">${esc(x.last_error.slice(0, 100))}</span>` : ''}</div>`
                : ''),
          )
          .join('')
      : '<div class="empty">还没有投递记录</div>';
  } catch (e) {
    $('#cron-outbox').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
  }
}
const outboxStateText = (x) =>
  x.status === 'pending'
    ? '<span style="color:#fbbf24">等 QQ 连上就发</span>'
    : x.status === 'sending'
      ? '<span style="color:#fbbf24">正在发，等对方确认</span>'
      : x.status === 'unknown'
        ? '<span style="color:#f87171">不知道发没发出去（不会自动重发）</span>'
        : x.status === 'dead'
          ? '<span style="color:#f87171">自动重发次数用完了</span>'
          : x.status === 'discarded'
            ? '<span style="color:var(--dim)">已丢弃</span>'
            : '<span style="color:#34d399">已经发出去了</span>';
async function outboxRetry(id) {
  if (
    !(await OmniUI.ask({
      title: '重新排队投递',
      message: '再发一次这条？\n\n如果上一次其实已经送到了，对方会收到两条。先跟他确认一下更稳。',
      confirmLabel: '重新排队',
      danger: true,
    }))
  )
    return;
  try {
    await api(`/api/system/cron/outbox/${id}/retry`, { method: 'POST' });
    loadCron();
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}
async function outboxDiscard(id) {
  if (
    !(await OmniUI.ask({
      title: '丢弃待投递',
      message: '丢弃这条待投递消息？丢弃后不会发送（任务结果仍保留在会话里）。',
      confirmLabel: '丢弃',
      danger: true,
    }))
  )
    return;
  try {
    await api(`/api/system/cron/outbox/${id}/discard`, { method: 'POST' });
    loadCron();
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}
let cronEditing = null; // 编辑中的任务（null=创建模式）；保存带 revision 防旧表单覆盖
function cronEdit(name) {
  const j = cronJobs.find((x) => x.name === name);
  if (!j) return;
  cronEditing = j;
  $('#cronf-name').value = j.name;
  $('#cronf-prompt').value = j.prompt || '';
  $('#cronf-daily').value = j.daily_at || '';
  $('#cronf-every').value = j.every_minutes || '';
  $('#cronf-qq').value = j.deliver_to_qq || '';
  $('#cronf-skills').value = (j.skills || []).join(',');
  $('#cronf-ctx').value = j.context_from || '';
  $('#cronf-zone').value = j.timezone || 'local';
  $('#cronf-age').value = j.context_max_age_minutes || 1440;
  $('#cron-create-btn').textContent = '保存修改';
  $('#cron-cancel-edit').classList.remove('hidden');
  $('#cronf-name').disabled = true; // 任务名是主键，编辑模式不给改
  $('#cronf-msg').textContent = '正在编辑 ' + name + '（保存时校验版本，别人改过会提示冲突）';
}
function cronCancelEdit() {
  cronEditing = null;
  ['cronf-name', 'cronf-prompt', 'cronf-daily', 'cronf-every', 'cronf-qq', 'cronf-skills', 'cronf-ctx'].forEach(
    (id) => ($('#' + id).value = ''),
  );
  $('#cronf-name').disabled = false;
  $('#cronf-zone').value = 'local';
  $('#cronf-age').value = 1440;
  $('#cron-create-btn').textContent = '创建任务';
  $('#cron-cancel-edit').classList.add('hidden');
  $('#cronf-msg').textContent = '';
}
async function createCron() {
  if (cronEditing) {
    // 编辑模式：全量提交，空输入显式 null（后端按"字段是否出现"区分不改/清除）
    const daily = $('#cronf-daily').value.trim(),
      every = parseInt($('#cronf-every').value);
    const deliver = $('#cronf-qq').value.trim(),
      ctx = $('#cronf-ctx').value.trim();
    const body = {
      name: cronEditing.name,
      expected_revision: cronEditing.revision,
      timezone: $('#cronf-zone').value.trim(),
      context_max_age_minutes: Number($('#cronf-age').value),
      prompt: $('#cronf-prompt').value.trim(),
      skills: $('#cronf-skills')
        .value.split(/[,，\s]+/)
        .filter(Boolean),
      enabled: cronEditing.enabled,
      daily_at: daily || null,
      every_minutes: every || null,
      deliver_to_qq: deliver ? parseInt(deliver) : null,
      context_from: ctx || null,
    };
    try {
      const d = await api('/api/system/cron/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      $('#cronf-msg').textContent =
        '✅ 已更新 ' + d.name + (d.enabled ? '（下次运行 ' + d.next_run_at + '）' : '（已停用）');
      cronCancelEdit();
      loadCron();
    } catch (e) {
      $('#cronf-msg').textContent = '❌ ' + e.message;
    }
    return;
  }
  const body = {
    name: $('#cronf-name').value.trim(),
    timezone: $('#cronf-zone').value.trim(),
    context_max_age_minutes: Number($('#cronf-age').value),
    prompt: $('#cronf-prompt').value.trim(),
    skills: $('#cronf-skills')
      .value.split(/[,，\s]+/)
      .filter(Boolean),
  };
  const daily = $('#cronf-daily').value.trim(),
    every = parseInt($('#cronf-every').value);
  const deliver = $('#cronf-qq').value.trim(),
    ctx = $('#cronf-ctx').value.trim();
  if (daily) body.daily_at = daily;
  if (every) body.every_minutes = every;
  if (deliver) body.deliver_to_qq = parseInt(deliver);
  if (ctx) body.context_from = ctx;
  try {
    const d = await api('/api/system/cron/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#cronf-msg').textContent = '✅ 已创建 ' + d.name + '（下次运行 ' + d.next_run_at + '）';
    loadCron();
  } catch (e) {
    $('#cronf-msg').textContent = '❌ ' + e.message;
  }
}
async function cronToggle(name, enabled) {
  try {
    await api('/api/system/cron/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, enabled }),
    });
    loadCron();
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}
async function cronDelete(name) {
  try {
    await api('/api/system/cron/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    loadCron();
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}

// data-action 注册表（main.js 合并后统一分发）
export const actions = {
  'cron:edit': (el) => cronEdit(el.dataset.name),
  'cron:toggle': (el) => cronToggle(el.dataset.name, el.dataset.enabled === 'true'),
  'cron:delete': (el) => cronDelete(el.dataset.name),
  'cron:create': () => createCron(),
  'cron:cancel-edit': () => cronCancelEdit(),
  'cron:outbox-retry': (el) => outboxRetry(Number(el.dataset.id)),
  'cron:outbox-discard': (el) => outboxDiscard(Number(el.dataset.id)),
};
