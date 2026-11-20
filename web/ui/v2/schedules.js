import { ask } from '../dialog.js?v=20261103v7';

export function mountSchedules({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const drafts = new Map(),
    selections = new Map(),
    choices = new Map(),
    writes = new Set();
  let selected = '',
    epoch = 0,
    cursor = null,
    occurrenceCursor = null,
    historyCursor = null,
    occurrenceCount = 0,
    historyCount = 0,
    blocker = null;
  const root = (a = getAgent()) => `/agents/${a}/schedules`;
  const key = (a = getAgent(), id = selected) => `${a}/${id}`;
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
  const DOW_NAMES = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const pad = (n) => String(n).padStart(2, '0');
  function buildChips(pick, entries, cls) {
    for (const [value, label] of entries) {
      const chip = document.createElement('label');
      chip.className = 'wiz-chip';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.className = cls;
      input.value = String(value);
      const span = document.createElement('span');
      span.textContent = label;
      chip.append(input, span);
      pick.append(chip);
    }
  }
  buildChips(
    document.querySelector('#schedule-wiz-weekdays .wiz-pick'),
    [1, 2, 3, 4, 5, 6, 0].map((d) => [d, DOW_NAMES[d]]),
    'wiz-dow',
  );
  buildChips(
    document.querySelector('#schedule-wiz-days .wiz-pick'),
    Array.from({ length: 31 }, (_, i) => [i + 1, String(i + 1)]),
    'wiz-day',
  );
  const wizModes = [...document.querySelectorAll('input[name="schedule-wiz-mode"]')];
  const dowBoxes = [...document.querySelectorAll('.wiz-dow')];
  const dayBoxes = [...document.querySelectorAll('.wiz-day')];
  function parseWizard(expression) {
    const fields = String(expression ?? '')
      .trim()
      .split(/\s+/);
    if (fields.length !== 5 || fields[3] !== '*') return null;
    const [minute, hour, day, , week] = fields;
    if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return null;
    const m = Number(minute),
      h = Number(hour);
    if (m > 59 || h > 23) return null;
    const listOf = (text, lower, upper) => {
      if (!/^\d{1,2}(,\d{1,2})*$/.test(text)) return null;
      const numbers = text.split(',').map(Number);
      if (numbers.some((n) => n < lower || n > upper) || new Set(numbers).size !== numbers.length) return null;
      return numbers;
    };
    if (day === '*' && week === '*') return { mode: 'daily', h, m };
    if (day === '*') {
      const numbers = listOf(week, 0, 7);
      if (!numbers) return null;
      return { mode: 'weekly', h, m, days: [...new Set(numbers.map((n) => n % 7))].sort((a, b) => a - b) };
    }
    if (week === '*') {
      const numbers = listOf(day, 1, 31);
      if (!numbers) return null;
      return { mode: 'monthly', h, m, days: numbers.sort((a, b) => a - b) };
    }
    return null;
  }
  function compileWizard() {
    const mode = wizModes.find((radio) => radio.checked)?.value || 'daily';
    const previous = parseWizard(drafts.get(key())?.value.expression);
    const time = $('schedule-wiz-time').value || (previous ? `${pad(previous.h)}:${pad(previous.m)}` : '09:00');
    const [h, m] = time.split(':').map(Number);
    let days = [];
    if (mode === 'weekly') {
      days = dowBoxes.filter((box) => box.checked).map((box) => Number(box.value));
      if (!days.length) {
        const today = new Date().getDay();
        dowBoxes.find((box) => Number(box.value) === today).checked = true;
        days = [today];
      }
    } else if (mode === 'monthly') {
      days = dayBoxes.filter((box) => box.checked).map((box) => Number(box.value));
      if (!days.length) {
        const today = new Date().getDate();
        dayBoxes.find((box) => Number(box.value) === today).checked = true;
        days = [today];
      }
    }
    days.sort((a, b) => a - b);
    $('schedule-kind').value = 'cron';
    $('schedule-expression').value =
      mode === 'weekly'
        ? `${m} ${h} * * ${days.join(',')}`
        : mode === 'monthly'
          ? `${m} ${h} ${days.join(',')} * *`
          : `${m} ${h} * * *`;
  }
  function describeSchedule(draft) {
    const value = draft.value;
    const zone = ` · 时区 ${value.timezone || '未设置'}`;
    if (value.kind === 'interval')
      return `固定间隔${value.interval_seconds ? ` 每 ${value.interval_seconds} 秒` : '（秒数在高级模式填写）'}${zone}`;
    if (value.kind === 'once')
      return `单次${value.start_at ? ` ${value.start_at}` : '（起始时间在高级模式填写）'}${zone}`;
    const parsed = parseWizard(value.expression);
    if (!parsed) return `自定义日历规则「${value.expression || '空'}」${zone}`;
    const clock = `${pad(parsed.h)}:${pad(parsed.m)}`;
    if (parsed.mode === 'daily') return `每天 ${clock}${zone}`;
    if (parsed.mode === 'weekly') return `每周 ${parsed.days.map((d) => DOW_NAMES[d]).join('、')} ${clock}${zone}`;
    return `每月 ${parsed.days.join('、')} 日 ${clock}${zone}`;
  }
  function syncWizard(draft) {
    const value = draft.value;
    const parsed = value.kind === 'cron' ? parseWizard(value.expression) : null;
    const custom = value.kind === 'cron' && !parsed;
    const active = document.activeElement;
    for (const radio of wizModes) {
      radio.disabled = value.kind !== 'cron';
      if (radio !== active) radio.checked = Boolean(parsed && radio.value === parsed.mode);
    }
    const time = $('schedule-wiz-time');
    time.disabled = value.kind !== 'cron';
    if (parsed && time !== active) time.value = `${pad(parsed.h)}:${pad(parsed.m)}`;
    $('schedule-wiz-weekdays').hidden = !(parsed && parsed.mode === 'weekly');
    $('schedule-wiz-days').hidden = !(parsed && parsed.mode === 'monthly');
    for (const box of dowBoxes) {
      box.disabled = value.kind !== 'cron';
      if (parsed?.mode === 'weekly' && box !== active) box.checked = parsed.days.includes(Number(box.value));
    }
    for (const box of dayBoxes) {
      box.disabled = value.kind !== 'cron';
      if (parsed?.mode === 'monthly' && box !== active) box.checked = parsed.days.includes(Number(box.value));
    }
    const note = $('schedule-wiz-note');
    note.hidden = Boolean(parsed);
    note.textContent =
      value.kind === 'interval'
        ? '当前为固定间隔模式，频率向导不适用；秒数与起始时间在下方高级模式中调整。'
        : value.kind === 'once'
          ? '当前为单次模式，频率向导不适用；起始时间在下方高级模式中填写。'
          : '当前日历规则不是「每天 / 每周 / 每月」形式，原文已在高级模式打开；点选上方频率可改回向导，并覆盖现有规则。';
    if (value.kind !== 'cron' || custom) $('schedule-advanced').open = true;
    $('schedule-wiz-summary-text').textContent = describeSchedule(draft);
  }
  const target = () => ({ agent: getAgent(), generation: getGeneration(), selected, epoch });
  const current = (t) =>
    t.agent === getAgent() && t.generation === getGeneration() && t.selected === selected && t.epoch === epoch;
  function put(k, value) {
    if (!drafts.has(k) && drafts.size >= 64) {
      for (const [id, draft] of drafts) if (!draft.dirty) drafts.delete(id);
      if (drafts.size >= 64) throw new Error('未保存计划草稿较多，请先保存或放弃部分草稿');
    }
    drafts.set(k, value);
    return value;
  }
  const fresh = () => ({
    name: '',
    enabled: false,
    revision: 0,
    value: {
      kind: 'cron',
      timezone: localZone,
      expression: '0 9 * * *',
      interval_seconds: null,
      start_at: null,
      session_id: null,
      prompt: '',
      misfire: 'latest',
    },
    dirty: false,
  });
  function reset() {
    epoch++;
    selected = '';
    choices.clear();
    $('schedule-fields').disabled = true;
    $('schedule-list').replaceChildren();
    $('schedule-ledger').replaceChildren();
    $('schedule-history').textContent = $('schedule-progress').textContent = $('schedule-status').textContent = '';
    $('schedule-review').hidden = true;
    $('schedule-wiz-summary-text').textContent = '';
    $('schedule-wiz-note').hidden = true;
    $('schedule-wiz-weekdays').hidden = $('schedule-wiz-days').hidden = true;
    blocker = cursor = occurrenceCursor = historyCursor = null;
  }
  function render(draft, row = null) {
    $('schedule-name').value = draft.name;
    $('schedule-enabled').checked = draft.enabled;
    for (const [id, field] of [
      ['kind', 'kind'],
      ['timezone', 'timezone'],
      ['expression', 'expression'],
      ['interval', 'interval_seconds'],
      ['start', 'start_at'],
      ['prompt', 'prompt'],
    ])
      $('schedule-' + id).value = draft.value[field] ?? '';
    $('schedule-fields').disabled = Boolean(row?.deleted_at);
    $('schedule-expression').disabled = draft.value.kind !== 'cron';
    $('schedule-interval').disabled = draft.value.kind !== 'interval';
    $('schedule-start').disabled = draft.value.kind === 'cron';
    for (const id of ['schedule-archive', 'schedule-occurrences', 'schedule-history-button'])
      $(id).disabled = !selected || (id === 'schedule-archive' && Boolean(row?.deleted_at));
    $('schedule-status').textContent =
      `${draft.dirty ? '草稿保留，基于' : '当前'}修订 ${draft.revision}${!selected ? '；尚未保存，默认停用' : ''}`;
    if (row) {
      const p = row.progress;
      const reasons = {
        restored_schedule_paused: '恢复保护：核对外部结果与预算后，重新保存计划才能恢复自动调度',
        previous_outcome_unknown: '上一轮存在未知结果，等待人工核查',
        timezone_data_changed: '时区数据版本变化，请核对并重新保存计划',
        queue_full: '执行队列已满，等待空位后重试',
      };
      $('schedule-progress').textContent =
        `时区数据 ${row.tzdb_version}\n下次 UTC：${p.next_at || '无待登记时间'}\n已登记 ${p.sequence} 次；核查序号 ${p.reviewed_sequence}（逐条核查，不覆盖后续新增未知结果）\n${reasons[p.error_code] || p.error_code || ''}${row.blocker ? '\n阻塞：' + row.blocker.reason + ' · 运行 ' + (row.blocker.run_id || '尚未入队') : ''}${row.blocker?.items ? '\n待核查身份：\n' + row.blocker.items.map((i) => `${i.kind} · ${i.id} · ${i.state}`).join('\n') : ''}`;
      blocker = row.blocker;
    }
    if (row?.deleted_at) $('schedule-status').textContent = '已归档：定义只读；仍可查看账本并取消在途回合';
    $('schedule-review').hidden = blocker?.reason !== 'unknown';
    syncWizard(draft);
  }
  function renderChoices() {
    $('schedule-list').replaceChildren(
      new Option('新计划草稿', ''),
      ...[...choices.values()].map(
        (r) =>
          new Option(`${r.name} · ${r.deleted_at ? '已归档' : r.enabled ? '启用' : '停用'} · 修订 ${r.revision}`, r.id),
      ),
    );
    $('schedule-list').value = selected;
    $('schedule-more').disabled = !cursor;
  }
  async function loadSelected() {
    epoch++;
    const t = target();
    $('schedule-fields').disabled = true;
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    $('schedule-ledger').replaceChildren();
    $('schedule-history').textContent = $('schedule-progress').textContent = '';
    occurrenceCursor = historyCursor = blocker = null;
    occurrenceCount = historyCount = 0;
    $('schedule-occurrences-more').disabled = $('schedule-history-more').disabled = true;
    const row = selected ? await api(`${root(t.agent)}/${selected}`) : null;
    if (!current(t)) return;
    if (row) {
      choices.set(row.id, row);
      renderChoices();
    }
    let draft = drafts.get(key());
    if (!draft || !draft.dirty) draft = put(key(), row ? { ...structuredClone(row), dirty: false } : fresh());
    render(draft, row);
  }
  async function load(more = false) {
    epoch++;
    const t = target();
    const page = await api(
      `${root(t.agent)}?limit=100&include_archived=${$('schedule-archived').checked}${more && cursor ? '&after=' + cursor : ''}`,
    );
    if (!current(t)) return;
    if (!more) choices.clear();
    for (const row of page.items) choices.set(row.id, row);
    cursor = page.next_after;
    selected = selections.get(t.agent) || '';
    renderChoices();
    await loadSelected();
  }
  function capture() {
    const draft = drafts.get(key());
    if (!draft) return;
    draft.name = $('schedule-name').value;
    draft.enabled = $('schedule-enabled').checked;
    const kind = $('schedule-kind').value;
    draft.value = {
      ...draft.value,
      kind,
      timezone: $('schedule-timezone').value,
      expression: kind === 'cron' ? $('schedule-expression').value : '',
      interval_seconds: kind === 'interval' ? Number($('schedule-interval').value) : null,
      start_at: kind !== 'cron' ? $('schedule-start').value || null : null,
      prompt: $('schedule-prompt').value,
    };
    draft.dirty = true;
    render(draft);
  }
  $('schedule-wizard').addEventListener('input', (event) => {
    const box = event.target;
    if (
      (box.classList.contains('wiz-dow') || box.classList.contains('wiz-day')) &&
      !box.closest('.wiz-pick').querySelector('input:checked')
    )
      box.checked = true;
    compileWizard();
  });
  $('schedule-form').addEventListener('input', capture);
  $('schedule-list').addEventListener(
    'change',
    wrap(async () => {
      selected = $('schedule-list').value;
      selections.set(getAgent(), selected);
      await loadSelected();
    }),
  );
  $('schedule-new').addEventListener(
    'click',
    wrap(async () => {
      selected = '';
      selections.set(getAgent(), '');
      renderChoices();
      await loadSelected();
    }),
  );
  $('schedule-archived').addEventListener(
    'change',
    wrap(() => {
      selections.delete(getAgent());
      selected = '';
      return load();
    }),
  );
  $('schedule-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('schedule-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('schedule-form').addEventListener(
    'submit',
    wrap(async () => {
      capture();
      const t = target(),
        k = key(),
        draft = structuredClone(drafts.get(k));
      if (
        draft.enabled &&
        !(await ask({
          title: '启用持久调度',
          message: `Agent ${t.agent}；计划 ${draft.name}。到期会自动调用模型并占用预算；工具写入仍逐次审批。`,
          confirmLabel: '保存并启用',
        }))
      )
        return;
      if (!current(t)) throw new Error('空间或计划已变化，请重新确认');
      $('schedule-fields').disabled = true;
      const operation = (async () => {
        const row = await api(root(t.agent) + (t.selected ? '/' + t.selected : ''), {
          method: t.selected ? 'PUT' : 'POST',
          body: { name: draft.name, enabled: draft.enabled, value: draft.value, revision: draft.revision },
        });
        drafts.delete(k);
        put(key(t.agent, row.id), { ...structuredClone(row), dirty: false });
        selections.set(t.agent, row.id);
        if (!current(t)) return;
        selected = row.id;
        choices.set(row.id, row);
        renderChoices();
        await loadSelectedAfterWrite(row);
      })();
      writes.add(operation);
      try {
        await operation;
      } finally {
        writes.delete(operation);
        if (current(t)) $('schedule-fields').disabled = false;
      }
    }),
  );
  async function loadSelectedAfterWrite(row) {
    epoch++;
    render(drafts.get(key()), row);
  }
  $('schedule-reload').addEventListener(
    'click',
    wrap(async () => {
      const t = target();
      if (!(await ask({ title: '放弃计划草稿', message: '重新载入服务端当前修订。', confirmLabel: '放弃并重载' })))
        return;
      if (!current(t)) throw new Error('空间或计划已变化');
      drafts.delete(key());
      await loadSelected();
    }),
  );
  $('schedule-archive').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        draft = drafts.get(key());
      if (
        !(await ask({
          title: '归档计划',
          message: `计划 ${draft.name}（${t.selected}），Agent ${t.agent}。停止后续登记，取消尚未入队的记录；已经入队的回合仍需单独取消。`,
          confirmLabel: '归档',
          danger: true,
        }))
      )
        return;
      if (!current(t)) throw new Error('空间或计划已变化');
      await api(`${root(t.agent)}/${t.selected}?revision=${draft.revision}`, { method: 'DELETE' });
      drafts.delete(key(t.agent, t.selected));
      selections.delete(t.agent);
      if (current(t)) {
        selected = '';
        await load();
      }
    }),
  );
  async function ledger(more = false) {
    const t = target();
    if (!selected) return;
    const data = await api(
      `${root(t.agent)}/${t.selected}/occurrences?limit=50&after=${more ? occurrenceCursor || 0 : 0}`,
    );
    if (!current(t)) return;
    if (!more) {
      $('schedule-ledger').replaceChildren();
      occurrenceCount = 0;
    }
    for (const row of data.items) {
      const article = document.createElement('article');
      article.textContent = `#${row.sequence} · ${row.local_time} · UTC ${row.scheduled_at} · 计划修订 ${row.definition_revision}\n${row.state} · ${row.run_state || ''} · ${row.error_code || row.run_error_code || ''}${row.coalesced_from ? '\n错过周期合并自 ' + row.coalesced_from : ''}\n运行 ${row.run_id || '尚未入队'}`;
      if (
        (!row.run_id && ['pending', 'blocked'].includes(row.state)) ||
        ['queued', 'running', 'waiting_approval', 'cancelling'].includes(row.run_state) ||
        row.delivery_pending
      ) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = '取消本次';
        button.addEventListener(
          'click',
          wrap(async () => {
            if (
              !(await ask({
                title: '取消本次到期',
                message: `Agent ${t.agent}；到期记录 ${row.id}。已发出的请求无法撤回，未知结果继续保留。`,
                confirmLabel: '取消本次',
                danger: true,
              }))
            )
              return;
            if (!current(t)) throw new Error('空间或计划已变化');
            await api(`${root(t.agent)}/${t.selected}/occurrences/${row.id}/cancel`, { method: 'POST' });
            if (current(t)) await ledger();
          }),
        );
        article.append(document.createElement('br'), button);
      }
      $('schedule-ledger').append(article);
    }
    occurrenceCursor = data.next_after;
    occurrenceCount += data.items.length;
    $('schedule-occurrences-more').disabled = !occurrenceCursor || occurrenceCount >= 500;
    const row = await api(`${root(t.agent)}/${t.selected}`);
    if (current(t)) render(drafts.get(key()), row);
  }
  $('schedule-occurrences').addEventListener(
    'click',
    wrap(() => ledger()),
  );
  $('schedule-occurrences-more').addEventListener(
    'click',
    wrap(() => ledger(true)),
  );
  async function history(more = false) {
    const t = target(),
      data = await api(`${root(t.agent)}/${t.selected}/history?limit=50&after=${more ? historyCursor || 0 : 0}`);
    if (!current(t)) return;
    const text = data.items
      .map(
        (r) =>
          `修订 ${r.revision} · ${r.created_at} · ${r.name} · ${r.deleted_at ? '已归档' : r.enabled ? '启用' : '停用'}\n${r.kind} ${r.expression || r.start_at} · ${r.timezone}\n${r.prompt}`,
      )
      .join('\n\n');
    $('schedule-history').textContent = more ? $('schedule-history').textContent + '\n\n' + text : text;
    historyCursor = data.next_after;
    historyCount = (more ? historyCount : 0) + data.items.length;
    $('schedule-history-more').disabled = !historyCursor || historyCount >= 500;
  }
  $('schedule-history-button').addEventListener(
    'click',
    wrap(() => history()),
  );
  $('schedule-history-more').addEventListener(
    'click',
    wrap(() => history(true)),
  );
  $('schedule-review').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        captured = structuredClone(blocker);
      if (
        !captured ||
        !(await ask({
          title: '允许后续调度',
          message: `请先核查运行 ${captured.run_id} 的未知费用、动作和投递。此操作仅允许未来到期回合继续；不会重试旧回合、释放费用或把未知结果改成成功。Agent ${t.agent}。`,
          confirmLabel: '已核查，允许后续调度',
          danger: true,
        }))
      )
        return;
      if (!current(t)) throw new Error('空间或计划已变化');
      await api(`${root(t.agent)}/${t.selected}/review`, {
        method: 'POST',
        body: { occurrence_id: captured.id, fingerprint: captured.fingerprint },
      });
      if (current(t)) await loadSelected();
    }),
  );
  return { load, reset };
}
