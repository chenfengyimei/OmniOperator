import { mountMaintenance } from './maintenance.js?v=20261110v53';

export function mountAuditHistory({
  api,
  wrap,
  getGeneration,
  path,
  listId,
  statusId,
  refreshId,
  moreId,
  empty,
  recordLabel = '审计',
  pageSize = 100,
  parameters = () => ({}),
  describe = () => '',
  render = (rows) => JSON.stringify(rows, null, 2),
}) {
  const $ = (id) => document.getElementById(id);
  let read = 0,
    cursor = '',
    rows = [],
    hasMore = false;
  function clear() {
    cursor = '';
    rows = [];
    hasMore = false;
    $(listId).textContent = '';
    $(moreId).disabled = true;
  }
  function reset() {
    read++;
    clear();
    $(statusId).textContent = '';
    $(refreshId).disabled = false;
  }
  async function load(restart = true) {
    const ticket = ++read,
      generation = getGeneration(),
      endpoint = path();
    if (restart) clear();
    $(refreshId).disabled = $(moreId).disabled = true;
    $(statusId).textContent = `正在读取${recordLabel}记录…`;
    const params = new URLSearchParams({ limit: String(pageSize) });
    if (cursor) params.set('cursor', cursor);
    for (const [key, value] of Object.entries(parameters(cursor))) {
      if (value !== undefined) params.set(key, value);
    }
    try {
      const data = await api(`${endpoint}?${params}`);
      if (ticket !== read || generation !== getGeneration()) return;
      cursor = data.next_cursor || '';
      hasMore = Boolean(data.has_more && cursor);
      rows = [...rows, ...data.items].slice(-200);
      $(listId).textContent = rows.length ? render(rows, data) : empty;
      $(statusId).textContent =
        `当前显示 ${rows.length} 条，最多保留最近读取的 200 条。${hasMore ? '还有记录可继续加载。' : '已读到当前末尾。'}` +
        (data.history_trimmed ? ` 部分${recordLabel}历史已清理，仅显示保留记录。` : '') +
        describe(data);
    } catch (error) {
      if (ticket !== read || generation !== getGeneration()) return;
      if (error.code === 'history_cursor_expired' || error.status === 410) {
        clear();
        $(statusId).textContent =
          `${recordLabel}历史已清理，原游标已失效；旧记录已清空，请点击“${$(refreshId).textContent}”重新加载。`;
        return;
      }
      $(statusId).textContent = `${recordLabel}读取失败，请重新加载。`;
      throw error;
    } finally {
      if (ticket === read) {
        if (generation !== getGeneration()) $(statusId).textContent = `${recordLabel}读取已中止，请重新加载。`;
        $(refreshId).disabled = false;
        $(moreId).disabled = !hasMore;
      }
    }
  }
  $(refreshId).addEventListener(
    'click',
    wrap(() => load()),
  );
  $(moreId).addEventListener(
    'click',
    wrap(() => load(false)),
  );
  return { load, reset };
}

