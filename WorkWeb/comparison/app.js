const panels = [...document.querySelectorAll("[data-proto]")];
const tabs = [...document.querySelectorAll("[data-proto-tab]")];
const settings = document.querySelector("#preview-settings");
const confirmation = document.querySelector("#preview-confirm");
const drawerViewport = matchMedia("(max-width: 900px)");
const drafts = new Map();
const views = Object.freeze({ session: "主会话", workers: "Worker 中心", tasks: "任务中心", approvals: "审批中心", tools: "工具与文件" });
const draftInput = "[data-draft], [data-draft-input]";
let activePanel;
let openFrom;
let confirmAction;
let approval = "pending";
const cancelledRuns = new Set();

function selectDirection(direction, updateUrl = true) {
  activePanel = panels.find((panel) => panel.dataset.proto === direction) ?? panels[0];
  for (const panel of panels) panel.hidden = panel !== activePanel;
  for (const tab of tabs) {
    const current = tab.dataset.protoTab === activePanel.dataset.proto;
    tab.setAttribute("aria-selected", String(current));
    tab.tabIndex = current ? 0 : -1;
  }
  if (updateUrl) {
    const url = new URL(location.href);
    url.searchParams.set("direction", activePanel.dataset.proto);
    history.replaceState(null, "", url);
  }
}

function setTheme(theme) {
  activePanel.dataset.theme = theme;
  for (const toggle of activePanel.querySelectorAll("[data-theme-toggle]")) {
    toggle.setAttribute("aria-label", theme === "dark" ? "切换为浅色" : "切换为深色");
    const text = toggle.querySelector("[data-theme-label], [class$='theme-label']");
    if (text) text.textContent = theme === "dark" ? "浅色" : "深色";
  }
  for (const control of settings.querySelectorAll("[data-set-theme]")) {
    control.setAttribute("aria-pressed", String(control.dataset.setTheme === theme));
  }
  if (settings.open) settings.dataset.theme = theme;
}

function showView(panel, name, moveFocus = false) {
  if (!Object.hasOwn(views, name)) return;
  for (const screen of panel.querySelectorAll("[data-screen], [data-view]")) {
    screen.hidden = (screen.dataset.screen ?? screen.dataset.view) !== name;
  }
  for (const control of panel.querySelectorAll("[data-view-target]")) {
    const current = control.dataset.viewTarget === name;
    control.classList.toggle("is-active", current);
    if (current) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  }
  const title = panel.querySelector("[data-view-name]");
  if (title) title.textContent = views[name];
  if (moveFocus) panel.querySelector("main")?.focus({ preventScroll: true });
}

function openSettings(trigger) {
  openFrom = trigger;
  settings.dataset.scheme = activePanel.dataset.proto;
  settings.dataset.theme = activePanel.dataset.theme;
  const search = settings.querySelector("input[type=search]");
  search.value = "";
  filterSettings();
  setTheme(activePanel.dataset.theme);
  settings.showModal();
  search.focus();
}

function filterSettings() {
  const query = settings.querySelector("input[type=search]").value.trim().toLocaleLowerCase();
  let visible = false;
  for (const group of settings.querySelectorAll(".settings-group")) {
    group.hidden = !group.textContent.toLocaleLowerCase().includes(query);
    visible ||= !group.hidden;
  }
  settings.querySelector("[data-settings-empty]").hidden = visible;
}

function openConfirmation(trigger, title, description, action) {
  openFrom = trigger;
  confirmAction = action;
  confirmation.dataset.scheme = activePanel.dataset.proto;
  confirmation.dataset.theme = activePanel.dataset.theme;
  confirmation.querySelector("h2").textContent = title;
  confirmation.querySelector("[data-confirm-description]").textContent = description;
  confirmation.showModal();
  confirmation.querySelector("[data-close-dialog]").focus();
}

function closeNavigation(panel, open, restoreFocus = true) {
  panel.classList.toggle("nav-open", open);
  const trigger = panel.querySelector("[data-b-nav-toggle]");
  if (!trigger) return;
  trigger.setAttribute("aria-expanded", String(open));
  const overlay = panel.querySelector(".b-nav-scrim");
  if (overlay) overlay.hidden = !open;
  const nav = panel.querySelector(".b-nav");
  nav.hidden = drawerViewport.matches && !open;
  const main = panel.querySelector("main");
  main.inert = open;
  if (open) {
    nav.querySelector(".b-nav-close").focus({ preventScroll: true });
  } else if (restoreFocus) trigger.focus();
}

