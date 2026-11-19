# V2 Android 全能力与视觉子代理审计（K14）

## 结论与范围

K14 是**逐项审计**（非新增工具批次）：把参考 `xuexi/android-agent`（固定提交 `7c09f6cd14b6f9890517f2bbf19fb92f0970f8fc`，"ghost-in-the-droid" v1.5.1，`gitd/services/agent_tools.py` 约 65 个工具，MCP + 端侧 Gemma  loop 共享）与 `xuexi/android-adb-control`（固定提交 `82b41743fa62dcc664aa643ffcaa10b69aacc303`，纯指令 Agent Skill，无运行时）的 Android 能力面，与 V2 Android 链逐项对照，分三类：**已对等**、**需真机/模拟器验收**、**可离线补齐缺口**。已有 observe/act/Unicode/许可**不重复实现**；**不宣称真机质量对等**。

V2 Android 模型侧工具：`android_request_access`（所有者批准本 run 观察指定 app + 请求逐次批准输入，TTL 60–900s、packages≤16、kinds 白名单）、`android_observe`（ui/apps，需 access_id，私有渠道禁用）、`android_act`（绑定本 run 观察的逐次批准输入，**绝不重试 unknown**）、`android_input`（所有者选择输入）、`android_restore_ime`（所有者批准恢复临时 IME）。输入 KINDS：tap/toggle/append_ascii/append_unicode/long_press/swipe/key/launch。纪律：`observation_sha256` 新鲜观察绑定、`node_id` 元素索引 targeting、`consent=True` + `grant_id`、逐输入审批、`scroll_verified`（两个稳定子身份沿批准轴移动）、Unicode 经 helper IME（helper_revision + restore_ime）、ASCII-only input text（无 shell 插值）、本地服务器强制（127.0.0.1 http）、设备身份（serial/transport_id/boot_id/user_id/fingerprint）。

## 组 1 — 观察 / 动作 / 输入纪律

| 参考能力 | V2 对应 | 分类 |
| --- | --- | --- |
| adb-control perceive→reason→act→settle→verify；每次观察一个设备变更；mutate/scroll/rotation/keyboard/popup 后新鲜 XML/截图 | `android_observe` + `android_act` 的 `observation_sha256` 新鲜绑定 | **已对等**（V2 以指纹强制新鲜观察） |
| `tap_element(idx)` 元素索引优先；`get_screen_tree` first | `android_act` 的 `node_id` 索引 targeting | **已对等** |
| adb-control 授权类（send/post/purchase/delete/install/export/settings）；android-agent 平台过滤工具 | `android_request_access` kinds/packages 白名单 + 逐输入审批 | **已对等**（V2 逐次所有者审批） |
| adb-control unknown 外部提交停止依赖变更、绝不自动重试 | `android_act` "Never retry unknown results" + unknown 效果结算 | **已对等** |
| `type_unicode` + ADBKeyboard 安装/启用/恢复原 IME | `append_unicode` + helper_revision + `android_restore_ime` | **已对等**（V2 helper IME 恢复链） |
| adb-control 列表遍历需两次无移动滚动 + 结束标记 | `scroll_verified`（两稳定子身份移动，仅文本变化不足） | **已对等** |
| `tap`/`swipe(direction)`/`long_press`/`press_back/home/key`/`launch_app` | KINDS tap/swipe/long_press/key(BACK/HOME/ENTER/TAB/DPAD_*)/launch | **已对等**（基础输入） |
| adb-control 默认预算 30 mutations/5min + 恢复预留；stuck/repetition/drift 阈值 | V2 run max_steps + 费用预算 + K07 重复守卫 | **部分对等**：V2 有步/费预算与重复守卫；Android 专用 mutation 预算/恢复预留待核 |
| `type_text`/`paste_text`（clipboard-set+paste，长/特殊文本优先） | `append_ascii`（≤256 ASCII）+ `append_unicode` | **部分对等**：V2 无 clipboard-set paste 路径 |
| `chain`（多动作一步，仅读/UI 子动作，禁 shell/run_skill/嵌套） | 无（V2 逐输入审批，K05 批次并行） | **设计差异**：V2 逐次审批不合并变更链 |
| `shell`（原始 ADB 逃生舱） | 无（V2 禁 shell 插值，仅批准 KINDS） | **设计差异**（V2 更严格，有意不提供） |

