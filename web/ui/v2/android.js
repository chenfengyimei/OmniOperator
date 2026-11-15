import { mountAndroidUnicode } from './android-unicode.js?v=20261115v128';
// Device content is ephemeral. Input preparation uses the shared approval/run ledger.
export function mountAndroid({ api, wrap, getAgent, getGeneration, getSession }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map();
  let epoch = 0,
    loadSequence = 0,
    readSequence = 0,
    expiryTimer = null,
    rows = new Map(),
    cursor = null,
    probe = null,
    observation = null,
    imageURL = null,
    pendingAction = null,
    lastRun = null;
  let accessCursor = null,
    appCursor = null,
    appRows = [];
  const draftFields = ['android-kind', 'android-direction', 'android-duration', 'android-key', 'android-package'];
  let displayedAgent = '';
  const path = (agent = getAgent()) => `/agents/${agent}/android`;
  const stamp = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;
  const unicode = mountAndroidUnicode({
    api,
    wrap,
    path,
    stamp,
    current,
    getGrant: () => $('android-list').value,
    getSession: () => $('android-session').value.trim(),
    onSaved: clearObservation,
    onRun: (id) => {
      lastRun = id;
      $('android-status').textContent = `恢复动作 ${id} 已准备；请在统一审批区核对，尚未执行。`;
    },
  });
  function save() {
    if (!getAgent()) return;
    if (!drafts.has(getAgent()) && drafts.size >= 32) throw new Error('安卓草稿较多，请先处理已有草稿');
    drafts.set(getAgent(), {
      config: $('android-config').value,
      text: $('android-text').value,
      fields: Object.fromEntries(draftFields.map((id) => [id, $(id).value])),
    });
  }
  function clearCapture() {
    if (imageURL) URL.revokeObjectURL(imageURL);
    imageURL = null;
    $('android-image').removeAttribute('src');
    $('android-image').hidden = true;
  }
  function release(value) {
    if (value) api(path(value.agent) + `/observations/${value.observation_id}`, { method: 'DELETE' }).catch(() => {});
  }
  function clearObservation() {
    pendingAction = null;
    clearTimeout(expiryTimer);
    expiryTimer = null;
    release(observation);
    observation = null;
    $('android-nodes').replaceChildren();
    $('android-tree').textContent = '';
    $('android-apps-result').textContent = '';
    appCursor = null;
    appRows = [];
    $('android-apps-more').disabled = true;
    $('android-action').disabled = true;
    clearCapture();
  }
  function deactivate() {
    epoch++;
    readSequence++;
    probe = null;
    clearObservation();
    unicode.deactivate();
    $('android-probe-result').textContent = '';
    $('android-grant').disabled = true;
  }
  $('android-config').addEventListener('change', () => {
    save();
    deactivate();
  });
  $('android-text').addEventListener('input', save);
  for (const id of draftFields) $(id).addEventListener('change', save);
  function renderReference() {
    const value = rows.get($('android-list').value);
    $('android-device-reference').textContent = value
      ? JSON.stringify(
          { grant_id: value.id, target_serial: value.device.serial, target_user: value.device.user_id },
          null,
          2,
        )
      : '';
  }
  $('android-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('android-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('android-list').addEventListener(
    'change',
    wrap(async () => {
      clearObservation();
      renderReference();
      await unicode.load();
    }),
  );
  async function load(more = false) {
    const loading = ++loadSequence;
    const t = stamp(),
      selected = $('android-list').value;
    const [grants, configs] = await Promise.all([
      api(path(t.agent) + '/grants?limit=50' + (more && cursor ? '&after=' + encodeURIComponent(cursor) : '')),
      api(`/agents/${t.agent}/configurations?kind=channel&limit=200`),
    ]);
    if (!current(t) || loading !== loadSequence) return;
    if (!more) rows = new Map();
    for (const row of grants.items) rows.set(row.id, row);
    cursor = grants.next_after;
    $('android-list').replaceChildren(
      ...[...rows.values()].map(
        (r) =>
          new Option(`${r.device.serial} · 用户 ${r.device.user_id} · ${r.revoked_at ? '已撤销' : '已授权'}`, r.id),
      ),
    );
    if (rows.has(selected)) $('android-list').value = selected;
    renderReference();
    const draft = drafts.get(t.agent),
      prior = $('android-config').value || draft?.config;
    $('android-config').replaceChildren(
      new Option('选择已启用的安卓通道', ''),
      ...configs.items.map((r) => new Option(`${r.name} · ${r.enabled ? '启用' : '停用'}`, r.id)),
    );
    if ([...$('android-config').options].some((o) => o.value === prior)) $('android-config').value = prior;
    if (draft && (displayedAgent !== t.agent || document.activeElement !== $('android-text')))
      $('android-text').value = draft.text;
    if (draft?.fields) for (const [id, value] of Object.entries(draft.fields)) $(id).value = value;
    displayedAgent = t.agent;
    if (!$('android-session').value) $('android-session').value = getSession() || '';
    $('android-capability').textContent =
      '本机已启动的 ADB 服务 · 显式设备授权 · Unicode需配对辅助组件；远程 Shell/自动配对不可用，设备和平台实测另记。';
    $('android-more').disabled = !cursor || rows.size >= 200;
    await loadAccess();
    if (!current(t) || loading !== loadSequence) return;
    await unicode.load();
    if (!current(t) || loading !== loadSequence) return;
    await unicode.environments();
  }
  async function loadAccess(more = false) {
    const t = stamp();
    const value = await api(
      path(t.agent) + '/access?limit=50' + (more && accessCursor ? '&after=' + encodeURIComponent(accessCursor) : ''),
    );
    if (!current(t)) return;
    if (!more) $('android-access-list').replaceChildren();
    accessCursor = value.next_after;
    for (const row of value.items) {
      const card = document.createElement('article'),
        detail = document.createElement('pre'),
        button = document.createElement('button');
      detail.textContent = JSON.stringify(
        {
          id: row.id,
          run_id: row.run_id,
          packages: row.packages,
          kinds: row.kinds,
          expires_at: row.expires_at,
          status: row.availability,
        },
        null,
        2,
      );
      button.type = 'button';
      button.textContent = '撤销本次运行权限';
      button.disabled = Boolean(row.revoked_at);
      button.addEventListener(
        'click',
        wrap(async () => {
          await api(path(t.agent) + `/access/${row.id}/revoke`, { method: 'POST', body: { revision: row.revision } });
          if (current(t)) await loadAccess();
        }),
      );
      card.append(detail, button);
      $('android-access-list').append(card);
    }
    $('android-access-more').disabled = !accessCursor || $('android-access-list').childElementCount >= 200;
  }
  $('android-access-refresh').addEventListener(
    'click',
    wrap(() => loadAccess()),
  );
  $('android-access-more').addEventListener(
    'click',
    wrap(() => loadAccess(true)),
  );
  async function apps(more = false) {
    const row = selected(),
      t = stamp();
    const result = await api(path(t.agent) + `/grants/${row.id}/apps`, {
      method: 'POST',
      body: { consent: true, after: more ? appCursor || '' : '' },
    });
    if (!current(t) || row.id !== $('android-list').value) return;
    appRows = more ? [...appRows, ...result.items] : result.items;
    appCursor = result.next_after;
    $('android-apps-result').textContent = appRows.join('\n');
    $('android-apps-more').disabled = !appCursor || appRows.length >= 1000;
  }
  $('android-apps').addEventListener(
    'click',
    wrap(() => apps()),
  );
  $('android-apps-more').addEventListener(
    'click',
    wrap(() => apps(true)),
  );
  function selected() {
    const row = rows.get($('android-list').value);
    if (!row || row.revoked_at) throw new Error('先选择有效设备授权');
    return row;
  }
  $('android-probe').addEventListener(
    'click',
    wrap(async () => {
      if (!$('android-consent').checked) throw new Error('请先确认读取指定设备身份');
      const t = stamp(),
        config = $('android-config').value;
      if (!config) throw new Error('先在配置中建立并选择安卓通道');
      const value = await api(path(t.agent) + '/probe', {
        method: 'POST',
        body: { config_id: config, consent: true },
      });
      if (!current(t) || config !== $('android-config').value) return;
      probe = value;
      $('android-probe-result').textContent = JSON.stringify(value, null, 2);
      $('android-grant').disabled = false;
    }),
  );
  $('android-grant').addEventListener(
    'click',
    wrap(async () => {
      if (!probe || !$('android-consent').checked) throw new Error('重新核验设备并确认授权');
      const t = stamp();
      await api(path(t.agent) + '/grants', {
        method: 'POST',
        body: { probe_id: probe.probe_id, consent: true },
      });
      if (!current(t)) return;
      probe = null;
      $('android-grant').disabled = true;
      await load();
    }),
  );
  $('android-revoke').addEventListener(
    'click',
    wrap(async () => {
      const row = selected(),
        t = stamp();
      await api(path(t.agent) + `/grants/${row.id}/revoke`, {
        method: 'POST',
        body: { revision: row.revision },
      });
      if (!current(t)) return;
      clearObservation();
      $('android-status').textContent = '设备授权已撤销；已派发的输入不能保证撤回。';
      await load();
    }),
  );
  $('android-observe').addEventListener(
    'click',
    wrap(async () => {
      const row = selected();
      clearObservation();
      const reading = ++readSequence;
      const t = stamp();
      const value = await api(path(t.agent) + `/grants/${row.id}/observe`, {
        method: 'POST',
        body: { consent: true },
      });
      if (!current(t) || reading !== readSequence || row.id !== $('android-list').value) {
        release({ ...value, agent: t.agent });
        return;
      }
      observation = { ...value, agent: t.agent };
      expiryTimer = setTimeout(() => {
        clearObservation();
        $('android-status').textContent = '界面观察已过期，请重新读取。';
      }, value.expires_in_s * 1000);
      $('android-tree').textContent = JSON.stringify(value, null, 2);
      $('android-nodes').replaceChildren(
        ...value.nodes
          .filter((n) => n.actions.length)
          .map((n) => new Option(`${n.resource_id || n.class} · ${n.text || n.description}`, n.node_id)),
      );
      $('android-action').disabled = !value.window.unlocked || !$('android-nodes').options.length;
      $('android-status').textContent =
        `${value.window.package} · ${value.window.unlocked ? '已核验解锁' : '无法证明解锁，输入不可用'} · 观察有效期60秒${value.truncated ? ' · 界面树已截断' : ''}`;
    }),
  );
  $('android-capture').addEventListener(
    'click',
    wrap(async () => {
      const row = selected(),
        session = $('android-session').value.trim(),
        t = stamp();
      if (!session) throw new Error('截图需要归属会话 UUID');
      const value = await api(path(t.agent) + `/grants/${row.id}/capture`, {
        method: 'POST',
        body: { consent: true, session_id: session },
      });
      const blob = await api(`/agents/${t.agent}/objects/${value.object_id}/content`, { binary: true });
      if (!current(t) || row.id !== $('android-list').value) return;
      clearCapture();
      imageURL = URL.createObjectURL(blob);
      $('android-image').src = imageURL;
      $('android-image').hidden = false;
      $('android-status').textContent = `截图已保存到会话 · ${value.width}×${value.height} · 未自动附加模型`;
    }),
  );
  $('android-action-form').addEventListener(
    'submit',
    wrap(async (event) => {
      event.preventDefault();
      const row = selected(),
        t = stamp();
      if (!observation) throw new Error('先重新观察并选择控件');
      const kind = $('android-kind').value,
        node = observation.nodes.find((n) => n.node_id === $('android-nodes').value);
      if (!node || !node.actions.includes(kind)) throw new Error('该控件不支持所选动作');
      const request = {
        consent: true,
        grant_id: row.id,
        observation_id: observation.observation_id,
        node_id: node.node_id,
        session_id: $('android-session').value.trim(),
        kind,
        text: ['append_ascii', 'append_unicode'].includes(kind) ? $('android-text').value : '',
        params:
          kind === 'append_unicode'
            ? { helper_revision: observation.unicode_helper.revision, restore_ime: true }
            : kind === 'long_press'
              ? { duration_ms: Number($('android-duration').value) }
              : kind === 'swipe'
                ? { direction: $('android-direction').value, duration_ms: Number($('android-duration').value) }
                : kind === 'key'
                  ? { key: $('android-key').value }
                  : kind === 'launch'
                    ? { package: $('android-package').value.trim() }
                    : {},
      };
      const signature = JSON.stringify(request);
      if (pendingAction?.signature !== signature) pendingAction = { signature, request_id: crypto.randomUUID() };
      const value = await api(path(t.agent) + '/actions', {
        method: 'POST',
        body: { ...request, request_id: pendingAction.request_id },
      });
      if (!current(t)) return;
      lastRun = value.run_id;
      $('android-status').textContent = `已准备动作 ${lastRun}；请在统一审批区核对设备、目标和文本。尚未执行。`;
      $('android-action').disabled = true;
    }),
  );
  $('android-run-status').addEventListener(
    'click',
    wrap(async () => {
      if (!lastRun) throw new Error('尚无本次动作运行');
      const t = stamp(),
        value = await api(`/agents/${t.agent}/runs/${lastRun}`);
      if (current(t))
        $('android-status').textContent =
          `动作状态：${value.state} · ${value.error_code || ''}；unknown需要在「动作核查」处理，不可重发。`;
    }),
  );
  return {
    load,
    deactivate,
    reset() {
      deactivate();
      rows.clear();
      cursor = null;
      lastRun = null;
      displayedAgent = '';
      $('android-session').value = '';
      $('android-text').value = '';
      $('android-consent').checked = false;
      $('android-list').replaceChildren();
      $('android-config').replaceChildren();
      $('android-status').textContent = '';
      $('android-device-reference').textContent = '';
      $('android-access-list').replaceChildren();
      accessCursor = null;
      const defaults = ['tap', 'up', '600', 'BACK', ''];
      draftFields.forEach((id, index) => {
        $(id).value = defaults[index];
      });
    },
  };
}
