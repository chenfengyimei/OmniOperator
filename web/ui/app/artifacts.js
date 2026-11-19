// 产物面板（D15）：浏览器/安卓截图落在 state.db，保留 7 天；聊天里的 [artifact:id] 内联图同源。
import { $, esc } from './dom.js?v=20261103u6';
import { api } from './api.js?v=20261103u6';
import { openModal } from './modals.js?v=20261103u6';
import { setTabBadge } from './tabs.js?v=20261103u6';

export async function loadArtifacts() {
  try {
    const d = await api('/api/artifacts?limit=40');
    setTabBadge('artifacts', d.artifacts.length);
    const total = d.artifacts.reduce((s, a) => s + a.size, 0);
    $('#artifacts-usage').innerHTML =
      `截图/导出产物 <b>${d.artifacts.length}</b> 张 · 约 <b>${(total / 1048576).toFixed(1)}</b>MB（保留 7 天，过期自动清理）`;
    $('#artifacts-grid').innerHTML = d.artifacts.length
      ? d.artifacts
          .map(
            (a) =>
              `<div class="art-card" data-action="artifacts:view" data-id="${esc(a.artifact_id)}"` +
              ` title="${esc(a.source || a.kind)} · ${esc(a.created_at || '')}">` +
              `<img loading="lazy" src="/api/artifacts/${esc(a.artifact_id)}/image" alt="产物">` +
              `<button class="art-del" data-action="artifacts:del" data-id="${esc(a.artifact_id)}">删</button>` +
              `<div class="art-meta">${a.width && a.height ? a.width + 'x' + a.height + ' · ' : ''}${esc((a.created_at || '').slice(5, 16))} · ${esc(a.source || a.kind)}</div></div>`,
          )
          .join('')
      : '<div class="empty" style="grid-column:1/-1;padding:20px;text-align:center;color:var(--dim)">还没有产物（浏览器/安卓截图后出现在这里）</div>';
  } catch (e) {
    $('#artifacts-grid').innerHTML = '<div class="empty" style="grid-column:1/-1">' + esc(e.message) + '</div>';
  }
}
function viewArtifact(id) {
  $('#img-view').src = '/api/artifacts/' + encodeURIComponent(id) + '/image';
  $('#img-title').textContent = '产物 ' + id;
  $('#img-hint').textContent = '点图外区域关闭；产物保留 7 天，过期自动清理。';
  openModal('img-modal');
}
async function delArtifact(id) {
  if (
    !(await OmniUI.ask({
      title: '删除产物',
      message: '删除这个产物？删除后聊天里的引用将不可再显示。',
      confirmLabel: '删除',
      danger: true,
    }))
  )
    return;
  try {
    await api('/api/artifacts/' + encodeURIComponent(id), { method: 'DELETE' });
    loadArtifacts();
  } catch (e) {
    OmniUI.notify(e.message, 'error');
  }
}

// data-action 注册表（main.js 合并后统一分发）
export const actions = {
  'artifacts:view': (el) => viewArtifact(el.dataset.id),
  'artifacts:del': (el) => delArtifact(el.dataset.id),
};
