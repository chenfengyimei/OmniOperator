# Linux 原生接入与验收交接

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

共享内核当前为137表/v2_0039，桌面IPC6/Portal IPC2/凭据IPC1，新增持续窗口证明IPC1。Linux AT-SPI/X11读取、持续X11窗口代次、Portal窗口帧、受控语义、系统凭据helper及D4终端核查源码已接通；Portal授权确认状态按下文明确区分系统同意和业务审计，不扩大Linux输入授权。Linux/GStreamer/Portal、原生凭据与PTY仍未实测，前台/RemoteDesktop控制仍待实现；整个V2未完成，Windows离线候选默认V2并保留显式V1回退。持续窗口读取身份见[专项合同](V2_LINUX_WINDOW_LEASES.md)。

最新接续：已接非可编辑控件Enter的明确状态后验：原观察封存可勾选/选择/展开状态，许可后完整复核，再沿原XRes/焦点/XKB/来源及释放链仅发一次键对；完整窗口树双读保持同一唯一焦点，只有目标一个已声明状态位翻转且其余身份/正文/结构/状态全相同才key_verified。不变/其他变化仍unknown，不猜提交/关闭或重发；可读标签正文保留，编辑文本继续原光标/全文合同。26新增与168调用方/合同共194专项通过（93.81秒），3运行文件strict零错误/警告，386仅登记。旧源码6失败、初实现误排除可读标签的6失败与源码留档，修正实际可编辑判定后原6节点及当前组通过。三类真实隔离SQLite审批/UUID许可/单次消费已验；原生端口全注入，无真实Linux/输入/账号/设备/72h或全量，整个V2继续开发。见[状态证据](../../../evidence/v2-linux-key-state.json)。

上一接续：已接内部只读wlroots窗口观察：显式规范Wayland端点与原获准EI的内核peer PID/UID/创建身份相等后才发协议请求；私有dup只核验peer，不读写EI。绑定真实输出及v3窗口对象，done批次、父关系、活动状态与逐事件代次核验；原目标离开后恢复也失效，不以app_id推PID。原prepare/check读任务、部分帧及关闭归属保留；EI按键释放未结清时不关窗口/监测/Portal。53新增与211调用方/合同分阶段264不同专项通过（主252约43秒，后53直接与6架构）；4运行文件strict零错误/警告，385仅登记。原1闭窗夹具失败、1类型诊断、并发选择原失败及1缓存WinError5警告留档；并发选择现先占位，不能两次成功。当前Windows原生端口全注入，未Wayland/真实输入；活动状态不证明EI座席接收端，AT-SPI/PID、区域/共享来源和非evdev介入/业务后验仍开发，生产输入保持false。整个V2继续开发，无真实账号/模型/设备/72h或全量；远程授权待答，仅本地提交，全量留整个开发冻结后。见[窗口证据](../../../evidence/v2-wayland-window-view.json)。

上一接续：已接内部kernel seat输入守卫：固定libudev/实际logind seat、内核与udev双通知先于完整目录；既有权限下只读逐个event节点，核对真实双FD身份、全键域及触点释放，250ms静止和原线程/监听/关闭任务归属。内核源码确认EVIOCGKEY清本客户端队列，竞态已重现并修复为两次独立open：监测FD只看就绪，另一FD查询状态，不用dup；介入锁存后取消监听避免空转，原FD保留至实际结清。ControlSession内部单次接线，EI释放先结清才关闭监测/原Session；失败撤销设备许可。58新增与187调用方/合同分阶段245不同专项通过（主242约38秒、最后21个直接节点约0.4秒），4运行文件strict零错误/警告，382仅登记。首缓存WinError5警告、2个Windows stat类型诊断及清队列原失败/源码留档。当前全为Windows注入，不改设备权限/路由，不开生产输入；仅覆盖所选seat内核设备，非evdev合成器输入、原生目标/区域及业务/应用后验仍未证明，input_authorized=false。整个V2继续开发，无真实Linux/账号/模型/设备/72h或全量；远程授权待答，仅本地提交，全量留整个开发冻结后。见[监测证据](../../../evidence/v2-wayland-seat-input.json)。

上一接续：核对固定Mutter源码发现合法EI键图按strlen长度发送、不含尾NUL，旧读取器会在实际FD复制处拒绝。现接有/无可选尾NUL两种原始格式：声明尺寸、实际字节及SHA不规范化，完整UTF-8/内部NUL/原1MiB及文件稳定性守卫保持；仅C解析缓冲区补独立终止字节，传完整正文长度，不丢末字节。25新增与182受影响节点分阶段207不同专项通过（主206约17秒、补1可用性节点）；原失败精确重验通过，2修改运行文件strict零错误/警告，379仅登记。原否定夹具按实际协议改查内部NUL/尾垃圾，未冒充语法解析；旧失败与源码保留。当前Windows的EI/XKB端口仍注入，原生解析明确false；没有真实Mutter/Wayland/输入/账号/模型/设备/72h或全量。物理介入、目标关联、通用提供方及整个V2继续开发，input_authorized=false；远程授权待答，仅本地提交，全量留整个开发冻结后。见[格式证据](../../../evidence/v2-wayland-keymap-formats.json)。

上一接续：已接EI内部成对特殊键传输：实际evdev码、原serial与单调微秒时间，按下/抬起分属两个frame，再stop/sync。176字节只计费一次，精确保留部分写入；未完整按下取消就实际丢弃，完整按下仅续原抬起后缀。原writer/部分回调/释放任务在超时、取消和close后保留，不重放；暂停重置只证明本来源逻辑释放，移除/坏回执/未知发送仍待结清。关闭未结清保留原连接、键图正文及实际Portal Session/订阅。43新增与196调用方/合同分阶段239不同专项通过（主237约34秒、补2约3秒）；3运行文件strict零错误/警告，379仅登记。首1夹具FD生命周期失败、28私有访问类型诊断及2重复关闭已关总线的夹具失败和源码均留档后修正，没有放宽合同/期限。当前Windows协议端口/库函数注入，未真实Wayland/输入；只有内部transport_verified，没有业务许可/应用结果，input_authorized=false。人工介入、目标关联、通用输入与整个V2继续开发；无原生平台/账号/模型/设备/72h或全量，远程授权待答，仅本地提交，全量留整个开发冻结后。见[分帧证据](../../../evidence/v2-wayland-eis-key-packet.json)。

