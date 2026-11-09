// Publication requests prepare the shared outbox; this panel never bypasses delivery approval.
export function mountBilibili({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map(),
    writes = new Set(),
    uiDrafts = new Map();
  const uiFields = [
    'bili-config',
    'bili-read-operation',
    'bili-read-target',
    'bili-message',
    'bili-kind',
    'bili-target',
    'bili-mentions',
  ];
  let epoch = 0,
    selected = '',
    cursor = null,
    publications = new Map(),
    readSequence = 0,
    displayedAgent = '';
  function saveUI() {
    if (!getAgent()) return;
    if (!uiDrafts.has(getAgent()) && uiDrafts.size >= 32) throw new Error('B站空间草稿较多，请先处理已有草稿');
    uiDrafts.set(getAgent(), { values: Object.fromEntries(uiFields.map((id) => [id, $(id).value])), selected });
  }
  for (const id of uiFields) $(id).addEventListener('input', saveUI);
  const path = (agent = getAgent()) => `/agents/${agent}/bilibili`;
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;
  const stamp = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  function restoreUI(agent) {
    if (displayedAgent === agent) return;
    displayedAgent = agent;
    const saved = uiDrafts.get(agent);
    if (saved) {
      for (const [id, value] of Object.entries(saved.values)) $(id).value = value;
      selected = saved.selected;
    }
  }
  const key = (agent = getAgent(), config = selected) => `${agent}/${config}`;
  function store(k, draft) {
    if (!drafts.has(k) && drafts.size >= 64) {
      for (const [name, value] of drafts) if (!value.dirty) drafts.delete(name);
      if (drafts.size >= 64) throw new Error('B站规则草稿较多，请先保存已有草稿');
    }
    drafts.set(k, draft);
    return draft;
  }
  function render(draft) {
    $('bili-forms').value = draft.forms;
    $('bili-enabled').checked = draft.enabled;
    $('bili-headless').checked = draft.headless;
    $('bili-profile-status').textContent = `${draft.dirty ? '草稿未保存' : '已载入'} · 修订 ${draft.revision}`;
    $('bili-profile-fields').disabled = !selected;
  }
  function edit() {
    const draft = drafts.get(key());
    if (!draft) return;
    Object.assign(draft, {
      forms: $('bili-forms').value,
      enabled: $('bili-enabled').checked,
      headless: $('bili-headless').checked,
      dirty: true,
    });
    $('bili-profile-status').textContent = `草稿未保存 · 修订 ${draft.revision}`;
  }
  $('bili-forms').addEventListener('input', edit);
  $('bili-enabled').addEventListener('change', edit);
  $('bili-headless').addEventListener('change', edit);
  $('bili-form-template').addEventListener(
    'click',
    wrap(() => {
      if (!['', '{}'].includes($('bili-forms').value.trim()))
        throw new Error('当前规则草稿已有内容，请先保存；需要替换时可手动清空编辑框');
      $('bili-forms').value = JSON.stringify(
        {
          comment: {
            account: '',
            account_attr: 'data-mid',
            target: '',
            target_attr: 'data-id',
            editor: '',
            submit: '',
            records: '',
            record_id_attr: 'data-id',
            record_author: '',
            record_author_attr: 'data-mid',
            record_text: '',
            challenge: '',
            logged_out: '',
            mention_input: '',
            mention_options: '',
            mention_nodes: '',
            mention_uid_attr: 'data-mid',
            record_mentions: '',
          },
        },
        null,
        2,
      );
      edit();
    }),
  );
  $('bili-load-profile').addEventListener(
    'click',
    wrap(async () => {
      selected = $('bili-config').value.trim();
      epoch++;
      const t = stamp(),
        k = key();
      await Promise.allSettled([...writes]);
      const data = await api(`${path(t.agent)}/profiles/${selected}`);
      if (!current(t)) return;
      render(
        drafts.get(k)?.dirty
          ? drafts.get(k)
          : store(k, {
              forms: JSON.stringify(data.forms, null, 2),
              enabled: data.enabled,
              headless: data.headless,
              revision: data.revision,
              dirty: false,
            }),
      );
      saveUI();
    }),
  );
  $('bili-profile-form').addEventListener(
    'submit',
    wrap(async () => {
      if (writes.size) throw new Error('B站规则正在保存，请稍候');
      if (selected !== $('bili-config').value.trim()) throw new Error('请先载入当前通道的页面规则');
      edit();
      const t = stamp(),
        k = key(),
        config = selected,
        draft = structuredClone(drafts.get(k));
      const pending = api(`${path(t.agent)}/profiles/${config}`, {
        method: 'PUT',
        body: {
          forms: JSON.parse(draft.forms),
          enabled: draft.enabled,
          headless: draft.headless,
          revision: draft.revision,
        },
      });
      writes.add(pending);
      try {
        const saved = await pending;
        const latest = drafts.get(k);
        if (JSON.stringify(latest) === JSON.stringify(draft))
          store(k, {
            forms: JSON.stringify(saved.forms, null, 2),
            enabled: saved.enabled,
            headless: saved.headless,
            revision: saved.revision,
            dirty: false,
          });
        else if (latest) latest.revision = saved.revision;
        if (current(t)) render(drafts.get(k));
      } finally {
        writes.delete(pending);
      }
    }),
  );
  $('bili-read-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = stamp(),
        sequence = ++readSequence;
      $('bili-read-status').textContent = '正在读取并核验登录账号…';
      try {
        const data = await api(`${path(t.agent)}/read`, {
          method: 'POST',
          body: {
            config_id: $('bili-config').value.trim(),
            operation: $('bili-read-operation').value,
            target: $('bili-read-target').value.trim(),
            limit: 10,
          },
        });
        if (current(t) && sequence === readSequence) {
          $('bili-read-result').textContent = JSON.stringify(data, null, 2);
          $('bili-read-status').textContent = '读取完成';
        }
      } catch (error) {
        if (current(t) && sequence === readSequence) $('bili-read-status').textContent = error.message;
        throw error;
      }
    }),
  );
  let request = null;
  $('bili-prepare-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = stamp();
      const body = {
        config_id: $('bili-config').value.trim(),
        message_id: $('bili-message').value.trim(),
        kind: $('bili-kind').value,
        target: $('bili-target').value.trim(),
        mentions: JSON.parse($('bili-mentions').value || '[]'),
      };
      const fingerprint = JSON.stringify(body);
      if (!request || request.fingerprint !== fingerprint || request.agent !== t.agent)
        request = { fingerprint, agent: t.agent, key: crypto.randomUUID() };
      $('bili-prepare').disabled = true;
      try {
        const data = await api(`${path(t.agent)}/publications`, {
          method: 'POST',
          body: { ...body, key: request.key },
        });
        if (!current(t)) return;
        $('bili-preview').textContent = JSON.stringify(data, null, 2);
        $('bili-publication-status').textContent =
          '发布意图已保存。请到「持久投递」核对账号、目标、完整正文和 mention 后逐条审批。';
        await load();
      } finally {
        $('bili-prepare').disabled = false;
      }
    }),
  );
  async function load(more = false) {
    const t = stamp();
    if (!t.agent) return;
    restoreUI(t.agent);
    const data = await api(`${path(t.agent)}/publications?limit=50${more && cursor ? `&after=${cursor}` : ''}`);
    if (!current(t)) return;
    if (!more) publications.clear();
    for (const row of data.items) publications.set(row.id, row);
    cursor = data.next_after;
    $('bili-more').disabled = !cursor || publications.size >= 200;
    $('bili-publication-list').replaceChildren(
      ...[...publications.values()].map(
        (p) => new Option(`${p.kind} · ${p.latest_delivery?.state || '尚无投递'} · ${p.id}`, p.id),
      ),
    );
    if (drafts.has(key())) render(drafts.get(key()));
  }
  $('bili-publication-list').addEventListener(
    'change',
    wrap(async () => {
      const t = stamp(),
        identity = $('bili-publication-list').value;
      if (!identity) return;
      const data = await api(`${path(t.agent)}/publications/${identity}`);
      if (current(t) && $('bili-publication-list').value === identity)
        $('bili-preview').textContent = JSON.stringify(data, null, 2);
    }),
  );
  $('bili-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('bili-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  return {
    load,
    useMessage(id) {
      restoreUI(getAgent());
      $('bili-message').value = id;
      saveUI();
    },
    reset() {
      if (displayedAgent === getAgent()) saveUI();
      displayedAgent = '';
      epoch++;
      readSequence++;
      selected = '';
      cursor = null;
      publications.clear();
      request = null;
      $('bili-profile-fields').disabled = true;
      for (const id of [
        'bili-config',
        'bili-message',
        'bili-target',
        'bili-forms',
        'bili-preview',
        'bili-read-result',
        'bili-read-status',
        'bili-publication-status',
        'bili-profile-status',
      ]) {
        const el = $(id);
        if ('value' in el) el.value = '';
        else el.textContent = '';
      }
      $('bili-mentions').value = '[]';
      $('bili-publication-list').replaceChildren();
    },
  };
}
