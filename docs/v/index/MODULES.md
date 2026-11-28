# 模块与源码索引

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

源码指纹：`f7be87dc5ab9d28efe3855448cfd37d1b369b5dfd86e4861dbc7df8b4f85ca02`。包含 65 个职责模块、941 个源码/界面文件。

## V2 领域合同 · v2-domain

分类：内核与模型。纯领域状态机、结构化结果与配置校验；不依赖旧运行时。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/domain/__init__.py](../../../agent/src/omni/domain/__init__.py) | V2 | 1 | V2 business rules. No storage, operating-system or transport dependencies. |
| [agent/src/omni/domain/action_review.py](../../../agent/src/omni/domain/action_review.py) | V2 | 199 | Owner observations about unknown writes; does not settle actions, authorize replay or release model costs. |
| [agent/src/omni/domain/agents.py](../../../agent/src/omni/domain/agents.py) | V2 | 30 | Owner-managed Agent names and bounds; no credentials, state copying or platform access. |
| [agent/src/omni/domain/android.py](../../../agent/src/omni/domain/android.py) | V2 | 393 | Android identities, bounded UI observations and approved input contracts; no native I/O. |
| [agent/src/omni/domain/android_explore.py](../../../agent/src/omni/domain/android_explore.py) | V2 | 217 | Pure Android explore_app state graph: structural screen keys, bounded edges and summaries. |
| [agent/src/omni/domain/android_unicode.py](../../../agent/src/omni/domain/android_unicode.py) | V2 | 121 | Pinned helper profiles and authenticated, bounded Unicode input messages; no device I/O. |
| [agent/src/omni/domain/audit_retention.py](../../../agent/src/omni/domain/audit_retention.py) | V2 | 291 | Agent audit-retention subjects, frozen previews and explicit plans; performs no I/O or deletion. |
| [agent/src/omni/domain/background_review.py](../../../agent/src/omni/domain/background_review.py) | V2 | 229 | Bounded post-turn review proposals and owner decisions; no model calls, memory writes or I/O. |
| [agent/src/omni/domain/bilibili.py](../../../agent/src/omni/domain/bilibili.py) | V2 | 1045 | Explicit Bilibili form profiles, targets and mention ranges; no browser, network or storage. |
| [agent/src/omni/domain/capabilities.py](../../../agent/src/omni/domain/capabilities.py) | V2 | 116 | Read-only capability projection over the instance health snapshot; no I/O and no acceptance claims. |
| [agent/src/omni/domain/clarify.py](../../../agent/src/omni/domain/clarify.py) | V2 | 199 | Pure rules for structured clarification: question normalization, answer cleaning and outcomes. |
| [agent/src/omni/domain/configuration.py](../../../agent/src/omni/domain/configuration.py) | V2 | 164 | Typed configuration payloads and fingerprints; contains no I/O or credential values. |
| [agent/src/omni/domain/context.py](../../../agent/src/omni/domain/context.py) | V2 | 121 | Bounded conversation policy; whole turns are the smallest compression unit. |
| [agent/src/omni/domain/contracts.py](../../../agent/src/omni/domain/contracts.py) | V2 | 235 | Typed execution contracts and state transitions; performs no I/O. |
| [agent/src/omni/domain/credential_ipc.py](../../../agent/src/omni/domain/credential_ipc.py) | V2 | 109 | Bounded native credential messages; no platform selection, imports or diagnostics payloads. |
| [agent/src/omni/domain/credentials.py](../../../agent/src/omni/domain/credentials.py) | V2 | 79 | Credential reference and payload bounds; contains no secret store or platform dependency. |
| [agent/src/omni/domain/cronjob.py](../../../agent/src/omni/domain/cronjob.py) | V2 | 277 | Model-side scheduled-job contract: decidable syntax, bounded requests and projections; no I/O. |
| [agent/src/omni/domain/delegation.py](../../../agent/src/omni/domain/delegation.py) | V2 | 113 | Bounded child-run requests and tool inheritance; no scheduling, I/O or permission grants. |
| [agent/src/omni/domain/delivery.py](../../../agent/src/omni/domain/delivery.py) | V2 | 89 | Outbound intent identities and trusted transport results; no I/O or credentials. |
| [agent/src/omni/domain/desktop.py](../../../agent/src/omni/domain/desktop.py) | V2 | 623 | Bounded desktop discovery and read-only UI contracts; no native I/O or action authorization. |
| [agent/src/omni/domain/desktop_actions.py](../../../agent/src/omni/domain/desktop_actions.py) | V2 | 233 | Exact semantic action contracts; observations never grant write authority. |
| [agent/src/omni/domain/desktop_control.py](../../../agent/src/omni/domain/desktop_control.py) | V2 | 197 | Independent ephemeral remote-control consent protocol; system device selection never authorizes a business input action. |
| [agent/src/omni/domain/desktop_portal.py](../../../agent/src/omni/domain/desktop_portal.py) | V2 | 236 | Ephemeral window-share consent and helper protocol; grants never identify a native PID or authorize input. |
| [agent/src/omni/domain/documents.py](../../../agent/src/omni/domain/documents.py) | V2 | 100 | Bounded source text, deterministic chunks and local vector math; contains no I/O. |
| [agent/src/omni/domain/event_retention.py](../../../agent/src/omni/domain/event_retention.py) | V2 | 292 | Bounded event-retention previews and explicit plans; performs no I/O, deletion or sequence reconstruction. |
| [agent/src/omni/domain/extensions.py](../../../agent/src/omni/domain/extensions.py) | V2 | 482 | Static trusted-plugin metadata, immutable capture records and owner authorizations; never loads code or files. |
| [agent/src/omni/domain/files_patch.py](../../../agent/src/omni/domain/files_patch.py) | V2 | 525 | Bounded fuzzy search-replace patch rules; no filesystem, network or storage I/O. |
| [agent/src/omni/domain/files_search.py](../../../agent/src/omni/domain/files_search.py) | V2 | 234 | Bounded workspace search rules (content substring/regex + filename glob); no filesystem, network or storage I/O. |
| [agent/src/omni/domain/foreground.py](../../../agent/src/omni/domain/foreground.py) | V2 | 107 | Single-use foreground authority and bounded input, independent of native APIs. |
| [agent/src/omni/domain/history.py](../../../agent/src/omni/domain/history.py) | V2 | 142 | Canonical, bounded history cursors bind a stream and its retention generation; performs no I/O. |
| [agent/src/omni/domain/images.py](../../../agent/src/omni/domain/images.py) | V2 | 181 | Validated image references, immutable model policies and conservative low-detail metering; no I/O. |
| [agent/src/omni/domain/installation.py](../../../agent/src/omni/domain/installation.py) | V2 | 63 | Immutable local installation receipt written once per home; no I/O and no upgrade execution. |
| [agent/src/omni/domain/linux_window_leases.py](../../../agent/src/omni/domain/linux_window_leases.py) | V2 | 149 | Exact private read-only X11 lease IPC; opaque aliases never authorize a desktop action. |
| [agent/src/omni/domain/local_vectors.py](../../../agent/src/omni/domain/local_vectors.py) | V2 | 156 | Local embedding bundle and bounded transport contracts; no files, model imports or I/O. |
| [agent/src/omni/domain/maintenance.py](../../../agent/src/omni/domain/maintenance.py) | V2 | 115 | Bounded local maintenance requests, ownership claims and lease times; no ORM, I/O or execution. |
| [agent/src/omni/domain/mcp.py](../../../agent/src/omni/domain/mcp.py) | V2 | 419 | External MCP transport identity, catalog fingerprints and bounded model text; performs no I/O. |
| [agent/src/omni/domain/media.py](../../../agent/src/omni/domain/media.py) | V2 | 518 | Bounded media sources, transcripts and frame sheets; timestamps are anchors, never guesses. |
| [agent/src/omni/domain/media_locate.py](../../../agent/src/omni/domain/media_locate.py) | V2 | 104 | Bounded transcript locate rules; pure logic, no I/O. Anchors come from the source transcript, never guessed. |
| [agent/src/omni/domain/memory_decay.py](../../../agent/src/omni/domain/memory_decay.py) | V2 | 83 | Pure memory decay/consolidation rules: retention scoring, clarity steps and catch-up; no I/O. |
| [agent/src/omni/domain/memory_organization.py](../../../agent/src/omni/domain/memory_organization.py) | V2 | 118 | Bounded exact-fact organization requests and owner decisions; no memory writes, model calls or I/O. |
| [agent/src/omni/domain/memory_scoring.py](../../../agent/src/omni/domain/memory_scoring.py) | V2 | 162 | Pure memory-importance scoring heuristic (owner/private/emotion/promise/conflict/self/length); no I/O. |
| [agent/src/omni/domain/memory_semantics.py](../../../agent/src/omni/domain/memory_semantics.py) | V2 | 55 | Bounded derived-memory indexing and related-fact proposals; no I/O or fact rewriting. |
| [agent/src/omni/domain/metering.py](../../../agent/src/omni/domain/metering.py) | V2 | 80 | Request shape and capacity policy shared by counting and model invocation; no I/O. |
| [agent/src/omni/domain/model_recovery.py](../../../agent/src/omni/domain/model_recovery.py) | V2 | 79 | Pure rules for bounded model empty-reply recovery: deterministic-empty detection and cost-aware budget. |
| [agent/src/omni/domain/model_reply.py](../../../agent/src/omni/domain/model_reply.py) | V2 | 62 | One tool-reply contract for all model transports; validation never repairs or dispatches calls. |
| [agent/src/omni/domain/model_stream.py](../../../agent/src/omni/domain/model_stream.py) | V2 | 205 | Bounded chat stream assembly. Partial text and tool arguments never become executable results. |
| [agent/src/omni/domain/objects.py](../../../agent/src/omni/domain/objects.py) | V2 | 44 | Immutable object identities and metadata constraints, independent of storage. |
| [agent/src/omni/domain/omniweave.py](../../../agent/src/omni/domain/omniweave.py) | V2 | 337 | OmniWeave rollout modes and the canonical owner Web trigger contract. |
| [agent/src/omni/domain/omniweave_artifacts.py](../../../agent/src/omni/domain/omniweave_artifacts.py) | V2 | 102 | Bounded OmniWeave artifact commands and immutable text-object envelopes. |
| [agent/src/omni/domain/people.py](../../../agent/src/omni/domain/people.py) | V2 | 48 | Person, account and memory document rules; contains no I/O or ORM types. |
| [agent/src/omni/domain/persona_growth.py](../../../agent/src/omni/domain/persona_growth.py) | V2 | 129 | Deterministic persona-growth rendering from approved reflection entries; no I/O. |
| [agent/src/omni/domain/qq.py](../../../agent/src/omni/domain/qq.py) | V2 | 142 | Bounded OneBot text events and explicit conversation grants; no transport I/O. |
| [agent/src/omni/domain/recovery_authority.py](../../../agent/src/omni/domain/recovery_authority.py) | V2 | 84 | Versioned authority for keeping an isolated predecessor fenced; no IO or claims about external effects. |
| [agent/src/omni/domain/reference_release.py](../../../agent/src/omni/domain/reference_release.py) | V2 | 69 | Bounded artifact-reference release plans; validates identities without accessing storage or files. |
| [agent/src/omni/domain/reflection.py](../../../agent/src/omni/domain/reflection.py) | V2 | 82 | Validate a journal draft before its captured document evidence is resolved. |
| [agent/src/omni/domain/release_fence.py](../../../agent/src/omni/domain/release_fence.py) | V2 | 78 | Persisted startup exclusion contract shared by immutable installations; no IO or mutable state. |
| [agent/src/omni/domain/releases.py](../../../agent/src/omni/domain/releases.py) | V2 | 273 | Reviewed immutable release/recovery plans; same-schema first delivery, with no platform or storage IO. |
| [agent/src/omni/domain/repetition.py](../../../agent/src/omni/domain/repetition.py) | V2 | 70 | Conservative model-output repetition degeneration detection; pure rules, no I/O. |
| [agent/src/omni/domain/retention.py](../../../agent/src/omni/domain/retention.py) | V2 | 101 | Execution-body retention policy; time is injected, no erasure or file operations. |
| [agent/src/omni/domain/routing.py](../../../agent/src/omni/domain/routing.py) | V2 | 208 | Deterministic capability tiers and immutable route policy; neither permissions nor model/network calls. |
| [agent/src/omni/domain/run_recovery.py](../../../agent/src/omni/domain/run_recovery.py) | V2 | 18 | Restart checkpoints contain identities and digests of persisted receipts. |
| [agent/src/omni/domain/scheduling.py](../../../agent/src/omni/domain/scheduling.py) | V2 | 242 | Deterministic UTC scheduling and numeric cron rules; resolved timezone data is injected. |
| [agent/src/omni/domain/schema_upgrade.py](../../../agent/src/omni/domain/schema_upgrade.py) | V2 | 81 | Explicit forward-only V2 schema routes bound to both immutable runtime inventories; no IO. |
| [agent/src/omni/domain/service_activation.py](../../../agent/src/omni/domain/service_activation.py) | V2 | 83 | Bind unattended exchange activation to one reviewed boot; pure persisted contracts with no platform IO. |
| [agent/src/omni/domain/service_admin.py](../../../agent/src/omni/domain/service_admin.py) | V2 | 285 | Exact operation or exchange privilege scope; never accepts arbitrary commands, paths or identities. |
| [agent/src/omni/domain/service_cleanup.py](../../../agent/src/omni/domain/service_cleanup.py) | V2 | 180 | Exact quarantined-registration cleanup plans; no filesystem, native calls or reinterpretation of old effects. |
| [agent/src/omni/domain/service_control.py](../../../agent/src/omni/domain/service_control.py) | V2 | 205 | Reviewed service start/drain identities and domain-separated HMAC messages; no network or process IO. |
| [agent/src/omni/domain/service_deployment.py](../../../agent/src/omni/domain/service_deployment.py) | V2 | 566 | Pure, versioned service plans. Validation performs no filesystem or OS calls. |
| [agent/src/omni/domain/service_exchange.py](../../../agent/src/omni/domain/service_exchange.py) | V2 | 329 | Reviewed same-schema service exchanges and ordered witnesses; no platform, process or storage IO. |
| [agent/src/omni/domain/service_isolation.py](../../../agent/src/omni/domain/service_isolation.py) | V2 | 115 | Reviewed offline startup quarantine; retains unknown outcomes and authorizes only a separate recovery plan. |
| [agent/src/omni/domain/service_operations.py](../../../agent/src/omni/domain/service_operations.py) | V2 | 140 | Fingerprint offline service changes; registration never implies a healthy running service. |
| [agent/src/omni/domain/session_search.py](../../../agent/src/omni/domain/session_search.py) | V2 | 271 | Pure rules for model-side history recall: mode inference, bounded shaping and lineage-free dedup. |
| [agent/src/omni/domain/settings.py](../../../agent/src/omni/domain/settings.py) | V2 | 112 | Editable Agent settings and immutable resolved run configuration; no I/O. |
| [agent/src/omni/domain/skills.py](../../../agent/src/omni/domain/skills.py) | V2 | 251 | Versioned textual skills, bounded disclosure and edit rules; skills confer no authority. |
| [agent/src/omni/domain/soak.py](../../../agent/src/omni/domain/soak.py) | V2 | 119 | V2 observational soak coverage and owner limits; no sampling, clocks, files or acceptance execution. |
| [agent/src/omni/domain/som.py](../../../agent/src/omni/domain/som.py) | V2 | 132 | Pure Android SoM node admission and stable numbering: bounded, password-excluding; no image I/O. |
| [agent/src/omni/domain/terminal_review.py](../../../agent/src/omni/domain/terminal_review.py) | V2 | 87 | Owner observations of unknown terminal effects; never a receipt correction or replay authority. |
| [agent/src/omni/domain/terminals.py](../../../agent/src/omni/domain/terminals.py) | V2 | 124 | Explicit owner terminal requests and metadata identities; no model tools, shell execution or transcript storage. |
| [agent/src/omni/domain/todos.py](../../../agent/src/omni/domain/todos.py) | V2 | 166 | Bounded revisioned planning data and hierarchy rules; no execution or authorization. |
| [agent/src/omni/domain/tool_batch.py](../../../agent/src/omni/domain/tool_batch.py) | V2 | 65 | Model tool-batch segment planning: independent read-only capabilities may run in parallel. |
| [agent/src/omni/domain/turn_prompt.py](../../../agent/src/omni/domain/turn_prompt.py) | V2 | 45 | Freeze one run's system prefix; publish changed runtime guidance in the message tail. |
| [agent/src/omni/domain/verification_stop.py](../../../agent/src/omni/domain/verification_stop.py) | V2 | 133 | Scoped completion evidence and bounded follow-up after unverified writes. |
| [agent/src/omni/domain/visual_subagent.py](../../../agent/src/omni/domain/visual_subagent.py) | V2 | 198 | Pure rules for the bounded vision sub-agent: frame admission, ordered subsampling, fixed prompts and summary bounds. |

文档：[docs/v/reference/V2_REFACTOR.md](../../../docs/v/reference/V2_REFACTOR.md)

测试：[agent/tests/v2/core/test_v2_architecture.py](../../../agent/tests/v2/core/test_v2_architecture.py)

直接静态依赖：无内部模块依赖

## V2 应用服务 · v2-application

分类：内核与模型。单实例调度、会话顺序、审批、取消与费用结算；仍在开发。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/application/__init__.py](../../../agent/src/omni/application/__init__.py) | V2 | 1 | V2 use cases consume ports and domain types, never infrastructure. |
| [agent/src/omni/application/action_review.py](../../../agent/src/omni/application/action_review.py) | V2 | 111 | Owner reconciliation records observations, never dispatches an action or rewrites its result. |
| [agent/src/omni/application/android.py](../../../agent/src/omni/application/android.py) | V2 | 802 | Owner Android device grants, ephemeral observations and shared approved actions; no platform I/O. |
| [agent/src/omni/application/background_review.py](../../../agent/src/omni/application/background_review.py) | V2 | 389 | Own one bounded post-turn reviewer per finished run; proposals stay pending until the owner decides. |
| [agent/src/omni/application/bilibili.py](../../../agent/src/omni/application/bilibili.py) | V2 | 318 | Owner Bilibili queries and publication preparation; all sending remains in shared approvals/outbox. |
| [agent/src/omni/application/capabilities.py](../../../agent/src/omni/application/capabilities.py) | V2 | 129 | Register capability providers and capture a deterministic per-run tool surface. |
| [agent/src/omni/application/clarify.py](../../../agent/src/omni/application/clarify.py) | V2 | 42 | Owner-facing clarification use cases; the model-side wait lives in the engine run coroutine. |
| [agent/src/omni/application/configuration.py](../../../agent/src/omni/application/configuration.py) | V2 | 193 | Owner configuration use cases; import only previews and never starts an adapter. |
| [agent/src/omni/application/context.py](../../../agent/src/omni/application/context.py) | V2 | 333 | Token-aware bounded compression; external calls never hold a transaction. |
| [agent/src/omni/application/credentials.py](../../../agent/src/omni/application/credentials.py) | V2 | 185 | Serialize audited owner credential changes; shield accepted mutations through caller cancellation. |
| [agent/src/omni/application/cronjob.py](../../../agent/src/omni/application/cronjob.py) | V2 | 148 | Model-side scheduled-job capability; a thin adapter over the one scheduling service, no second kernel. |
| [agent/src/omni/application/debugging.py](../../../agent/src/omni/application/debugging.py) | V2 | 122 | Boot-scoped debug authorization, durable audit and bounded maintenance; no log files here. |
| [agent/src/omni/application/delivery.py](../../../agent/src/omni/application/delivery.py) | V2 | 447 | Supervised, bounded outbound execution; never supplies a real transport by default. |
| [agent/src/omni/application/desktop.py](../../../agent/src/omni/application/desktop.py) | V2 | 531 | Owner desktop observations with short-lived scoped identities; never authorizes or dispatches desktop writes. |
| [agent/src/omni/application/desktop_actions.py](../../../agent/src/omni/application/desktop_actions.py) | V2 | 224 | Owner-selected semantic commands using the shared run, approval and action ledger. |
| [agent/src/omni/application/desktop_control.py](../../../agent/src/omni/application/desktop_control.py) | V2 | 383 | Owner/view-bound independent system-device consent and durable audit; never authorizes or dispatches an input action. |
| [agent/src/omni/application/desktop_portal.py](../../../agent/src/omni/application/desktop_portal.py) | V2 | 326 | Owner/view-scoped temporary share sessions with durable consent metadata; no image, native identity or input authorization. |
| [agent/src/omni/application/desktop_services.py](../../../agent/src/omni/application/desktop_services.py) | V2 | 18 | Group the desktop services that share the same owner view. |
| [agent/src/omni/application/documents.py](../../../agent/src/omni/application/documents.py) | V2 | 378 | Source documents, explicitly requested local indexes and registered retrieval dependencies. |
| [agent/src/omni/application/engine.py](../../../agent/src/omni/application/engine.py) | V2 | 1514 | One supervised runtime: admission, session FIFO, model/tool execution and shutdown. |
| [agent/src/omni/application/exchange_recovery.py](../../../agent/src/omni/application/exchange_recovery.py) | V2 | 86 | Explicit offline exchange quarantine and new-directory rescue; no native changes or unknown-outcome erasure. |
| [agent/src/omni/application/exchange_recovery_steps.py](../../../agent/src/omni/application/exchange_recovery_steps.py) | V2 | 47 | Reuse release snapshots and fences for explicit quarantine/rescue; never clear a child unknown or call a manager. |
| [agent/src/omni/application/exchange_steps.py](../../../agent/src/omni/application/exchange_steps.py) | V2 | 139 | Compose existing release/registration/control use cases without holding instance locks across target startup. |
| [agent/src/omni/application/extensions.py](../../../agent/src/omni/application/extensions.py) | V2 | 503 | Trusted extension installation, scoped catalogs and explicit authorization; no plugin imports or process creation. |
| [agent/src/omni/application/images.py](../../../agent/src/omni/application/images.py) | V2 | 148 | Validated image admission, context selection and last-mile wire materialization over injected ports. |
| [agent/src/omni/application/learning_services.py](../../../agent/src/omni/application/learning_services.py) | V2 | 14 | Group the learning services used by run processing and the owner workbench. |
| [agent/src/omni/application/local_vectors.py](../../../agent/src/omni/application/local_vectors.py) | V2 | 40 | Shared local-vector preflight and computation; ownership stays with the composing lifecycle. |
| [agent/src/omni/application/maintenance.py](../../../agent/src/omni/application/maintenance.py) | V2 | 267 | Own bounded durable maintenance executions; never runs exclusive offline cleanup or remote model/device actions. |
| [agent/src/omni/application/mcp.py](../../../agent/src/omni/application/mcp.py) | V2 | 543 | Owner MCP servers: explicit connect, bounded catalog, per-tool authorization and run-scoped calls. |
| [agent/src/omni/application/media.py](../../../agent/src/omni/application/media.py) | V2 | 874 | Owner media sources, bounded processing jobs and model-visible anchored transcripts. |
| [agent/src/omni/application/memory_organization.py](../../../agent/src/omni/application/memory_organization.py) | V2 | 144 | Own bounded exact-fact proposals and explicit owner decisions; never calls a model or erases facts. |
| [agent/src/omni/application/memory_semantics.py](../../../agent/src/omni/application/memory_semantics.py) | V2 | 222 | Owned local memory indexing, hybrid recall and explicit related-fact reviews; no automatic merge or deletion. |
| [agent/src/omni/application/memory_services.py](../../../agent/src/omni/application/memory_services.py) | V2 | 14 | Group memory search and organization services. |
| [agent/src/omni/application/memory_tools.py](../../../agent/src/omni/application/memory_tools.py) | V2 | 113 | Memory capabilities share application transactions and verified source rules. |
| [agent/src/omni/application/model_calls.py](../../../agent/src/omni/application/model_calls.py) | V2 | 392 | One preflight, authorization and settlement path for model and compression calls. |
| [agent/src/omni/application/model_output.py](../../../agent/src/omni/application/model_output.py) | V2 | 57 | Bounded previews owned by active model calls; durable messages remain the only completed output. |
| [agent/src/omni/application/objects.py](../../../agent/src/omni/application/objects.py) | V2 | 175 | Object publication and access use cases; no filesystem paths or ORM objects. |
| [agent/src/omni/application/omniweave.py](../../../agent/src/omni/application/omniweave.py) | V2 | 284 | Observe Web admissions and expose scoped planning controls to Router runs. |
| [agent/src/omni/application/operations.py](../../../agent/src/omni/application/operations.py) | V2 | 127 | Bounded operational queries and privacy-preserving diagnostic export; no model or device actions. |
| [agent/src/omni/application/people.py](../../../agent/src/omni/application/people.py) | V2 | 78 | Owner-facing people and personal-document use cases; no ORM or filesystem access. |
| [agent/src/omni/application/persona.py](../../../agent/src/omni/application/persona.py) | V2 | 80 | Owner-only persona growth projection over the approved reflection ledger. |
| [agent/src/omni/application/ports.py](../../../agent/src/omni/application/ports.py) | V2 | 1174 | Application boundaries. Implementations belong to adapters. |
| [agent/src/omni/application/qq.py](../../../agent/src/omni/application/qq.py) | V2 | 227 | Own bounded QQ listeners and owner conversation grants; turns use the shared V2 kernel. |
| [agent/src/omni/application/reflections.py](../../../agent/src/omni/application/reflections.py) | V2 | 148 | Local reflection control and owner review use cases sharing one transactional ledger. |
| [agent/src/omni/application/release_drain.py](../../../agent/src/omni/application/release_drain.py) | V2 | 30 | Bind release draining to the existing once-only boot control; reconciliation never sends another request. |
| [agent/src/omni/application/releases.py](../../../agent/src/omni/application/releases.py) | V2 | 337 | Offline release preparation and single-directory selection; never elevates, registers or starts a service. |
| [agent/src/omni/application/routing.py](../../../agent/src/omni/application/routing.py) | V2 | 19 | Tier configuration, query and bounded control use cases; model invocation remains in ModelCalls. |
| [agent/src/omni/application/scheduling.py](../../../agent/src/omni/application/scheduling.py) | V2 | 249 | One bounded scheduler; durable identities and admission are committed together. |
| [agent/src/omni/application/service_cleanup.py](../../../agent/src/omni/application/service_cleanup.py) | V2 | 78 | Review quarantined native definitions and reuse once-only registration use cases; never clears original unknowns. |
| [agent/src/omni/application/service_control.py](../../../agent/src/omni/application/service_control.py) | V2 | 242 | Reviewed start and graceful stop use cases; external dispatch happens once and unknown work is never replayed. |
| [agent/src/omni/application/service_deployment.py](../../../agent/src/omni/application/service_deployment.py) | V2 | 47 | Prepare reviewable service deployments and combine independent proofs; never installs or starts services. |
| [agent/src/omni/application/service_exchange.py](../../../agent/src/omni/application/service_exchange.py) | V2 | 194 | Ordered service exchange ownership; interrupted native steps are observed, never automatically replayed. |
| [agent/src/omni/application/service_operations.py](../../../agent/src/omni/application/service_operations.py) | V2 | 286 | Offline registration use cases with durable intent, no replay, and retained cancellation ownership. |
| [agent/src/omni/application/session_search.py](../../../agent/src/omni/application/session_search.py) | V2 | 110 | Model-side history recall capability; shares application transactions and the erasure barrier. |
| [agent/src/omni/application/skills.py](../../../agent/src/omni/application/skills.py) | V2 | 448 | Owned textual skill import/edit/disclosure; explicit trust and shared action authorization remain separate. |
| [agent/src/omni/application/terminal_review.py](../../../agent/src/omni/application/terminal_review.py) | V2 | 127 | Owner terminal evidence use cases; no native actions, receipt changes or historical PID operations. |
| [agent/src/omni/application/terminal_services.py](../../../agent/src/omni/application/terminal_services.py) | V2 | 14 | Group owner terminals and their durable operation reviews. |
| [agent/src/omni/application/terminals.py](../../../agent/src/omni/application/terminals.py) | V2 | 506 | Bounded owner terminals with durable input receipts, single-writer leases and explicit output gaps. |
| [agent/src/omni/application/todos.py](../../../agent/src/omni/application/todos.py) | V2 | 49 | Model task-plan control with scoped transactions; creates no external action or background task. |
| [agent/src/omni/application/vision_services.py](../../../agent/src/omni/application/vision_services.py) | V2 | 14 | Group image admission and visual analysis for the instance. |
| [agent/src/omni/application/visual_subagent.py](../../../agent/src/omni/application/visual_subagent.py) | V2 | 337 | Bounded vision sub-agent orchestration over injected frame and model ports. |

