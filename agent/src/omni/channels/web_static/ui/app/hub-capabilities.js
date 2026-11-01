// 能力诊断分区（原生面板）：把子站同一份控制器挂进设置中心卡片。
import {$,ensureStylesheet} from './dom.js?v=20261031m19';

export async function loadCapabilitiesHub(){
  // 页面模块用动态 import：子站脚本加载失败（离线、路径缺失、版本串写错）时
  // 只让这一个分区报错，不能连带把工作台的 ES 模块图整体打挂。
  await ensureStylesheet('/capabilities/capabilities.css?v=20261031a');
  const mod=await import('/capabilities/capabilities.js?v=20261031m19');
  return mod.loadCapabilities($('#hub-capabilities-content'));
}
