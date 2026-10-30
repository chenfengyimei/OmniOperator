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

  /* 跟随系统：仅在用户没手动指定过时跟随系统偏好变化 */
  if (mq && mq.addEventListener) {
    mq.addEventListener('change', function () {
      if (pref === 'auto') document.documentElement.setAttribute('data-theme', resolve('auto'));
    });
  }
})();
