import { demoWork, demoWorkspace } from "./demo.js?v=4";

export const workTitles = {
  activity: "工作进展",
  workers: "Worker 协作",
  tasks: "任务",
  approvals: "审批",
  files: "工具与文件",
};

export const runLabels = {
  queued: "正在等待开始",
  running: "正在处理",
  waiting_approval: "等待确认",
  cancelling: "正在停止",
  succeeded: "已完成",
  failed: "暂未完成",
  cancelled: "已停止",
  interrupted: "处理已中断",
};

const labels = {
  ...runLabels,
  pending: "待处理",
  in_progress: "正在处理",
  completed: "已完成",
  waiting_peer: "等待协作结果",
  ready: "可用",
  attention: "需要留意",
  unavailable: "暂不可用",
};

const descriptions = {
  activity: "当前会话中的工作记录。",
  workers: "查看协作分工和处理状态。",
  tasks: "当前会话中 Agent 已建立的事项。",
  approvals: "当前 Agent 中需要你决定的请求。",
  files: "当前会话的产物和实例提供的能力。",
};

const emptyLabels = {
  activity: "这段会话还没有工作记录。",
  workers: "这段会话还没有协作分工。",
  tasks: "这段会话还没有任务。",
  approvals: "当前没有等待你决定的请求。",
  files: "这段会话还没有可查看的产物。",
};

const toolNames = {
  calendar_add: "添加日历安排", calendar_list: "查看日程",
  files_list: "列出文件", files_read: "读取文件", files_write: "写入文件",
  email_search: "搜索邮件", email_send: "发送邮件", qq_send: "发送消息",
};
const workerTimeFormat = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const element = (id) => document.getElementById(id);
let exampleResolved = false;

const detailTabs = [
  ["conversation", "对话"],
  ["events", "执行记录"],
  ["tools", "能力"],
];

const eventLabels = {
  "delegation.created": "委派已创建",
  "action.completed": "动作已完成",
  started: "开始处理",
  running: "正在处理",
  completed: "已完成",
  succeeded: "已完成",
  failed: "未完成",
  waiting_peer: "等待协作结果",
  cancelled: "已停止",
  interrupted: "已中断",
  tool_call: "调用工具",
};

const detailState = {
  controller: undefined,
  revision: 0,
  tab: "conversation",
  trigger: undefined,
  worker: undefined,
  detail: undefined,
};

export function approvalLabel(request) {
  return Object.hasOwn(toolNames, request.tool) ? toolNames[request.tool] : request.tool || "需要确认的操作";
}

export function readApprovals(current, api) {
  if (api) return api.approvals(current.controller.signal);
  return current.id === demoWorkspace.mainId && !exampleResolved ? demoWork.approvals : [];
}

function node(tag, content, className) {
  const item = document.createElement(tag);
  item.textContent = content;
  if (className) item.className = className;
  return item;
}

function button(label, handler, className = "work-action") {
  const control = node("button", label, className);
  control.type = "button";
  control.addEventListener("click", handler);
  return control;
}

function stateLabel(value) {
  return Object.hasOwn(labels, value) ? labels[value] : "状态待确认";
}

function record(row) {
  const item = node("li", "");
  const status = node("span", stateLabel(row.state), "state-pill");
  if (Object.hasOwn(labels, row.state)) status.dataset.state = row.state;
  item.append(node("strong", row.label), status);
  if (row.detail) item.append(node("p", row.detail, "work-detail"));
  return item;
}

function workerRecord(row, open) {
  const item = node("li", "");
  const status = node("span", stateLabel(row.state), "state-pill");
  if (Object.hasOwn(labels, row.state)) status.dataset.state = row.state;
  item.append(node("strong", row.label || "协作角色"), status);
  if (row.detail) item.append(node("p", row.detail, "work-detail"));
  const actions = node("div", "", "work-actions");
  const control = button("查看详情", () => open(control));
  control.dataset.workerDetail = row.id || "";
  actions.append(control);
  item.append(actions);
  return item;
}

function detailTime(value) {
  if (typeof value !== "string" || !value) return "";
  if (/^\d{2}:\d{2}$/.test(value)) return value;
  const at = Date.parse(value);
  return Number.isFinite(at) ? workerTimeFormat.format(at) : "";
}

function detailNote(content) {
  return node("p", content, "work-detail");
}

