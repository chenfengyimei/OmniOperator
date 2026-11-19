// 项目索引分区（原生面板）：把子站的模块/接口/工具/插件浏览器挂进设置中心卡片。
import { $, ensureStylesheet } from './dom.js?v=20261103u6';

export async function loadProjectHub() {
  // 动态 import 把「子站脚本加载失败」限制在单个分区内（静态 import 会打挂整个模块图）。
  await ensureStylesheet('/project/project.css?v=20261103u6');
  const mod = await import('/project/project.js?v=20261103u6');
  return mod.loadProject($('#hub-project-content'));
}