文档：[docs/v/reference/V2_REFACTOR.md](../../../docs/v/reference/V2_REFACTOR.md)

测试：[agent/tests/v2/core/test_v2_execution.py](../../../agent/tests/v2/core/test_v2_execution.py)

直接静态依赖：`v2-domain`

## V2 数据与平台适配 · v2-adapters

分类：平台与数据。单库事务、HTTP 功能路由、外部服务、平台桌面与受管工作进程。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/adapters/__init__.py](../../../agent/src/omni/adapters/__init__.py) | V2 | 1 | V2 infrastructure and channel adapters; no import-time external activity. |
| [agent/src/omni/adapters/android.py](../../../agent/src/omni/adapters/android.py) | V2 | 198 | Managed Android workers and bounded PNG loading; no main-process device API or application database. |
| [agent/src/omni/adapters/android_unicode.py](../../../agent/src/omni/adapters/android_unicode.py) | V2 | 160 | Owned Unicode/IME transaction over managed workers; secrets stay in private IPC. |
| [agent/src/omni/adapters/bilibili.py](../../../agent/src/omni/adapters/bilibili.py) | V2 | 996 | Bounded Bilibili reads and a single approved UI publication; credentials never enter diagnostics. |
| [agent/src/omni/adapters/build_identity.py](../../../agent/src/omni/adapters/build_identity.py) | V2 | 5 | Shared inventory of executable V2 resources for runtime and deployment identities. |
| [agent/src/omni/adapters/credentials.py](../../../agent/src/omni/adapters/credentials.py) | V2 | 179 | One bounded, serial credential executor; actual work outlives cancelled request waiters. |
| [agent/src/omni/adapters/diagnostic_bundle.py](../../../agent/src/omni/adapters/diagnostic_bundle.py) | V2 | 342 | Bounded metadata and structured correlations; never opens business bodies or credential stores. |
| [agent/src/omni/adapters/diagnostics.py](../../../agent/src/omni/adapters/diagnostics.py) | V2 | 343 | Bounded, single-writer JSON diagnostics. Business payloads belong in repositories. |
| [agent/src/omni/adapters/extensions/__init__.py](../../../agent/src/omni/adapters/extensions/__init__.py) | V2 | 1 | Trusted Python extension files and process adapters; never opens the application database. |
| [agent/src/omni/adapters/extensions/hosts.py](../../../agent/src/omni/adapters/extensions/hosts.py) | V2 | 208 | Bounded trusted Python worker ownership; no database, model, credential or arbitrary command access. |
| [agent/src/omni/adapters/extensions/packages.py](../../../agent/src/omni/adapters/extensions/packages.py) | V2 | 570 | Bounded zero-execution package capture and immutable verified snapshots; never opens a database. |
| [agent/src/omni/adapters/extensions/worker.py](../../../agent/src/omni/adapters/extensions/worker.py) | V2 | 326 | Static extension handshake and trusted Python invocation in an owned child; never opens the main database. |
| [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py) | V2 | 791 | Versioned HTTP contract for the V2 kernel; business access goes through use cases. |
| [agent/src/omni/adapters/http_events.py](../../../agent/src/omni/adapters/http_events.py) | V2 | 177 | Bounded authenticated history notifications over SSE; no event bodies, replay queue or database ownership. |
| [agent/src/omni/adapters/http_routes/__init__.py](../../../agent/src/omni/adapters/http_routes/__init__.py) | V2 | 137 | Feature routers and bounded input for the authenticated V2 API. |
| [agent/src/omni/adapters/http_routes/action_reviews.py](../../../agent/src/omni/adapters/http_routes/action_reviews.py) | V2 | 44 | Owner review for actions that require durable effect verification. |
| [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py) | V2 | 128 | Owner Android grants, observations and approved actions. |
| [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py) | V2 | 121 | Owner APIs for Bilibili profile revisions, reads and publication drafts. |
| [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py) | V2 | 216 | Model and routing configuration, revisions, probes and Agent settings. |
| [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py) | V2 | 95 | Owner credential registration, changes and authority reviews. |
| [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py) | V2 | 116 | Owner delivery queues, approval, cancellation and unknown-effect review. |
| [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py) | V2 | 108 | Owner desktop views, window sharing, control consent and approved actions. |
| [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py) | V2 | 127 | Source document revisions, bounded indexing and search. |
| [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py) | V2 | 81 | Owner extension market, installation and version-bound authorization. |
| [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py) | V2 | 77 | Owner review for reflection journals and post-turn proposals. |
| [agent/src/omni/adapters/http_routes/maintenance.py](../../../agent/src/omni/adapters/http_routes/maintenance.py) | V2 | 41 | Owner admission, readback and cancellation for maintenance jobs. |
| [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py) | V2 | 131 | Owner operations to connect, authorize and inspect agent-scoped MCP providers. |
| [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py) | V2 | 135 | Owner media sources, job receipts and processor diagnostics. |
| [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py) | V2 | 134 | Owner memory search, organization and evidence-aware changes. |
| [agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py) | V2 | 64 | Expose Ticket progress for the owning Web main conversation. |
| [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py) | V2 | 140 | Read-only audit and diagnostic views, bounded debug controls and retention reports. |
| [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py) | V2 | 152 | Person records, derived persona and associated revision history. |
| [agent/src/omni/adapters/http_routes/qq.py](../../../agent/src/omni/adapters/http_routes/qq.py) | V2 | 66 | Owner APIs for QQ channel status and consenting peer bindings. |
| [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py) | V2 | 101 | Owner APIs for schedule definitions, history and occurrence decisions. |
| [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py) | V2 | 120 | Owner skill editing, activation, version history and run reports. |
| [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py) | V2 | 123 | Owner terminal control, operation receipts and review history. |
| [agent/src/omni/adapters/http_routes/vision.py](../../../agent/src/omni/adapters/http_routes/vision.py) | V2 | 53 | Image admission, preparation and per-run image reports. |
| [agent/src/omni/adapters/images.py](../../../agent/src/omni/adapters/images.py) | V2 | 82 | Bounded image verification and metadata-free low-detail PNG encoding; no database or remote URLs. |
| [agent/src/omni/adapters/local_vectors.py](../../../agent/src/omni/adapters/local_vectors.py) | V2 | 288 | One bounded, supervised local embedding process; no native ML imports in the application process. |
| [agent/src/omni/adapters/log_query.py](../../../agent/src/omni/adapters/log_query.py) | V2 | 320 | Bounded diagnostic pages with cursors that survive rename and process restart. |
| [agent/src/omni/adapters/mcp/__init__.py](../../../agent/src/omni/adapters/mcp/__init__.py) | V2 | 1 | Bounded MCP transports: stdio JSON-RPC, Streamable HTTP and later explicit OAuth. |
| [agent/src/omni/adapters/mcp/hosts.py](../../../agent/src/omni/adapters/mcp/hosts.py) | V2 | 219 | Bounded MCP session ownership keyed by server generation; no database or model access. |
| [agent/src/omni/adapters/mcp/http.py](../../../agent/src/omni/adapters/mcp/http.py) | V2 | 325 | Bounded Streamable HTTP JSON-RPC transport with explicit bearer/OAuth tokens. |
| [agent/src/omni/adapters/mcp/oauth.py](../../../agent/src/omni/adapters/mcp/oauth.py) | V2 | 484 | Explicit MCP OAuth 2.0 authorization-code flow with PKCE, state and a vault token store. |
| [agent/src/omni/adapters/mcp/stdio.py](../../../agent/src/omni/adapters/mcp/stdio.py) | V2 | 322 | Bounded JSON-RPC over one owned stdio process per MCP server generation. |
| [agent/src/omni/adapters/media.py](../../../agent/src/omni/adapters/media.py) | V2 | 343 | One bounded, supervised media processing subprocess; heavy libraries never load in the host. |
| [agent/src/omni/adapters/metrics.py](../../../agent/src/omni/adapters/metrics.py) | V2 | 92 | Fixed-cardinality in-process measurements; never stores request identities or text. |
| [agent/src/omni/adapters/model_stream.py](../../../agent/src/omni/adapters/model_stream.py) | V2 | 240 | One-shot LiteLLM chat stream with verified async upstream close; no persistence or global runtime. |
| [agent/src/omni/adapters/models.py](../../../agent/src/omni/adapters/models.py) | V2 | 300 | Async model adapter. Credentials and vendor exceptions do not cross the port. |
| [agent/src/omni/adapters/native_credentials.py](../../../agent/src/omni/adapters/native_credentials.py) | V2 | 369 | Serial owned OS-credential helpers; cancelled waiters never release native work or stale reads. |
| [agent/src/omni/adapters/object_store.py](../../../agent/src/omni/adapters/object_store.py) | V2 | 142 | Private, immutable content-addressed files; never opens the application database. |
| [agent/src/omni/adapters/onebot.py](../../../agent/src/omni/adapters/onebot.py) | V2 | 199 | V2 OneBot v11 text transport; no V1 runtime, database access or automatic write replay. |
| [agent/src/omni/adapters/persistence/__init__.py](../../../agent/src/omni/adapters/persistence/__init__.py) | V2 | 1 | Only this package owns application SQL, connections and schema evolution. |
| [agent/src/omni/adapters/persistence/action_predicates.py](../../../agent/src/omni/adapters/persistence/action_predicates.py) | V2 | 65 | One current owner-resolution predicate; never rewrites raw action observations or acknowledges model costs. |
| [agent/src/omni/adapters/persistence/action_review_repository.py](../../../agent/src/omni/adapters/persistence/action_review_repository.py) | V2 | 446 | Owner observation ledger for uncertain actions; original actions, costs and delivery state remain unchanged. |
| [agent/src/omni/adapters/persistence/android_repository.py](../../../agent/src/omni/adapters/persistence/android_repository.py) | V2 | 295 | Device grants bind immutable configuration/device identities; actions remain in the shared ledger. |
| [agent/src/omni/adapters/persistence/android_unicode_repository.py](../../../agent/src/omni/adapters/persistence/android_unicode_repository.py) | V2 | 251 | Immutable helper profiles and durable IME restoration obligations; input truth stays in actions. |
| [agent/src/omni/adapters/persistence/audit_pruning.py](../../../agent/src/omni/adapters/persistence/audit_pruning.py) | V2 | 57 | Offline audit-pruning bindings; shared infrastructure owns locks, verified backups and plan files. |
| [agent/src/omni/adapters/persistence/audit_pruning_repository.py](../../../agent/src/omni/adapters/persistence/audit_pruning_repository.py) | V2 | 103 | Persist reviewed audit deletion and its proof atomically; preserve identities and business ledgers. |
| [agent/src/omni/adapters/persistence/audit_retention_repository.py](../../../agent/src/omni/adapters/persistence/audit_retention_repository.py) | V2 | 216 | Bounded metadata-only audit retention preview; never deletes rows or creates executable plans. |
| [agent/src/omni/adapters/persistence/background_review_repository.py](../../../agent/src/omni/adapters/persistence/background_review_repository.py) | V2 | 457 | Bounded post-turn review proposals and owner review; applying still uses the memory/skill chains. |
| [agent/src/omni/adapters/persistence/bilibili_authority.py](../../../agent/src/omni/adapters/persistence/bilibili_authority.py) | V2 | 74 | One Bilibili authority implementation shared by owner reads, previews and final dispatch. |
| [agent/src/omni/adapters/persistence/bilibili_repository.py](../../../agent/src/omni/adapters/persistence/bilibili_repository.py) | V2 | 365 | Immutable Bilibili form revisions and publications referencing the common delivery ledger. |
| [agent/src/omni/adapters/persistence/clarify_repository.py](../../../agent/src/omni/adapters/persistence/clarify_repository.py) | V2 | 181 | Structured clarification requests that pause the current run for the owner and resume the same run. |
| [agent/src/omni/adapters/persistence/configuration_repository.py](../../../agent/src/omni/adapters/persistence/configuration_repository.py) | V2 | 443 | Typed configuration revisions and Agent preferences; no connections, DDL or credentials. |
| [agent/src/omni/adapters/persistence/context_repository.py](../../../agent/src/omni/adapters/persistence/context_repository.py) | V2 | 732 | Revision-bound summaries and context provenance; all operations stay inside a short transaction. |
| [agent/src/omni/adapters/persistence/credential_predicates.py](../../../agent/src/omni/adapters/persistence/credential_predicates.py) | V2 | 7 | One unreviewed credential authority predicate for diagnostics and retention guards. |
| [agent/src/omni/adapters/persistence/credential_repository.py](../../../agent/src/omni/adapters/persistence/credential_repository.py) | V2 | 462 | Owner credential references, operation state and review; no native I/O or connection creation. |
| [agent/src/omni/adapters/persistence/database.py](../../../agent/src/omni/adapters/persistence/database.py) | V2 | 190 | Bounded, single-writer database actor with isolated read connections. |
| [agent/src/omni/adapters/persistence/delegation_authority.py](../../../agent/src/omni/adapters/persistence/delegation_authority.py) | V2 | 106 | Parent-source authority and session invalidation closure for single-level delegated runs. |
| [agent/src/omni/adapters/persistence/delegation_repository.py](../../../agent/src/omni/adapters/persistence/delegation_repository.py) | V2 | 483 | Persist child admission, inherited authority, bounded budgets and recovery in the shared database. |
| [agent/src/omni/adapters/persistence/delivery_predicates.py](../../../agent/src/omni/adapters/persistence/delivery_predicates.py) | V2 | 32 | Shared outbound pending/review predicates for dispatch, scheduling and maintenance queries. |
| [agent/src/omni/adapters/persistence/delivery_repository.py](../../../agent/src/omni/adapters/persistence/delivery_repository.py) | V2 | 927 | Transactional outbound authority, FIFO ownership and immutable attempt observations. |
| [agent/src/omni/adapters/persistence/deployed_instance.py](../../../agent/src/omni/adapters/persistence/deployed_instance.py) | V2 | 30 | Check an initialized service home without migrations or business recovery. |
| [agent/src/omni/adapters/persistence/document_repository.py](../../../agent/src/omni/adapters/persistence/document_repository.py) | V2 | 796 | Source revision, bounded search and derived index transactions; never reads files. |
| [agent/src/omni/adapters/persistence/event_pruning.py](../../../agent/src/omni/adapters/persistence/event_pruning.py) | V2 | 57 | Offline event-pruning bindings; shared infrastructure owns locks, verified backups and plan files. |
| [agent/src/omni/adapters/persistence/event_retention_repository.py](../../../agent/src/omni/adapters/persistence/event_retention_repository.py) | V2 | 332 | Preview and prune retired run events through the shared history cycle; never remove run identities. |
| [agent/src/omni/adapters/persistence/execution_pruning.py](../../../agent/src/omni/adapters/persistence/execution_pruning.py) | V2 | 41 | Execution-body maintenance bindings; shared offline infrastructure owns files and lifecycle. |
| [agent/src/omni/adapters/persistence/execution_retention_repository.py](../../../agent/src/omni/adapters/persistence/execution_retention_repository.py) | V2 | 467 | Execution-body eligibility, owner pins and transactional prune progress; preserves conversations and ledgers. |
| [agent/src/omni/adapters/persistence/extension_repository.py](../../../agent/src/omni/adapters/persistence/extension_repository.py) | V2 | 806 | Immutable trusted-plugin versions, scoped grants and execution bindings; no plugin code or file I/O. |
| [agent/src/omni/adapters/persistence/history_protection.py](../../../agent/src/omni/adapters/persistence/history_protection.py) | V2 | 68 | Shared provenance and maintenance protection for aged pruned history; never reads execution bodies. |
| [agent/src/omni/adapters/persistence/history_pruning_cycle.py](../../../agent/src/omni/adapters/persistence/history_pruning_cycle.py) | V2 | 308 | One transactional history-pruning algorithm; stream policies own target eligibility and identity. |
| [agent/src/omni/adapters/persistence/history_repository.py](../../../agent/src/omni/adapters/persistence/history_repository.py) | V2 | 89 | Read history pages and retention revisions in one transaction; does not delete history. |
| [agent/src/omni/adapters/persistence/history_sequence.py](../../../agent/src/omni/adapters/persistence/history_sequence.py) | V2 | 27 | Allocate global history positions in the caller's transaction; never opens connections. |
| [agent/src/omni/adapters/persistence/image_repository.py](../../../agent/src/omni/adapters/persistence/image_repository.py) | V2 | 367 | Scoped image metadata, model-revision policies and per-call references; never reads files or encodes images. |
| [agent/src/omni/adapters/persistence/lease_repository.py](../../../agent/src/omni/adapters/persistence/lease_repository.py) | V2 | 323 | Persistent FIFO claims and fenced resource leases, within the caller's transaction. |
| [agent/src/omni/adapters/persistence/maintenance.py](../../../agent/src/omni/adapters/persistence/maintenance.py) | V2 | 1067 | Exclusive offline snapshots and restore. Live SQLite files are never copied directly. |
| [agent/src/omni/adapters/persistence/maintenance_repository.py](../../../agent/src/omni/adapters/persistence/maintenance_repository.py) | V2 | 543 | Bounded document-maintenance admission, fenced attempts and business-result settlement; no execution or I/O. |
| [agent/src/omni/adapters/persistence/mcp_repository.py](../../../agent/src/omni/adapters/persistence/mcp_repository.py) | V2 | 1016 | MCP runtime generations, catalog snapshots, per-tool grants, run captures and call records. |
| [agent/src/omni/adapters/persistence/media_repository.py](../../../agent/src/omni/adapters/persistence/media_repository.py) | V2 | 377 | Scoped media sources, bounded jobs and immutable artifact rows; never reads files. |
| [agent/src/omni/adapters/persistence/memory_organization_predicates.py](../../../agent/src/omni/adapters/persistence/memory_organization_predicates.py) | V2 | 114 | One live approved-duplicate predicate for recall and new context capture; never edits memory truth. |
| [agent/src/omni/adapters/persistence/memory_organization_repository.py](../../../agent/src/omni/adapters/persistence/memory_organization_repository.py) | V2 | 622 | Bounded exact-duplicate proposals and owner review; never changes, forgets or copies memory truth. |
| [agent/src/omni/adapters/persistence/memory_repository.py](../../../agent/src/omni/adapters/persistence/memory_repository.py) | V2 | 636 | One memory truth for facts and personal documents, evidence, revisions and erasure. |
| [agent/src/omni/adapters/persistence/memory_semantic_repository.py](../../../agent/src/omni/adapters/persistence/memory_semantic_repository.py) | V2 | 414 | Derived memory vectors and source-bound related-fact decisions; never suppresses or rewrites facts. |
| [agent/src/omni/adapters/persistence/migration_config.py](../../../agent/src/omni/adapters/persistence/migration_config.py) | V2 | 23 | One migration-directory and expected-head lookup for startup and offline readers. |
| [agent/src/omni/adapters/persistence/migrations/__init__.py](../../../agent/src/omni/adapters/persistence/migrations/__init__.py) | V2 | 1 | Fresh V2 baseline; V1 data is never imported. |
| [agent/src/omni/adapters/persistence/migrations/env.py](../../../agent/src/omni/adapters/persistence/migrations/env.py) | V2 | 8 | Alembic environment uses the connection and exclusive ownership of its caller. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0001.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0001.py) | V2 | 93 | Initial V2 schema; immutable SQL snapshot, never imports current metadata. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0002.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0002.py) | V2 | 36 | Object pin revisions and action artifact references; frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0003.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0003.py) | V2 | 150 | People, revision-bound evidence and memory context; frozen schema evolution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0004.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0004.py) | V2 | 99 | Persistent conversation summaries and context barriers; frozen SQL, no current metadata. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0005.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0005.py) | V2 | 254 | Typed business configuration and immutable run references; frozen migration. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0006.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0006.py) | V2 | 85 | Source document revisions, provenance and rebuildable local indexes; frozen sql_statements. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0007.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0007.py) | V2 | 152 | Immutable schedules, due identities and fenced work claims; frozen migration. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0008.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0008.py) | V2 | 101 | Outbound intent, attempt, approval and recovery baseline; frozen SQL only. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0009.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0009.py) | V2 | 30 | Instance-wide credential intent and outcome audit; no arbitrary Agent attribution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0010.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0010.py) | V2 | 120 | Credential authority state and immutable review. Frozen SQL; no V1 credential import. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0011.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0011.py) | V2 | 23 | Owner run retention pins, independent of run execution/configuration revisions. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0012.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0012.py) | V2 | 68 | Recoverable execution-body pruning; frozen SQL, no live model imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0013.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0013.py) | V2 | 61 | Historical reference-release evidence never points back to live object rows. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0014.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0014.py) | V2 | 69 | Persistent sequence allocation and scoped cursor identity precede all history deletion. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0015.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0015.py) | V2 | 74 | Immutable audit-retention plans and deletion evidence; never retain the deleted audit rows. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0016.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0016.py) | V2 | 73 | Immutable event-retention plans and deletion evidence; never retain the deleted event rows. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0017.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0017.py) | V2 | 98 | Durable local document maintenance with owned, fenced attempts and retained evidence. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0018.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0018.py) | V2 | 149 | Exact fact-duplicate organization stores immutable identities and revocable review relationships, never text. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0019.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0019.py) | V2 | 245 | Captured static Python extension revisions and epoch-bound authorization; never migrates old placeholder data. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0020.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0020.py) | V2 | 177 | Immutable owner observations for unknown writes, erasable evidence and source-invalidation fences. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0021.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0021.py) | V2 | 45 | Owner terminal metadata and immutable input receipts; no transcript or native PID execution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0022.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0022.py) | V2 | 27 | Scoped window capture artifacts and publication constraints. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0023.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0023.py) | V2 | 31 | Owner semantic commands: atomic queueing and one-way argument transfer. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0024.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0024.py) | V2 | 30 | Capture provenance for each implemented native backend; preserve existing references and guards. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0025.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0025.py) | V2 | 31 | Portal window frame provenance, retaining scoped references and immutability. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0026.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0026.py) | V2 | 29 | Owner terminal observations, immutable metadata and explicitly erasable evidence. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0027.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0027.py) | V2 | 30 | Explicit QQ conversation grants and atomic inbound run attribution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0028.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0028.py) | V2 | 34 | Reviewed Bilibili profiles and publication references; no duplicate message body or sending state. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0029.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0029.py) | V2 | 51 | Scoped Android grants and shared owner-command/capture contracts; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0030.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0030.py) | V2 | 30 | Normalized per-run Android authority bound to the common approval ledger; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0031.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0031.py) | V2 | 60 | Pinned Unicode helper revisions and durable restoration obligations; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0032.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0032.py) | V2 | 43 | Verified images, immutable revision policies and cumulative call attachments; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0033.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0033.py) | V2 | 52 | Immutable tier policies, atomic run selection and per-call tool snapshots; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0034.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0034.py) | V2 | 51 | Derived memory embeddings and source-bound semantic relationship reviews; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0035.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0035.py) | V2 | 52 | Reviewed textual skills and immutable run disclosure; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0036.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0036.py) | V2 | 37 | Scoped media sources, bounded jobs and immutable anchored artifacts; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0037.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0037.py) | V2 | 53 | MCP runtime generations, catalog fingerprints, per-tool grants, run captures and calls; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0038.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0038.py) | V2 | 26 | Model custom request headers (e.g. OpenCode Go session routing); frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0039.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0039.py) | V2 | 49 | Immutable uploaded media objects in the existing source/job/artifact graph; frozen DDL, transactional rebuild with foreign keys enabled. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0040.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0040.py) | V2 | 29 | Provider-reported prompt cache usage on model calls; frozen SQL, idempotent add-column. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0041.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0041.py) | V2 | 22 | Session planning snapshots; frozen SQL and no current application imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0042.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0042.py) | V2 | 25 | Immutable single-level child-run authority; frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0043.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0043.py) | V2 | 86 | Bounded post-turn review batches and revocable pending proposals; frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0044.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0044.py) | V2 | 45 | Structured clarification requests pausing a run for the owner; frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0045.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0045.py) | V2 | 24 | Heal actions.started_at for databases whose earlier add-column did not land; idempotent. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0046.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0046.py) | V2 | 40 | Per-account Bilibili risk-control pauses; frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0047.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0047.py) | V2 | 52 | Memory decay columns and per-agent decay cursor; frozen SQL, idempotent add-column (v0045 precedent). |
| [agent/src/omni/adapters/persistence/migrations/versions/v0048.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0048.py) | V2 | 55 | Align event_retention_targets kind whitelist with domain KINDS (action.executing/model.started/model.completed); frozen DDL, transactional rebuild preserving every evidence row, in |
| [agent/src/omni/adapters/persistence/migrations/versions/v0049.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0049.py) | V2 | 100 | Scheduled skill bindings and task output references; frozen SQL, idempotent add-column (v0047 precedent). |
| [agent/src/omni/adapters/persistence/migrations/versions/v0050.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0050.py) | V2 | 77 | Tool-result image ledger: nullable message_id, two-branch scope and run-terminal release; frozen DDL, transactional rebuild preserving every accounting row (v0048 precedent). |
| [agent/src/omni/adapters/persistence/migrations/versions/v0051.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0051.py) | V2 | 81 | Document-backed reflection drafts, daily quota slots and erasure propagation. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0052.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0052.py) | V2 | 111 | Reflection media provenance, revision-bound review and owner day slots. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0053.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0053.py) | V2 | 18 | Capture the owner's kernel policy, fallback grants and per-call model identity. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0054.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0054.py) | V2 | 83 | Add scoped OmniWeave observation records to the existing V2 database. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0055.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0055.py) | V2 | 77 | Add durable execution authorization and dependency edges for OmniWeave Tickets. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0056.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0056.py) | V2 | 43 | Give OWA and legacy delegation separate limits while preserving source bindings. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0057.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0057.py) | V2 | 30 | Persist an explicit owner selection independently of historical Router admissions. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0058.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0058.py) | V2 | 71 | Capture immutable root and Worker authority in the V2 database. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0059.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0059.py) | V2 | 107 | Keep Worker execution attempts and directed peer messages in the shared ledger. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0060.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0060.py) | V2 | 89 | Bind immutable artifact versions, directed sharing and subscription receipts. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0061.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0061.py) | V2 | 67 | Bind owner goal updates to the original Ticket and fence Worker versions. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0062.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0062.py) | V2 | 56 | Record one Router delivery and its message per admitted goal version. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0063.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0063.py) | V2 | 50 | Reference prior accepted Worker evidence and authorized peer citations. |
| [agent/src/omni/adapters/persistence/object_gc.py](../../../agent/src/omni/adapters/persistence/object_gc.py) | V2 | 248 | Exclusive plan/apply collection; commit unreferenced metadata before unlinking files. |
| [agent/src/omni/adapters/persistence/object_inventory.py](../../../agent/src/omni/adapters/persistence/object_inventory.py) | V2 | 129 | Offline object registry snapshots and bounded physical inventory validation. |
| [agent/src/omni/adapters/persistence/object_repository.py](../../../agent/src/omni/adapters/persistence/object_repository.py) | V2 | 233 | Object metadata, pins and real foreign-key references; never touches files. |
| [agent/src/omni/adapters/persistence/offline_plans.py](../../../agent/src/omni/adapters/persistence/offline_plans.py) | V2 | 190 | Exclusive reviewed maintenance plans; domain callbacks own eligibility and transactional progress. |
| [agent/src/omni/adapters/persistence/omniweave_artifacts.py](../../../agent/src/omni/adapters/persistence/omniweave_artifacts.py) | V2 | 398 | Versioned objects and explicit reader grants scoped to a Worker plan. |
| [agent/src/omni/adapters/persistence/omniweave_attempts.py](../../../agent/src/omni/adapters/persistence/omniweave_attempts.py) | V2 | 116 | Read Worker attempt identities and allocate later Runs in the original Session. |
| [agent/src/omni/adapters/persistence/omniweave_authority.py](../../../agent/src/omni/adapters/persistence/omniweave_authority.py) | V2 | 114 | Check immutable OmniWeave authority against the live V2 execution fences. |
| [agent/src/omni/adapters/persistence/omniweave_delivery.py](../../../agent/src/omni/adapters/persistence/omniweave_delivery.py) | V2 | 555 | Reduce durable Worker results into the original private Web conversation. |
| [agent/src/omni/adapters/persistence/omniweave_goals.py](../../../agent/src/omni/adapters/persistence/omniweave_goals.py) | V2 | 72 | Read the execution and reconciliation scope of a versioned Ticket. |
| [agent/src/omni/adapters/persistence/omniweave_mailbox.py](../../../agent/src/omni/adapters/persistence/omniweave_mailbox.py) | V2 | 691 | Deliver scoped peer traffic through durable V2 Worker turns. |
| [agent/src/omni/adapters/persistence/omniweave_repository.py](../../../agent/src/omni/adapters/persistence/omniweave_repository.py) | V2 | 719 | Persist scoped OmniWeave admissions, execution plans and Worker dependencies. |
| [agent/src/omni/adapters/persistence/operations_read.py](../../../agent/src/omni/adapters/persistence/operations_read.py) | V2 | 58 | Offline read-only operational snapshot; caller holds exclusive instance ownership. |
| [agent/src/omni/adapters/persistence/operations_repository.py](../../../agent/src/omni/adapters/persistence/operations_repository.py) | V2 | 100 | Operational aggregates and owner credential audit; business bodies and secret values stay excluded. |
| [agent/src/omni/adapters/persistence/people_repository.py](../../../agent/src/omni/adapters/persistence/people_repository.py) | V2 | 212 | People and manual account identities; personal documents reuse memory repositories. |
| [agent/src/omni/adapters/persistence/qq_authority.py](../../../agent/src/omni/adapters/persistence/qq_authority.py) | V2 | 99 | Shared QQ audience and credential authority for context, model and outbound checks. |
| [agent/src/omni/adapters/persistence/qq_repository.py](../../../agent/src/omni/adapters/persistence/qq_repository.py) | V2 | 242 | QQ admission and authority within the shared unit of work; no network or extra truth store. |
| [agent/src/omni/adapters/persistence/reference_release.py](../../../agent/src/omni/adapters/persistence/reference_release.py) | V2 | 41 | Offline reference release uses the shared verified maintenance lifecycle; never unlinks objects. |
| [agent/src/omni/adapters/persistence/reference_release_repository.py](../../../agent/src/omni/adapters/persistence/reference_release_repository.py) | V2 | 330 | Release only reviewed retired action-object references; history is not a live object reference. |
| [agent/src/omni/adapters/persistence/reflection_media.py](../../../agent/src/omni/adapters/persistence/reflection_media.py) | V2 | 97 | Resolve media evidence from settled tool receipts and the tool-image dispatch ledger. |
| [agent/src/omni/adapters/persistence/reflection_repository.py](../../../agent/src/omni/adapters/persistence/reflection_repository.py) | V2 | 496 | Scoped reflection review, exact excerpts and atomic owner decisions. |
| [agent/src/omni/adapters/persistence/releases.py](../../../agent/src/omni/adapters/persistence/releases.py) | V2 | 215 | Consistent release snapshots and quarantined new-directory restores under caller-owned instance locks. |
| [agent/src/omni/adapters/persistence/repository.py](../../../agent/src/omni/adapters/persistence/repository.py) | V2 | 1312 | Transaction-scoped repositories. No connection creation, DDL or external calls. |
| [agent/src/omni/adapters/persistence/repository_base.py](../../../agent/src/omni/adapters/persistence/repository_base.py) | V2 | 52 | Common transaction-scoped identity, ownership and audit primitives. |
| [agent/src/omni/adapters/persistence/retention_repository.py](../../../agent/src/omni/adapters/persistence/retention_repository.py) | V2 | 150 | Context barriers invalidate derived views without deleting original conversations. |
| [agent/src/omni/adapters/persistence/routing_repository.py](../../../agent/src/omni/adapters/persistence/routing_repository.py) | V2 | 379 | Scoped immutable tier policies, run choices, promotions and actual tool manifests; no model calls or I/O. |
| [agent/src/omni/adapters/persistence/run_checkpoint_repository.py](../../../agent/src/omni/adapters/persistence/run_checkpoint_repository.py) | V2 | 207 | Use the turn queue's existing payload, instance lock and lease for safe restart. |
| [agent/src/omni/adapters/persistence/schedule_repository.py](../../../agent/src/omni/adapters/persistence/schedule_repository.py) | V2 | 638 | Immutable schedule definitions, durable due cursors and atomic occurrence admission. |
| [agent/src/omni/adapters/persistence/schema.py](../../../agent/src/omni/adapters/persistence/schema.py) | V2 | 3866 | V2 relational schema. Repositories never issue DDL; migrations use frozen SQL snapshots. |
| [agent/src/omni/adapters/persistence/schema_upgrade.py](../../../agent/src/omni/adapters/persistence/schema_upgrade.py) | V2 | 161 | Reviewed migration catalog and transactional upgrades only in a locked, fenced release target. |
| [agent/src/omni/adapters/persistence/search_schema.py](../../../agent/src/omni/adapters/persistence/search_schema.py) | V2 | 8 | Derived FTS definitions. Applied only by versioned migrations. |
| [agent/src/omni/adapters/persistence/session_search_repository.py](../../../agent/src/omni/adapters/persistence/session_search_repository.py) | V2 | 265 | Bounded read-only history recall over the shared session/message store. |
| [agent/src/omni/adapters/persistence/skill_repository.py](../../../agent/src/omni/adapters/persistence/skill_repository.py) | V2 | 476 | Immutable skill versions, review state and per-run bindings; no package or filesystem access. |
| [agent/src/omni/adapters/persistence/sqlalchemy_access.py](../../../agent/src/omni/adapters/persistence/sqlalchemy_access.py) | V2 | 33 | Shared SQLAlchemy SQLite transaction setup for the actor and exclusive offline migration. |
| [agent/src/omni/adapters/persistence/sqlite_access.py](../../../agent/src/omni/adapters/persistence/sqlite_access.py) | V2 | 32 | Owned SQLite connections for offline inspection, backup and restore only. |
| [agent/src/omni/adapters/persistence/terminal_repository.py](../../../agent/src/omni/adapters/persistence/terminal_repository.py) | V2 | 328 | Terminal intent transactions and immutable metadata receipts; never opens a native process or stores terminal bodies. |
| [agent/src/omni/adapters/persistence/terminal_review_repository.py](../../../agent/src/omni/adapters/persistence/terminal_review_repository.py) | V2 | 238 | Append owner observations and erasable evidence; terminal receipts and native lifecycle remain unchanged. |
| [agent/src/omni/adapters/persistence/todo_repository.py](../../../agent/src/omni/adapters/persistence/todo_repository.py) | V2 | 90 | Session task snapshots in the shared transaction; never dispatches or approves work. |
| [agent/src/omni/adapters/persona_files.py](../../../agent/src/omni/adapters/persona_files.py) | V2 | 154 | Owner-only managed persona growth cache; validates bytes against the ledger expectation. |
| [agent/src/omni/adapters/platform/__init__.py](../../../agent/src/omni/adapters/platform/__init__.py) | V2 | 1 | Portable paths, ownership, credentials and platform capability implementations. |
| [agent/src/omni/adapters/platform/desktop/__init__.py](../../../agent/src/omni/adapters/platform/desktop/__init__.py) | V2 | 5 | Desktop transport public entry; platform probing only occurs through explicit calls. |
| [agent/src/omni/adapters/platform/desktop/backend.py](../../../agent/src/omni/adapters/platform/desktop/backend.py) | V2 | 693 | Bounded, one-shot desktop metadata helpers; construction never probes a native desktop. |
| [agent/src/omni/adapters/platform/desktop/desktop_capture.py](../../../agent/src/omni/adapters/platform/desktop/desktop_capture.py) | V2 | 83 | Host-owned screenshot staging. File decoding runs only on the desktop I/O worker. |
| [agent/src/omni/adapters/platform/desktop/desktop_control.py](../../../agent/src/omni/adapters/platform/desktop/desktop_control.py) | V2 | 407 | Owned parent transport for independent control consent; no input dispatch, capture grants or unowned process termination. |
| [agent/src/omni/adapters/platform/desktop/desktop_native.py](../../../agent/src/omni/adapters/platform/desktop/desktop_native.py) | V2 | 493 | Read-only desktop diagnostics and process/window identities; never reads UI contents or performs input. |
| [agent/src/omni/adapters/platform/desktop/linux/__init__.py](../../../agent/src/omni/adapters/platform/desktop/linux/__init__.py) | V2 | 1 | V2 desktop/linux adapters; importing never probes the platform. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_atspi.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_atspi.py) | V2 | 366 | Read-only AT-SPI windows in a proven logind session. No X11/Portal capture or input fallback. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_bus.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_bus.py) | V2 | 447 | Verified local D-Bus transport for desktop helpers; no environment-selected or network buses. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_button_events.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_button_events.py) | V2 | 70 | Exact RawButton pairs and their ordinary XKB pointer-state notifications; no blanket input exemptions. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_button_mapping.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_button_mapping.py) | V2 | 221 | Read primary-button composition on a proven Xorg point/source pair; never changes maps, moves, clicks or grants dispatch. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_button_packet.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_button_packet.py) | V2 | 197 | Own one paired XTEST button request and retained release on the proven stream; no application-success claim. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_click_action.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_click_action.py) | V2 | 119 | One approved component-bound native move/click with concrete application state readback; no replay or focus acquisition. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_eis.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_eis.py) | V2 | 286 | Bounded EI handshake/roundtrips and owned release lifetime; no business input authority or application proof. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_eis_devices.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_eis_devices.py) | V2 | 535 | EI device inventory, key plans and owned transport; no business authority, physical-input or application proof. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_eis_key_packet.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_eis_key_packet.py) | V2 | 159 | Single EI key pair with separate frames, exact partial writes and retained release/barrier; no business permission or app proof. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_eis_keyboard.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_eis_keyboard.py) | V2 | 84 | Bind interpreted special keys to exact live EI inventory/map; planning only, no physical-input proof or dispatch. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_eis_keymap.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_eis_keymap.py) | V2 | 93 | Bounded EI keymap copy with original FD/thread lifetime; no keymap execution, layout inference or input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_evdev_input.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_evdev_input.py) | V2 | 195 | Owned read-only evdev neutral-state/readiness checks; no event contents, grabs, writes, permission or routing changes. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_fds.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_fds.py) | V2 | 109 | Owned received descriptors and bounded SCM_RIGHTS; no bus or desktop policy. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_focus_navigation.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_focus_navigation.py) | V2 | 171 | Bounded complete AT-SPI focus readback in one native-bound window; never predicts tab order or sends input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_foreground.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_foreground.py) | V2 | 424 | Read-only X11 focus ancestry proof for future explicit foreground delivery; never maps AT-SPI nodes or sends input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_input.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_input.py) | V2 | 453 | Verified Xorg input-change guard for addressed semantic actions; never grabs, synthesizes or stores input content. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_key_action.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_key_action.py) | V2 | 171 | One approved native key pair for an explicitly bound focused semantic node; no focus acquisition or replay. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_key_switch.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_key_switch.py) | V2 | 125 | Prove the single Xorg keyboard slave switch surrounding an owned key pair; no input or configuration writes. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_keyboard.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_keyboard.py) | V2 | 165 | Bounded read-only core keymap preflight on an existing focus proof; never translates Unicode, changes layouts or sends input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_keystroke.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_keystroke.py) | V2 | 157 | Single paired XTEST wire delivery and retained release; business target/permit integration remains external. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_lease_client.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_lease_client.py) | V2 | 335 | Owned persistent X11 guardian transport; no native APIs, target rebinding from verify, unbounded queues or input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_motion_events.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_motion_events.py) | V2 | 68 | Exact two-axis RawMotion receipts belonging to one reserved XTEST request. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_motion_packet.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_motion_packet.py) | V2 | 162 | Own one XTEST absolute move and its acknowledgment; no business click or automatic retry. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_node_point.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_node_point.py) | V2 | 159 | Bind actual AT-SPI component hit/physical extents to a continuous X11 window; no scaling guesses or input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pipewire.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pipewire.py) | V2 | 256 | One serial-targeted GStreamer PipeWire frame inside an owned helper, never the default remote or display. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pointer_actions.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pointer_actions.py) | V2 | 140 | Read XKB pointer actions and released keyboard state on a mapped native point; never moves, clicks or grants input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pointer_config.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pointer_config.py) | V2 | 281 | Read canonical pointer axes, feedback and transforms on an existing XKB/point proof; no device writes or mouse dispatch. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pointer_layout.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pointer_layout.py) | V2 | 240 | Bind a planned pointer move to live single-root RandR resources and active scanouts; no input or layout mutation. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pointer_motion.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pointer_motion.py) | V2 | 229 | Plan signed core XTEST coordinates and raw valuators from proven native geometry/configuration; never sends input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pointer_switch.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pointer_switch.py) | V2 | 141 | Precompute and verify one owned pointer slave-class copy; never writes device configuration. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_pointer_target.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_pointer_target.py) | V2 | 215 | Read a bounded Xorg hit path for one physical window point; no pointer motion, button input or AT-SPI coordinate inference. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_portal.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_portal.py) | V2 | 229 | Explicit ephemeral window selection on a pinned Wayland portal; never opens PipeWire FDs or dispatches input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_remote_desktop.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_remote_desktop.py) | V2 | 298 | Separate Wayland device consent and owned EI core negotiation; no ScreenCast reuse, clipboard, restored authority or input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_seat_input.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_seat_input.py) | V2 | 182 | One seat's kernel-input guard with owned executor/FD watches; no authority, input data capture or compositor-target proof. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_semantic.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_semantic.py) | V2 | 320 | Addressed AT-SPI semantic proof and single mutation; no global input, focus acquisition or retry. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_session.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_session.py) | V2 | 366 | logind display-session and accessibility bus ownership; never trusts desktop environment variables. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_signals.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_signals.py) | V2 | 103 | Bounded, pinned D-Bus signals on the same pre-authenticated local connection; no service activation or FDs. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_text.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_text.py) | V2 | 100 | One addressed Unicode append in a manually focused AT-SPI editor; no key synthesis, clipboard, focus acquisition or retry. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_udev_input.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_udev_input.py) | V2 | 210 | Read-only seat input catalog with kernel/udev change FDs; no input reads, grants or device configuration. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_wayland_peer.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_wayland_peer.py) | V2 | 119 | Bind a canonical Wayland socket to the actual granted EI peer; no environment discovery, authority or protocol requests. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_wayland_windows.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_wayland_windows.py) | V2 | 353 | Read-only wlroots toplevel generations on the granted EI compositor; no app PID inference, focus acquisition or input authority. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_wayland_wire.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_wayland_wire.py) | V2 | 131 | Bounded read-only Wayland display/registry/output transport; no surfaces, grabs, pixel capture or input commands. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_window_binding.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_window_binding.py) | V2 | 141 | Bind an explicit AT-SPI provider XID to continuous native ownership/focus; no guessed mapping, focus acquisition or input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_window_leases.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_window_leases.py) | V2 | 137 | Continuous X11 window generations on one proven connection; no input, AT-SPI mapping or database access. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_x11.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_x11.py) | V2 | 262 | XRes-bound local X11 targets and existing Composite pixmaps; never captures the root or sends input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_x11_socket.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_x11_socket.py) | V2 | 251 | Canonical local X11 sockets and private Xauthority; verify peers before sending credentials. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_x11_wire.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_x11_wire.py) | V2 | 309 | Small bounded X11 read protocol on an already verified socket; no discovery, input or reconnect. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_xkb.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_xkb.py) | V2 | 263 | Read-only XKB server state/actions on the existing input connection; no layout mutation, device acquisition or event dispatch. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_xkb_copy.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_xkb_copy.py) | V2 | 176 | Canonical bounded identities for XKB copied compatibility, names, indicator maps and current geometry; no writes. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_xkb_map.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_xkb_map.py) | V2 | 107 | Bounded canonical identity of a full native XKB map; no key translation, mutation or input dispatch. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_xkbcommon.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_xkbcommon.py) | V2 | 191 | Owned libxkbcommon buffer compilation and special-key interpretation; no default layout, includes or native input. |
| [agent/src/omni/adapters/platform/desktop/linux/linux_xtest.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_xtest.py) | V2 | 130 | Read-only Xorg XTEST device identity proof; no input dispatch or own-event exemption. |
| [agent/src/omni/adapters/platform/desktop/macos_helper.py](../../../agent/src/omni/adapters/platform/desktop/macos_helper.py) | V2 | 114 | Resolve the fixed, owner-installed Swift bundle; never build, install or request TCC permissions. |
| [agent/src/omni/adapters/platform/desktop/macos_launch.py](../../../agent/src/omni/adapters/platform/desktop/macos_launch.py) | V2 | 255 | Owner-only Unix-socket launcher for the fixed macOS helper. |
| [agent/src/omni/adapters/platform/desktop/portal.py](../../../agent/src/omni/adapters/platform/desktop/portal.py) | V2 | 506 | Owned long-lived Portal helper sessions with shared desktop capacity and bounded metadata pipes; no pixel or input dispatch. |
| [agent/src/omni/adapters/platform/releases/__init__.py](../../../agent/src/omni/adapters/platform/releases/__init__.py) | V2 | 1 | V2 releases adapters; importing never probes the platform. |
| [agent/src/omni/adapters/platform/releases/candidate_installer.py](../../../agent/src/omni/adapters/platform/releases/candidate_installer.py) | V2 | 131 | Bootstrap a fresh offline V2 candidate with a separate maintenance interpreter; never registers services. |
| [agent/src/omni/adapters/platform/releases/dependency_bundle.py](../../../agent/src/omni/adapters/platform/releases/dependency_bundle.py) | V2 | 252 | Capture a hash-pinned local wheel bundle for an isolated environment; never downloads or executes wheels. |
| [agent/src/omni/adapters/platform/releases/release_files.py](../../../agent/src/omni/adapters/platform/releases/release_files.py) | V2 | 411 | Private release journals, installation bytes and startup fences; no database or native registration. |
| [agent/src/omni/adapters/platform/releases/wheel_artifact.py](../../../agent/src/omni/adapters/platform/releases/wheel_artifact.py) | V2 | 61 | Read-only wheel artifact verification: hash, archive safety and V2 asset presence; never installs. |
| [agent/src/omni/adapters/platform/releases/wheel_backup.py](../../../agent/src/omni/adapters/platform/releases/wheel_backup.py) | V2 | 68 | Execute only the backup step of a reviewed switch plan; no stop, switch, migration or rollback. |
| [agent/src/omni/adapters/platform/releases/wheel_environment.py](../../../agent/src/omni/adapters/platform/releases/wheel_environment.py) | V2 | 249 | Create an offline candidate environment: a venv that installs the staged wheel without a network. |
| [agent/src/omni/adapters/platform/releases/wheel_install.py](../../../agent/src/omni/adapters/platform/releases/wheel_install.py) | V2 | 411 | Execute the switch step: point the runtime at a prepared candidate; no stop, migrate or start. |
| [agent/src/omni/adapters/platform/releases/wheel_prepare.py](../../../agent/src/omni/adapters/platform/releases/wheel_prepare.py) | V2 | 121 | Safely unpack one staged wheel into a candidate tree; never creates a venv, switches or migrates. |
| [agent/src/omni/adapters/platform/releases/wheel_rollback.py](../../../agent/src/omni/adapters/platform/releases/wheel_rollback.py) | V2 | 71 | Rollback drill: restore the update backup into a new sibling directory and verify it; never touches live home. |
| [agent/src/omni/adapters/platform/releases/wheel_smoke.py](../../../agent/src/omni/adapters/platform/releases/wheel_smoke.py) | V2 | 115 | Smoke-test a prepared candidate tree in a child interpreter; no install, switch or migration. |
| [agent/src/omni/adapters/platform/releases/wheel_stage.py](../../../agent/src/omni/adapters/platform/releases/wheel_stage.py) | V2 | 76 | Atomic local staging of a verified wheel; never extracts, switches directories or runs migrations. |
| [agent/src/omni/adapters/platform/releases/wheel_status.py](../../../agent/src/omni/adapters/platform/releases/wheel_status.py) | V2 | 119 | Read-only installation/update status projection; never mutates, plans, switches or migrates. |
| [agent/src/omni/adapters/platform/releases/wheel_switch.py](../../../agent/src/omni/adapters/platform/releases/wheel_switch.py) | V2 | 85 | Read-only switch plan for a prepared candidate; never stops, switches, migrates or rolls back. |
| [agent/src/omni/adapters/platform/releases/wheel_uninstall.py](../../../agent/src/omni/adapters/platform/releases/wheel_uninstall.py) | V2 | 218 | Remove one staged candidate or deactivate the runtime pointer; data and installation receipts stay. |
| [agent/src/omni/adapters/platform/runtime/__init__.py](../../../agent/src/omni/adapters/platform/runtime/__init__.py) | V2 | 1 | V2 runtime adapters; importing never probes the platform. |
| [agent/src/omni/adapters/platform/runtime/event_loop.py](../../../agent/src/omni/adapters/platform/runtime/event_loop.py) | V2 | 105 | Own the Windows TCP reset workaround without patching global asyncio classes. |
| [agent/src/omni/adapters/platform/runtime/paths.py](../../../agent/src/omni/adapters/platform/runtime/paths.py) | V2 | 161 | Resolve writable instance paths independently from installed code and cwd. |
| [agent/src/omni/adapters/platform/runtime/permissions.py](../../../agent/src/omni/adapters/platform/runtime/permissions.py) | V2 | 225 | Current-owner private files on POSIX and NTFS; never changes ancestor ACLs. |
| [agent/src/omni/adapters/platform/runtime/processes.py](../../../agent/src/omni/adapters/platform/runtime/processes.py) | V2 | 232 | Owned process groups and Windows Job Objects. Never terminate an unverified PID. |
| [agent/src/omni/adapters/platform/runtime/publication.py](../../../agent/src/omni/adapters/platform/runtime/publication.py) | V2 | 70 | Atomic, exclusive directory publication on the supported hosts; never replaces an existing target. |
| [agent/src/omni/adapters/platform/runtime/runtime_identity.py](../../../agent/src/omni/adapters/platform/runtime/runtime_identity.py) | V2 | 242 | Publish private boot identity while the instance lock is held; never controls another process. |
| [agent/src/omni/adapters/platform/runtime/secrets.py](../../../agent/src/omni/adapters/platform/runtime/secrets.py) | V2 | 159 | Explicit OS credential store or externally keyed encrypted vault; no plaintext fallback. |
| [agent/src/omni/adapters/platform/runtime/terminal.py](../../../agent/src/omni/adapters/platform/runtime/terminal.py) | V2 | 588 | Owned terminal IPC. Native PTY libraries run only in the fixed helper process. |
| [agent/src/omni/adapters/platform/runtime/workbench.py](../../../agent/src/omni/adapters/platform/runtime/workbench.py) | V2 | 120 | Open an explicitly requested local workbench after proving this process's ready boot; never reads owner tokens. |
| [agent/src/omni/adapters/platform/runtime/workspaces.py](../../../agent/src/omni/adapters/platform/runtime/workspaces.py) | V2 | 51 | Workspace admission keeps instance control, secrets and installed code private. |
| [agent/src/omni/adapters/platform/services/__init__.py](../../../agent/src/omni/adapters/platform/services/__init__.py) | V2 | 1 | V2 services adapters; importing never probes the platform. |
| [agent/src/omni/adapters/platform/services/darwin_admin_commands.py](../../../agent/src/omni/adapters/platform/services/darwin_admin_commands.py) | V2 | 276 | Single-threaded Darwin command ownership; pre-exec permit, kqueue no-fork proof and unreaped group anchor. |
| [agent/src/omni/adapters/platform/services/darwin_admin_files.py](../../../agent/src/omni/adapters/platform/services/darwin_admin_files.py) | V2 | 184 | Darwin local-volume ACL and root file proofs; no application storage or arbitrary library lookup. |
| [agent/src/omni/adapters/platform/services/darwin_admin_peer.py](../../../agent/src/omni/adapters/platform/services/darwin_admin_peer.py) | V2 | 30 | Kernel Unix peer UID/GID and PID; never accepts a TCP credential result or an empty native option. |
| [agent/src/omni/adapters/platform/services/darwin_admin_policy.py](../../../agent/src/omni/adapters/platform/services/darwin_admin_policy.py) | V2 | 181 | Exact local-account Darwin administrator policy, protected root artifacts and shared audit bytes. |
| [agent/src/omni/adapters/platform/services/darwin_admin_probe.py](../../../agent/src/omni/adapters/platform/services/darwin_admin_probe.py) | V2 | 154 | Bounded public CoreFoundation/ServiceManagement reads for a separately owned root worker; no mutation or GUI. |
| [agent/src/omni/adapters/platform/services/darwin_service_admin.py](../../../agent/src/omni/adapters/platform/services/darwin_service_admin.py) | V2 | 147 | Darwin registration and single-start root backend; fixed launchctl operations, no GUI, database or force. |
| [agent/src/omni/adapters/platform/services/deployment_files.py](../../../agent/src/omni/adapters/platform/services/deployment_files.py) | V2 | 631 | Capture installation identity and publish reviewed deployment bundles; never registers services or opens business storage. |
| [agent/src/omni/adapters/platform/services/exchange_deployments.py](../../../agent/src/omni/adapters/platform/services/exchange_deployments.py) | V2 | 27 | Verify a reviewed source for removal or signed boot draining; never execute or import the source installation. |
| [agent/src/omni/adapters/platform/services/exchange_files.py](../../../agent/src/omni/adapters/platform/services/exchange_files.py) | V2 | 284 | Private bounded exchange witnesses and maintenance reservations; no database, native calls or rollback. |
| [agent/src/omni/adapters/platform/services/macos_service.py](../../../agent/src/omni/adapters/platform/services/macos_service.py) | V2 | 109 | Inspect loaded launchd descriptions via the fixed Swift helper; never parses launchctl print as authority. |
| [agent/src/omni/adapters/platform/services/macos_service_state.py](../../../agent/src/omni/adapters/platform/services/macos_service_state.py) | V2 | 114 | Pure bounded launchd definition and override comparison shared by owner and administrator ports. |
| [agent/src/omni/adapters/platform/services/service_admin.py](../../../agent/src/omni/adapters/platform/services/service_admin.py) | V2 | 364 | Fixed Linux system-service registration/start broker; no owner code execution, business DB, stop or shell. |
| [agent/src/omni/adapters/platform/services/service_admin_bundle.py](../../../agent/src/omni/adapters/platform/services/service_admin_bundle.py) | V2 | 144 | Publish a reviewable stdlib-only administrator artifact; never installs it or elevates privileges. |
| [agent/src/omni/adapters/platform/services/service_admin_client.py](../../../agent/src/omni/adapters/platform/services/service_admin_client.py) | V2 | 446 | Owner-side pinned root broker connection; no elevation, root code launch or automatic request retry. |
| [agent/src/omni/adapters/platform/services/service_admin_commands.py](../../../agent/src/omni/adapters/platform/services/service_admin_commands.py) | V2 | 220 | Single-threaded root manager process ownership using pidfds and subreaping; never uses historical PIDs. |
| [agent/src/omni/adapters/platform/services/service_admin_policy.py](../../../agent/src/omni/adapters/platform/services/service_admin_policy.py) | V2 | 329 | Root-only fixed Linux paths and append-only dispatch evidence; never opens owner business storage. |
| [agent/src/omni/adapters/platform/services/service_cleanup_files.py](../../../agent/src/omni/adapters/platform/services/service_cleanup_files.py) | V2 | 265 | Separate cleanup journals and two-home reservations; preserves old unknown records, fences and business data. |
| [agent/src/omni/adapters/platform/services/service_control_files.py](../../../agent/src/omni/adapters/platform/services/service_control_files.py) | V2 | 126 | Service lifecycle journal and once-only startup reservation; reuses registration audit and file-lock ownership. |
| [agent/src/omni/adapters/platform/services/service_handover.py](../../../agent/src/omni/adapters/platform/services/service_handover.py) | V2 | 77 | Two pinned root phases, opened in order and owned until native lock release; never elevates or spawns a broker. |
| [agent/src/omni/adapters/platform/services/service_launch.py](../../../agent/src/omni/adapters/platform/services/service_launch.py) | V2 | 95 | Once-only reviewed boot reservations under the instance lock; never starts a process or reads credentials. |
| [agent/src/omni/adapters/platform/services/service_operation_files.py](../../../agent/src/omni/adapters/platform/services/service_operation_files.py) | V2 | 274 | Private offline maintenance journal; never migrates, replays or removes instance data. |
| [agent/src/omni/adapters/platform/services/service_query.py](../../../agent/src/omni/adapters/platform/services/service_query.py) | V2 | 591 | Read-only OS service inspection with bounded output and retained process ownership. |
| [agent/src/omni/adapters/platform/services/service_readiness.py](../../../agent/src/omni/adapters/platform/services/service_readiness.py) | V2 | 307 | Bounded, per-boot HMAC readiness proof. Never reads or sends an owner credential. |
| [agent/src/omni/adapters/platform/services/service_registration.py](../../../agent/src/omni/adapters/platform/services/service_registration.py) | V2 | 442 | Native user-service registration with fixed commands and scoped macOS unload; no elevation or data deletion. |
| [agent/src/omni/adapters/platform/services/service_templates.py](../../../agent/src/omni/adapters/platform/services/service_templates.py) | V2 | 219 | Deterministic service artifacts. Rendering never installs or controls a service. |
| [agent/src/omni/adapters/platform/services/systemd_state.py](../../../agent/src/omni/adapters/platform/services/systemd_state.py) | V2 | 101 | Shared bounded systemd decoding; no command execution or filesystem access. |
| [agent/src/omni/adapters/primitives.py](../../../agent/src/omni/adapters/primitives.py) | V2 | 20 | Real clocks and full UUID identities, injected by the composition root. |
| [agent/src/omni/adapters/process_diagnostics.py](../../../agent/src/omni/adapters/process_diagnostics.py) | V2 | 112 | Process-owned logging and exception hooks; installed only by the executable host. |
| [agent/src/omni/adapters/skill_packages.py](../../../agent/src/omni/adapters/skill_packages.py) | V2 | 177 | Bounded in-memory Markdown/ZIP parsing; never extracts files or executes package content. |
| [agent/src/omni/adapters/soak.py](../../../agent/src/omni/adapters/soak.py) | V2 | 402 | Proof-bound read-only V2 observation and append-only evidence; no V1, service control, models or database opens. |
| [agent/src/omni/adapters/som.py](../../../agent/src/omni/adapters/som.py) | V2 | 71 | Deterministic bounded SoM PNG rendering; no capture, database or model calls. |
| [agent/src/omni/adapters/temporary_debug.py](../../../agent/src/omni/adapters/temporary_debug.py) | V2 | 322 | Boot-bound, owner-enabled metadata capture; never opens business storage or records free text. |
| [agent/src/omni/adapters/timezones.py](../../../agent/src/omni/adapters/timezones.py) | V2 | 29 | Pinned tzdata resolver; avoids OS-dependent timezone database drift. |
| [agent/src/omni/adapters/token_counter.py](../../../agent/src/omni/adapters/token_counter.py) | V2 | 54 | Versioned UTF-8 token envelope: v1 counts bytes, v2 converts conservatively to tokens. |
| [agent/src/omni/adapters/vector_files.py](../../../agent/src/omni/adapters/vector_files.py) | V2 | 131 | Verify explicit local vector files and startup selection; never imports a model or downloads data. |
| [agent/src/omni/adapters/visual.py](../../../agent/src/omni/adapters/visual.py) | V2 | 214 | Production visual-subagent adapters: authorized image objects and one budgeted ModelCalls request. |
| [agent/src/omni/adapters/workers/__init__.py](../../../agent/src/omni/adapters/workers/__init__.py) | V2 | 1 | Owned capability workers; no application database or credentials access. |
| [agent/src/omni/adapters/workers/android.py](../../../agent/src/omni/adapters/workers/android.py) | V2 | 406 | Fixed Android observation/input worker. No database, arbitrary shell or automatic device setup. |
| [agent/src/omni/adapters/workers/android_adb.py](../../../agent/src/omni/adapters/workers/android_adb.py) | V2 | 127 | Bounded smart-socket ADB client. Never starts a server, pairs, installs, roots or accepts arbitrary commands. |
| [agent/src/omni/adapters/workers/android_unicode.py](../../../agent/src/omni/adapters/workers/android_unicode.py) | V2 | 307 | Fixed IME transaction and authenticated localabstract IPC; no database or arbitrary shell. |
| [agent/src/omni/adapters/workers/bilibili.py](../../../agent/src/omni/adapters/workers/bilibili.py) | V2 | 499 | One isolated Bilibili browser transaction; no database, arbitrary script, API writes or persisted cookies. |
| [agent/src/omni/adapters/workers/client.py](../../../agent/src/omni/adapters/workers/client.py) | V2 | 359 | Versioned, bounded JSON IPC to a supervised capability process. |
| [agent/src/omni/adapters/workers/credentials.py](../../../agent/src/omni/adapters/workers/credentials.py) | V2 | 76 | Fixed one-operation OS credential helper; no database, config lookup or plaintext fallback. |
| [agent/src/omni/adapters/workers/darwin_admin_probe.py](../../../agent/src/omni/adapters/workers/darwin_admin_probe.py) | V2 | 45 | Root bundle's fixed read-only system probe. Never imported from an owner environment. |
| [agent/src/omni/adapters/workers/desktop.py](../../../agent/src/omni/adapters/workers/desktop.py) | V2 | 133 | One bounded read-only desktop request per helper; stdout is IPC and never contains diagnostics. |
| [agent/src/omni/adapters/workers/desktop_control.py](../../../agent/src/omni/adapters/workers/desktop_control.py) | V2 | 222 | Owned independent Wayland control-consent helper; no input commands, EIS/clipboard descriptors, database or restored authority. |
| [agent/src/omni/adapters/workers/linux_action.py](../../../agent/src/omni/adapters/workers/linux_action.py) | V2 | 85 | One prepared Linux semantic action; the initial frame never permits a write, and no provider text is logged. |
| [agent/src/omni/adapters/workers/linux_desktop.py](../../../agent/src/omni/adapters/workers/linux_desktop.py) | V2 | 336 | One bounded Linux desktop request; addressed writes need a fresh parent permit, with no global input or database access. |
| [agent/src/omni/adapters/workers/linux_window_lease.py](../../../agent/src/omni/adapters/workers/linux_window_lease.py) | V2 | 204 | Bounded read-only X11 lease helper; one owned connection carries window lifetime notifications across requests. |
| [agent/src/omni/adapters/workers/local_vectors.py](../../../agent/src/omni/adapters/workers/local_vectors.py) | V2 | 204 | CPU ONNX/tokenizers subprocess. No database, credentials, model downloads or arbitrary Python imports. |
| [agent/src/omni/adapters/workers/macos/Core/Package.swift](../../../agent/src/omni/adapters/workers/macos/Core/Package.swift) | V2 | 12 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Core/Sources/ForegroundCore/Takeover.swift](../../../agent/src/omni/adapters/workers/macos/Core/Sources/ForegroundCore/Takeover.swift) | V2 | 107 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Core/Tests/ForegroundCoreTests/TakeoverTests.swift](../../../agent/src/omni/adapters/workers/macos/Core/Tests/ForegroundCoreTests/TakeoverTests.swift) | V2 | 126 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Package.swift](../../../agent/src/omni/adapters/workers/macos/Package.swift) | V2 | 17 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/identity.c](../../../agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/identity.c) | V2 | 129 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/include/NativeIdentity.h](../../../agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/include/NativeIdentity.h) | V2 | 11 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopFixture/Fixture.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopFixture/Fixture.swift) | V2 | 65 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Accessibility.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Accessibility.swift) | V2 | 202 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Actions.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Actions.swift) | V2 | 76 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Capture.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Capture.swift) | V2 | 52 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Connect.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Connect.swift) | V2 | 46 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/FocusWatch.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/FocusWatch.swift) | V2 | 75 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Foreground.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Foreground.swift) | V2 | 210 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/HelperMain.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/HelperMain.swift) | V2 | 73 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Identity.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Identity.swift) | V2 | 92 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/InputWatch.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/InputWatch.swift) | V2 | 99 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Protocol.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Protocol.swift) | V2 | 186 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/ServiceQuery.swift](../../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/ServiceQuery.swift) | V2 | 139 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Tests/NativeIdentityTests/RegistrationPaths.swift](../../../agent/src/omni/adapters/workers/macos/Tests/NativeIdentityTests/RegistrationPaths.swift) | V2 | 97 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/main.py](../../../agent/src/omni/adapters/workers/main.py) | V2 | 693 | Capability subprocess. Requests carry scope; no database or Runtime is imported. |
| [agent/src/omni/adapters/workers/media.py](../../../agent/src/omni/adapters/workers/media.py) | V2 | 651 | Bounded media worker: audio decode + local ASR and frame extraction in an owned subprocess. |
| [agent/src/omni/adapters/workers/portal.py](../../../agent/src/omni/adapters/workers/portal.py) | V2 | 256 | Owned Wayland window-share helper with explicit one-frame requests; no database, restore token or input capability. |
| [agent/src/omni/adapters/workers/protocol.py](../../../agent/src/omni/adapters/workers/protocol.py) | V2 | 201 | Strict, bounded IPC records shared by the host and capability worker; no dispatch or I/O. |
| [agent/src/omni/adapters/workers/service_admin_boot.py](../../../agent/src/omni/adapters/workers/service_admin_boot.py) | V2 | 211 | Standalone administrator entry. Invoke a reviewed root-owned bundle with Python -I -S. |
| [agent/src/omni/adapters/workers/terminal.py](../../../agent/src/omni/adapters/workers/terminal.py) | V2 | 720 | Fixed terminal host. Only this isolated, single-threaded startup may fork. |
| [agent/src/omni/adapters/workers/terminal_gate.py](../../../agent/src/omni/adapters/workers/terminal_gate.py) | V2 | 104 | Trusted ConPTY gate: no user startup code before the parent owns its Job. |
| [agent/src/omni/adapters/workers/windows_foreground.cs](../../../agent/src/omni/adapters/workers/windows_foreground.cs) | V2 | 250 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/windows_services.ps1](../../../agent/src/omni/adapters/workers/windows_services.ps1) | V2 | 209 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/windows_uia.cs](../../../agent/src/omni/adapters/workers/windows_uia.cs) | V2 | 356 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/windows_uia.ps1](../../../agent/src/omni/adapters/workers/windows_uia.ps1) | V2 | 102 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/V2_REFACTOR.md](../../../docs/v/reference/V2_REFACTOR.md)