function changeInspector(panel, open) {
  const inspector = panel.querySelector("[data-inspector]");
  if (!inspector) return;
  inspector.hidden = !open;
  panel.classList.toggle("inspector-closed", !open);
  const trigger = panel.querySelector("[data-toggle-inspector]");
  trigger?.setAttribute("aria-expanded", String(open));
  if (open) inspector.querySelector("button")?.focus({ preventScroll: true });
  else trigger?.focus({ preventScroll: true });
}

function selectInspector(panel, name) {
  for (const tab of panel.querySelectorAll("[data-inspector-tab]")) {
    const selected = tab.dataset.inspectorTab === name;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  for (const region of panel.querySelectorAll("[data-inspector-panel]")) {
    region.hidden = region.dataset.inspectorPanel !== name;
  }
}

function selectFile(panel, name) {
  for (const item of panel.querySelectorAll("[data-file-open], button[data-file-target]:not([data-view-link])")) {
    const current = (item.dataset.fileOpen ?? item.dataset.fileTarget) === name;
    item.classList.toggle("is-active", current);
    if (item.getAttribute("role") === "tab") {
      item.setAttribute("aria-selected", String(current));
      item.tabIndex = current ? 0 : -1;
    }
    else item.setAttribute("aria-pressed", String(current));
  }
  for (const preview of panel.querySelectorAll("[data-file-panel]")) {
    const current = preview.dataset.filePanel === name;
    preview.hidden = !current;
    preview.classList.toggle("is-active", current);
  }
  const button = [...panel.querySelectorAll("[data-file-open]")].find((item) => item.dataset.fileOpen === name);
  const title = panel.querySelector("[data-file-title]");
  if (button && title) {
    title.textContent = button.querySelector("strong")?.textContent.trim() ?? button.textContent.trim();
  }
}

function filterRows(input, list, items, empty) {
  const value = input.value.trim().toLocaleLowerCase();
  const root = input.closest("dialog") ?? input.closest("[data-proto]");
  if (!root) return;
  for (const group of root.querySelectorAll(list)) {
    let matches = 0;
    for (const row of group.querySelectorAll(items)) {
      row.hidden = !row.textContent.toLocaleLowerCase().includes(value);
      matches += Number(!row.hidden);
    }
    const message = group.querySelector(empty) ?? group.parentElement.querySelector(empty);
    if (message) message.hidden = matches > 0;
  }
}

function draftKey(panel) {
  return panel.dataset.sessionKey ?? "open-day";
}

function restoreDraft(panel) {
  const field = panel.querySelector(draftInput);
  if (!field) return;
  field.value = drafts.get(draftKey(panel)) ?? "";
  const count = panel.querySelector("[data-char-count]");
  if (count) count.textContent = String(field.value.length);
}

function selectSession(panel, control) {
  const key = control.dataset.sessionKey;
  panel.dataset.sessionKey = key;
  const archive = key !== "open-day";
  const heading = panel.querySelector("#a-session-title");
  if (heading) heading.textContent = archive ? control.querySelector("strong").textContent : "整理校园开放日资料";
  const kicker = panel.querySelector(".a-thread-kicker");
  if (kicker) kicker.hidden = archive;
  const introduction = panel.querySelector(".a-thread-head > p");
  if (introduction) introduction.textContent = archive ? "本地会话样例。暂无可查看的关联运行。" :
    "由主 Agent 控制 4 个 Worker。下方依赖、状态和文件均为演示样例。";
  const inspector = panel.querySelector("[data-inspector]");
  const inspectorToggle = panel.querySelector("[data-toggle-inspector]");
  if (inspector && inspectorToggle) {
    inspector.hidden = archive;
    inspectorToggle.disabled = archive;
    inspectorToggle.setAttribute("aria-expanded", String(!archive));
    panel.classList.toggle("inspector-closed", archive);
  }
  for (const item of panel.querySelectorAll(".js-session-item")) {
    const selected = item.dataset.sessionKey === key;
    item.classList.toggle("is-selected", selected);
    if (selected) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");
  }
  const log = panel.querySelector("[role=log]");
  if (log) {
    const children = [...log.children].filter((element) => !element.matches("[data-session-preview]"));
    let preview = log.querySelector("[data-session-preview]");
    if (!preview) {
      preview = document.createElement("p");
      preview.dataset.sessionPreview = "";
      log.append(preview);
    }
    for (const child of children) {
      if (key !== "open-day" && !child.hidden) {
        child.hidden = true;
        child.dataset.hiddenForSession = "";
      } else if (key === "open-day" && child.hasAttribute("data-hidden-for-session")) {
        child.hidden = false;
        delete child.dataset.hiddenForSession;
      }
    }
    preview.hidden = key === "open-day";
    preview.textContent = (control.querySelector("strong")?.textContent.trim() ?? "") + " · 暂无可展开的演示记录";
  }
  control.closest("dialog")?.close();
  restoreDraft(panel);
  showView(panel, "session");
}

function finishApproval(decision) {
  if (approval !== "pending") return;
  approval = decision;
  const message = decision === "allowed" ? "演示审批已批准一次" : "演示审批已拒绝";
  for (const control of document.querySelectorAll("[data-approve], [data-deny-approval], [data-action=allow], [data-action=deny]")) {
    control.disabled = true;
  }
  const allowed = decision === "allowed";
  for (const shortcut of document.querySelectorAll("[data-awaiting-approval]")) {
    shortcut.textContent = "查看审批记录";
    shortcut.dataset.status = "neutral";
  }
  for (const count of document.querySelectorAll("[data-approval-count]")) {
    count.textContent = count.dataset.approvalCount;
    count.removeAttribute("aria-label");
    if (!count.textContent) count.hidden = true;
  }
  for (const badge of document.querySelectorAll(".status-pill[data-status=approval], .a-mini-status[data-tone=approval]")) {
    if (badge.closest(".b-legend") || badge.hasAttribute("data-awaiting-approval")) continue;
    const request = badge.closest("[data-approval-state], [data-screen=approvals], [data-view=approvals]");
    const status = allowed && request ? "succeeded" : allowed ? "queued" : "cancelled";
    badge.dataset.status = status;
    if (badge.classList.contains("a-mini-status")) badge.dataset.tone = allowed ? "waiting" : "quiet";
    badge.textContent = allowed ? (request ? "已批准（演示）" : "待执行回执") : "已拒绝（演示）";
  }
  for (const detail of document.querySelectorAll("[data-approval-detail]")) {
    detail.textContent = allowed ? "演示审批已批准，等待执行回执。" : "演示审批已拒绝。";
  }
  for (const card of document.querySelectorAll("[data-approval-state]")) {
    card.dataset.approvalState = decision;
    const outcome = document.createElement("p");
    outcome.className = "demo-receipt";
    outcome.setAttribute("role", "status");
    outcome.textContent = message;
    card.append(outcome);
  }
}

function cancelRun(key, label) {
  if (cancelledRuns.has(key)) return;
  cancelledRuns.add(key);
  const selector = key === "exhibit" ? "[data-cancel-run=exhibit]" : ".proto-c [data-action=stop]";
  const affectedPanels = new Set();
  for (const control of document.querySelectorAll(selector)) {
    affectedPanels.add(control.closest("[data-proto]"));
    control.disabled = true;
    control.setAttribute("aria-label", "已取消演示运行");
    if (key !== "exhibit") {
      const status = control.parentElement.querySelector("[data-status=running]");
      if (status) {
        status.dataset.status = "cancelled";
        status.textContent = "已取消（演示）";
      }
    }
  }
  if (key === "exhibit") {
    for (const item of document.querySelectorAll("[data-worker=exhibit], [data-task=exhibit]")) {
      const status = item.querySelector("[data-status=running]");
      if (status) {
        status.dataset.status = "cancelled";
        status.textContent = "已取消（演示）";
      }
    }
    const activeCount = document.querySelector("[data-running-count]");
    if (activeCount) activeCount.textContent = "00";
  }
  for (const panel of affectedPanels) {
    const note = panel.querySelector("[data-composer-note]");
    if (note) note.textContent = label + "演示运行已取消，草稿和已有记录仍可查看。";
  }
}
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("close", () => {
    if (openFrom?.isConnected && !openFrom.disabled) openFrom.focus({ preventScroll: true });
    else activePanel.querySelector("main")?.focus({ preventScroll: true });
    openFrom = null;
  });
}

