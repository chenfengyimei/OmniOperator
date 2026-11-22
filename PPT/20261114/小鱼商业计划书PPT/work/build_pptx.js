const { PptxGenJS, fs, path, SLIDE_W, SLIDE_H, imgOpts, imgMap, gradientMaskB64 } = require("./build_header.js");

const C = { canvas: "0A0A0F", canvas2: "12121A", panel: "1A1A24", ink: "FAFAFA", accent: "F59E0B", support: "71717A", line: "FFFFFF" };
const FONTS = { display: "微软雅黑", body: "微软雅黑", mono: "Courier New" };
const FS = { display: 54, heading: 28, sub: 18, body: 16, label: 13, big: 46 };
const RADIUS = 0.11, CARD_R = 0.17;

const deck = new PptxGenJS();
deck.layout = "LAYOUT_WIDE";
deck.author = `小鱼造物队`;
deck.title = `OmniOperator 小鱼 · 功能与系统模块介绍`;

// ═══ 通用 helper ═══
function bgContent(s) {
  const e = imgMap()["bg_content"];
  if (e && !e.failed) {
    const abs = path.resolve(__dirname, e.src);
    if (fs.existsSync(abs)) {
      s.background = { path: abs };
      s.addShape(deck.shapes.RECTANGLE, { x: 0, y: 0, w: SLIDE_W, h: SLIDE_H, fill: { color: C.canvas, transparency: 55 }, line: { type: "none" } });
      return;
    }
  }
  s.background = { color: C.canvas };
}
function orb(s, cx, cy, r, color) {
  const tr = [96, 92, 86, 80];
  for (let i = 0; i < 4; i++) {
    const rr = r * (1 - i * 0.22);
    s.addShape(deck.shapes.OVAL, { x: cx - rr, y: cy - rr, w: rr * 2, h: rr * 2, fill: { color: color || C.accent, transparency: tr[i] }, line: { type: "none" } });
  }
}
function overline(s, text, x, y) {
  s.addShape(deck.shapes.OVAL, { x: x, y: y + 0.06, w: 0.12, h: 0.12, fill: { color: C.accent }, line: { type: "none" } });
  s.addText(text, { x: x + 0.22, y: y, w: 11, h: 0.3, margin: 0, fontFace: FONTS.mono, fontSize: FS.label, color: C.accent, charSpacing: 2, valign: "middle" });
}
function title(s, text, x, y, w) {
  s.addText(text, { x: x, y: y, w: w || 12, h: 0.7, margin: 0, fontFace: FONTS.display, fontSize: FS.heading, bold: true, color: C.ink, valign: "middle" });
}
function hairline(s, x, y, w) {
  s.addShape(deck.shapes.LINE, { x: x, y: y, w: w, h: 0, line: { color: C.line, width: 1, transparency: 88 } });
}
function glass(s, x, y, w, h, glow) {
  const opt = { x, y, w, h, fill: { color: C.panel, transparency: 40 }, rectRadius: CARD_R, line: { color: C.line, width: 1, transparency: 92 } };
  if (glow) { opt.line = { color: C.accent, width: 1, transparency: 70 }; opt.shadow = { type: "outer", blur: 24, offset: 0, color: C.accent, opacity: 0.25 }; }
  s.addShape(deck.shapes.ROUNDED_RECTANGLE, opt);
}
function numBadge(s, x, y, n, d) {
  const dd = d || 0.5;
  s.addShape(deck.shapes.OVAL, { x, y, w: dd, h: dd, fill: { color: C.accent, transparency: 85 }, line: { color: C.accent, width: 1, transparency: 60 } });
  s.addText(String(n), { x, y, w: dd, h: dd, margin: 0, align: "center", valign: "middle", fontFace: FONTS.mono, fontSize: 16, bold: true, color: C.accent });
}