测试：[agent/tests/v2/core/test_v2_kernel.py](../../../agent/tests/v2/core/test_v2_kernel.py)、[agent/tests/v2/core/test_v2_reliability.py](../../../agent/tests/v2/core/test_v2_reliability.py)、[agent/tests/v2/core/test_v2_diagnostics.py](../../../agent/tests/v2/core/test_v2_diagnostics.py)、[agent/tests/v2/core/test_v2_workers.py](../../../agent/tests/v2/core/test_v2_workers.py)

直接静态依赖：`v2-application`、`v2-domain`

## V2 实例组装 · v2-composition

分类：内核与模型。独立新目录、生命周期和开发入口，不读取旧库。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/__main__.py](../../../agent/src/omni/__main__.py) | V2 | 5 | Run the V2 CLI; explicit V1 commands retain the legacy runtime. |
| [agent/src/omni/composition/__init__.py](../../../agent/src/omni/composition/__init__.py) | V2 | 1 | V2 composition root. Constructors and lifecycle ownership are explicit here. |
| [agent/src/omni/composition/capabilities.py](../../../agent/src/omni/composition/capabilities.py) | V2 | 270 | Explicit builtin capability registration; capabilities never construct the kernel. |
| [agent/src/omni/composition/channel_providers.py](../../../agent/src/omni/composition/channel_providers.py) | V2 | 57 | Bind channel drivers and own each transport shared by their services. |
| [agent/src/omni/composition/cli.py](../../../agent/src/omni/composition/cli.py) | V2 | 192 | V2 command host and instance bootstrap. |
| [agent/src/omni/composition/commands/__init__.py](../../../agent/src/omni/composition/commands/__init__.py) | V2 | 27 | Typed registration for trusted V2 command-line capabilities. |
| [agent/src/omni/composition/commands/deployment.py](../../../agent/src/omni/composition/commands/deployment.py) | V2 | 326 | Reviewed service, release and exchange commands. |
| [agent/src/omni/composition/commands/installation.py](../../../agent/src/omni/composition/commands/installation.py) | V2 | 233 | Wheel lifecycle and read-only installation preflight. |
| [agent/src/omni/composition/commands/maintenance.py](../../../agent/src/omni/composition/commands/maintenance.py) | V2 | 146 | Offline backup, restore, object and index maintenance. |
| [agent/src/omni/composition/commands/operations.py](../../../agent/src/omni/composition/commands/operations.py) | V2 | 160 | Exclusive diagnostics and authenticated local diagnostic download. |
| [agent/src/omni/composition/commands/retention.py](../../../agent/src/omni/composition/commands/retention.py) | V2 | 281 | Read, plan, apply and cancel offline retention work. |
| [agent/src/omni/composition/features/__init__.py](../../../agent/src/omni/composition/features/__init__.py) | V2 | 152 | Install trusted features and expose their services to the tools and HTTP host. |
| [agent/src/omni/composition/features/action_reviews.py](../../../agent/src/omni/composition/features/action_reviews.py) | V2 | 15 | Bind review decisions to executor activity and the schedule notifier. |
| [agent/src/omni/composition/features/android.py](../../../agent/src/omni/composition/features/android.py) | V2 | 31 | Construct the Android feature and advertise its approved tool surface. |
| [agent/src/omni/composition/features/bilibili.py](../../../agent/src/omni/composition/features/bilibili.py) | V2 | 14 | Bind Bilibili reads and publication drafts to the shared channel provider. |
| [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py) | V2 | 156 | Declare bundled feature installers and their authenticated routers. |
| [agent/src/omni/composition/features/configurations.py](../../../agent/src/omni/composition/features/configurations.py) | V2 | 10 | Register model and routing configuration views over the instance database. |
| [agent/src/omni/composition/features/credentials.py](../../../agent/src/omni/composition/features/credentials.py) | V2 | 14 | Register owner credential changes with the delivery revocation callback. |
| [agent/src/omni/composition/features/deliveries.py](../../../agent/src/omni/composition/features/deliveries.py) | V2 | 13 | Bind durable delivery to registered transports and the instance lifecycle. |
| [agent/src/omni/composition/features/desktop.py](../../../agent/src/omni/composition/features/desktop.py) | V2 | 73 | Bind desktop views, consent and owner actions to the platform ports. |
| [agent/src/omni/composition/features/documents.py](../../../agent/src/omni/composition/features/documents.py) | V2 | 14 | Bind source documents to local indexes and persona invalidation. |
| [agent/src/omni/composition/features/extensions.py](../../../agent/src/omni/composition/features/extensions.py) | V2 | 32 | Bind owner-installed extensions to their package store and supervised hosts. |
| [agent/src/omni/composition/features/learning.py](../../../agent/src/omni/composition/features/learning.py) | V2 | 27 | Install post-turn learning with the captured read tools and the skills service. |
| [agent/src/omni/composition/features/maintenance.py](../../../agent/src/omni/composition/features/maintenance.py) | V2 | 30 | Install supervised maintenance after its document, memory and learning dependencies. |
| [agent/src/omni/composition/features/mcp.py](../../../agent/src/omni/composition/features/mcp.py) | V2 | 55 | Bind MCP authorization, transport recovery and the per-run capability provider. |
| [agent/src/omni/composition/features/media.py](../../../agent/src/omni/composition/features/media.py) | V2 | 55 | Mount media processing, including recovery of jobs from the previous process. |
| [agent/src/omni/composition/features/memory.py](../../../agent/src/omni/composition/features/memory.py) | V2 | 16 | Install memory indexing and organization with scoped instance dependencies. |
| [agent/src/omni/composition/features/omniweave.py](../../../agent/src/omni/composition/features/omniweave.py) | V2 | 18 | Install the shared owner Web and Router capability service. |
| [agent/src/omni/composition/features/operations.py](../../../agent/src/omni/composition/features/operations.py) | V2 | 90 | Bind diagnostics, debug maintenance and runtime health to an owned service. |
| [agent/src/omni/composition/features/people.py](../../../agent/src/omni/composition/features/people.py) | V2 | 15 | Bind person records and derived persona text to instance-owned storage. |
| [agent/src/omni/composition/features/qq.py](../../../agent/src/omni/composition/features/qq.py) | V2 | 13 | Bind the QQ listener to the run dispatcher and the shared OneBot provider. |
| [agent/src/omni/composition/features/schedules.py](../../../agent/src/omni/composition/features/schedules.py) | V2 | 28 | Bind scheduling to run notifications, cancellation and late startup. |
| [agent/src/omni/composition/features/skills.py](../../../agent/src/omni/composition/features/skills.py) | V2 | 20 | Bind the skills API, package storage and revision tools to one service. |
| [agent/src/omni/composition/features/terminals.py](../../../agent/src/omni/composition/features/terminals.py) | V2 | 21 | Bind terminal sessions and their review service to the same platform port. |
| [agent/src/omni/composition/features/vision.py](../../../agent/src/omni/composition/features/vision.py) | V2 | 28 | Install visual analysis over the registered model, media and image services. |
| [agent/src/omni/composition/http.py](../../../agent/src/omni/composition/http.py) | V2 | 228 | HTTP composition and owner bootstrap; tokens never appear in diagnostic output. |
| [agent/src/omni/composition/instance.py](../../../agent/src/omni/composition/instance.py) | V2 | 688 | Build one isolated instance; never imports or opens the V1 runtime. |
| [agent/src/omni/composition/lifecycle.py](../../../agent/src/omni/composition/lifecycle.py) | V2 | 164 | Own startup rollback and one shared shutdown budget; never dispatches business work. |
| [agent/src/omni/composition/model_providers.py](../../../agent/src/omni/composition/model_providers.py) | V2 | 37 | Bind model transport ports and their instance lifetime. |
| [agent/src/omni/composition/platform_providers.py](../../../agent/src/omni/composition/platform_providers.py) | V2 | 77 | Select the host platform's desktop and terminal bindings at startup. |
| [agent/src/omni/composition/releases.py](../../../agent/src/omni/composition/releases.py) | V2 | 124 | Assemble explicit offline release commands. No service install, stop, model or default-entry switch. |
| [agent/src/omni/composition/service_cleanup.py](../../../agent/src/omni/composition/service_cleanup.py) | V2 | 80 | Assemble quarantined registration cleanup, with an explicit root route for system definitions. |
| [agent/src/omni/composition/service_control.py](../../../agent/src/omni/composition/service_control.py) | V2 | 70 | Assemble reviewed lifecycle commands, with no operation during import or plan inspection. |
| [agent/src/omni/composition/service_exchange.py](../../../agent/src/omni/composition/service_exchange.py) | V2 | 187 | Assemble reviewed exchanges and their explicitly pinned root session; never elevates or switches default entrypoints. |
| [agent/src/omni/composition/service_operations.py](../../../agent/src/omni/composition/service_operations.py) | V2 | 77 | Assemble one explicit offline service-registration command; production entry remains unchanged. |
| [agent/src/omni/composition/service_runner.py](../../../agent/src/omni/composition/service_runner.py) | V2 | 68 | Start only a verified immutable deployment as its declared account; never registers a native service. |
| [agent/src/omni/composition/serving.py](../../../agent/src/omni/composition/serving.py) | V2 | 86 | Compose the single-worker HTTP process for manual and pinned service startup. |

