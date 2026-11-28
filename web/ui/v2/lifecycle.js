/* ==========================================================================
   V2 任务生命周期模型（Phase 2）
   --------------------------------------------------------------------------
   单一状态源：服务端 run/ticket 状态 + completion/continuation 事实 → UI 阶段。
   禁止由多个独立布尔量拼状态；本模块只做纯映射，不发请求、不摸 DOM。

   关于 Resume 与 Retry 的诚实边界（后端事实，见 docs 实施记录）：
   - 服务端没有面向所有者的手动续跑 API；中断/崩溃后的续跑只发生在后端的自动恢复扫描
     （run_checkpoint / recover）里，本界面不得把"重新执行"伪装成 Resume。
   - 服务端没有 run 级 retry API；可用的等价物是提交一个新 Turn 重新执行同一输入，
     因此 UI 明确标注为「重新执行（新运行）」，并提示可能重复副作用。
   - 审批只有允许/拒绝；没有延后/暂缓 API，过期由服务端处理。
   ========================================================================== */

export const RUN_STATE_LABEL = {
  idle: '待开始',
  queued: '排队中',
  running: '执行中',
  waiting_approval: '等待审批',
  cancelling: '取消中',
  stopped: '已停止',
  failed: '失败',
  partial: '部分成功',
  succeeded: '已完成',
  cancelled: '已取消',
  interrupted: '已中断',
  unknown: '状态未知',
};

export const ACTIVE_STATES = new Set(['queued', 'running', 'waiting_approval', 'cancelling']);
export const TERMINAL_STATES = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

/** 服务端能力声明：哪部分是真实可用、哪部分只能降级。 */
export const CAPABILITIES = {
  resume: {
    supported: false,
    reason: '服务端只提供后台自动恢复扫描（断点/中断），没有面向所有者的手动续跑 API；本界面不提供 Resume。',
  },
  retry: {
    supported: true,
    kind: 're-execute',
    label: '重新执行本次输入',
    note: '通过新的 Turn 重新执行同一输入，与已取消/失败的运行不是同一 run；已完成的消息与产物保留。',
  },
  approvalDefer: {
    supported: false,
    reason: '审批只有允许/拒绝；没有延后 API，过期由服务端处理。',
  },
};

export function isTerminal(state) {
  return TERMINAL_STATES.has(String(state || ''));
}

export function isActive(state) {
  return ACTIVE_STATES.has(String(state || ''));
}

/**
 * 把 run 记录映射为 UI 阶段：
 * completion.state ∈ verified | partial | needs_review | not_required
 * continuation.decision ∈ review | ...
 */
export function derivePhase(run) {
  if (!run) return { key: 'idle', label: RUN_STATE_LABEL.idle, tone: 'idle', active: false, terminal: false };
  const state = String(run.state || 'unknown');
  const completion = String(run.completion?.state || '');
  let key = state;
  let label = RUN_STATE_LABEL[state] || String(state);
  if (state === 'succeeded' && completion === 'partial') {
    key = 'partial';
    label = RUN_STATE_LABEL.partial;
  } else if (state === 'failed' && completion === 'partial') {
    key = 'partial';
    label = '部分成功（有失败项）';
  } else if (state === 'running' && completion === 'needs_review') {
    label = '执行中 · 结果待核查';
  }
  const active = ACTIVE_STATES.has(state);
  const terminal = TERMINAL_STATES.has(state);
  const tone =
    key === 'failed'
      ? 'danger'
      : key === 'partial'
        ? 'warn'
        : key === 'succeeded'
          ? 'ok'
          : key === 'cancelling' || key === 'waiting_approval'
            ? 'warn'
            : active
              ? 'active'
              : 'idle';
  return { key, label, tone, active, terminal, state, completion };
}

/** 停止按钮的可用性：只有活跃状态可停止；取消中不可重复提交。 */
export function stopAvailability(phase) {
  if (!phase) return { enabled: false, reason: '没有可停止的运行' };
  if (phase.state === 'cancelling') return { enabled: false, reason: '取消请求已提交，等待服务端确认' };
  if (isActive(phase.state)) return { enabled: true, reason: '' };
  return { enabled: false, reason: '运行已结束，无需停止' };
}

/** 重试可用性：终态且属于失败类才提供；成功运行不默认重试。 */
export function retryAvailability(phase, inputText) {
  if (!phase || !phase.terminal) return { enabled: false, reason: '运行尚未结束' };
  if (!inputText) return { enabled: false, reason: '无法恢复本次运行的原始输入' };
  if (phase.key === 'succeeded') return { enabled: false, reason: '本次运行已完成；如需继续请直接发送新消息' };
  return { enabled: true, reason: '' };
}

/** 续跑说明：始终返回事实，供 UI 展示而不是提供假按钮。 */
export function resumeNote(phase) {
  if (!phase) return '';
  if (phase.state === 'interrupted') return CAPABILITIES.resume.reason;
  return '';
}
