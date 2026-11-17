# Linux 显式前台控制开发

整个Linux通用前台控制仍在开发。已接通人工聚焦AT-SPI编辑器末尾Unicode追加，以及明确提供原生窗口XID的节点逐次按键（当前Avalonia属性）；鼠标、通用GTK/Qt窗口关联和Wayland输入继续开发。文本追加保持直接寻址AT-SPI，不增加原生窗口关联要求。以下历史节点与当前业务接线分别列明。

`platform/linux_foreground.py` 的 `WindowFocus` 持有外部 `WindowLeases` 和自己拥有的独立 `X11Input` 连接。先核对原窗口持续代次，再建立已有物理输入释放/拓扑监测和 250ms 静默窗口。两条连接必须来自同一个 SessionInspector，具有相同 Xorg 进程、socket inode、screen/display 及 root。另在监测连接核验 XRes1.2，实际焦点接收客户端的 PID、进程创建身份、可执行文件和 UID 必须属于所选窗口。

同一 PID 不能替代窗口关系：GetInputFocus 的实际窗口必须等于所选窗口，或沿 QueryTree 的有界父链到达所选窗口。最多 16 层，每次最多 4096 个子项；root、循环、链外同应用窗口、远程客户端、窗口管理器代理或损坏响应拒绝。控件坐标、窗口标题和应用自报 PID 不参与这个判定。

原输入守卫订阅焦点窗口的 FocusChange/StructureNotify；新增一次性的祖先链订阅只加 StructureNotify，不覆盖焦点窗口已有订阅。订阅前登记允许的窗口，屏障期间接到祖先重挂载/销毁等通知也会锁存输入变化。之后每次检查都重验连续窗口身份、物理输入/焦点、服务器和完整父链，并再做输入屏障。检查失败后即使恢复为相同元数据也不能复活证明；准备失败不能作为已准备证明使用。关闭只回收自己拥有的监测连接，不关闭外部窗口租约；失败继续返回未完成。

18 项新增离线回归覆盖实际二进制协议的直接/子孙焦点、同 PID 链外窗口、循环/深度/根/坏长度、不同客户端、服务器/会话不符、订阅中重挂载、未聚焦祖先变化、输入按住、进程复用、窗口代次变化和失败锁存。原输入、语义动作、持续租约和架构调用方另作专项。均使用注入 Xorg/logind，不发送真实输入，也没有 Linux 原生验收结论。见[证据](evidence/v2-linux-foreground-proof.json)。

后续仍需把控件身份与所选窗口的关联、逐次业务授权、Unicode/键映射、鼠标遮挡和派发确定性接入实际 worker。AT-SPI 的对象树与 Component 坐标接口不能单独证明 X11 窗口句柄，不能因为本批焦点证明通过而启用通用输入。Wayland 控制另走独立 RemoteDesktop 系统许可，不能复用 ScreenCast 读取授权。

## 物理窗口点的原生命中预检（2026-11-17）

`WindowFocus.prepare_pointer/verify_pointer`借用已核验焦点和连续窗口的同一输入监测连接，`linux_pointer_target.py`只接收选定原生窗口内的物理像素点，不接收任意屏幕坐标、不从AT-SPI边界推测缩放，也不移动或点击。输入点限窗口内且小于8192；根屏幕必须可用、零偏移/边框且能以有符号16位坐标表示。准备时和使用前均以原焦点/物理输入/窗口代次检查夹住读取，失败或取消使旧准备失效。

通过Xorg `TranslateCoordinates`把所选窗口的点变成根坐标，再沿服务器实际返回的命中子窗口向下读取，最多16层、每层256子项。`QueryTree`核对根、真实直接父子关系和堆叠清单；长度、重复、循环、链外子项、越界和不同screen均拒绝。不是按矩形选一个同PID窗口：Xorg实现会按真实堆叠顺序结合bounding/input shape返回命中子窗口，因此透明输入区域可穿透，实际覆盖所选窗口的其他窗口拒绝。

窗口管理器外框可属于另一进程，但命中路径必须经过原先选定窗口；从该窗口到实际接收子窗口的每一级都通过XRes核对真实客户端PID、进程创建身份、可执行文件与UID。支持本进程的可见InputOnly接收子窗口，不因它不是键盘焦点而改换焦点。双读核对点、窗口/子项、几何和客户端指纹，观察订阅的事件掩码不参与指纹；准备后的再次读取仍必须相同。

原输入守卫新增一次性的命中路径订阅，保留已有FocusChange；在根及路径父节点订阅SubstructureNotify，在路径上订阅StructureNotify，先登记允许的父节点再发订阅。新建/重排/映射的未知兄弟窗口也会锁存变化，不把它误当本次输入或静默忽略。订阅只影响本连接；关闭沿原WindowFocus回收监测连接，外部窗口租约与会话仍由原所有者持有。

这完成当前物理点的原生读取链，不证明AT-SPI控件与物理坐标的换算、主动/被动输入抓取、事件归属或应用处理结果。按钮映射现接下节的独立只读预检。业务`click`仍未开放；后续须接指针配置与逐次许可、移动/按下/抬起的真实归属和释放、控件命中及应用后验，不能把预检通过当成允许派发。

