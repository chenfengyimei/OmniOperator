import { ask } from '../dialog.js?v=20261103v7';

const stateLabels = {
  enabled: '已授权；实际写动作仍须逐次审批',
  disabled: '后续调用授权已禁用',
  drifted: '指纹已漂移；请重新登记并核查版本',
  missing: '快照缺失；须由保存的包重建后再使用',
};

export function mountExtensions({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    slots = new Map(),
    drafts = new Map(),
    reviews = new Map(),
    pendingInstalls = new Map(),
    selections = new Map();
  let owner = '',
    selected = null,
    detail = null,
    marketResult = null,
    scopeRead = 0,
    viewRead = 0,
    listRead = 0,
    detailRead = 0,
    after = 0,
    actionAfter = '',
    listLoading = false,
    detailLoading = false,
    actionRead = 0,
    mutation = null;
  const root = (agent = getAgent()) => `/agents/${agent}/extensions`;
  const formKey = (agent = getAgent(), identity = selected) => `${agent}/${identity || 'new'}`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), scope: scopeRead, view: viewRead, selected });
  const sameScope = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.scope === scopeRead;
  const current = (t) => sameScope(t) && t.view === viewRead && t.selected === selected;
  function draft(k = formKey()) {
    if (!drafts.has(k)) {
      if (drafts.size >= 64) throw new Error('保留的插件路径草稿较多，请先完成或放弃部分登记');
      drafts.set(k, { source: '', version: 0 });
    }
    return drafts.get(k);
  }
  function review() {
    if (!detail) return null;
    const k = `${formKey()}/${detail.revision}/${detail.extension_revision_id}/${detail.history_epoch}`;
    if (!reviews.has(k)) {
      if (reviews.size >= 64) reviews.delete(reviews.keys().next().value);
      reviews.set(k, { tools: new Set(), trust: false });
    }
    return reviews.get(k);
  }
  function remember(record) {
    const metadata = {};
    for (const name of ['id', 'name', 'state', 'enabled', 'revision', 'sequence', 'version'])
      metadata[name] = record[name];
    const previous = slots.get(metadata.id);
    if (!previous || previous.revision <= metadata.revision) slots.set(metadata.id, metadata);
    while (slots.size > 200) slots.delete(slots.keys().next().value);
  }
  function choices() {
    $('extension-select').replaceChildren(
      new Option('新插件登记', ''),
      ...[...slots.values()].map((row) => new Option(`${row.name} · ${row.state} · ${row.id}`, row.id)),
    );
    $('extension-select').value = selected || '';
  }
  function controls() {
    const pending = pendingInstalls.get(formKey()),
      source = $('extension-source').value,
      authorization = detail ? review() : null,
      matched =
        detail?.trust_model === 'trusted_python_not_os_sandbox' &&
        detail.requires_action_approval === true &&
        detail.is_current_version === true &&
        detail.tools.length > 0 &&
        detail.tools.every((tool) => tool.writes === true);
    $('extension-select').disabled = listLoading || Boolean(mutation);
    $('extension-refresh').disabled = listLoading || Boolean(mutation);
    $('extension-more').disabled = listLoading || Boolean(mutation) || !after;
    $('extension-new').disabled = Boolean(mutation);
    $('extension-refresh-detail').disabled = !selected || detailLoading || Boolean(mutation);
    $('extension-install').textContent = selected ? '登记所选插件的新版本' : '登记插件包';
    $('extension-install').disabled =
      Boolean(mutation) ||
      !source.trim() ||
      (selected && (!detail || detailLoading)) ||
      Boolean(pending && pending.source !== source);
    $('extension-resolve-install').hidden = !pending;
    $('extension-resolve-install').disabled = !pending || Boolean(mutation);
    $('extension-authorization-fields').disabled = !matched || detailLoading || Boolean(mutation);
    $('extension-authorize').disabled =
      !matched ||
      detail?.state === 'drifted' ||
      Boolean(mutation) ||
      !authorization?.trust ||
      !authorization.tools.size;
    $('extension-disable').disabled = !detail || detailLoading || Boolean(mutation);
    $('extension-actions-refresh').disabled = !selected || detailLoading || Boolean(mutation);
    $('extension-actions-more').disabled = !selected || !actionAfter || detailLoading || Boolean(mutation);
    $('extension-market-check').disabled = Boolean(mutation) || !$('extension-market-source').value.trim();
  }
  function clearDetail() {
    detail = null;
    actionAfter = '';
    actionRead++;
    $('extension-details').textContent = '';
    $('extension-files').textContent = '';
    $('extension-tools').replaceChildren();
    $('extension-actions').replaceChildren();
    $('extension-artifact-preview').textContent = '';
    $('extension-trust').checked = false;
  }
  function renderDetail(record) {
    detail = record;
    remember(record);
    choices();
    const runtime = record.runtime,
      authorization = review();
    $('extension-details').textContent =
      `${record.name} · ${stateLabels[record.state] || record.state}\n` +
      `插件 ${record.id} · 版本 ${record.version} · 槽修订 ${record.revision}\n` +
      `入口 ${record.entry} · 协议 ${record.protocol}\n` +
      `包 SHA-256 ${record.package_sha256}\n目录 SHA-256 ${record.catalog_sha256}\n` +
      `包内 ${record.files.length} 个文件，共 ${record.files.reduce((total, file) => total + file.bytes, 0)} 字节\n` +
      (runtime
        ? `实例插件宿主合计：常驻 ${runtime.resident}，活跃 ${runtime.active}，待回收 ${runtime.cleanup_pending}。\n`
        : '实例宿主状态未提供，请刷新核查。\n') +
      (record.runtime_error_code ? `运行原因：${record.runtime_error_code}\n` : '') +
      (record.is_current_version ? '' : '本收据对应历史版本；请刷新当前版本后再授权。\n') +
      '登记和查看不会执行插件；授权不代替每次写动作的人工审批。';
    $('extension-files').textContent = record.files
      .map((file) => `${file.path} · ${file.bytes} 字节 · ${file.sha256}`)
      .join('\n');
    $('extension-trust').checked = authorization.trust;
    $('extension-tools').replaceChildren(
      ...record.tools.map((tool) => {
        const card = document.createElement('article'),
          label = document.createElement('label'),
          checkbox = document.createElement('input'),
          text = document.createElement('span'),
          parameters = document.createElement('pre'),
          captured = record,
          t = target();
        card.dataset.toolName = tool.name;
        checkbox.type = 'checkbox';
        checkbox.value = tool.name;
        checkbox.checked = authorization.tools.has(tool.name);
        checkbox.setAttribute('aria-label', `授权工具 ${tool.name}`);
        text.textContent = `${tool.name} · ${tool.granted ? '当前已授权' : '当前未授权'}\n${tool.description}\n本期为写工具，超时 ${tool.timeout_s} 秒，实际执行仍须人工审批。`;
        parameters.textContent = JSON.stringify(tool.parameters, null, 2);
        checkbox.addEventListener('change', () => {
          if (!current(t) || detail !== captured || mutation) return;
          if (checkbox.checked) authorization.tools.add(tool.name);
          else authorization.tools.delete(tool.name);
          controls();
        });
        label.append(checkbox, text);
        card.append(label, parameters);
        return card;
      }),
    );
    controls();
  }
  function restoreSource() {
    $('extension-source').value = draft().source;
    controls();
  }
  function renderActions(result, appended) {
    const box = $('extension-actions');
    if (!appended) box.replaceChildren();
    for (const item of result.items) {
      const line = document.createElement('p');
      const artifacts = item.artifacts.length
        ? item.artifacts.map((artifact) => `${artifact.media_type} ${artifact.bytes}B`).join('，')
        : '无产物';
      line.textContent = `${(item.ended_at || '').slice(0, 19)} · ${item.tool} · ${item.state}/${item.effect} · ${artifacts}`;
      box.append(line);
      for (const artifact of item.artifacts) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `预览 ${artifact.media_type} · ${artifact.bytes}B`;
        button.addEventListener(
          'click',
          wrap(async () => {
            const t = target();
            const blob = await api(`/agents/${t.agent}/objects/${artifact.id}/content`, { binary: true });
            if (!current(t)) return;
            const text =
              artifact.media_type.startsWith('text/') || artifact.media_type === 'application/json'
                ? (await blob.text()).slice(0, 4000)
                : `非文本产物，已保存为对象 ${artifact.id}，不在此展开。`;
            $('extension-artifact-preview').textContent =
              `对象 ${artifact.id} · ${artifact.media_type} · ${artifact.bytes} 字节\n${text}`;
          }),
        );
        box.append(button);
      }
    }
    if (!result.items.length) {
      const empty = document.createElement('p');
      empty.textContent = '尚无调用记录；实际调用需要模型发起并完成审批。';
      box.append(empty);
    }
  }
  async function loadActions(more = false) {
    const t = target(),
      ticket = ++actionRead;
    if (!t.selected) {
      actionAfter = '';
      renderActions({ items: [] }, false);
      return;
    }
    try {
      const query = more && actionAfter ? `?after=${actionAfter}&limit=20` : '?limit=20';
      const result = await api(`${root(t.agent)}/${t.selected}/actions${query}`);
      if (!current(t) || ticket !== actionRead) return;
      actionAfter = result.next_after || '';
      renderActions(result, more);
    } catch (error) {
      if (!current(t) || ticket !== actionRead) return;
      $('extension-actions').textContent = '最近调用读取失败，请显式刷新后重试。';
      throw error;
    } finally {
      if (current(t) && ticket === actionRead) controls();
    }
  }
  function reset() {
    if (owner) selections.set(owner, selected);
    owner = '';
    scopeRead++;
    viewRead++;
    listRead++;
    detailRead++;
    selected = mutation = null;
    listLoading = detailLoading = false;
    after = 0;
    slots.clear();
    clearDetail();
    choices();
    $('extension-source').value = '';
    marketResult = null;
    $('extension-market-source').value = '';
    $('extension-market-list').replaceChildren();
    $('extension-market-status').textContent = '';
    $('extension-status').textContent = $('extension-list-status').textContent = '';
    controls();
  }
  async function loadList(more = false) {
    const t = target(),
      ticket = ++listRead;
    listLoading = true;
    if (!more) {
      after = 0;
      slots.clear();
      choices();
    }
    controls();
    $('extension-list-status').textContent = '正在读取当前 Agent 的可信插件…';
    try {
      const result = await api(`${root(t.agent)}?after=${more ? after : 0}&limit=20`);
      if (!sameScope(t) || ticket !== listRead) return;
      for (const row of result.items) remember(row);
      after = result.next_after || 0;
      if (detail) remember(detail);
      choices();
      $('extension-list-status').textContent =
        `当前显示 ${slots.size} 个插件，最多保留最近读取的 200 个。` +
        (after ? '还有插件可继续加载。' : '已读到当前末尾。');
    } catch (error) {
      if (!sameScope(t) || ticket !== listRead) return;
      $('extension-list-status').textContent = '插件列表读取失败，请重新刷新。';
      throw error;
    } finally {
      if (sameScope(t) && ticket === listRead) {
        listLoading = false;
        controls();
      }
    }
  }
  async function loadDetail() {
    const t = target(),
      ticket = ++detailRead;
    detailLoading = false;
    clearDetail();
    if (!selected) {
      controls();
      return;
    }
    detailLoading = true;
    controls();
    $('extension-status').textContent = '正在读取已保存的包、目录与实际宿主状态，不执行插件…';
    try {
      const record = await api(`${root(t.agent)}/${t.selected}`);
      if (!current(t) || ticket !== detailRead) return;
      renderDetail(record);
      await loadActions(false);
      if (!current(t) || ticket !== detailRead) return;
      $('extension-status').textContent =
        record.runtime?.cleanup_pending > 0
          ? '实例仍有宿主待回收；授权状态与实际回收状态需分别核查，请显式刷新。'
          : '已读取当前状态。手动选择工具并明确认可可信代码后，才能提交授权。';
    } catch (error) {
      if (!current(t) || ticket !== detailRead) return;
      $('extension-status').textContent = '插件详情读取失败，旧详情已清空；输入草稿保留，请重新刷新。';
      throw error;
    } finally {
      if (current(t) && ticket === detailRead) {
        detailLoading = false;
        controls();
      }
    }
  }
  async function install(reconcile = false) {
    const t = target(),
      k = formKey(),
      sourceDraft = draft(),
      sourceVersion = sourceDraft.version,
      previous = pendingInstalls.get(k),
      request = previous || {
        key: crypto.randomUUID(),
        source: sourceDraft.source,
        extension_id: selected,
        revision: selected ? detail?.revision : 0,
      };
    if (mutation || !request.source.trim() || (selected && !detail && !previous)) return;
    if (previous && previous.source !== sourceDraft.source && !reconcile) return;
    if (
      !(await ask({
        title: reconcile ? '核查上次登记结果' : '登记可信插件包',
        message: `空间 ${t.agent}；${request.extension_id ? `更新插件 ${request.extension_id} 的修订 ${request.revision}` : '新插件登记'}；目录 ${request.source}。只读取并保存固定包与工具目录，不执行插件代码；更新版本会撤销旧授权，之后需重新手选授权。${reconcile ? '重试上次相同请求键，不替换当前新输入。' : ''}`,
        confirmLabel: reconcile ? '核查相同登记请求' : '仅登记包',
      }))
    )
      return;
    if (!current(t) || mutation) return;
    if (!previous && (sourceDraft.version !== sourceVersion || draft(k) !== sourceDraft)) return;
    if (!pendingInstalls.has(k)) {
      if (pendingInstalls.size >= 64) throw new Error('未核清的插件登记较多，请先核查已有请求');
      pendingInstalls.set(k, request);
    }
    const flight = {};
    mutation = flight;
    controls();
    try {
      const record = await api(root(t.agent), { method: 'POST', body: request });
      if (pendingInstalls.get(k) === request) pendingInstalls.delete(k);
      if (!current(t)) return;
      remember(record);
      choices();
      if (draft(k).version !== sourceVersion || draft(k).source !== request.source) {
        clearDetail();
        $('extension-status').textContent =
          '此前登记已有服务端收据；当前新输入保持不变，请刷新列表或所选插件查看当前版本。';
        return;
      }
      selected = record.id;
      viewRead++;
      selections.set(t.agent, selected);
      draft().source = request.source;
      restoreSource();
      choices();
      const installedTarget = target();
      await loadDetail();
      if (current(installedTarget))
        $('extension-status').textContent = '包已登记，尚未执行插件；请核查指纹并手动选择授权工具。';
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && pendingInstalls.get(k) === request) pendingInstalls.delete(k);
      if (!current(t)) return;
      $('extension-status').textContent = pendingInstalls.has(k)
        ? '登记结果尚未核清；请求键和原始意图已保留，可显式核查上次登记结果。当前输入保持不变。'
        : '登记被拒绝；当前输入保留，请刷新所选插件后核对修订和路径。';
      if (error.status !== 409) throw error;
    } finally {
      if (mutation === flight) mutation = null;
      if (sameScope(t)) controls();
    }
  }
  function renderMarket(result) {
    const list = $('extension-market-list');
    list.replaceChildren();
    for (const item of result.items) {
      const card = document.createElement('article');
      const text = document.createElement('p');
      const state = item.is_current_version
        ? '已是当前版本'
        : item.update_available
          ? `可更新（当前 ${item.installed_version}）`
          : '未安装';
      text.textContent = `${item.name} · ${item.version} · ${state} · ${item.source}`;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = item.is_current_version ? '已是当前版本' : item.installed ? '登记为新版本' : '登记为新插件';
      button.disabled = item.is_current_version || Boolean(mutation);
      button.addEventListener(
        'click',
        wrap(() => installMarket(item)),
      );
      card.append(text, button);
      list.append(card);
    }
    if (!result.items.length) {
      const empty = document.createElement('p');
      empty.textContent = '该目录没有可读取的插件包（仅扫描直接子目录的 extension.json）。';
      list.append(empty);
    }
  }
  async function checkMarket(source) {
    const t = target(),
      flight = {};
    if (!source) return;
    mutation = flight;
    controls();
    $('extension-market-status').textContent = '正在只读扫描本机市场目录，不会安装或执行插件…';
    try {
      const result = await api(`${root(t.agent)}/market`, { method: 'POST', body: { source } });
      if (!current(t) || mutation !== flight) return;
      marketResult = result;
      renderMarket(result);
      $('extension-market-status').textContent =
        `扫描 ${result.scanned} 个候选；登记仍需逐项确认，更新版本会撤销旧授权。`;
    } catch (error) {
      if (!current(t) || mutation !== flight) return;
      $('extension-market-status').textContent = '市场目录扫描被拒绝或不可读；请核对绝对路径后重试。';
      throw error;
    } finally {
      if (mutation === flight) mutation = null;
      if (current(t)) {
        controls();
        if (marketResult) renderMarket(marketResult);
      }
    }
  }
  async function installMarket(item) {
    if (mutation || !current(target())) return;
    const t = target();
    if (
      !(await ask({
        title: item.installed ? '登记插件新版本' : '登记新插件',
        message: `${item.name} 版本 ${item.version}，目录 ${item.source}。登记只捕获固定包与工具目录，不执行插件；更新版本会撤销旧授权并要求重新审查。`,
        confirmLabel: '登记该版本',
      }))
    )
      return;
    if (!current(t) || mutation) return;
    const flight = {};
    mutation = flight;
    controls();
    try {
      const record = await api(root(t.agent), {
        method: 'POST',
        body: {
          key: crypto.randomUUID(),
          source: item.source,
          extension_id: item.extension_id,
          revision: item.revision,
        },
      });
      if (!current(t)) return;
      selected = record.id;
      viewRead++;
      selections.set(t.agent, selected);
      remember(record);
      choices();
      await loadDetail();
      if (current(target()))
        $('extension-status').textContent = '包已登记，尚未执行插件；请核查指纹并手动选择授权工具。';
    } catch (error) {
      if (!current(t)) return;
      $('extension-market-status').textContent = '登记被拒绝或结果未核清；请显式刷新后核对版本。';
      if (error.status !== 409) throw error;
    } finally {
      if (mutation === flight) mutation = null;
      if (current(t)) controls();
    }
    if (current(t)) await checkMarket($('extension-market-source').value.trim());
  }
  async function mutate(captured, operation, disabling = false) {
    const t = target(),
      flight = {};
    if (mutation || detail !== captured) return;
    mutation = flight;
    controls();
    try {
      const record = await operation();
      if (!current(t)) return;
      if (detail && detail.revision > record.revision) return;
      renderDetail(record);
      $('extension-status').textContent = disabling
        ? record.runtime?.cleanup_pending > 0
          ? '后续授权已禁用；实例仍有宿主待回收，请显式刷新，不将授权撤销解释为进程已退出。'
          : '后续授权已禁用；实例当前未报告待回收宿主。'
        : '所选工具已获后续调用授权；没有执行插件，实际写动作仍须逐次人工审批。';
    } catch (error) {
      if (!current(t)) return;
      clearDetail();
      $('extension-status').textContent =
        error.status === 409 || error.status === 410
          ? '插件、指纹或授权代次已变化；旧详情已清空，请刷新后重新核查。路径草稿保留。'
          : '未能确认操作结果；请显式刷新服务端状态后再决定后续操作。路径草稿保留。';
      if (error.status !== 409 && error.status !== 410) throw error;
    } finally {
      if (mutation === flight) mutation = null;
      if (current(t)) controls();
    }
  }
  $('extension-source').addEventListener('input', () => {
    const value = draft();
    value.source = $('extension-source').value;
    value.version++;
    controls();
  });
  $('extension-market-source').addEventListener('input', controls);
  $('extension-market-form').addEventListener(
    'submit',
    wrap(async () => {
      const source = $('extension-market-source').value.trim();
      if (!source) return;
      await checkMarket(source);
    }),
  );
  $('extension-install-form').addEventListener(
    'submit',
    wrap(() => install()),
  );
  $('extension-resolve-install').addEventListener(
    'click',
    wrap(() => install(true)),
  );
  $('extension-trust').addEventListener('change', () => {
    if (!detail || mutation) return;
    review().trust = $('extension-trust').checked;
    controls();
  });
  $('extension-authorize').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        captured = detail,
        chosen = review();
      if (!captured || !chosen.trust || !chosen.tools.size || mutation) return;
      const tools = [...chosen.tools].sort(),
        request = {
          revision: captured.revision,
          package_sha256: captured.package_sha256,
          catalog_sha256: captured.catalog_sha256,
          history_epoch: captured.history_epoch,
          tools,
          trust: true,
        };
      if (
        !(await ask({
          title: '授权可信 Python 插件',
          message: `空间 ${t.agent}；插件 ${captured.name}（${captured.id}），版本 ${captured.version}。所选工具：${tools.join('、')}。此 Python 代码具有当前用户的本机权限；进程隔离不是操作系统沙箱。授权只允许后续调用，本期所有工具实际执行仍须逐次人工审批。包指纹 ${captured.package_sha256}；目录指纹 ${captured.catalog_sha256}。`,
          confirmLabel: '信任并授权所选工具',
        }))
      )
        return;
      if (
        !current(t) ||
        detail !== captured ||
        mutation ||
        !review().trust ||
        JSON.stringify([...review().tools].sort()) !== JSON.stringify(tools)
      )
        return;
      await mutate(captured, () =>
        api(`${root(t.agent)}/${t.selected}/authorization`, { method: 'PUT', body: request }),
      );
    }),
  );
  $('extension-disable').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        captured = detail;
      if (!captured || mutation) return;
      if (
        !(await ask({
          title: '禁用插件并核查回收',
          message: `空间 ${t.agent}；插件 ${captured.name}（${captured.id}）。撤销后续调用授权并请求宿主回收；已派发动作无法撤回，未知结果仍需核查。进程回收可能尚未完成，请以刷新后的实际状态为准。`,
          confirmLabel: '禁用并核查回收',
        }))
      )
        return;
      if (!current(t) || detail !== captured || mutation) return;
      await mutate(
        captured,
        () => api(`${root(t.agent)}/${t.selected}/disable`, { method: 'POST', body: { revision: captured.revision } }),
        true,
      );
    }),
  );
  $('extension-select').addEventListener(
    'change',
    wrap(async () => {
      selected = $('extension-select').value || null;
      viewRead++;
      clearDetail();
      restoreSource();
      await loadDetail();
    }),
  );
  $('extension-new').addEventListener('click', () => {
    if (mutation) return;
    selected = null;
    viewRead++;
    detailRead++;
    detailLoading = false;
    clearDetail();
    choices();
    restoreSource();
    $('extension-status').textContent = '新登记只保存包，不执行代码；输入草稿仅保存在当前网页内存。';
  });
  $('extension-refresh').addEventListener(
    'click',
    wrap(() => loadList()),
  );
  $('extension-more').addEventListener(
    'click',
    wrap(() => loadList(true)),
  );
  $('extension-refresh-detail').addEventListener('click', wrap(loadDetail));
  $('extension-actions-refresh').addEventListener(
    'click',
    wrap(() => loadActions(false)),
  );
  $('extension-actions-more').addEventListener(
    'click',
    wrap(() => loadActions(true)),
  );
  return {
    reset,
    resume: () => {
      viewRead++;
      detailRead++;
      mutation = null;
      detailLoading = false;
      clearDetail();
      $('extension-status').textContent = '页面已恢复，请显式刷新当前插件后再核查授权；路径草稿保留。';
      controls();
    },
    load: async () => {
      const t = target();
      if (owner !== t.agent) {
        owner = t.agent;
        selected = selections.get(owner) || null;
        viewRead++;
        restoreSource();
      }
      const wanted = selected;
      await loadList();
      if (!sameScope(t) || selected !== wanted) return;
      await loadDetail();
    },
  };
}
