/* 轻量 Markdown 渲染（零依赖，工作台专用子集）。
   安全模型：先把整段文本整体转义，再在转义后的文本上做结构/行内转换——
   模型输出里的任何 HTML 都不可能变成标签；链接只放行 http(s):// 与站内 / 路径。
   支持：代码块、标题、引用、有序/无序列表、表格、分隔线、加粗/斜体/删除线/行内代码/链接。 */
(() => {
  'use strict';
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = s => String(s).replace(/[&<>"']/g, c => ESC[c]);
  const safeUrl = u => (/^(https?:\/\/|\/)/i.test(u) ? u : '#');

  // 行内：行内代码先抽成占位，避免 ** 与 `` 互相干扰
  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (m, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g,
      (m, text, href) => '<a href="' + safeUrl(href) + '" target="_blank" rel="noopener">' + text + '</a>');
    s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => '<code>' + codes[+i] + '</code>');
    return s;
  }

  const isQuote = l => /^\s*&gt;\s?/.test(l);           // 原文的 "> " 转义后是 "&gt; "
  const isItem = l => /^\s*([-*+]|\d+\.)\s+/.test(l);
  const isOrdered = l => /^\s*\d+\.\s+/.test(l);

  function render(raw) {
    const lines = esc(String(raw == null ? '' : raw)).replace(/\r\n?/g, '\n').split('\n');
    let html = '';
    let i = 0;
    let para = [];

    const flush = () => {
      if (!para.length) return;
      html += '<p>' + inline(para.join('<br>')) + '</p>';
      para = [];
    };

    while (i < lines.length) {
      const line = lines[i];

      // 围栏代码块
      if (/^\s*```/.test(line)) {
        flush();
        const buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // 跳过收尾 ```
        html += '<pre><code>' + buf.join('\n') + '</code></pre>';
        continue;
      }

      // 表格：表头行 + 分隔行（至少两列且分隔行含 -）
      if ((line.match(/\|/g) || []).length >= 2 && i + 1 < lines.length &&
          /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        flush();
        const cells = r => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
        const head = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') { rows.push(cells(lines[i])); i++; }
        html += '<div class="md-table"><table><thead><tr>' +
          head.map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>' +
          rows.map(r => '<tr>' + r.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') +
          '</tbody></table></div>';
        continue;
      }

      // 标题：舱室内只到 h3，更大层级封顶 h6
      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) {
        flush();
        const n = Math.min(h[1].length + 2, 6);
        html += '<h' + n + '>' + inline(h[2]) + '</h' + n + '>';
        i++;
        continue;
      }

      // 分隔线
      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); html += '<hr>'; i++; continue; }

      // 引用
      if (isQuote(line)) {
        flush();
        const buf = [];
        while (i < lines.length && isQuote(lines[i])) { buf.push(lines[i].replace(/^\s*&gt;\s?/, '')); i++; }
        html += '<blockquote>' + buf.map(inline).join('<br>') + '</blockquote>';
        continue;
      }

      // 列表
      if (isItem(line)) {
        flush();
        const ordered = isOrdered(line);
        const items = [];
        while (i < lines.length && isItem(lines[i])) {
          items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''));
          i++;
        }
        const tag = ordered ? 'ol' : 'ul';
        html += '<' + tag + '>' + items.map(t => '<li>' + inline(t) + '</li>').join('') + '</' + tag + '>';
        continue;
      }

      if (line.trim() === '') { flush(); i++; continue; }

      para.push(line);
      i++;
    }
    flush();
    return html;
  }

  window.OmniMarkdown = { render, inline };
})();
