// Generation fragments are temporary and never copied to saved chat or browser storage.
export function mountModelOutput({ api, getAgent, getGeneration }) {
  const panel = document.getElementById('model-live'),
    body = document.getElementById('model-live-text'),
    status = document.getElementById('model-live-status');
  let sequence = 0;
  function clear() {
    sequence++;
    panel.hidden = true;
    body.textContent = '';
    status.textContent = '';
  }
  async function refresh(runId) {
    const agent = getAgent(),
      generation = getGeneration(),
      reading = ++sequence;
    const value = await api(`/agents/${agent}/runs/${runId}/live-output`);
    if (agent !== getAgent() || generation !== getGeneration() || reading !== sequence) return;
    if (value.run_id !== runId || value.state !== 'streaming' || typeof value.text !== 'string') {
      clear();
      return;
    }
    panel.hidden = false;
    body.textContent = value.text;
    status.textContent = value.truncated
      ? '正在生成 · 仅显示片段末尾 · 尚未保存，最终以会话消息为准'
      : '正在生成 · 临时片段，尚未保存或确认完成';
  }
  return { refresh, clear };
}
