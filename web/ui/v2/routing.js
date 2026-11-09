// One routing editor per Agent; drafts stay bounded in memory, while actual choices come from run records.
export function mountRouting({ api, wrap, getAgent, getGeneration, getSession }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map();
  const modes = ['chat', 'retrieval', 'agent'];
  let epoch = 0,
    readVersion = 0;
  const bound = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;
  const root = (agent) => `/agents/${agent}/routing`;
  function read() {
    const draft = drafts.get(getAgent());
    if (!draft) return;
    draft.value.default_mode = $('route-default').value;
    draft.value.allow_escalation = $('route-escalation').checked;
    for (const mode of modes) {
      draft.value[`${mode}_model_id`] = $(`route-${mode}-model`).value || null;
      const value = $(`route-${mode}-reserve`).value;
      draft.value[`${mode}_reservation_micro`] = value === '' ? null : Number(value);
    }
    draft.dirty = true;
    $('route-config-status').textContent = '档位草稿尚未保存，已有运行继续使用原修订';
  }
  $('route-fields').addEventListener('input', read);
  $('route-fields').addEventListener('change', read);
  async function load() {
    const t = { ...bound(), read: ++readVersion };
    if (!t.agent) return;
    $('route-fields').disabled = true;
    const [row, configs] = await Promise.all([
      api(root(t.agent)),
      api(`/agents/${t.agent}/configurations?kind=model&limit=200`),
    ]);
    if (!current(t) || t.read !== readVersion) return;
    let draft = drafts.get(t.agent);
    if (!draft?.dirty) {
      if (!draft && drafts.size >= 64) for (const [key, value] of drafts) if (!value.dirty) drafts.delete(key);
      if (!draft && drafts.size >= 64) throw new Error('档位草稿过多，请先保存部分草稿');
      draft = {
        revision: row.revision,
        dirty: false,
        value: Object.fromEntries(
          [
            'default_mode',
            'allow_escalation',
            ...modes.flatMap((m) => [`${m}_model_id`, `${m}_reservation_micro`]),
          ].map((key) => [key, row[key]]),
        ),
      };
      drafts.set(t.agent, draft);
    }
    $('route-default').value = draft.value.default_mode;
    $('route-escalation').checked = draft.value.allow_escalation;
    for (const mode of modes) {
      const select = $(`route-${mode}-model`),
        identity = draft.value[`${mode}_model_id`];
      select.replaceChildren(
        new Option('跟随本空间默认模型', ''),
        ...configs.items.map((r) => {
          const option = new Option(`${r.name}${r.enabled ? '' : '（停用）'}`, r.id);
          option.disabled = !r.enabled;
          return option;
        }),
      );
      if (identity && !configs.items.some((r) => r.id === identity))
        select.append(new Option('原配置不可用，请重新选择', identity));
      select.value = identity || '';
      $(`route-${mode}-reserve`).value = draft.value[`${mode}_reservation_micro`] ?? '';
    }
    $('route-config-status').textContent =
      `${draft.dirty ? '草稿保留，基于' : '已载入'}修订 ${draft.revision}。普通修改仅作用于新运行，模型停用和权限撤销仍即时核验。`;
    $('route-fields').disabled = false;
  }
  $('route-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = bound(),
        draft = structuredClone(drafts.get(t.agent));
      if (!draft) return;
      $('route-fields').disabled = true;
      try {
        const saved = await api(root(t.agent), {
          method: 'PUT',
          body: { value: draft.value, revision: draft.revision },
        });
        drafts.set(t.agent, { ...draft, revision: saved.revision, dirty: false });
        if (current(t)) await load();
      } finally {
        if (current(t)) $('route-fields').disabled = false;
      }
    }),
  );
  $('route-reload').addEventListener(
    'click',
    wrap(async () => {
      drafts.delete(getAgent());
      epoch++;
      await load();
    }),
  );
  return {
    load,
    reset() {
      epoch++;
      $('route-fields').disabled = true;
      $('route-config-status').textContent = '';
      $('route-run-status').textContent = '';
      $('route-call-list').replaceChildren();
    },
    clear() {
      $('route-run-status').textContent = '';
      $('route-call-list').replaceChildren();
    },
    async report(run, session) {
      const t = bound(),
        value = await api(`/agents/${t.agent}/runs/${run}/routing`);
      if (!current(t) || getSession() !== session) return;
      if (!value.route.recorded) {
        $('route-run-status').textContent = '这次运行未登记档位来源。';
        $('route-call-list').replaceChildren();
        return;
      }
      const labels = { chat: '聊天', retrieval: '检索', agent: '完整' },
        route = value.route;
      $('route-run-status').textContent =
        `档位 ${labels[route.mode]}（请求 ${labels[route.requested_mode] || (route.requested_mode === 'default' ? '跟随设置' : '自动')}） · ${route.source} / ${route.reason}${route.promoted ? ' · 已升档一次' : ''}；本轮模型 ${route.model_name} / ${route.model}，修订 ${route.model_revision_id}，单次预留 ${route.reservation_micro} 微美元。\n` +
        `已登记 ${value.calls.length} 份调用工具快照；工具规格字节差 ${value.byte_delta}（正值表示减少，不是费用收益）。已知结算 ${value.known_cost_micro} 微美元，${value.unsettled_calls} 次待结清；路由模型调用 ${value.routing_model_calls} 次。`;
      if (value.unrecorded_turn_calls)
        $('route-run-status').textContent +=
          ` ${value.unrecorded_turn_calls} 条主调用记录无工具快照（历史记录或预检未通过）。`;
      $('route-call-list').replaceChildren(
        ...value.calls.map((call) => {
          const node = document.createElement('article');
          node.textContent = `调用 ${call.ordinal} · ${labels[call.mode]} · ${call.call_state} · 工具 ${call.tool_count}/${call.full_tool_count} · 规格 ${call.tool_bytes}/${call.full_tool_bytes} 字节\n输入估算 ${call.input_token_estimate} token（${call.counting_method}）；费用 ${call.cost_micro === null ? '未知' : `${call.cost_micro} 微美元`}\n${call.tools.map((tool) => tool.name).join('、') || '无工具'}`;
          return node;
        }),
      );
    },
  };
}
