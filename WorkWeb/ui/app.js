import { RequestError, WorkbenchClient } from "./api.js?v=7";
import { approvalLabel, clearWorkerDetail, readApprovals, runLabels, showWorkPage, workTitles } from "./work-pages.js?v=6";
import { demoContents, demoThreads, demoWork, demoWorkspace } from "./demo.js?v=4";

const element = (id) => document.getElementById(id);
const draft = element("draft");
const messages = element("messages");
const threadLog = element("thread-log");
const messageEmpty = element("messages-empty");
const sessionActivity = element("session-activity");
const activitySummaryList = element("activity-summary-list");
const send = element("send");
const settings = element("settings-dialog");
const detail = element("artifact-dialog");
const narrow = matchMedia("(max-width: 900px)");
const drafts = new Map();
const positions = new Map();
const artifactSelections = new Map();

const terminalRuns = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" });
const timeFormat = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
let workspace = demoWorkspace;
let client;
let selection;
let artifactRequest;
let receiptCheck;
let connectionAttempt;
let uncertain;
let sending = false;
let page = "conversation";
let workRevision = 0;
let openedPane;
let paneTrigger;

function child(tag, content, className) {
  const node = document.createElement(tag);
  node.textContent = content;
  if (className) node.className = className;
  return node;
}

function buttonRow(label, action, selected) {
  const item = child("li", "");
  const control = child("button", label);
  control.type = "button";
  control.setAttribute("aria-pressed", String(selected));
  if (selected) control.setAttribute("aria-current", "true");
  control.addEventListener("click", action);
  item.append(control);
  return item;
}

function bindThemeControls() {
  const group = document.querySelector(".themes");
  if (!group) return;
  const options = [...group.querySelectorAll("[data-theme-choice]")];
  if (!options.length) return;
  const api = window.WorkWebTheme;
  const choose = (choice) => api?.set(choice);
  const render = (pref) => {
    for (const option of options) {
      const selected = option.dataset.themeChoice === pref;
      option.setAttribute("aria-checked", String(selected));
      option.tabIndex = selected ? 0 : -1;
    }
  };
  for (const option of options) {
    option.addEventListener("click", () => choose(option.dataset.themeChoice));
  }
  group.addEventListener("keydown", (event) => {
    const current = options.indexOf(document.activeElement);
    if (current < 0) return;
    let next;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % options.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + options.length) % options.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = options.length - 1;
    else return;
    event.preventDefault();
    options[next].focus();
    choose(options[next].dataset.themeChoice);
  });
  document.addEventListener("workweb:theme-change", (event) => render(event.detail.pref));
  render(api ? api.pref() : "auto");
}

function syncPaneVisibility() {
  element("sessions-pane").hidden = narrow.matches && openedPane !== "sessions";
  element("artifacts-pane").hidden = (narrow.matches && openedPane !== "artifacts") ||
    (!narrow.matches && document.body.classList.contains("artifacts-collapsed"));
  element("open-artifacts").setAttribute("aria-expanded", String(!element("artifacts-pane").hidden));
}

function closePane(restore = true) {
  if (!openedPane) return;
  document.body.classList.remove(openedPane + "-open");
  paneTrigger?.setAttribute("aria-expanded", "false");
  element("workspace").inert = false;
  element(openedPane === "sessions" ? "artifacts-pane" : "sessions-pane").inert = false;
  if (restore) paneTrigger?.focus();
  openedPane = null;
  paneTrigger = null;
  syncPaneVisibility();
}

function openPane(name, trigger) {
  if (!narrow.matches) {
    if (name === "artifacts") document.body.classList.toggle("artifacts-collapsed");
    syncPaneVisibility();
    return;
  }
  closePane(false);
  openedPane = name;
  paneTrigger = trigger;
  document.body.classList.add(name + "-open");
  trigger.setAttribute("aria-expanded", "true");
  element("workspace").inert = true;
  element(name === "sessions" ? "artifacts-pane" : "sessions-pane").inert = true;
  syncPaneVisibility();
  element("close-" + name).focus();
}

