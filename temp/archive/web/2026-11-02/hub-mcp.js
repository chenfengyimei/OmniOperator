// MCP 面板（D19）：状态纯只读；「查看工具」才触发真实发现。
import { $, esc } from './dom.js?v=20261102u1';
import { api } from './api.js?v=20261102u1';

const mcpStateText = (s) =>
  s.state === 'ready'
    ? '<span style="color:#34d399">已连上</span>'
    : s.state === 'starting'
      ? '<span style="color:#fbbf24">正在连接…</span>'
      : s.state === 'failed'
        ? '<span style="color:#f87171">连不上（已暂停）</span>'
        : s.state === 'closed'
          ? '<span style="color:var(--dim)">已断开</span>'
          : s.state === 'disabled'
            ? '<span style="color:var(--dim)">已关闭</span>'
            : '<span style="color:var(--dim)">未启动</span>';
// 只说"能不能放心用"，不提 schema/指纹这类实现细节
const mcpSchemaText = (s) =>
  s.schema_state === 'drifted'
    ? '<span style="color:#fbbf24" title="它提供的能力跟上次核对时不一样了">⚠ 能力有变化，建议重新核对</span>'
    : s.schema_state === 'confirmed'
      ? '<span style="color:#34d399">已核对，只读操作可自动放行</span>'
      : '<span style="color:var(--dim)">还没核对过，点「看看能做什么」开始</span>';
export async function loadMcp() {
  try {
    const d = await api('/api/mcp/overview');
    $('#mcp-list').innerHTML = d.servers.length
      ? d.servers
          .map(
            (s) =>
              `<div class="mcp-item">` +
              `<div class="cron-row"><span class="c-name">${esc(s.name)}</span>` +
              `<span class="c-sched">${mcpStateText(s)} · 提供 ${s.read_only_count} 项只读能力${s.allowed_count != null ? ' · 另有 ' + s.allowed_count + ' 项需确认' : ''}</span>` +
              (s.state === 'disabled'
                ? ''
                : `<button class="btn" data-action="mcp:tools" data-name="${esc(s.name)}">看看能做什么</button>`) +
              (s.state === 'failed' || s.state === 'closed'
                ? `<button class="btn" data-action="mcp:reset" data-name="${esc(s.name)}">重连一次</button>`
                : '') +
              (s.schema_state === 'drifted'
                ? `<button class="btn warn" data-action="mcp:confirm-schema" data-name="${esc(s.name)}" data-fingerprint="${esc(s.schema_fingerprint)}">重新核对</button>`
                : '') +
              `</div>` +
              `<div class="hint" style="margin-top:4px">${mcpSchemaText(s)}</div>` +
              (s.last_error
                ? `<div class="hint" style="color:#f87171;margin-top:2px">上次出错：${esc(s.last_error)}</div>`
                : '') +
              `<div id="mcp-tools-${esc(s.name)}"></div></div>`,
          )
          .join('')
      : '<div class="empty">还没有装扩展。<br>想要什么新本事，直接跟小鱼说——它会告诉你需要装哪一个，装好就会出现在这里。</div>';
  } catch (e) {
    $('#mcp-list').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
  }
}
async function mcpTools(name) {
  const box = document.getElementById('mcp-tools-' + name);
  if (!box) return;
  box.innerHTML = '<div class="hint">正在问它能做什么…（第一次会把它启动起来，可能要几秒）</div>';
  try {
    const d = await api('/api/mcp/' + encodeURIComponent(name) + '/tools');
    box.innerHTML =
      (d.drift
        ? `<div class="hint" style="color:#fbbf24;margin:4px 0">⚠ ${esc(d.drift)}</div><button class="btn warn" data-action="mcp:confirm-schema" data-name="${esc(name)}" data-fingerprint="${esc(d.fingerprint)}">我看过了，恢复自动放行</button>`
        : '') +
      (d.tools.length
        ? d.tools
            .map(
              (t) =>
                `<div class="mcp-cap"><span class="cap-dot" style="background:${t.approval === 'safe' ? '#34d399' : '#fbbf24'}"></span>` +
                `<div class="cap-main"><div class="cap-name">${esc(t.name)}</div>` +
                (t.description ? `<div class="cap-desc">${esc(String(t.description).slice(0, 120))}</div>` : '') +
                `</div>` +
                `<span class="cap-tag">${t.approval === 'safe' ? '不用问你' : '要问你'}</span></div>`,
            )
            .join('')
        : '<div class="hint">这个扩展没说明自己能做什么</div>');
    // 保留刚发现的能力清单供用户核对；确认后再刷新整个面板。
  } catch (e) {
    box.innerHTML = '<div class="hint" style="color:#f87171">' + esc(e.message) + '</div>';
  }
}
async function mcpConfirmSchema(name, fingerprint) {
  if (
    !(await OmniUI.ask({
      title: '重新核对 ' + name,
      message: '重新核对「' + name + '」？\n\n核对之后，它提供的那几项只看不改的操作会恢复自动放行，不用你一个个点。',
      confirmLabel: '重新核对',
    }))
  )
    return;
  try {
    await api('/api/mcp/' + encodeURIComponent(name) + '/confirm-schema', {
      method: 'POST',
      body: JSON.stringify({ fingerprint }),
    });
    loadMcp();
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}
async function mcpReset(name) {
  if (
    !(await OmniUI.ask({
      title: '重连 ' + name,
      message: '重新连一次「' + name + '」？\n\n不会改动任何设置，只是重新握个手。连不上的话再告诉我。',
      confirmLabel: '重连',
    }))
  )
    return;
  try {
    const d = await api('/api/mcp/' + encodeURIComponent(name) + '/reset', { method: 'POST' });
    loadMcp();
    OmniUI.notify('已经重连过了，点「看看能做什么」确认一下能不能用');
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}

// data-action 注册表（main.js 合并后统一分发）
export const actions = {
  'mcp:tools': (el) => mcpTools(el.dataset.name),
  'mcp:reset': (el) => mcpReset(el.dataset.name),
  'mcp:confirm-schema': (el) => mcpConfirmSchema(el.dataset.name, el.dataset.fingerprint),
};
