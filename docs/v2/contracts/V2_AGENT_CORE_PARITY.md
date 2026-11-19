# V2 Agent 内核与学习项目对标

## 结论与范围

2026-11-18 实读源码核对：**V2 有可运行的共享 Agent 内核，但尚未完整对标 `xuexi/` 的参考功能，不能宣称内核或整个 V2 开发完成。** 此前补上运行中系统前缀冻结、普通回包工具协议校验和空回复失败结算；随后 K03 补修订化会话任务清单、K04 补单层父子 run 委派、K05 补模型批次工具并行、K06 补 run 内工具上下文压缩、K07 补重复退化守卫与预算内收敛 grace、K08 补回合后 reviewer/curator 后台学习（待审建议进现有审核撤回链）、K09 补模型侧历史会话检索（四形态只读工具、原文锚点、分页/大小限额、Agent/会话隔离与删除屏障）、K10 补结构化澄清与当前 run 暂停续跑（复用 waiting_approval、响应/取消/超时、CAS 与不可变证据）、K11 补停止前验核（通用 verify-on-stop gate，证据用既有动作回执 verification，模型文本与清单勾选不作证据，默认 OFF 有界 nudge）。模型故障恢复等仍有明确源码缺口。

本表是当前已核对范围，不是七个参考仓库全部 API 的穷尽验收。参考仓库位于忽略目录，不随主仓 Git 迁移；下面固定版本、[任务清单](../../tasks/v2-agent-core-parity.json)与[证据](../../evidence/v2-agent-core-parity.json)随主仓保存。阅读旧 `xuexi/NOTES.md` 后仍以固定版本实际代码为准，不把 V1 的实现当作 V2 已有功能。

## 参考分工与版本

| 目录 | 固定提交 | 对标作用与本轮读取入口 |
| --- | --- | --- |
| `xuexi/hermes-agent` | `9976f4857b72439d79016354d7b6e1a3ee952e2f` | 主内核；`agent/conversation_loop.py`、`prompt_builder.py`、`prompt_cache_scope.py`、`tool_executor.py`、`tool_call_batches.py`、`iteration_budget.py`、`context_engine.py`、`turn_stop_gates.py`、`background_review.py`、`subagent_lifecycle.py`、`empty_response_guard.py`、`repetition_guard.py`，`tools/todo_tool.py`、`delegate_tool.py` |
| `xuexi/android-agent` | `7c09f6cd14b6f9890517f2bbf19fb92f0970f8fc` | Android/视觉链；`gitd/services/agent_chat.py`、`agent_tools.py`、`sub_agent.py` |
| `xuexi/android-adb-control` | `82b41743fa62dcc664aa643ffcaa10b69aacc303` | 指令型操作纪律；观察→单步动作→重新观察及分档授权，不是另一套运行内核 |
| `xuexi/surfcore` | `59ec3066f7d42a0452fae01f170c9a8484d5dbe5` | 人格、记忆、主动行为；`plugins/surfcore/lib/memory.js`、`persona.js`、`scheduler.js` |
| `xuexi/dsh-video-storyboard` | `e124ae6fcfb31f3572ea24aad093a86897b556a3` | 视频分镜、局部放大与时间码；本轮只核对 README 功能及 V2 媒体生产入口，算法逐项核对仍待推进 |
| `xuexi/dsh-bili-humanism` | `2ec50417e64a72a28b9445c06872d7f14d9dbf24` | 内容证据→信笺→可修正反思→人格成长；本轮功能层核对，未完成全部源码等价性审计 |
| `xuexi/dsh-bili-whale` | `8b0009f8ce2ebdc421afcb2e727b19ac18912ec6` | B 站 API、作息与受控投递；本轮功能层核对，全部读写 API 与主动观看链仍待审计 |

没有拉取远程更新，也没有运行参考项目的真实账号或模型。学习行为和合同后在 V2 四层内实现，本批没有复制第三方源码。

## 主内核功能矩阵

下列 V2 路径均相对 `agent/src/omni/`。已有实现表示找到生产接线，不等于整模块已重新验收。

