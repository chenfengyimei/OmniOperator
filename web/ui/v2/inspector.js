/* ==========================================================================
   V2 右栏（会话面板）数据渲染
   --------------------------------------------------------------------------
   职责：把「技能 / 产物 / 桌面」三个页签接上服务端事实——本轮技能目录与授权
   状态、当前 Agent 的对象列表（可下载）、当前会话的桌面截图（可预览）。
   明确不做：不发起运行、不改变授权、不缓存跨 Agent 的数据；渲染前只比对
   请求发起时的 Agent/会话/代次，过期响应直接丢弃。
   ========================================================================== */

const $ = (id) => document.getElementById(id);

function emptyState(container, { icon, title, hint, action }) {
  const box = document.createElement('div');
  box.className = 'inspector-empty';
  const mark = document.createElement('span');
  mark.className = 'material-icons';
  mark.textContent = icon;
  const strong = document.createElement('strong');
  strong.textContent = title;
  const text = document.createElement('span');
  text.className = 'inspector-empty-hint';
  text.textContent = hint;
  box.append(mark, strong, text);
  if (action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = action.label;
    button.addEventListener('click', () => document.querySelector(`[data-page="${action.page}"]`)?.click());
    box.append(button);
  }
  container.replaceChildren(box);
}

function bytesLabel(bytes) {
  if (!Number.isFinite(bytes)) return '未知大小';
  if (bytes < 1024) return `${bytes} 字节`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function clockLabel(value) {
  const stamp = String(value || '');
  const clock = /T(\d{2}:\d{2})/.exec(stamp);
  return clock ? clock[1] : stamp.slice(11, 16);
}

export function mountInspector({ api, wrap, getAgent, getSession, getGeneration, getRun }) {
  let skillTicket = 0;
  let objectTicket = 0;
  let desktopTicket = 0;
  let previewUrl = null;
  const bound = () => ({ agent: getAgent(), session: getSession(), stamp: getGeneration() });
  const current = (t) => t.agent === getAgent() && t.stamp === getGeneration();
  const sessionCurrent = (t) => current(t) && t.session === getSession();

  function releasePreview() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  async function loadSkills() {
    const container = $('inspector-skills');
    const t = { ...bound(), ticket: ++skillTicket };
    if (!t.agent) {
      emptyState(container, {
        icon: 'auto_awesome',
        title: '尚未连接',
        hint: '连接实例后显示本轮使用的技能。',
      });
      return;
    }
    const run = getRun();
    if (!run) {
      emptyState(container, {
        icon: 'auto_awesome',
        title: '本轮未使用技能',
        hint: '发起一次运行后，这里列出本轮技能目录与授权状态。',
        action: { label: '打开技能页', page: 'skills' },
      });
      return;
    }
    const value = await api(`/agents/${t.agent}/runs/${run}/skills`);
    if (!current(t) || t.ticket !== skillTicket) return;
    const rows = Array.isArray(value.items) ? value.items : [];
    if (value.captured === false) {
      emptyState(container, {
        icon: 'auto_awesome',
        title: '本轮未登记技能目录',
        hint: '该运行没有技能快照，无法判断本轮可用技能。',
        action: { label: '打开技能页', page: 'skills' },
      });
      return;
    }
    if (!rows.length) {
      emptyState(container, {
        icon: 'auto_awesome',
        title: '本轮未使用技能',
        hint: '本轮没有启用任何技能；技能只是流程说明，不授予工具权限。',
        action: { label: '打开技能页', page: 'skills' },
      });
      return;
    }
    container.replaceChildren(
      ...rows.map((row) => {
        const item = document.createElement('div');
        item.className = 'inspector-row';
        item.dataset.active = String(Boolean(row.active));
        const icon = document.createElement('span');
        icon.className = 'material-icons';
        icon.textContent = row.active ? 'check_circle' : 'radio_button_unchecked';
        const text = document.createElement('div');
        text.className = 'inspector-row-text';
        const name = document.createElement('strong');
        name.textContent = String(row.name || '未命名技能');
        const meta = document.createElement('span');
        const states = [
          row.active ? '本轮已使用' : '本轮未使用',
          row.available ? '授权有效' : '授权已撤销或平台不可用',
        ];
        meta.textContent = states.join(' · ');
        text.append(name, meta);
        item.append(icon, text);
        if (row.revision_id) item.title = `修订 ${row.revision_id}`;
        return item;
      }),
    );
  }

  async function loadObjects() {
    const container = $('inspector-objects');
    const t = { ...bound(), ticket: ++objectTicket };
    if (!t.agent) {
      emptyState(container, { icon: 'folder', title: '尚未连接', hint: '连接实例后显示本空间的产物。' });
      return;
    }
    const data = await api(`/agents/${t.agent}/objects?limit=12`);
    if (!current(t) || t.ticket !== objectTicket) return;
    const rows = Array.isArray(data.items) ? data.items : [];
    if (!rows.length) {
      emptyState(container, {
        icon: 'folder_open',
        title: '暂无产物',
        hint: '上传文件或运行产生的产物会出现在这里，点击即可下载。',
        action: { label: '打开文件页', page: 'objects' },
      });
      return;
    }
    container.replaceChildren(
      ...rows.map((item) => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'inspector-row inspector-row-button';
        row.title = `下载 ${item.media_type}（sha256 ${String(item.sha256).slice(0, 10)}）`;
        const icon = document.createElement('span');
        icon.className = 'material-icons';
        icon.textContent = item.pinned ? 'bookmark' : 'description';
        const text = document.createElement('div');
        text.className = 'inspector-row-text';
        const name = document.createElement('strong');
        name.textContent = String(item.media_type || '未知类型');
        const meta = document.createElement('span');
        meta.textContent = `${bytesLabel(item.bytes)} · ${String(item.sha256).slice(0, 10)}…`;
        text.append(name, meta);
        row.append(icon, text);
        row.addEventListener(
          'click',
          wrap(async () => {
            const blob = await api(`/agents/${t.agent}/objects/${item.id}/content`, { binary: true });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${String(item.sha256).slice(0, 12)}.bin`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }),
        );
        return row;
      }),
    );
  }

  async function loadDesktop() {
    const container = $('inspector-desktop');
    const t = { ...bound(), ticket: ++desktopTicket };
    releasePreview();
    if (!t.agent || !t.session) {
      emptyState(container, {
        icon: 'desktop_windows',
        title: '尚未选择会话',
        hint: '选择会话后，这里列出本会话保存的桌面截图。',
        action: { label: '打开桌面能力', page: 'desktop' },
      });
      return;
    }
    const data = await api(`/agents/${t.agent}/sessions/${t.session}/captures?limit=12`);
    if (!sessionCurrent(t) || t.ticket !== desktopTicket) return;
    const rows = Array.isArray(data.items) ? data.items : [];
    if (!rows.length) {
      emptyState(container, {
        icon: 'photo_camera',
        title: '暂无截图',
        hint: '在「桌面能力」页把窗口截图保存到当前会话后，可在此预览。',
        action: { label: '打开桌面能力', page: 'desktop' },
      });
      return;
    }
    const preview = document.createElement('img');
    preview.className = 'inspector-preview';
    preview.alt = '会话截图预览';
    preview.hidden = true;
    const caption = document.createElement('p');
    caption.className = 'inspector-preview-caption';
    caption.textContent = '点击缩略条目预览；图片可能包含私人内容。';
    const list = document.createElement('div');
    list.className = 'inspector-list';
    list.replaceChildren(
      ...rows.map((item) => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'inspector-row inspector-row-button';
        const icon = document.createElement('span');
        icon.className = 'material-icons';
        icon.textContent = 'image';
        const text = document.createElement('div');
        text.className = 'inspector-row-text';
        const name = document.createElement('strong');
        name.textContent = clockLabel(item.created_at) || '截图';
        const meta = document.createElement('span');
        meta.textContent = String(item.object_id).slice(0, 8);
        text.append(name, meta);
        row.append(icon, text);
        row.addEventListener(
          'click',
          wrap(async () => {
            const stamp = { ...bound(), ticket: desktopTicket };
            const blob = await api(`/agents/${t.agent}/objects/${item.object_id}/content`, { binary: true });
            if (!sessionCurrent(stamp) || stamp.ticket !== desktopTicket) return;
            releasePreview();
            previewUrl = URL.createObjectURL(blob);
            preview.src = previewUrl;
            preview.hidden = false;
            caption.textContent = `${clockLabel(item.created_at) || '截图'} · ${bytesLabel(blob.size)} · 保存于当前会话`;
          }),
        );
        return row;
      }),
    );
    container.replaceChildren(caption, list, preview);
  }

  return {
    loadSkills: () => loadSkills().catch(() => {}),
    loadObjects: () => loadObjects().catch(() => {}),
    loadDesktop: () => loadDesktop().catch(() => {}),
    reset() {
      skillTicket++;
      objectTicket++;
      desktopTicket++;
      releasePreview();
      for (const id of ['inspector-skills', 'inspector-objects', 'inspector-desktop']) {
        const node = $(id);
        if (node) node.replaceChildren();
      }
    },
  };
}