## 组 2 — 观察增强 / 视觉

| 参考工具 | V2 对应 | 分类 |
| --- | --- | --- |
| `screenshot`（b64 JPEG）/`get_screen_xml`/`get_elements` | `android_observe` ui（UI 树 + 节点） | **部分对等**：V2 有 UI 观察；原始截图工具待核 |
| `screenshot_annotated`（**set-of-mark** 编号元素叠加，匹配 get_elements 索引） | 无 | **可离线缺口**（需设备帧 + 图像处理）：SoM 编号叠加 |
| `screenshot_cropped`（区域 zoom） | 无 | **可离线缺口**（需设备帧）：区域裁剪 |
| `get_screen_tree`（≤80 节点缩进树）/`get_phone_state`（app/activity/keyboard/focus）/`classify_screen`（home/search/profile/dialog/error/loading） | `android_observe` ui/apps | **部分对等**：V2 有 UI/app 观察；屏幕分类/phone_state 待核 |
| `find_on_screen`（XML→OCR fallback）/`ocr_screen`/`ocr_region`（RapidOCR） | 无 | **可离线缺口**（OCR 库 + 设备帧）：屏幕 OCR/查找 |
| `start/stop_screen_recording`/`screen_recording_status`/`get_stream_info`（Portal/H264/screencap） | 无 | **需真机验收**：录屏/流 |
| `toggle_overlay`（Portal 视觉叠加） | 无 | **需真机验收** |

## 组 3 — 独立有界视觉子代理（K14 命名缺口）

| 参考能力（`gitd/services/sub_agent.py`） | V2 对应 | 分类 |
| --- | --- | --- |
| `sub_agent(task, max_frames=60)` + `screenshot_sequence(duration 2–180s, fps 0.1–4)` | 无专用视觉子代理；V2 有 K04 单层有界委派 + media/images 服务 | **可离线缺口**：独立有界视觉子代理 |
| 隔离：独立**无状态一次性**视觉调用，master 上下文不携带帧，**仅返回文本** | K04 委派子 run 独立上下文 + 汇总 | **可离线补齐**：基于 K04 委派 + 视觉模型调用 |
| 边界：硬上限 `MAX_SUB_AGENT_FRAMES=60`（保序均匀子采样）、`max_tokens=1024`、固定聚焦 system prompt、**子代理无工具**、单次调用无 loop | K04 子 run max_calls/预算/工具白名单 | **可离线补齐**：帧上限 + 无工具 + 单次 + 文本汇总 |
| 后端：Anthropic API（SUB_AGENT_MODEL override），缺 key 优雅返回说明字符串不抛 | V2 固定授权模型 + 离线桩 | **可离线补齐**（模型调用可离线桩测）；真实视觉质量需账号/设备帧 |

## 组 4 — 设备能力矩阵 / 应用管理

| 参考能力 | V2 对应 | 分类 |
| --- | --- | --- |
| `device_health`（connection/Portal 安装/accessibility/HTTP/WiFi ip/battery/storage/model/brand/android_version/api_level/screen/目标 app 版本/默认 IME/screen-on/recommended_fix）+ `fix_device_health` | V2 设备身份（serial/transport_id/boot_id/user_id/fingerprint）+ `android_observe` apps | **部分对等**：V2 有设备身份与 app 观察；完整健康矩阵/修复待核（**需真机验收**） |
| 平台矩阵 `tool_platforms.py`（cross_platform/android_only/ios_supported/ios_planned，按设备 ref 过滤 schema） | V2 Android 专用工具 + 路由档位 | **部分对等**：V2 有 Android 工具；逐工具平台分类矩阵待核 |
| `launch_app(fresh)`/`force_stop`/`app_state`/`list_apps`/`search_apps`/`list_packages`/`open_camera`/`launch_intent` | `android_observe` apps + KINDS launch | **部分对等**：V2 有 app 观察/launch；force_stop/app_state/intent/camera 待核（**需真机验收**） |
| `clipboard_get/set`/`get/open/clear_notifications`/`speak_text`(TTS)/wireless pair/connect | 无 | **需真机验收** |
| adb-control 预检（boot state/wm size/foreground activity/current user/image-capability） | V2 设备身份 + boot_id + 本地服务器校验 | **部分对等** |

