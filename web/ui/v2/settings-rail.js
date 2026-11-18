/* ==========================================================================
   V2 设置中心左侧分类栏
   --------------------------------------------------------------------------
   职责：把 rail 上「7 组 / 23 页」的导航在设置覆盖层内复刻成左侧分类列表，
   点击分类项等价于点击对应 rail 按钮（页面切换入口保持唯一），并把当前页
   选中态同步到分类列表；窄屏退化为横向滚动条。
   明确不做：不自己切换 section、不复制页面内容、不改写 rail 的 DOM 顺序或
   分组文本——分组与页面名一律从 rail 读取，避免两处清单漂移。
   ========================================================================== */

const RAIL = 'nav[aria-label="工作台"]';

function collectGroups() {
  const rail = document.querySelector(RAIL);
  if (!rail) return [];
  const groups = [];
  let current = null;
  for (const node of rail.children) {
    if (node.classList?.contains('nav-group')) {
      current = { title: node.textContent.trim(), items: [] };
      groups.push(current);
      continue;
    }
    if (!current || !node.matches?.('button[data-page]')) continue;
    // 「对话」不进设置分类栏：左上角导航与页头「返回对话」都能回对话页，
    // 侧边栏再放一项是冗余入口（所有者要求移除）。
    if (node.dataset.page === 'chat') continue;
    current.items.push({
      page: node.dataset.page,
      label: node.getAttribute('aria-label') || node.title || node.dataset.page,
      icon: node.querySelector('.material-icons')?.textContent.trim() || 'settings',
    });
  }
  // 空分组自动剔除：如「对话」组只剩 chat 且 chat 不进设置栏时，整组不再显示
  // （所有者要求：分组空了就删）。
  return groups.filter((group) => group.items.length);
}

export function mountSettingsRail({ getPage }) {
  const host = document.getElementById('settings-rail');
  if (!host) return { sync() {}, reset() {} };
  let links = [];

  function go(target) {
    const rail = document.querySelector(RAIL);
    const button = rail?.querySelector(`button[data-page="${target}"]`);
    button?.click();
  }

  function render() {
    const groups = collectGroups();
    host.replaceChildren(
      ...groups.map((group) => {
        const block = document.createElement('div');
        block.className = 'settings-rail-group';
        const title = document.createElement('p');
        title.className = 'settings-rail-title';
        title.textContent = group.title;
        block.append(title);
        for (const item of group.items) {
          const link = document.createElement('a');
          link.className = 'settings-rail-link';
          link.href = '#settings';
          link.dataset.settingsPage = item.page;
          const glyph = document.createElement('span');
          glyph.className = 'material-icons';
          glyph.setAttribute('aria-hidden', 'true');
          glyph.textContent = item.icon;
          const label = document.createElement('span');
          label.className = 'settings-rail-label';
          label.textContent = item.label;
          link.append(glyph, label);
          link.addEventListener('click', (event) => {
            event.preventDefault();
            go(item.page);
          });
          block.append(link);
          links.push(link);
        }
        return block;
      }),
    );
  }

  render();

  return {
    sync(page) {
      const next = page || getPage?.() || 'chat';
      for (const link of host.querySelectorAll('[data-settings-page]')) {
        const on = link.dataset.settingsPage === next;
        link.setAttribute('aria-current', String(on));
        // 侧边栏独立滚动：选中项可能在可见区外，滚入最近可见位置
        // （block:'nearest' 只滚侧边栏自身，不带动整页）。
        if (on) link.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    },
    reset() {
      links = [];
    },
  };
}
