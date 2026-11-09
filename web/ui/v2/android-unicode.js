// Profile drafts hold credential references only. Recovery uses the shared owner approval ledger.
export function mountAndroidUnicode({ api, wrap, path, stamp, current, getGrant, getSession, onSaved, onRun }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map();
  let active = null,
    sequence = 0,
    cursor = null,
    environmentSequence = 0;
  const fields = ['reference', 'apk', 'certificate'];
  const key = (agent, grant) => `${agent}/${grant}`;
  function saveDraft() {
    if (!active) return;
    if (!drafts.has(active.key) && drafts.size >= 32) throw new Error('辅助组件草稿较多，请先处理已有草稿');
    drafts.set(active.key, {
      revision: active.revision,
      profile: {
        enabled: $('android-unicode-enabled').checked,
        credential_ref: $('android-unicode-reference').value,
        apk_sha256: $('android-unicode-apk').value,
        certificate_sha256: $('android-unicode-certificate').value,
      },
    });
  }
  for (const name of fields) $(`android-unicode-${name}`).addEventListener('input', saveDraft);
  $('android-unicode-enabled').addEventListener('change', saveDraft);
  function deactivate() {
    sequence++;
    environmentSequence++;
    active = null;
    $('android-unicode-fields').disabled = true;
    $('android-unicode-enabled').checked = false;
    fields.forEach((name) => {
      $(`android-unicode-${name}`).value = '';
    });
    $('android-unicode-status').textContent = '';
    $('android-environments').replaceChildren();
    cursor = null;
    $('android-environments-more').disabled = true;
  }
  async function load(discard = false) {
    const t = stamp(),
      grant = getGrant(),
      loading = ++sequence;
    if (!grant) {
      deactivate();
      return;
    }
    const draftKey = key(t.agent, grant);
    if (discard) drafts.delete(draftKey);
    const row = await api(path(t.agent) + `/grants/${grant}/unicode`);
    if (!current(t) || loading !== sequence || grant !== getGrant()) return;
    const draft = drafts.get(draftKey),
      profile = draft?.profile || row;
    active = { key: draftKey, revision: draft?.revision ?? row.revision ?? 0, agent: t.agent, grant };
    $('android-unicode-enabled').checked = profile.enabled === true;
    for (const [name, property] of [
      ['reference', 'credential_ref'],
      ['apk', 'apk_sha256'],
      ['certificate', 'certificate_sha256'],
    ])
      $(`android-unicode-${name}`).value = profile[property] || '';
    $('android-unicode-fields').disabled = false;
    $('android-unicode-status').textContent =
      `配置修订 ${active.revision} · ${draft ? '保留当前草稿' : '已载入'}；保存配置不代表辅助组件已可用。`;
  }
  $('android-unicode-reload').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('android-unicode-form').addEventListener(
    'submit',
    wrap(async (event) => {
      event.preventDefault();
      if (!active) throw new Error('先选择设备授权');
      saveDraft();
      const t = stamp(),
        selected = { ...active },
        draft = drafts.get(active.key);
      const row = await api(path(t.agent) + `/grants/${selected.grant}/unicode`, { method: 'PUT', body: draft });
      if (!current(t) || selected.grant !== getGrant()) return;
      // Preserve edits made while the save was in flight, including their original base revision.
      if (drafts.get(selected.key) === draft) drafts.delete(selected.key);
      await load();
      onSaved();
      $('android-unicode-status').textContent =
        `已保存修订 ${row.revision}；请重新观察。设备允许和实际编辑器将在输入前核验。`;
    }),
  );
  async function environments(more = false) {
    const t = stamp(),
      loading = ++environmentSequence;
    const result = await api(
      path(t.agent) + '/environments?limit=50' + (more && cursor ? '&after=' + encodeURIComponent(cursor) : ''),
    );
    if (!current(t) || loading !== environmentSequence) return;
    if (!more) $('android-environments').replaceChildren();
    cursor = result.next_after;
    for (const row of result.items) {
      const card = document.createElement('article'),
        detail = document.createElement('pre'),
        recover = document.createElement('button');
      detail.textContent = JSON.stringify(
        {
          id: row.id,
          action_id: row.action_id,
          target: row.device_serial,
          original_ime: row.original_ime,
          original_subtype: row.original_subtype,
          current_ime: row.current_ime,
          current_subtype: row.current_subtype,
          state: row.state,
          result: row.result_code,
          revision: row.revision,
        },
        null,
        2,
      );
      recover.type = 'button';
      recover.textContent = '准备核查/恢复并请求审批';
      recover.disabled = row.state !== 'pending';
      let requestId = null;
      recover.addEventListener(
        'click',
        wrap(async () => {
          if (!current(t)) return;
          const session = getSession();
          if (!session) throw new Error('先填写归属会话 UUID');
          requestId ||= crypto.randomUUID();
          const receipt = await api(path(t.agent) + `/environments/${row.id}/restore`, {
            method: 'POST',
            body: {
              consent: true,
              session_id: session,
              request_id: requestId,
              revision: row.revision,
            },
          });
          if (current(t)) {
            onRun(receipt.run_id);
            recover.disabled = true;
          }
        }),
      );
      card.append(detail, recover);
      $('android-environments').append(card);
    }
    $('android-environments-more').disabled = !cursor || $('android-environments').childElementCount >= 200;
  }
  $('android-environments-refresh').addEventListener(
    'click',
    wrap(() => environments()),
  );
  $('android-environments-more').addEventListener(
    'click',
    wrap(() => environments(true)),
  );
  return { load, environments, deactivate };
}