export function mountOperations({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const maintenance = mountMaintenance({ api, wrap, getAgent, getGeneration });
  const retentionReasons = {
    kind_not_eligible: '该审计种类须保留',
    subject_missing: '主体不存在或归属无法核验',
    subject_state_mismatch: '主体当前状态与审计不一致',
    audit_memory_source: '审计仍是记忆来源',
    audit_document_author: '审计仍是文档作者依据',
    audit_reference: '审计仍被授权或核查记录引用',
    configuration_unresolved: '历史配置尚未核清',
    run_not_terminal: '运行尚未结束',
    within_audit_retention: '审计尚在保留期内',
    within_run_retention: '运行尚在保留期内',
    run_pinned: '运行已固定保留',
    body_not_pruned: '执行正文尚未裁剪',
    pruning_batch_incomplete: '原正文裁剪批次未完成',
    approval_pending: '仍有待处理审批',
    action_unresolved: '动作或副作用尚未核清',
    model_cost_unresolved: '模型费用尚未核清',
    work_unresolved: '仍有未完成工作',
    run_resource_held: '运行仍持有资源',
    outbound_unresolved: '投递尚未完成或结果未知',
    memory_source: '仍被记忆来源引用',
    document_context_source: '仍被文档上下文引用',
    artifact_pinned: '产物已固定保留',
    retention_batch_held: '仍有未完成正文维护批次',
    reference_release_held: '仍有未完成引用维护批次',
    audit_retention_held: '审计已被待完成清理计划占用',
    event_retention_held: '事件明细已被待完成清理计划占用',
    event_kind_not_eligible: '该事件种类须保留',
    event_run_missing: '事件所属运行不存在或归属无法核验',
    event_body_not_pruned: '事件正文尚未裁剪或裁剪依据不一致',
    event_state_mismatch: '事件与当前运行或动作状态不一致',
    event_action_missing: '事件关联动作不存在或归属无法核验',
  };
  const protectionText = (row) =>
    row.protected_reasons.map((reason) => retentionReasons[reason] || reason).join('；') ||
    '无（仅为当前预览，不授权删除）';
  let previewAsOf = '',
    eventPreviewAsOf = '';
  const retentionPreview = mountAuditHistory({
    api,
    wrap,
    getGeneration,
    path: () => `/agents/${getAgent()}/audit/retention-preview`,
    listId: 'audit-retention-list',
    statusId: 'audit-retention-status',
    refreshId: 'refresh-audit-retention',
    moreId: 'more-audit-retention',
    empty: '当前页没有待检查的审计记录。',
    pageSize: 20,
    parameters: (cursor) => (cursor ? { as_of: previewAsOf } : {}),
    describe: (data) => {
      previewAsOf = data.as_of;
      return ` 本页检查 ${data.counts.scanned} 条，符合预览条件 ${data.counts.eligible} 条，受保护 ${data.counts.protected} 条。预览时点 ${data.as_of}；180天截止 ${data.cutoff}。`;
    },
    render: (rows) =>
      rows
        .map(
          (row) =>
            `${row.created_at} · ${row.kind} · ${row.eligible ? '符合预览条件' : '保留'}\n审计 ${row.audit_id} · 序号 ${row.sequence}\n主体 ${row.subject_kind || '未识别'} · ${row.operation_id || '未登记'}\n运行 ${row.run_id || '未关联'}\n保护原因：${protectionText(row)}`,
        )
        .join('\n\n'),
  });
  const eventRetentionPreview = mountAuditHistory({
    api,
    wrap,
    getGeneration,
    path: () => `/agents/${getAgent()}/events/retention-preview`,
    listId: 'event-retention-list',
    statusId: 'event-retention-status',
    refreshId: 'refresh-event-retention',
    moreId: 'more-event-retention',
    empty: '当前页没有待检查的事件明细。',
    recordLabel: '事件',
    pageSize: 20,
    parameters: (cursor) => (cursor ? { as_of: eventPreviewAsOf } : {}),
    describe: (data) => {
      eventPreviewAsOf = data.as_of;
      return ` 本页检查 ${data.counts.scanned} 条，符合预览条件 ${data.counts.eligible} 条，受保护 ${data.counts.protected} 条。预览时点 ${data.as_of}；180天截止 ${data.cutoff}。`;
    },
    render: (rows) =>
      rows
        .map(
          (row) =>
            `${row.created_at} · ${row.kind} · ${row.eligible ? '符合预览条件' : '保留'}\n全局序号 ${row.sequence} · 运行内序号 ${row.seq}\n运行 ${row.run_id}\n保护原因：${protectionText(row)}`,
        )
        .join('\n\n'),
  });
  const instanceAudit = mountAuditHistory({
    api,
    wrap,
    getGeneration,
    path: () => '/system/audit',
    listId: 'instance-audit',
    statusId: 'instance-audit-status',
    refreshId: 'read-instance-audit',
    moreId: 'more-instance-audit',
    empty: '暂无实例维护审计',
    render: (rows) => {
      const labels = {
        requested: '已登记意图，须结合后续结果；缺少结果时按未知处理',
        applied: '已完成',
        not_applied: '未应用',
        unknown: '结果未知，请核查后再决定后续操作',
        registered: '已登记',
        allowed: '已允许后续使用',
        revoked: '已撤销后续使用',
      };
      const kinds = {
        'credential.requested': '凭据修改意图',
        'credential.resolved': '凭据修改结果',
        'credential.registered': '凭据引用登记',
        'credential.reviewed': '凭据授权核查',
        'credential.recovered': '凭据恢复对账',
        'credential.restored': '凭据备份恢复',
      };
      return rows
        .map((row) => {
          const operation = row.data?.operation,
            credentialChange = ['credential.requested', 'credential.resolved'].includes(row.kind),
            label =
              credentialChange && ['set', 'delete'].includes(operation)
                ? operation === 'delete'
                  ? '删除凭据'
                  : '保存凭据'
                : kinds[row.kind] || `实例维护记录 · ${row.kind || '未知类型'}`;
          return (
            `${row.created_at} · ${label} · ${labels[row.outcome] || row.outcome || '未提供结果'}\n操作 ${row.operation_id}` +
            (row.data?.reference_sha256 ? ` · 引用指纹 ${row.data.reference_sha256}` : '')
          );
        })
        .join('\n\n');
    },
  });
  let read = 0,
    download = 0,
    debugOperation = null,
    debugRead = 0,
    debugCursor = '',
    debugRows = [];
  function reset() {
    read++;
    download++;
    instanceAudit.reset();
    retentionPreview.reset();
    eventRetentionPreview.reset();
    maintenance.reset();
    previewAsOf = eventPreviewAsOf = '';
    $('metrics-status').textContent = $('metrics-details').textContent = '';
    $('refresh-metrics').disabled = $('export-diagnostics').disabled = false;
    debugOperation = null;
    debugRead++;
    debugCursor = '';
    debugRows = [];
    $('debug-records').textContent = '';
    $('read-debug').disabled = true;
    $('debug-status').textContent = '';
    $('start-debug').disabled = false;
    $('stop-debug').disabled = true;
  }
  async function load() {
    const ticket = ++read,
      generation = getGeneration();
    $('refresh-metrics').disabled = true;
    $('metrics-status').textContent = '正在读取运行指标…';
    try {
      const [data, debug] = await Promise.all([
        api('/system/metrics'),
        api(`/agents/${getAgent()}/debug`),
        maintenance.load(),
      ]);
      if (ticket !== read || generation !== getGeneration()) return;
      const persisted = data.persisted,
        maintained = persisted.maintenance || {},
        activeMaintenance = (maintained.claimed || 0) + (maintained.running || 0) + (maintained.cancelling || 0);
      $('metrics-status').textContent =
        `${data.runtime.ready ? '实例已就绪' : '实例尚未就绪'} · 活跃回合 ${data.runtime.active_runs} · 排队回合 ${persisted.runs.queued || 0} · 未核查投递 ${persisted.unreviewed_deliveries} · 未知模型调用 ${persisted.model_calls.unknown || 0} · 待收尾审计清理 ${persisted.audit_retention?.prepared || 0} · 待收尾事件清理 ${persisted.event_retention?.prepared || 0} · 维护排队 ${maintained.queued || 0} · 维护执行中 ${activeMaintenance} · 记忆整理排队 ${persisted.memory_organization?.queued || 0} · 待核查记忆提案 ${persisted.memory_organization?.ready || 0} · 待核查凭据变更 ${persisted.unresolved_credential_changes} · 日志丢失 ${data.host.logs.dropped ?? '未知'}`;
      $('metrics-details').textContent = JSON.stringify(data, null, 2);
      if (debugOperation !== (debug.operation_id || null)) {
        debugRead++;
        debugCursor = '';
        debugRows = [];
        $('debug-records').textContent = '';
      }
      debugOperation = debug.operation_id || null;
      $('start-debug').disabled = debug.active || debug.occupied;
      $('stop-debug').disabled = !debug.active;
      $('read-debug').disabled = !debugOperation;
      $('debug-status').textContent = debug.active
        ? `限时调试已开启 · 刷新时剩余 ${Math.ceil(debug.remaining_s)} 秒 · 已记录 ${debug.bytes} 字节`
        : debug.occupied
          ? '另一 Agent 正在调试，请先在对应空间停止。'
          : '限时调试已关闭';
    } finally {
      if (ticket === read && generation === getGeneration()) $('refresh-metrics').disabled = false;
    }
  }
  $('refresh-metrics').addEventListener('click', wrap(load));
  $('read-debug').addEventListener(
    'click',
    wrap(async () => {
      const ticket = ++debugRead,
        generation = getGeneration(),
        operation = debugOperation;
      if (!operation) return;
      $('read-debug').disabled = true;
      try {
        const data = await api(
          `/agents/${getAgent()}/debug/${operation}/logs?limit=100&cursor=${encodeURIComponent(debugCursor)}`,
        );
        if (ticket !== debugRead || generation !== getGeneration() || operation !== debugOperation) return;
        debugCursor = data.next_cursor || '';
        debugRows = [...debugRows, ...data.items].slice(-500);
        $('debug-records').textContent = debugRows.map((row) => JSON.stringify(row)).join('\n') || '尚无调试记录';
      } finally {
        if (ticket === debugRead && generation === getGeneration()) $('read-debug').disabled = false;
      }
    }),
  );
  for (const action of ['start', 'stop']) {
    $(`${action}-debug`).addEventListener(
      'click',
      wrap(async () => {
        const generation = getGeneration(),
          agent = getAgent(),
          operation = debugOperation;
        $('start-debug').disabled = $('stop-debug').disabled = true;
        try {
          await api(`/agents/${agent}/debug${action === 'stop' ? `/${operation}` : ''}`, {
            method: action === 'start' ? 'POST' : 'DELETE',
            ...(action === 'start' ? { body: { duration_s: Number($('debug-duration').value) } } : {}),
          });
        } finally {
          if (generation === getGeneration()) await load();
        }
      }),
    );
  }
  $('export-diagnostics').addEventListener(
    'click',
    wrap(async () => {
      const ticket = ++download,
        generation = getGeneration();
      $('export-diagnostics').disabled = true;
      try {
        const blob = await api('/system/diagnostics', { binary: true });
        if (ticket !== download || generation !== getGeneration()) return;
        const url = URL.createObjectURL(blob),
          link = document.createElement('a');
        link.href = url;
        link.download = 'omni-diagnostics.zip';
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } finally {
        if (ticket === download && generation === getGeneration()) $('export-diagnostics').disabled = false;
      }
    }),
  );
  return { load, reset };
}