文档：[docs/v/reference/V2_REFACTOR.md](../../../docs/v/reference/V2_REFACTOR.md)

测试：[agent/tests/v2/core/test_v2_kernel.py](../../../agent/tests/v2/core/test_v2_kernel.py)

直接静态依赖：`v2-adapters`、`v2-application`、`v2-domain`

## V2 开发工作台 · v2-workbench

分类：Web 接口。V2 工作台的功能页面、交互与样式。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [web/ui/v2/action-reviews.js](../../../web/ui/v2/action-reviews.js) | V2 | 424 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/android-unicode.js](../../../web/ui/v2/android-unicode.js) | V2 | 158 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/android.js](../../../web/ui/v2/android.js) | V2 | 403 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/bilibili.js](../../../web/ui/v2/bilibili.js) | V2 | 295 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/capabilities.js](../../../web/ui/v2/capabilities.js) | V2 | 57 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/chat-images.js](../../../web/ui/v2/chat-images.js) | V2 | 174 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/configuration.js](../../../web/ui/v2/configuration.js) | V2 | 566 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/credentials.js](../../../web/ui/v2/credentials.js) | V2 | 187 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/deliveries.js](../../../web/ui/v2/deliveries.js) | V2 | 451 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/desktop-actions.js](../../../web/ui/v2/desktop-actions.js) | V2 | 148 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/desktop.js](../../../web/ui/v2/desktop.js) | V2 | 661 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/documents.js](../../../web/ui/v2/documents.js) | V2 | 487 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/event-stream.js](../../../web/ui/v2/event-stream.js) | V2 | 168 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/extensions.js](../../../web/ui/v2/extensions.js) | V2 | 659 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/index.html](../../../web/ui/v2/index.html) | V2 | 2248 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/inspector.js](../../../web/ui/v2/inspector.js) | V2 | 266 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/main.js](../../../web/ui/v2/main.js) | V2 | 2539 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/maintenance.js](../../../web/ui/v2/maintenance.js) | V2 | 126 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/mcp.js](../../../web/ui/v2/mcp.js) | V2 | 378 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/media.js](../../../web/ui/v2/media.js) | V2 | 426 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/memory-organization.js](../../../web/ui/v2/memory-organization.js) | V2 | 465 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/memory-semantics.js](../../../web/ui/v2/memory-semantics.js) | V2 | 252 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/model-output.js](../../../web/ui/v2/model-output.js) | V2 | 26 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/models.js](../../../web/ui/v2/models.js) | V2 | 672 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/operations.js](../../../web/ui/v2/operations.js) | V2 | 352 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/people.js](../../../web/ui/v2/people.js) | V2 | 319 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/persona.js](../../../web/ui/v2/persona.js) | V2 | 87 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/qq.js](../../../web/ui/v2/qq.js) | V2 | 181 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/reflections.js](../../../web/ui/v2/reflections.js) | V2 | 232 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/routing.js](../../../web/ui/v2/routing.js) | V2 | 221 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/schedules.js](../../../web/ui/v2/schedules.js) | V2 | 523 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/settings-rail.js](../../../web/ui/v2/settings-rail.js) | V2 | 107 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/skills.js](../../../web/ui/v2/skills.js) | V2 | 458 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/terminal-reviews.js](../../../web/ui/v2/terminal-reviews.js) | V2 | 254 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/terminals.js](../../../web/ui/v2/terminals.js) | V2 | 564 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/tokens.js](../../../web/ui/v2/tokens.js) | V2 | 81 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/v1/components.css](../../../web/ui/v2/v1/components.css) | V2 | 655 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/v1/frame.css](../../../web/ui/v2/v1/frame.css) | V2 | 938 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/v1/workbench.css](../../../web/ui/v2/v1/workbench.css) | V2 | 4453 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/workbench.css](../../../web/ui/v2/workbench.css) | V2 | 4486 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/V2_REFACTOR.md](../../../docs/v/reference/V2_REFACTOR.md)

