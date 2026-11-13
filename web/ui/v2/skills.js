// Reviewed immutable text versions; drafts and turn selection are bounded per Agent, never browser storage.
import { ask } from '../dialog.js?v=20261103v7';

export function mountSkills({ api, wrap, getAgent, getGeneration, getSession }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map();
  let epoch = 0,
    reading = 0,
    busy = false,
    cursor = '',
    historyCursor = 0,
    reportRead = 0;
  const bound = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;
  const root = (agent) => `/agents/${agent}/skills`;
  function draft() {
    if (!drafts.has(getAgent())) {
      if (drafts.size >= 64) throw new Error('技能草稿较多，请先保存或重新连接');
      drafts.set(getAgent(), { selected: '', revision: 0, content: '', dirty: false, row: null, ids: [] });
    }
    return drafts.get(getAgent());
  }
  function controls() {
    for (const id of [
      'skills-save',
      'skills-import',
      'skills-refresh',
      'skills-new',
      'skills-list',
      'skills-content',
      'skills-file',
    ])
      $(id).disabled = busy;
    const d = getAgent() ? draft() : null,
      row = d?.row;
    for (const id of [
      'skills-enable',
      'skills-disable',
      'skills-archive',
      'skills-restore',
      'skills-export',
      'skills-reference-read',
      'skills-rollback',
    ])
      $(id).disabled = busy || !row || d.dirty;
    $('skills-reload').disabled = busy || !row;
    $('skills-more').disabled = busy || !cursor;
    $('skills-history-more').disabled = busy || !historyCursor;
    if (row) {
      $('skills-enable').disabled ||= row.state !== 'disabled';
      $('skills-disable').disabled ||= row.state !== 'enabled';
      $('skills-archive').disabled ||= row.state === 'archived';
      $('skills-restore').disabled ||= row.state !== 'archived';
      $('skills-save').disabled ||= row.state === 'archived';
    }
  }
  function renderRow(row) {
    const d = draft();
    d.selected = row.id;
    d.revision = row.revision;
    d.content = row.content;
    d.dirty = false;
    d.row = row;
    $('skills-content').value = row.content;
    if (![...$('skills-list').options].some((o) => o.value === row.id))
      $('skills-list').append(new Option(row.name, row.id));
    $('skills-list').value = row.id;
    $('skills-status').textContent =
      `${row.name} · ${row.state} · 内容修订 ${row.content_revision} · 编辑修订 ${row.revision}\n清单 ${row.manifest_sha256}；导入/编辑后须审阅并重新启用。`;
    $('skills-files').textContent = row.files.map((f) => `${f.path} · ${f.bytes} 字节 · ${f.sha256}`).join('\n');
    $('skills-reference').replaceChildren(...row.files.map((f) => new Option(f.path, f.path)));
    $('skills-reference-body').textContent = '';
    controls();
  }
  async function available() {
    const t = bound(),
      data = await api(root(t.agent) + '/available');
    if (!current(t)) return;
    const d = draft();
    $('skill-turn').replaceChildren(...data.items.map((r) => new Option(`${r.name} · ${r.description}`, r.id)));
    for (const id of d.ids) {
      let option = [...$('skill-turn').options].find((o) => o.value === id);
      if (!option) {
        option = new Option('所选技能已停用或不可用于本平台，请清空后重新选择', id);
        option.disabled = true;
        $('skill-turn').append(option);
      }
      option.selected = true;
    }
  }
  async function load(more = false) {
    const t = bound(),
      ticket = ++reading;
    const data = await api(`${root(t.agent)}?limit=50&after=${more ? cursor : ''}`);
    if (!current(t) || ticket !== reading) return;
    if (!more) $('skills-list').replaceChildren(new Option('新建技能', ''));
    for (const r of data.items)
      if (![...$('skills-list').options].some((o) => o.value === r.id))
        $('skills-list').append(new Option(`${r.name} · ${r.state}`, r.id));
    cursor = data.next_after || '';
    const d = draft();
    if (d.selected && ![...$('skills-list').options].some((o) => o.value === d.selected))
      $('skills-list').append(new Option('已选技能', d.selected));
    $('skills-list').value = d.selected;
    $('skills-content').value = d.content;
    $('skills-status').textContent = d.dirty
      ? `草稿保留，基于编辑修订 ${d.revision}；保存后停用，须重新审阅。`
      : '请选择技能审阅正文、参考文件与清单，再明确启用。';
    controls();
    await available();
  }
  async function history(more = false) {
    const t = bound(),
      selected = draft().selected;
    if (!selected) return;
    const data = await api(`${root(t.agent)}/${selected}/history?after=${more ? historyCursor : 0}&limit=50`);
    if (!current(t) || draft().selected !== selected) return;
    if (!more) $('skills-history').replaceChildren();
    for (const r of data.items)
      $('skills-history').append(new Option(`修订 ${r.revision} · ${r.created_at} · ${r.manifest_sha256}`, r.id));
    historyCursor = data.next_after || 0;
    controls();
  }
  async function loadRow(id) {
    const t = bound(),
      ticket = ++reading;
    if (!id) {
      const d = draft();
      Object.assign(d, { selected: '', revision: 0, row: null, dirty: false, content: '' });
      $('skills-content').value = '';
      controls();
      return;
    }
    const row = await api(`${root(t.agent)}/${id}`);
    if (!current(t) || ticket !== reading) return;
    renderRow(row);
    await history();
  }
  async function mutate(t, operation) {
    busy = true;
    controls();
    try {
      await operation();
    } finally {
      if (current(t)) {
        busy = false;
        controls();
      }
    }
  }
  $('skills-content').addEventListener('input', () => {
    const d = draft();
    d.content = $('skills-content').value;
    d.dirty = true;
    $('skills-status').textContent = '草稿未保存；保存会停用新修订，需要重新审阅。';
    controls();
  });
  $('skills-list').addEventListener(
    'change',
    wrap(async () => {
      const d = draft(),
        selected = $('skills-list').value,
        t = bound();
      if (
        d.dirty &&
        !(await ask({
          title: '切换技能',
          message: '当前技能草稿尚未保存，继续将放弃这份草稿。',
          confirmLabel: '放弃草稿并切换',
        }))
      ) {
        $('skills-list').value = d.selected;
        return;
      }
      if (current(t)) await loadRow(selected);
    }),
  );
  $('skills-new').addEventListener(
    'click',
    wrap(async () => {
      if (
        draft().dirty &&
        !(await ask({ title: '新建技能', message: '当前草稿未保存，继续将放弃。', confirmLabel: '放弃草稿并新建' }))
      )
        return;
      await loadRow('');
      draft().content =
        '---\nname: my-skill\ndescription: 说明适用的流程\nplatforms: [windows, macos, linux]\n---\n\n## When to Use\n说明适用条件。\n\n## Procedure\n写出可执行并需要逐步验证的流程，所有操作仍遵守原有权限与审批。\n\n## Pitfalls\n列出失败、未知状态和停止条件。\n\n## Verification\n说明如何核验真实结果。\n';
      draft().dirty = true;
      $('skills-content').value = draft().content;
      controls();
    }),
  );
  $('skills-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = bound(),
        d = draft(),
        content = d.content,
        selected = d.selected,
        revision = d.revision;
      await mutate(t, async () => {
        const row = await api(root(t.agent), {
          method: 'POST',
          body: { content, skill_id: selected || null, revision },
        });
        if (current(t)) {
          renderRow({ ...row, content });
          await history();
          await available();
        }
      });
    }),
  );
  for (const [id, state, title] of [
    ['skills-enable', 'enabled', '启用此修订'],
    ['skills-disable', 'disabled', '停用此技能'],
    ['skills-archive', 'archived', '归档此技能'],
    ['skills-restore', 'disabled', '恢复为停用'],
  ])
    $(id).addEventListener(
      'click',
      wrap(async () => {
        const t = bound(),
          row = draft().row;
        if (
          !row ||
          draft().dirty ||
          !(await ask({
            title,
            message: `${row.name}；修订 ${row.revision_id}；清单 ${row.manifest_sha256}；文件 ${row.files.map((f) => f.path).join('、')}。技能仅提供流程说明，不授权工具。停用或改写会撤销已使用它的运行。`,
            confirmLabel: title,
          }))
        )
          return;
        if (!current(t) || draft().row !== row || busy) return;
        await mutate(t, async () => {
          const updated = await api(`${root(t.agent)}/${row.id}/state`, {
            method: 'PUT',
            body: { revision: row.revision, state, revision_id: row.revision_id, manifest_sha256: row.manifest_sha256 },
          });
          if (current(t)) {
            renderRow({ ...updated, content: row.content });
            await available();
          }
        });
      }),
    );
  $('skills-import').addEventListener(
    'click',
    wrap(async () => {
      const t = bound(),
        file = $('skills-file').files[0],
        d = draft(),
        selected = d.selected,
        revision = d.revision;
      if (!file || file.size > 1048576 || !/\.(md|zip)$/i.test(file.name))
        throw new Error('请选择不超过 1 MiB 的 SKILL.md 或扁平 ZIP 文本包');
      if (
        !(await ask({
          title: '导入停用修订',
          message: `${file.name}；${selected ? '将替换所选技能的完整文件集合并停用，旧版本保留' : '新技能将保持停用，审阅后才能启用'}。不会执行文件。`,
          confirmLabel: '仅导入为停用',
        }))
      )
        return;
      if (!current(t) || busy) return;
      await mutate(t, async () => {
        const obj = await api(`/agents/${t.agent}/objects`, { method: 'POST', body: file, raw: true });
        const row = await api(root(t.agent) + '/import', {
          method: 'POST',
          body: {
            object_id: obj.id,
            format: /\.zip$/i.test(file.name) ? 'zip' : 'markdown',
            skill_id: selected || null,
            revision,
          },
        });
        if (current(t)) {
          await loadRow(row.id);
          await available();
        }
      });
    }),
  );
  $('skills-reload').addEventListener(
    'click',
    wrap(async () => {
      if (
        draft().dirty &&
        !(await ask({ title: '重新载入', message: '将放弃未保存的技能草稿。', confirmLabel: '放弃草稿并载入' }))
      )
        return;
      await loadRow(draft().selected);
    }),
  );
  $('skills-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('skills-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('skills-history-more').addEventListener(
    'click',
    wrap(() => history(true)),
  );
  $('skills-reference-read').addEventListener(
    'click',
    wrap(async () => {
      const t = bound(),
        row = draft().row,
        path = $('skills-reference').value;
      if (!row) return;
      const value = await api(
        `${root(t.agent)}/${row.id}?revision_id=${row.revision_id}&path=${encodeURIComponent(path)}`,
      );
      if (current(t) && draft().row === row) $('skills-reference-body').textContent = value.content;
    }),
  );
  $('skills-rollback').addEventListener(
    'click',
    wrap(async () => {
      const t = bound(),
        row = draft().row,
        version = $('skills-history').value;
      if (
        !row ||
        !version ||
        !(await ask({
          title: '复制历史为新修订',
          message: '不会改写历史；新修订保持停用，须重新审阅。',
          confirmLabel: '创建停用修订',
        }))
      )
        return;
      if (!current(t) || busy) return;
      await mutate(t, async () => {
        const value = await api(`${root(t.agent)}/${row.id}/rollback`, {
          method: 'POST',
          body: { revision: row.revision, revision_id: version },
        });
        if (current(t)) {
          await loadRow(value.id);
          await available();
        }
      });
    }),
  );
  $('skills-export').addEventListener(
    'click',
    wrap(async () => {
      const t = bound(),
        row = draft().row;
      if (!row) return;
      await mutate(t, async () => {
        const value = await api(`${root(t.agent)}/${row.id}/export?revision_id=${row.revision_id}`, { method: 'POST' }),
          data = await api(`/agents/${t.agent}/objects/${value.object_id}/content`, { binary: true });
        if (!current(t)) return;
        const url = URL.createObjectURL(data),
          link = document.createElement('a');
        link.href = url;
        link.download = value.filename;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
    }),
  );
  // 多选 select 在 size=1 时浏览器强制渲染英文「N selected」，无法用 CSS 改写。
  // 保留原生 select 作为数据源（测试按 id 定位），另挂中文摘要按钮承接视觉与点击。
  const turnLabel = document.createElement('span');
  turnLabel.id = 'skill-turn-label';
  turnLabel.className = 'pick-label';
  turnLabel.setAttribute('aria-hidden', 'true');
  const syncTurnLabel = () => {
    const picked = [...$('skill-turn').selectedOptions];
    const usable = [...$('skill-turn').options].filter((o) => !o.disabled).length;
    turnLabel.textContent = picked.length
      ? `已选 ${picked.length} 项`
      : usable
        ? `选择技能（${usable}）`
        : '暂无可用技能';
    turnLabel.title = picked.length
      ? picked.map((o) => o.textContent).join('\n')
      : '本轮使用的技能，最多 4 个';
    turnLabel.dataset.count = String(picked.length);
  };
  // 聚焦即展开原生多选列表（浮层），失焦收回；键盘与自动化操作均走原生控件
  $('skill-turn').addEventListener('focus', () => {
    const select = $('skill-turn');
    select.size = Math.min(Math.max(select.options.length, 2), 6);
    select.dataset.expanded = 'true';
  });
  $('skill-turn').addEventListener('blur', () => {
    const select = $('skill-turn');
    select.size = 1;
    delete select.dataset.expanded;
    syncTurnLabel();
  });
  $('skill-turn').parentElement.append(turnLabel);
  $('skill-turn').addEventListener('change', () => {
    draft().ids = [...$('skill-turn').selectedOptions].map((o) => o.value);
    syncTurnLabel();
  });
  $('skill-turn-clear').addEventListener('click', () => {
    draft().ids = [];
    for (const option of $('skill-turn').options) option.selected = false;
    syncTurnLabel();
  });
  // 选项由 available()/reset() 异步重建，用 MutationObserver 保证摘要始终同步
  new MutationObserver(syncTurnLabel).observe($('skill-turn'), { childList: true });
  syncTurnLabel();
  return {
    load,
    available,
    snapshot() {
      const options = [...$('skill-turn').selectedOptions];
      if (options.length > 4 || options.some((o) => o.disabled))
        throw new Error('最多选择 4 个当前可用技能；停用项请清空后重新选择');
      return options.map((o) => o.value);
    },
    reset() {
      epoch++;
      reading++;
      reportRead++;
      busy = false;
      cursor = '';
      historyCursor = 0;
      for (const id of ['skills-status', 'skills-files', 'skills-reference-body', 'skill-run-status'])
        $(id).textContent = '';
      $('skills-content').value = '';
      $('skills-list').replaceChildren();
      $('skill-turn').replaceChildren();
      $('skills-history').replaceChildren();
    },
    async report(run, session) {
      const t = bound(),
        ticket = ++reportRead,
        value = await api(`/agents/${t.agent}/runs/${run}/skills`);
      if (!current(t) || ticket !== reportRead || getSession() !== session) return;
      const rows = Array.isArray(value.items) ? value.items : [];
      $('skill-run-status').textContent =
        value.captured === false
          ? '此运行未登记技能目录。'
          : rows.length
            ? rows
                .map(
                  (r) =>
                    `${r.name} · ${r.active ? '已使用' : '未使用'} · ${r.available ? '授权有效' : '授权已撤销或平台不可用'} · ${r.revision_id}`,
                )
                .join('\n')
            : '本轮未使用技能。';
    },
    clear() {
      reportRead++;
      $('skill-run-status').textContent = '';
    },
  };
}