// ═══ 第 1 页 · 封面 ═══
(() => {
  const s = deck.addSlide();
  const e = imgMap()["cover_main"];
  let hasImg = false;
  if (e && !e.failed) { const abs = path.resolve(__dirname, e.src); if (fs.existsSync(abs)) { s.background = { path: abs }; hasImg = true; } }
  if (!hasImg) s.background = { color: C.canvas };
  if (hasImg) s.addImage({ data: gradientMaskB64({ w: 512, h: 288, color: C.canvas, dir: "right", stops: [[0, 1], [0.5, 0.82], [1, 0]] }), x: 0, y: 0, w: SLIDE_W, h: SLIDE_H });
  orb(s, 11.4, 1.2, 2.6, C.accent);
  overline(s, `2026 全球AI造物大赛 · 智能办公与任务执行`, 0.7, 1.5);
  s.addText([
    { text: `OmniOperator`, options: { color: C.ink, breakLine: true } },
    { text: `小鱼`, options: { color: C.accent } }
  ], { x: 0.7, y: 2.0, w: 9.5, h: 2.0, margin: 0, fontFace: FONTS.display, fontSize: FS.display, bold: true, charSpacing: -1, lineSpacingMultiple: 1.05, valign: "top" });
  s.addText(`像真人一样，自动帮你把事做完`, { x: 0.7, y: 4.15, w: 9.5, h: 0.6, margin: 0, fontFace: FONTS.display, fontSize: 26, bold: true, color: C.ink, valign: "middle" });
  s.addText(`常驻本地的数字具身办公智能体：自动登录、查询、核实、执行、通知——你只需回复一句话。`, { x: 0.7, y: 4.85, w: 9.2, h: 0.8, margin: 0, fontFace: FONTS.body, fontSize: FS.sub, color: C.support, lineSpacingMultiple: 1.4, valign: "top" });
  hairline(s, 0.7, 6.1, 6.0);
  s.addText(`小鱼造物队 · 功能与系统模块介绍`, { x: 0.7, y: 6.25, w: 8, h: 0.4, margin: 0, fontFace: FONTS.mono, fontSize: FS.label, color: C.support, charSpacing: 1, valign: "middle" });
})();

// ═══ 第 2 页 · 像真人的数字员工 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  orb(s, 6.66, 4.3, 2.0, C.accent);
  overline(s, `WHAT IS IT · 数字具身`, 0.7, 0.55);
  title(s, `像真人一样的数字员工`, 0.7, 0.95, 12);
  s.addText(`它不是只会聊天的窗口，而是住在你电脑里、有手有眼有记忆的数字员工——你说一句，它自己把事做完。`, { x: 0.7, y: 1.7, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, valign: "top" });
  s.addShape(deck.shapes.OVAL, { x: 5.46, y: 3.35, w: 2.4, h: 2.4, fill: { color: C.panel, transparency: 20 }, line: { color: C.accent, width: 1.5, transparency: 50 }, shadow: { type: "outer", blur: 30, offset: 0, color: C.accent, opacity: 0.3 } });
  s.addText([
    { text: `数字员工`, options: { breakLine: true, fontSize: 22, bold: true, color: C.ink } },
    { text: `小鱼`, options: { fontSize: 16, color: C.accent } }
  ], { x: 5.46, y: 3.35, w: 2.4, h: 2.4, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, lineSpacingMultiple: 1.2 });
  const dims = [
    [`有眼 · 感官`, `截图、UI 树、网页正文——先看见，再动手`, 1.0, 2.5],
    [`有手 · 身体`, `桌面、手机、浏览器三套效应器，真的能操作`, 9.4, 2.5],
    [`有记忆`, `六层记忆，记得住事、懂你的人、查得到资料`, 0.7, 5.0],
    [`有节律`, `心跳驱动，到点主动做事，不用你反复催`, 9.7, 5.0],
    [`有边界`, `确定性安全内核，写操作要你点头才执行`, 5.16, 5.85]
  ];
  dims.forEach((d) => {
    const w = 3.0;
    glass(s, d[2], d[3], w, 1.5, false);
    s.addText(d[0], { x: d[2] + 0.25, y: d[3] + 0.18, w: w - 0.5, h: 0.4, margin: 0, fontFace: FONTS.display, fontSize: 18, bold: true, color: C.accent, valign: "middle" });
    s.addText(d[1], { x: d[2] + 0.25, y: d[3] + 0.62, w: w - 0.5, h: 0.8, margin: 0, fontFace: FONTS.body, fontSize: 14, color: C.ink, lineSpacingMultiple: 1.3, valign: "top" });
  });
})();