测试：[agent/tests/v2/http/test_v2_http.py](../../../agent/tests/v2/http/test_v2_http.py)

直接静态依赖：无内部模块依赖

## 版本与升级事务 · updates

分类：平台与数据。Gitee 固定提交检查、源码预检、安装级维护、独立升级执行与本机恢复快照。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/update_probe.py](../../../agent/src/omni/channels/update_probe.py) | V1 | 52 | 升级事务的数据副本迁移演练入口；不加载插件、不启动服务、不调用模型。 |
| [agent/src/omni/system/updates/__init__.py](../../../agent/src/omni/system/updates/__init__.py) | V1 | 1 | 安装级版本检查与升级事务；不构造 Agent，不提供模型自更新工具。 |
| [agent/src/omni/system/updates/archive.py](../../../agent/src/omni/system/updates/archive.py) | V1 | 99 | 源码包安全展开与发行清单；只处理受管源码，不读取用户配置或运行数据。 |
| [agent/src/omni/system/updates/common.py](../../../agent/src/omni/system/updates/common.py) | V1 | 203 | 可随恢复执行器冻结的标准库协议、原子状态和路径约束；不导入运行中应用。 |
| [agent/src/omni/system/updates/gate.py](../../../agent/src/omni/system/updates/gate.py) | V1 | 113 | 安装级维护闸门和运行入口租约；只管理协议文件，不构造或终止业务对象。 |
| [agent/src/omni/system/updates/gitee.py](../../../agent/src/omni/system/updates/gitee.py) | V1 | 196 | 固定公开 Gitee 源的有界 HTTP 读取；不接受任意仓库、令牌或执行下载内容。 |
| [agent/src/omni/system/updates/manager.py](../../../agent/src/omni/system/updates/manager.py) | V1 | 606 | 更新应用服务：缓存、预检和停写协调；实际替换交给独立冻结执行器。 |
| [agent/src/omni/system/updates/snapshot.py](../../../agent/src/omni/system/updates/snapshot.py) | V1 | 94 | 停写后的升级恢复快照；保留本机原值，清单验证先于任何原位恢复。 |
| [agent/src/omni/system/updates/version.py](../../../agent/src/omni/system/updates/version.py) | V1 | 111 | 源码身份与 SemVer 比较；不联网、不改变 Git 或安装环境。 |
| [agent/src/omni/system/updates/worker.py](../../../agent/src/omni/system/updates/worker.py) | V1 | 314 | 冻结后以基础 Python 独立运行的升级/恢复执行器；只依赖标准库和冻结的平台原语。 |

文档：[docs/v/reference/UPDATES.md](../../../docs/v/reference/UPDATES.md)

测试：[agent/tests/test_updates.py](../../../agent/tests/test_updates.py)

直接静态依赖：`agent-directories`、`assembly`、`config`、`platform`

## 关于与更新 API · http-updates

分类：Web 接口。关于页版本、更新通道、人工安装确认与持久进度；沿用统一配对身份。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py) | V1 | 55 | 版本与更新 HTTP 适配；只消费 Runtime，统一鉴权由工作台负责。 |
| [web/ui/app/hub-about.js](../../../web/ui/app/hub-about.js) | V1 | 187 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/UPDATES.md](../../../docs/v/reference/UPDATES.md)

测试：[agent/tests/test_updates_api.py](../../../agent/tests/test_updates_api.py)

直接静态依赖：`http-contracts`、`platform`、`updates`

## 项目存储与 Agent 目录 · agent-directories

分类：平台与数据。全局与独立空间目录解析、CAS 配置、运维路径解析与用途索引；不构造执行对象或搬移数据。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/system/agent_spaces/__init__.py](../../../agent/src/omni/system/agent_spaces/__init__.py) | V1 | 22 | Agent 目录注册与纯路径解析；执行对象仍由 bootstrap 构造。 |
| [agent/src/omni/system/agent_spaces/catalog.py](../../../agent/src/omni/system/agent_spaces/catalog.py) | V1 | 184 | 配置注册表：有界读取、CAS、跨进程互斥、原子写；不搬运用户数据。 |
| [agent/src/omni/system/agent_spaces/inspection.py](../../../agent/src/omni/system/agent_spaces/inspection.py) | V1 | 58 | 只读运维入口共用的路径选择，不组装服务、不建库、不创建目录。 |
| [agent/src/omni/system/agent_spaces/inventory.py](../../../agent/src/omni/system/agent_spaces/inventory.py) | V1 | 138 | 路径用途索引：纯声明，不扫描磁盘或读取记忆、凭据内容。 |
| [agent/src/omni/system/agent_spaces/layout.py](../../../agent/src/omni/system/agent_spaces/layout.py) | V1 | 366 | 本机 Agent 布局的声明与校验，不创建目录或打开数据。 |

文档：[docs/v/reference/STORAGE.md](../../../docs/v/reference/STORAGE.md)、[docs/v/reference/AGENT_STORAGE.md](../../../docs/v/reference/AGENT_STORAGE.md)

测试：[agent/tests/test_agent_storage.py](../../../agent/tests/test_agent_storage.py)、[agent/tests/test_storage_cli.py](../../../agent/tests/test_storage_cli.py)、[agent/tests/test_application_storage.py](../../../agent/tests/test_application_storage.py)

直接静态依赖：`config`、`platform`

## 项目目录与文件管理 · http-storage

分类：Web 接口。全局与 Agent 目录设置、运行与待重启路径、用途索引及当前空间公开文件区的有界浏览和人工上传。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py) | V1 | 250 | Agent 目录设置和当前公开文件区；只消费组装根注入的目录与路径策略。 |
| [web/storage/index.html](../../../web/storage/index.html) | V1 | 195 | 原生源码、平台脚本或界面资源 |
| [web/storage/storage.css](../../../web/storage/storage.css) | V1 | 490 | 原生源码、平台脚本或界面资源 |
| [web/storage/storage.js](../../../web/storage/storage.js) | V1 | 493 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/STORAGE.md](../../../docs/v/reference/STORAGE.md)、[docs/v/reference/AGENT_STORAGE.md](../../../docs/v/reference/AGENT_STORAGE.md)

测试：[agent/tests/test_storage_settings.py](../../../agent/tests/test_storage_settings.py)、[agent/tests/test_agent_storage_ui.py](../../../agent/tests/test_agent_storage_ui.py)

直接静态依赖：`agent-directories`、`assembly`、`files`、`platform`、`toolkit`

## 上下文设置与统计 · http-context

分类：Web 接口。设置中心的只读窗口统计、供应商缓存样本和带版本检查的配置保存；不触发模型。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/context.py](../../../agent/src/omni/channels/web_api/context.py) | V1 | 106 | 上下文设置与只读统计；不调用模型、不构造服务、不修改运行中的配置。 |
| [web/ui/app/hub-context.js](../../../web/ui/app/hub-context.js) | V1 | 267 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/CONTEXT_SETTINGS.md](../../../docs/v/reference/CONTEXT_SETTINGS.md)、[docs/v/reference/PROMPT_CONTEXT.md](../../../docs/v/reference/PROMPT_CONTEXT.md)

测试：[agent/tests/test_context_settings.py](../../../agent/tests/test_context_settings.py)

直接静态依赖：`assembly`、`config`、`platform`、`storage`

## 运行时组装 · assembly

分类：内核与模型。唯一组装根，构造依赖并暴露 Runtime 门面。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/bootstrap.py](../../../agent/src/omni/bootstrap.py) | V1 | 958 | 组装根（Composition Root）：全项目唯一负责装配的地方。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)、[docs/v/reference/REPOSITORY_STRUCTURE.md](../../../docs/v/reference/REPOSITORY_STRUCTURE.md)

测试：[agent/tests/test_paths.py](../../../agent/tests/test_paths.py)、[agent/tests/test_repository_layout.py](../../../agent/tests/test_repository_layout.py)

直接静态依赖：`agent-directories`、`android`、`bilibili`、`browser`、`command-jobs`、`community`、`config`、`delegation`、`desktop`、`engine`、`files`、`mcp`、`models`、`personal`、`platform`、`plugins`、`qq-tools`、`safety`、`scheduling`、`shell`、`skills`、`storage`、`storyboard`、`system-tools`、`toolkit`、`updates`、`web-fetch`

## 配置与路径契约 · config

分类：平台与数据。配置模型、加载与 RuntimePaths；不包含业务编排。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/config.py](../../../agent/src/omni/config.py) | 共享 | 887 | 配置加载：.env 密钥 + config.yaml 主配置 + 运行期路径表。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_config.py](../../../agent/tests/test_config.py)

直接静态依赖：无内部模块依赖

## 提示词与上下文 · prompt-context

分类：内核与模型。稳定系统前缀、回合上下文快照渲染与保留当前请求的摘要边界。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/core/compress.py](../../../agent/src/omni/core/compress.py) | V1 | 328 | 上下文压缩 + 消息不变量（Hermes compression_facade 的最小版，NOTES §1.2）。 |
| [agent/src/omni/core/context.py](../../../agent/src/omni/core/context.py) | V1 | 195 | 缓存友好的提示词布局：固定规则在前，每轮快照附于该轮用户正文末尾。 |

文档：[docs/v/reference/PROMPT_CONTEXT.md](../../../docs/v/reference/PROMPT_CONTEXT.md)、[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_prompt_context.py](../../../agent/tests/test_prompt_context.py)、[agent/tests/test_context.py](../../../agent/tests/test_context.py)、[agent/tests/test_compress.py](../../../agent/tests/test_compress.py)

直接静态依赖：`models`

## Agent 内核 · engine

