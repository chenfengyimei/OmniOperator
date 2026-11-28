/* 能力报告：分类列表 + 详情 Inspector（只读；缺字段显示未知，不编造） */
export function mountCapabilities({ api, wrap, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  let read = 0;
  let selected = '';

  function reset() {
    read++;
    $('capabilities-list').replaceChildren();
    $('capabilities-status').textContent = '';
    $('capabilities-meta').textContent = '';
    $('capabilities-notes').textContent = '';
    selected = '';
  }

  function stateLabel(area) {
    return area.state === 'available' ? '可用' : '需注意';
  }

  function factRows(area) {
    const entries = Object.entries(area.facts || {});
    if (!entries.length) return null;
    const dl = document.createElement('dl');
    dl.className = 'cap-facts';
    for (const [key, value] of entries) {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = value == null ? '未知' : String(value);
      dl.append(dt, dd);
    }
    return dl;
  }

  function renderDetail(area) {
    const detail = $('capabilities-list').querySelector('.cap-detail');
    if (!detail) return;
    detail.replaceChildren();
    if (!area) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = '选择左侧能力域查看详情。';
      detail.append(empty);
      return;
    }
    const head = document.createElement('div');
    head.className = 'cap-detail-head';
    const title = document.createElement('h3');
    title.textContent = area.name || '未命名能力域';
    const badge = document.createElement('span');
    badge.className = 'worker-badge';
    badge.dataset.state = area.state === 'available' ? 'succeeded' : 'failed';
    badge.textContent = stateLabel(area);
    head.append(title, badge);
    const limits = document.createElement('p');
    limits.className = 'cap-detail-limits';
    limits.textContent = `限制：${area.limits || '未提供'}`;
    detail.append(head, limits);
    const facts = factRows(area);
    if (facts) detail.append(facts);
    else {
      const none = document.createElement('p');
      none.className = 'muted';
      none.textContent = '该能力域没有可展示的计数。';
      detail.append(none);
    }
    const id = document.createElement('p');
    id.className = 'cap-detail-id';
    id.textContent = `能力域 ID：${area.id || '未知'}`;
    detail.append(id);
  }

  function render(report) {
    $('capabilities-meta').textContent =
      `格式 ${report.format} · 架构 ${report.schema_version} · API v${report.api_version} · ` +
      `平台验收 ${report.platform_acceptance} · V2 已验收 ${report.v2_accepted ? '是' : '否'}`;
    const host = $('capabilities-list');
    host.replaceChildren();
    const split = document.createElement('div');
    split.className = 'cap-split';
    const list = document.createElement('div');
    list.className = 'cap-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', '能力域');
    const detail = document.createElement('aside');
    detail.className = 'cap-detail';
    detail.setAttribute('aria-label', '能力域详情');
    const areas = Array.isArray(report.areas) ? report.areas : [];
    const buttons = [];
    const select = (area, button) => {
      selected = area.id || '';
      for (const other of buttons) other.classList.toggle('active', other === button);
      renderDetail(area);
    };
    for (const area of areas) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'cap-item';
      button.dataset.area = area.id || '';
      button.setAttribute('role', 'option');
      const name = document.createElement('strong');
      name.textContent = area.name || '未命名能力域';
      const state = document.createElement('span');
      state.className = 'worker-badge';
      state.dataset.state = area.state === 'available' ? 'succeeded' : 'failed';
      state.textContent = stateLabel(area);
      button.append(name, state);
      button.addEventListener('click', () => select(area, button));
      buttons.push(button);
      list.append(button);
    }
    if (!areas.length) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = '能力报告为空：服务端没有返回能力域。';
      list.append(empty);
    }
    split.append(list, detail);
    host.append(split);
    $('capabilities-notes').textContent = (report.notes || []).map((note) => `• ${note}`).join('\n');
    const first = areas[0];
    if (first) select(first, buttons[0]);
    else renderDetail(null);
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