上一接续：已接libxkbcommon缓冲区编译源码与EI内部特殊键计划：固定可选系统so/C ABI、禁默认include与环境布局，只编译原键图；11键查实际唯一单符号行，私有状态模拟按下/抬起并核对零修饰/组，evdev惯例减8，不猜US键码。计划绑定原seat/device/接口、完整观察/代次及实际正文SHA，编译后和使用前重验；取消保留原executor/任务/C对象，清理不明保留待清理且不重试unref。49新与180调用方/合同分阶段229不同专项通过，3修改运行文件strict零错误/警告，378仅登记。首46过/2失败是夹具把x改成原x，改有效不同字节后原两节点过；关闭复核仅改close及导入，补5节点过，原报告/源码/AST差异留档。当前Windows注入库函数并操作ctypes缓冲区，原生解析未运行；没有EI输入/业务许可或应用结果证明。真实XKB/通用映射、分帧释放、人工介入/目标关联及整个V2继续开发，input_authorized=false。无真实平台/账号/模型/设备/72h或全量；远程授权待答，仅本地提交，全量留整个开发冻结后。见[XKB证据](../../../evidence/v2-wayland-eis-xkb.json)。

上一接续：已接内部EI设备档：原独立许可/单次FD后只读列seat，再显式选择实际ID，以服务器稀疏能力掩码绑定键鼠，不猜默认seat/固定bits。接虚拟设备v1/v2、逻辑区域/映射ID、键图与修饰状态；暂停/恢复/移除保留代次，旧快照不能复活。键图1MiB有界pread/身份前后验，原线程/Future/FD实际完成后才关闭，取消及close保留同一归属；只校验字节格式，未解析XKB。EI窗口支持最多4个FIFO批量FD，旧默认1/寿命4及核心预算保持，孤立FD真实3秒后关闭拒绝。59新与182调用方/合同分阶段241不同专项通过（主237约23秒、补5约4秒），5修改运行文件strict零错误/警告，376仅登记；首37通过及1类型诊断/源码留档。新增FIFO用不同键图确认关联，精确1MiB/4键图/16区域已验。生产worker/业务未调用，未派发输入；原生坐标关联、XKB、释放/人工介入及整个V2仍开发，input_authorized保持false。无真实Wayland/输入/账号/设备/72h或全量验收；远程授权待答，仅本地提交，全量留整个开发冻结后。见[设备证据](../../../evidence/v2-wayland-eis-devices.json)。

上一接续：已接独立系统许可后的内部单次ConnectToEIS，精确FD回包沿原serial/owner/单描述符守卫；取消等待保留原任务和迟到FD，未结清不关闭原Session。新增EI本机字节序/有界部分收发、sender握手v1、回调sync及ping/pong；未声明seat/device或输入接口。4096单帧/64KiB累计/128事件不重置，意外FD关闭拒绝；真实3秒/1秒期限、失败不复活，detach失败保留原FD再关闭。54新与128调用方/合同共182专项通过（22.09秒），3修改运行文件strict零错误/警告，374仅登记；原PipeWire正文AST按固定参数代换一致。启动脚本/调用方路径错误均未执行测试并留档，初46通过的缓存rename警告与后续修正源码保留。当前只接内部原生连接API，生产worker尚未调用，设备/区域/键图、释放/人工介入、目标与逐次业务许可仍开发，input_authorized保持false。无真实Wayland/EIS/输入/账号/设备/72h或全量验收；整个V2继续开发。远程授权待答，仅本地提交，全量留整个开发冻结后。见[EI证据](../../../evidence/v2-wayland-eis.json)。

上一接续：已接Tab/Escape同窗口唯一焦点后验：完整AT-SPI树实际Parent/同总线双读，128节点/8层/16KiB精确边界；原节点唯一聚焦、窗口与原生接收端保持，许可后先重验再创建当前序号按键包。一次按下/抬起后仅FOCUSED位成对转移且其他身份/正文/结构/状态不变才key_verified，零焦点短等、其他不明unknown不重放；不猜Tab顺序或弹窗关闭。45新与163调用方/合同共208不同专项通过（81.02秒），2修改运行文件strict零错误/警告，373仅登记。首轮18失败/21通过暴露新复核插在包创建之后，已调序保留原序号保护；18原节点通过，原失败/源码留档。原生释放取消及两种键的真实隔离SQLite审批/单次消费已验。核对固定GTK3/GTK4/Qt默认属性无可用XID，未扩大提供方；通用关联、准确命中、关闭/提交、Wayland及整个V2继续开发。无真实Linux/输入/账号/设备/72h或全量验收。远程授权待答，仅本地提交；全量留整个开发冻结后。见[导航证据](../../../evidence/v2-linux-focus-navigation.json)。

上一接续：已接AT-SPI物理点/鼠标业务：SCREEN/WINDOW精确平移、双空间Contains与真实命中后代链，再与持续X11几何/原点双读核对，不猜缩放。原观察指纹、单次审批/UUID ready/permit复用，许可后重验，再一次移动及重新准备成对按钮；勾选/选中/展开/聚焦明确后验才click_verified，其余unknown不重放。父关闭保留实际释放worker/管道/名额及消费回执，原文本/按键兼容。36新与211调用方/合同分阶段247不同节点通过；主组241过/2能力说明回归失败，补回原无剪贴板说明后仅重验探测函数3节点，新状态4节点另过；原失败/源码与AST单文案差异留档，未整组重跑。7修改运行文件strict零错误/警告，372仅登记。已核对Avalonia固定实现窗口只返回自身，不能给子控件点击候选；正向是准确命中提供方注入合同，未声称已安装平台支持。通用提供方/导航、Wayland及整V2仍开发，无真实Linux/输入/账号/设备/72h验收。远程授权待答，仅本地提交，全量留整个开发冻结后。见[点击证据](../../../evidence/v2-linux-click-business.json)。

