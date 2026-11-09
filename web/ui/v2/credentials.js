import { ask } from '../dialog.js?v=20261103v7';

// Instance owner authority: no key reads, automatic review, or background polling.
export function mountCredentials({ api, wrap, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const states = { ready: '允许尝试使用', changing: '修改执行中', unknown: '待人工核查', revoked: '已撤销使用' };
  let rows = [],
    row = null,
    selected = '',
    cursor = '',
    epoch = 0,
    writing = false;
  const stamp = () => ({ generation: getGeneration(), epoch, selected });
  const current = (t) => t.generation === getGeneration() && t.epoch === epoch && t.selected === selected;
  function render() {
    $('credential-list').replaceChildren(
      new Option('选择凭据引用', ''),
      ...rows.map(
        (r) =>
          new Option(
            `${r.reference || r.reference_sha256.slice(0, 16) + '…（名称未绑定）'} · ${states[r.state]}`,
            r.id,
          ),
      ),
    );
    $('credential-list').value = selected;
    $('credential-more').disabled = !cursor || rows.length >= 200;
    $('credential-review-fields').disabled = writing || !row || row.state !== 'unknown';
    $('credential-state').textContent = row
      ? `${states[row.state]} · 修订 ${row.revision} · 授权代次 ${row.authority_epoch}\n引用指纹 ${row.reference_sha256}\n此状态只代表所有者授权，不证明密钥或账号有效。`
      : '实例所有者的凭据引用在各 Agent 间共用。每页最多 100 条，当前最多显示 200 条。';
  }
  function reset() {
    epoch++;
    rows = [];
    row = null;
    selected = '';
    cursor = '';
    $('credential-history').textContent = '';
    $('credential-evidence').value = '';
    render();
  }
  async function detail() {
    epoch++;
    row = null;
    $('credential-evidence').value = '';
    $('credential-history').textContent = '';
    render();
    const t = stamp();
    if (!selected) return;
    const value = await api(`/credentials/${selected}`);
    if (!current(t)) return;
    row = value;
    render();
    const [operations, reviews] = await Promise.all([
      api(`/credentials/${selected}/operations?limit=100`),
      api(`/credentials/${selected}/reviews?limit=100`),
    ]);
    if (!current(t)) return;
    $('credential-history').textContent = JSON.stringify(
      { operations: operations.items, reviews: reviews.items },
      null,
      2,
    );
  }
  async function load(more = false) {
    const t = stamp();
    const result = await api(`/credentials?limit=100&after=${encodeURIComponent(more ? cursor : '')}`);
    if (!current(t)) return;
    rows = (more ? [...rows, ...result.items] : result.items).slice(0, 200);
    cursor = result.items.length === 100 ? result.next_after : '';
    render();
  }
  $('credential-list').addEventListener(
    'change',
    wrap(async () => {
      selected = $('credential-list').value;
      await detail();
    }),
  );
  $('credential-refresh').addEventListener(
    'click',
    wrap(async () => {
      await load();
      await detail();
    }),
  );
  $('credential-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('credential-register').addEventListener(
    'click',
    wrap(async () => {
      const reference = $('secret-ref').value,
        t = stamp();
      const accepted = await ask({
        title: '登记凭据引用',
        message: `允许未来尝试使用引用 ${reference}。已有待核查或撤销状态会保留；本操作不读取密钥。`,
        confirmLabel: '登记引用',
      });
      if (!accepted) return;
      if (!current(t)) throw new Error('连接状态已变化，请重新登记');
      await api('/credentials/register', { method: 'POST', body: { reference } });
      await load();
    }),
  );
  $('credential-delete').addEventListener(
    'click',
    wrap(async () => {
      const reference = $('secret-ref').value,
        t = stamp();
      const accepted = await ask({
        title: '删除凭据',
        message: `删除引用 ${reference} 对应的凭据并撤销未来使用。投递授权也会撤销。`,
        confirmLabel: '删除凭据',
        danger: true,
      });
      if (!accepted) return;
      if (!current(t)) throw new Error('连接状态已变化，请重新核对');
      await api(`/credentials?reference=${encodeURIComponent(reference)}`, { method: 'DELETE' });
      if (!current(t)) return;
      $('saved').textContent = '凭据已删除，未来使用已撤销';
      await load();
      await detail();
    }),
  );
  $('credential-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = stamp(),
        reference = $('secret-ref').value,
        value = $('secret').value;
      $('secret').value = '';
      await api(`/credentials?reference=${encodeURIComponent(reference)}`, { method: 'PUT', body: { value } });
      if (!current(t)) return;
      $('saved').textContent = '凭据修改已完成并记录';
      await load();
    }),
  );
  $('credential-review').addEventListener(
    'submit',
    wrap(async () => {
      if (!row || writing || row.state !== 'unknown') return;
      const target = row,
        t = stamp(),
        decision = $('credential-decision').value,
        evidence = $('credential-evidence').value.trim();
      if (!evidence) throw new Error('请填写人工核查依据，不要填写密钥');
      const accepted = await ask({
        title: '核查凭据引用',
        message: `引用 ${target.reference || target.reference_sha256}；修订 ${target.revision}；代次 ${target.authority_epoch}。结论：${decision === 'allow_current' ? '允许未来尝试使用' : '撤销未来使用'}。历史未知操作仍保留，投递需要重新审批。`,
        confirmLabel: '提交核查',
      });
      if (!accepted) return;
      if (!current(t) || row !== target) throw new Error('引用已变化，请重新核对');
      writing = true;
      render();
      try {
        await api(`/credentials/${target.id}/review`, {
          method: 'POST',
          body: {
            revision: target.revision,
            authority_epoch: target.authority_epoch,
            fingerprint: target.fingerprint,
            policy: target.policy,
            decision,
            evidence,
          },
        });
        if (current(t)) {
          await load();
          await detail();
        }
      } finally {
        writing = false;
        render();
      }
    }),
  );
  return { load, reset };
}