分类：内核与模型。回合、上下文、工具循环、取消、委派约束、review 与调度。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/core/__init__.py](../../../agent/src/omni/core/__init__.py) | V1 | 1 | Agent 核心：编排循环。工具注册表在 tools 包，审批在 safety 包。 |
| [agent/src/omni/core/agent.py](../../../agent/src/omni/core/agent.py) | V1 | 1380 | Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。 |
| [agent/src/omni/core/budget_alert.py](../../../agent/src/omni/core/budget_alert.py) | V1 | 102 | 费用额度的**阶梯告警**（P5-c，抄 surfcore `token-budget.js` 的判定逻辑）。 |
| [agent/src/omni/core/cron.py](../../../agent/src/omni/core/cron.py) | V1 | 471 | 心跳调度器：tick 循环 + 至多一次派发 + 任务执行/投递（Hermes cron 最小版）。 |
| [agent/src/omni/core/evaluation.py](../../../agent/src/omni/core/evaluation.py) | V1 | 36 | 固定样本检索评测；不把 mock 模型的排序表现当成真实语义收益。 |
| [agent/src/omni/core/journal.py](../../../agent/src/omni/core/journal.py) | V1 | 424 | 记忆系统整理编排：日/周/月笔记的模型整理 worker（与 TurnEndReviewer 同构）。 |
| [agent/src/omni/core/modes.py](../../../agent/src/omni/core/modes.py) | V1 | 206 | 按回合能力模式（chat / retrieval / agent）的声明与纯函数。 |
| [agent/src/omni/core/qq_policy.py](../../../agent/src/omni/core/qq_policy.py) | V1 | 101 | 主动提醒策略：显式所有者、安静时段、独立每日额度，以及通道层冷却位；不做网络发送。 |
| [agent/src/omni/core/retrieval.py](../../../agent/src/omni/core/retrieval.py) | V1 | 103 | FTS + 向量 RRF 融合；后台有界增量索引，网络请求不占数据库事务。 |
| [agent/src/omni/core/review.py](../../../agent/src/omni/core/review.py) | V1 | 252 | 回合后异步 review：自进化最小闭环（Hermes background_review 的 P1 简化版， |
| [agent/src/omni/core/router.py](../../../agent/src/omni/core/router.py) | V1 | 166 | 回合能力模式路由：先规则、后模型、最后回退。 |
| [agent/src/omni/core/run.py](../../../agent/src/omni/core/run.py) | V1 | 285 | 运行（run）的身份、取消信号与上下文（D09）。 |
| [agent/src/omni/core/session.py](../../../agent/src/omni/core/session.py) | V1 | 142 | 会话作用域：同一会话串行、不同会话并行、排队有界、跨进程有租约。 |
| [agent/src/omni/core/vision.py](../../../agent/src/omni/core/vision.py) | V1 | 202 | 工具产物 → 模型图像块（D15/A13）。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_agent.py](../../../agent/tests/test_agent.py)、[agent/tests/test_runs.py](../../../agent/tests/test_runs.py)、[agent/tests/test_parallel_tools.py](../../../agent/tests/test_parallel_tools.py)

直接静态依赖：`config`、`models`、`prompt-context`、`qq-tools`、`safety`、`storage`、`toolkit`

## 模型网关与 embedding · models

分类：内核与模型。模型路由、能力、重试、流式、预算与 embedding 传输。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/llm/__init__.py](../../../agent/src/omni/llm/__init__.py) | V1 | 1 | LLM 层：LiteLLM 统一网关。 |
| [agent/src/omni/llm/budget.py](../../../agent/src/omni/llm/budget.py) | V1 | 216 | 费用预留与结算的契约（D10）。 |
| [agent/src/omni/llm/embedding.py](../../../agent/src/omni/llm/embedding.py) | V1 | 135 | 可切换 embedding 出口：本地 Ollama / LiteLLM API；费用账本从 bootstrap 注入。 |
| [agent/src/omni/llm/errors.py](../../../agent/src/omni/llm/errors.py) | V1 | 205 | 模型调用的错误分类与脱敏（D10）。 |
| [agent/src/omni/llm/gateway.py](../../../agent/src/omni/llm/gateway.py) | V1 | 806 | LiteLLM 统一网关：一套 OpenAI 格式接所有供应商（海外/国产/本地 Ollama）。 |
| [agent/src/omni/llm/tiers.py](../../../agent/src/omni/llm/tiers.py) | V1 | 179 | 费用额度的**口径化判定层**（P5-c，抄 surfcore `token-budget.js` 的判定逻辑）。 |
| [agent/src/omni/llm/usage.py](../../../agent/src/omni/llm/usage.py) | V1 | 199 | 用量（tokens）与价格：把"这次调用大概/实际花了多少"变成可记账的数字。（D10） |

文档：[docs/v/reference/RETRIEVAL.md](../../../docs/v/reference/RETRIEVAL.md)

测试：[agent/tests/test_gateway.py](../../../agent/tests/test_gateway.py)、[agent/tests/test_usage.py](../../../agent/tests/test_usage.py)

直接静态依赖：`config`

## 审批与权限 · safety

分类：内核与模型。风险分级、人工审批与无人值守拒绝。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/safety/__init__.py](../../../agent/src/omni/safety/__init__.py) | V1 | 1 | 安全层：审批闸门（工具风险分级 + 按上下文选择审批策略）。 |
| [agent/src/omni/safety/activity.py](../../../agent/src/omni/safety/activity.py) | V1 | 212 | 活跃时段统计与「建议安静时段」推导（抄 surfcore 的作息窗口**形态**，不是它的学习能力）。 |
| [agent/src/omni/safety/broker.py](../../../agent/src/omni/safety/broker.py) | V1 | 117 | 审批代理：跨线程确认流（Web 工作台用）。 |
| [agent/src/omni/safety/cooldown.py](../../../agent/src/omni/safety/cooldown.py) | V1 | 98 | 通道层冷却位：刚聊过就潜水，别急着主动搭话。 |
| [agent/src/omni/safety/gates.py](../../../agent/src/omni/safety/gates.py) | V1 | 147 | 审批闸门：工具风险分级 + 按上下文选择审批策略（Strategy 模式）。 |
| [agent/src/omni/safety/proactive.py](../../../agent/src/omni/safety/proactive.py) | V1 | 72 | 主动发言闸门：白名单 + 仅所有者 + 安静时段 + 每日额度上限。 |
| [agent/src/omni/safety/smart.py](../../../agent/src/omni/safety/smart.py) | V1 | 78 | smart 审批档（Hermes approval_smart 同款）：辅助 LLM 先判风险，拿不准再问人。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_safety.py](../../../agent/tests/test_safety.py)

直接静态依赖：无内部模块依赖

## 记忆与运行账本 · storage

分类：平台与数据。会话/事实/技能/产物及 state.db 事务、迁移、额度、投递。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/memory/__init__.py](../../../agent/src/omni/memory/__init__.py) | V1 | 1 | 记忆层：SQLite 持久化（P0 版；P1 升级向量检索与三层记忆）。 |
| [agent/src/omni/memory/anchor.py](../../../agent/src/omni/memory/anchor.py) | V1 | 163 | 沉淀锚定（P5-b）：后台写入的记忆必须能指回它依据的原文。 |
| [agent/src/omni/memory/artifacts.py](../../../agent/src/omni/memory/artifacts.py) | V1 | 338 | 产物存储（D15 / A13）：截图、文件、报告统一按 artifact_id 管理。 |
| [agent/src/omni/memory/capacity.py](../../../agent/src/omni/memory/capacity.py) | V1 | 184 | 记忆容量的**口径化档位判定**（抄 surfcore `src/memory-card.js` 的判定逻辑）。 |
| [agent/src/omni/memory/clarity.py](../../../agent/src/omni/memory/clarity.py) | V1 | 268 | 事实记忆的清晰度阶梯（纯判定层，抄 surfcore `memory-card.js` 的思路）。 |
| [agent/src/omni/memory/command_jobs.py](../../../agent/src/omni/memory/command_jobs.py) | V1 | 83 | 命令任务元数据仓储；不执行或终止操作系统进程。 |
| [agent/src/omni/memory/cron.py](../../../agent/src/omni/memory/cron.py) | V1 | 62 | 定时任务模型与存储的公开入口（保留旧导入路径）。 |
| [agent/src/omni/memory/cron_store.py](../../../agent/src/omni/memory/cron_store.py) | V1 | 105 | 定时任务域的两个门面（`JobStore` / `ExecutionLog`）。 |
| [agent/src/omni/memory/dialogue.py](../../../agent/src/omni/memory/dialogue.py) | V1 | 399 | 对话全量记忆（M1）：完整对话文本一字不差的追加型存档。 |
| [agent/src/omni/memory/guard.py](../../../agent/src/omni/memory/guard.py) | V1 | 136 | 记忆写入的确定性闸门（D12 / A15）。 |
| [agent/src/omni/memory/jobs.py](../../../agent/src/omni/memory/jobs.py) | V1 | 139 | 定时任务模型与调度时间（纯数据 + 纯函数，不碰存储）。 |
| [agent/src/omni/memory/journal.py](../../../agent/src/omni/memory/journal.py) | V1 | 416 | 笔记记忆（M3/M4/M5）：每日/每周/每月笔记的存储与到期判定。 |
| [agent/src/omni/memory/mcp_schemas.py](../../../agent/src/omni/memory/mcp_schemas.py) | V1 | 81 | MCP 工具目录指纹仓储（D19，`state.db`）。 |
| [agent/src/omni/memory/outbox.py](../../../agent/src/omni/memory/outbox.py) | V1 | 267 | QQ 投递 outbox 仓储（`state.db`）。 |
| [agent/src/omni/memory/owner.py](../../../agent/src/omni/memory/owner.py) | V1 | 322 | 所有者记忆门面：档案行与常驻笔记统一进人物系统后的注入与维护面。 |
| [agent/src/omni/memory/owner_migrate.py](../../../agent/src/omni/memory/owner_migrate.py) | V1 | 150 | 所有者记忆一次性迁移：profile 表与 MEMORY.md → owner 人物条目。 |
| [agent/src/omni/memory/people.py](../../../agent/src/omni/memory/people.py) | V1 | 657 | 核心人物记忆（M6）：重要人物的账号、常驻档案、常驻记忆与临时事件。 |
| [agent/src/omni/memory/privacy.py](../../../agent/src/omni/memory/privacy.py) | V1 | 79 | 记忆写入的隐私闸门（判定委托给共享原语 `system/text_guard.py`）。 |
| [agent/src/omni/memory/publications.py](../../../agent/src/omni/memory/publications.py) | V1 | 134 | 人工发布草稿与发送身份账本；进程中断/结果未知不自动二次发布。 |
| [agent/src/omni/memory/qq.py](../../../agent/src/omni/memory/qq.py) | V1 | 113 | QQ 入站去重、撤回墓碑与回复身份；只保存 ID/状态，不保存原文。 |
| [agent/src/omni/memory/resident.py](../../../agent/src/omni/memory/resident.py) | V1 | 360 | 常驻小记忆（Hermes MEMORY.md 式，NOTES §1.3a）：跨会话冻结快照注入系统提示词。 |
| [agent/src/omni/memory/revisions.py](../../../agent/src/omni/memory/revisions.py) | V1 | 235 | 内容修订历史（D08）：技能与常驻记忆的"可回滚快照"。 |
| [agent/src/omni/memory/runs.py](../../../agent/src/omni/memory/runs.py) | V1 | 407 | 运行记录（run）与事件流（`state.db`）：一次"问—想—做—答"的可查账本。 |
| [agent/src/omni/memory/session_log.py](../../../agent/src/omni/memory/session_log.py) | V1 | 418 | 会话全量记忆（M2）：对话 + 工具过程的完整流水（比 M1 多工具与系统边界事件）。 |
| [agent/src/omni/memory/skill_acks.py](../../../agent/src/omni/memory/skill_acks.py) | V1 | 54 | 技能变更「已读」标记仓储（D18，`state.db`）。 |
| [agent/src/omni/memory/skill_inbox.py](../../../agent/src/omni/memory/skill_inbox.py) | V1 | 531 | 外来技能包隔离安装（D18 后置项）：inbox 收包 → 隔离区校验 → 用户激活。 |
| [agent/src/omni/memory/skills.py](../../../agent/src/omni/memory/skills.py) | V1 | 1040 | 技能存储（程序性记忆）：skills/<name>/SKILL.md，agentskills.io 兼容子集。 |
| [agent/src/omni/memory/state.py](../../../agent/src/omni/memory/state.py) | V1 | 480 | 共享运行状态（`data/state.db`）：定时任务、执行账本、额度与迁移记录。 |
| [agent/src/omni/memory/state_errors.py](../../../agent/src/omni/memory/state_errors.py) | V1 | 44 | state.db 的异常语义与错误翻译（唯一来源）。 |
| [agent/src/omni/memory/state_mixin_jobs.py](../../../agent/src/omni/memory/state_mixin_jobs.py) | V1 | 259 | 定时任务域的 `state.db` 访问 mixin（任务定义 / revision / 原子领取）。 |
| [agent/src/omni/memory/state_mixin_leases.py](../../../agent/src/omni/memory/state_mixin_leases.py) | V1 | 113 | 资源租约域的 `state.db` 访问 mixin（会话/浏览器 profile 等互斥资源）。 |
| [agent/src/omni/memory/state_mixin_ledger.py](../../../agent/src/omni/memory/state_mixin_ledger.py) | V1 | 76 | 执行账本与写额度域的 `state.db` 访问 mixin。 |
| [agent/src/omni/memory/state_records.py](../../../agent/src/omni/memory/state_records.py) | V1 | 56 | state.db 各域仓储返回值的数据形状（唯一来源）。 |
| [agent/src/omni/memory/store.py](../../../agent/src/omni/memory/store.py) | V1 | 1310 | SQLite 记忆存储：会话 / 消息 / 事实 / 用户档案 / 全文检索 / review 日志。 |
| [agent/src/omni/memory/store_schema.py](../../../agent/src/omni/memory/store_schema.py) | V1 | 165 | memory.db 的表结构、FTS 索引声明与额度常量（唯一来源）。 |
| [agent/src/omni/memory/system.py](../../../agent/src/omni/memory/system.py) | V1 | 62 | 记忆系统门面：六个记忆模块对外的单点聚合（组合根纪律，无业务逻辑）。 |
| [agent/src/omni/memory/usage.py](../../../agent/src/omni/memory/usage.py) | V1 | 363 | 模型调用的用量与费用账本（`state.db`，D10）。 |
| [agent/src/omni/memory/vectors.py](../../../agent/src/omni/memory/vectors.py) | V1 | 111 | sqlite-vec 派生索引。与事实同库；遗忘/纠错触发器只用普通 SQL，无扩展依赖。 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)、[docs/v/reference/RETRIEVAL.md](../../../docs/v/reference/RETRIEVAL.md)

测试：[agent/tests/test_state.py](../../../agent/tests/test_state.py)、[agent/tests/test_memory.py](../../../agent/tests/test_memory.py)

直接静态依赖：`command-contracts`、`config`、`platform`

## 内置终端 · terminal

分类：交互与终端。ConPTY 后端、会话、会话池、WebSocket 四层；与 agent shell 工具分工。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/terminal/__init__.py](../../../agent/src/omni/channels/terminal/__init__.py) | V1 | 7 | 工作台终端公共入口；平台适配、会话、会话池和 WS 路由分别维护。 |
| [agent/src/omni/channels/terminal/backend.py](../../../agent/src/omni/channels/terminal/backend.py) | V1 | 40 | ConPTY 平台适配：检测可用性与创建进程；不管理会话池或 HTTP。 |
| [agent/src/omni/channels/terminal/managed.py](../../../agent/src/omni/channels/terminal/managed.py) | V1 | 83 | 可恢复的人类终端：持续排空、有限输出环与单写者；不向模型暴露。 |
| [agent/src/omni/channels/terminal/pool.py](../../../agent/src/omni/channels/terminal/pool.py) | V1 | 146 | 终端会话池：并发上限、关闭预算和残留报告。 |
| [agent/src/omni/channels/terminal/routes.py](../../../agent/src/omni/channels/terminal/routes.py) | V1 | 79 | 内置终端 WebSocket 适配；复用工作台身份及注入的会话池。 |
| [agent/src/omni/channels/terminal/session.py](../../../agent/src/omni/channels/terminal/session.py) | V1 | 55 | 单终端会话的读、写、窗口尺寸和退出确认。 |
| [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py) | V1 | 160 | 人工终端 v2：身份归属、结构化消息及只回放输出。旧 WS 独立兼容。 |
| [web/terminal/index.html](../../../web/terminal/index.html) | V1 | 87 | 原生源码、平台脚本或界面资源 |
| [web/terminal/terminal.css](../../../web/terminal/terminal.css) | V1 | 477 | 原生源码、平台脚本或界面资源 |
| [web/terminal/terminal.js](../../../web/terminal/terminal.js) | V1 | 346 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)、[docs/v/reference/TERMINAL.md](../../../docs/v/reference/TERMINAL.md)

测试：[agent/tests/test_terminal.py](../../../agent/tests/test_terminal.py)、[agent/tests/test_terminal_managed.py](../../../agent/tests/test_terminal_managed.py)、[agent/tests/test_doctor_lifecycle.py](../../../agent/tests/test_doctor_lifecycle.py)

直接静态依赖：`config`、`web-ui`

## 项目与能力工作台 · workbench

分类：交互与终端。项目索引、API、已注册工具和扩展状态的只读 HTTP 适配。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/workbench/__init__.py](../../../agent/src/omni/channels/workbench/__init__.py) | V1 | 5 | 专题工作台路由；只消费注入的 Runtime，不在通道构造核心对象。 |
| [agent/src/omni/channels/workbench/routes.py](../../../agent/src/omni/channels/workbench/routes.py) | V1 | 113 | 只读项目目录、接口与实时能力；由 web.py 的统一鉴权中间件保护。 |
| [web/project/index.html](../../../web/project/index.html) | V1 | 26 | 原生源码、平台脚本或界面资源 |
| [web/project/panel.html](../../../web/project/panel.html) | V1 | 49 | 原生源码、平台脚本或界面资源 |
| [web/project/project.css](../../../web/project/project.css) | V1 | 486 | 原生源码、平台脚本或界面资源 |
| [web/project/project.js](../../../web/project/project.js) | V1 | 450 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_project_catalog.py](../../../agent/tests/test_project_catalog.py)

直接静态依赖：`assembly`、`catalog`、`config`

## 界面公共交互 · ui-shared

分类：交互与终端。统一导航、移动区域切换、有界请求、反馈和确认框；不执行工具或自动启动依赖。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [web/ui/app/api.js](../../../web/ui/app/api.js) | V1 | 14 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/approvals.js](../../../web/ui/app/approvals.js) | V1 | 65 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/artifacts.js](../../../web/ui/app/artifacts.js) | V1 | 58 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/chat.js](../../../web/ui/app/chat.js) | V1 | 198 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/desktop.js](../../../web/ui/app/desktop.js) | V1 | 41 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/dom.js](../../../web/ui/app/dom.js) | V1 | 45 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/header.js](../../../web/ui/app/header.js) | V1 | 181 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-capabilities.js](../../../web/ui/app/hub-capabilities.js) | V1 | 10 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-cron.js](../../../web/ui/app/hub-cron.js) | V1 | 244 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-embodiment.js](../../../web/ui/app/hub-embodiment.js) | V1 | 102 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-jobs.js](../../../web/ui/app/hub-jobs.js) | V1 | 9 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-market.js](../../../web/ui/app/hub-market.js) | V1 | 361 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-mcp-services.js](../../../web/ui/app/hub-mcp-services.js) | V1 | 10 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-mcp.js](../../../web/ui/app/hub-mcp.js) | V1 | 3 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-memory.js](../../../web/ui/app/hub-memory.js) | V1 | 5 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-models.js](../../../web/ui/app/hub-models.js) | V1 | 675 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-persona.js](../../../web/ui/app/hub-persona.js) | V1 | 268 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-project.js](../../../web/ui/app/hub-project.js) | V1 | 9 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-services.js](../../../web/ui/app/hub-services.js) | V1 | 196 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-skills.js](../../../web/ui/app/hub-skills.js) | V1 | 656 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-usage.js](../../../web/ui/app/hub-usage.js) | V1 | 63 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub.js](../../../web/ui/app/hub.js) | V1 | 624 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/main.js](../../../web/ui/app/main.js) | V1 | 136 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/memory.js](../../../web/ui/app/memory.js) | V1 | 54 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/modals.js](../../../web/ui/app/modals.js) | V1 | 30 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/publications.js](../../../web/ui/app/publications.js) | V1 | 87 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/rail.js](../../../web/ui/app/rail.js) | V1 | 39 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/runs.js](../../../web/ui/app/runs.js) | V1 | 288 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/services.js](../../../web/ui/app/services.js) | V1 | 133 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/sessions.js](../../../web/ui/app/sessions.js) | V1 | 74 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/skills.js](../../../web/ui/app/skills.js) | V1 | 250 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/state.js](../../../web/ui/app/state.js) | V1 | 15 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/tabs.js](../../../web/ui/app/tabs.js) | V1 | 58 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/tips.js](../../../web/ui/app/tips.js) | V1 | 26 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/tools.js](../../../web/ui/app/tools.js) | V1 | 34 | 原生源码、平台脚本或界面资源 |
| [web/ui/base.css](../../../web/ui/base.css) | 共享 | 373 | 原生源码、平台脚本或界面资源 |
| [web/ui/components.css](../../../web/ui/components.css) | V1 | 654 | 原生源码、平台脚本或界面资源 |
| [web/ui/dialog.js](../../../web/ui/dialog.js) | 共享 | 65 | 原生源码、平台脚本或界面资源 |
| [web/ui/embed-height.js](../../../web/ui/embed-height.js) | V1 | 27 | 原生源码、平台脚本或界面资源 |
| [web/ui/frame.css](../../../web/ui/frame.css) | V1 | 956 | 原生源码、平台脚本或界面资源 |
| [web/ui/icons.css](../../../web/ui/icons.css) | 共享 | 33 | 原生源码、平台脚本或界面资源 |
| [web/ui/markdown.js](../../../web/ui/markdown.js) | 共享 | 152 | 原生源码、平台脚本或界面资源 |
| [web/ui/sections.js](../../../web/ui/sections.js) | V1 | 144 | 原生源码、平台脚本或界面资源 |
| [web/ui/shared.js](../../../web/ui/shared.js) | V1 | 341 | 原生源码、平台脚本或界面资源 |
| [web/ui/terminal-theme.js](../../../web/ui/terminal-theme.js) | V1 | 68 | 原生源码、平台脚本或界面资源 |
| [web/ui/theme.js](../../../web/ui/theme.js) | 共享 | 96 | 原生源码、平台脚本或界面资源 |
| [web/ui/tokens.css](../../../web/ui/tokens.css) | 共享 | 259 | 原生源码、平台脚本或界面资源 |
| [web/ui/workbench.css](../../../web/ui/workbench.css) | V1 | 4443 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/UI_QA.md](../../../docs/v/reference/UI_QA.md)

测试：[agent/tests/test_ui_flows.py](../../../agent/tests/test_ui_flows.py)

直接静态依赖：无内部模块依赖

## Web 界面与身份 · web-ui