上一接续：同连接成对按钮内核已接WindowFocus，先重验原物理点/配置/RandR并核对当前真实光标；两次36字节使用实际物理码，不附带移动。八步RawButton/XKB通知按两级映射精确核对，包含核心范围外中间码和原生passive-grab早退的明确码值。取消未完整按下则丢弃，完整按下只补抬起后缀；逻辑掩码与全设备物理按钮均释放才结清，持有/坏回读/原查询未明保留实际连接任务，不重放。46新增及591调用方/合同共637不同专项通过（153.83秒），6修改运行文件strict零错误/警告；370仅登记，未整范围/全量。首轮39过/1断言将自身回执齐全等同无额外事件的错误留档，原失败及新增7节点修正通过，运行源码SHA保持。当前内部派发，AT-SPI物理点/逐次鼠标业务与应用后验继续开发，整个V2未完成；无真实Linux/输入/账号/设备/72h验收。远程授权待答，仅本地提交，全量留完整开发冻结后。见[按钮证据](../../../evidence/v2-linux-button-packet.json)。

上一接续：同连接内部移动派发已接WindowFocus：单次36字节请求、准确部分写入、原QueryPointer结清和两条source/master RawMotion归属；不完整取消丢弃，完整回读不明保留实际连接/任务，不重放。来源类复制先预计算，通知只比对source描述，保留master按钮数/尾标签反馈/映射/CTM；全部窗口/焦点/释放/配置/RandR后验成立才采用，不清空变化。51新增及540调用方/合同共591不同专项通过（135.74秒），10修改运行文件strict零错误/警告；368仅登记，未整范围/全量。首轮43过/1夹具失败及源码留档，原失败和新增4节点修正通过，运行源码SHA保持。更正历史InputWire累计接收为原1MiB、普通Wire66MB，未改额度。内部派发通过注入验证，未真实Linux/鼠标/账号/设备/72h；成对点击释放、AT-SPI物理点/逐次业务及其他平台继续开发，整个V2未完成。远程授权待答，仅本地提交；全量留完整开发冻结后。见[派发证据](../../../evidence/v2-linux-motion-packet.json)。

上一接续：WindowFocus已接单root原生RandR资源/CRTC覆盖及实际轴/完整变换坐标计划，模式/通知/失败/取消不重绑旧准备；负请求、投影、整数网格及不可达像素不猜测。完整配置组合复核实际读取各叶并保留前后原守卫，避免递归重复，原额度/字节/超时保持。40新与500调用方/合同共540不同专项通过，修改10运行文件strict通过；365仅登记，未整范围/全量。测试收集参数错误与排版后SHA阻止均留档修正。仍未发原生移动/点击；来源复制、raw归属/释放、控件物理点/业务及旧Xinerama/多root布局继续开发，无Linux实机结论。见[移动计划证据](../../../evidence/v2-linux-pointer-motion.json)。当前仅本地提交，远程授权待答。

上一接续：WindowFocus已接实际master/XTEST完整轴/滚动/按钮标签/来源、反馈及FLOAT变换配置，不猜固定范围或单位矩阵。瞬时位置/填充排除，双读与使用前重读复用原窗口/映射/XKB守卫，实际属性变化及失败/取消使旧配置失效。43新回归与364直接调用方共407不同专项通过（89.12秒），修改2运行文件strict零错误/警告；363仅登记，未整范围/全量。初轮缓存rename警告留档，当前组无警告。坐标换算、预期来源复制、移动/点击释放及AT-SPI物理点/业务继续开发，无Linux实机或真实输入结论。见[配置证据](../../../evidence/v2-linux-pointer-config.json)。继续本地开发，远程授权待答。

上一接续：WindowFocus已接两设备XKB按钮动作及配对核心键盘环境预检，真实回复的裁剪/显式NoAction和填充规范化；动作通知先于双读，键盘/指针同连接协商保留各自订阅，变化/失败/取消不复活。专用监测寿命8192请求/8448消息，普通Wire原1024/2048和原累计字节/事件/超时保持。42新增及558调用方共600不同专项通过，修改6运行文件strict通过；362仅登记，未整范围或全量。首轮26过/14额度失败及1类型诊断原样留档后修正。当前仍不派发鼠标，变换/轴/反馈/来源复制及控件物理点/业务继续开发；无Linux实机或真实输入结论。见[动作证据](../../../evidence/v2-linux-pointer-actions.json)。远程授权待答，继续本地开发。

上一接续：同连接按钮预检已接WindowFocus；真实ClientPointer、XI2按钮数、slave/master两级完整映射与核心映射交叉核验，实际计算主按钮的物理码。XI1两设备映射通知与核心通知接原变化锁存，订阅中变化、双读不符、取消及恢复映射都不能重绑旧计划；不改映射或发送输入。43新增及341调用方/合同共384不同专项通过（77.33秒），修改3运行文件strict通过；361仅登记，未整范围/全量。原38通过、后续收集参数命名错误及源码均留档。XKB按钮动作、指针变换/轴/反馈与切换复制，移动/点击释放、AT-SPI物理点关联及业务后验继续开发，无真实Linux/鼠标验收，见[前台合同](V2_LINUX_FOREGROUND.md)与[映射证据](../../../evidence/v2-linux-button-mapping.json)。

上一原生鼠标物理点预检已接WindowFocus，使用Xorg实际形状/堆叠命中、QueryTree路径和接收客户端XRes，允许本进程InputOnly子窗口，拒绝同PID链外窗口；结构/子结构订阅保留原焦点监测，失败/取消锁存。60新增及252调用方/合同共312不同专项通过，修改3运行文件strict通过；360仅登记，未整范围/全量。当前只读准备，不移动或点击；AT-SPI物理坐标、按钮映射、逐事件派发/释放及业务后验继续开发，无真实Linux/输入结论，见[前台合同](V2_LINUX_FOREGROUND.md)与[命中证据](../../../evidence/v2-linux-pointer-target.json)。

已完成的显式XID提供方AT-SPI节点按键仍保留：11特殊键复用单次审批、UUID ready/permit、窗口/节点/映射/复制重验与内部成对释放；明确文本/光标后验匹配才报告key_verified，其余unknown不重放。许可后的父进程关闭保留实际释放worker/管道/名额，并等业务读取回执后才结清；首轮发现的回执竞态已保留并修复。当前仅Avalonia明确属性，通用提供方、Tab/Escape/焦点变化专门后验及Wayland仍在开发，详见[按键业务证据](../../../evidence/v2-linux-key-business.json)。