document.addEventListener("input", (event) => {
  const input = event.target;
  if (input.matches("#settings-search")) filterSettings();
  if (input.matches("[data-session-search]")) filterRows(input, "[data-session-list]", ".js-session-item", "[data-session-empty]");
  if (input.matches("[data-file-search]")) filterRows(input, "[data-file-list]", "[data-file-open]", "[data-file-empty]");
  if (!input.matches(draftInput)) return;
  const panel = input.closest("[data-proto]");
  drafts.set(draftKey(panel), input.value);
  const count = panel.querySelector("[data-char-count]");
  if (count) count.textContent = String(input.value.length);
});

document.addEventListener("submit", (event) => {
  if (!event.target.matches("[data-composer]")) return;
  event.preventDefault();
  const field = event.target.querySelector(draftInput);
  if (!field?.value.trim()) {
    field?.focus();
    return;
  }
  const panel = field.closest("[data-proto]");
  drafts.set(draftKey(panel), field.value);
  const message = panel.querySelector("[data-composer-note]");
  if (message) message.textContent = "草稿已保存在本次预览中。";
});

document.addEventListener("click", (event) => {
  if (event.target.closest(".b-nav-scrim")) {
    closeNavigation(activePanel, false);
    return;
  }
  const button = event.target.closest("button");
  if (!button) return;
  const data = button.dataset;
  const panel = button.closest("[data-proto]") ?? activePanel;

  if (data.protoTab) {
    selectDirection(data.protoTab);
    restoreDraft(activePanel);
  }
  if ("themeToggle" in data) setTheme(panel.dataset.theme === "dark" ? "light" : "dark");
  if ("openSettings" in data || "settingsOpen" in data) openSettings(button);
  if (data.setTheme) setTheme(data.setTheme);
  if ("closeDialog" in data) button.closest("dialog").close();
  if ("applyConfirmation" in data) {
    confirmation.close();
    confirmAction?.();
  }
  if (data.viewTarget || data.viewLink) {
    if (panel.classList.contains("nav-open")) closeNavigation(panel, false, false);
    showView(panel, data.viewTarget ?? data.viewLink, true);
  }
  if (data.fileOpen || data.fileTarget) selectFile(panel, data.fileOpen ?? data.fileTarget);
  if (data.disclosure) {
    const region = document.getElementById(data.disclosure);
    region.hidden = !region.hidden;
    button.setAttribute("aria-expanded", String(!region.hidden));
  }
  if ("toggleInspector" in data) changeInspector(panel, panel.querySelector("[data-inspector]").hidden);
  if ("closeInspector" in data) changeInspector(panel, false);
  if (data.inspectorTab) selectInspector(panel, data.inspectorTab);
  if ("openSessionModal" in data) {
    openFrom = button;
    const dialog = panel.querySelector(".a-session-modal");
    dialog.showModal();
    dialog.querySelector("input[type=search]")?.focus();
  }
  if (button.matches(".js-session-item")) selectSession(panel, button);
  if ("bNavToggle" in data) closeNavigation(panel, !panel.classList.contains("nav-open"));
  if ("bNavClose" in data) closeNavigation(panel, false);
  if (data.cancelRun || data.action === "stop") {
    const label = data.runLabel ?? "院系展位提取";
    openConfirmation(button, "取消演示运行", "将停止「" + label + "」的演示运行。已完成工作和草稿仍可查看。", () =>
      cancelRun(data.cancelRun ?? "time-check", label)
    );
  }
  if ("approve" in data || data.action === "allow") finishApproval("allowed");
  if ("denyApproval" in data || data.action === "deny") finishApproval("denied");
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !document.querySelector("dialog[open]")) {
    const trigger = activePanel.querySelector("[data-open-settings], [data-settings-open]");
    if (trigger) {
      event.preventDefault();
      openSettings(trigger);
      return;
    }
  }
  if (event.key === "Tab" && activePanel.classList.contains("nav-open") && !document.querySelector("dialog[open]")) {
    const nav = activePanel.querySelector(".b-nav");
    const controls = [...nav.querySelectorAll("button:not([disabled])")];
    const first = controls[0];
    const last = controls.at(-1);
    if (!nav.contains(event.target) || event.target === (event.shiftKey ? first : last)) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    }
    return;
  }
  const innerTab = event.target.closest("[data-inspector-tab], [role=tab][data-file-target]");
  if (innerTab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    const group = [...innerTab.closest("[role=tablist]").querySelectorAll("[role=tab]")];
    const index = group.indexOf(innerTab);
    const next = { ArrowLeft: (index + group.length - 1) % group.length,
      ArrowRight: (index + 1) % group.length, Home: 0, End: group.length - 1 }[event.key];
    event.preventDefault();
    group[next].click();
    group[next].focus();
    return;
  }
  const tab = event.target.closest("[data-proto-tab]");
  if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const current = tabs.indexOf(tab);
    const next = { ArrowLeft: (current + tabs.length - 1) % tabs.length,
      ArrowRight: (current + 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
    tabs[next].click();
    tabs[next].focus();
  }
  if (event.key === "Escape" && !document.querySelector("dialog[open]") && activePanel.classList.contains("nav-open")) {
    closeNavigation(activePanel, false);
  }
});

for (const tab of document.querySelectorAll("[data-inspector-tab], [role=tab][data-file-target]")) {
  tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
}
const commandPanel = document.querySelector("#proto-b");
const resetDrawer = () => closeNavigation(commandPanel, false, false);
drawerViewport.addEventListener("change", resetDrawer);
resetDrawer();
const options = new URLSearchParams(location.search);
selectDirection(options.get("direction"), false);
const theme = options.get("theme");
setTheme(["dark", "light"].includes(theme) ? theme : activePanel.dataset.theme);
restoreDraft(activePanel);
for (const field of document.querySelectorAll(draftInput)) field.maxLength = 2000;
