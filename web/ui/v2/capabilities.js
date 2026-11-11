export function mountCapabilities({ api, wrap, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  let read = 0;

  function reset() {
    read++;
    $('capabilities-list').replaceChildren();
    $('capabilities-status').textContent = '';
    $('capabilities-meta').textContent = '';
    $('capabilities-notes').textContent = '';
  }

  function render(report) {
    $('capabilities-meta').textContent =
      `格式 ${report.format} · 架构 ${report.schema_version} · API v${report.api_version} · ` +
      `平台验收 ${report.platform_acceptance} · V2 已验收 ${report.v2_accepted ? '是' : '否'}`;
    const list = $('capabilities-list');
    list.replaceChildren();
    for (const area of report.areas) {
      const card = document.createElement('article');
      card.dataset.area = area.id;
      const title = document.createElement('h3');
      title.textContent = area.name;
      const state = document.createElement('p');
      state.textContent = `状态 ${area.state === 'available' ? '可用' : '需注意'} · ${area.limits}`;
      const facts = document.createElement('p');
      const entries = Object.entries(area.facts);
      facts.textContent = entries.length
        ? entries.map(([key, value]) => `${key}=${value}`).join(' · ')
        : '（无可展示计数）';
      card.append(title, state, facts);
      list.append(card);
    }
    $('capabilities-notes').textContent = report.notes.map((note) => `• ${note}`).join('\n');
  }

  async function load() {
    const generation = getGeneration(),
      ticket = ++read;
    $('capabilities-status').textContent = '正在读取实例能力报告…';
    try {
      const report = await api('/capabilities');
      if (ticket !== read || generation !== getGeneration()) return;
      render(report);
      $('capabilities-status').textContent =
        `${report.counts.areas} 个能力域，${report.counts.attention} 个需注意；默认入口 ${report.entrypoint.toUpperCase()}。`;
    } catch (error) {
      if (ticket !== read) return;
      reset();
      $('capabilities-status').textContent = '能力报告读取失败，请刷新重试。';
      throw error;
    }
  }

  $('capabilities-refresh').addEventListener('click', wrap(load));
  return { reset, load };
}