// ═══ 第 3 页 · 系统模块总览 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  overline(s, `ARCHITECTURE · 系统模块`, 0.7, 0.55);
  title(s, `系统模块总览 · 单内核四入口`, 0.7, 0.95, 12);
  // 四入口
  const entries = [`QQ 通道`, `Web 工作台`, `CLI 命令行`, `心跳进程`];
  const ew = 2.7, egap = 0.31, ex0 = 0.7;
  entries.forEach((en, i) => {
    const x = ex0 + i * (ew + egap);
    glass(s, x, 1.95, ew, 0.62, false);
    s.addText(en, { x: x, y: 1.95, w: ew, h: 0.62, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 16, bold: true, color: C.ink });
  });
  // 入口→内核 连线
  s.addShape(deck.shapes.LINE, { x: 6.66, y: 2.57, w: 0, h: 0.42, line: { color: C.accent, width: 1.5, transparency: 40 } });
  // 内核
  s.addShape(deck.shapes.ROUNDED_RECTANGLE, { x: 2.5, y: 2.99, w: 8.33, h: 0.78, fill: { color: C.panel, transparency: 15 }, rectRadius: CARD_R, line: { color: C.accent, width: 1.5, transparency: 50 }, shadow: { type: "outer", blur: 22, offset: 0, color: C.accent, opacity: 0.25 } });
  s.addText([
    { text: `单内核 · 统一组装根 → 核心编排`, options: { bold: true, color: C.ink, breakLine: true, fontSize: 17 } },
    { text: `回合 / 工具循环 / 取消 / 委派 / 档位路由`, options: { color: C.support, fontSize: 13 } }
  ], { x: 2.5, y: 2.99, w: 8.33, h: 0.78, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, lineSpacingMultiple: 1.15 });
  // 内核→模块 连线
  s.addShape(deck.shapes.LINE, { x: 6.66, y: 3.77, w: 0, h: 0.42, line: { color: C.accent, width: 1.5, transparency: 40 } });
  // 五模块
  const mods = [
    [`工具能力`, `桌面/安卓/浏览器/文件/网页/定时`],
    [`模型网关`, `多供应商归一·能力校验·费用账本`],
    [`记忆系统`, `六层记忆·混合检索·技能存储`],
    [`安全内核`, `风险分级·审批·fail-closed`],
    [`插件生态`, `技能包·MCP扩展·社区分发`]
  ];
  const mw = 2.2, mgap = 0.23, mx0 = 0.7;
  mods.forEach((m, i) => {
    const x = mx0 + i * (mw + mgap);
    glass(s, x, 4.19, mw, 1.85, i === 3);
    s.addText(m[0], { x: x + 0.15, y: 4.38, w: mw - 0.3, h: 0.45, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 16, bold: true, color: C.accent });
    hairline(s, x + 0.25, 4.92, mw - 0.5);
    s.addText(m[1], { x: x + 0.18, y: 5.05, w: mw - 0.36, h: 0.9, margin: 0, align: "center", valign: "top", fontFace: FONTS.body, fontSize: 13, color: C.ink, lineSpacingMultiple: 1.3 });
  });
  s.addText(`分层架构由 AST 静态检查强制，改坏即红；四个入口共用同一份记忆与同一套权限。`, { x: 0.7, y: 6.4, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, valign: "middle" });
})();

// ═══ 第 4 页 · 功能全景 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  orb(s, 12.5, 1.0, 2.0, C.accent);
  overline(s, `CAPABILITIES · 功能全景`, 0.7, 0.55);
  title(s, `功能全景 · 它能替你做什么`, 0.7, 0.95, 12);
  const rows = [
    [`能力域`, `像真人一样做的事`],
    [`Windows 桌面`, `操作桌面应用：打开、点击、输入、核对结果，带观察令牌防点错`],
    [`安卓手机`, `ADB 驱动手机：点按、滑动、输入、实时画面，跨设备协同`],
    [`浏览器`, `自动登录后台、填表、抓取信息、操作网页邮箱`],
    [`文件与网页`, `读写本地文件、抓取网页正文核实内容，数据留在本机`],
    [`定时任务`, `周期/一次性闹钟、任务依赖链、掉线补投，到点主动做`],
    [`QQ 通道`, `接收你的指令、主动通知你、收到回复后继续执行`],
    [`技能与插件`, `自然语言技能包、MCP 扩展、社区生态，能力可生长`]
  ];
  const colW = [3.0, 8.9];
  const tblRows = rows.map((r, ri) => r.map((cell, ci) => ({
    text: cell,
    options: {
      fill: { color: ri === 0 ? C.panel : (ri % 2 ? C.canvas2 : C.canvas) },
      color: ri === 0 ? C.accent : (ci === 0 ? C.ink : C.support),
      bold: ri === 0 || ci === 0,
      fontSize: ri === 0 ? 16 : 15,
      fontFace: FONTS.body,
      valign: "middle"
    }
  })));
  s.addTable(tblRows, { x: 0.7, y: 1.8, w: 11.9, colW, rowH: [0.5, 0.58, 0.58, 0.58, 0.58, 0.58, 0.58, 0.58], border: { pt: 1, color: C.line, transparency: 88 }, margin: [0.05, 0.2, 0.05, 0.2] });
  s.addText(`读、查询、核实全自动；涉及发送、修改的写操作，默认经你确认后执行。`, { x: 0.7, y: 6.6, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, bold: true, color: C.accent, valign: "middle" });
})();

