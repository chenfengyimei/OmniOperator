/* 内嵌页高度上报：iframe 内页把内容高度告诉宿主，宿主据此调 iframe 高度。
   协议：postMessage({omni:'embed-height', id:<页ID>, h:<内容高度 px>}, location.origin)
   降级：宿主收不到消息时用 CSS 固定高度（.embed-shell）。

   为什么不用纯 scrollHeight 轮询：那会给每个 iframe 开一个定时器，
   而且 iframe 高度 ↔ 100dvh 布局互相影响时会震荡。postMessage 是事件驱动的。 */
(function () {
  'use strict';
  var ID = (document.body && document.body.dataset.uiPage) || 'unknown';
  var last = -1;
  function report() {
    var h = Math.max(
      document.documentElement ? document.documentElement.scrollHeight : 0,
      document.body ? document.body.scrollHeight : 0,
    );
    if (!h || h === last) return;
    last = h;
    try {
      parent.postMessage({ omni: 'embed-height', id: ID, h: h }, location.origin);
    } catch (_) {}
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', report, { once: true });
  else report();
  if (window.ResizeObserver) new ResizeObserver(report).observe(document.documentElement);
  window.addEventListener('load', report);
  window.addEventListener('resize', report);
})();
