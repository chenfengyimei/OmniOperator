/* ==========================================================================
   V2 输入队列（Phase 2）
   --------------------------------------------------------------------------
   语义（诚实声明，UI 也会标注）：
   - 这是**本地草稿队列**：只保存在本机浏览器 localStorage，未提交给服务端。
   - 服务端没有队列管理 API（列表/编辑/删除/重排/取消都没有）；当前端检测到运行中时，
     先把输入存在本地队列，等用户显式「发送下一条」才创建真实 Turn。
   - 不用自动发送代替用户决定；失败条目保留并阻塞后续条目，直到编辑/删除/重试。
   - 切换会话/页面后按 agent+session 恢复；停止运行不会清空队列。
   ========================================================================== */

const STORAGE_KEY = 'omni.v2.input-queue.v1';
const MAX_ITEMS = 20;
const MAX_TEXT = 4000;

const $ = (id) => document.getElementById(id);

function readAll() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function writeAll(value) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 存储不可用时队列只在内存里生效，不阻塞界面
  }
}

export function mountQueue({ getAgent, getSession, onSend, onEdit, isBusy }) {
  let all = readAll();
  let items = [];
  let inflight = false;
  let open = false;
  const key = () => `${getAgent() || ''}|${getSession() || 'new'}`;

  function load() {
    const store = readAll();
    all = store;
    const list = Array.isArray(store[key()]) ? store[key()] : [];
    items = list.filter((item) => item && typeof item.text === 'string').slice(0, MAX_ITEMS);
    render();
  }

  function persist() {
    all[key()] = items;
    writeAll(all);
  }

  function enqueue({ text, mode }) {
    const value = String(text || '');
    if (!value.trim()) return false;
    if (items.length >= MAX_ITEMS) return false;
    items.push({
      id: crypto.randomUUID(),
      text: value.slice(0, MAX_TEXT),
      mode: mode || 'auto',
      state: 'pending',
      error: '',
      created_at: new Date().toISOString(),
    });
    persist();
    render();
    return true;
  }

  function head() {
    return items[0] || null;
  }

  function blocked() {
    return Boolean(items.length && items[0].state === 'failed');
  }

  function pendingCount() {
    return items.filter((item) => item.state !== 'sending').length;
  }

  async function sendNext() {
    if (inflight) return;
    if (isBusy?.()) {
      render();
      return;
    }
    const next = head();
    if (!next) return;
    if (next.state === 'failed') {
      next.error = next.error || '上一次发送失败；请先重试或删除该条，后续条目不会被跳过。';
      persist();
      render();
      return;
    }
    inflight = true;
    next.state = 'sending';
    next.error = '';
    render();
    try {
      await onSend({ text: next.text, mode: next.mode });
      items = items.filter((item) => item.id !== next.id);
    } catch (error) {
      next.state = 'failed';
      next.error = `发送失败：${error?.message || error?.code || '未知错误'}；后续条目保持阻塞。`;
    } finally {
      inflight = false;
      persist();
      render();
    }
  }

  function remove(id) {
    items = items.filter((item) => item.id !== id);
    persist();
    render();
  }

  function edit(id) {
    const node = items.find((item) => item.id === id);
    if (!node) return;
    items = items.filter((item) => item.id !== id);
    persist();
    render();
    onEdit?.(node.text);
  }

  function retry(id) {
    const node = items.find((item) => item.id === id);
    if (!node) return;
    node.state = 'pending';
    node.error = '';
    persist();
    render();
    if (head()?.id === id) sendNext();
  }

  function render() {
    const strip = $('queue-strip');
    if (!strip) return;
    strip.hidden = items.length === 0;
    if (!items.length) {
      strip.replaceChildren();
      return;
    }
    const headNode = document.createElement('div');
    headNode.className = 'queue-head';
    const title = document.createElement('strong');
    title.textContent = `输入队列 · ${items.length} 条（本地草稿，未提交）`;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'btn small';
    toggle.textContent = open ? '收起' : '展开';
    toggle.addEventListener('click', () => {
      open = !open;
      render();
    });
    const send = document.createElement('button');
    send.type = 'button';
    send.className = 'btn small';
    const busy = Boolean(isBusy?.());
    send.textContent =
      items[0]?.state === 'failed' ? '队列已阻塞' : inflight ? '发送中…' : busy ? '等本轮结束' : '发送下一条';
    send.disabled = inflight || blocked() || busy;
    send.title = busy ? '当前运行尚未结束；结束后再发送下一条（输入已保存在本地队列）' : '';
    send.addEventListener('click', () => sendNext());
    headNode.append(title, toggle, send);
    const list = document.createElement('div');
    list.className = 'queue-list';
    list.hidden = !open;
    items.forEach((item, index) => {
      const row = document.createElement('div');
      row.className = 'queue-item';
      row.dataset.state = item.state;
      const order = document.createElement('span');
      order.className = 'queue-order';
      order.textContent = String(index + 1);
      const text = document.createElement('span');
      text.className = 'queue-text';
      const preview = item.text.length > 80 ? `${item.text.slice(0, 77)}…` : item.text;
      text.textContent = index === 0 && blocked() ? `（阻塞）${preview}` : preview;
      const state = document.createElement('span');
      state.className = 'queue-state';
      state.textContent = item.state === 'failed' ? '发送失败' : item.state === 'sending' ? '发送中' : '待发送';
      const acts = document.createElement('span');
      acts.className = 'queue-acts';
      if (item.state === 'failed') {
        const retryBtn = document.createElement('button');
        retryBtn.type = 'button';
        retryBtn.className = 'btn small';
        retryBtn.textContent = '重试';
        retryBtn.addEventListener('click', () => retry(item.id));
        acts.append(retryBtn);
      }
      const editBtn = document.createElement('button');
      editBtn.type = 'button';
      editBtn.className = 'btn small';
      editBtn.textContent = '编辑';
      editBtn.addEventListener('click', () => edit(item.id));
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn small danger';
      del.textContent = '删除';
      del.addEventListener('click', () => remove(item.id));
      acts.append(editBtn, del);
      row.append(order, text, state, acts);
      if (item.error) {
        const error = document.createElement('p');
        error.className = 'queue-error';
        error.textContent = item.error;
        row.append(error);
      }
      list.append(row);
    });
    const note = document.createElement('p');
    note.className = 'queue-note';
    note.textContent =
      '本地队列：停止运行或切换页面不会丢失；服务端没有队列管理 API，只有点「发送下一条」才会创建真实 Turn。';
    strip.replaceChildren(headNode, list, note);
  }

  return {
    load,
    refresh: render,
    reset() {
      load();
    },
    enqueue,
    sendNext,
    count: () => items.length,
    blocked,
    pendingCount,
    /** 供测试/调试读取，不参与渲染。 */
    state() {
      return items.map((item) => ({ id: item.id, state: item.state, text: item.text, error: item.error }));
    },
  };
}