// ═══ 第 5 页 · 真实场景 导航网收录审核 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  orb(s, 1.0, 6.6, 2.0, C.accent);
  overline(s, `REAL SCENARIO · 导航网收录审核`, 0.7, 0.55);
  title(s, `真实场景 · 导航网收录审核`, 0.7, 0.95, 12);
  // 左：痛点
  glass(s, 0.7, 1.95, 5.7, 4.4, false);
  s.addText(`你的烦恼`, { x: 1.0, y: 2.2, w: 5.1, h: 0.5, margin: 0, fontFace: FONTS.display, fontSize: 20, bold: true, color: C.ink, valign: "middle" });
  const pains = [
    `你运营一个导航网，天天有人申请收录网站`,
    `人工审核费时费力，一条条打开看太累`,
    `假网站、诈骗站混在里面，一不小心就收录`,
    `漏审、误收，影响导航站的口碑和安全`
  ];
  s.addText(pains.map((t, j) => ({ text: t, options: { bullet: { code: "25CF", color: C.support }, breakLine: j < pains.length - 1, color: C.ink } })), { x: 1.0, y: 2.85, w: 5.1, h: 3.2, margin: 0, fontFace: FONTS.body, fontSize: FS.body, lineSpacingMultiple: 1.4, paraSpaceAfter: 12, valign: "top" });
  // 右：方案
  glass(s, 6.7, 1.95, 5.93, 4.4, true);
  s.addText(`小鱼接管全流程`, { x: 7.0, y: 2.2, w: 5.3, h: 0.5, margin: 0, fontFace: FONTS.display, fontSize: 20, bold: true, color: C.accent, valign: "middle" });
  const sols = [
    `自动登录后台，逐条查询收录申请`,
    `打开网站核实内容，识别假站 / 诈骗站`,
    `问题网站直接 pass，正常网站推给你`,
    `QQ 发你一条消息，你回一句就完成收录`
  ];
  s.addText(sols.map((t, j) => ({ text: t, options: { bullet: { code: "25CF", color: C.accent }, breakLine: j < sols.length - 1, color: C.ink } })), { x: 7.0, y: 2.85, w: 5.3, h: 3.2, margin: 0, fontFace: FONTS.body, fontSize: FS.body, lineSpacingMultiple: 1.4, paraSpaceAfter: 12, valign: "top" });
  s.addText(`繁琐的核实它全干，最后拍板你回一个字。`, { x: 0.7, y: 6.55, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, bold: true, color: C.accent, valign: "middle" });
})();

