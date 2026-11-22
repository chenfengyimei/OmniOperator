/* ==========================================================================
   V2 人格成长只读页
   --------------------------------------------------------------------------
   职责：读取所有者已批准反思生成的派生成长文件，显示内容、指纹、条数与
        构建时间；文件缺失或账本已变化时只显示「需重建」，绝不展示旧内容。
   明确不做：不编辑、不注入模型上下文或系统前缀、不读来源正文、不在前端缓存
        旧内容用于回退。
   ========================================================================== */

export function mountPersona({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  let epoch = 0;

  const path = () => `/agents/${getAgent()}/persona-growth`;
  const target = () => ({ agent: getAgent(), epoch, generation: getGeneration() });
  const current = (t) => t.agent === getAgent() && t.epoch === epoch && t.generation === getGeneration();

  function render(mode, value = {}) {
    const ready = mode === 'ready';
    $('persona-growth-content').textContent = ready ? String(value.content ?? '') : '';
    $('persona-growth-content').hidden = !ready;
    $('persona-growth-meta').textContent = ready
      ? `sha256 ${String(value.sha256 || '').slice(0, 12)} · ${value.approved_count} 条已批准` +
        `${value.truncated ? ' · 已截断' : ''} · 构建于 ${value.built_at || '未知'}`
      : '';
    $('persona-growth-rebuild').hidden = ready;
    $('persona-growth-rebuild').disabled = false;
  }

  async function load() {
    if (!getAgent()) return;
    epoch++;
    const t = target();
    $('persona-growth-status').textContent = '正在核对派生文件与已批准账本…';
    $('persona-growth-refresh').disabled = true;
    try {
      const value = await api(path());
      if (!current(t)) return;
      render('ready', value);
      $('persona-growth-status').textContent = value.approved_count
        ? '已同步：内容与已批准账本逐字节一致。'
        : '已同步：暂无已批准的反思，派生文件为空态。';
    } catch (error) {
      if (!current(t)) return;
      if (error.status === 409) {
        render('stale');
        $('persona-growth-status').textContent =
          '派生文件已过期或缺失（撤回、修订或来源擦除已改变账本）。请显式重建；本页不会展示旧内容。';
        return;
      }
      throw error;
    } finally {
      if (current(t)) $('persona-growth-refresh').disabled = false;
    }
  }

  async function rebuild() {
    const t = target();
    $('persona-growth-rebuild').disabled = true;
    $('persona-growth-status').textContent = '正在从已批准账本重建派生文件…';
    try {
      const value = await api(path() + '/rebuild', { method: 'POST' });
      if (!current(t)) return;
      render('ready', value);
      $('persona-growth-status').textContent = '重建完成：内容与已批准账本逐字节一致。';
    } catch (error) {
      if (current(t)) $('persona-growth-rebuild').disabled = false;
      throw error;
    }
  }

  function reset() {
    epoch++;
    $('persona-growth-content').textContent = '';
    $('persona-growth-content').hidden = false;
    $('persona-growth-meta').textContent = '';
    $('persona-growth-status').textContent = '';
    $('persona-growth-refresh').disabled = false;
    $('persona-growth-rebuild').hidden = true;
    $('persona-growth-rebuild').disabled = false;
  }

  $('persona-growth-refresh').addEventListener('click', wrap(load));
  $('persona-growth-rebuild').addEventListener('click', wrap(rebuild));

  return { load, reset };
}