function showDialog(dialog, trigger) {
  if (dialog.open) return;
  dialog.addEventListener("close", () => {
    if (trigger.isConnected) trigger.focus();
  }, { once: true });
  dialog.showModal();
}

function refreshWork(name) {
  const current = selection;
  const api = client;
  const revision = ++workRevision;
  const active = () => current === selection && revision === workRevision && page === name;
  void showWorkPage(name, current, client, active, async (artifact, trigger) => {
    const loaded = await showArtifact(current, artifact, false, true);
    if (loaded && active()) openReport(trigger, artifact);
  }, () => {
    if (api === client) void refreshApprovalSummary(selection);
  });
}

function showPage(name) {
  if (page === "conversation" && name !== "conversation") rememberReadingPosition();
  if (page === "workers" && name === "conversation") clearWorkerDetail();
  page = name;
  element("view-location").textContent = workTitles[name] || "主会话";
  element("conversation-view").hidden = name !== "conversation";
  element("work-view").hidden = name === "conversation";
  for (const control of document.querySelectorAll("[data-page]")) {
    if (control.dataset.page === name) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  }
  if (name === "conversation") restoreReadingPosition();
  else refreshWork(name);
  syncJumpLatest();
}

function formatDate(value, format) {
  const date = Date.parse(value);
  return Number.isFinite(date) ? format.format(date) : "";
}

function renderSessions(keepFocus = false) {
  const list = element("session-list");
  const query = element("session-search").value.trim().toLowerCase();
  const filtered = workspace.sessions.filter(({ title }) => title.toLowerCase().includes(query));
  list.replaceChildren(...filtered.map((session) => {
    const item = buttonRow(session.title, () => {
      openSession(session);
      if (!narrow.matches) renderSessions(true);
    }, session.id === selection.id);
    const control = item.firstElementChild;
    control.setAttribute("aria-label", session.title);
    const heading = child("span", "", "session-top");
    heading.append(child("strong", session.title, "session-name"));
    const date = client ? formatDate(session.at, dateFormat) : session.at;
    if (date) heading.append(child("span", date, "session-time"));
    const meta = session.meta || (session.id === workspace.mainId ? "主会话" : "会话记录");
    control.replaceChildren(heading);
    if (session.note) control.append(child("span", session.note, "session-note"));
    control.append(child("span", meta, "session-meta"));
    return item;
  }));
  element("session-empty").hidden = filtered.length > 0;
  if (keepFocus) list.querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
}

function isFollowingMessages() {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 36;
}

function conversationVisible() {
  return page === "conversation" && !element("conversation-view").hidden;
}

function rememberReadingPosition() {
  if (!selection?.id || !selection.loaded || !conversationVisible() || messages.clientHeight === 0) return;
  positions.set(selection.id, { top: Math.max(0, messages.scrollTop), following: isFollowingMessages() });
}

function restoreReadingPosition() {
  if (!selection?.id || !selection.loaded) return;
  const saved = positions.get(selection.id) ?? { top: 0, following: Boolean(client) };
  messages.scrollTop = saved.following ? messages.scrollHeight : saved.top;
  rememberReadingPosition();
}

function syncJumpLatest() {
  const control = element("jump-latest");
  const long = threadLog.scrollHeight > messages.clientHeight + 1;
  control.hidden = !selection?.loaded || !conversationVisible() || !long || isFollowingMessages();
}

function followLatestMessages() {
  messages.scrollTop = messages.scrollHeight;
  rememberReadingPosition();
  syncJumpLatest();
}

function jumpToLatest() {
  followLatestMessages();
  messages.focus({ preventScroll: true });
}

