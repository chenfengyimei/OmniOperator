// 后台任务分区（原生面板）：把子站同一份控制器挂进设置中心卡片。
import { $, ensureStylesheet } from './dom.js?v=20261103u6';

export async function loadJobsHub() {
  // 动态 import 把「子站脚本加载失败」限制在单个分区内（静态 import 会打挂整个模块图）。
  await ensureStylesheet('/jobs/jobs.css?v=20261103u6');
  const mod = await import('/jobs/jobs.js?v=20261103u6');
  return mod.loadJobs($('#hub-jobs-content'));
}
