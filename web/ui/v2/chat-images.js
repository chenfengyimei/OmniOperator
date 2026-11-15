// Image drafts contain verified object references only. No image payloads enter browser storage.
export function mountChatImages({ api, wrap, getAgent, getSession, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map();
  let epoch = 0,
    active = '',
    busy = false;
  const key = () => `${getAgent()}/${getSession() || 'new'}`;
  const capture = () => ({ agent: getAgent(), session: getSession(), generation: getGeneration(), epoch });
  const current = (t) =>
    t.agent === getAgent() && t.session === getSession() && t.generation === getGeneration() && t.epoch === epoch;
  function rows() {
    return drafts.get(key()) || [];
  }
  // 截图下拉空态给中文占位，避免渲染成无提示的空白窄框；
  // 占位项 value 为空且 disabled，不会被当成真实截图提交。
  function syncCaptures() {
    const select = $('image-capture');
    const real = [...select.options].filter((o) => o.value);
    if (!real.length) {
      select.replaceChildren(new Option('先载入截图', ''));
      select.options[0].disabled = true;
      select.selectedIndex = 0;
    } else if (select.options[0] && !select.options[0].value) {
      select.options[0].remove();
    }
    select.disabled = !real.length;
    render();
  }
  function render() {
    $('image-drafts').replaceChildren(
      ...rows().map((image) => {
        const item = document.createElement('li'),
          remove = document.createElement('button');
        item.textContent = `${image.width} × ${image.height} · ${image.bytes} 字节 · 已登记，待附加 `;
        remove.type = 'button';
        remove.textContent = '移除';
        remove.addEventListener('click', () => {
          drafts.set(
            key(),
            rows().filter((r) => r.object_id !== image.object_id),
          );
          render();
        });
        item.append(remove);
        return item;
      }),
    );
    $('image-file').disabled = busy || !getAgent() || rows().length >= 4;
    $('image-add-capture').disabled = busy || !getAgent() || rows().length >= 4 || !$('image-capture').value;
  }
  function add(image) {
    if (rows().some((r) => r.object_id === image.object_id)) return;
    if (rows().length >= 4) throw new Error('每条消息最多 4 张图片');
    if (!drafts.has(key()) && drafts.size >= 32) throw new Error('图片草稿较多，请先发送或移除部分草稿');
    drafts.set(key(), [...rows(), image]);
    render();
  }
  $('image-file').addEventListener(
    'change',
    wrap(async () => {
      const file = $('image-file').files[0],
        t = capture();
      $('image-file').value = '';
      if (!file || busy) return;
      if (file.size > 16 * 1024 * 1024) throw new Error('图片上限 16 MiB');
      busy = true;
      render();
      $('image-status').textContent = '正在校验并登记图片…';
      try {
        const image = await api(`/agents/${t.agent}/images`, { method: 'POST', body: file, raw: true });
        if (!current(t)) return;
        add(image);
        $('image-status').textContent = '已登记。发送时才附加到模型请求；需要在模型配置中开启图片协议。';
      } finally {
        busy = false;
        render();
      }
    }),
  );
  $('image-refresh-captures').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      if (!t.agent || !t.session) throw new Error('请先选择已有会话');
      const data = await api(`/agents/${t.agent}/sessions/${t.session}/captures`);
      if (!current(t)) return;
      $('image-capture').replaceChildren(
        ...data.items.map((r) => new Option(`${r.created_at} · ${r.object_id}`, r.object_id)),
      );
      syncCaptures();
    }),
  );
  $('image-capture').addEventListener('change', render);
  $('image-add-capture').addEventListener(
    'click',
    wrap(async () => {
      const object = $('image-capture').value,
        t = capture();
      if (!object || busy) throw new Error('请先选择一张会话截图');
      busy = true;
      render();
      try {
        const image = await api(`/agents/${t.agent}/images/${object}/prepare`, { method: 'POST' });
        if (current(t)) {
          add(image);
          $('image-status').textContent = '截图已校验，待随消息附加。';
        }
      } finally {
        busy = false;
        render();
      }
    }),
  );
  syncCaptures();
  return {
    snapshot() {
      if (busy) throw new Error('请等待图片校验完成');
      return rows().map((r) => r.object_id);
    },
    sent(k, ids) {
      const rest = (drafts.get(k) || []).filter((r) => !ids.includes(r.object_id));
      drafts.set(k, rest);
      render();
    },
    change() {
      epoch++;
      active = key();
      $('image-capture').replaceChildren();
      $('image-status').textContent = '';
      $('image-run-status').textContent = '';
      syncCaptures();
    },
    async report(run, state) {
      const t = capture(),
        report = await api(`/agents/${t.agent}/runs/${run}/images`);
      if (!current(t) || active !== key()) return;
      const states = {
        prepared: '已预留待附加',
        dispatched: '已附加到派发请求',
        attached: '已附加到完成的请求',
        not_sent: '确定未发送',
        unknown: '已派发，结果未知',
      };
      const counts = Object.entries(states)
        .map(([k, text]) => {
          const n = report.requests.filter((r) => r.state === k).length;
          return n ? `${text} ${n} 次` : '';
        })
        .filter(Boolean);
      $('image-run-status').textContent =
        report.registered.length || report.requests.length
          ? `图片请求次数 ${report.used}/${report.budget}；${counts.join('；') || `尚未附加（${state}）`}。这不代表模型已理解图片。`
          : '';
      if (report.unattached?.length)
        $('image-run-status').textContent +=
          ` 未附加原因：${[...new Set(report.unattached.map((r) => r.reason))].join('、')}。`;
    },
    append(node, message) {
      for (const image of message.images || []) {
        const text = document.createElement('p');
        text.textContent = `图片 ${image.width} × ${image.height} · ${image.bytes} 字节 · 对象 ${image.object_id}`;
        node.append(text);
      }
    },
    deactivate() {
      epoch++;
      $('image-capture').replaceChildren();
      $('image-status').textContent = '';
      $('image-run-status').textContent = '';
      syncCaptures();
    },
  };
}