| 功能 | V2 当前代码与判定 | 对标仍需完成 |
| --- | --- | --- |
| 单内核、多入口、回合/动作持久化 | 已有：`application/engine.py`、`composition/instance.py`、`adapters/persistence/repository.py` | 保持共用组装链，真实入口集成验收单列 |
| 会话 FIFO、跨会话并行与取消归属 | 已有：engine、work/lease/lifecycle 仓储及实例关闭链 | 不是单个模型批次的工具并行，也不是子代理 |
| 每次模型调用、摘要费用与未知结算 | 已有：`application/model_calls.py`、context、model 仓储 | 预算耗尽后的收敛回合尚需合同，不直接增加调用上限 |
| 系统前缀稳定、动态指引注入 | **本批实现单 run 冻结**：`domain/turn_prompt.py`；档位和已审技能变化写消息尾部 | Hermes 的会话/分叉 lineage 级缓存范围尚未实现；跨 run 前缀不保证相同，未证明供应商实际缓存命中 |
| 工具调用身份与 JSON 完整性 | 流式已有有界组装；**本批补普通回包统一验证**：`domain/model_reply.py`、ModelCalls | 同批 ID 唯一、名称/ID 格式、32 调用与累计 262144 字节参数；不修造 ID、不重发动作 |
| 空回复、截断、拒绝、重复退化 | 空回复转明确失败；截断/内容过滤与输出超额结算后拒绝；**K07 已接重复退化守卫与预算内 grace**：`domain/repetition.py`、引擎 stop-path 检查与非委派收敛 grace，见 [迭代守卫合同](V2_ITERATION_GUARD.md) | 保守文本重复检测（stop-path ≥16000 字符 runaway）命中即失败保留费用不重放；非委派步数耗尽一次 no-tool grace（费用闸门保持），委派保持调用数合同；未含参考周期锚点全部变体、不做 provider fallback（K12），仍非全集对等 |
| 上下文压缩与来源失效 | 已有持久历史整回合压缩、快照/摘要/删除屏障；**K06 已接 run 内有界压缩**：`domain/context.py` 工具轮规则、`ContextService.compact_run`，见 [run 内压缩合同](V2_RUN_COMPACTION.md) | 旧完整工具轮摘要为有界 summary，冻结前缀/初始 head/最近 2 轮与尾部 notice 保留、工具对不拆分，摘要计费且来源删除屏障保持；单个超大保留轮仍可能超硬限、极旧轮摘要输入有损截断，仍非参考全集对等 |
| 多工具执行 | **K05 已接批次分段并行**：`domain/tool_batch.py`、`Application._execute_parallel`；已提供只读能力按两两不同资源键并发，控制/写/冲突资源串行，逐项生命周期不变，见 [批次并行合同](V2_TOOL_BATCH.md) | 结果按原调用顺序汇合、取消等待每个已启动动作结算、unknown 不重放已实现；写动作并行需改造 run 审批状态机，冲突分类基于显式资源键而非路径启发式，仍非参考全集对等 |
| 子代理委派 | **K04 已接单层有界生产委派**：`delegate_task`、父子独立会话/上下文/租约、批量并行、固定模型/工具捕获、预算与实际取消汇合，见 [委派合同](V2_DELEGATION.md) | 深度 1、每父最多 4 子；未知动作不重放。跨层嵌套、异步 handle/reconnect/steer、自选模型和专用视觉子代理仍非参考全集对等；工具批次并行另属 K05 |
| 修订化任务清单 | **K03 已接生产模型控制、持久快照与所有者只读 API**，见 [任务清单合同](V2_TODOS.md) | 已实现读写/合并、revision、层级/顺序、数量/字节限额、压缩/重启恢复和来源删除；清单勾选不作为实际任务成功证据，未新增编辑界面 |
| 记忆检索、来源及删除 | 已有 FTS、语义向量、引用来源、审计与删除：memory/people/documents/semantics | 不等于自动人格成长或回合后复盘 |
| 技能渐进披露、补丁与审核 | 已有 skills_list/view/activate、参考文件、revision、patch 与审核：`application/skills.py`、skill 仓储 | 继续保留审核；不把模型生成内容直接当新权限 |
| 回合后学习/整理 | **K08 已接生产回合后 reviewer/curator 后台链**，见 [回合后学习合同](V2_BACKGROUND_REVIEW.md)；另有显式记忆整理（memory_organization 不调模型）与技能修订入口 | 后台批次持有/独立预算/只读+记忆技能白名单/证据锚点/待审输出/关闭与撤回已实现；建议经现有 remember/forget 与技能审核撤回链应用，来源不直接变授权；无 prompt-cache parity fork 与 aux 模型路由 |
| 历史会话检索 | 所有者历史分页/审计接口已有；**K09 已接模型侧 session_search 四形态只读工具**：`domain/session_search.py`、`application/session_search.py`、`session_search_repository.py`，见 [历史检索合同](V2_SESSION_SEARCH.md) | Agent/会话隔离、原文锚点（message_id/session_id/seq）、分页/大小限额与删除屏障（deleted_at+context_floor_seq）已实现，不等同 memory_search；V2 无 messages FTS，discover 用有界 token LIKE+按会话分组，非参考 FTS5/bm25/lineage/跨 profile/时间界全集 |
| 澄清与主动纠偏 | 普通用户追加消息已有；**K10 已接结构化 clarify 当前 run 暂停与续跑**：`domain/clarify.py`、`application/clarify.py`、`clarify_repository.py`、engine `_clarify_wait`，见 [澄清合同](V2_CLARIFY.md) | 1–5 结构化问题、复用 waiting_approval 暂停态、同一 run 响应/取消/超时续跑、revision/policy CAS、run 取消清理与不可变证据已实现；排队新回合不算当前回合继续。显式用户 steering 完整合同、多平台 UI 回调与委派限制仍非参考全集对等 |
| 停止前验核 | 外部动作已有结构化 outcome/effect/verification，unknown 不当成功；**K11 已接通用的 verify-on-stop gate**：`domain/verification_stop.py`、`repository.run_applied_writes`、engine 自然停止点，见 [停止前验核合同](V2_VERIFICATION_STOP.md) | 模型自然文本停止时，本 run 已应用写动作回执缺 verification 则追加有界 nudge（≤2）续跑、次数用尽以候选答案收尾；唯一证据为持久化动作回执 verification，模型最终文本与清单勾选均不作证据；默认 OFF（opt-in），无新表/迁移；参考工作区验证账本/LSP/pre_verify 钩子/kanban 守卫未对等 |
| 模型故障恢复/切换 | 已有超时/取消、固定授权模型、已送达未知费用归属；适配器关闭自动重试 | Hermes 的重试、模型/provider fallback 未完整对等。先设计显式授权与预算合同，不能擅换账号、模型或重复未知调用 |
| 文件、Shell、浏览器与扩展 | 已有：`composition/capabilities.py`、workers、extensions/MCP | 工具存在不等于参考所有工具组/API 一致；浏览器高级交互、代码沙箱、渠道/供应商全集逐项盘点仍待完成 |
| 定时任务与投递 | 已有 schedules、work_items、outbox 与审批/限额/恢复链 | Hermes cron 的技能绑定、任务输出依赖等变体需逐项盘点；surfcore 的完整自主作息/动机不应标已完成 |

