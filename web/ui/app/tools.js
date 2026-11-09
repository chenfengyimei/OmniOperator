// 工具弹窗只导航，不创建任务或启动设备。
import { $, esc } from './dom.js?v=20261103u5';
import { SECTIONS } from '../sections.js?v=20261103u5';
import { openHub } from './hub.js?v=20261103u5';

function openTools() {
  let box = $('#tools-panel');
  if (!box) {
    box = document.createElement('dialog');
    box.id = 'tools-panel';
    box.className = 'modal';
    document.body.append(box);
  }
  box.innerHTML =
    '<div class="modal-box"><div class="modal-title">工具与设置<button class="modal-x" data-action="tools:close" aria-label="关闭">×</button></div><div class="tools-grid">' +
    SECTIONS.map(
      (item) =>
        '<button class="btn" data-action="tools:select" data-section="' +
        esc(item.id) +
        '">' +
        esc(item.label) +
        '</button>',
    ).join('') +
    '</div></div>';
  if (!box.open) box.showModal();
}
export const actions = {
  'tools:open': openTools,
  'tools:close': () => $('#tools-panel')?.close(),
  'tools:select': (el) => {
    $('#tools-panel').close();
    openHub(el.dataset.section);
  },
};