分类：交互与终端。工作台业务路由、统一鉴权与静态界面；新专题使用独立 APIRouter。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web.py](../../../agent/src/omni/channels/web.py) | V1 | 255 | Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。 |
| [agent/src/omni/channels/web_auth.py](../../../agent/src/omni/channels/web_auth.py) | V1 | 340 | 工作台本地身份：配对码 → HttpOnly 会话 cookie，外加 Host/Origin/CSRF 校验。 |
| [web/index.html](../../../web/index.html) | V1 | 1280 | 原生源码、平台脚本或界面资源 |
| [web/memory/index.html](../../../web/memory/index.html) | V1 | 27 | 原生源码、平台脚本或界面资源 |
| [web/memory/memory.css](../../../web/memory/memory.css) | V1 | 489 | 原生源码、平台脚本或界面资源 |
| [web/memory/memory.js](../../../web/memory/memory.js) | V1 | 1060 | 原生源码、平台脚本或界面资源 |
| [web/memory/page.js](../../../web/memory/page.js) | V1 | 2 | 原生源码、平台脚本或界面资源 |
| [web/memory/panel.html](../../../web/memory/panel.html) | V1 | 330 | 原生源码、平台脚本或界面资源 |
| [web/vendor/xterm-addon-fit.js](../../../web/vendor/xterm-addon-fit.js) | 共享 | 2 | 原生源码、平台脚本或界面资源 |
| [web/vendor/xterm.js](../../../web/vendor/xterm.js) | 共享 | 6 | 原生源码、平台脚本或界面资源 |
| [web/vendor/xterm.min.css](../../../web/vendor/xterm.min.css) | 共享 | 8 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)、[docs/v/reference/UI_QA.md](../../../docs/v/reference/UI_QA.md)

测试：[agent/tests/test_web.py](../../../agent/tests/test_web.py)、[agent/tests/test_web_auth.py](../../../agent/tests/test_web_auth.py)、[agent/tests/test_ui_flows.py](../../../agent/tests/test_ui_flows.py)

直接静态依赖：`assembly`、`config`、`http-capabilities`、`http-contracts`、`http-routing`、`platform`、`safety`、`terminal`、`updates`、`workbench`

## 会话与待办 API · http-sessions

分类：Web 接口。会话选择、历史、状态和待办；消费注入 Runtime。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py) | V1 | 99 | 会话与待办 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../../agent/tests/test_web_api.py)、[agent/tests/test_sessions.py](../../../agent/tests/test_sessions.py)

直接静态依赖：`assembly`、`http-contracts`、`storage`

## 对话与流式 API · http-conversation

分类：Web 接口。普通/流式对话、显式运行入口；共用会话闸门、租约和取消。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/conversation.py](../../../agent/src/omni/channels/web_api/conversation.py) | V1 | 194 | 对话与流式 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../../agent/tests/test_web_api.py)、[agent/tests/test_web.py](../../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`engine`、`http-contracts`、`models`

## 状态库与命令账本声明 · command-contracts

分类：平台与数据。state.db 全部表结构与迁移脚本的唯一声明源；command_schema 仅保留旧 import 兼容。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/memory/command_schema.py](../../../agent/src/omni/memory/command_schema.py) | V1 | 7 | 命令账本声明的旧 import 兼容层；真实声明见 `memory/state_schema.py`。 |
| [agent/src/omni/memory/state_schema.py](../../../agent/src/omni/memory/state_schema.py) | V1 | 337 | `state.db` 的表结构与迁移声明唯一来源。 |

文档：[docs/v/reference/TERMINAL.md](../../../docs/v/reference/TERMINAL.md)

测试：[agent/tests/test_state.py](../../../agent/tests/test_state.py)、[agent/tests/test_command_jobs.py](../../../agent/tests/test_command_jobs.py)

直接静态依赖：`config`

## 后台命令任务 · command-jobs

分类：工具能力。审批后的独立命令任务、状态仓储、有限日志、会话隔离与取消；不复用人工 PTY。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/command_jobs.py](../../../agent/src/omni/channels/web_api/command_jobs.py) | V1 | 31 | 后台命令管理适配；不提供绕过 Registry 审批的启动接口。 |
| [agent/src/omni/tools/shell/job_logs.py](../../../agent/src/omni/tools/shell/job_logs.py) | V1 | 59 | 命令输出的有界 JSONL 环：序号续读、跨流串行、最近日志与缺口标记。 |
| [agent/src/omni/tools/shell/job_tools.py](../../../agent/src/omni/tools/shell/job_tools.py) | V1 | 116 | 后台任务的 Registry 适配；不复用人工 PTY，不接受外部会话身份。 |
| [agent/src/omni/tools/shell/jobs.py](../../../agent/src/omni/tools/shell/jobs.py) | V1 | 373 | 独立后台命令执行器：审批后入队、有界进程/日志、会话归属与诚实回收。 |
| [web/jobs/index.html](../../../web/jobs/index.html) | V1 | 28 | 原生源码、平台脚本或界面资源 |
| [web/jobs/jobs.css](../../../web/jobs/jobs.css) | V1 | 225 | 原生源码、平台脚本或界面资源 |
| [web/jobs/jobs.js](../../../web/jobs/jobs.js) | V1 | 254 | 原生源码、平台脚本或界面资源 |
| [web/jobs/panel.html](../../../web/jobs/panel.html) | V1 | 50 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/TERMINAL.md](../../../docs/v/reference/TERMINAL.md)

测试：[agent/tests/test_command_jobs.py](../../../agent/tests/test_command_jobs.py)

直接静态依赖：`config`、`platform`、`shell`、`storage`、`toolkit`

## 运行与用量 API · http-runs

分类：Web 接口。运行查询、事件续读、协作取消和费用视图。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/runs.py](../../../agent/src/omni/channels/web_api/runs.py) | V1 | 96 | 运行与用量 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_web.py](../../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`models`、`storage`

## 记忆与档案 API · http-memory

分类：Web 接口。事实、档案、常驻、review 和检索状态。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py) | V1 | 173 | 记忆与档案 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_web.py](../../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`platform`、`storage`

## 技能与审阅 API · http-skills

分类：Web 接口。技能编辑/修订/差异/回滚和待审阅状态，保留静态路由优先级。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py) | V1 | 484 | 技能 HTTP 适配：编辑/审阅/统计/归档/外来包，全部消费注入 Runtime。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../../agent/tests/test_web_api.py)、[agent/tests/test_skills_history.py](../../../agent/tests/test_skills_history.py)

直接静态依赖：`assembly`、`config`、`http-contracts`、`platform`、`storage`

## 产物 API · http-artifacts

分类：Web 接口。产物列表、指纹验证读取和清理，统一领域错误映射。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/artifacts.py](../../../agent/src/omni/channels/web_api/artifacts.py) | V1 | 83 | 产物 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_artifacts.py](../../../agent/tests/test_artifacts.py)

直接静态依赖：`assembly`、`storage`

## 审批 API · http-approvals

分类：Web 接口。待批准事项和人工决定；不构造或绕过审批器。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/approvals.py](../../../agent/src/omni/channels/web_api/approvals.py) | V1 | 31 | 人工审批 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_web_auth.py](../../../agent/tests/test_web_auth.py)

直接静态依赖：`assembly`、`http-contracts`

## MCP 管理 API · http-mcp

分类：Web 接口。只读状态、显式发现、目录复核和重连。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py) | V1 | 164 | MCP 管理 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |
| [web/mcp/index.html](../../../web/mcp/index.html) | V1 | 25 | 原生源码、平台脚本或界面资源 |
| [web/mcp/mcp.css](../../../web/mcp/mcp.css) | V1 | 227 | 原生源码、平台脚本或界面资源 |
| [web/mcp/mcp.js](../../../web/mcp/mcp.js) | V1 | 364 | 原生源码、平台脚本或界面资源 |
| [web/mcp/panel.html](../../../web/mcp/panel.html) | V1 | 37 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/MCP.md](../../../docs/v/reference/MCP.md)

测试：[agent/tests/test_mcp.py](../../../agent/tests/test_mcp.py)

直接静态依赖：`assembly`、`http-contracts`、`mcp`

## 服务与诊断 API · http-operations

分类：Web 接口。系统概览、doctor、备份、服务启停和日志。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py) | V1 | 133 | 服务与诊断 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)

测试：[agent/tests/test_web.py](../../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`platform`

## 调度与投递 API · http-automation

分类：Web 接口。任务创建/编辑/启停、历史和 outbox 人工处理。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py) | V1 | 191 | 调度与可靠投递 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_cron.py](../../../agent/tests/test_cron.py)

直接静态依赖：`assembly`、`http-contracts`、`safety`、`storage`

## 发布草稿 API · http-publications

分类：Web 接口。草稿查询、乐观编辑和未知结果核查，不直接发布。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/publications.py](../../../agent/src/omni/channels/web_api/publications.py) | V1 | 63 | 发布草稿 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/BILIBILI_WRITING.md](../../../docs/v/reference/BILIBILI_WRITING.md)

测试：[agent/tests/test_bilibili_publishing.py](../../../agent/tests/test_bilibili_publishing.py)

直接静态依赖：`assembly`、`bilibili`、`storage`

## 能力与诊断面板 · http-capabilities

分类：Web 接口。被动诊断和能力汇总；显式探测分开执行，不自动启动依赖。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/capabilities.py](../../../agent/src/omni/channels/web_api/capabilities.py) | V1 | 44 | 能力与诊断只读聚合：不连接 MCP、不访问手机、不启动终端/服务。 |
| [web/capabilities/capabilities.css](../../../web/capabilities/capabilities.css) | V1 | 266 | 原生源码、平台脚本或界面资源 |
| [web/capabilities/capabilities.js](../../../web/capabilities/capabilities.js) | V1 | 185 | 原生源码、平台脚本或界面资源 |
| [web/capabilities/index.html](../../../web/capabilities/index.html) | V1 | 25 | 原生源码、平台脚本或界面资源 |
| [web/capabilities/panel.html](../../../web/capabilities/panel.html) | V1 | 48 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)

测试：[agent/tests/test_android_streams.py](../../../agent/tests/test_android_streams.py)

直接静态依赖：无内部模块依赖

## 安卓设备 API 与面板 · http-android

分类：Web 接口。按设备固定目标、配对归属的观察与受控应用操作。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py) | V1 | 104 | 按设备寻址的人工安卓适配；归属取已验配对身份，动作仍需单次观察。 |
| [agent/src/omni/channels/web_api/android_stream.py](../../../agent/src/omni/channels/web_api/android_stream.py) | V1 | 40 | MJPEG 适配：订阅共享帧，异步取消及时归还连接，不给预览帧授权令牌。 |
| [web/android/android.css](../../../web/android/android.css) | V1 | 342 | 原生源码、平台脚本或界面资源 |
| [web/android/android.js](../../../web/android/android.js) | V1 | 368 | 原生源码、平台脚本或界面资源 |
| [web/android/index.html](../../../web/android/index.html) | V1 | 114 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/ANDROID.md](../../../docs/v/reference/ANDROID.md)

测试：[agent/tests/test_android_devices.py](../../../agent/tests/test_android_devices.py)

直接静态依赖：`web-ui`

## 桌面与安卓 API · http-devices

分类：Web 接口。窗口观察、设备树/锁帧、受观察约束的手动动作和 MJPEG。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py) | V1 | 141 | 桌面与安卓 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/v/reference/ANDROID.md](../../../docs/v/reference/ANDROID.md)、[docs/v/reference/DESKTOP.md](../../../docs/v/reference/DESKTOP.md)

测试：[agent/tests/test_web.py](../../../agent/tests/test_web.py)、[agent/tests/test_web_api.py](../../../agent/tests/test_web_api.py)

直接静态依赖：`assembly`、`http-android`、`http-contracts`

## HTTP 请求契约 · http-contracts

分类：Web 接口。公共请求模型；只承载数据声明，旧 web 模型名继续显式转发。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py) | V1 | 240 | 工作台请求契约；路由共用，旧 web.py 继续显式转发公共模型名。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../../agent/tests/test_web_api.py)

直接静态依赖：无内部模块依赖

## 市场 API（人设与插件） · http-market

分类：Web 接口。社区源配置、人设卡与插件索引代理，以及插件的安装/激活/启停/卸载；安装只落隔离区，不执行代码。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py) | V1 | 298 | 市场 HTTP 适配：人设卡与插件列表/安装/激活，全部消费注入 Runtime。 |

文档：[docs/v/reference/PLUGIN_SYSTEM.md](../../../docs/v/reference/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_market_api.py](../../../agent/tests/test_market_api.py)、[agent/tests/test_market_client.py](../../../agent/tests/test_market_client.py)

直接静态依赖：`assembly`、`community`、`updates`

## HTTP 路由装配 · http-routing

分类：Web 接口。聚合注入 Runtime 的领域路由，由应用入口统一挂载；不与请求契约合并为同一职责。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/web_api/__init__.py](../../../agent/src/omni/channels/web_api/__init__.py) | V1 | 59 | 工作台领域路由聚合；应用生命周期与统一鉴权仍由 web.py 持有。 |
| [agent/src/omni/channels/web_api/embodiments.py](../../../agent/src/omni/channels/web_api/embodiments.py) | V1 | 142 | 数字具身总览与B站账号状态：只读聚合，不代替各能力域自己的操作接口。 |
| [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py) | V1 | 287 | 记忆系统 HTTP 适配（M1–M6）：只消费注入 Runtime，路由聚合进既有鉴权闸门。 |
| [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py) | V1 | 422 | 配对与设置HTTP适配；复用注入身份和Runtime，不承担应用生命周期。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)、[docs/v/reference/REFACTORING.md](../../../docs/v/reference/REFACTORING.md)

测试：[agent/tests/test_web_api.py](../../../agent/tests/test_web_api.py)

直接静态依赖：`assembly`、`bilibili`、`command-jobs`、`config`、`engine`、`http-android`、`http-approvals`、`http-artifacts`、`http-automation`、`http-context`、`http-contracts`、`http-conversation`、`http-devices`、`http-market`、`http-mcp`、`http-memory`、`http-operations`、`http-publications`、`http-runs`、`http-sessions`、`http-skills`、`http-storage`、`http-updates`、`models`、`platform`、`safety`、`storage`、`web-ui`

## CLI 入口 · cli

分类：交互与终端。终端对话命令与 Runtime 适配。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/cli.py](../../../agent/src/omni/channels/cli.py) | V1 | 147 | 本地终端 REPL 入口：uv run omni。 |

文档：[docs/v/HANDOFF.md](../../../docs/v/HANDOFF.md)

测试：关联模块集成验证

直接静态依赖：`assembly`、`config`、`models`、`safety`

## QQ 入口 · qq-channel

分类：交互与终端。QQ 场景队列、事件去重和消息收发适配。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/qq.py](../../../agent/src/omni/channels/qq.py) | V1 | 367 | QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。 |

文档：[docs/v/reference/QQ_SCENARIOS.md](../../../docs/v/reference/QQ_SCENARIOS.md)

测试：[agent/tests/test_qq.py](../../../agent/tests/test_qq.py)、[agent/tests/test_qq_scope.py](../../../agent/tests/test_qq_scope.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`models`、`platform`、`qq-tools`、`safety`

## 心跳入口 · heartbeat

分类：交互与终端。定时领取、执行、投递与周期清理。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/heartbeat.py](../../../agent/src/omni/channels/heartbeat.py) | V1 | 117 | 心跳入口：uv run omni-heartbeat（定时任务常驻调度，无人值守 fail-closed）。 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)

测试：[agent/tests/test_cron.py](../../../agent/tests/test_cron.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`platform`、`safety`、`storage`

## 入口公共适配 · channels

分类：交互与终端。入口生命周期和只读诊断/检索评测命令。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/channels/__init__.py](../../../agent/src/omni/channels/__init__.py) | V1 | 1 | 入口层：QQ(NapCat)、CLI、Web（P0 只有 CLI）。 |
| [agent/src/omni/channels/doctor.py](../../../agent/src/omni/channels/doctor.py) | V1 | 56 | 只读 doctor 命令；不组装运行态，以免诊断触发建库、迁移或后台任务。 |
| [agent/src/omni/channels/eval_retrieval.py](../../../agent/src/omni/channels/eval_retrieval.py) | V1 | 80 | 在临时数据库评测合成样本；hybrid 模式须显式传配置，默认完全离线。 |
| [agent/src/omni/channels/lifecycle.py](../../../agent/src/omni/channels/lifecycle.py) | V1 | 28 | 常驻入口响应本次启动的停止请求；不构造运行态、不自行终止其它进程。 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)

测试：关联模块集成验证

直接静态依赖：`agent-directories`、`assembly`、`config`、`engine`、`platform`

## 个人记忆工具 · personal

分类：工具能力。时钟、事实、档案、常驻、todo、摘要与手动整理各自独立。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/personal/__init__.py](../../../agent/src/omni/tools/personal/__init__.py) | V1 | 60 | 个人内置能力的注册入口：事实、档案、常驻、待办、摘要和整理。 |
| [agent/src/omni/tools/personal/clock.py](../../../agent/src/omni/tools/personal/clock.py) | V1 | 25 | 个人能力：clock；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/digest.py](../../../agent/src/omni/tools/personal/digest.py) | V1 | 68 | 个人能力：digest；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/facts.py](../../../agent/src/omni/tools/personal/facts.py) | V1 | 259 | 个人能力：facts；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/journal.py](../../../agent/src/omni/tools/personal/journal.py) | V1 | 64 | 记忆系统工具（二）：日/周/月笔记的读取（M3/M4/M5）。 |
| [agent/src/omni/tools/personal/people.py](../../../agent/src/omni/tools/personal/people.py) | V1 | 247 | 记忆系统工具（三）：核心人物记忆（M6）——查询、记住与临时事件。 |
| [agent/src/omni/tools/personal/profile.py](../../../agent/src/omni/tools/personal/profile.py) | V1 | 133 | 个人能力：profile；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/refine.py](../../../agent/src/omni/tools/personal/refine.py) | V1 | 57 | 个人能力：refine；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/resident.py](../../../agent/src/omni/tools/personal/resident.py) | V1 | 80 | 个人能力：resident；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/todos.py](../../../agent/src/omni/tools/personal/todos.py) | V1 | 85 | 个人能力：todos；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/transcript.py](../../../agent/src/omni/tools/personal/transcript.py) | V1 | 207 | 记忆系统工具（一）：全量记忆两个模块的检索与精确读取（M1/M2）。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_tools.py](../../../agent/tests/test_tools.py)、[agent/tests/test_profile_tools.py](../../../agent/tests/test_profile_tools.py)、[agent/tests/test_resident.py](../../../agent/tests/test_resident.py)

直接静态依赖：`storage`、`toolkit`

## 技能工具 · skills

分类：插件与扩展。技能查询/创建/编辑/回滚与外来技能隔离安装入口。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/skills/__init__.py](../../../agent/src/omni/tools/skills/__init__.py) | V1 | 5 | skills 能力域注册入口。 |
| [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py) | V1 | 452 | 技能工具集（toolset=core）：技能的渐进披露与维护。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_skills.py](../../../agent/tests/test_skills.py)、[agent/tests/test_skill_inbox.py](../../../agent/tests/test_skill_inbox.py)

直接静态依赖：`storage`、`toolkit`

## 调度工具 · scheduling

分类：工具能力。周期/一次性任务、编辑、依赖和执行查询。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/scheduling/__init__.py](../../../agent/src/omni/tools/scheduling/__init__.py) | V1 | 5 | scheduling 能力域注册入口。 |
| [agent/src/omni/tools/scheduling/tools.py](../../../agent/src/omni/tools/scheduling/tools.py) | V1 | 314 | 定时任务工具集（toolset=cron）：agent 可自主排程（Hermes cronjob_tools 精神）。 |

文档：[docs/v/HANDOFF.md](../../../docs/v/HANDOFF.md)

测试：[agent/tests/test_cron.py](../../../agent/tests/test_cron.py)

直接静态依赖：`safety`、`storage`、`toolkit`

## 委派工具 · delegation

分类：工具能力。受父预算/权限/取消约束的子任务注册入口。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/delegation/__init__.py](../../../agent/src/omni/tools/delegation/__init__.py) | V1 | 5 | delegation 能力域注册入口。 |
| [agent/src/omni/tools/delegation/tools.py](../../../agent/src/omni/tools/delegation/tools.py) | V1 | 122 | 子任务委派（Hermes delegate_task 同款）：把独立子任务交给一个干净会话去跑。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_delegate.py](../../../agent/tests/test_delegate.py)

直接静态依赖：`storage`、`toolkit`

## 系统服务工具 · system-tools

分类：工具能力。状态查询和受审批的服务操作适配。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/system/__init__.py](../../../agent/src/omni/tools/system/__init__.py) | V1 | 5 | system 能力域注册入口。 |
| [agent/src/omni/tools/system/tools.py](../../../agent/src/omni/tools/system/tools.py) | V1 | 121 | 系统工具集（toolset=system）：AI 自查本地服务状态、引导用户处置。 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)

测试：[agent/tests/test_system.py](../../../agent/tests/test_system.py)

直接静态依赖：`platform`、`toolkit`

## Agent shell 工具 · shell

分类：工具能力。只读命令语法、执行器、硬黑名单与人工确认命令；不复用交互式 PTY。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/shell/__init__.py](../../../agent/src/omni/tools/shell/__init__.py) | V1 | 37 | shell 能力域：SAFE 观察（结构化 argv）与 CONFIRM 执行（shell 语义）。 |
| [agent/src/omni/tools/shell/danger.py](../../../agent/src/omni/tools/shell/danger.py) | V1 | 46 | 硬性安全黑名单：灾难级操作命中即拒——即便用户已确认也不执行。 |
| [agent/src/omni/tools/shell/process.py](../../../agent/src/omni/tools/shell/process.py) | V1 | 182 | 进程边界：argv 执行、输出上限、超时后的进程树回收与"副作用是否已知"。 |
| [agent/src/omni/tools/shell/spec.py](../../../agent/src/omni/tools/shell/spec.py) | V1 | 539 | SAFE 命令解析：把命令串解析成**受控 argv**，解析不出来就拒绝。 |
| [agent/src/omni/tools/shell/tools.py](../../../agent/src/omni/tools/shell/tools.py) | V1 | 264 | shell 工具集（toolset=shell）：peek_command（SAFE 观察）与 run_command（CONFIRM 执行）。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_shell_tools.py](../../../agent/tests/test_shell_tools.py)

直接静态依赖：`config`、`toolkit`

## 文件工具 · files

分类：工具能力。允许根内的列表/搜索/读取/差异与受控写入回滚。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/files/__init__.py](../../../agent/src/omni/tools/files/__init__.py) | V1 | 20 | 结构化文件工具域（D13）：工作区内的有界读写、差异预览与回滚。 |
| [agent/src/omni/tools/files/history.py](../../../agent/src/omni/tools/files/history.py) | V1 | 158 | 工作区文件的修订快照（D13）：写前备份、可回滚。 |
| [agent/src/omni/tools/files/io.py](../../../agent/src/omni/tools/files/io.py) | V1 | 314 | 文本读写的有界实现（D13）：编码识别、二进制拒绝、窗口读取、原子替换。 |
| [agent/src/omni/tools/files/paths.py](../../../agent/src/omni/tools/files/paths.py) | V1 | 280 | 工作区路径边界（D13）：规范化、根校验、敏感项与"检查后不变"。 |
| [agent/src/omni/tools/files/search.py](../../../agent/src/omni/tools/files/search.py) | V1 | 169 | 有界目录遍历与内容搜索（D13）。 |
| [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py) | V1 | 674 | 结构化文件工具（D13，toolset=files）。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_files.py](../../../agent/tests/test_files.py)

直接静态依赖：`platform`、`toolkit`

## 网页读取工具 · web-fetch

分类：工具能力。公开网页的目标网络、重定向、体积限制与正文提取。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/web/__init__.py](../../../agent/src/omni/tools/web/__init__.py) | V1 | 20 | 通用网页读取（D14）：有界 HTTP 抓取 + 正文提取。 |
| [agent/src/omni/tools/web/extract.py](../../../agent/src/omni/tools/web/extract.py) | V1 | 248 | 正文提取（D14）：把 HTML 变成"能读的文本"，不执行任何东西。 |
| [agent/src/omni/tools/web/net.py](../../../agent/src/omni/tools/web/net.py) | V1 | 413 | 有界 HTTP 抓取（D14）：协议、目标网络、重定向、大小、超时全部受控。 |
| [agent/src/omni/tools/web/tools.py](../../../agent/src/omni/tools/web/tools.py) | V1 | 195 | 网页读取工具（D14，toolset=web）：`web_read`。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_web_fetch.py](../../../agent/tests/test_web_fetch.py)