60项新离线二进制协议/完整窗口绑定回归与252项原输入、窗口、按键、文本、语义worker及架构/类型合同，共312不同专项通过；修改3运行文件strict零错误/警告，360仅登记，未整范围或全量。初轮50专项通过，唯一冗余cast类型诊断及原源码留档后修正并扩充边界用例。没有真实Linux或鼠标输入。见[本批证据](evidence/v2-linux-pointer-target.json)。协议依据：[X11 TranslateCoordinates](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man3/XTranslateCoordinates.3.html)、[Xorg实际命中实现](https://github.com/XQuartz/xorg-server/blob/master/dix/dispatch.c)的`ProcTranslateCoords`。

## 同连接两级主按钮映射预检

`WindowFocus.prepare_button/verify_button`在原生物理点和XTEST来源已经准备后，读取监测连接实际ClientPointer，要求它仍为已核验的pointer master；不选择或修改ClientPointer。XI2设备完整拓扑必须保持，所选slave/master各有唯一ButtonClass，实际按钮数均为1至255。分别使用XI1 GetDeviceButtonMapping读取两个完整表，再以核心GetPointerMapping交叉核对master。核心QueryPointer还需同屏、正确根窗口且逻辑修饰/按钮掩码为零。之前的输入守卫仍逐次核验所有设备的释放状态。

Xorg先将slave物理按钮码通过slave表映射为master事件的detail，再应用master自己的表。准备计算最终逻辑主按钮1的真实物理候选，不硬编码物理1。零表示禁用，超出master表的中间码不成为候选；多个物理别名映到主按钮时固定选择最小实际码。完整两表、长度及设备ID均进入指纹，未使用按钮变化和两表同时变化但效果相同也使旧准备失效；协议填充不进入身份。

双读之前，在同一根窗口订阅两个具体设备的XI1 DeviceMappingNotify。其事件号从QueryExtension首事件加11计算，事件类携带真实设备ID；解析器先于void订阅发布，随后ClientPointer查询作为屏障。核心MappingNotify也纳入原`changed`锁存。不能仅依赖核心通知：Xorg对其他slave的映射变化可能只投递XI1通知。订阅途中、读取之后或映射恢复后的通知都使旧计划失效；未绑定设备、坏请求类别、读取失败和取消均不能恢复旧准备。没有增加自身鼠标事件豁免，也没有改变外部窗口连接归属。

这是按钮表预检，不证明XKB按钮动作、指针变换/轴/反馈及来源切换复制，也未派发移动或点击。接下来补这些状态，再接逐事件归属、按下/抬起释放、AT-SPI物理点关联与应用后验。43项新离线专项和341项输入/焦点/物理点/来源/按键/文本/语义/桌面及架构/类型调用方，共384不同节点通过；3修改运行文件strict零错误/警告，361仅为登记范围，未整范围或全量。首轮38项通过及之后1项测试收集错误（参数名与pytest保留名冲突）的原源码/报告均留档；更名修正没有改变运行实现或放宽断言。没有真实Linux或鼠标输入，见[本批证据](evidence/v2-linux-button-mapping.json)。

协议依据：[Xorg slave→master事件构造](https://github.com/XQuartz/xorg-server/blob/master/mi/mieq.c)、[master事件处理](https://github.com/XQuartz/xorg-server/blob/master/Xi/exevents.c)、[实际按钮表回复](https://github.com/XQuartz/xorg-server/blob/master/Xi/getbmap.c)、[映射变化投递](https://github.com/XQuartz/xorg-server/blob/master/dix/inpututils.c)、[ClientPointer选择](https://github.com/XQuartz/xorg-server/blob/master/dix/events.c)和[XCB XI协议结构](https://xcb.freedesktop.org/manual/xinput_8h_source.html)。

## 同连接XKB按钮动作与环境预检

`WindowFocus.prepare_pointer_actions/verify_pointer_actions`只接受已准备的原生点、XTEST来源和两级按钮计划；计划同时保留从XI2与实际映射核对得到的两个按钮数。XKB协商复用同一连接，不改变布局、设备或已有订阅。键盘与指针读取可任意先后准备；后续协商须仍是同一事件族，不能与XI1按钮映射通知重叠。

先核对实际核心键盘属于已验证pointer master的配对键盘，复用既有XKB释放状态及控制检查；锁存/组/修饰状态、MouseKeys等不支持控制不能成为普通鼠标准备。再为具体master和XTEST slave分别订阅ExtensionDeviceNotify，解析设备集合在两个void请求前登记；保留键盘的原状态/控制/映射等订阅。通知到达后只锁存变化，没有自身鼠标事件豁免。

对两个设备分别使用GetDeviceInfo，要求完整ButtonActions能力、正确设备、完整请求范围和实际总按钮数、无额外LED负载及独立键盘状态。计数字符串和返回范围均有界，回复长度精确；完整按钮集合只接受NoAction。Xorg会裁去前后NoAction，空动作数组与已分配但全为NoAction的数组可能分别返回起始0或按钮总数、长度0；二者及显式NoAction规范化为同一效果，填充与无动作的未使用字节不进入指纹。设备名称、类型、能力和反馈身份仍进入身份；其他实际动作、配置变化或同效果之外的元数据变化不能重绑旧准备。

动作准备双读，使用前重读，原窗口/物理点/设备/映射和XKB状态/控制在其前后复核。失败和取消均使旧准备失效，外部窗口连接/Session归属不改变。组合链实际暴露旧InputWire的1024请求上限不足；专用监测现保留固定8192请求及8448消息的连接寿命额度，InputWire原1MiB累计接收（普通Wire为66MB）、单回复/请求上界、500ms回复超时和独立256事件上界不变。普通窗口读取Wire仍保持1024请求/2048消息；不按每次操作重置或回绕序号。

本批42项新回归与558项X11/XKB/输入/窗口/按键/文本/语义及架构/类型调用方，共600不同专项通过（先修复的正向节点0.84秒，其余113.35秒）；6修改运行文件strict零错误/警告，362仅登记，未整范围或全量。首轮26通过/14旧请求额度失败及1条名称长度Unknown类型诊断、原源码均留档；当前专项固定源码前后核对SHA，默认离线闸门保留。没有真实Linux/鼠标输入。见[本批证据](evidence/v2-linux-pointer-actions.json)。

当前实现仍不证明指针变换、轴/反馈、来源切换复制、输入抓取、AT-SPI物理点关联或应用后验，不派发移动/点击。随后继续这些开发项；全量留整个V2开发冻结后。协议依据：[Xorg GetDeviceInfo及设备动作更新](https://github.com/XQuartz/xorg-server/blob/master/xkb/xkb.c)、[XCB XKB二进制结构](https://xcb.freedesktop.org/manual/xkb_8h_source.html)及[Xorg指针类别复制](https://github.com/XQuartz/xorg-server/blob/master/Xi/exevents.c)。

## 原生指针变换、轴和反馈配置

`WindowFocus.prepare_pointer_configuration/verify_pointer_configuration`在已有物理点、来源、两级按钮和XKB动作证明之后，继续读取同连接的真实指针配置。`PointerState`保存master与XTEST来源各自的按钮来源/标签、轴、滚动元数据、反馈及矩阵；两个设备保持独立，不把当前master状态等同于下一次来源切换后的状态。

XIQueryDevice完整拓扑仍须匹配原守卫，按钮数与已核对的完整表一致。两个指针都须有唯一按钮类和真实0/1轴；轴号唯一且小于32，模式明确为相对或绝对，类别长度精确。XTEST来源各类必须明确指向自身，master各类来源须是当前master或启用且挂接该master的实际slave pointer。标签、源ID、分辨率、模式与完整FP32.32上下限均进入不可变配置；整数表示保留分数精度，允许服务器的无范围标记。滚动轴要求已存在的唯一轴、明确方向、受支持标志及非零增量，完整有效字段保留。瞬时轴位置和文档填充不进入配置。

XI1 GetFeedbackControl最多读取8个明确的PtrFeedback，要求独立ID、精确12字节条目及非零分母；完整加速分子/分母/阈值保留，不预设默认值，不修改反馈或启用相对移动。已有Coordinate Transformation Matrix及FLOAT原子只查询，不创建；两设备的属性须完整FLOAT/32、9项、无剩余数据。所有值有限且矩阵可逆，负零规范化；合法非默认范围、模式及矩阵均可被读取，不以单位矩阵替代实际配置。

准备时完整双读，使用前完整重读，前后继续核验原窗口/物理点/完整映射/XKB状态。既有XI2类别、属性及XKB设备动作变化继续锁存，不新增自身输入豁免。反馈通过实际重读核对；此层不承诺移动结果，也不授予派发权。读取失败或取消后，旧配置不能因恢复可见值复活，关闭保持外部原生会话的所有权。

本批43项新回归与364项直接窗口、来源、按钮/XKB、按键、文本、语义/桌面及架构/类型调用方，共407不同专项通过（89.12秒）；修改2运行文件strict零错误/警告，363仅登记，未整范围/全量。初轮39项通过与缓存rename WinError5警告、源码均留档；扩充真实master/source属性通知和8反馈上限后当前组无警告。固定源码前后核对SHA，默认离线闸门保留，没有真实Linux或鼠标输入。见[本批证据](evidence/v2-linux-pointer-config.json)。

Xorg的XTEST绝对移动使用桌面坐标，但仍经过轴范围缩放、变换和裁剪。当前配置读取为随后坐标换算、预期slave切换与RawMotion值核对提供真实输入；尚未计算派发坐标、执行移动/按下/抬起或证明抓取路由、AT-SPI控件物理点及应用结果。业务`click`继续关闭，整个V2仍在开发。协议依据：[XTEST实现](https://github.com/XQuartz/xorg-server/blob/master/Xext/xtest.c)、[坐标缩放与变换](https://github.com/XQuartz/xorg-server/blob/master/dix/getevents.c)、[XI2真实轴回复](https://github.com/XQuartz/xorg-server/blob/master/Xi/xiquerydevice.c)、[反馈回复](https://github.com/XQuartz/xorg-server/blob/master/Xi/getfctl.c)与[XCB XI结构](https://xcb.freedesktop.org/manual/xinput_8h_source.html)。

## 原生布局与鼠标移动坐标计划

`WindowFocus.prepare_motion/verify_motion`在已有原生点与完整指针配置上准备只读`MotionPlan`。实际X11 setup现在保留根屏幕数；当前移动计划要求一个真实可核验的root，以及该root上可用的RandR 1.3以上资源。不是只凭根窗口大小或广告root数推断桌面：旧Xinerama可能隐藏多个实际屏幕，Xorg的xf86 RandR初始化明确不为这种布局建立原生资源。普通RandR多输出仍可使用，同一root上多个活动CRTC的回归已覆盖；旧Xinerama与多root输入布局继续开发，不影响原有窗口读取。

准备读取当前根几何、GetScreenResourcesCurrent和每个实际CRTC。资源/模式/名称及ID集合均有界、长度精确且无重复，CRTC回复必须成功，模式/输出关系和旋转明确；目标物理点须由活动扫描输出覆盖，不能落在输出间隙。模式、时间、名称及完整CRTC有效字段进入布局身份，回复序号与填充排除。先绑定RandR事件族再选择根窗口通知；所有通知均使旧计划失效，没有初始通知或自身输入豁免。准备双读、使用前重读夹在完整配置/点守卫中，失败或取消锁存。

坐标计算使用实际source轴上下限与完整FLOAT变换。精确有理矩阵提供逆投影及整数像素约束，再按Xorg原生双精度顺序模拟桌面→设备范围、归一化变换、裁剪及设备→桌面；FP32.32预计值按向下取整编码。候选必须是两个有符号16位整数，预测截断后的根像素必须仍为原点；包括负请求、旋转、镜像、非单位/投影矩阵以及有界/无界轴，不将raw和processed误作同一数值。

先查逆投影中心附近候选，再按整数不等式逐行搜索至多两个65536行域，避免二维穷举或无限浮点试探。中心附近舍入找不到时仍搜索边界候选；不可达整数网格、溢出、奇异/零归一化、X/Y滚动混入或无法预测的值拒绝，不把相邻像素当作成功。实际输入包、来源切换及RawMotion匹配尚未接入，预计值不等于观察到原生事件，也不授予派发许可。

为后续移动与成对点击保留连接额度，完整配置使用前复核现一次执行原窗口/焦点/释放守卫，再分别实际重读物理点、XTEST来源、两级按钮表、XKB动作/状态和完整配置，最后再次执行原守卫。各读取仍比较自己的旧快照，任何失败或取消使旧证明失效；没有缓存跳过叶读取、清空变化标志、扩大连接额度或减少原生通知。结果只在末次守卫通过后返回；完整夹具的单次复核不超过256请求，三次移动计划重验仍在原8192请求寿命内。

本批40项新回归与500项直接X11/输入/窗口/配置/XKB/成对按键/文本/语义及架构/类型调用方，共540不同专项通过（9.42秒+106.47秒）；10修改运行文件strict零错误/警告，365仅登记，未整范围/全量。首轮测试参数名与pytest保留名冲突、其后排版导致冻结哈希拦截，旧源码/清单/原报告均留档修正；运行实现一致，未放宽校验或断言。默认离线闸门保留，没有真实Linux、鼠标或全量结论。见[本批证据](evidence/v2-linux-pointer-motion.json)。

协议依据：[XTEST绝对请求](https://www.x.org/archive/X11R7.5/doc/Xext/xtest.pdf)、[Xorg坐标缩放与raw生成](https://github.com/XQuartz/xorg-server/blob/master/dix/getevents.c)、[原生屏幕原点归一化](https://github.com/XQuartz/xorg-server/blob/master/hw/xfree86/common/xf86Cursor.c)、[旧Xinerama与xf86 RandR资源边界](https://github.com/XQuartz/xorg-server/blob/master/hw/xfree86/modes/xf86RandR12.c)和[XCB RandR结构](https://xcb.freedesktop.org/manual/randr_8h_source.html)。业务`click`仍未开放；继续来源复制、逐事件归属/释放、控件物理点绑定与逐次许可/应用后验，整个V2尚未完成。

## 内部移动派发、RawMotion归属及指针复制

`WindowFocus.motion_packet()`把已准备的原生点、RandR布局与完整指针配置接到单次内部`MotionPacket`。同一XTEST连接上只发送一个36字节绝对移动请求，明确原root、零延迟及既有有符号16位坐标。对象不可重放；发送前序号须仍是准备时的序号，键盘/移动租约互斥。业务`click`尚未接入，不将内部移动回执当作应用操作成功。

首字节前登记真实连接租约并保留请求序号。每次非阻塞发送都累计实际字节；不完整请求取消后关闭该流丢弃，不能补齐一次新移动。完整请求只允许原所有者发QueryPointer屏障。结构有效的回复证明请求已处理，可结清移动传输；即使位置、root、same-screen、逻辑掩码或原始事件不符，也不能据此报告移动验证成功。移动没有本动作按住的按钮，不发送按键式抬起后缀。坏回复、超时或回读不明时保留原连接/租约/任务，不重查、不重放、不假报关闭；取消等待不取消原屏障任务，外部窗口连接继续归原所有者。

`MotionEvents`只接受该移动请求序号下、source再master的两条RawMotion17；设备/来源、detail0、flags0和配对时间均须精确。掩码长度1至8个word，仅0/1轴有效，其余全零；先读取processed再读取raw的两组FP32.32，按有符号整数部分与无符号小数部分解码，分别匹配原计划，不把坐标当小端int64。缺失、额外、乱序、其他客户端共用来源、非零标志或错误值都使本次结果未验证并锁存变化。

`PointerSwitch`在发送前预计算唯一的来源类复制。DeviceChanged携带的是source按钮/轴/滚动描述，不能用它替代完整master配置。预测保留master实际按钮数、尾标签/反馈、完整映射及CTM，仅复制原生会复制的来源、前部标签、轴/滚动和反馈头；不支持的复制形状或重复反馈ID在输入前拒绝。最多一条同请求SlaveSwitch可出现在首RawMotion前，规范化后须精确等于原source，只有瞬时轴值和协议填充忽略；没有通知时实际旧状态必须仍成立。

完整Raw回执后，两次真实拓扑/类读取包住原生窗口/焦点/会话/释放/祖先链、命中路径、XTEST属性、完整按钮表、XKB动作/环境、两次反馈/矩阵及实际RandR布局核验。预期新拓扑只用于只读比较；全部成立后才同步采用预计算拓扑和配置。变化标志不清空，失败或取消不能采用暂态配置或重绑旧证明。

本批51新增及540输入/窗口/配置/按键/文本/语义/桌面与架构/类型调用方，共591不同专项通过（135.74秒）；10修改运行文件strict零错误/警告，368仅登记，未整范围/全量。首轮43过/1个XKB变化夹具失败及源码保留，修正实际返回的动作后原失败节点与新增复制用例4过；运行源码三轮SHA相同。全程注入，未真实Linux/鼠标/账号/设备/72h验收。见[移动派发证据](evidence/v2-linux-motion-packet.json)。

同时更正历史额度说明：专用InputWire原有`read`覆盖限制累计1MiB，普通Wire基类为66MB；历史[动作证据](evidence/v2-linux-pointer-actions.json)保留原值并添加明确纠正，本批没有提高或重置任何额度。协议依据：[原生DeviceChanged/Raw编码](https://raw.githubusercontent.com/XQuartz/xorg-server/master/dix/eventconvert.c)、[指针类别复制](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/exevents.c)、[QueryDevice类描述](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/xiquerydevice.c)与[slave/master事件复制](https://raw.githubusercontent.com/XQuartz/xorg-server/master/mi/mieq.c)。成对点击释放、AT-SPI物理点和逐次业务许可/应用后验继续开发，整个V2未完成。

## 内部成对按钮派发与真实释放

`WindowFocus.button_packet()`先重读原生点/来源/完整按钮表/XKB/轴反馈矩阵/RandR，准备已有的预计算来源复制，再读取当前QueryPointer。root、same-screen、原像素和零逻辑掩码均成立才创建单次内部`ButtonPacket`。请求只含实际物理按钮码的按下/抬起各36字节、零延迟/root/坐标/core device，不附带移动，不写映射。准备对象不等于业务点击许可，控件物理点与逐次业务后验仍待接续。

原生Xorg普通按钮在Raw slave/master对之后，分别经source/master指针处理产生两条配对键盘侧XKB StateNotify。`ButtonEvents`核对八步顺序、对应请求序号和源/主设备；RawButton15/16两条均保留物理码，flags与所有valuator位为零、配对时间相同。XKB只接受配对键盘、changed=PointerButtonMask、无修饰/组/兼容/lookup/grab变化、零请求cause和原事件4/5。source掩码取实际从映射（中间码大于5时为零），master为逻辑1；通常通知恢复输入physical/intermediate码，按下触发passive grab的原生早退还可能保留对应mapped码，均由已有两级表精确预测。没有一般键盘通知豁免；额外事件即使出现在自身八条回执齐全后也使整体失败。

首字节前登记租约并保留两个序号，键/移动/按钮租约互斥。未完整36字节按下的流关闭丢弃；完整按下之后只补剩余抬起后缀至72字节，发送故障和取消仍由原监测连接拥有。QueryPointer屏障先证明五个核心逻辑按钮均释放，再读取实际XIQueryDevice，证明全部启用设备的物理按钮释放，且原source/master身份/种类/挂接/按钮类成立。用户其他修饰键或位置/root变化使动作结果未验证，不能把已知按钮释放误记成业务成功。物理/逻辑仍按住时保留连接并允许后续只读结清；原任一屏障超时/读帧未完成时保留同一任务，不另起查询、不重放按下，不假报关闭。取消emit或close等待不会取消实际释放，外部窗口/会话仍归原所有者。

八条回执齐全、逻辑/物理释放与像素回读成立后，继续已有完整窗口/焦点/来源/映射/XKB/配置/RandR复制后验，全部成立才采用预计算配置/拓扑；变化标志不清空。移动与按钮分别重新准备、沿同一原生流组合已有专项，但没有因此开放AT-SPI业务`click`或声称应用已处理输入。

本批46新与591调用方/合同共637不同专项通过（153.83秒），6修改运行文件strict通过；370仅登记，未整范围/全量。首轮39过/1测试断言错误及源码保留，原失败和新增7节点通过，当前运行源码不变。全程原生协议/连接注入，没有Linux或真实鼠标结论。见[按钮证据](evidence/v2-linux-button-packet.json)。原生依据：[XTEST按钮生成](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xext/xtest.c)、[Raw及master映射](https://raw.githubusercontent.com/XQuartz/xorg-server/master/mi/mieq.c)、[指针XKB状态处理](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkbAccessX.c)、[XKB通知编码](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkbEvents.c)和[设备事件/物理释放](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/exevents.c)。

## 已接通的受限文本追加

`linux_text.py` 只向选定 AT-SPI 非密码可编辑对象执行一次 `EditableText.InsertText`。观察时须能完整读取最多 511 字符原文、核验 CaretOffset 位于末尾及 GetNSelections 为零；上下文进入原节点 actions 指纹，支持的节点才提供 `type_text`。不支持这些可选方法的提供者继续保留原语义动作。

执行沿用 DesktopActions 的人工单动作审批、观察绑定、单次消费和资源租约。固定 `linux_action` worker 对前台请求使用既有 grant/run/operation UUID 绑定的 ready/permit，初始帧不许可写入。最多等待 10 秒由用户手工聚焦目标窗口和控件；活动 AT-SPI 窗口和聚焦控件都需成立，再经过原 X11 物理输入/拓扑及 250ms 静默守卫。父进程重新核验业务权限后才发 permit，随后完整重读节点/原文/光标/选区与焦点，再执行一次插入。

每次最多 128 字符，原文加结果最多 512 字符。插入位置为 Unicode 字符偏移，长度参数为 UTF-8 字节数，保留中文和补充平面字符。没有键盘布局改写、剪贴板、焦点获取、删除选区或补发。提供者可能保留原光标或将其移到结果末尾，两者可接受；完整原文前缀和追加结果、对象/祖先/名称/动作及输入守卫必须匹配，才能报告 `text_appended`。写入前失败为 denied，派发后回执丢失/回读不符/用户介入为 unknown，均不自动重放。

Linux 桌面协议只放行 AT-SPI 的这一前台动作；`press_key`、`click`、X11/Portal 采集目标和 Wayland 均不能借此获得全局输入。能力探测单独核验 X11 输入守卫，清理成功后才报告这一受限能力；探测不 arm、不写。通用键鼠及原生窗口关联继续开发，不能把受限文本追加视为整个 G3 完成。

本批 25 项原生注入/通信/隔离 SQLite 回归、2 个浏览器场景、114 个当前调用方节点及 2 项类型合同守卫，共 143 个不同专项已通过。首份调用方报告原未参数化的浏览器节点由当前 `set_text/type_text` 两场景替代，不重复计数。修改 6 后端文件 strict 零错误/警告；没有真实 Linux 输入或全量。见[文本专项证据](evidence/v2-linux-text.json)。

文本协议依据：[EditableText.InsertText](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/EditableText.xml) 和 [Text](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/Text.xml) 的 CaretOffset/GetNSelections。真实 GTK/Qt 的焦点标志、Unicode 位置和插入后光标行为仍需 Linux 正式验收。

## 当前服务器的核心按键映射预检

`linux_keyboard.py` 已接到 `WindowFocus.prepare_key/verify_key`：在同一持续窗口、实际焦点客户端及输入守卫成立时，才从监测连接读取本次 Xorg 的完整核心键盘/修饰键映射。连接 setup 保留并验证真实 min/max keycode，GetKeyboardMapping 每键最多 16 个符号、GetModifierMapping 每组最多 32 个条目；只接受领域既有 11 个特殊键，不使用固定美式 keycode、字符猜测、组合键或布局改写。

目标符号须只出现在一个物理 keycode 行，首项匹配，其余项只能为同符号或 NoSymbol；仅修饰层出现、行内混合符号、多个候选或该 keycode 本身作为修饰键均拒绝。修饰键表须完全有界且有效，QueryPointer 只检查逻辑修饰键/按键掩码为零和同一 root，不保存坐标。物理释放不能替代 CapsLock 等逻辑锁存状态。

准备时以焦点/输入屏障夹住两次完整读取，之后使用前再次核对全表指纹。其他键或修饰键的变化、MappingNotify、物理输入、焦点或连续窗口身份变化都会使本次证明锁存失效；恢复成旧元数据不能恢复旧计划。映射对象不拥有外部连接，关闭仍由原 WindowFocus 回收自己的监测连接，并保留外部窗口租约。

这是只读核心映射预检。X11 核心规范明确映射由客户端解释，不能据此证明应用的 XKB action、实际事件路由或按键已处理。因此本批没有开放 press_key、Unicode 键盘生成或 XTest，不把这层预检当作通用输入完成。控件与原生窗口的可靠关联、XKB 及派发/介入证明仍需继续实现。

31 项新回归和固定副本 150 项受影响调用方通过，修改 3 后端文件 strict 零错误/警告。受限环境曾在 Windows asyncio 建立内部 socketpair 的 accept 阶段停住，尚未进入被测逻辑；保留诊断栈后同一离线专项在获准环境执行通过，未改测试/生产超时、网络闸门或断言。没有真实 Linux 输入或全量，见[映射证据](evidence/v2-linux-keyboard.json)。

## 同连接 XKB 服务器预检

核心按键预检现要求同一已核验输入连接上的 XKB 1.0。`linux_xkb.py` 协商 XKEYBOARD，固定使用核心键盘，不创建/切换设备；先安装事件解析器，再订阅键盘更换、映射、状态、控制和兼容映射的完整变化。任何这些通知都会使原证明失效，既有 256 事件/1 MiB 总接收上界保持，不记录输入内容。

服务器状态、控制及按键描述的 device ID 必须一致。读取 effective/base/latched/locked、compatibility/lookup/grab 修饰状态、组状态与按钮，要求均释放/无变换；不能仅凭核心 QueryKeymap 的物理释放推断。拒绝 SlowKeys、BounceKeys、StickyKeys、MouseKeys、Overlay 及未知控制，原设置不改动。控制读取精确 92 字节，前后重新核对有意义字段，协议填充不进指纹。

GetMap 只请求该候选 keycode 的 symbols/actions/behaviors/explicit/真实与虚拟修饰映射，最多 1024 字节、64 个符号。精确核对设备、键码范围、请求部件、各返回范围和长度；所有实际 XKB 符号位置必须为同一已审批特殊键，不推测客户端的键类型或组翻译。只接受隐式零动作或显式 SA_NoAction；重定向、切组、控制变化等动作、非默认行为及修饰绑定均拒绝。读取前后再次核对状态/控制，并与原双读完整指纹和焦点/输入屏障组合；恢复旧值不能复活旧计划。

这补齐服务器侧检查，仍不证明某应用如何处理按键，也不发送 XTest。窗口/节点关联、自身输入区分、实际派发与释放仍在开发。专项和类型结果见[XKB证据](evidence/v2-linux-xkb.json)，没有真实 Linux、原生输入或全量结论。

协议依据：[X11 Protocol](https://xorg.freedesktop.org/archive/X11R7.7/doc/xproto/x11protocol.html) 的 GetInputFocus、QueryTree、ChangeWindowAttributes、GetKeyboardMapping、GetModifierMapping、QueryPointer 和 MappingNotify；[XKB协议](https://xorg.freedesktop.org/releases/X11R7.7/doc/kbproto/xkbproto.html)与[XCB生成布局](https://xcb.freedesktop.org/manual/xkb_8h_source.html)；[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Accessible.html) 与 [Component](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/Component.xml)。

## 服务器虚拟输入设备身份

`linux_xtest.py`的`XTestSources`借用已arm的X11Input连接，由`WindowFocus.prepare_sources/verify_sources`夹在持续窗口/焦点核验中。只协商XTEST>=2.2、读取已存在的`XTEST Device`原子和XIGetProperty，不创建属性、禁用设备、修改挂接或发送FakeInput。仅有版本协商不会取得业务输入权。

原有有界设备解析现可返回设备ID、类别、挂接、启用和类别集，另保留原摘要接口；名字、按键/坐标内容仍不外传或存储。逐一查询全部最多64设备，包括禁用、浮动及主设备。属性缺失只表示该设备不是候选；属性存在时必须是完整INTEGER/8位/单项真值，不允许剩余字节、额外数据或类型猜测。两项候选必须为唯一启用slave pointer/keyboard，正确挂接唯一主设备对，且具备相应Button/Key类别。名称不能取代属性；额外伪标记、主设备标记、禁用/脱离候选、缺少类别均拒绝。

依据Xorg的[虚拟设备实现](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xext/xtest.c)，真正XTEST设备具有不可删除/改写标记，核心虚拟对不可删除；读取全部设备并拒绝重复，避免把另一个伪标记设备选作真实候选。协议格式来自[XCB XIGetProperty布局](https://xcb.freedesktop.org/manual/xinput_8h_source.html)。准备时双读并夹住原守卫，使用前再次重验；XI_PropertyEvent现在也被订阅并锁存失效，恢复旧值不能复活旧证明。协议填充不参与候选判断。

全部XTEST客户端共享同一虚拟设备对。因此来源身份本身不能证明原始事件由本动作产生。未安排本连接动作时，原始输入仍全部使守卫失效；没有按名称或共享来源豁免。下一节的内部派发器另外约束实际请求序号、方向和精确事件数量，不把这层身份预检当作应用已处理按键的证明。

24项新增回归及207项现有输入、焦点、键映射/XKB、文本/语义/桌面与架构/类型合同专项，共231不同节点通过；3个修改后端文件strict零错误/警告。结果来自固定源码及注入Xorg/logind，不代表Linux原生、真实输入或全量通过，见[来源专项证据](evidence/v2-linux-xtest.json)。

## 内部成对按键派发与释放责任

`WindowFocus.key_packet`在已准备的特殊键与虚拟来源上重新核验窗口、完整键映射和来源身份，创建`linux_keystroke.KeyPacket`。派发前序号必须保持不变、无旧租约或变化；同连接一次性安排两个36字节XTEST FakeInput请求，仅含既有特殊键code，按下后抬起、零延迟/坐标/core device，不写布局或修饰键。一次对象只能派发一次。当前业务worker、domain/IPC及UI未接入这一路，不因此获得通用按键能力。

Xorg的[XTEST请求处理](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xext/xtest.c)同步处理生成事件；[事件写出](https://raw.githubusercontent.com/XQuartz/xorg-server/master/dix/events.c)按接收客户端当前请求序号标记。结合[slave先于master的事件路径](https://raw.githubusercontent.com/XQuartz/xorg-server/master/mi/mieq.c)和[RawEvent布局](https://raw.githubusercontent.com/XQuartz/xorg-server/master/dix/eventconvert.c)，内部守卫只消费准确四条：按下请求的slave/master RawKeyPress、抬起请求的slave/master RawKeyRelease。同一审批code、来源ID、设备ID、序号、方向和顺序必须完全一致；每对时间相同，flags为零，最多8字mask且全零，不允许额外valuator内容。共享来源、额外/缺失/乱序、物理来源、不同键或SendEvent不能通过；其余原守卫保持锁存失效。通知内容不落盘。

实际发送采用非阻塞socket.send返回的字节数，派发前登记连接所有的租约；未结清流禁止普通查询插入。回收时，按下请求不足36字节则关闭流丢弃该未完整请求；已经发满按下时，只补72字节中尚未发送的抬起后缀，不能重复按下或重新发送完整包。发送及释放回读各保留0.5秒上界。QueryKeymap精确回读40字节且全部键释放才结清，仍按住可在同一已对齐流再读，绝不再次派发。

取消等待保留实际释放/回读任务和同一连接。活跃发送期间关闭返回false，避免两个发送者；回读发送/读取超时或协议位置不明后保留未结清身份，不另建查询掩盖丢失回执。`X11Input.close`先结清该租约，未完成不能声称连接回收成功。内部emit完成只说明传输事件匹配和释放读回，不生成业务ok、也不证明应用处理了按键。

该次成对内核节点尚未处理合法slave/XKB通知，后续实现见下文。29新回归及232现有输入/来源/焦点/键映射/XKB、文本/语义/桌面、架构与类型合同，共261不同专项通过；首轮注入流缺少关闭状态的失败及原源码留档后修复。4个修改后端文件strict零错误/警告，354为当时登记范围，未整范围复验/全量。无真实Xorg连接、真实输入或Linux原生验收，见[成对内核证据](evidence/v2-linux-keypair.json)。

## Xorg切换前的完整映射比对

Xorg在slave切换时通过[CopyKeyClass/DeepCopyKeyboardClasses](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/exevents.c)复制键盘映射，随后发送NewKeyboardNotify和DeviceChanged。仅核对候选单键不能约束其他按键、类型和修饰映射。`WindowFocus.key_packet`现要求主键盘与已核验XTEST来源的完整GetMap八部件、控制和释放状态相同，才创建内部按键包。

`linux_xkb_map.py`解析最多65536字节返回数据，精确验证真实setup范围、设备、full部件、类型与各按键范围、符号/动作总数、稀疏项唯一递增键码和尾部完整性。规范化类型、KTMap/Preserve、全部符号及动作、非默认行为、16个虚拟修饰绑定、显式/真实/虚拟修饰项；仅排除响应序号/设备身份、文档填充和NoAction的未使用字节。其他动作描述保持完整，未见部件不能推断为空。该解析器只输出SHA，不解释键类型、不改布局或发送输入。格式与顺序依据[XKB GetMap服务器实现](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkb.c)和[XCB布局](https://xcb.freedesktop.org/manual/xkb_8h_source.html)。

同一XKB连接先订阅来源键盘，再依次读取主/从/主/从，期间分别夹住无锁存状态与完整GetControls，任何通知或不一致都拒绝；来源回复不覆盖原核心设备ID。`KeyboardMapping`绑定完整身份，主从后来共同变化也不能更新旧计划；最后再复核原候选、窗口与输入守卫。读取取消/失败使证明锁存失效，尚无派发/释放租约或按键字节。原受限AT-SPI文本路径不新增这些要求。

该次仅补齐GetMap/状态/控制核验；其余复制配置与合法通知由下一节接续，生产worker仍未开放通用按键。37项新回归、29项原成对派发及232项受影响调用方/合同，共298不同专项通过，修改4后端文件strict零错误/警告；没有整范围类型、全量、真实输入或Linux原生验收，见[专项证据](evidence/v2-linux-keymap-clone.json)。

## 完整复制配置与本次键盘切换

`linux_xkb_copy.py`补GetCompatMap的全部符号解释/组修饰、GetIndicatorMap的定义/物理掩码、GetNames的组件/类型/级别/键/别名/组等，以及GetGeometry的当前完整几何。几何包含字符串、属性、颜色、形状/轮廓/点、section/row/key、overlay及五种doodad，不以整块带填充的内存生成身份。所有解析均受原64KiB回复限制，精确计数、引用与尾部；仅排除协议填充和NoAction未用字节。没有KeyNames时忽略响应保留的请求范围，不能误读为实际负载。几何只请求当前描述（atom=0），不查找命名几何或加载配置；先证明组件名称存在。无当前几何的明确空响应可用。

上述身份与GetMap、状态、控制一同主/从/主/从四读并绑定旧证明。新增IndicatorMap/Names通知订阅；共同变化、不完整名称、损坏几何和来源不一致均拒绝。依据[Xorg配置复制](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkbUtils.c)、[原生回复写出](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkb.c)及[XKB结构定义](https://sources.debian.org/src/x11proto-kb/1.0.3-2/XKBproto.h)。

`linux_key_switch.py`读取现有完整设备拓扑，要求主/来源键盘均只有一个相同KeyClass（除sourceid），来源指向实际XTEST设备；预计算只将主KeyClass.sourceid改为该来源的唯一允许指纹。Xorg内部last.slave与lastSlave不同，不能由QueryDevice断言必有通知。若发生切换，必须在首条RawKeyPress前按顺序收到同一按下请求序号的NewKeyboardNotify和XI_DeviceChanged：旧新主设备、旧新键码范围、XKB major/SetMap minor、Keycodes及存在几何时的Geometry位、SlaveSwitch原因、单一来源KeyClass完整负载均须精确一致。重复、缺失、逆序、迟到和其他来源保持介入，未派发时没有通知豁免。原始事件仍精确四条。

释放屏障结清后，再以两次拓扑读取夹住原完整配置身份核对，随后检查全部键释放、已核验连接和原焦点。只有这些检查全部通过，才采用预计算指纹；没有切换时仍要求原指纹。其他设备/名称/挂接/类别变化不能随主键盘来源一并被接受，独立changed永不重置。派发后核验失败或取消使计划锁存失效，不重放按下；实际释放与外部窗口租约的归属保持原规则。依据[Xorg复制与通知顺序](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/exevents.c)和[NKN字段](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkbUtils.c)。

内部emit仍只说明传输、释放及复制配置核验，不能报告应用成功。业务worker/domain IPC尚未接通；原生窗口与语义节点关联、逐次permit、应用结果及鼠标/Wayland输入仍继续开发。固定副本相关专项及修改7后端文件strict结果见[本批证据](evidence/v2-linux-key-switch.json)，当前没有Linux原生、真实输入、全范围类型或全量结论。

## 显式提供方窗口绑定基础

`linux_window_binding.py`的`WindowBinding`从已经验证的AT-SPI顶层对象读取`Accessible.GetAttributes`，只接受有源码依据的原生窗口字段。当前配置为`toolkit=Avalonia`及`avalonia-window-handle`的规范十进制XID；属性表最多128项/16KiB，单值512字符，拒绝十六进制、符号、前导零、Unicode数字、root、溢出和未知提供方字段。该属性在[Avalonia原生实现PR](https://github.com/AvaloniaUI/Avalonia/pull/22687)中只由`RootAtSpiNode`且平台`HandleDescriptor=XID`时提供，已核对合并源码；未证明任意稳定版本或现有应用已提供。

提供方声明不单独授权。模块在同一已核验X11 SessionInspector下建立自己的`X11Desktop/LeaseWire`，对明确XID读取XRes实际客户端；PID、创建时间和可执行程序必须与AT-SPI总线进程证明全部一致，再创建独立连续窗口代次和`WindowFocus`。原生焦点须确在该窗口或其有界子孙内；另一应用句柄、同PID链外焦点、窗口管理器代理和Wayland均拒绝。描述的双读夹住连续窗口/焦点核验，变更后恢复原值不能复活旧绑定，调用方修改原target也不改变已捕获身份。没有标题、几何、任意`window-id`或ACTIVE位猜测。

绑定只拥有新建原生窗口连接及输入监测，不拥有外部AT-SPI/logind会话。准备失败或取消仍保留实际对象供关闭；关闭立即撤销自身和焦点证明，先结清原输入释放责任，失败时两条原生连接都保留，成功才关闭自己的窗口连接。原SessionInspector继续由外层worker关闭。

43项新回归以实际二进制XRes/QueryTree/结构事件和注入AT-SPI组合验证，另202项依赖、原文本/语义/桌面及架构/类型合同通过，共245不同专项；新增1文件strict零错误/警告。初轮字典Unknown类型和42通过报告/源码均保留，显式收窄并新增异进程所有者回归后验证当前源码。没有真实Linux、真实输入、全范围类型或全量，见[绑定证据](evidence/v2-linux-window-binding.json)。

该次只完成关联基础，当前业务接线见下一节。GTK、Qt的通用AT-SPI Component/Accessible接口不能由这些证据推导出原生句柄；保留现有直接寻址语义/文本能力，通用关联继续开发。不能把关联基础或按键传输通过当作整个前台控制完成。

## 已接入业务的显式窗口节点按键

X11语义观察对可聚焦、启用/可见的非密码节点读取顶层明确窗口属性；只有已核对的原生XID提供方才产生`press_key`候选。原生窗口与可选零选区文本/光标上下文进入既有actions SHA，沿原观察消费、单动作审批、工具权限和资源账本。公开请求仍只提交观察引用及领域既有11个特殊键，不允许客户端直接传XID、布局或组合键。没有提供方绑定的节点仍可使用原语义/文本能力。

`linux_key_action.py`等待最多10秒人工聚焦目标窗口与选定节点，随后建立WindowBinding、原生焦点、键映射及来源，并完整核验复制配置后才发ready。父进程重验授权并发送原request/grant/run/operation UUID绑定permit；worker再验窗口/原节点/动作/原文/光标，重新生成当前序号的内部包，执行一次按下/抬起。XKB、设备切换、四Raw归属和实际释放保持既有严格校验，没有焦点获取、布局改写或重试。

业务成功必须有应用后验：可完整读取的零选区文本，按具体键核对预期全文和光标，并保持节点/祖先/名称/其他动作、窗口/焦点及输入守卫。已实现字符移动、行首尾/上下逻辑行、前后删除及换行的具体匹配；最多只读等待750ms，不再发键。折行/字素行为不符、Enter实际提交、Tab/Escape及非文本控件缺少明确后验时保留unknown，不以四Raw、已抬起或不变状态冒充成功。当前通用窗口关联、焦点导航/关闭的专门后验及鼠标/Wayland仍待开发。

新增按键候选的动态文本/光标上下文只影响对应指纹，原SetText和Unicode追加核验保留静态动作/窗口身份，并重算受允许内容改变的当前目录SHA，避免正常光标变化使它们误报unknown。没有放宽节点名称/祖先、其他动作或绑定变化。

原生释放的归属延伸到父/子进程：Linux按键permit可能写出前即登记受保护宿主；关闭管道可撤销未来工作，但父进程不能强制终止仍持有释放后缀或原查询的辅助进程。worker在业务期限外结清绑定，失败时保留两条原生连接及外部会话；父进程保留进程/排空管道/共享名额直到实际退出。外部close与业务等待并发时，退出不能抢先清空业务未消费的回执；实际消费者离开后才结清回收。其他平台/旧读取路径不借用这一豁免。

专项包含实际二进制XRes/XKB/XTEST注入、UUID错配/撤回/变化拒绝、取消释放、父进程关闭竞态、原SetText/追加，以及真实隔离SQLite审批/单次消费；结果与首轮故障原档见[业务按键证据](evidence/v2-linux-key-business.json)。未连接真实Linux、设备或原生桌面，没有全量结论。
