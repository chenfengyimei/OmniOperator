// 右栏 9 页签：aria 状态同步、进入动效、tab 徽标（只显示真实计数，0 就摘掉徽标）。
import {$} from './dom.js?v=20261031m4';
import {loadDesktop} from './desktop.js?v=20261031m4';
import {loadPublications} from './publications.js?v=20261031m4';

export function setTabBadge(tab,n){
  const b=document.querySelector(`.tabs button[data-tab="${tab}"]`);if(!b)return;
  let tag=b.querySelector('.badge');
  if(!n){if(tag)tag.remove();return}
  if(!tag){tag=document.createElement('span');tag.className='badge';b.append(tag)}
  tag.textContent=n;
}

// 右栏页签：同步 aria 状态，并只对新出现的面板做一次轻量进入动效。
const tabsRoot=document.querySelector('.tabs');
if(tabsRoot)tabsRoot.setAttribute('role','tablist');
document.querySelectorAll('.tabs button').forEach(b=>{
  b.setAttribute('role','tab');b.setAttribute('aria-controls','tab-'+b.dataset.tab);
  b.setAttribute('aria-selected',String(b.classList.contains('active')));
  b.id='tab-btn-'+b.dataset.tab;
});
document.querySelectorAll('.tab-body').forEach(panel=>{
  panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','tab-btn-'+panel.id.replace(/^tab-/,''));
});
export function activateTab(b){
  document.querySelectorAll('.tabs button').forEach(x=>{
    const on=x===b;x.classList.toggle('active',on);x.setAttribute('aria-selected',String(on));
  });
  document.querySelectorAll('.tab-body').forEach(x=>{
    const on=x.id==='tab-'+b.dataset.tab;x.classList.toggle('hidden',!on);
    if(on&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      x.animate([{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:'cubic-bezier(.2,.8,.2,1)'});
    }
  });
  if(b.dataset.tab==='desktop')loadDesktop();
  if(b.dataset.tab==='publications')loadPublications();
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>activateTab(b));