前台控制已补[原生焦点及祖先链只读证明和受限文本追加](V2_LINUX_FOREGROUND.md)。人工聚焦的AT-SPI非密码编辑器可在末尾单次追加Unicode，沿用逐次审批/permit、原文/光标/选区与完整回读；通用键鼠、AT-SPI控件与原生窗口关联继续开发。窗口同PID、标题或坐标均不自动成为映射。

新增显式提供方窗口关联基础：当前仅接受已核对的Avalonia原生XID属性，另核对XRes实际进程/创建身份/程序，再组合自己持有的连续窗口与原生焦点祖先链；描述变化、窗口重建、取消均不能恢复旧绑定，释放未结清保留两条连接。GTK/Qt通用关联仍未解决，业务press_key、逐次许可及应用结果尚未接入。43新回归及202依赖/调用方/合同共245不同专项通过，新增1后端strict通过；未真实Linux/输入或全量，见[绑定证据](../../../evidence/v2-linux-window-binding.json)。

[Wayland独立控制许可](V2_WAYLAND_CONTROL.md)已接原生RemoteDesktop、固定worker、父进程归属、持久业务审计及显式所有者入口；系统设备许可不等于业务输入权。真实输入暂停/目标证明/派发仍在开发，所有回执保持input_authorized=false。

X11前台原生焦点证明另接只读核心按键映射及同连接XKB服务器预检，读取真实keycode范围、完整键盘/修饰键表、服务器状态/控制与候选按键动作。只接受同一特殊键、NoAction/默认行为，配置变化使计划失效；不猜美式键码、不改布局、不注入事件。该层不证明应用如何处理输入；节点关联、输入区分与派发/释放继续开发，详见[前台合同](V2_LINUX_FOREGROUND.md)。

另已接同连接XTEST2.2服务器虚拟设备身份预检：逐设备读取已有属性，核对唯一键鼠、正确主设备挂接及类别，包含禁用/浮动设备以拒绝额外伪标记。设备属性通知纳入原守卫；不按名称筛选，也不把共享XTEST来源当作本动作输入。24新回归及207既有调用方/合同专项通过，修改3后端文件strict通过，无全量/真实输入。来源预检本身不提供逐事件归属或派发/释放，见[来源证据](../../../evidence/v2-linux-xtest.json)。

内部成对按键派发器及释放账本已补齐，精确核对本连接按下/抬起各slave/master原始通知；部分写入不重放按下，释放/回读未结清不报告关闭成功。当时261不同专项通过（29新回归、232现有调用方/合同），修改4后端文件strict通过。合法slave/XKB变更随后接续如下；业务worker及公开press_key入口仍未开放，节点/原生目标关联、逐次许可和应用后置核验继续开发。没有真实输入/Linux原生/全量，见[成对内核证据](../../../evidence/v2-linux-keypair.json)。

切换前先补完整GetMap八部件及主/从状态、控制比对，来源订阅XKB后交替四读；主从共同变化不能重绑旧指纹，取消读取也失效。当时37新回归和原29成对/232调用方合同共298不同专项通过，修改4后端文件strict通过。见[切换前映射证据](../../../evidence/v2-linux-keymap-clone.json)。

现继续补齐兼容表、名称、指示器定义及当前完整几何，加入原四读身份；几何只查当前描述，不触发命名查找。内部包预计算主KeyClass唯一来源变化，仅在首Raw之前接受同按下序号精确NKN→XI SlaveSwitch；四Raw/释放结清后拓扑双读夹住旧配置再验，键释放/原焦点均成立才采用指纹，不清空changed。配置、拓扑异常及后验取消不能复活旧证明。业务节点关联、逐次许可与应用结果仍待，通用入口未开放。修改7后端strict零错误/警告，固定副本专项见[切换证据](../../../evidence/v2-linux-key-switch.json)，无真实输入/Linux原生/全量。

## 已实现的生产链路

`DesktopBackend → 固定 Python 辅助进程 → SessionInspector → LocalBus / AtspiDesktop`；Linux语义写入使用独立固定`omni.adapters.workers.linux_action`，按ready/permit逐次放行。主进程不加载 D-Bus、AT-SPI 或平台 API；核心不安装桌面依赖也可启动。请求总期限仍为 5 秒，辅助进程内部操作预算 4 秒；宿主最多 4 个实际操作、2 个常驻辅助进程。取消等待不会丢弃实际宿主，失败回收继续保留健康状态。

- 先核验 `/run/dbus/system_bus_socket`，随后查询 root 所有的 logind 唯一服务连接。核验当前 UID、活动本地 x11/wayland 用户会话、未锁屏、会话创建标记及 seat 的 ActiveSession。
- 辅助进程没有 logind 会话时，只接受 logind `User.Display`；已处于 TTY/SSH 会话时不回退到其他图形会话。root 不开放桌面能力。未取得 logind 证明时报告 unknown/unavailable，不能因缺少 DISPLAY 而报告已证明 headless。
- 用户总线固定 `/run/user/<uid>/bus`。AT-SPI 地址只取已核验的 `org.a11y.Bus.GetAddress`，接受受保护运行目录中的单个 Unix socket 或有限的 Linux abstract 地址；拒绝 TCP、多个地址、未知字段及跨 UID 路径。
- 每次连接在发送 D-Bus 认证数据前核验父目录、socket 所有者、设备/inode 和实际连接的 `SO_PEERCRED`。后续复核对端 PID/UID、创建时间及路径身份。使用本次已核验的 socket，不依赖环境变量指定总线，不重新连接。所有 RPC 禁用自动启动、固定唯一连接、检查响应来源及签名。
- `dbus-next==0.2.3` 是当前锁定实现；其受保护扩展点用于预连接、认证行及二进制帧限额，其他版本明确拒绝。认证行 4 KiB、单帧 64 KiB、每条连接最多 2048 条消息与 4 MiB 接收量。RPC 分别有超时；无数据库访问、服务激活或自动开启辅助功能。
- 通过 AT-SPI registry 列举最多 32 个应用、128 个顶层候选，返回最多 50 个窗口。目标绑定总线代次、唯一服务、实际进程创建身份、程序路径及图形会话；观察前后复核，变化即废弃。
- 观察最多 128 节点、8 层、单字段 512 字符、合计 16 KiB UTF-8；密码控件及子树不读取 Name/Text。跨连接子树、环、过量树明确截断。Linux响应必须带`coordinate_space: "window"`。仅在X11输入监测证明建立后提供AT-SPI语义动作候选；Windows/AX引用、全局坐标及前台输入候选均拒绝。观察引用本身不授权写入。
- UI 正文仅在观察响应短暂传递，不进入诊断、指标、元数据审计、视图缓存或验收包。异常、权限及清理失败使用静态错误码，不把空树作为成功。