function workerMeta(worker) {
  const parts = [];
  if (worker.kind !== "delegation") {
    const key = worker.id || worker.detail;
    if (key) parts.push("任务键 " + key);
  }
  if (worker.profile) parts.push("角色 " + worker.profile);
  if (Number.isInteger(worker.profileVersion)) parts.push("角色 v" + worker.profileVersion);
  if (Number.isInteger(worker.goalVersion)) parts.push("目标 v" + worker.goalVersion);
  if (Number.isInteger(worker.reusedFromVersion)) parts.push("复用 v" + worker.reusedFromVersion);
  if (worker.errorCode) parts.push("错误码 " + worker.errorCode);
  if (worker.unknownEffect === true) parts.push("执行结果未知");
  if (worker.unknownModelCalls === true) parts.push("模型调用情况未知");
  else if (Number.isInteger(worker.unknownModelCalls) && worker.unknownModelCalls > 0) {
    parts.push("有 " + worker.unknownModelCalls + " 次模型调用情况未知");
  }
  if (worker.summaryRedacted) {
    parts.push("摘要已脱敏");
  } else if (worker.summary) {
    let summary = worker.summary;
    if (worker.summaryTruncated) summary += "（摘要已截断）";
    parts.push(summary);
  } else if (worker.summaryRedacted) {
    parts.push("摘要已脱敏");
  }
  if (worker.demo) parts.push("本地演示数据");
  return parts.join(" · ");
}

function detailMode(worker) {
  if (worker.kind !== "delegation") return "ticket";
  if (worker.summaryRedacted === true || !worker.id || !worker.parentId) return "restricted";
  return "detail";
}

function snapshotState(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return { status: "unavailable" };
  return {
    status: "ready",
    messages: Array.isArray(value.messages) ? value.messages : null,
    events: Array.isArray(value.events) ? value.events : null,
    tools: Array.isArray(value.tools) ? value.tools : null,
  };
}

function conversationPanel(worker, rows) {
  const articles = [];
  for (const row of rows) {
    if (!row || (row.role !== "user" && row.role !== "assistant")) continue;
    const isUser = row.role === "user";
    const article = node("article", "", "message message-" + row.role);
    article.dataset.role = row.role;
    if (isUser) article.append(node("strong", "委派指令", "message-sender"));
    const bubble = node("div", "", "message-bubble");
    const metadata = node("header", "", "message-meta");
    if (!isUser) {
      metadata.append(node("strong", worker.label || "Worker", "message-author"), node("span", "Worker", "message-role"));
    }
    const at = detailTime(row.at);
    if (at) {
      const time = node("time", at, "message-time");
      if (typeof row.at === "string") time.dateTime = row.at;
      metadata.append(time);
    }
    bubble.append(metadata, node("p", typeof row.text === "string" ? row.text : "", "message-text"));
    article.append(bubble);
    articles.push(article);
  }
  return articles;
}

function eventsPanel(rows) {
  const list = node("ul", "", "activity-list");
  let count = 0;
  for (const row of rows) {
    if (!row) continue;
    const kind = typeof row.kind === "string" ? row.kind : "";
    const item = node("li", "");
    item.append(node("strong", Object.hasOwn(eventLabels, kind) ? eventLabels[kind] : kind || "执行更新"));
    const at = detailTime(row.at);
    if (at) {
      const time = node("time", at, "activity-time");
      time.dateTime = row.at;
      item.append(time);
    }
    list.append(item);
    count += 1;
  }
  return count ? list : null;
}

function toolsPanel(rows) {
  const list = node("ul", "", "activity-list");
  const seen = new Set();
  let count = 0;
  for (const name of rows) {
    if (typeof name !== "string" || !name || seen.has(name)) continue;
    seen.add(name);
    const item = node("li", "");
    const label = Object.hasOwn(toolNames, name) ? toolNames[name] : name;
    item.append(node("strong", label));
    if (label !== name) item.append(node("code", name));
    list.append(item);
    count += 1;
  }
  return count ? list : null;
}

