// 弹窗开关：Phase 3 起弹窗全部是原生 <dialog>（openModal 兼容旧 .hidden 形态）。
// 关闭钩子给调用方善后用（扫码轮询定时器必须随弹窗关闭清理），避免 modals 反向依赖 services。
import {$} from './dom.js?v=20261030d';

const closeHooks=new Map();
export function registerCloseHook(id,fn){closeHooks.set(id,fn)}

export function openModal(id){
  const el=$('#'+id);
  if(el.tagName==='DIALOG'){if(!el.open)el.showModal()}
  else el.classList.remove('hidden');
}
export function closeModal(id){
  const el=$('#'+id);
  if(el.tagName==='DIALOG')el.close();else el.classList.add('hidden');
  const hook=closeHooks.get(id);if(hook)hook();
}
window.closeModal=closeModal;window.openModal=openModal;