function renderMessages(rows, follow, position) {
  const top = messages.scrollTop;
  threadLog.replaceChildren(messageEmpty, ...rows.map((row) => {
    const article = child("article", "", "message message-" + row.role);
    article.dataset.role = row.role;
    const isUser = row.role === "user";
    const bubble = child("div", "", "message-bubble");
    const metadata = child("header", "", "message-meta");
    if (isUser) {
      const name = workspace.ownerDisplayName || "你";
      const sender = child("strong", name, "message-sender");
      sender.title = name;
      sender.dir = "auto";
      article.append(sender);
    } else {
      metadata.append(child("strong", workspace.name, "message-author"), child("span", "主 Agent", "message-role"));
    }
    const formatted = client ? formatDate(row.at, timeFormat) : row.at;
    if (formatted) {
      const time = child("time", formatted, "message-time");
      time.dateTime = row.at;
      metadata.append(time);
    }
    bubble.append(metadata, child("p", row.text, "message-text"));
    article.append(bubble);
    return article;
  }));
  messageEmpty.hidden = rows.length > 0;
  messages.scrollTop = follow ? messages.scrollHeight : (position ?? top);
}

function renderActivitySummary(current) {
  const follow = isFollowingMessages();
  const recent = client ? current.runs.slice(-3).reverse() : [...current.runs];
  if (!client && current.approvals?.length) {
    const request = current.approvals[0];
    recent.push({ label: approvalLabel(request), state: "waiting_approval", at: request.createdAt, detail: request.arguments.文件 });
  }
  activitySummaryList.replaceChildren(...recent.map((row) => {
    const item = child("li", "");
    const known = Object.hasOwn(runLabels, row.state);
    const mark = child("span", "", "activity-mark");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.classList.add("pane-icon");
    const symbol = document.createElementNS("http://www.w3.org/2000/svg", "use");
    symbol.setAttribute("href", row.state === "waiting_approval" ? "#i-approval" :
      row.state === "succeeded" ? "#i-check" : "#i-activity");
    icon.append(symbol);
    mark.append(icon);
    mark.setAttribute("aria-hidden", "true");
    if (known) mark.dataset.state = row.state;
    const input = row.inputId && current.rows.find((message) => message.id === row.inputId)?.text.trim();
    const label = row.label || (input && input.split("\n", 1)[0].slice(0, 96)) || "会话中的工作";
    const content = child("div", "");
    const state = known ? runLabels[row.state] : "状态待确认";
    content.append(child("strong", label, "activity-label"),
      child("span", row.detail ? state + " · " + row.detail : state, "activity-note"));
    item.append(mark, content);
    const at = client ? formatDate(row.at, timeFormat) : row.at;
    if (at) item.append(child("time", at, "activity-time"));
    return item;
  }));
  element("activity-total").textContent = recent.length + " 项";
  sessionActivity.hidden = !recent.length;
  if (client && follow && current === selection && conversationVisible() && current.loaded) followLatestMessages();
  else syncJumpLatest();
}

function renderSessionState(current) {
  const latest = current.runs.at(-1)?.state;
  const state = element("session-state");
  state.dataset.state = Object.hasOwn(runLabels, latest) ? latest : "";
  state.textContent = Object.hasOwn(runLabels, latest) ? runLabels[latest] :
    current.id && current.id === workspace.mainId ? "主会话已选定" : "会话记录";
}

function renderApprovalSummary(current) {
  const follow = isFollowingMessages();
  const waiting = current.approvals || [];
  const runIds = new Set(current.runs.map((run) => run.id));
  const scoped = client ? waiting.filter((request) => runIds.has(request.runId)) : waiting;
  const badge = element("approval-count");
  badge.hidden = !waiting.length;
  badge.textContent = waiting.length;
  const link = element("session-approval-link");
  link.hidden = !scoped.length;
  link.textContent = scoped.length + " 项待审批";
  link.dataset.state = "waiting_approval";
  element("session-approval").hidden = !scoped.length;
  if (scoped.length) {
    const request = scoped[0];
    const target = request.arguments?.文件 ?? request.arguments?.path;
    element("session-approval-description").textContent = approvalLabel(request) +
      (typeof target === "string" ? " · " + target : "");
  }
  if (current === selection && client && follow && conversationVisible() && current.loaded) {
    followLatestMessages();
  } else {
    syncJumpLatest();
  }
}

async function refreshApprovalSummary(current) {
  const revision = ++current.approvalRevision;
  try {
    const approvals = await readApprovals(current, client);
    if (current !== selection || revision !== current.approvalRevision) return;
    current.approvals = approvals;
    renderApprovalSummary(current);
    if (!client) renderActivitySummary(current);
  } catch {
    if (current === selection && revision === current.approvalRevision) {
      current.approvals = null;
      renderApprovalSummary(current);
    }
  }
}

