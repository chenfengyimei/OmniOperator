// 运行进度可视化：等待回复期间轮询本会话进行中的 run（D09 端点，只读）。
// 历史挂链把每个 run 的调用链卡片挂到它开始后出现的第一条助手消息上（事件持久化在服务端，随时回放）。
import { $, esc, modeLabel } from './dom.js?v=20261103u5';
import { api } from './api.js?v=20261103u5';
import { S } from './state.js?v=20261103u5';
import { selectSession } from './sessions.js?v=20261103u5';

let runWatch = null; // {timer, runId, seq, polling}：轮询器；chat 返回/出错即停

// ---------- 运行中间过程（原型 scaffolds）：一次"问—想—做—答"的调用链卡片 ----------
// model_start/model_end 合并成一行；run_started 不单列。可展开的行后跟 .scaf-out（CSS 相邻选择器）
const scafDur = (ms) => (ms == null ? '' : ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms');
const scafNote = (icon, text, cls = 'done') =>
  `<div class="scaf ${cls}"><span class="scaf-glyph"><span class="material-icons">${icon}</span></span><span class="scaf-label">${esc(text)}</span></div>`;
function scaffoldHTML(events, opts = {}) {
  const live = !!opts.live,
    openSet = opts.openSet || null,
    rows = [];
  // 开关行：链的折叠/展开由容器 .scaffolds.open 控制（运行中默认展开，历史默认折叠）
  const steps = events.filter((e) => e.kind === 'model_end').length;
  const tools = events.filter((e) => e.kind === 'tool').length;
  rows.push(
    `<div class="scaf-toggle" data-action="runs:toggle-chain"><span class="material-icons">${live ? 'manage_history' : 'manage_search'}</span><b>调用过程</b><span class="scaf-toggle-meta">· ${steps || '…'} 步${tools ? ` · ${tools} 个工具` : ''}</span><span class="chev"><span class="material-icons">chevron_right</span></span></div>`,
  );
  for (let i = 0; i < events.length; i++) {
    const ev = events[i],
      d = ev.data || {};
    if (ev.kind === 'model_start') {
      const end = events.slice(i + 1).find((e) => e.kind === 'model_end');
      const info = end ? end.data || {} : {};
      const out = [
        '第 ' + (d.step || '?') + ' 步模型调用' + (info.model ? ' · ' + String(info.model).split('/').pop() : ''),
      ];
      if (info.prompt_tokens != null || info.completion_tokens != null)
        out.push('输入 ' + (info.prompt_tokens || 0) + ' / 输出 ' + (info.completion_tokens || 0) + ' 字');
      if (info.cost_micro != null) out.push('$' + (info.cost_micro / 1e6).toFixed(4));
      if (Array.isArray(info.tool_calls) && info.tool_calls.length) out.push('决定调用：' + info.tool_calls.join('、'));
      const open = openSet && openSet.has('m' + ev.seq) ? ' open' : '';
      rows.push(
        `<div class="scaf ${live && !end ? 'run' : 'done'}${open}" data-k="m${ev.seq}" data-action="runs:toggle-scaf"><span class="scaf-glyph"><span class="material-icons">${live && !end ? 'progress_activity' : 'check'}</span></span><span class="scaf-label">模型调用<b>第 ${d.step || '?'} 步</b></span><span class="scaf-arg">${end ? '返回 ' + (info.text_chars || 0) + ' 字' : '思考中…'}</span><span class="scaf-chev"><span class="material-icons">chevron_right</span></span></div>`,
      );
      if (end || !live) rows.push(`<div class="scaf-out">${esc(out.join('\n'))}</div>`);
      continue;
    }
    if (ev.kind === 'model_end') continue; // 已并入模型调用行
    if (ev.kind === 'tool') {
      const st = d.status || 'ok';
      const glyph =
        st === 'ok'
          ? 'check'
          : st === 'error'
            ? 'close'
            : st === 'denied'
              ? 'block'
              : st === 'cancelled'
                ? 'do_not_disturb'
                : 'help';
      const out = ['参数：' + (d.args || '（无）')];
      if (d.excerpt) out.push('结果：' + d.excerpt);
      if (d.verification) out.push('回读校验：' + d.verification);
      if (d.error_code) out.push('错误码：' + d.error_code);
      if (d.side_effect_state && d.side_effect_state !== 'none') out.push('副作用状态：' + d.side_effect_state);
      if (d.artifacts && d.artifacts.length) out.push('产物：' + d.artifacts.join('、'));
      const open = openSet && openSet.has('t' + ev.seq) ? ' open' : '';
      rows.push(
        `<div class="scaf ${st === 'ok' ? 'done' : 'fail'}${open}" data-k="t${ev.seq}" data-action="runs:toggle-scaf"><span class="scaf-glyph"><span class="material-icons">${glyph}</span></span><span class="scaf-label"><b>${esc(d.name || '?')}</b></span><span class="scaf-arg">${esc(d.args || '')}</span><span class="scaf-time">${scafDur(d.elapsed_ms)}</span><span class="scaf-chev"><span class="material-icons">chevron_right</span></span></div>`,
      );
      rows.push(`<div class="scaf-out">${esc(out.join('\n'))}</div>`);
      continue;
    }
    switch (ev.kind) {
      case 'context_summary':
        rows.push(scafNote('compress', '旧历史已压缩为摘要'));
        break;
      case 'mode_selected':
        rows.push(
          scafNote(
            'tune',
            '能力档位：' +
              modeLabel(d.selected) +
              (d.source === 'rule'
                ? '（规则判定）'
                : d.source === 'router'
                  ? '（模型判定）'
                  : d.source === 'manual'
                    ? '（指定）'
                    : ''),
          ),
        );
        break;
      case 'mode_upgraded':
        rows.push(scafNote('upgrade', '已升档到' + modeLabel(d.selected) + (d.reason ? '：' + d.reason : '')));
        break;
      case 'image_attached':
        rows.push(scafNote('image', `截图已发给模型（${d.width || '?'}x${d.height || '?'}）`));
        break;
      case 'final_call':
        rows.push(scafNote('flag', '步数预算用尽，收敛最终回答'));
        break;
      case 'budget':
        rows.push(
          `<div class="scaf done"><span class="scaf-glyph"><span class="material-icons">savings</span></span><span class="scaf-label">本回合花费</span><span class="scaf-arg">${d.cost_micro != null ? '$' + (d.cost_micro / 1e6).toFixed(4) : '已记账'}</span></div>`,
        );
        break;
      case 'answer':
        rows.push(scafNote('task_alt', '回答完成'));
        break;
      case 'cancelled':
        rows.push(scafNote('do_not_disturb_on', '已取消'));
        break;
      case 'failed':
        rows.push(
          `<div class="scaf fail"><span class="scaf-glyph"><span class="material-icons">error</span></span><span class="scaf-label">回合失败</span><span class="scaf-arg">${esc(d.error || '')}</span></div>`,
        );
        break;
      case 'budget_blocked':
        rows.push(
          `<div class="scaf fail"><span class="scaf-glyph"><span class="material-icons">block</span></span><span class="scaf-label">预算拦截</span><span class="scaf-arg">${esc(d.message || '')}</span></div>`,
        );
        break;
      case 'run_started':
        break;
      default:
        rows.push(scafNote('fiber_manual_record', ev.kind));
        break;
    }
  }
  return rows.join('');
}
function toggleScaf(el) {
  return el.classList.toggle('open');
}
function toggleChain(el) {
  const box = el.closest('.scaffolds');
  if (!box) return;
  const open = box.classList.toggle('open');
  // 收起整条链时把内部行一并复位：否则下次展开会带出上一次留下的展开态，
  // 看起来像「点一下调用过程，里面的工具行也跟着全展开」。
  if (!open) box.querySelectorAll('.scaf.open').forEach((row) => row.classList.remove('open'));
}

const runChainCache = new Map(); // run_id -> events：切会话不重复拉
export async function attachHistoryRuns(sid, version) {
  try {
    const d = await api(`/api/runs?session_id=${sid}&limit=12`);
    if (sid !== S.currentSession || version !== S.messagesVersion) return;
    const runs = [...d.runs].sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
    const msgs = [...document.querySelectorAll('#messages .msg.assistant')];
    const claimed = new Set();
    for (const r of runs) {
      const target = msgs.find((el) => !claimed.has(el) && el.dataset.at && el.dataset.at >= (r.created_at || ''));
      if (!target) continue;
      claimed.add(target);
      let events = runChainCache.get(r.run_id);
      if (!events) {
        const ev = await api(`/api/runs/${r.run_id}/events?after=0`);
        events = ev.events;
        runChainCache.set(r.run_id, events);
        if (sid !== S.currentSession || version !== S.messagesVersion) return;
      }
      const steps = events.filter((e) => e.kind === 'model_end').length;
      const secs =
        r.created_at && r.ended_at
          ? Math.max(0, Math.round((new Date(r.ended_at) - new Date(r.created_at)) / 1000))
          : null;
      const stat =
        { done: '', failed: ' · ❌ 失败', cancelled: ' · ✋ 已取消', interrupted: ' · ⚠ 中断' }[r.status] || '';
      if (steps) {
        const meta = target.querySelector('.a-meta');
        if (meta) meta.textContent += ` · ${steps} 步${secs != null ? ' · ' + secs + 's' : ''}${stat}`;
      }
      if (events.length) {
        const box = document.createElement('div');
        box.className = 'scaffolds';
        box.innerHTML = scaffoldHTML(events);
        target.insertBefore(box, target.querySelector('.bubble'));
      }
    }
  } catch (e) {
    /* 调用链是附加信息，拉不到不影响正文 */
  }
}

export function syncActiveRun() {
  let panel = $('#active-run');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'active-run';
    panel.setAttribute('role', 'status');
    const text = document.createElement('span'),
      view = document.createElement('button'),
      stop = document.createElement('button');
    text.className = 'active-run-text';
    view.textContent = '查看会话';
    stop.textContent = '停止此回合';
    stop.className = 'ui-danger';
    view.onclick = () => {
      if (runWatch) selectSession(runWatch.sid);
    };
    stop.onclick = () => cancelCurrentRun();
    panel.append(text, view, stop);
    $('#composer').before(panel);
  }
  panel.hidden = !runWatch || !S.busy;
  if (panel.hidden) return;
  panel.querySelector('span').textContent = '会话 #' + runWatch.sid + ' 正在运行，切换页面区域不会停止回合';
  panel.querySelector('button:last-child').disabled = !runWatch.runId;
}
export function stopRunWatch() {
  if (runWatch && runWatch.timer) clearInterval(runWatch.timer);
  runWatch = null;
  syncActiveRun();
  const stop = $('#run-stop');
  if (stop) stop.classList.add('hidden');
}

