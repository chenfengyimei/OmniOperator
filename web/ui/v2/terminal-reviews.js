import { ask } from '../dialog.js?v=20261103v7';

const policy = 'terminal-review-1';
const decisions = { applied: '已观察到影响', not_applied: '未观察到影响', still_unknown: '核查后仍不确定' };
const reasons = {
  terminal_receipt_not_unknown: '原始回执不是未知结果',
  terminal_operation_not_ended: '操作尚未结算',
  terminal_review_capacity: '本操作的核查记录已达上限',
  terminal_native_work_active: '当前实例还有实际终端工作，或正在排空',
};

export function mountTerminalReviews({ api, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(`terminal-review-${id}`),
    rows = new Map(),
    drafts = new Map(),
    pending = new Map();
  let active = false,
    owner = '',
    selected = '',
    view = 0,
    sequence = 0,
    after = '',
    historyAfter = 0,
    detail = null,
    busy = false;
  const key = (agent = getAgent(), operation = selected) => `${agent}/${operation}`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), view, selected });
  const current = (t) =>
    active && t.agent === getAgent() && t.generation === getGeneration() && t.view === view && t.selected === selected;
  const root = (agent = getAgent()) => `/agents/${agent}/terminal-reviews`;
  const note = () => drafts.get(key()) || { decision: '', evidence: '', acknowledge: false };

  function controls() {
    const waiting = pending.has(key()),
      value = note();
    $('load').disabled = $('select').disabled = busy;
    $('more').disabled = busy || !after || rows.size >= 200;
    $('refresh').disabled = busy || !selected;
    $('fields').disabled = !active || busy || waiting || !detail?.reviewable;
    $('submit').disabled =
      busy ||
      waiting ||
      !detail?.reviewable ||
      !decisions[value.decision] ||
      !value.evidence.trim() ||
      !value.acknowledge;
    $('retry').hidden = $('abandon').hidden = !waiting;
    $('retry').disabled = $('abandon').disabled = busy;
    $('history-more').disabled = busy || !historyAfter;
  }
  function restoreDraft() {
    const value = note();
    $('decision').value = value.decision;
    $('evidence').value = value.evidence;
    $('ack').checked = value.acknowledge;
    controls();
  }
  function choices() {
    $('select').replaceChildren(
      new Option('选择未知操作', ''),
      ...[...rows.values()].map(
        (row) =>
          new Option(
            `${row.operation.kind} · ${row.operation.id} · ${row.latest_matches_source ? decisions[row.latest_review.decision] : '待核查'}`,
            row.operation.id,
          ),
      ),
    );
    $('select').value = selected;
  }
  function remember(row) {
    rows.set(row.operation.id, {
      operation: { id: row.operation.id, kind: row.operation.kind },
      latest_matches_source: row.latest_matches_source,
      latest_review: row.latest_review ? { decision: row.latest_review.decision } : null,
    });
  }
  async function run(action) {
    if (busy || !active) return;
    const t = target();
    busy = true;
    $('status').textContent = '正在读取或登记核查…';
    controls();
    try {
      await action(t);
    } catch (error) {
      if (current(t)) $('status').textContent = error.message;
    } finally {
      if (current(t)) {
        busy = false;
        controls();
      }
    }
  }
  function clearView() {
    sequence++;
    detail = null;
    historyAfter = 0;
    $('detail').textContent = '';
    $('history').replaceChildren();
    restoreDraft();
  }
  async function loadList(t, more = false) {
    const ticket = ++sequence;
    const result = await api(`${root(t.agent)}?after=${encodeURIComponent(more ? after : '')}&limit=20`);
    if (!current(t) || ticket !== sequence) return;
    const retained = rows.get(selected);
    if (!more) rows.clear();
    for (const row of result.items) {
      if (rows.size >= 200) break;
      // Lists retain bounded identity/status only; evidence is fetched explicitly below.
      remember(row);
    }
    if (retained && !rows.has(selected) && rows.size < 200) rows.set(selected, retained);
    after = result.next_after || '';
    choices();
    $('status').textContent = rows.size
      ? `已显示 ${rows.size} 条未知操作。请先独立核对，再登记观察。`
      : '暂无未知终端操作。';
  }
  async function history(t, more = false) {
    const result = await api(`${root(t.agent)}/${t.selected}/history?after=${more ? historyAfter : 0}&limit=20`);
    if (!current(t)) return;
    if (!more) $('history').replaceChildren();
    for (const row of result.items) {
      const card = document.createElement('article'),
        text = document.createElement('pre'),
        erase = document.createElement('button');
      text.textContent = `核查 ${row.revision} · ${decisions[row.decision]}\n${row.created_at}\n${row.evidence ?? '核查说明已清除，保留观察元数据和指纹。'}`;
      erase.type = 'button';
      erase.textContent = '清除这条核查说明';
      erase.disabled = row.evidence === null;
      erase.addEventListener('click', () => {
        if (!current(t)) return;
        return run(async (captured) => {
          if (!(await ask('清除这条核查说明？原始回执与核查结论仍保留。')) || !current(captured) || !current(t)) return;
          await api(`${root(t.agent)}/${t.selected}/history/${row.id}/evidence`, { method: 'DELETE', body: {} });
          const identity = key(t.agent, t.selected);
          if (pending.get(identity)?.review_id === row.id) {
            pending.delete(identity);
            drafts.delete(identity);
          }
          if (current(captured)) await refresh(captured);
        });
      });
      card.append(text, erase);
      $('history').append(card);
    }
    historyAfter = result.next_after || 0;
  }
  async function refresh(t) {
    if (!t.selected) return;
    const ticket = ++sequence;
    const result = await api(`${root(t.agent)}/${t.selected}`);
    if (!current(t) || ticket !== sequence) return;
    detail = result;
    remember(result);
    choices();
    $('detail').textContent =
      `操作 ${result.operation.id}\n类型 ${result.operation.kind} · 原始回执 ${result.operation.state}\n登记于 ${result.operation.created_at} · ${result.operation.bytes} 字节\n意图指纹 ${result.operation.payload_sha256}\n终端 ${result.session.state} · ${result.process_reclamation_verified ? '记录中已确认回收' : '历史进程回收未证实'}\n核查修订 ${result.revision} · ${result.latest_matches_source ? '已有匹配当前来源的观察' : '尚无匹配当前来源的观察'}\n${result.native_quiescent ? '本次查询未发现当前实例中的实际终端工作' : '请先结束当前实例中的所有实际终端并完成回收，再登记核查。'}\n核查只记录独立观察，不证明命令成功、不重发旧输入，也不改变历史进程状态。`;
    await history(t);
    if (current(t)) {
      restoreDraft();
      $('status').textContent = result.reviewable
        ? '可登记独立核查观察。'
        : `当前不可登记：${result.reasons.map((reason) => reasons[reason] || '请刷新来源后重试').join('、')}`;
    }
  }
  async function submit(t, retry = false) {
    const identity = key(t.agent, t.selected);
    let request = pending.get(identity);
    if (!retry) {
      const value = note();
      if (!detail?.reviewable || !value.acknowledge || !value.evidence.trim() || !decisions[value.decision]) return;
      if (pending.size >= 32) throw new Error('待确认提交已达上限，请先查询或处理已有提交。');
      request = {
        review_id: crypto.randomUUID(),
        revision: detail.revision,
        history_epoch: detail.history_epoch,
        fingerprint: detail.fingerprint,
        policy,
        decision: value.decision,
        evidence: value.evidence,
        acknowledge_limits: true,
      };
      pending.set(identity, request);
      controls();
    }
    if (!request) return;
    await api(`${root(t.agent)}/${t.selected}`, { method: 'POST', body: request });
    pending.delete(identity);
    drafts.delete(identity);
    if (current(t)) await refresh(t);
  }
  $('load').addEventListener('click', () => run((t) => loadList(t)));
  $('more').addEventListener('click', () => run((t) => loadList(t, true)));
  $('select').addEventListener('change', () => {
    selected = $('select').value;
    clearView();
    run(refresh);
  });
  $('refresh').addEventListener('click', () => run(refresh));
  $('history-more').addEventListener('click', () => run((t) => history(t, true)));
  $('submit').addEventListener('click', () => run((t) => submit(t)));
  $('retry').addEventListener('click', () => run((t) => submit(t, true)));
  $('abandon').addEventListener('click', () =>
    run(async (t) => {
      if (!(await ask('放弃本地待确认请求？这不会撤销服务端可能已登记的核查，请先刷新历史记录。')) || !current(t))
        return;
      pending.delete(key());
      await refresh(t);
    }),
  );
  for (const field of ['decision', 'evidence', 'ack'])
    $(field).addEventListener('input', () => {
      if (!active || !selected || pending.has(key())) return;
      if (!drafts.has(key()) && drafts.size >= 32) {
        $('status').textContent = '草稿已达上限，请先完成或清除已有草稿。';
        restoreDraft();
        return;
      }
      drafts.set(key(), {
        decision: $('decision').value,
        evidence: $('evidence').value,
        acknowledge: $('ack').checked,
      });
      controls();
    });
  return {
    activate() {
      active = true;
      if (owner !== getAgent()) {
        owner = getAgent();
        rows.clear();
        selected = '';
        after = '';
        choices();
        clearView();
      }
      controls();
    },
    deactivate() {
      active = false;
      view++;
      busy = false;
      rows.clear();
      selected = '';
      after = '';
      choices();
      clearView();
      $('status').textContent = '';
    },
  };
}