// ═══ 第 6 页 · 第一步 自动登录与核实 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  overline(s, `SCENARIO STEP 1 · 全自动核实`, 0.7, 0.55);
  title(s, `第一步 · 自动登录后台，逐条核实`, 0.7, 0.95, 12);
  s.addText(`这一段全是读与查询，小鱼全自动完成，不需要你盯着。`, { x: 0.7, y: 1.7, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, valign: "top" });
  const steps = [
    [`登录后台`, `浏览器自动化打开导航网后台，自动完成登录`],
    [`查询申请`, `读取收录申请列表，逐条取出待审网站`],
    [`核实内容`, `抓取网站正文，判断是否真实、有无诈骗风险`],
    [`智能过滤`, `假站 / 诈骗站直接 pass，正常站进入待确认`]
  ];
  const n = steps.length, x0 = 1.8, x1 = 11.5, yLine = 3.5;
  const span = (x1 - x0) / (n - 1);
  s.addShape(deck.shapes.LINE, { x: x0, y: yLine, w: x1 - x0, h: 0, line: { color: C.accent, width: 2, transparency: 40 } });
  steps.forEach((st, i) => {
    const cx = x0 + i * span;
    s.addShape(deck.shapes.OVAL, { x: cx - 0.32, y: yLine - 0.32, w: 0.64, h: 0.64, fill: { color: C.accent, transparency: 80 }, line: { color: C.accent, width: 1.5, transparency: 40 }, shadow: { type: "outer", blur: 18, offset: 0, color: C.accent, opacity: 0.3 } });
    s.addText(String(i + 1), { x: cx - 0.32, y: yLine - 0.32, w: 0.64, h: 0.64, margin: 0, align: "center", valign: "middle", fontFace: FONTS.mono, fontSize: 18, bold: true, color: C.accent });
    s.addText(st[0], { x: cx - 1.3, y: yLine - 1.15, w: 2.6, h: 0.5, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 20, bold: true, color: C.ink });
    glass(s, cx - 1.35, yLine + 0.6, 2.7, 1.7, false);
    s.addText(st[1], { x: cx - 1.15, y: yLine + 0.78, w: 2.3, h: 1.4, margin: 0, align: "center", valign: "middle", fontFace: FONTS.body, fontSize: 14, color: C.support, lineSpacingMultiple: 1.35 });
  });
  s.addText(`每一步都绑定观察令牌：界面变了就重新看，绝不点到错的地方。`, { x: 0.7, y: 6.5, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, bold: true, color: C.accent, valign: "middle" });
})();

// ═══ 第 7 页 · 第二步 消息通知 你只需回复 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  orb(s, 12.4, 6.6, 2.0, C.accent);
  overline(s, `SCENARIO STEP 2 · 人机协同`, 0.7, 0.55);
  title(s, `第二步 · QQ 通知你，你只需回复一句`, 0.7, 0.95, 12);
  // 左：QQ 对话模拟
  glass(s, 0.7, 1.9, 6.0, 4.6, false);
  s.addText(`QQ 对话`, { x: 1.0, y: 2.1, w: 5.4, h: 0.4, margin: 0, fontFace: FONTS.mono, fontSize: FS.label, color: C.support, charSpacing: 1, valign: "middle" });
  // 小鱼气泡1
  s.addShape(deck.shapes.ROUNDED_RECTANGLE, { x: 1.0, y: 2.6, w: 5.0, h: 1.35, fill: { color: C.canvas2 }, rectRadius: 0.12, line: { color: C.line, width: 1, transparency: 88 } });
  s.addText([
    { text: `小鱼：`, options: { bold: true, color: C.accent, breakLine: true } },
    { text: `导航网收录申请「XX工具站」，已核实内容正常、无诈骗风险。是否同意收录？`, options: { color: C.ink } }
  ], { x: 1.2, y: 2.72, w: 4.6, h: 1.1, margin: 0, fontFace: FONTS.body, fontSize: 14, lineSpacingMultiple: 1.3, valign: "top" });
  // 用户气泡
  s.addShape(deck.shapes.ROUNDED_RECTANGLE, { x: 3.6, y: 4.1, w: 2.4, h: 0.6, fill: { color: C.accent, transparency: 75 }, rectRadius: 0.12, line: { color: C.accent, width: 1, transparency: 50 } });
  s.addText(`你：同意`, { x: 3.6, y: 4.1, w: 2.4, h: 0.6, margin: 0, align: "center", valign: "middle", fontFace: FONTS.body, fontSize: 15, bold: true, color: C.ink });
  // 小鱼气泡2
  s.addShape(deck.shapes.ROUNDED_RECTANGLE, { x: 1.0, y: 4.85, w: 5.0, h: 1.35, fill: { color: C.canvas2 }, rectRadius: 0.12, line: { color: C.line, width: 1, transparency: 88 } });
  s.addText([
    { text: `小鱼：`, options: { bold: true, color: C.accent, breakLine: true } },
    { text: `已收录「XX工具站」，并回复了申请者。下一条申请继续核实中。`, options: { color: C.ink } }
  ], { x: 1.2, y: 4.97, w: 4.6, h: 1.1, margin: 0, fontFace: FONTS.body, fontSize: 14, lineSpacingMultiple: 1.3, valign: "top" });
  // 右：说明
  s.addText(`你只需回复一句话`, { x: 7.1, y: 2.0, w: 5.5, h: 0.5, margin: 0, fontFace: FONTS.display, fontSize: 22, bold: true, color: C.accent, valign: "middle" });
  const pts = [
    [`全自动`, `登录、查询、核实、过滤，它自己干完`],
    [`你拍板`, `收录是写操作，推给你确认后才执行`],
    [`即回复即执行`, `QQ 收到你的回复，立刻完成收录并回执`],
    [`可扩展`, `当前走 QQ 通道，微信等渠道可扩展接入`]
  ];
  let y = 2.7;
  pts.forEach((p) => {
    s.addShape(deck.shapes.RECTANGLE, { x: 7.1, y: y + 0.06, w: 0.05, h: 0.72, fill: { color: C.accent }, line: { type: "none" } });
    s.addText(p[0], { x: 7.35, y: y, w: 5.2, h: 0.4, margin: 0, fontFace: FONTS.display, fontSize: 17, bold: true, color: C.ink, valign: "middle" });
    s.addText(p[1], { x: 7.35, y: y + 0.42, w: 5.2, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: 14, color: C.support, valign: "top" });
    y += 0.98;
  });
})();