function renderCoordination(snapshot) {
  const ticket = snapshot?.ticket;
  const label = element("session-ticket");
  label.textContent = ticket?.id ? "Ticket " + ticket.id.slice(0, 8) +
    (Number.isInteger(ticket.version) ? " · 目标 v" + ticket.version : "") : "";
  const workers = element("worker-total");
  const tasks = element("task-total");
  workers.hidden = !snapshot?.workers?.length;
  tasks.hidden = !snapshot?.tasks?.length;
  if (!workers.hidden) workers.textContent = snapshot.workers.length + " 个 Worker";
  if (!tasks.hidden) tasks.textContent = snapshot.tasks.length + " 项任务";
  element("session-work-meta").hidden = workers.hidden && tasks.hidden;
}

async function refreshActivitySummary(current) {
  const revision = ++current.activityRevision;
  const api = client;
  const isExample = current.id === demoWorkspace.mainId;
  const rows = api ? await api.runs(current.id, current.controller.signal) : isExample ? demoWork.activity : [];
  if (current !== selection || revision !== current.activityRevision) return;
  current.runs = rows;
  renderSessionState(current);
  renderApprovalSummary(current);
  renderActivitySummary(current);
  const coordination = api ? await api.coordination(current.id, rows.at(-1), current.controller.signal) :
    isExample ? demoWork : null;
  if (current === selection && revision === current.activityRevision) renderCoordination(coordination);
}

function renderArtifactChoices(current) {
  element("artifact-list").replaceChildren(...current.artifacts.map((artifact) =>
    buttonRow(artifact.name, () => void showArtifact(current, artifact, true, true), current.artifactId === artifact.id)));
  element("artifact-empty").hidden = current.artifacts.length > 0;
}

function artifactKey(current, artifact) {
  return current.id + "\u0000" + artifact.id + "\u0000" + String(artifact.version ?? "");
}

function focusArtifactChoice() {
  element("artifact-list").querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
}

function showArtifact(current, artifact, keepFocus = false, retry = false) {
  if (current !== selection) return Promise.resolve(false);
  const key = artifactKey(current, artifact);
  const version = artifact.version ?? null;
  if (current.artifactId === artifact.id && current.artifactVersion === version &&
      current.content !== null) {
    artifactSelections.set(current.id, artifact.id);
    if (keepFocus) focusArtifactChoice();
    return Promise.resolve(true);
  }
  if (!retry && current.artifactFailure === key) return Promise.resolve(false);
  if (artifactRequest?.current === current && artifactRequest.key === key &&
      !artifactRequest.controller.signal.aborted) {
    if (keepFocus) focusArtifactChoice();
    return artifactRequest.promise;
  }
  artifactRequest?.controller.abort();
  const request = { current, key, controller: new AbortController() };
  artifactRequest = request;
  current.artifactId = artifact.id;
  current.artifactVersion = version;
  current.content = null;
  current.artifactFailure = null;
  artifactSelections.set(current.id, artifact.id);
  renderArtifactChoices(current);
  if (keepFocus) focusArtifactChoice();
  element("artifact-preview").hidden = true;
  element("artifacts-status").textContent = "正在打开产物…";
  request.promise = (async () => {
    try {
      const content = client ? await client.artifact(current.id, artifact.id, request.controller.signal) :
        demoContents[artifact.id];
      if (current !== selection || request !== artifactRequest || request.controller.signal.aborted) return false;
      current.content = content;
      element("artifact-title").textContent = artifact.name;
      element("artifact-meta").textContent = artifact.version ? "版本 " + artifact.version : "";
      element("artifact-meta").hidden = !element("artifact-meta").textContent;
      element("artifact-body").textContent = content.length > 920 ? content.slice(0, 920) + "…" : content;
      element("artifact-preview").hidden = false;
      element("artifacts-status").textContent = "";
      return true;
    } catch {
      if (current === selection && request === artifactRequest && !request.controller.signal.aborted) {
        current.artifactFailure = key;
        element("artifacts-status").textContent = "暂时打不开这份产物。可以再选一次试试。";
      }
      return false;
    } finally {
      if (artifactRequest === request) artifactRequest = undefined;
    }
  })();
  return request.promise;
}

