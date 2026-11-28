/* WorkWeb 主题引导：必须在样式生效前给 <html> 打上 data-theme。
 *
 * 三态：auto（跟随系统）/ light / dark。实际生效值始终显式写到
 * <html data-theme>，CSS 只认 light / dark，避免跟随系统与手动两套样式分叉。
 *
 * 首次偏好：?theme=light|dark|auto 仅用于预览；否则读取 WorkWeb 自己的
 * localStorage，再退回 auto。显式切换才保存偏好，并同步已有的 theme 查询参数。
 */
(function () {
  "use strict";
  var KEY = "workweb_theme";
  var choices = ["light", "dark", "auto"];
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function normalize(value) {
    return choices.indexOf(value) === -1 ? null : value;
  }

  function resolve(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return mq && mq.matches ? "dark" : "light";
  }

  function saved() {
    try {
      return normalize(localStorage.getItem(KEY)) || "auto";
    } catch (error) {
      return "auto";
    }
  }

  function initial() {
    var preview = null;
    try {
      preview = normalize(new URLSearchParams(window.location.search).get("theme"));
    } catch (error) {
      preview = null;
    }
    return preview || saved();
  }

  var pref = initial();
  document.documentElement.setAttribute("data-theme", resolve(pref));

  function apply() {
    document.documentElement.setAttribute("data-theme", resolve(pref));
  }

  function notify() {
    document.dispatchEvent(
      new CustomEvent("workweb:theme-change", {
        detail: { pref: pref, effective: resolve(pref) },
      }),
    );
  }

  function save(value) {
    try {
      localStorage.setItem(KEY, value);
    } catch (error) {
      /* 隐私模式下写不进去也不影响本次会话 */
    }
  }

  function syncQuery(value) {
    try {
      var url = new URL(window.location.href);
      if (!url.searchParams.has("theme") || url.searchParams.get("theme") === value) return;
      url.searchParams.set("theme", value);
      window.history.replaceState(window.history.state, "", url);
    } catch (error) {
      /* 地址栏不可写时保留当前查询参数 */
    }
  }

  window.WorkWebTheme = {
    pref: function () {
      return pref;
    },
    set: function (next) {
      pref = normalize(next) || "auto";
      save(pref);
      apply();
      syncQuery(pref);
      notify();
    },
  };

  if (mq) {
    var onSystemChange = function () {
      if (pref !== "auto") return;
      apply();
      notify();
    };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);
  }

  window.addEventListener("storage", function (event) {
    if (event.key !== KEY) return;
    var next = normalize(event.newValue);
    if (!next || next === pref) return;
    pref = next;
    apply();
    notify();
  });
})();