// ═══ 第 8 页 · 更多自动化事务 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  overline(s, `MORE AUTOMATION · 举一反三`, 0.7, 0.55);
  title(s, `它还能自动帮你做这些`, 0.7, 0.95, 12);
  const cards = [
    [`邮件处理`, `用浏览器自动化操作网页邮箱：阅读、分类、起草回复；发送经你确认后发出。`],
    [`定时报表`, `心跳节律到点自动汇总数据、生成报表，主动推送给你，不用你催。`],
    [`跨设备协同`, `电脑、手机、浏览器同一套记忆与权限，一个任务跨设备接着做。`],
    [`数据分析`, `本地文件 + 网页抓取，混合检索查得到、用得上，数据全程不出本机。`]
  ];
  const cw = 5.8, ch = 2.05, gx = 0.3, gy = 0.3, x0 = 0.7, y0 = 1.95;
  cards.forEach((cd, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = x0 + col * (cw + gx), y = y0 + row * (ch + gy);
    glass(s, x, y, cw, ch, i === 0);
    numBadge(s, x + 0.3, y + 0.28, i + 1, 0.46);
    s.addText(cd[0], { x: x + 0.95, y: y + 0.28, w: cw - 1.2, h: 0.46, margin: 0, fontFace: FONTS.display, fontSize: 19, bold: true, color: C.ink, valign: "middle" });
    s.addText(cd[1], { x: x + 0.3, y: y + 0.9, w: cw - 0.6, h: 1.0, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, lineSpacingMultiple: 1.4, valign: "top" });
  });
  s.addText(`同一套能力，换个场景就能复用——办公自动化、个人事务、行业工作流都适用。`, { x: 0.7, y: 6.55, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, bold: true, color: C.accent, valign: "middle" });
})();

// ═══ 第 9 页 · 为什么敢放心交给它 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  orb(s, 12.5, 1.0, 2.0, C.accent);
  overline(s, `SAFETY · 确定性安全内核`, 0.7, 0.55);
  title(s, `为什么敢放心交给它`, 0.7, 0.95, 12);
  s.addText(`安全不能是提示词，必须是拦不住的代码。`, { x: 0.7, y: 1.7, w: 12, h: 0.5, margin: 0, fontFace: FONTS.display, fontSize: 22, bold: true, color: C.ink, valign: "middle" });
  const rows = [
    [`读写分离`, `读、查询、核实全自动；发送、修改、删除等写操作必须经你确认。`],
    [`灾难级硬黑名单`, `格式化、递归删除等命令人工确认也不执行——不存在“点错一次全盘清空”。`],
    [`无人值守 fail-closed`, `定时任务默认拒绝一切写操作，审批器缺省即拒绝；不是尽量安全，是默认关闭。`],
    [`结果未知永不重试`, `发送中途失败的写操作标记 unknown，禁止自动重发——重复发送比漏发更糟。`]
  ];
  let y = 2.5;
  rows.forEach((r, i) => {
    s.addShape(deck.shapes.RECTANGLE, { x: 0.7, y: y + 0.08, w: 0.05, h: 0.78, fill: { color: C.accent }, line: { type: "none" } });
    s.addText(r[0], { x: 0.95, y: y, w: 3.3, h: 0.9, margin: 0, fontFace: FONTS.display, fontSize: 18, bold: true, color: C.ink, valign: "middle" });
    s.addText(r[1], { x: 4.4, y: y, w: 8.2, h: 0.9, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, lineSpacingMultiple: 1.35, valign: "middle" });
    if (i < rows.length - 1) hairline(s, 0.7, y + 1.02, 11.9);
    y += 1.12;
  });
})();