async function loadConversation(current, follow = false) {
  const rows = client ? await client.messages(current.id, current.controller.signal) : demoThreads[current.id] ?? [];
  if (current !== selection) return;
  messages.removeAttribute("aria-busy");
  const firstLoad = !current.loaded;
  const visible = conversationVisible();
  const saved = firstLoad && visible ? positions.get(current.id) : undefined;
  const nearEnd = isFollowingMessages();
  if (!current.rows || rows.length !== current.rows.length ||
      rows.some((row, index) => row.role !== current.rows[index].role || row.text !== current.rows[index].text ||
        row.at !== current.rows[index].at)) {
    const followNow = visible && (saved?.following ?? (Boolean(client) && (follow || (!firstLoad && nearEnd))));
    renderMessages(rows, followNow, saved?.top);
  }
  current.rows = rows;
  current.loaded = true;
  rememberReadingPosition();
  syncJumpLatest();
  void refreshApprovalSummary(current);
  void refreshActivitySummary(current).catch(() => {
    // The conversation remains readable when its supplemental work history is unavailable.
  });
  const latest = new Map();
  for (const row of [...rows].reverse()) {
    for (const report of row.artifacts) {
      if (!latest.has(report.id)) latest.set(report.id, report);
    }
  }
  const reports = [...latest.values()];
  if (reports.length !== current.artifacts.length ||
      reports.some((report, index) => report.id !== current.artifacts[index].id ||
        report.version !== current.artifacts[index].version || report.name !== current.artifacts[index].name)) {
    current.artifacts = reports;
    renderArtifactChoices(current);
    if (page === "files") refreshWork(page);
  }
  element("artifact-empty").hidden = reports.length > 0;
  const preferred = latest.get(current.artifactId) ?? current.artifacts[0];
  if (!preferred) {
    artifactRequest?.controller.abort();
    artifactRequest = undefined;
    current.content = null;
    current.artifactVersion = null;
    current.artifactFailure = null;
    artifactSelections.delete(current.id);
    element("artifact-preview").hidden = true;
    element("artifacts-status").textContent = "";
  } else if (preferred.id !== current.artifactId ||
      (preferred.version ?? null) !== current.artifactVersion || current.content === null) {
    void showArtifact(current, preferred);
  }
}

function setSendState(status) {
  const historical = selection.id !== workspace.mainId || (!selection.id && !workspace.canStart);
  draft.disabled = historical || sending;
  send.disabled = historical || sending || !draft.value.trim();
  element("send-label").textContent = client ? "发送" : "保存草稿";
  element("composer-status").textContent = status ?? (historical ?
    (workspace.mainId ? "你可以在主会话里继续交谈。" : "请先在实例中选定主会话。") :
    client ? "" : "草稿保留在此页");
}

function openSession(session) {
  if (selection) {
    drafts.set(selection.id, draft.value);
    rememberReadingPosition();
    selection.controller.abort();
  }
  artifactRequest?.controller.abort();
  artifactRequest = undefined;
  messageEmpty.textContent = "让人物在数字具身中真实地生活。";
  messages.removeAttribute("aria-busy");
  const requested = selection = {
    id: session?.id || "",
    controller: new AbortController(),
    artifactId: artifactSelections.get(session?.id || "") || "",
    artifactVersion: null,
    artifactFailure: null,
    artifacts: [],
    content: null,
    loaded: false,
    activityRevision: 0,
    approvalRevision: 0,
    runs: [],
    approvals: null,
  };
  element("session-title").textContent = session?.title || "和 " + workspace.name + " 聊聊";
  const context = requested.id === workspace.mainId ? workspace.name + " · 主会话" : "会话记录";
  element("session-context").textContent = client ? context : "演示会话 · " + workspace.name;
  renderSessionState(requested);
  renderApprovalSummary(requested);
  renderCoordination(null);
  draft.value = drafts.get(requested.id) || "";
  element("artifact-preview").hidden = true;
  element("artifacts-status").textContent = client && requested.id ? "正在查看产物…" : "";
  renderArtifactChoices(requested);
  sessionActivity.hidden = true;
  activitySummaryList.replaceChildren();
  renderMessages([], false);
  if (client && requested.id) {
    element("artifact-empty").hidden = true;
    messageEmpty.hidden = true;
    messages.setAttribute("aria-busy", "true");
  }
  renderSessions();
  showPage("conversation");
  setSendState();
  if (openedPane) {
    closePane(false);
    element("workspace").focus();
  }
  void loadConversation(requested, true).catch(() => {
    if (requested === selection) {
      messages.removeAttribute("aria-busy");
      messageEmpty.hidden = false;
      messageEmpty.textContent = "会话暂时无法读取。可以重新选择会话。";
      element("artifacts-status").textContent = "暂时无法查看这段会话的产物。";
    }
  });
}

