// 工作台共享可变状态：会话选择、忙闲、并发代数计数、草稿、人设昵称。
// 为什么是单对象 S 而不是导出 let：ES 模块的导入绑定不允许被导入方赋值，
// 跨模块共享的可变状态必须挂在对象属性上（属性读写天然跨模块可见）。
export const S={
  currentSession:null,   // 当前会话 id：loadState/showSession/sendMessage 写，多处读
  busy:false,            // 发送中：sendMessage 置位/复位，syncActiveRun 读取
  selectionVersion:0,    // 会话选择代数：并发切换/迟到响应的守门人（++ 先加后比对）
  messagesVersion:0,     // 消息渲染代数：旧会话的渲染不覆盖新会话
  personaName:'小鱼',     // 人设昵称：loadState 时取自 /api/state（与后端默认一致）
  drafts:new Map(),      // session_id -> 输入框草稿（切换会话保留未发送文字）
};