// ═══ 第 10 页 · 工程成熟度 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  orb(s, 6.66, 7.4, 2.4, C.accent);
  overline(s, `MATURITY · 可验证的质量`, 0.7, 0.55);
  title(s, `工程成熟度 · 可验证的质量`, 0.7, 0.95, 12);
  const stats = [
    [`4654`, `项离线自动化测试通过`, `最新全量 4658 项中 4654 通过（2026-11-12 交接记录口径）`],
    [`1.4 : 1`, `生产代码与测试代码比`, `每个新模块必须有单测，默认离线、数据隔离注入`],
    [`Apache-2.0`, `开源许可`, `架构分层由 AST 静态检查强制，改坏即红`]
  ];
  const cw = 3.86, gap = 0.28, x0 = 0.7, y0 = 2.0, ch = 3.0;
  stats.forEach((st, i) => {
    const x = x0 + i * (cw + gap);
    glass(s, x, y0, cw, ch, i === 0);
    s.addText(st[0], { x: x + 0.25, y: y0 + 0.3, w: cw - 0.5, h: 1.0, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: i === 2 ? 30 : FS.big, bold: true, color: i === 0 ? C.accent : C.ink });
    s.addText(st[1], { x: x + 0.25, y: y0 + 1.4, w: cw - 0.5, h: 0.5, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 16, bold: true, color: C.ink });
    s.addText(st[2], { x: x + 0.3, y: y0 + 1.95, w: cw - 0.6, h: 0.95, margin: 0, align: "center", valign: "top", fontFace: FONTS.body, fontSize: 13, color: C.support, lineSpacingMultiple: 1.3 });
  });
  s.addText(`Windows 安装器链全离线验证；四层架构 + 组装根唯一 + DDL 防漂移守卫。V2 重构推进中，macOS / Linux 服务管理已接线、待实机验收。`, { x: 0.7, y: 5.4, w: 12, h: 1.0, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, lineSpacingMultiple: 1.45, valign: "top" });
  s.addText(`数据来源：项目交接记录（2026-11-12）与 README 工程质量章节；未验证平台不写成已支持。`, { x: 0.7, y: 6.6, w: 12, h: 0.4, margin: 0, fontFace: FONTS.mono, fontSize: FS.label, color: C.support, valign: "middle" });
})();

// ═══ 第 11 页 · 商业模式与路线图 ═══
(() => {
  const s = deck.addSlide(); bgContent(s);
  overline(s, `BUSINESS & ROADMAP · 商业与路线`, 0.7, 0.55);
  title(s, `商业模式与路线图`, 0.7, 0.95, 12);
  // 上：三档商业模式
  const tiers = [
    [`开源社区版`, `Apache-2.0 免费，社区获客建信任`],
    [`个人订阅`, `高级技能 · 多设备协同 · 优先支持`],
    [`企业私有化`, `审计合规 · 私有部署 · 定制集成`]
  ];
  const cw = 3.86, gap = 0.28, x0 = 0.7, y0 = 1.85, ch = 1.7;
  tiers.forEach((t, i) => {
    const x = x0 + i * (cw + gap);
    glass(s, x, y0, cw, ch, i === 2);
    s.addText(t[0], { x: x + 0.25, y: y0 + 0.22, w: cw - 0.5, h: 0.45, margin: 0, fontFace: FONTS.display, fontSize: 18, bold: true, color: C.accent, valign: "middle" });
    s.addText(t[1], { x: x + 0.25, y: y0 + 0.75, w: cw - 0.5, h: 0.8, margin: 0, fontFace: FONTS.body, fontSize: 14, color: C.ink, lineSpacingMultiple: 1.35, valign: "top" });
  });
  // 下：路线图时间轴
  const phases = [
    [`近期`, `V2 收尾`, `三平台实机验收、干净机器端到端`],
    [`中期`, `企业版`, `私有化与技能市场、审计合规`],
    [`赛后`, `孵化加速`, `园区入驻、导师辅导、资本对接`]
  ];
  const n = phases.length, px0 = 2.1, px1 = 11.2, yLine = 4.7;
  const span = (px1 - px0) / (n - 1);
  s.addShape(deck.shapes.LINE, { x: px0, y: yLine, w: px1 - px0, h: 0, line: { color: C.accent, width: 2, transparency: 40 } });
  phases.forEach((p, i) => {
    const cx = px0 + i * span;
    s.addShape(deck.shapes.OVAL, { x: cx - 0.26, y: yLine - 0.26, w: 0.52, h: 0.52, fill: { color: C.accent, transparency: 80 }, line: { color: C.accent, width: 1.5, transparency: 40 } });
    s.addText(p[0], { x: cx - 1.4, y: yLine - 0.9, w: 2.8, h: 0.4, margin: 0, align: "center", valign: "middle", fontFace: FONTS.mono, fontSize: 14, bold: true, color: C.accent, charSpacing: 1 });
    s.addText(p[1], { x: cx - 1.6, y: yLine + 0.4, w: 3.2, h: 0.4, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 17, bold: true, color: C.ink });
    s.addText(p[2], { x: cx - 1.6, y: yLine + 0.85, w: 3.2, h: 0.8, margin: 0, align: "center", valign: "top", fontFace: FONTS.body, fontSize: 13, color: C.support, lineSpacingMultiple: 1.3 });
  });
  s.addText(`大赛资源支持：云算力与 API 额度 · 硬件服务券 · 深圳园区孵化 · 导师辅导 · 资本对接。`, { x: 0.7, y: 6.55, w: 12, h: 0.5, margin: 0, fontFace: FONTS.body, fontSize: FS.body, color: C.support, valign: "middle" });
})();

