const dailySession = "daily-planning";
const reports = [
  { id: "tomorrow", name: "明天的行动清单", version: null },
  { id: "decisions", name: "待确认事项", version: null },
];

export const demoWorkspace = {
  name: "小鱼",
  sessions: [
    { id: dailySession, title: "整理明天的待办", at: "21:14", note: "晚间回顾与行动清单", meta: "主会话 · 2 个 Worker" },
    { id: "weekend", title: "周末的出行计划", note: "出行安排与路线", meta: "暂无消息" },
    { id: "work-habits", title: "我的工作习惯", note: "上午专注，午休再看待办", meta: "日常约定 · 2 条消息" },
    { id: "local-files", title: "整理本机的资料", note: "票据与手稿分类", meta: "会话记录 · 2 条消息" },
  ],
  mainId: dailySession,
  canStart: false,
};

const dailyMessages = [
  { role: "user", at: "21:12", text: "以后晚上帮我回顾一下今天，把明天先要做的事理出来。需要移文件或发消息时先问我。", artifacts: [] },
  {
    role: "assistant",
    at: "21:14",
    text: "我按晚间回顾的约定整理了明天的行动清单。会议时间有一处还待确认，归档资料和发送邮件也单独列出来了。你可以在「产物」里阅读。",
    artifacts: reports,
  },
];

export const demoThreads = {
  [dailySession]: dailyMessages,
  "work-habits": [
    { role: "user", text: "如果我上午在写东西，先帮我记下零散的待办，等午休再一起看。", artifacts: [] },
    { role: "assistant", text: "好。上午先记下来，午休时一起看。碰到有明确截止时间的事情，我会单独提醒你。", artifacts: [] },
  ],
  "local-files": [
    { role: "user", text: "下载目录里有些票据和手稿，你先看看怎么分比较顺手。", artifacts: [] },
    { role: "assistant", text: "票据可以按月份分，手稿可以按项目放。我先给你整理出分类思路，等你看过再移动原件。", artifacts: [] },
  ],
};

export const demoContents = {
  tomorrow: "上午先核对明天的会议时间，再处理需要回复的两封邮件。下午留一段时间整理最近下载的资料。\n\n会议开始时间仍待确认。资料原件留在原处，移动文件和发送邮件前会先请你决定。",
  decisions: "确认会议开始时间。确定哪些资料可以放进「资料归档」文件夹。\n\n邮件草稿已列入待办，发送前等你确认。",
};

export const demoWork = {
  ticket: { id: "演示", version: 1 },
  activity: [
    { label: "晚间回顾与待办整理", state: "succeeded", at: "21:12", detail: "按日程、邮件和资料归纳明天要做的事。" },
    { label: "行动清单与待确认事项已汇总", state: "succeeded", at: "21:14", detail: "两份产物可以直接阅读。" },
  ],
  workers: [
    {
      kind: "delegation",
      id: "files-review",
      parentId: "演示",
      label: "资料梳理",
      state: "succeeded",
      detail: "已按需要回复、需要归档和待核对整理。",
      profile: "资料整理",
      profileVersion: 2,
      goalVersion: 1,
      reusedFromVersion: 1,
      summary: "按需要回复、需要归档和待核对整理今晚下载的资料；原件留在原处。",
      summaryTruncated: false,
      summaryRedacted: false,
      errorCode: null,
      unknownEffect: false,
      unknownModelCalls: 0,
      demo: true,
      snapshot: {
        demo: true,
        messages: [
          { role: "user", at: "21:11", text: "先看看今天下载的资料，哪些要回复、哪些能归档；别移动原件。" },
          { role: "assistant", at: "21:12", text: "下载目录里有两份会议材料、一封需要回复的邮件附件和几页手稿。我按「需要回复」「可以归档」「待核对」分好了，原件都没动。" },
          { role: "user", at: "21:12", text: "会议材料先放待核对，明早我确认后再归档。" },
          { role: "assistant", at: "21:13", text: "好，会议材料留在待核对。归档清单和需要回复的两项已整理好，主 Agent 会写进《明天的行动清单》。" },
        ],
        events: [
          { kind: "delegation.created", at: "21:11" },
          { kind: "action.completed", at: "21:12" },
          { kind: "succeeded", at: "21:13" },
        ],
        tools: ["files_list", "files_read"],
      },
    },
    {
      kind: "delegation",
      id: "calendar-check",
      parentId: "演示",
      label: "日程核对",
      state: "waiting_peer",
      detail: "会议开始时间仍待确认。",
      profile: "日程核对",
      profileVersion: 1,
      goalVersion: 1,
      reusedFromVersion: null,
      summary: "会议开始时间仍待确认；日程保持原样，未改动。",
      summaryTruncated: false,
      summaryRedacted: false,
      errorCode: null,
      unknownEffect: false,
      unknownModelCalls: 0,
      demo: true,
      snapshot: {
        demo: true,
        messages: [
          { role: "user", at: "21:12", text: "明早的会议时间帮我核对一下，日历和邮件都要看。" },
          { role: "assistant", at: "21:13", text: "日历里是 9:30，邮件里写的是 10:00，两边不一致。我先标为待确认，没有改动日程。" },
          { role: "user", at: "21:14", text: "先别改，等我明天问清楚。" },
          { role: "assistant", at: "21:14", text: "好，日程保持原样。会议时间已列进《待确认事项》。" },
        ],
        events: [
          { kind: "delegation.created", at: "21:12" },
          { kind: "action.completed", at: "21:13" },
          { kind: "waiting_peer", at: "21:14" },
        ],
        tools: ["calendar_list", "email_search"],
      },
    },
  ],
  tasks: [
    { label: "整理明天的行动清单", state: "completed" },
    { label: "标出需要确认的资料和邮件", state: "completed" },
    { label: "核对会议开始时间", state: "pending" },
  ],
  approvals: [{
    id: "notes-example", tool: "files_write", createdAt: "21:15",
    arguments: { 文件: "明天的行动清单.md", 位置: "本机笔记" },
  }],
};
