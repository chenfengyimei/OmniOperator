// 统一 API 出口：有 OmniUI（shared.js）就走它（带超时/错误文案适配），否则裸 fetch 兜底。
// /api/chat 特例不设超时：回合可能跑很久，由 run 轮询与取消机制管理预期。
export async function api(path, opts) {
  if (window.OmniUI) return OmniUI.request(path, { ...opts, ...(path === '/api/chat' ? { timeoutMs: 0 } : {}) });
  const r = await fetch(path, opts);
  if (!r.ok) {
    let d = '';
    try {
      d = (await r.json()).detail || '';
    } catch (e) {}
    throw new Error(d || r.statusText);
  }
  return r.json();
}
