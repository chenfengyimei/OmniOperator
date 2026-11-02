/* 终端主题桥：把站点 token 翻译成 xterm theme，让终端跟随深浅色与强调色。
   token 单源在 ui/tokens.css；这里只读不算，不定义任何颜色。

   注意：getComputedStyle().getPropertyValue('--x') 返回的是**未解析的 token 文本**
   （例如 color-mix(in oklab, ...)），不是颜色值。必须经一个真实元素中转，
   才能拿到浏览器解析后的最终色值。 */
(function () {
  'use strict';
  function resolve(varName) {
    var probe = document.createElement('i');
    probe.style.cssText = 'position:absolute;width:0;height:0;visibility:hidden';
    probe.style.color = 'var(' + varName + ')';
    document.body.appendChild(probe);
    var value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }
  function buildTheme() {
    var surface = resolve('--surface-inset');
    var text = resolve('--text-1');
    var text3 = resolve('--text-3');
    var accent = resolve('--seed-accent');
    var accent2 = resolve('--seed-accent2');
    var danger = resolve('--danger');
    var ok = resolve('--ok');
    var warn = resolve('--warn');
    return {
      background: surface,
      foreground: text,
      cursor: accent,
      cursorAccent: surface,
      selectionBackground: accent,
      selectionForeground: text,
      black: surface,
      red: danger,
      green: ok,
      yellow: warn,
      blue: accent,
      magenta: accent2,
      cyan: accent,
      white: text,
      brightBlack: text3,
      brightRed: danger,
      brightGreen: ok,
      brightYellow: warn,
      brightBlue: accent,
      brightMagenta: accent2,
      brightCyan: accent,
      brightWhite: text,
    };
  }
  window.OmniTerminalTheme = {
    theme: buildTheme,
    // 订阅主题变化：xterm 没有公开的 setTheme，整体替换 options.theme 即可刷新
    watch: function (term) {
      var apply = function () {
        term.options.theme = buildTheme();
      };
      apply();
      document.addEventListener('omni:theme-change', apply);
      return apply;
    },
  };
})();