function openWorkPage(name) {
  showPage(name);
  element("work-title").focus();
}

element("open-tools").addEventListener("click", () => openWorkPage("files"));
element("show-activity").addEventListener("click", () => openWorkPage("activity"));
for (const shortcut of document.querySelectorAll("[data-open-page]")) {
  shortcut.addEventListener("click", () => openWorkPage(shortcut.dataset.openPage));
}

function useWorkspace(api, nextWorkspace) {
  receiptCheck?.abort();
  selection?.controller.abort();
  selection = undefined;
  artifactRequest?.controller.abort();
  artifactRequest = undefined;
  uncertain = undefined;
  sending = false;
  drafts.clear();
  positions.clear();
  artifactSelections.clear();
  client = api;
  workspace = nextWorkspace;
  element("workspace-persona").textContent = workspace.name;
  const connection = element("connection-status");
  connection.textContent = api ? "已连接实例" : "本地演示";
  connection.dataset.state = api ? "connected" : "demo";
  element("disconnect").hidden = !api;
  element("session-search").value = "";
  openSession(workspace.sessions.find((session) => session.id === workspace.mainId) ?? workspace.sessions[0]);
}

async function watchReply(api, receipt, check) {
  let state = "queued";
  while (!terminalRuns.has(state) && !check.signal.aborted) {
    const previous = state;
    state = await api.run(receipt.runId, check.signal);
    if (check.signal.aborted || api !== client) return;
    if (selection.id === receipt.sessionId && previous !== state) {
      setSendState(runLabels[state] || "正在确认处理进度");
      await loadConversation(selection);
    }
    if (terminalRuns.has(state) || !Object.hasOwn(runLabels, state)) return;
    await new Promise((resolve) => setTimeout(resolve, state === "waiting_approval" ? 2000 : 900));
  }
}

const connectionForm = element("connection-form");
const connectButton = connectionForm.querySelector('button[type="submit"]');
const password = element("owner-token");

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = password.value.trim();
  if (connectionAttempt || !token) return;
  const attempt = connectionAttempt = new AbortController();
  connectButton.disabled = true;
  element("connection-error").textContent = "正在连接…";
  const api = new WorkbenchClient(token);
  try {
    const signal = AbortSignal.any([attempt.signal, AbortSignal.timeout(12000)]);
    const metadata = await api.workspace(signal);
    if (attempt.signal.aborted) return;
    useWorkspace(api, metadata);
    settings.close();
  } catch (error) {
    if (!attempt.signal.aborted) {
      element("connection-error").textContent = error instanceof RequestError && error.status === 401 ?
        "凭据未通过验证。请检查后再试。" : "暂时无法连接实例。你仍可查看本地演示。";
    }
  } finally {
    if (connectionAttempt === attempt) {
      connectionAttempt = undefined;
      connectButton.disabled = false;
      password.value = "";
    }
  }
});