## 当前限制与下一开发合同

同UID不足以证明应用属于所选图形会话。AT-SPI语义后端仍要求应用PID由logind `GetSessionByPID`证明归属；许多systemd用户服务应用没有这个映射，会返回`desktop_target_session_unverified`。X11采集后端使用另一路证明：先证明Xorg服务器属于当前活动会话，再由服务器XRes核验实际客户端PID、UID与创建时间。两种目标不按PID或几何互相映射，不能用X11证明直接解除AT-SPI限制。

窗口共享只读链路和X11监测支持的AT-SPI语义动作已接入生产源码；D3整包仍开发中。D3.linux.control继续实现X11显式前台输入及Wayland RemoteDesktop独立授权与输入暂停证明。Wayland语义及全局输入目前均保持不可用；窗口共享不授予控制权。原生Linux行为尚未验证。


## X11监测支持的AT-SPI语义动作（原生待验收）

沿用`POST /api/v1/agents/{agent_id}/desktop/actions`及通用工作台，客户端只提交当前view/snapshot/target/observation/node与session/request UUID、kind、text。节点候选只属于当前观察，不把总线名、对象路径或原生句柄暴露为客户端写授权，不新增业务表。

观察与证明使用同一次有界Name/Text读取；仅完整、未清洗或截断、非密码节点产生候选。证明包含所选窗口下最多7个子索引、祖先身份/名称链以及角色、名称、完整文本和动作目录SHA。执行在准备阶段与permit后重新定位、核验每级Parent和唯一应用连接；对象路径/索引仍不能证明提供方永久对象代次，禁止将证明跨视图持久复用。需要logind证明目标进程属于本图形会话，不能借X11的另一套PID证明绕过。

文本设置要求EditableText/Text与enabled/sensitive/showing/visible/editable；单选要求Selection父容器及可选子节点，拒绝多选父容器；调用仅接受唯一机器动作click/press/activate。文本最多512字符，超长控件保持可读但不提供动作。密码及祖先密码路径不读取正文，也不提供动作。

输入守卫另持有经过既有会话、Xorg进程、socket对端及Cookie核验的连接。要求XI2>=2.2、唯一启用主键盘/指针对、能证明释放的键鼠状态；触摸/手势、浮动键盘、多个主设备或未知类别保持不可用。只订阅原始事件与设备变更并丢弃载荷，跟踪当前焦点窗口的焦点/结构变化；不监听输入文本，不把keycode、坐标、设备名写日志。固定250ms静止期前后复核，事件/焦点/设备/锁屏变化、按键未释放、超限或协议异常均拒绝或停止后续动作。

辅助进程准备完成仅发送ready；主进程再次核验动作审批、运行、策略、资源租约、视图期限与会话代次后才发permit。初始动作帧不表示已派发。permit后重新核验完整节点证明和输入守卫才调用一次AT-SPI变更RPC，不设置焦点、不使用XTest/全局输入、不重试。当前焦点窗口只用于变化监测，未按PID/坐标映射到AT-SPI目标。

RPC前失败为denied；发出后失联、用户介入、目标漂移、清理失败或无后置证明为unknown。SetTextContents必须完整回读匹配，SelectChild必须回读IsChildSelected；DoAction返回也保持unknown。unknown进入现有核查账本，不自动重发。输入监测不可用时观察保留只读树、不暴露动作候选；Wayland始终如此，不能由XWayland或环境变量降级获得控制权。

该链路仍遵守宿主5秒总期限、辅助进程4秒预算、最多4项实际工作/2个helper和64KiB帧限额；初始帧与permit合计也受限。事件单条<=4KiB、最多256条、连接接收<=1MiB；所有进程/总线/socket回收由原生命周期持有，回收未证实保留容量、数据库和实例锁。主进程不调用X11或AT-SPI。

Linux实机先在隔离账号下打开临时GTK/Qt测试窗口，再通过V2工作台选择AT-SPI窗口，逐次观察、设置临时文本、人工审批并核对回读；另测列表单选和只在隔离窗口调用。无自动批量输入。分别验收密码、大树、目标退出/重建、审批撤回、用户输入、焦点变化、锁屏/睡眠、设备热插拔、未知结果和回收。当前Windows注入用例和浏览器不替代这些实测，也不代表输入守卫在实际Xorg/设备布局上已可用。