直接静态依赖：`toolkit`

## 浏览器工具 · browser

分类：工具能力。Playwright 所有权、表单协议、观察与写预算。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/browser/__init__.py](../../../agent/src/omni/tools/browser/__init__.py) | V1 | 5 | 浏览器能力域（P3）：Playwright 驱动 + 写预算 + 工具。 |
| [agent/src/omni/tools/browser/budget.py](../../../agent/src/omni/tools/browser/budget.py) | V1 | 68 | 每日写操作预算：写走 UI 的第一道闸（bili-whale 的 quota 精神，xuexi/NOTES.md §4.3）。 |
| [agent/src/omni/tools/browser/driver.py](../../../agent/src/omni/tools/browser/driver.py) | V1 | 431 | Playwright 浏览器驱动：单一所有者线程 + profile 跨进程租约 + 拟人节奏。 |
| [agent/src/omni/tools/browser/forms.py](../../../agent/src/omni/tools/browser/forms.py) | V1 | 273 | 固定配置的 UI 表单观察/提交；所有操作在 BrowserDriver 的所有者线程执行。 |
| [agent/src/omni/tools/browser/tools.py](../../../agent/src/omni/tools/browser/tools.py) | V1 | 199 | 浏览器工具集（toolset=browser）：读 SAFE，写 CONFIRM。 |

文档：[docs/v/reference/BILIBILI_WRITING.md](../../../docs/v/reference/BILIBILI_WRITING.md)

测试：[agent/tests/test_browser.py](../../../agent/tests/test_browser.py)、[agent/tests/test_browser_owner.py](../../../agent/tests/test_browser_owner.py)

直接静态依赖：`config`、`storage`、`toolkit`

## 安卓工具 · android

分类：工具能力。ADB、防护、UI 树、观察令牌、锁帧和输入法。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/android/__init__.py](../../../agent/src/omni/tools/android/__init__.py) | V1 | 7 | 安卓能力域（P4 地基）：纯 ADB 协议层 + 高层语义驱动 + 工具。 |
| [agent/src/omni/tools/android/adb.py](../../../agent/src/omni/tools/android/adb.py) | V1 | 243 | 纯 ADB 协议层：设备无关的 adb 命令封装（不知道 LLM 与语义）。 |
| [agent/src/omni/tools/android/driver.py](../../../agent/src/omni/tools/android/driver.py) | V1 | 450 | Android 观察令牌、单设备互斥、一次动作与回读。磁盘缓存仅用于展示。 |
| [agent/src/omni/tools/android/process.py](../../../agent/src/omni/tools/android/process.py) | V1 | 48 | ADB 客户端的有界读取与超时；不把客户端退出当作设备操作已撤销。 |
| [agent/src/omni/tools/android/stream.py](../../../agent/src/omni/tools/android/stream.py) | V1 | 201 | 按设备共享最新 JPEG；预览不产生观察授权，慢客户端跳过旧帧。 |
| [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py) | V1 | 317 | 安卓工具集（toolset=android）：观察 SAFE，动作 CONFIRM。 |
| [agent/src/omni/tools/android/ui.py](../../../agent/src/omni/tools/android/ui.py) | V1 | 90 | UI 树解析（纯函数）：uiautomator XML → 结构化元素 → 文本树。 |

文档：[docs/v/reference/ANDROID.md](../../../docs/v/reference/ANDROID.md)

测试：[agent/tests/test_android.py](../../../agent/tests/test_android.py)、[agent/tests/test_android_observations.py](../../../agent/tests/test_android_observations.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 桌面工具 · desktop

分类：工具能力。Windows UIAutomation、窗口观察、租约与受控语义动作。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/desktop/__init__.py](../../../agent/src/omni/tools/desktop/__init__.py) | V1 | 1 | Windows 桌面域：观察绑定、语义后台动作、写后回读。 |
| [agent/src/omni/tools/desktop/driver.py](../../../agent/src/omni/tools/desktop/driver.py) | V1 | 190 |  |
| [agent/src/omni/tools/desktop/native.py](../../../agent/src/omni/tools/desktop/native.py) | V1 | 50 | 静态 UIAutomation 辅助程序。用户参数只经 stdin JSON 传递，不拼接 PowerShell。 |
| [agent/src/omni/tools/desktop/tools.py](../../../agent/src/omni/tools/desktop/tools.py) | V1 | 115 |  |
| [agent/src/omni/tools/desktop/uia.ps1](../../../agent/src/omni/tools/desktop/uia.ps1) | V1 | 198 | 原生源码、平台脚本或界面资源 |

文档：[docs/v/reference/DESKTOP.md](../../../docs/v/reference/DESKTOP.md)

测试：[agent/tests/test_desktop.py](../../../agent/tests/test_desktop.py)

直接静态依赖：`config`、`toolkit`

## B站工具 · bilibili

分类：工具能力。读 API/转写及注入浏览器协议的草稿与受控写。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/bilibili/__init__.py](../../../agent/src/omni/tools/bilibili/__init__.py) | V1 | 1 | B站能力域（P1：只读）。api 层不知道 LLM，tools 层不知道 HTTP 细节。 |
| [agent/src/omni/tools/bilibili/api.py](../../../agent/src/omni/tools/bilibili/api.py) | V1 | 414 | B站读 API 封装：只读端点 + 目标归一化 + WBI 签名。 |
| [agent/src/omni/tools/bilibili/asr.py](../../../agent/src/omni/tools/bilibili/asr.py) | V1 | 183 | 无字幕视频的 ASR 兜底（faster-whisper，本地转写）。 |
| [agent/src/omni/tools/bilibili/frames.py](../../../agent/src/omni/tools/bilibili/frames.py) | V1 | 135 | B站视频抽帧并拼成一张联络表（contact sheet）。 |
| [agent/src/omni/tools/bilibili/publishing.py](../../../agent/src/omni/tools/bilibili/publishing.py) | V1 | 269 | B站草稿→人工批准→一次 UI 提交→记录回读；浏览器能力通过协议注入。 |
| [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py) | V1 | 379 | B站读工具集（toolset=bilibili，全部 SAFE 只读）。 |
| [agent/src/omni/tools/bilibili/video_source.py](../../../agent/src/omni/tools/bilibili/video_source.py) | V1 | 65 | B站取流适配器：实现分镜域的 VideoSource 协议。 |
| [agent/src/omni/tools/bilibili/wbi.py](../../../agent/src/omni/tools/bilibili/wbi.py) | V1 | 99 | B站 WBI 签名（纯算法，无网络依赖）。 |
| [agent/src/omni/tools/bilibili/write_tools.py](../../../agent/src/omni/tools/bilibili/write_tools.py) | V1 | 114 | B站草稿工具及逐次人工发布审批；所有浏览器依赖由组装根注入。 |

文档：[docs/v/reference/BILIBILI_WRITING.md](../../../docs/v/reference/BILIBILI_WRITING.md)

测试：[agent/tests/test_bilibili_tools.py](../../../agent/tests/test_bilibili_tools.py)、[agent/tests/test_bilibili_publishing.py](../../../agent/tests/test_bilibili_publishing.py)

直接静态依赖：`config`、`storage`、`toolkit`

## 视频理解与分镜 · storyboard

分类：工具能力。切点检测、局部剪辑率格位分配、PyAV 取最锐帧、宫格渲染与可寻址索引；取流能力由平台适配器注入。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/storyboard/__init__.py](../../../agent/src/omni/tools/storyboard/__init__.py) | V1 | 48 | 视频理解能力域：把一段视频变成模型能"引用"的画面索引。 |
| [agent/src/omni/tools/storyboard/decode.py](../../../agent/src/omni/tools/storyboard/decode.py) | V1 | 409 | 用 PyAV 解视频帧（不依赖 ffmpeg 可执行文件）。 |
| [agent/src/omni/tools/storyboard/detect.py](../../../agent/src/omni/tools/storyboard/detect.py) | V1 | 428 | 场景切点检测：把一段视频切成若干镜头。 |
| [agent/src/omni/tools/storyboard/render.py](../../../agent/src/omni/tools/storyboard/render.py) | V1 | 260 | 宫格渲染：把镜头帧拼成一张模型能看、也能引用的图。 |
| [agent/src/omni/tools/storyboard/segments.py](../../../agent/src/omni/tools/storyboard/segments.py) | V1 | 337 | 镜头分段与可寻址索引。 |
| [agent/src/omni/tools/storyboard/source.py](../../../agent/src/omni/tools/storyboard/source.py) | V1 | 45 | 视频来源：把"一个目标"变成"一个本地可解码的视频文件 + 可选的带时间戳台词"。 |
| [agent/src/omni/tools/storyboard/tools.py](../../../agent/src/omni/tools/storyboard/tools.py) | V1 | 254 | 分镜工具：把视频变成"能看、能引用、能回看"的画面索引。 |

文档：[docs/v/reference/VIDEO_UNDERSTANDING.md](../../../docs/v/reference/VIDEO_UNDERSTANDING.md)

测试：[agent/tests/test_storyboard.py](../../../agent/tests/test_storyboard.py)

直接静态依赖：`toolkit`

## QQ 工具 · qq-tools

分类：工具能力。OneBot 客户端与白名单内主动消息。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/qq/__init__.py](../../../agent/src/omni/tools/qq/__init__.py) | V1 | 4 | QQ 能力域（P2）：OneBot v11 客户端（NapCat）与主动消息工具。 |
| [agent/src/omni/tools/qq/client.py](../../../agent/src/omni/tools/qq/client.py) | V1 | 299 | OneBot v11 正向 WebSocket 客户端（对接 NapCat）。 |
| [agent/src/omni/tools/qq/tools.py](../../../agent/src/omni/tools/qq/tools.py) | V1 | 123 | QQ 工具集（toolset=qq）：主动发消息。 |

文档：[docs/v/reference/QQ_SCENARIOS.md](../../../docs/v/reference/QQ_SCENARIOS.md)

测试：[agent/tests/test_outbox.py](../../../agent/tests/test_outbox.py)

直接静态依赖：`safety`、`toolkit`

## MCP 扩展适配 · mcp

分类：插件与扩展。外部 stdio 工具发现、授权、漂移撤权与子进程生命周期。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/mcp/__init__.py](../../../agent/src/omni/tools/mcp/__init__.py) | V1 | 1 | 外部 MCP 能力域：本地 stdio 客户端与工具适配；不依赖 LLM 或内核。 |
| [agent/src/omni/tools/mcp/client.py](../../../agent/src/omni/tools/mcp/client.py) | V1 | 733 | 同步 agent 到异步 MCP 的桥接：每服务一个会话所有者线程。 |
| [agent/src/omni/tools/mcp/remote.py](../../../agent/src/omni/tools/mcp/remote.py) | V1 | 178 | 显式配置的远程MCP传输与人工OAuth；不扩大工具授权，不自动打开浏览器。 |
| [agent/src/omni/tools/mcp/tools.py](../../../agent/src/omni/tools/mcp/tools.py) | V1 | 107 | MCP 工具适配：渐进发现、用户审核只读调用、人工确认调用；不决定传输细节。 |

文档：[docs/v/reference/MCP.md](../../../docs/v/reference/MCP.md)

测试：[agent/tests/test_mcp.py](../../../agent/tests/test_mcp.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 工具公共协议与兼容层 · toolkit

分类：工具能力。Registry、ToolResult、SessionRef/RunRef、路径策略、截图协议及旧 import 薄转发。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/tools/__init__.py](../../../agent/src/omni/tools/__init__.py) | V1 | 8 | 工具层：registry（注册表）+ builtin（内置工具集）+ 后续各能力域包。 |
| [agent/src/omni/tools/builtin.py](../../../agent/src/omni/tools/builtin.py) | V1 | 23 | 旧导入兼容层；新增个人工具请进入 tools/personal/。 |
| [agent/src/omni/tools/cron_tools.py](../../../agent/src/omni/tools/cron_tools.py) | V1 | 5 | 旧导入兼容层；实现位于 tools/scheduling/。 |
| [agent/src/omni/tools/delegate_tools.py](../../../agent/src/omni/tools/delegate_tools.py) | V1 | 5 | 旧导入兼容层；实现位于 tools/delegation/。 |
| [agent/src/omni/tools/pathpolicy.py](../../../agent/src/omni/tools/pathpolicy.py) | V1 | 85 | 工作区路径与敏感项的单一策略（D13）。 |
| [agent/src/omni/tools/registry.py](../../../agent/src/omni/tools/registry.py) | V1 | 275 | 工具注册表：LLM function calling 的统一入口（Registry 模式）。 |
| [agent/src/omni/tools/result.py](../../../agent/src/omni/tools/result.py) | V1 | 137 | 工具结果的结构化契约（D09）。 |
| [agent/src/omni/tools/runctx.py](../../../agent/src/omni/tools/runctx.py) | V1 | 50 | 低层可见的"当前运行"引用（D09）。 |
| [agent/src/omni/tools/shell_tools.py](../../../agent/src/omni/tools/shell_tools.py) | V1 | 39 | 兼容薄层：旧的 `omni.tools.shell_tools` 导入路径继续可用。 |
| [agent/src/omni/tools/shots.py](../../../agent/src/omni/tools/shots.py) | V1 | 84 | 截图工具与 ArtifactStore 的桥（D15/A13）。 |
| [agent/src/omni/tools/skill_tools.py](../../../agent/src/omni/tools/skill_tools.py) | V1 | 5 | 旧导入兼容层；实现位于 tools/skills/。 |
| [agent/src/omni/tools/system_tools.py](../../../agent/src/omni/tools/system_tools.py) | V1 | 5 | 旧导入兼容层；实现位于 tools/system/。 |

文档：[docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

测试：[agent/tests/test_tools.py](../../../agent/tests/test_tools.py)

直接静态依赖：`delegation`、`personal`、`safety`、`scheduling`、`shell`、`skills`、`system-tools`

## 插件社区 · community

分类：插件与扩展。社区客户端、工具适配与独立分发服务器。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/plugins/community/__init__.py](../../../agent/src/omni/plugins/community/__init__.py) | V1 | 6 | 插件社区公共入口；客户端、工具与服务器分别维护。 |
| [agent/src/omni/plugins/community/client.py](../../../agent/src/omni/plugins/community/client.py) | V1 | 323 | 社区客户端：插件与人设的索引 / 详情 / 安装 / 发布。 |
| [agent/src/omni/plugins/community/persona.py](../../../agent/src/omni/plugins/community/persona.py) | V1 | 93 | 人设卡：社区分发的人设内容规格与收窄。 |
| [agent/src/omni/plugins/community/server.py](../../../agent/src/omni/plugins/community/server.py) | V1 | 254 | 插件社区参考服务器（FastAPI + 文件存储，单 owner 部署）。 |
| [agent/src/omni/plugins/community/tools.py](../../../agent/src/omni/plugins/community/tools.py) | V1 | 138 | 社区工具适配；HTTP/发布逻辑由注入客户端承担。 |
| [agent/src/omni/plugins/server.py](../../../agent/src/omni/plugins/server.py) | V1 | 8 | 兼容 python -m omni.plugins.server；实现位于 plugins/community/server.py。 |

文档：[docs/v/reference/PLUGIN_SYSTEM.md](../../../docs/v/reference/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_plugin_community.py](../../../agent/tests/test_plugin_community.py)

直接静态依赖：`platform`、`plugins`、`toolkit`

## 插件宿主 · plugins

分类：插件与扩展。manifest、隔离校验、人工激活、插件注册与生命周期；进程内受信代码。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/plugins/__init__.py](../../../agent/src/omni/plugins/__init__.py) | V1 | 17 | 插件子系统（顶层包，与 channels/bootstrap 同级）。 |
| [agent/src/omni/plugins/host.py](../../../agent/src/omni/plugins/host.py) | V1 | 412 | 插件宿主：外来功能包的隔离安装、激活、装载与卸载。 |
| [agent/src/omni/plugins/manifest.py](../../../agent/src/omni/plugins/manifest.py) | V1 | 100 | 插件清单（manifest.json）：插件包的唯一身份与能力声明。 |
| [agent/src/omni/plugins/package_reader.py](../../../agent/src/omni/plugins/package_reader.py) | V1 | 214 | 插件包读取与类型校验：纯数据检查，**零执行**。 |
| [agent/src/omni/plugins/quarantine.py](../../../agent/src/omni/plugins/quarantine.py) | V1 | 242 | 插件隔离区与安装状态的文件层：隔离区落盘、清单记录、防篡改复核。 |
| [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py) | V1 | 178 | 插件生命周期工具（面向 agent）：安装 → 审阅 → 激活 → 启停 → 卸载。 |

文档：[docs/v/reference/PLUGIN_SYSTEM.md](../../../docs/v/reference/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_plugins.py](../../../agent/tests/test_plugins.py)

直接静态依赖：`platform`、`safety`、`toolkit`

## 项目索引基础设施 · catalog

分类：开发与验证。只读 AST 扫描、分类和确定性文档生成；不会执行待索引代码。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/system/catalog/__init__.py](../../../agent/src/omni/system/catalog/__init__.py) | 共享 | 5 | 项目索引：只解析源码与显式分类，不导入被索引模块、不接触运行数据。 |
| [agent/src/omni/system/catalog/features.py](../../../agent/src/omni/system/catalog/features.py) | 共享 | 217 | Developer navigation from an explicit manifest. Resolve Python locations from AST only. |
| [agent/src/omni/system/catalog/render.py](../../../agent/src/omni/system/catalog/render.py) | 共享 | 58 | 索引输出：从同一结构生成模块表、API 表和可机器查询的 JSON。 |
| [agent/src/omni/system/catalog/scan.py](../../../agent/src/omni/system/catalog/scan.py) | 共享 | 408 | 静态源码索引；扫描范围为本地 Agent 的源码包（兼容旧布局），不读取 config/data/.env。 |
| [agent/src/omni/system/catalog/schema.py](../../../agent/src/omni/system/catalog/schema.py) | 共享 | 717 | 模块分类与关键调用链；分层规则见 docs/v/reference/ARCHITECTURE.md。 |

文档：[docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

测试：[agent/tests/test_project_catalog.py](../../../agent/tests/test_project_catalog.py)

直接静态依赖：`config`

## 平台基础设施 · platform

分类：平台与数据。服务、进程身份、配置写回、锁/原子文件、日志、doctor、备份与验收。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/system/__init__.py](../../../agent/src/omni/system/__init__.py) | V1 | 6 | 本地系统域：服务管理（NapCat / 常驻通道启停与状态）+ 配置读写。 |
| [agent/src/omni/system/archive.py](../../../agent/src/omni/system/archive.py) | V1 | 236 | 可恢复的项目归档事务；不自动挑选用户资料，不删除源数据库。 |
| [agent/src/omni/system/atomic.py](../../../agent/src/omni/system/atomic.py) | V1 | 38 | 原子写文件（A09 的同源问题）。 |
| [agent/src/omni/system/backup.py](../../../agent/src/omni/system/backup.py) | V1 | 567 | 离线备份与恢复（D25 的提前子项，D05 迁移前必须先能用）。 |
| [agent/src/omni/system/config_io.py](../../../agent/src/omni/system/config_io.py) | V1 | 490 | config.yaml 安全读写：保留注释的定向修改（只动白名单键）。 |
| [agent/src/omni/system/doctor.py](../../../agent/src/omni/system/doctor.py) | V1 | 139 | 只读诊断：配置、安装元数据、DB 完整性和进程身份；不修复、不调用模型。 |
| [agent/src/omni/system/filelock.py](../../../agent/src/omni/system/filelock.py) | V1 | 157 | 跨进程文件锁（D08）。 |
| [agent/src/omni/system/observability.py](../../../agent/src/omni/system/observability.py) | V1 | 114 | 有界诊断日志。事件仅保留关联字段，排除对话、工具参数与结果正文。 |
| [agent/src/omni/system/path_guard.py](../../../agent/src/omni/system/path_guard.py) | V1 | 46 | 跨域共享的路径安全原语：zip/目录条目名规范化与内容摘要。 |
| [agent/src/omni/system/persona_presets.py](../../../agent/src/omni/system/persona_presets.py) | V1 | 43 | 人设预设文件的版本化事务；不改变当前运行人格，不接触浏览器存储。 |
| [agent/src/omni/system/persona_rules.py](../../../agent/src/omni/system/persona_rules.py) | V1 | 163 | 人格保存契约的判定规则（判定逻辑，不抄 surfcore 的形态）。 |
| [agent/src/omni/system/process_identity.py](../../../agent/src/omni/system/process_identity.py) | V1 | 295 | 进程身份与 Windows 句柄终止；不以裸 PID 或端口身份授权终止。 |
| [agent/src/omni/system/protected_json.py](../../../agent/src/omni/system/protected_json.py) | V1 | 60 | Windows当前用户DPAPI保护的JSON文件；不回显凭据，不跨用户解密。 |
| [agent/src/omni/system/provenance.py](../../../agent/src/omni/system/provenance.py) | V1 | 48 | 服务启动源码指纹，不依赖 Git 可用性，不包含配置/凭据/运行数据。 |
| [agent/src/omni/system/release_gate.py](../../../agent/src/omni/system/release_gate.py) | V1 | 98 | 校验真实证据与常驻结果；永不执行发布或代替用户批准。 |
| [agent/src/omni/system/service_overview.py](../../../agent/src/omni/system/service_overview.py) | V1 | 134 | 把服务状态、守护记账与运行态快照拼成前端要的一份汇总。 |
| [agent/src/omni/system/service_state.py](../../../agent/src/omni/system/service_state.py) | V1 | 124 | 服务运行态快照：子进程写，工作台读。 |
| [agent/src/omni/system/services.py](../../../agent/src/omni/system/services.py) | V1 | 845 | 本地服务管理：NapCat 与 OmniOperator 常驻服务的 启动/停止/状态。 |
| [agent/src/omni/system/soak.py](../../../agent/src/omni/system/soak.py) | V1 | 371 | 只读常驻采样与连续性核验；不会启动服务、调用模型或投递消息。 |
| [agent/src/omni/system/stderr_log.py](../../../agent/src/omni/system/stderr_log.py) | V1 | 71 | 子进程 stderr 的有限管道泵：持续排空、跨块脱敏与文件轮转。 |
| [agent/src/omni/system/supervisor.py](../../../agent/src/omni/system/supervisor.py) | V1 | 384 | 服务守护：让常驻服务在 agent 运行期间保持在线。 |
| [agent/src/omni/system/text_guard.py](../../../agent/src/omni/system/text_guard.py) | V1 | 268 | 确定性文本守卫：凭据形态与高危标识的扫描（**单一真源**）。 |

文档：[docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)、[docs/v/reference/ACCEPTANCE.md](../../../docs/v/reference/ACCEPTANCE.md)

测试：[agent/tests/test_backup.py](../../../agent/tests/test_backup.py)、[agent/tests/test_process_identity.py](../../../agent/tests/test_process_identity.py)

直接静态依赖：`agent-directories`、`config`、`updates`

## Python 包入口 · package

分类：开发与验证。安装包标识。

| 文件 | 归属 | 行数 | 职责 |
|---|---|---:|---|
| [agent/src/omni/__init__.py](../../../agent/src/omni/__init__.py) | 共享 | 3 | OmniOperator：像人一样生活在平台里的具身个人 agent。 |

文档：

测试：关联模块集成验证

直接静态依赖：无内部模块依赖