settings.addEventListener("close", () => {
  connectionAttempt?.abort();
  connectionAttempt = undefined;
  connectButton.disabled = false;
  password.value = "";
  element("connection-error").textContent = "";
});
element("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (send.disabled) return;
  if (!client) {
    drafts.set(selection.id, draft.value);
    setSendState("草稿已保留在当前体验中。");
    return;
  }
  const text = draft.value.trim();
  if (uncertain && (uncertain.id !== selection.id || uncertain.text !== text)) {
    setSendState("上一条发送仍待确认，请用原内容再试一次。");
    return;
  }
  const api = client;
  const sender = selection;
  const pending = uncertain ?? { id: sender.id, text, key: crypto.randomUUID() };
  sending = true;
  setSendState("正在发送…");
  try {
    const receipt = await api.send(pending.id, pending.text, pending.key, AbortSignal.timeout(12000));
    if (api !== client) return;
    uncertain = undefined;
    sending = false;
    drafts.delete(sender.id);
    if (!workspace.mainId) {
      const created = { id: receipt.sessionId, title: text.slice(0, 56) };
      workspace = { ...workspace, mainId: created.id, sessions: [created, ...workspace.sessions] };
      if (selection === sender) {
        draft.value = "";
        openSession(created);
      }
    } else if (selection === sender) {
      draft.value = "";
      setSendState("消息已送出。");
    }
    receiptCheck?.abort();
    const check = receiptCheck = new AbortController();
    void watchReply(api, receipt, check).catch(() => {
      if (!check.signal.aborted && client === api && selection.id === receipt.sessionId) setSendState("消息已送出，暂时无法读取进度。");
    });
  } catch (error) {
    if (api !== client) return;
    sending = false;
    const rejected = error instanceof RequestError && error.status >= 400 && error.status < 500;
    if (!rejected) uncertain = pending;
    setSendState(rejected ? "这条消息未被接受，草稿已保留。" : "发送结果待确认。可以用原内容再试一次。");
  }
});

for (const action of document.querySelectorAll("[data-page]")) {
  action.addEventListener("click", () => {
    showPage(action.dataset.page);
    if (openedPane) {
      closePane(false);
      element("workspace").focus();
    }
  });
}
for (const name of ["sessions", "artifacts"]) {
  const trigger = element("open-" + name);
  trigger.addEventListener("click", () => openPane(name, trigger));
  element("close-" + name).addEventListener("click", () => {
    if (openedPane) closePane();
    else if (name === "artifacts") {
      document.body.classList.add("artifacts-collapsed");
      syncPaneVisibility();
      trigger.focus();
    }
  });
}
element("session-search").addEventListener("input", () => renderSessions());
element("open-settings").addEventListener("click", (event) => showDialog(settings, event.currentTarget));
element("close-settings").addEventListener("click", () => settings.close());
element("disconnect").addEventListener("click", () => {
  useWorkspace(undefined, demoWorkspace);
  settings.close();
});
function openReport(trigger, artifact) {
  if (selection.content === null) return;
  if (artifact && (selection.artifactId !== artifact.id ||
      selection.artifactVersion !== (artifact.version ?? null))) return;
  element("detail-artifact-title").textContent = element("artifact-title").textContent;
  element("detail-artifact-body").textContent = selection.content;
  showDialog(detail, trigger);
}

element("open-artifact").addEventListener("click", (event) => openReport(event.currentTarget));
element("close-artifact").addEventListener("click", () => detail.close());
draft.addEventListener("input", () => setSendState());
messages.addEventListener("scroll", () => {
  rememberReadingPosition();
  syncJumpLatest();
}, { passive: true });
element("jump-latest").addEventListener("click", jumpToLatest);
window.addEventListener("resize", syncJumpLatest);
document.addEventListener("keydown", (event) => {
  if (!openedPane || document.querySelector("dialog[open]")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closePane();
  }
  if (event.key === "Tab") {
    const pane = element(openedPane + "-pane");
    const choices = [...pane.querySelectorAll("button:not(:disabled), input:not(:disabled)")]
      .filter((control) => !control.closest("[hidden]"));
    const first = choices[0];
    const last = choices.at(-1);
    if (!pane.contains(event.target) || event.target === (event.shiftKey ? first : last)) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    }
  }
});
narrow.addEventListener("change", () => {
  closePane(false);
  syncPaneVisibility();
});
syncPaneVisibility();
bindThemeControls();
useWorkspace(undefined, demoWorkspace);
