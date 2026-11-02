// 工作台分区、旧地址与工具入口的单一声明。
export const SECTIONS = Object.freeze([
  {
    id: 'sec-context',
    label: '上下文系统',
    group: '核心系统',
    path: null,
  },
  {
    id: 'sec-skills',
    label: '技能系统',
    group: '核心系统',
    path: null,
  },
  {
    id: 'sec-memory-system',
    label: '记忆系统',
    group: '核心系统',
    path: '/memory/',
  },
  {
    id: 'sec-persona',
    label: '人设',
    group: '对话与模型',
    path: null,
  },
  {
    id: 'sec-models',
    label: '模型',
    group: '对话与模型',
    path: null,
  },
  {
    id: 'sec-memory',
    label: '压缩与复盘',
    group: '对话与模型',
    path: null,
  },
  {
    id: 'sec-terminal',
    label: '人工终端',
    group: '工具与设备',
    path: '/terminal/',
  },
  {
    id: 'sec-jobs',
    label: '后台任务',
    group: '工具与设备',
    path: '/jobs/',
  },
  {
    id: 'sec-plugins',
    label: '插件',
    group: '工具与设备',
    path: null,
  },
  {
    id: 'sec-capabilities',
    label: '能力诊断',
    group: '工具与设备',
    path: '/capabilities/',
  },
  {
    id: 'sec-project',
    label: '项目索引',
    group: '工具与设备',
    path: '/project/',
  },
  {
    id: 'sec-embodiment',
    label: '数字具身总览',
    group: '数字具身',
    path: null,
  },
  {
    id: 'sec-media',
    label: '哔哩哔哩',
    group: '数字具身',
    path: null,
  },
  {
    id: 'sec-browser',
    label: '浏览器',
    group: '数字具身',
    path: null,
  },
  {
    id: 'sec-android',
    label: '手机',
    group: '数字具身',
    path: '/android/',
  },
  {
    id: 'sec-qq',
    label: 'QQ',
    group: '数字具身',
    path: null,
  },
  {
    id: 'sec-services',
    label: '服务与运行',
    group: '平台与通道',
    path: null,
  },
  {
    id: 'sec-cron',
    label: '定时任务',
    group: '平台与通道',
    path: null,
  },
  {
    id: 'sec-storage',
    label: 'Agent 与文件',
    group: '文件与用量',
    path: '/storage/',
  },
  {
    id: 'sec-usage',
    label: '花费',
    group: '文件与用量',
    path: null,
  },
  {
    id: 'sec-mcp',
    label: 'MCP 服务',
    group: '文件与用量',
    path: '/mcp/',
  },
  {
    id: 'sec-about',
    label: '关于',
    group: '文件与用量',
    path: null,
  },
]);
export const ALIASES = Object.freeze({ 'sec-mcp-services': 'sec-mcp' });
export function resolveSection(id) {
  const key = ALIASES[id] || id;
  return SECTIONS.find((item) => item.id === key) || null;
}
export function sectionUrl(id) {
  const item = resolveSection(id);
  return item ? '/?settings=1&section=' + encodeURIComponent(item.id) : '/?settings=1';
}