## 组 5 — Web / 技能 / 工作流

| 参考能力 | V2 对应 | 分类 |
| --- | --- | --- |
| `open_url`/`web_search`（浏览器优先 walk）/`browser_back`/`get_current_url`/`wait_for_text`/`extract_visible_text`/`extract_articles`/`read_news` | V2 browser_navigate/read/click/fill（独立 agent 浏览器，非设备浏览器） | **设计差异**：V2 浏览器独立于 Android 设备；设备内 web 工具需真机 |
| `list_skills`/`run_skill`/`run_workflow`/`run_action`/`explore_app`（XML-skeleton hash 状态图）/`create_skill`/`draft_skill`/`save_skill` | V2 skills_list/activate/view/history/create + 审核 | **部分对等**：V2 技能链对等（审核更严）；explore_app 状态图可离线缺口 |
| adb-control `scripts/check_docs.py`（静态文档 lint，离线） | V2 项目索引/任务状态守卫 | **设计一致**（离线文档守卫） |

## 可离线补齐缺口清单（后续实现批次，非 K14 实现）

1. **独立有界视觉子代理**（参考 `sub_agent`）：基于 K04 单层有界委派 + 视觉模型调用，帧上限（≤60 保序子采样）、子代理无工具、单次调用、仅文本汇总、master 不携带帧；模型调用可离线桩测，真实视觉质量需账号/设备帧。独立批次（先授权/预算合同后接线）。
2. **set-of-mark 编号截图叠加**（参考 `screenshot_annotated`）：UI 节点索引→编号叠加图，匹配 observe 索引；需设备帧 + ImageCodec。独立批次。
3. **屏幕 OCR / 查找 / 分类**（参考 `ocr_screen`/`find_on_screen`/`classify_screen`）：OCR 库 + 设备帧。独立批次。
4. **explore_app 状态图**（XML-skeleton hash）：技能探索状态图，可离线。独立批次。

## 需真机/模拟器验收清单（不可离线宣称对等）

- 全部设备 I/O 实际质量（截图/UI dump/输入送达/app launch/录屏/流/overlay）。
- 完整设备健康矩阵（device_health/fix_device_health：Portal/accessibility/battery/storage/版本/屏幕/IME）与逐工具平台分类矩阵。
- force_stop/app_state/launch_intent/open_camera/clipboard/notifications/TTS/wireless。
- 设备内 web 工具；真实视觉子代理质量（需账号 + 设备帧）。
- Android 真机输入送达/Unicode/滚动/恢复质量。

## 完成条件与边界

K14 交付本逐项审计记录（五组分类 + 可离线缺口清单 + 真机验收清单），证据为参考固定提交工具名清单与 V2 Android 链对照。**V2 Android 纪律（新鲜观察绑定/元素索引/逐次审批/unknown 不重试/滚动验证/Unicode IME 恢复/本地服务器/设备身份）已对等 adb-control 的 rigor**；广度（约 65 工具）与独立视觉子代理/设备健康矩阵/SoM/OCR 仍有缺口。**审计完成不等于真机质量对等**：可离线缺口与真机验收项保留为后续任务，`whole_core_parity_complete` 与 `whole_reference_feature_audit_complete` 仍 false（K15… 领域参考审计未核完）。本批不新增工具/迁移/路由，不改运行中服务与数据库。