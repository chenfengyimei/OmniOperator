// Generation fragments are temporary and never copied to saved chat or browser storage.
// 状态提示行已删（所有者反馈：实时调用链已表达进度，重复文案是噪音）。
export function mountModelOutput({ api, getAgent, getGeneration }) {
  const panel = document.getElementById('model-live'),
    body = document.getElementById('model-live-text');
  let sequence = 0;
  function clear() {
    sequence++;
    panel.hidden = true;
    body.textContent = '';
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
  }
  return { refresh, clear };
}
