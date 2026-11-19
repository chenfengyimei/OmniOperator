import { renderDesktopActions } from './desktop-actions.js?v=20261119v156';

export function mountDesktop({ api, wrap, getAgent, getGeneration, getSession }) {
  const $ = (id) => document.getElementById(id);
  const capabilities = {
    window_identity: '窗口身份',
    accessibility: '界面语义访问',
    screen_capture: '屏幕采集',
    foreground_control: '前台接管',
  };
  const permissions = { granted: '已允许', denied: '未允许', unknown: '尚未核验', not_applicable: '无需单独授权' };
  const openings = new Map();
  let active = false,
    owner = '',
    view = 0,
    viewId = '',
    established = false,
    busy = false,
    snapshot = null,
    timer = null,
    available = false,
    observationAvailable = false,
    captureAvailable = false,
    captureCursor = null;
  const previews = new Set();
  let portalGrant = null;
  let controlGrant = null;
  const root = (agent = owner) => `/agents/${agent}/desktop`;
  const target = () => ({ agent: owner, generation: getGeneration(), session: getSession(), view, viewId });
  const current = (captured) =>
    active &&
    captured.agent === owner &&
    captured.agent === getAgent() &&
    captured.generation === getGeneration() &&
    captured.view === view &&
    captured.viewId === viewId;

  function controls() {
    $('desktop-refresh').disabled = !active || busy;
    $('desktop-probe').disabled = !active || busy || !established;
    $('desktop-list').disabled = !active || busy || !available || !established;
    for (const button of $('desktop-targets').querySelectorAll('button'))
      button.disabled =
        !active ||
        busy ||
        button.dataset.supported === 'false' ||
        !snapshot ||
        (button.dataset.observe === 'true' && !observationAvailable) ||
        (button.dataset.capture === 'true' && (!captureAvailable || !getSession()));
    $('desktop-captures-refresh').disabled = !active || busy || !getSession();
    $('desktop-captures-more').disabled = !active || busy;
    for (const button of $('desktop-captures').querySelectorAll('button')) button.disabled = !active || busy;
    $('desktop-portal-open').disabled = !active || busy || !established;
    $('desktop-portal-query').disabled = $('desktop-portal-close').disabled = !active || busy || !portalGrant;
    $('desktop-portal-capture').disabled = !active || busy || !getSession() || !portalGrant?.capture_available;
    $('desktop-control-consent').disabled = $('desktop-control-devices').disabled = !active || busy || !established;
    $('desktop-control-open').disabled = !active || busy || !established || !$('desktop-control-consent').checked;
    $('desktop-control-query').disabled = $('desktop-control-close').disabled = !active || busy || !controlGrant;
  }

  function clearControl() {
    controlGrant = null;
    $('desktop-control-status').textContent = '';
    $('desktop-control-consent').checked = false;
    $('desktop-control-devices').value = '1';
    $('desktop-control').hidden = true;
  }

  function renderControl(grant) {
    controlGrant = grant;
    const phases = {
      starting: '正在准备许可申请',
      prepared: '准备请求系统许可',
      creating: '正在建立控制会话',
      selecting: '正在选择设备',
      authorizing: '等待你确认系统许可',
      system_authorized: '系统许可已记录，尚未启用键鼠输入',
      closing: '正在结束控制许可',
      closed: '控制许可已结束',
      denied: '你未同意系统许可',
      failed: '控制许可不可用',
    };
    const label = grant?.authorization_pending ? '系统已同意，正在确认记录' : phases[grant?.phase] || '状态待核查';
    $('desktop-control-status').textContent = grant
      ? `${label}。${grant.cleanup_pending ? '会话仍在关闭，请查询状态。' : ''}${grant.error_code ? ' 原因：' + grant.error_code : ''}`
      : '本次视图暂无独立控制许可。';
    controls();
  }

  function clearPortal() {
    portalGrant = null;
    $('desktop-portal-status').textContent = '';
    $('desktop-portal').hidden = true;
  }

  function renderPortal(grant) {
    portalGrant = grant;
    const phases = {
      starting: '正在准备',
      prepared: '等待系统选择',
      creating: '正在建立共享',
      selecting: '正在选择窗口',
      authorizing: '等待你授权窗口',
      active: '窗口共享已授权',
      closing: '正在撤回',
      closed: '共享已结束',
      denied: '未授权',
      failed: '共享不可用',
    };
    const phaseText =
      grant?.authorization_pending === true
        ? '正在确认共享授权，请稍后查询共享状态'
        : phases[grant?.phase] || '状态待核查';
    $('desktop-portal-status').textContent = grant
      ? `${phaseText}。${grant.cleanup_pending ? '资源仍在回收，请查询状态。' : ''}${grant.capture_available ? '可以显式保存当前窗口单帧。' : '当前不能保存画面。'}${grant.error_code || grant.capture_error_code ? ' 原因：' + (grant.error_code || grant.capture_error_code) : ''}`
      : '本次视图暂无窗口共享。';
    controls();
  }

  const currentSession = (captured) => current(captured) && captured.session === getSession();

  function clearCaptures() {
    for (const preview of previews) preview.removeAttribute('src');
    previews.clear();
    $('desktop-captures').replaceChildren();
    captureCursor = null;
    $('desktop-captures-more').hidden = true;
  }

  function renderCapture(item, captured) {
    const article = document.createElement('article'),
      label = document.createElement('p'),
      preview = document.createElement('img'),
      show = document.createElement('button'),
      remove = document.createElement('button');
    label.textContent = `${item.created_at} · ${item.width} × ${item.height} · 已保存，未附加到模型请求`;
    preview.alt = '用户显式保存的窗口截图';
    preview.className = 'desktop-capture-preview';
    preview.hidden = true;
    previews.add(preview);
    show.type = remove.type = 'button';
    show.textContent = '查看截图';
    remove.textContent = '移除会话引用';
    show.addEventListener(
      'click',
      wrap(() =>
        perform(async () => {
          if (!currentSession(captured)) return;
          const blob = await api(`/agents/${captured.agent}/objects/${item.object_id}/content`, { binary: true });
          // A single bounded data URL uses the existing img-src policy and carries no owner token.
          if (blob.size > 16 * 1024 * 1024) throw new Error('Screenshot exceeds the display limit');
          const source = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error('Screenshot could not be read'));
            reader.readAsDataURL(new Blob([blob], { type: 'image/png' }));
          });
          if (!currentSession(captured) || !article.isConnected) return;
          for (const other of previews) {
            other.removeAttribute('src');
            other.hidden = true;
          }
          preview.src = source;
          preview.hidden = false;
          $('desktop-status').textContent = '已读取保存的截图；画面完整性需人工查看，未发送给模型。';
        }),
      ),
    );
    remove.addEventListener(
      'click',
      wrap(() =>
        perform(async () => {
          if (!currentSession(captured)) return;
          await api(`/agents/${captured.agent}/sessions/${captured.session}/captures/${item.id}`, { method: 'DELETE' });
          if (!currentSession(captured)) return;
          preview.removeAttribute('src');
          previews.delete(preview);
          article.remove();
          $('desktop-captures-status').textContent = '引用已移除。无其他引用且未固定的对象到期后可清理。';
        }),
      ),
    );
    article.append(label, show, remove, preview);
    $('desktop-captures').append(article);
  }

  async function loadCaptures(captured, more = false) {
    if (!currentSession(captured) || !captured.session) return;
    if (!more) clearCaptures();
    const result = await api(
      `/agents/${captured.agent}/sessions/${captured.session}/captures?limit=50${more && captureCursor ? '&after=' + encodeURIComponent(captureCursor) : ''}`,
    );
    if (!currentSession(captured)) return;
    for (const item of result.items) renderCapture(item, captured);
    captureCursor = result.next_after;
    $('desktop-captures-more').hidden = !captureCursor;
    $('desktop-captures-status').textContent = $('desktop-captures').childElementCount
      ? '仅加载记录；点击查看才读取图片。'
      : '当前会话暂无截图。';
  }

  function clearTargets() {
    clearTimeout(timer);
    timer = null;
    snapshot = null;
    $('desktop-targets').replaceChildren();
  }

  function render(report, checkedAt) {
    clearPortal();
    clearControl();
    $('desktop-portal').hidden = report?.platform !== 'linux' || report?.session?.kind !== 'interactive';
    $('desktop-control').hidden = report?.platform !== 'linux' || report?.session?.kind !== 'interactive';
    available = Boolean(report?.capabilities?.find((item) => item.id === 'window_identity')?.available);
    observationAvailable = Boolean(report?.capabilities?.find((item) => item.id === 'accessibility')?.available);
    captureAvailable = Boolean(report?.capabilities?.find((item) => item.id === 'screen_capture')?.available);
    $('desktop-capabilities').replaceChildren();
    if (!report) {
      $('desktop-summary').textContent = '尚未检查。检查仅查询当前会话与权限，不打开系统授权窗口。';
      controls();
      return;
    }
    const session = { interactive: '已核验交互会话', headless: '无交互桌面', unknown: '桌面会话尚未核验' }[
      report.session.kind
    ];
    $('desktop-summary').textContent =
      `${report.platform} · ${session} · 上次检查 ${checkedAt || '未知时间'}。平台完整验收仍待完成。`;
    for (const item of report.capabilities) {
      const article = document.createElement('article'),
        heading = document.createElement('h2'),
        state = document.createElement('p');
      heading.textContent = capabilities[item.id] || item.id;
      state.textContent = `${item.available ? '上次检查可用' : '上次检查不可用'} · 组件${item.installed ? '已安装' : '未接入'} · 权限${permissions[item.permission] || '未知'}`;
      article.append(heading, state);
      for (const hint of [...item.limitations, ...item.recovery]) {
        const paragraph = document.createElement('p');
        paragraph.className = 'muted';
        paragraph.textContent = hint;
        article.append(paragraph);
      }
      $('desktop-capabilities').append(article);
    }
    controls();
  }

  function renderRuntime(runtime) {
    const staging = runtime?.backend?.capture_staging;
    $('desktop-staging').textContent = staging?.error_code
      ? `截图暂存需要核查：${staging.error_code}${staging.residual_count ? '，未归属项目至少 ' + staging.residual_count + ' 项' : ''}。这些内容未自动删除；请在停止实例后核查私有 run/desktop-captures 目录。`
      : '';
  }

  function forget(agent, id) {
    if (!agent || !id) return;
    // Detach survives the main page's old-request abort. It never cancels an owned native operation.
    api(root(agent) + '/snapshot?view_id=' + encodeURIComponent(id), {
      method: 'DELETE',
      signal: AbortSignal.timeout(6000),
    }).catch(() => {});
  }

  function begin(captured) {
    const previous = openings.get(captured.agent) || Promise.resolve();
    const pending = previous
      .catch(() => {})
      .then(() =>
        api(root(captured.agent) + '/view', {
          method: 'POST',
          body: { consent: true, view_id: captured.viewId },
          signal: AbortSignal.timeout(6000),
        }),
      );
    openings.set(captured.agent, pending);
    const cleanup = () => {
      if (openings.get(captured.agent) === pending) openings.delete(captured.agent);
    };
    pending.then(cleanup, cleanup);
    return pending;
  }

  async function perform(action) {
    if (!active || busy) return;
    const captured = target();
    busy = true;
    $('desktop-status').textContent = '正在核验…';
    controls();
    try {
      await action(captured);
      if (current(captured) && $('desktop-status').textContent === '正在核验…')
        $('desktop-status').textContent = '本次操作已返回，请查看对应窗口的状态。';
    } catch (error) {
      if (current(captured)) {
        if (['desktop_snapshot_expired', 'desktop_view_changed'].includes(error.code)) clearTargets();
        if (error.code === 'desktop_view_changed') established = false;
        $('desktop-status').textContent =
          error.code === 'desktop_snapshot_expired'
            ? '窗口观察已过期，请重新列举。'
            : `本次检查未完成，请重新载入视图后重试。${error.code ? ' 原因：' + error.code : ''}`;
      }
    } finally {
      if (current(captured)) {
        busy = false;
        controls();
      }
    }
  }

  $('desktop-control-consent').addEventListener('change', controls);
  $('desktop-control-devices').addEventListener('change', () => {
    $('desktop-control-consent').checked = false;
    controls();
  });
  $('desktop-control-open').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        if (!$('desktop-control-consent').checked) return;
        const devices = Number($('desktop-control-devices').value);
        $('desktop-control-consent').checked = false;
        const grant = await api(root(captured.agent) + '/control', {
          method: 'POST',
          body: { consent: true, purpose: 'interactive-input', view_id: captured.viewId, devices },
        });
        if (!current(captured)) return;
        renderControl(grant);
        $('desktop-status').textContent = '请在系统窗口确认所选设备，再查询控制许可。窗口共享不会授予此许可。';
      }),
    ),
  );
  $('desktop-control-query').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        const value = await api(root(captured.agent) + '/control?view_id=' + encodeURIComponent(captured.viewId));
        if (current(captured)) renderControl(value.grant);
      }),
    ),
  );
  $('desktop-control-close').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        const grant = controlGrant;
        if (!grant) return;
        await api(
          root(captured.agent) +
            '/control?view_id=' +
            encodeURIComponent(captured.viewId) +
            '&grant_id=' +
            encodeURIComponent(grant.grant_id),
          { method: 'DELETE' },
        );
        if (!current(captured)) return;
        renderControl(null);
        $('desktop-control-consent').checked = false;
        controls();
      }),
    ),
  );

  $('desktop-portal-open').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        const grant = await api(root(captured.agent) + '/portal', {
          method: 'POST',
          body: { consent: true, view_id: captured.viewId },
        });
        if (!current(captured)) return;
        renderPortal(grant);
        $('desktop-status').textContent = '请在系统选择器中选择临时目标窗口，再点击“查询共享状态”。';
      }),
    ),
  );
  $('desktop-portal-query').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        const value = await api(root(captured.agent) + '/portal?view_id=' + encodeURIComponent(captured.viewId));
        if (current(captured)) renderPortal(value.grant);
      }),
    ),
  );
  $('desktop-portal-close').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        const grant = portalGrant;
        if (!grant) return;
        await api(
          root(captured.agent) +
            '/portal?view_id=' +
            encodeURIComponent(captured.viewId) +
            '&grant_id=' +
            encodeURIComponent(grant.grant_id),
          { method: 'DELETE' },
        );
        if (current(captured)) renderPortal(null);
      }),
    ),
  );
  $('desktop-portal-capture').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        const grant = portalGrant;
        if (!grant || !currentSession(captured) || !captured.session) return;
        const saved = await api(root(captured.agent) + '/portal/capture', {
          method: 'POST',
          body: {
            consent: true,
            view_id: captured.viewId,
            grant_id: grant.grant_id,
            session_id: captured.session,
          },
        });
        if (!currentSession(captured) || portalGrant?.grant_id !== grant.grant_id) return;
        await loadCaptures(captured);
        if (currentSession(captured))
          $('desktop-status').textContent = `共享窗口单帧已保存 · ${saved.width} × ${saved.height}。未发送给模型。`;
      }),
    ),
  );

  $('desktop-probe').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        clearTargets();
        render(null, null);
        const value = await api(root(captured.agent) + '/probe', {
          method: 'POST',
          body: { consent: true, view_id: captured.viewId },
        });
        if (!current(captured)) return;
        render(value.capabilities, value.checked_at);
        renderRuntime(value.runtime);
        $('desktop-status').textContent = '能力检查已完成；未申请系统权限或执行桌面动作。';
      }),
    ),
  );

  $('desktop-list').addEventListener(
    'click',
    wrap(() =>
      perform(async (captured) => {
        clearTargets();
        const value = await api(root(captured.agent) + '/targets', {
          method: 'POST',
          body: { consent: true, view_id: captured.viewId, limit: 50 },
        });
        if (!current(captured)) {
          forget(captured.agent, captured.viewId);
          return;
        }
        snapshot = value;
        $('desktop-status').textContent = value.items.length
          ? `本次列出 ${value.items.length} 个窗口${value.truncated ? '，结果已达上限' : ''}；观察 ${value.expires_in_s} 秒后过期。`
          : '当前没有可核验的窗口。';
        for (const item of value.items) {
          const article = document.createElement('article'),
            name = document.createElement('p'),
            button = document.createElement('button'),
            status = document.createElement('p'),
            observe = document.createElement('button'),
            capture = document.createElement('button'),
            contents = document.createElement('pre');
          name.textContent = `${item.application} · PID ${item.pid}`;
          button.type = 'button';
          button.textContent = '重新核验身份';
          observe.type = 'button';
          observe.dataset.observe = 'true';
          observe.dataset.supported = String(item.can_observe !== false);
          observe.textContent = '读取界面内容';
          capture.type = 'button';
          capture.dataset.capture = 'true';
          capture.dataset.supported = String(item.can_capture !== false);
          capture.textContent = '截图保存至当前会话';
          contents.setAttribute('aria-label', '本次界面观察');
          status.setAttribute('role', 'status');
          button.addEventListener(
            'click',
            wrap(() =>
              perform(async (checking) => {
                if (!snapshot || snapshot.snapshot_id !== value.snapshot_id) return;
                const verified = await api(root(checking.agent) + '/verify', {
                  method: 'POST',
                  body: {
                    consent: true,
                    view_id: checking.viewId,
                    snapshot_id: value.snapshot_id,
                    target_id: item.target_id,
                  },
                });
                if (!current(checking) || snapshot?.snapshot_id !== value.snapshot_id) return;
                status.textContent = verified.matches
                  ? '当前窗口与进程元数据匹配。该观察不授予点击、输入或截图权限。'
                  : '身份已变化或窗口不可用，请重新列举。';
                if (!verified.matches) {
                  button.remove();
                  observe.remove();
                  capture.remove();
                  contents.textContent = '';
                  commands.replaceChildren();
                }
                $('desktop-status').textContent = '本次身份检查已返回。';
              }),
            ),
          );
          observe.addEventListener(
            'click',
            wrap(() =>
              perform(async (checking) => {
                contents.textContent = '';
                commands.replaceChildren();
                if (!snapshot || snapshot.snapshot_id !== value.snapshot_id) return;
                const observed = await api(root(checking.agent) + '/observe', {
                  method: 'POST',
                  body: {
                    consent: true,
                    view_id: checking.viewId,
                    snapshot_id: value.snapshot_id,
                    target_id: item.target_id,
                  },
                });
                if (!current(checking) || snapshot?.snapshot_id !== value.snapshot_id) return;
                const depths = new Map();
                contents.textContent = observed.nodes
                  .map((node) => {
                    const depth = node.parent_id ? (depths.get(node.parent_id) || 0) + 1 : 0;
                    depths.set(node.node_id, depth);
                    return (
                      '  '.repeat(depth) +
                      node.role +
                      ' · ' +
                      (node.password ? '[受保护控件]' : [node.name, node.text].filter(Boolean).join(' · '))
                    );
                  })
                  .join('\n');
                commands.replaceChildren();
                for (const node of observed.nodes)
                  renderDesktopActions({
                    api,
                    wrap,
                    perform,
                    currentSession,
                    captured: checking,
                    observed,
                    node,
                    parent: commands,
                  });
                status.textContent = `观察时间 ${observed.observed_at} · ${observed.nodes.length} 个节点${observed.truncated ? ' · 内容已截断' : ''}。仅供本次查看，不授予操作权限。`;
                $('desktop-status').textContent = '界面观察已返回；内容未写入诊断日志，离开或过期后清除。';
              }),
            ),
          );
          capture.addEventListener(
            'click',
            wrap(() =>
              perform(async (checking) => {
                if (!currentSession(checking) || !checking.session || snapshot?.snapshot_id !== value.snapshot_id)
                  return;
                const saved = await api(root(checking.agent) + '/capture', {
                  method: 'POST',
                  body: {
                    consent: true,
                    view_id: checking.viewId,
                    snapshot_id: value.snapshot_id,
                    target_id: item.target_id,
                    session_id: checking.session,
                  },
                });
                if (!currentSession(checking) || snapshot?.snapshot_id !== value.snapshot_id) return;
                status.textContent = `截图已保存 · ${saved.width} × ${saved.height}。未发送给模型。`;
                await loadCaptures(checking);
                $('desktop-status').textContent = '窗口截图已保存至当前会话，可在下方显式查看或移除引用。';
              }),
            ),
          );
          const commands = document.createElement('div');
          article.append(name, button, observe, capture, status, contents, commands);
          $('desktop-targets').append(article);
        }
        timer = setTimeout(() => {
          if (!current(captured) || snapshot?.snapshot_id !== value.snapshot_id) return;
          clearTargets();
          $('desktop-status').textContent = '窗口观察已过期，请重新列举。';
          controls();
        }, value.expires_in_s * 1000);
      }),
    ),
  );

  function deactivate() {
    const previous = target(),
      wasActive = active;
    active = false;
    established = false;
    view++;
    busy = false;
    clearTargets();
    clearCaptures();
    clearPortal();
    clearControl();
    renderRuntime(null);
    controls();
    if (wasActive) forget(previous.agent, previous.viewId);
  }

  function reset() {
    deactivate();
    owner = '';
    viewId = '';
    available = false;
    render(null, null);
    $('desktop-status').textContent = '';
  }

  async function load() {
    deactivate();
    owner = getAgent();
    viewId = crypto.randomUUID();
    active = true;
    available = false;
    const captured = target();
    busy = true;
    render(null, null);
    try {
      const state = await begin(captured);
      if (!current(captured)) {
        forget(captured.agent, captured.viewId);
        return;
      }
      established = true;
      render(state.capabilities, state.checked_at);
      renderRuntime(state.runtime);
      $('desktop-status').textContent = '窗口列举由你显式发起；本页不自动扫描或轮询桌面。';
      $('desktop-captures-status').textContent = getSession()
        ? '截图将保存至当前会话，点击加载可查看已有记录。'
        : '请先在对话页建立或选择会话，再保存截图。';
    } finally {
      if (!current(captured)) forget(captured.agent, captured.viewId);
      if (current(captured)) {
        busy = false;
        controls();
      }
    }
  }

  controls();
  $('desktop-captures-refresh').addEventListener(
    'click',
    wrap(() => perform((captured) => loadCaptures(captured))),
  );
  $('desktop-captures-more').addEventListener(
    'click',
    wrap(() => perform((captured) => loadCaptures(captured, true))),
  );
  $('desktop-refresh').addEventListener('click', wrap(load));
  return { load, deactivate, reset };
}
