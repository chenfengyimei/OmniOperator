// Person-scoped drafts remain in memory; authentication and transport are injected.
export function mountPeople({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const drafts = new Map();
  const writes = new Set();
  let selected = null,
    person = null,
    epoch = 0,
    cursor = null;
  const choices = new Map();
  const key = (kind, owner = getAgent(), identity = selected) => `${owner}/${identity}/${kind}`;
  const path = (suffix, target = getAgent()) => `/agents/${target}/people${suffix}`;
  const target = () => ({ agent: getAgent(), person: selected, epoch, generation: getGeneration() });
  const current = (t) =>
    t.agent === getAgent() && t.person === selected && t.epoch === epoch && t.generation === getGeneration();
  function store(k, value) {
    if (!drafts.has(k) && drafts.size >= 64) {
      for (const [name, draft] of drafts) if (!draft.dirty) drafts.delete(name);
      if (drafts.size >= 64) throw new Error('未保存草稿较多，请先保存或放弃部分草稿');
    }
    drafts.set(k, value);
    return value;
  }
  function documentDraft(kind, loaded) {
    const cached = drafts.get(key(kind));
    if (cached?.dirty) return cached;
    return store(key(kind), { ...loaded, dirty: false });
  }
  function reset() {
    epoch++;
    selected = person = cursor = null;
    choices.clear();
    $('person-fields').disabled = true;
    $('person-select').replaceChildren();
    for (const kind of ['profile', 'resident']) {
      $(`${kind}-content`).value = '';
      $(`${kind}-history`).textContent = '';
    }
  }
  function renderChoices() {
    $('person-select').replaceChildren(
      ...[...choices.values()].map((row) => new Option(`${row.owner_id ? '所有者 · ' : ''}${row.name}`, row.id)),
    );
    if (selected && choices.has(selected)) $('person-select').value = selected;
    $('more-people').disabled = !cursor || choices.size >= 500;
  }
  async function loadSelected() {
    epoch++;
    const t = target();
    if (!t.person) return;
    $('person-fields').disabled = true;
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    const [loaded, accounts, profile, resident] = await Promise.all([
      api(path(`/${t.person}`, t.agent)),
      api(path(`/${t.person}/accounts`, t.agent)),
      api(path(`/${t.person}/documents/profile`, t.agent)),
      api(path(`/${t.person}/documents/resident`, t.agent)),
    ]);
    if (!current(t)) return;
    person = loaded;
    choices.set(loaded.id, loaded);
    renderChoices();
    let name = drafts.get(key('name'));
    if (!name?.dirty)
      name = store(key('name'), { content: loaded.name, base: loaded.name, revision: loaded.revision, dirty: false });
    else if (name.base === loaded.name) name.revision = loaded.revision;
    $('person-name').value = name.content;
    $('person-role').textContent = loaded.owner_id ? '所有者人物（受保护）' : '普通人物';
    for (const value of [profile, resident]) {
      const draft = documentDraft(value.kind, value);
      $(`${value.kind}-content`).value = draft.content;
      $(`${value.kind}-status`).textContent = draft.dirty ? '草稿已保留，尚未保存' : `已载入修订 ${value.revision}`;
      $(`${value.kind}-history`).textContent = '';
      $(`${value.kind}-history-button`).disabled = !value.entry_id;
    }
    const account = drafts.get(key('account')) || {};
    $('account-channel').value = account.channel || '';
    $('account-external').value = account.external_id || '';
    $('accounts-list').replaceChildren(
      ...accounts.items.map((row) => {
        const card = document.createElement('article');
        card.textContent = `${row.channel} · ${row.external_id} · ${row.verification === 'manual' ? '人工记录' : '通道已核验'}\n`;
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = '解除关联';
        button.addEventListener(
          'click',
          wrap(() =>
            mutate(async () => {
              await api(path(`/${t.person}/accounts/${row.id}?revision=${row.revision}`, t.agent), {
                method: 'DELETE',
              });
            }),
          ),
        );
        card.append(button);
        return card;
      }),
    );
    $('erase-person-consent').checked = false;
    $('erase-person-consent').disabled = !!loaded.owner_id;
    $('erase-person').disabled = true;
    $('person-fields').disabled = false;
    $('people-status').textContent = `已载入 ${loaded.name}；列表最多显示 500 个人物。`;
  }
  async function load(resetList = true) {
    const tAgent = getAgent(),
      tGeneration = getGeneration(),
      stamp = ++epoch;
    if (!tAgent) throw new Error('请先连接实例');
    $('person-fields').disabled = true;
    await Promise.allSettled([...writes]);
    if (stamp !== epoch || tAgent !== getAgent() || tGeneration !== getGeneration()) return;
    const [owner, page, files] = await Promise.all([
      api(path('/owner', tAgent)),
      api(path(`?limit=100${!resetList && cursor ? `&after=${cursor}` : ''}`, tAgent)),
      api(`/agents/${tAgent}/objects?limit=100`),
    ]);
    if (stamp !== epoch || tAgent !== getAgent() || tGeneration !== getGeneration()) return;
    if (resetList) choices.clear();
    choices.set(owner.id, owner);
    for (const row of page.items) if (choices.size < 500 || choices.has(row.id)) choices.set(row.id, row);
    cursor = page.next_after;
    if (!selected || !choices.has(selected)) selected = owner.id;
    renderChoices();
    const fileSelection = $('document-object').value;
    $('document-object').replaceChildren(
      new Option('选择已保存的 UTF-8 文件', ''),
      ...files.items.map((row) => new Option(`${row.sha256.slice(0, 16)}… · ${row.bytes} 字节`, row.id)),
    );
    if (files.items.some((row) => row.id === fileSelection)) $('document-object').value = fileSelection;
    await loadSelected();
  }
  async function mutate(work) {
    const t = target();
    if (!person || !t.person) throw new Error('请先选择人物');
    $('person-fields').disabled = true;
    const pending = work(t);
    writes.add(pending);
    try {
      await pending;
      writes.delete(pending);
      if (current(t)) await loadSelected();
    } catch (e) {
      if (current(t)) throw e;
    } finally {
      writes.delete(pending);
      if (current(t)) $('person-fields').disabled = false;
    }
  }
  $('person-select').addEventListener(
    'change',
    wrap(async () => {
      selected = $('person-select').value;
      await loadSelected();
    }),
  );
  $('refresh-people').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('more-people').addEventListener(
    'click',
    wrap(() => load(false)),
  );
  $('person-create').addEventListener(
    'submit',
    wrap(async () => {
      const a = getAgent(),
        generation = getGeneration();
      const row = await api(path('', a), { method: 'POST', body: { name: $('new-person-name').value } });
      if (a !== getAgent() || generation !== getGeneration()) return;
      $('new-person-name').value = '';
      choices.set(row.id, row);
      selected = row.id;
      renderChoices();
      await loadSelected();
    }),
  );
  $('person-name').addEventListener('input', () => {
    const draft = drafts.get(key('name'));
    if (draft) {
      draft.content = $('person-name').value;
      draft.dirty = true;
    }
  });
  $('person-rename').addEventListener(
    'submit',
    wrap(() =>
      mutate(async (t) => {
        const k = key('name', t.agent, t.person),
          draft = drafts.get(k);
        const saved = await api(path(`/${t.person}`, t.agent), {
          method: 'PUT',
          body: { name: draft.content, revision: draft.revision },
        });
        store(k, { content: saved.name, base: saved.name, revision: saved.revision, dirty: false });
      }),
    ),
  );
  for (const kind of ['profile', 'resident']) {
    $(`${kind}-content`).addEventListener('input', () => {
      const draft = drafts.get(key(kind));
      if (draft) {
        draft.content = $(`${kind}-content`).value;
        draft.dirty = true;
        $(`${kind}-status`).textContent = '草稿尚未保存';
      }
    });
    $(`${kind}-form`).addEventListener(
      'submit',
      wrap(() =>
        mutate(async (t) => {
          const k = key(kind, t.agent, t.person),
            draft = drafts.get(k);
          const saved = await api(path(`/${t.person}/documents/${kind}`, t.agent), {
            method: 'PUT',
            body: { content: draft.content, entry_id: draft.entry_id, revision: draft.revision },
          });
          store(k, { ...saved, dirty: false });
        }),
      ),
    );
    $(`${kind}-reload`).addEventListener(
      'click',
      wrap(async () => {
        drafts.delete(key(kind));
        await loadSelected();
      }),
    );
    $(`${kind}-export`).addEventListener(
      'click',
      wrap(async () => {
        const t = target();
        const saved = await api(path(`/${t.person}/documents/${kind}`, t.agent));
        if (!current(t)) return;
        const url = URL.createObjectURL(new Blob([saved.content], { type: 'text/markdown;charset=utf-8' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = `${kind}-${t.person}.md`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }),
    );
    $(`${kind}-history-button`).addEventListener(
      'click',
      wrap(async () => {
        const t = target(),
          draft = drafts.get(key(kind));
        if (!draft?.entry_id) return;
        const history = await api(`/agents/${t.agent}/memory/${draft.entry_id}/revisions?limit=20`);
        if (!current(t)) return;
        $(`${kind}-history`).textContent = history.items
          .map(
            (row) =>
              `修订 ${row.revision} · ${row.erased ? '已遗忘' : row.sources[0]?.object_id ? '来源文件' : row.sources[0]?.message_id ? '来源消息' : '人工保存'}\n${row.content}`,
          )
          .join('\n\n');
      }),
    );
  }
  $('document-import').addEventListener(
    'submit',
    wrap(() =>
      mutate(async (t) => {
        const kind = $('document-kind').value,
          k = key(kind, t.agent, t.person),
          draft = drafts.get(k);
        if (draft.dirty) throw new Error('当前有未保存草稿，请先保存或放弃后导入');
        const saved = await api(path(`/${t.person}/documents/${kind}/import`, t.agent), {
          method: 'POST',
          body: { object_id: $('document-object').value, entry_id: draft.entry_id, revision: draft.revision },
        });
        store(k, { ...saved, dirty: false });
      }),
    ),
  );
  for (const id of ['account-channel', 'account-external'])
    $(id).addEventListener('input', () =>
      store(key('account'), {
        channel: $('account-channel').value,
        external_id: $('account-external').value,
        dirty: true,
      }),
    );
  $('account-bind').addEventListener(
    'submit',
    wrap(() =>
      mutate(async (t) => {
        await api(path(`/${t.person}/accounts`, t.agent), {
          method: 'POST',
          body: { channel: $('account-channel').value, external_id: $('account-external').value },
        });
        drafts.delete(key('account', t.agent, t.person));
      }),
    ),
  );
  $('erase-person-consent').addEventListener('change', () => {
    $('erase-person').disabled = !!person?.owner_id || !$('erase-person-consent').checked;
  });
  $('erase-person').addEventListener(
    'click',
    wrap(async () => {
      const t = target();
      if (person?.owner_id || !$('erase-person-consent').checked) throw new Error('请先确认遗忘此人物及其关联记录');
      await mutate(async () => {
        await api(path(`/${t.person}?revision=${person.revision}`, t.agent), { method: 'DELETE' });
        for (const k of drafts.keys()) if (k.startsWith(`${t.agent}/${t.person}/`)) drafts.delete(k);
        if (current(t)) {
          selected = null;
          choices.delete(t.person);
        }
      });
      if (t.agent === getAgent() && t.generation === getGeneration()) await load();
    }),
  );
  return { load, reset };
}
