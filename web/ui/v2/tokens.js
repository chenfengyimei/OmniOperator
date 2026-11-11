import { ask } from '../dialog.js?v=20261103v7';

export function mountTokens({ api, wrap, getGeneration, onRotated }) {
  const $ = (id) => document.getElementById(id);
  let read = 0;

  function reset() {
    read++;
    $('token-sessions').replaceChildren();
    $('token-status').textContent = '';
    $('token-new').textContent = '';
    $('token-notes').textContent =
      '令牌不写入日志或诊断包；轮换后旧令牌立即失效，请立即保存新令牌。页面不会持久化令牌。';
  }

  function render(result) {
    const list = $('token-sessions');
    list.replaceChildren();
    for (const item of result.items) {
      const row = document.createElement('article');
      const title = document.createElement('h3');
      title.textContent = item.current ? '当前会话' : '历史会话';
      const facts = document.createElement('p');
      facts.textContent =
        `创建 ${item.created_at} · 过期 ${item.expires_at} · ` + `撤销 ${item.revoked_at || '未撤销'}`;
      row.append(title, facts);
      list.append(row);
    }
    if (!result.items.length) {
      const empty = document.createElement('p');
      empty.textContent = '没有可显示的会话。';
      list.append(empty);
    }
  }

  async function load() {
    const generation = getGeneration(),
      ticket = ++read;
    $('token-status').textContent = '正在读取 owner 会话…';
    try {
      const result = await api('/owner-token/sessions');
      if (ticket !== read || generation !== getGeneration()) return;
      render(result);
      const current = result.items.filter((item) => item.current).length;
      $('token-status').textContent = `共 ${result.items.length} 个会话，当前有效 ${current} 个。`;
    } catch (error) {
      if (ticket !== read) return;
      reset();
      $('token-status').textContent = '会话读取失败，请刷新重试。';
      throw error;
    }
  }

  async function rotate() {
    const generation = getGeneration();
    if (
      !(await ask({
        title: '轮换 owner 令牌',
        message: '轮换后旧令牌立即失效，所有使用旧令牌的页面需要改用新令牌。新令牌只显示一次，请立即保存到安全位置。',
        confirmLabel: '轮换令牌',
      }))
    )
      return;
    if (generation !== getGeneration()) return;
    $('token-status').textContent = '正在轮换令牌…';
    const result = await api('/owner-token/rotate', { method: 'POST', body: {} });
    if (generation !== getGeneration()) return;
    onRotated(result.token);
    $('token-new').textContent = result.token;
    await load();
    if (generation !== getGeneration()) return;
    $('token-status').textContent += ' 已轮换：新令牌仅显示一次，旧令牌已失效。';
  }

  $('token-refresh').addEventListener('click', wrap(load));
  $('token-rotate').addEventListener('click', wrap(rotate));
  $('token-hide').addEventListener('click', () => {
    $('token-new').textContent = '';
  });
  return { reset, load };
}