依据：[AT-SPI Action](https://github.com/GNOME/at-spi2-core/blob/main/xml/Action.xml)、[EditableText](https://github.com/GNOME/at-spi2-core/blob/main/xml/EditableText.xml)、[Selection](https://github.com/GNOME/at-spi2-core/blob/main/xml/Selection.xml)、[XInput协议结构](https://xcb.freedesktop.org/manual/xinput_8h_source.html)、[XISelectEvents](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man3/XISelectEvents.3.html)。

## Wayland Portal：授权、受控FD与窗口单帧

生产链路为`DesktopPortalService → PortalBackend → 固定Python长驻helper → PortalSession/SignalBus → 已核验的本机用户总线`。应用层保存短期Agent/view/boot/grant UUID归属，先持久登记请求审计，再通过prepared/permit复核视图后弹出系统授权；授权成功审计提交前API不报告authorized。等待用户选择在事务外。

沿用logind、socket实际对端和进程创建身份核验，固定Portal唯一服务；不自动启动服务或使用任意环境总线地址。先订阅精确sender/path/signature再调用，处理Response早于方法返回；拒绝伪造handle、签名、跨连接或多窗口结果。只请求一个WINDOW、隐藏光标、persist_mode=0，无restore token。要求ScreenCast>=6及有效pipewire-serial；不将可复用node ID当作永久身份。提供旧版Portal的发行版可能暂不可用，必须如实报告portal_version_unavailable。

每项选择最多90秒，helper总生存150秒，prepared放行5秒、心跳中断8秒关闭。主机共享桌面2个实际helper容量，Portal最多4个实际入场、32份有界元数据；每个Agent串行。Portal IPC2单帧16KiB、总256KiB、最多512事件；子进程不读数据库，原生对象、总线路径、stream信息和异常正文不进HTTP或审计。

监听Session.Closed，并复核活动会话、锁屏、Portal唯一连接与创建身份。显式关闭、换视图、重查权限、视图淘汰和实例排空都撤回会话；关闭Request/Session并回收实际进程。回收失败继续保留宿主与容量、显示cleanup_pending；后续关闭重试回收，再结算审计，不提前关闭数据库与实例锁。

所有者API为POST/GET/DELETE `/api/v1/agents/{agent_id}/desktop/portal`。POST只接受`{"consent":true,"view_id":"<UUID>"}`；GET带view_id；DELETE带view_id和grant_id。拒绝客户端提供native handle、PID、monitor类型或restore token。新增POST `/api/v1/agents/{agent_id}/desktop/portal/capture`，只接consent=true、view_id、grant_id、session_id。客户端不能提交FD、node ID、serial或输出路径。授权审计未提交、视图变化、跨Agent会话、依赖缺失或撤回时拒绝读取；每个共享最多4帧，同Agent串行，最多4个在途保存。input_authorized始终false。

共享回执的 `authorization_pending` 是派生的确认状态：仅在原生共享已经 active、应用授权尚未成立、且没有终态或回收义务时为 true，不新增持久状态或授权来源。`phase` 保留原语义，不能单独替代 `authorized`。工作台此时显示“正在确认共享授权”，继续禁用保存；后台审计完成后，所有者显式查询才更新本次回执。系统仍等待用户选择、审计失败或正在回收时不标记为该确认状态，截图许可仍由原有完整检查决定。

主进程和HTTP不接触FD或GStreamer。只有专用Portal用户总线协商Unix FD；已核验的同一连接发送OpenPipeWireRemote，并按唯一sender、reply_serial、h签名、零索引和恰好一个描述符收取。设置不可继承，拒绝截断/多余描述符；部分帧、迟到响应、错误与关闭都保留清理义务。系统/AT-SPI普通连接仍禁止FD。

helper内单个有界工作线程调用固定GStreamer pipeline：pipewiresrc → 尺寸限制 → videoconvert → RGB → appsink。source只设置Portal FD、完整十进制pipewire-serial和dont-reconnect，不设置可复用node ID或默认远端；只接受必要属性均存在的插件。上游缓冲2、appsink 1、转换线程1；单帧最多3秒采样，边长4096、1600万像素、原始64MiB、PNG16MiB。RGB步长/偏移、平面、映射、均匀内容和NULL状态回收均核验；未能证明回收时不发布成功。

PNG只写宿主创建的私有UUID目录/固定frame.png，独占创建并fsync；大载荷不进IPC。宿主核验PNG/指纹，再由既有对象服务共同提交对象、会话引用与审计。v2_0025新增pipewire_window来源，保持不可变、作用域外键及遗忘触发器。取消HTTP等待不提前释放实际工作；撤回或会话内容变化使帧失效。文件清理失败保留暂存义务，关闭返回未完成并保护存储与实例锁。

工作台仅手动选择、查询、保存和结束共享，不自动轮询或抓图。预览/移除引用复用现有截图区；离开、换Agent/视图、重查权限均撤回。画面不自动发给模型，也不授权键鼠输入。状态是上次查询结果，每次保存仍由服务端重新核验。

Linux原生授权样例使用隔离Wayland账号和临时窗口：

```sh
uv run --no-sync python agent/scripts/accept_linux_portal.py --consent-window-share --hold-seconds 1 --output temp/private/linux-portal-unique
```

这个命令只检查授权，不读取像素。consent_sample_passed仅说明该次授权样例持有结束仍有效且资源已回收，不代表业务审计或平台通过。本机没有执行此原生命令。

需要安装帧组件，并显式增加像素授权才检查一帧：

```sh
uv sync --frozen --extra desktop-linux --extra desktop-wayland
uv run --no-sync python agent/scripts/accept_linux_portal.py --consent-window-share --capture-window --output temp/private/linux-portal-frame-unique
```

安装只在新的Linux验收环境进行。desktop-wayland锁定PyGObject 3.48.2及其依赖；另需系统GLib/GObject Introspection、GStreamer>=1.20的Gst/GstApp/GstVideo typelib、videoconvert/appsink和带fd/target-object/dont-reconnect属性的PipeWire插件。Debian/Ubuntu包名与实际插件属性在实机核验后记录；不假定系统自带GI可被虚拟环境导入。缺少或不匹配时明确降级，不改变核心安装依赖。

验收者在系统选择器中只选择临时窗口；分别检查颜色、缩放、多屏、最小化/销毁、锁屏、撤回和Portal重启。capture_sample_passed只证明该次原生单帧样例；输出仅尺寸/方法/大小/指纹，不把像素存入证据。读取结果未知时字段为null，不写成未发生。它仍不覆盖业务审计、工作台和完整Linux验收；这些必须另行真实执行。

依据：[ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)、[Request](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html)、[Session](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Session.html)。后续消费者依据[PipeWire远端FD示例](https://docs.pipewire.org/page_tutorial5.html)实现并验证，不以当前授权通过替代帧读取证据。

## X11采集源码合同与限制

`linux_x11_socket.py`只连接logind给出的`:display.screen`对应`/tmp/.X11-unix/Xn`，不使用环境DISPLAY或远程TCP。认证前核验socket及父目录、SO_PEERCRED、进程创建身份和logind归属；服务器必须为root保护的真实Xorg二进制及目录。Xwayland、代理、自定义服务器、无法证明会话或二进制时拒绝，不能以同UID替代。这里尚无真实Xorg运行结论。

随后读取当前用户私有Xauthority，只接受固定家目录`.Xauthority`或当前`/run/user/<uid>`下的无链接路径、独占普通文件及受限权限，最大64KiB。默认尝试gdm文件和`.Xauthority`；可显式指定受管范围内的XAUTHORITY作为文件提示，不能据此授权服务器。只接受本机hostname、显示编号和唯一FamilyLocal MIT-MAGIC-COOKIE-1记录；通配/远程/歧义记录拒绝。Cookie不进IPC、日志或报告。发行版使用其他布局/认证时明确列为限制。

`linux_x11_wire.py`是有限读取协议：setup/元数据64KiB，最多1024请求、2048消息，图像单次64MB、总接收66MB。逐帧校验长度/序号，错误不导出服务器正文。要求XRes1.2；EWMH列表仅为最多128个候选，所有权以XRes实际客户端PID为准，不读取_NET_WM_PID作为证明。跨用户、远程客户端无本机PID、隐藏/override-redirect或无法核验的目标拒绝。

截图要求Composite>=0.2且目标已有重定向pixmap。只命名该已有图像，不执行RedirectWindow/Subwindows；没有普通窗口GetImage、根窗口、全屏或其他窗口回退。订阅当前窗口结构生命周期，退出/隐藏/重挂载/尺寸改变等通知使结果失效。仅TrueColor标准RGB掩码、24/32位和32位行对齐；超过8192边长、1600万像素、16MiB PNG或均匀图像拒绝。RGB缓冲不含鼠标与管理器装饰，实际完整性需人工核验。

截图前后复核服务器、会话、客户端PID/创建身份及目标结构；临时文件必须为宿主生成的私有UUID目录中的`frame.png`，独占创建并fsync。宿主在现有文件执行器内校验指纹/尺寸/编码，再由对象服务原子登记对象、会话引用和审计；取消或过期不得发布迟到内容。资源由FreePixmap/连接关闭及既有宿主回收，失败显式报告。跨调用XID与相同元数据不能证明永久对象代次，不授权写动作。

工作台列表中语义和采集目标分别携带`can_observe/can_capture`，禁用不支持的操作；同一应用可能有两种目标，不自动映射或自动执行。AT-SPI失败不会因其可选连接残留阻塞已核验X11；清理失败仍阻止成功。

PTY、Secret Service 与服务部署还需分别完成本机闭环。Linux 容器只证明无桌面核心，不能替代 X11/Wayland 桌面、权限撤回、登录切换、睡眠恢复或 72 小时验收。

## 在 Linux 运行的步骤

使用已登录、非 root 的隔离测试账号及独立工作目录。默认入口为 V2，显式 V1 回退保留；下列独立原生检查工具不代表 Linux 正式支持。依赖安装在新验收环境中完成，不能修改正在运行实例的环境：

```sh
uv sync --frozen --extra desktop-linux
uv run --no-sync pytest -q -n 0 agent/tests/v2/platforms/linux/test_v2_linux_bus.py agent/tests/v2/platforms/linux/test_v2_linux_desktop.py agent/tests/v2/platforms/linux/test_v2_linux_acceptance.py
uv run --no-sync python agent/scripts/accept_linux_desktop.py --output temp/private/linux-probe-unique
```

第三条只做真实会话/能力诊断；`probe_completed` 表示诊断完成，能力是否可用以报告字段为准，不等于桌面验收通过。输出目录必须不存在。报告默认不含 PID、程序路径、会话 ID、节点正文或截图。

在临时目录手工打开单窗口 GTK 样例，填入非敏感测试文本；取得实际窗口所有进程 PID。明确选择该样例后读取：

```sh
uv run --no-sync python agent/scripts/accept_linux_desktop.py --fixture-pid <实际样例PID> --consent-read-ui --output temp/private/linux-observe-unique
```

工具只接受匹配 PID 的一个可见顶层窗口，记录前后身份核验、节点数量、密码节点数、截断、坐标空间和回收结果；不执行动作或保存原文。`sample_passed` 只证明该窗口的只读样例。应用无法证明会话归属时记录限制，不修改断言放行。多窗口、目标退出、锁屏、总线重启、权限撤回及阻塞仍需分别实测。

X11截图样例必须显式选择采集后端及像素读取；不要选择用户日常应用。只保留图像大小、方法与指纹，不将像素加入验收包：

```sh
uv run --no-sync python agent/scripts/accept_linux_desktop.py --fixture-pid <实际样例PID> --consent-read-ui --native-backend x11 --capture-window --output temp/private/linux-x11-unique
```

该样例要求已登录X11、XRes与已运行的Composite管理器；不自动开启服务或更改桌面。无Composite、用户服务会话、Xauthority布局、目标销毁、重挂载、遮挡、缩放、多屏和资源回收分别记录实测结果。`sample_passed`不代表完整Linux支持，也不替代工作台与数据库的真实集成验收。

## 回传证据

保留源码与锁文件指纹、构建/内核版本、发行版、架构、登录方式、X11/Wayland、辅助功能服务及 `dbus-next` 版本。分别回传专项原始报告、脱敏的 `evidence.json` 和复现步骤。另行记录是否真实测试了 socket 认证、对端变化、目标退出、密码控件、大树、阻塞超时、取消和残留进程。

不得回传聊天数据库、应用正文、屏幕图片、环境变量全集、D-Bus 内容转储、Cookie 或凭据。未执行的项目标为待真实验收；不以 Windows 注入测试或容器结果更新 Linux 支持状态。

协议依据：[logind D-Bus](https://freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html)、[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.Accessible.html)、[dbus-next 0.2.3 transport](https://github.com/altdesktop/python-dbus-next/tree/v0.2.3/dbus_next)、[RemoteDesktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)。

X11依据：[Xserver XRes客户端身份](https://github.com/mirror/xserver/blob/master/Xext/xres.c)、[Composite命名已有窗口图像](https://github.com/mirror/xserver/blob/master/composite/compext.c)、[Python-Xlib核心协议字段](https://github.com/python-xlib/python-xlib/blob/master/Xlib/protocol/request.py)。项目实现没有复制这些库的源码。

## 三平台统一系统凭据样例（尚未执行）

新`accept_native_credentials.py`通过生产NativeCredentials/受管worker执行，仅操作随机独立测试命名空间。只在已准备的隔离账号中显式运行；此处未调用任何系统存储。

```sh
uv run --no-sync python agent/scripts/accept_native_credentials.py --write-owned-item --output temp/private/native-credentials-unique
```

先将cleanup.json清理身份落盘并同步，再验证测试项不存在、写入随机值、完整回读、删除和不存在核验。不会改已有条目；证据不包含密钥、引用或系统存储内容。sample_passed只证明该次样例，不替代工作台审计、完整平台或真实账号验收。当前实测状态保持待真实验收。

遇到丢回复、锁定、权限或超时后，不在finally自动重做/删除；保留cleanup_required和未确认状态。确认实际进程已回收、OS解锁或提示已处理后，验收者显式清理该测试身份：

```sh
uv run --no-sync python agent/scripts/accept_native_credentials.py --cleanup <原目录>/cleanup.json --output temp/private/native-credentials-cleanup-unique
```

清理只接受本平台的固定格式随机token，不能传入生产命名空间或任意引用。原生解锁提示、拒绝/撤回、注销与睡眠恢复、原生服务阻塞及条目清理分别回传；不要回传密钥、系统凭据清单或数据库。Linux原生后端需要实际Secret Service及可访问的当前UID用户总线；headless使用显式FileVault，不自动生成同目录主密钥。Keychain/WinVault的权限与提示也必须在对应平台核验。


### 2026-11-22 容器/无桌面验收（Mac mini 上的 Linux VM）

本机（macOS 27.2.1 arm64）没有原生 Linux、显示会话或容器引擎。用 Lima 2.3.0（vz）起 Ubuntu 26.04.1 LTS（kernel 7.0.0-38-generic、aarch64、Docker 29.10.0），从 `deploy/v2/Dockerfile` 构建镜像 `omni-v2:check2`（image `sha256:3899bd7c…`），在 VM 内运行 `scripts/check_v2_container.py`：**passed=true、cleanup_complete=true**；fresh 与 reopened 两阶段均为 nonroot/private_home/owner_auth/headless，同一 agent/instance 跨重启保留、boot_id 更新、schema v2_0052、build_id 匹配；容器离线（network none）运行。

过程中修复一个真实缺陷：`serve_instance` 在 SIGTERM 时因 uvicorn 恢复旧 handler 并重发信号而退出 **143**，导致容器 check 报 `container_shutdown_incomplete`；现在服务自持 SIGTERM/SIGINT handler，优雅退出返回 0。受影响离线专项 57 项通过（service runtime/lifecycle/entrypoints/operations CLI）。

未验：X11/AT-SPI 控件身份、XRes 前台后验、用户介入与撤权（无 X11 会话）；Wayland RemoteDesktop/EIS 关联、暂停与撤权（无 Wayland 会话）；图形样例、锁屏、睡眠、热插拔。容器 check 仅为无桌面/离线/持久/重启证据（full_suite_run=false），不代表 Linux 原生 GUI 支持。证据：[容器验收](../../../evidence/v2-linux-container-2026-11-22.json)。

### 2026-11-22 Linux 原生专项与 headless 诊断

在 Lima vz 的 Ubuntu 26.04.1 LTS（aarch64、kernel 7.0.0-38、Python 3.12.16、`desktop-linux` 额外依赖）中运行 `test_v2_linux_bus.py`、`test_v2_linux_desktop.py`、`test_v2_linux_acceptance.py`：**77 passed，2.56s**。随后执行 `accept_linux_desktop.py`（无 DISPLAY/Wayland/D-Bus 会话）：`probe_completed`、`cleanup_verified=true`；能力字段为 `window_identity/accessibility=unknown/desktop_native_unavailable`、`screen_capture/foreground_control=desktop_adapter_not_implemented`，**未把缺少 DISPLAY 报告为已证明 headless，也未声称 GUI 支持**。容器侧证据见上一节（`check_v2_container.py` passed）；X11/Wayland 真机会话仍缺，G3/G4 的控件身份/前台后验/暂停撤权未验。证据：[Linux 专项与探针](../../../evidence/v2-linux-native-tests-2026-11-22.json)。

### 2026-11-23 X11 会话尝试与失败关闭

在 Lima VM 中安装 LightDM + openbox + Xorg（vkms 虚拟 DRM），`loginctl` 得到真实会话：id 25、user `omni`（uid 10001）、**Type=x11、Class=user、Seat=seat0、Display=:0**，`CanGraphical=yes`；GTK3 样例（含 password entry）在会话内运行。以 `accept_linux_desktop.py --fixture-pid <pid> --consent-read-ui --native-backend x11 --capture-window` 分别从 lima shell 和会话内（openbox autostart）运行，均返回 **`linux_sample_read_unavailable`、`session_kind=unknown`**：工具未能证明 X 服务器/目标属于当前活动会话，因此在读取前拒绝（失败关闭）。未尝试 AT-SPI 写动作或前台输入，也没有产出截图。**G3 仍未验证**；需要能建立 Xorg/session 证明的真实桌面 X11（或由 A 合同决定是否接受 display-manager 启动的 Xorg）。证据：[X11 尝试](../../../evidence/v2-linux-x11-probe-2026-11-23.json)。

进一步诊断：seat 会话为 **29**（Type=x11、Seat=seat0、Display=:0、Active=yes），而 Xorg 进程在 `system.slice/lightdm.service` 中，**不属于任何 logind 会话**；会话内探针仍为 `session.kind=unknown`。若 Inspector 的 X11 证明要求 X 服务器归属活动会话，那么 LightDM/GDM 这类真实桌面也会同样不满足——这更像合同缺口而非 VM 特例。需 A 决定：接受 display-manager 启动的 Xorg（以 seat/会话活动性证明），还是要求 startx 式 Xorg 位于用户会话内。

会话启动后等待 45 秒再跑验收，结果仍为 `session_kind=unknown` / `linux_sample_read_unavailable`，排除“autostart 过早”假设。logind 本身显示 `User.Display=29`、`Sessions=29 24`、`State=active`，会话 29 为 x11/user/seat0/:0。下一步需确认 Inspector 实际查询的 PID（worker 还是 Xorg）及其 `GetSessionByPID` 返回值，再决定是代码缺口还是启动方式问题。
