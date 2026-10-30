// DOM 与格式化小工具：模块化后子站也可以 import 复用（Phase 5）。
export const $=s=>document.querySelector(s);
export const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 消息卡片的头与时间：时间戳来自 messages.created_at（本地时区 ISO），解析失败就不显示
export const fmtTime=s=>{if(!s)return '';const d=new Date(s);return Number.isNaN(d.getTime())?'':d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})};
// 会话列表的相对时间（数据来自 sessions.created_at，与原型 meta 行同款式）
export const relTime=iso=>{
  if(!iso)return '';
  const d=new Date(iso);if(Number.isNaN(d.getTime()))return '';
  const s=(Date.now()-d.getTime())/1000;
  if(s<60)return '刚刚';
  if(s<3600)return Math.floor(s/60)+' 分钟前';
  if(s<86400)return Math.floor(s/3600)+' 小时前';
  if(s<172800)return '昨天';
  return Math.floor(s/86400)+' 天前';
};
// 模型用量金额（D10 账本单位是微美元，1e6 = $1）
export const usd=m=>m==null?'—':(m>=1e6?`$${(m/1e6).toFixed(2)}`:`$${(m/1e6).toFixed(4)}`);
// 能力档位的中文名（D28）：auto=自动判定，chat=聊天，retrieval=检索，agent=完整
export const modeLabel=m=>({auto:'自动',chat:'聊天',retrieval:'检索',agent:'完整'}[m]||m||'');