## 专项参考能力矩阵

| 参考 | V2 已有基础 | 尚不能宣称对等的范围 |
| --- | --- | --- |
| Android / ADB 纪律 | Android 模型工具、观察绑定、Unicode、单次许可、重观察及 unknown 后验 | 全部参考工具/API、独立有界视觉子代理、设备能力矩阵与真机质量仍需逐项核对 |
| surfcore | 人物/人格配置、记忆归属、QQ、B 站、日程与投递 | 跨渠道一致人格、会遗忘/巩固的学习、主动动机和作息反馈闭环尚非完整等价 |
| video-storyboard | 上传/B 站来源、ASR、有限抽帧、时间码产物和模型读取 | 镜头切点密度、自适应宫格、局部 zoom、字幕带和文本定位五工具的完整组合尚未对等 |
| bili-humanism | 来源文档/媒体、人物记忆、待审技能/记忆、投递账本 | 从观影到信笺/反思、修订/撤回传播、专用人格成长及观影提问闭环仍需源码/合同对照 |
| bili-whale | B 站读写/登录、预算/冷却、写审批和送达核查已有 | 全 API、观看筛选、双日记、随机作息/沉默衰减、直播等逐项核对未完成 |

功能逐项完整对标是本任务目标；需要账号/设备/模型质量的验收与能够离线实现的功能缺口分别记录。不能因为某项有相近名称、待实机验收或旧 V1 有实现就将其判为 V2 完成。

## 本批实现合同

1. ContextService 准备完成后捕获一个 run 的系统消息；后续不重写该字节串。档位或已审技能变化作为带说明的数据放在尾部 user 消息；原 tool 消息、引用关系和多模态块保留。固定模型、授权读取、上下文删除屏障及每次费用检查保持。
2. 所有普通 ModelReply 在费用结算之后、创建任何动作之前走统一校验；流式组装仍先验证完整结束。格式/大小无效或无答案且无工具调用时结束为明确失败，已发生费用保留，不自动重试。摘要用途继续走原专用无工具合同。
3. 同批工具 ID 唯一；上限沿用已有流协议。非有限数、循环对象、非字符串 JSON 键、代理字符及会改变数据的 JSON 转换均拒绝。不将“修复 ID”当作无损执行，也不增加未知动作重放。
4. 缓存统计合入后的类型问题已修正：context_memories 移至所属 context 仓储，JSON 视图明确类型，流式 usage 复用 Mapping 边界；数值仍只来自供应商字段，不杜撰缓存命中。

## 后续顺序和完成条件

K03 修订化任务清单、K04 单层有界父子 run 委派、K05 模型批次工具并行、K06 run 内工具上下文压缩、K07 重复退化守卫与预算内收敛 grace、K08 回合后 reviewer/curator 后台学习、K09 模型侧历史会话检索、K10 结构化澄清与当前 run 暂停续跑和 K11 停止前验核已实现。下一步按 [K12…任务](../../tasks/v2-agent-core-parity.json)推进显式模型故障恢复。委派子批并行、K05 工具批次并行、K06 run 内压缩、K07 迭代守卫、K08 回合后评审与 K11 停止前验核是不同链路，各自合同独立。顺序中的授权/持久化合同应先于执行接线；任务记录和审计文档不是相应功能的实现。

每项需有生产工具/用例/组装链、持久来源与资源归属、错误/取消/删除行为、受影响调用方专项和可复查证据；仅有类、提示词或空工具名称不算完成。所有领域参考功能也需核完后才能关闭总对标任务。开发中局部测试，整个 V2 开发冻结后统一全量，真实模型、设备、各平台和 72 小时分别验收。当前总任务保持未完成。