export async function pollRunProgress(watch) {
  if (runWatch !== watch || watch.polling) return;
  watch.polling = true;
  try {
    const d = await api(`/api/runs?session_id=${watch.sid}&limit=5`);
    if (runWatch !== watch) return;
    const active = d.runs.find((r) => ['queued', 'running', 'waiting_approval', 'cancelling'].includes(r.status));
    if (!active) return;
    watch.runId = active.run_id;
    syncActiveRun();
    if (S.currentSession !== watch.sid) return;
    const feed = $('#run-feed'),
      stop = $('#run-stop');
    if (!feed) return;
    stop?.classList.remove('hidden');
    const ev = await api(`/api/runs/${active.run_id}/events?after=${watch.seq}`);
    if (runWatch !== watch || S.currentSession !== watch.sid || !feed.isConnected) return;
    if (ev.events.length) {
      watch.events.push(...ev.events);
      watch.seq = ev.events[ev.events.length - 1].seq;
      feed.classList.remove('hidden');
      // 实况链每秒整体重建：先把用户展开过的行记下来，重建后回放——
      // 否则展开状态每秒被清掉，看起来就是"卡住、只显示一半"
      const openSet = new Set([...feed.querySelectorAll('.scaf.open')].map((el) => el.dataset.k).filter(Boolean));
      feed.innerHTML = scaffoldHTML(watch.events, { live: true, openSet });
      // 用户正开着某行看细节时不再强制滚到底，别把人正在读的内容顶走
      if (!openSet.size) feed.scrollTop = feed.scrollHeight;
    }
  } catch (e) {
    /* 进度读取不重放模型请求。 */
  } finally {
    watch.polling = false;
  }
}

// 发送回合时启动轮询器（chat.js 调用）：定时器句柄留在模块内，外部只拿 watch 引用
export function startRunWatch(sid) {
  const watch = { sid, runId: null, seq: 0, events: [], polling: false };
  runWatch = watch;
  watch.timer = setInterval(() => pollRunProgress(watch), 1000);
  pollRunProgress(watch);
  syncActiveRun();
}

async function cancelCurrentRun() {
  const watch = runWatch,
    id = watch?.runId;
  if (!id) return;
  if (
    !(await OmniUI.ask({
      title: '停止会话 #' + watch.sid + ' 的回合',
      message: '模型请求返回后会停下；已发生的工具副作用不会撤销。',
      confirmLabel: '停止回合',
      danger: true,
    }))
  )
    return;
  if (runWatch !== watch) return;
  try {
    await api(`/api/runs/${id}/cancel`, { method: 'POST' });
    OmniUI.notify('已提交停止请求，请等待最终状态');
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}

// data-action 注册表（main.js 合并后统一分发）
export const actions = {
  'runs:toggle-chain': (el) => toggleChain(el),
  'runs:toggle-scaf': (el) => toggleScaf(el),
  'runs:cancel': () => cancelCurrentRun(),
};
