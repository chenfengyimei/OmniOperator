// rail 图标提示（data-tip → #tip-layer 顶层浮层）：hover 显示、移出隐藏、屏幕边缘自动翻转。
import { $ } from './dom.js?v=20261103u6';

(function () {
  const layer = $('#tip-layer');
  const show = (el) => {
    const text = el.getAttribute('data-tip');
    if (!text) return;
    layer.textContent = text;
    layer.classList.add('on');
    const r = el.getBoundingClientRect();
    let x = r.left + r.width / 2 - layer.offsetWidth / 2;
    x = Math.max(6, Math.min(x, innerWidth - layer.offsetWidth - 6));
    let y = r.bottom + 7;
    if (y + layer.offsetHeight > innerHeight - 6) y = r.top - layer.offsetHeight - 7;
    layer.style.transform = `translate(${x}px,${y}px)`;
  };
  const hide = () => layer.classList.remove('on');
  document.addEventListener('mouseover', (e) => {
    const el = e.target.closest('[data-tip]');
    el ? show(el) : hide();
  });
  document.addEventListener('mouseout', (e) => {
    if (e.target.closest('[data-tip]')) hide();
  });
})();