// ═══ 第 12 页 · 团队与诉求 ═══
(() => {
  const s = deck.addSlide();
  const e = imgMap()["bg_content"];
  let hasImg = false;
  if (e && !e.failed) { const abs = path.resolve(__dirname, e.src); if (fs.existsSync(abs)) { s.background = { path: abs }; hasImg = true; } }
  if (!hasImg) s.background = { color: C.canvas };
  if (hasImg) s.addShape(deck.shapes.RECTANGLE, { x: 0, y: 0, w: SLIDE_W, h: SLIDE_H, fill: { color: C.canvas, transparency: 50 }, line: { type: "none" } });
  orb(s, 6.66, 2.0, 3.0, C.accent);
  overline(s, `TEAM & ASK · 智能办公与任务执行`, 0.7, 1.3);
  s.addText([
    { text: `让 AI 真正`, options: { color: C.ink } },
    { text: `完成任务`, options: { color: C.accent } }
  ], { x: 0.7, y: 1.8, w: 12, h: 1.2, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 48, bold: true, charSpacing: -1 });
  s.addText(`小鱼造物队 · 1–3 人团队 · 技术负责人（架构 / 安全内核）+ 产品演示 + 工程测试`, { x: 1.5, y: 3.2, w: 10.3, h: 0.5, margin: 0, align: "center", valign: "middle", fontFace: FONTS.body, fontSize: FS.body, color: C.support });
  hairline(s, 4.0, 4.0, 5.3);
  const asks = [
    [`奖金用途`, `研发投入 · 设备适配 · 合规建设`],
    [`资源诉求`, `云算力 / API 额度 · 园区孵化 · 导师 · 资本对接`]
  ];
  asks.forEach((a, i) => {
    const x = 1.5 + i * 5.4;
    s.addText(a[0], { x: x, y: 4.3, w: 4.9, h: 0.45, margin: 0, align: "center", valign: "middle", fontFace: FONTS.display, fontSize: 18, bold: true, color: C.accent });
    s.addText(a[1], { x: x, y: 4.8, w: 4.9, h: 0.9, margin: 0, align: "center", valign: "top", fontFace: FONTS.body, fontSize: FS.body, color: C.ink, lineSpacingMultiple: 1.4 });
  });
  s.addText(`OmniOperator · 小鱼 —— 2026 全球AI造物大赛 · 智能办公与任务执行赛道`, { x: 1.5, y: 6.2, w: 10.3, h: 0.4, margin: 0, align: "center", valign: "middle", fontFace: FONTS.mono, fontSize: FS.label, color: C.support, charSpacing: 1 });
  s.addText(`千问AI生成`, { x: SLIDE_W - 3.1, y: 0.14, w: 2.9, h: 0.34, align: "right", valign: "middle", margin: 0, fontFace: FONTS.body, fontSize: 12, color: "9A9AA2" });
})();

deck.writeFile({ fileName: path.join(__dirname, "..", `小鱼商业计划书PPT.pptx`) });
