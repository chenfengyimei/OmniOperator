// Commands stay in their observation view; leaving it revokes undispatched work.
export function renderDesktopActions({ api, wrap, perform, currentSession, captured, observed, node, parent }) {
  if (!node.actions?.length) return;
  const row = document.createElement('div'),
    title = document.createElement('p'),
    kind = document.createElement('select'),
    text = document.createElement('input'),
    key = document.createElement('select'),
    takeoverLabel = document.createElement('label'),
    takeover = document.createElement('input'),
    hint = document.createElement('p'),
    submit = document.createElement('button'),
    refresh = document.createElement('button'),
    cancel = document.createElement('button'),
    status = document.createElement('p'),
    approval = document.createElement('div');
  title.textContent = `${node.role} · ${node.name || '未命名控件'}`;
  status.setAttribute('role', 'status');
  kind.setAttribute('aria-label', '语义动作');
  const labels = {
    invoke: '调用',
    select: '选择',
    set_text: '设置文本',
    type_text: '前台输入文本',
    press_key: '前台按键',
    click: '前台点击',
  };
  const foreground = () => ['type_text', 'press_key', 'click'].includes(kind.value);
  for (const action of node.actions) kind.add(new Option(labels[action], action));
  text.maxLength = 512;
  text.setAttribute('aria-label', '待设置的文本');
  key.setAttribute('aria-label', '前台按键');
  for (const name of ['Enter', 'Tab', 'Escape', 'Backspace', 'Delete', 'Home', 'End', 'Left', 'Right', 'Up', 'Down'])
    key.add(new Option(name, name));
  takeover.type = 'checkbox';
  takeoverLabel.append(takeover, '我授权本次前台接管，仅执行当前审批中的一个动作');
  hint.textContent =
    '批准后请在10秒内手动切换到目标窗口；输入文本或按键还需将焦点放到选定控件。保持片刻静止后执行。介入、失焦或撤销将暂停本次动作，须重新观察和授权。';
  const update = () => {
    text.hidden = !['set_text', 'type_text'].includes(kind.value);
    text.maxLength = kind.value === 'type_text' ? 128 : 512;
    key.hidden = kind.value !== 'press_key';
    takeoverLabel.hidden = hint.hidden = !foreground();
    if (!foreground()) takeover.checked = false;
  };
  kind.addEventListener('change', update);
  update();
  submit.type = refresh.type = cancel.type = 'button';
  submit.textContent = '提交动作审批';
  refresh.textContent = '刷新动作状态';
  cancel.textContent = '取消动作';
  refresh.hidden = cancel.hidden = true;
  let receipt = null,
    request = null;
  const live = () => currentSession(captured) && row.isConnected;
  const base = `/agents/${captured.agent}`;

  async function reload() {
    if (!live() || !receipt) return;
    const run = await api(`${base}/runs/${receipt.run_id}`);
    const pending = run.state === 'waiting_approval' ? (await api(`${base}/approvals`)).items : [];
    if (!live()) return;
    status.textContent = `运行 ${receipt.run_id} · ${run.state}${run.error_code ? ' · ' + run.error_code : ''}`;
    if (run.error_code === 'action_outcome_unknown')
      status.textContent += '。请独立检查目标应用，再到动作核查页登记结论；不要重复提交。';
    approval.replaceChildren();
    const item = pending.find((entry) => entry.run_id === receipt.run_id && entry.tool === 'desktop_semantic');
    if (!item) return;
    const details = document.createElement('pre');
    details.textContent = `目标应用：${item.arguments.target_application}\n控件：${item.arguments.target_role}\n动作：${labels[item.arguments.kind]}\n文本：${item.arguments.text}\n策略：${item.arguments.policy}\n观察失效、离开页面或用户介入会阻止后续派发。`;
    if (item.arguments.takeover)
      details.textContent += '\n本次已请求前台接管。批准后请手动切到目标窗口/控件，程序会等待焦点；不会自动抢焦点。';
    approval.append(details);
    for (const allowed of [true, false]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = allowed ? '批准本次动作' : '拒绝';
      button.addEventListener(
        'click',
        wrap(() =>
          perform(async () => {
            if (!live()) return;
            await api(`${base}/approvals/${item.id}`, {
              method: 'POST',
              body: { allowed, policy_revision: item.policy_revision },
            });
            await reload();
          }),
        ),
      );
      approval.append(button);
    }
  }

  submit.addEventListener(
    'click',
    wrap(() =>
      perform(async () => {
        if (!live() || !captured.session || receipt) return;
        if (foreground() && !takeover.checked) {
          status.textContent = '请先明确授权本次前台接管，再提交动作审批。';
          return;
        }
        request ??= {
          consent: true,
          view_id: captured.viewId,
          snapshot_id: observed.snapshot_id,
          target_id: observed.target_id,
          observation_id: observed.observation_id,
          node_id: node.node_id,
          session_id: captured.session,
          request_id: crypto.randomUUID(),
          kind: kind.value,
          text: ['set_text', 'type_text'].includes(kind.value)
            ? text.value
            : kind.value === 'press_key'
              ? key.value
              : '',
          ...(foreground() ? { takeover: true } : {}),
        };
        // Keep the same id and payload when a response is lost; changing content requires a fresh observation.
        kind.disabled = text.disabled = key.disabled = takeover.disabled = true;
        receipt = await api(`${base}/desktop/actions`, { method: 'POST', body: request });
        if (!live()) return;
        submit.hidden = true;
        refresh.hidden = cancel.hidden = false;
        status.textContent = '已排队。请刷新状态后核对并批准；本页不自动授权或轮询。';
        await reload();
      }),
    ),
  );
  refresh.addEventListener(
    'click',
    wrap(() => perform(reload)),
  );
  cancel.addEventListener(
    'click',
    wrap(() =>
      perform(async () => {
        if (!live() || !receipt) return;
        await api(`${base}/runs/${receipt.run_id}/cancel`, { method: 'POST' });
        await reload();
      }),
    ),
  );
  row.append(title, kind, text, key, takeoverLabel, hint, submit, refresh, cancel, status, approval);
  parent.append(row);
}
