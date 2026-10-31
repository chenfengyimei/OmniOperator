// MCP 服务分区（原生面板）：与 hub-mcp.js 的「扩展能力」分区不同——
// 那边是通俗措辞的只读概览，这里承载完整的 MCP 检查器（诊断/日志/重连/目录指纹核对）。
import {$,ensureStylesheet} from './dom.js?v=20261031m16';

export async function loadMcpServicesHub(){
  // 动态 import 把「子站脚本加载失败」限制在单个分区内（静态 import 会打挂整个模块图）。
  await ensureStylesheet('/mcp/mcp.css?v=20261031a');
  const mod=await import('/mcp/mcp.js?v=20261031m16');
  return mod.loadMcpServices($('#hub-mcp-services-content'));
}