function renderDetailBody() {
  const body = element("worker-detail-body");
  const worker = detailState.worker;
  if (!body || !worker) return;
  const detail = detailState.detail || { status: "loading" };
  const tab = detailState.tab;
  if (detail.status === "loading") {
    body.replaceChildren(detailNote("正在读取 Worker 详情…"));
    return;
  }
  if (detail.status === "unavailable") {
    body.replaceChildren(detailNote("暂时无法读取这个 Worker 的详情。可以返回列表后再试。"));
    return;
  }
  if (detail.status === "restricted") {
    const target = tab === "conversation" ? "Worker 对话" : tab === "events" ? "执行记录" : "工具调用";
    body.replaceChildren(detailNote(detail.reason === "ticket" ?
      "Ticket 的安全投影不包含" + target + "。" :
      "这次委派只提供安全投影，" + target + "没有开放。"));
    return;
  }
  const key = tab === "conversation" ? "messages" : tab;
  const rows = detail[key];
  if (!Array.isArray(rows)) {
    const name = tab === "conversation" ? "对话" : tab === "events" ? "执行记录" : "能力记录";
    body.replaceChildren(detailNote("暂时无法读取这个 Worker 的" + name + "。"));
    return;
  }
  if (!rows.length) {
    body.replaceChildren(detailNote(tab === "conversation" ? "这次协作没有留下对话。" :
      tab === "events" ? "这次协作没有执行记录。" : "这次协作暂无可查看的工具快照。"));
    return;
  }
  const panel = tab === "conversation" ? conversationPanel(worker, rows) :
    tab === "events" ? eventsPanel(rows) : toolsPanel(rows);
  if (Array.isArray(panel)) {
    if (panel.length) body.replaceChildren(...panel);
    else body.replaceChildren(detailNote("这次协作没有留下对话。"));
  } else if (panel) {
    if (tab === "tools") body.replaceChildren(detailNote("该 Worker 执行时可用的工具"), panel);
    else body.replaceChildren(panel);
  } else {
    body.replaceChildren(detailNote(tab === "events" ? "这次协作没有执行记录。" : "这次协作暂无可查看的工具快照。"));
  }
}

function selectDetailTab(key) {
  detailState.tab = key;
  for (const control of element("worker-detail-tabs").children) {
    control.setAttribute("aria-pressed", String(control.dataset.workerTab === key));
  }
  renderDetailBody();
}

function buildDetailTabs() {
  const tabs = detailTabs.map(([key, label]) => {
    const control = button(label, () => {
      if (detailState.tab !== key) selectDetailTab(key);
    }, "");
    control.dataset.workerTab = key;
    control.setAttribute("aria-pressed", String(detailState.tab === key));
    control.setAttribute("aria-controls", "worker-detail-body");
    return control;
  });
  element("worker-detail-tabs").replaceChildren(...tabs);
}

export function clearWorkerDetail() {
  detailState.controller?.abort();
  detailState.controller = undefined;
  detailState.revision += 1;
  detailState.tab = "conversation";
  detailState.trigger = undefined;
  detailState.worker = undefined;
  detailState.detail = undefined;
  const detail = element("worker-detail");
  if (detail) detail.hidden = true;
  const name = element("worker-detail-name");
  if (name) name.textContent = "";
  const state = element("worker-detail-state");
  if (state) {
    state.textContent = "";
    state.removeAttribute("data-state");
  }
  const meta = element("worker-detail-meta");
  if (meta) meta.textContent = "";
  element("worker-detail-body")?.replaceChildren();
  element("worker-detail-tabs")?.replaceChildren();
  const list = element("work-list");
  if (list) list.hidden = false;
  const empty = element("work-empty");
  if (empty) empty.hidden = list ? list.children.length > 0 : true;
  const more = element("work-more");
  if (more) more.hidden = false;
  const status = element("work-status");
  if (status) status.hidden = false;
}

function bindWorkerDetail() {
  const back = element("worker-detail-back");
  if (!back || back.dataset.workerDetailBound === "true") return;
  back.dataset.workerDetailBound = "true";
  back.addEventListener("click", () => {
    const trigger = detailState.trigger;
    clearWorkerDetail();
    if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    else element("work-title")?.focus({ preventScroll: true });
  });
}

function openWorkerDetail(worker, trigger, current, api, active) {
  const detail = element("worker-detail");
  if (!detail) return;
  detailState.controller?.abort();
  detailState.revision += 1;
  const revision = detailState.revision;
  const controller = detailState.controller = new AbortController();
  detailState.tab = "conversation";
  detailState.trigger = trigger;
  detailState.worker = worker;
  const mode = detailMode(worker);
  detailState.detail = mode === "detail" ? { status: "loading" } : {
    status: "restricted",
    reason: mode === "ticket" ? "ticket" : "restricted",
  };

  const name = element("worker-detail-name");
  name.textContent = worker.label || "协作 Worker";
  const status = element("worker-detail-state");
  const known = Object.hasOwn(labels, worker.state);
  status.textContent = stateLabel(worker.state);
  status.dataset.state = known ? worker.state : "";
  element("worker-detail-meta").textContent = workerMeta(worker);
  buildDetailTabs();
  renderDetailBody();

  element("work-list").hidden = true;
  element("work-empty").hidden = true;
  element("work-more").hidden = true;
  element("work-status").hidden = true;
  detail.hidden = false;
  name.focus({ preventScroll: true });

  if (mode === "detail" && worker.snapshot) {
    detailState.detail = snapshotState(worker.snapshot);
    renderDetailBody();
    return;
  }
  if (mode !== "detail") return;
  if (!api || typeof api.workerDetail !== "function") {
    detailState.detail = { status: "unavailable" };
    renderDetailBody();
    return;
  }
  const signal = AbortSignal.any([current.controller.signal, controller.signal]);
  void api.workerDetail(worker, signal).then((snapshot) => {
    if (revision !== detailState.revision || signal.aborted || !active()) return;
    detailState.detail = snapshotState(snapshot);
    renderDetailBody();
  }).catch(() => {
    if (revision !== detailState.revision || signal.aborted || !active()) return;
    detailState.detail = { status: "unavailable" };
    renderDetailBody();
  });
}

