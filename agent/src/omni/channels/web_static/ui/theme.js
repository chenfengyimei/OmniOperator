/* OmniOperator 主题引导：必须在样式生效前给 <html> 打上 data-theme。
 *
 * 为什么是外部文件而不是内联 snippet：
 *   - UI_DESIGN_SYSTEM.md §2 明文「index.html 无内联 JS」，内联脚本与权威规范冲突；
 *   - 内联脚本无法带 ?v= 缓存版本串（规范 §10 要求所有前端资源带版本串）。
 *
 * 为什么放在所有 <link> 之前：
 *   同步阻塞脚本先于首次绘制执行，<html data-theme> 在第一帧就位，浅色用户不会闪一下暗色。
 *   若放到 DOMContentLoaded 再设，浅色用户会看到「暗色闪一下再变浅色」。
 *
 * 主题状态三态：auto（跟随系统）/ light / dark。
 * 实际生效值始终显式写到 <html data-theme>，CSS 只认 [data-theme="light"]——
 * 不写 @media (prefers-color-scheme)，避免「跟随系统」与「手动」两套 CSS 分叉。
 */
(function () {
  'use strict';
  var KEY = 'omni_theme';
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function resolve(pref) {
    if (pref === 'light' || pref === 'dark') return pref;
    return mq && mq.matches ? 'light' : 'dark';
  }
  function read() {
    try {
      var stored = localStorage.getItem(KEY);
      return (stored === 'light' || stored === 'dark' || stored === 'auto') ? stored : 'auto';
    } catch (e) {
      return 'auto';
    }
  }

  var pref = read();
  document.documentElement.setAttribute('data-theme', resolve(pref));

  window.OmniTheme = {
    pref: function () { return pref; },
    effective: function () { return resolve(pref); },
    set: function (next) {
      pref = (next === 'light' || next === 'dark' || next === 'auto') ? next : 'auto';
      try { localStorage.setItem(KEY, pref); } catch (e) { /* 隐私模式下写不进去也不影响本次会话 */ }
      document.documentElement.setAttribute('data-theme', resolve(pref));
      document.dispatchEvent(new CustomEvent('omni:theme-change', {
        detail: { pref: pref, effective: resolve(pref) }
      }));
    },
    /* auto → light → dark → auto */
    cycle: function () {
      this.set(pref === 'auto' ? 'light' : (pref === 'light' ? 'dark' : 'auto'));
    }
  };

  /* 跟随系统：仅在用户没手动指定过时跟随系统偏好变化。
     必须补派发 omni:theme-change——否则终端（xterm）这类按事件重绘主题的
     组件在系统切浅色时不会跟着翻。 */
  if (mq && mq.addEventListener) {
    mq.addEventListener('change', function () {
      if (pref !== 'auto') return;
      document.documentElement.setAttribute('data-theme', resolve('auto'));
      document.dispatchEvent(new CustomEvent('omni:theme-change', {
        detail: { pref: pref, effective: resolve('auto') }
      }));
    });
  }

  /* 同源其它文档改了主题偏好，本页同步跟上。
     为什么必须有：主题切换按钮在工作台，终端页是 iframe 里的独立文档——
     omni:theme-change 是 document 级事件，跨 document 不传播。
     localStorage 的 storage 事件在同源其它文档间广播，正好覆盖这个场景
     （也顺带覆盖「另一个标签页切了主题」）。 */
  window.addEventListener('storage', function (event) {
    if (event.key !== KEY || !event.newValue) return;
    var next = (event.newValue === 'light' || event.newValue === 'dark' || event.newValue === 'auto')
      ? event.newValue : 'auto';
    if (next === pref) return;
    pref = next;
    document.documentElement.setAttribute('data-theme', resolve(pref));
    document.dispatchEvent(new CustomEvent('omni:theme-change', {
      detail: { pref: pref, effective: resolve(pref) }
    }));
  });
})();