function approvalRow(request, api, active, finish, onApprovalDecided) {
  const item = node("li", "");
  const status = node("span", "待决定", "state-pill");
  status.dataset.state = "waiting_approval";
  item.append(node("strong", approvalLabel(request)), status);
  if (request.arguments && Object.keys(request.arguments).length) {
    const details = node("details", "");
    details.append(node("summary", "查看请求详情"), node("pre", JSON.stringify(request.arguments, null, 2)));
    item.append(details);
  }
  const actions = node("div", "", "work-actions");
  const feedback = node("p", "", "work-detail");
  feedback.setAttribute("role", "status");
  const decide = async (allowed) => {
    for (const control of actions.querySelectorAll("button")) control.disabled = true;
    feedback.textContent = "正在提交决定…";
    try {
      if (api) await api.decide(request, allowed, AbortSignal.timeout(12000));
      else exampleResolved = true;
      onApprovalDecided();
      if (active()) finish(item, allowed ? "已记录这次允许。" : "已记录这次拒绝。");
    } catch {
      if (!active()) return;
      feedback.textContent = "暂时无法确认决定。请重新打开审批核对状态。";
    }
  };
  actions.append(button("拒绝", () => void decide(false)), button("允许一次", () => void decide(true)));
  item.append(actions, feedback);
  return item;
}

async function rowsFor(page, current, api) {
  if (page === "approvals") return readApprovals(current, api);
  if (!api) {
    if (current.id !== demoWorkspace.mainId) return [];
    return demoWork[page];
  }
  const signal = current.controller.signal;
  if (page === "activity") return api.runs(current.id, signal);
  if (page === "workers") return api.workers(current.id, signal);
  return api.tasks(current.id, signal);
}

export async function showWorkPage(page, current, api, active, openReport, onApprovalDecided) {
  bindWorkerDetail();
  clearWorkerDetail();
  const list = element("work-list");
  const more = element("work-more");
  const empty = element("work-empty");
  const note = element("work-status");
  element("work-title").textContent = workTitles[page];
  element("work-description").textContent = descriptions[page];
  list.replaceChildren();
  more.replaceChildren();
  empty.hidden = true;
  note.textContent = "正在读取…";

  if (page === "files") {
    for (const report of current.artifacts) {
      const entry = node("li", "");
      entry.append(node("strong", report.name), button("打开", (event) => openReport(report, event.currentTarget)));
      list.append(entry);
    }
    empty.textContent = emptyLabels.files;
    empty.hidden = list.children.length > 0;
    note.textContent = "";
    if (api) {
      try {
        const capabilities = await api.capabilities(current.controller.signal);
        if (active() && capabilities.length) {
          const capabilityList = node("ul", "", "activity-list");
          capabilityList.append(...capabilities.map(record));
          more.append(node("h3", "可用能力"), capabilityList);
        }
      } catch {
        if (active()) more.append(node("p", "暂时无法读取实例能力。", "work-detail"));
      }
    }
    return;
  }

  try {
    const rows = await rowsFor(page, current, api);
    if (!active()) return;
    note.textContent = "";
    empty.textContent = emptyLabels[page];
    empty.hidden = rows.length > 0;
    const finish = (item, receipt) => {
      item.remove();
      empty.hidden = list.children.length > 0;
      note.textContent = receipt;
      element("work-title").focus({ preventScroll: true });
    };
    list.append(...rows.map((row) => {
      if (page === "approvals") return approvalRow(row, api, active, finish, onApprovalDecided);
      if (page === "workers") {
        return workerRecord(row, (control) => openWorkerDetail(row, control, current, api, active));
      }
      return record({ ...row, label: row.label || "会话中的工作" });
    }));
  } catch {
    if (active()) note.textContent = "暂时无法读取。可以重新打开这页试试。";
  }
}
