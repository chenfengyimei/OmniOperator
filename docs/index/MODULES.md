# 模块与源码索引

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

源码指纹：`297fe487b720bf406193fa50d58224bc564926dda950d63aa39cc7e05d397012`。包含 65 个职责模块、736 个源码/界面文件。

## V2 领域合同 · v2-domain

分类：内核与模型。纯领域状态机、结构化结果与配置校验；不依赖旧运行时。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/domain/__init__.py](../../agent/src/omni/domain/__init__.py) | 1 | V2 business rules. No storage, operating-system or transport dependencies. |
| [agent/src/omni/domain/action_review.py](../../agent/src/omni/domain/action_review.py) | 199 | Owner observations about unknown writes; does not settle actions, authorize replay or release model costs. |
| [agent/src/omni/domain/android.py](../../agent/src/omni/domain/android.py) | 401 | Android identities, bounded UI observations and approved input contracts; no native I/O. |
| [agent/src/omni/domain/android_unicode.py](../../agent/src/omni/domain/android_unicode.py) | 121 | Pinned helper profiles and authenticated, bounded Unicode input messages; no device I/O. |
| [agent/src/omni/domain/audit_retention.py](../../agent/src/omni/domain/audit_retention.py) | 291 | Agent audit-retention subjects, frozen previews and explicit plans; performs no I/O or deletion. |
| [agent/src/omni/domain/bilibili.py](../../agent/src/omni/domain/bilibili.py) | 286 | Explicit Bilibili form profiles, targets and mention ranges; no browser, network or storage. |
| [agent/src/omni/domain/capabilities.py](../../agent/src/omni/domain/capabilities.py) | 116 | Read-only capability projection over the instance health snapshot; no I/O and no acceptance claims. |
| [agent/src/omni/domain/configuration.py](../../agent/src/omni/domain/configuration.py) | 154 | Typed configuration payloads and fingerprints; contains no I/O or credential values. |
| [agent/src/omni/domain/context.py](../../agent/src/omni/domain/context.py) | 56 | Bounded conversation policy; whole turns are the smallest compression unit. |
| [agent/src/omni/domain/contracts.py](../../agent/src/omni/domain/contracts.py) | 161 | Typed execution contracts and state transitions; performs no I/O. |
| [agent/src/omni/domain/credential_ipc.py](../../agent/src/omni/domain/credential_ipc.py) | 109 | Bounded native credential messages; no platform selection, imports or diagnostics payloads. |
| [agent/src/omni/domain/credentials.py](../../agent/src/omni/domain/credentials.py) | 79 | Credential reference and payload bounds; contains no secret store or platform dependency. |
| [agent/src/omni/domain/delivery.py](../../agent/src/omni/domain/delivery.py) | 89 | Outbound intent identities and trusted transport results; no I/O or credentials. |
| [agent/src/omni/domain/desktop.py](../../agent/src/omni/domain/desktop.py) | 613 | Bounded desktop discovery and read-only UI contracts; no native I/O or action authorization. |
| [agent/src/omni/domain/desktop_actions.py](../../agent/src/omni/domain/desktop_actions.py) | 228 | Exact semantic action contracts; observations never grant write authority. |
| [agent/src/omni/domain/desktop_portal.py](../../agent/src/omni/domain/desktop_portal.py) | 240 | Ephemeral window-share consent and helper protocol; grants never identify a native PID or authorize input. |
| [agent/src/omni/domain/documents.py](../../agent/src/omni/domain/documents.py) | 100 | Bounded source text, deterministic chunks and local vector math; contains no I/O. |
| [agent/src/omni/domain/event_retention.py](../../agent/src/omni/domain/event_retention.py) | 289 | Bounded event-retention previews and explicit plans; performs no I/O, deletion or sequence reconstruction. |
| [agent/src/omni/domain/extensions.py](../../agent/src/omni/domain/extensions.py) | 483 | Static trusted-plugin metadata, immutable capture records and owner authorizations; never loads code or files. |
| [agent/src/omni/domain/foreground.py](../../agent/src/omni/domain/foreground.py) | 107 | Single-use foreground authority and bounded input, independent of native APIs. |
| [agent/src/omni/domain/history.py](../../agent/src/omni/domain/history.py) | 142 | Canonical, bounded history cursors bind a stream and its retention generation; performs no I/O. |
| [agent/src/omni/domain/images.py](../../agent/src/omni/domain/images.py) | 207 | Validated image references, immutable model policies and conservative low-detail metering; no I/O. |
| [agent/src/omni/domain/installation.py](../../agent/src/omni/domain/installation.py) | 63 | Immutable local installation receipt written once per home; no I/O and no upgrade execution. |
| [agent/src/omni/domain/local_vectors.py](../../agent/src/omni/domain/local_vectors.py) | 156 | Local embedding bundle and bounded transport contracts; no files, model imports or I/O. |
| [agent/src/omni/domain/maintenance.py](../../agent/src/omni/domain/maintenance.py) | 115 | Bounded local maintenance requests, ownership claims and lease times; no ORM, I/O or execution. |
| [agent/src/omni/domain/mcp.py](../../agent/src/omni/domain/mcp.py) | 419 | External MCP transport identity, catalog fingerprints and bounded model text; performs no I/O. |
| [agent/src/omni/domain/media.py](../../agent/src/omni/domain/media.py) | 214 | Bounded media sources, transcripts and frame sheets; timestamps are anchors, never guesses. |
| [agent/src/omni/domain/memory_organization.py](../../agent/src/omni/domain/memory_organization.py) | 118 | Bounded exact-fact organization requests and owner decisions; no memory writes, model calls or I/O. |
| [agent/src/omni/domain/memory_semantics.py](../../agent/src/omni/domain/memory_semantics.py) | 55 | Bounded derived-memory indexing and related-fact proposals; no I/O or fact rewriting. |
| [agent/src/omni/domain/metering.py](../../agent/src/omni/domain/metering.py) | 80 | Request shape and capacity policy shared by counting and model invocation; no I/O. |
| [agent/src/omni/domain/model_stream.py](../../agent/src/omni/domain/model_stream.py) | 193 | Bounded chat stream assembly. Partial text and tool arguments never become executable results. |
| [agent/src/omni/domain/objects.py](../../agent/src/omni/domain/objects.py) | 44 | Immutable object identities and metadata constraints, independent of storage. |
| [agent/src/omni/domain/people.py](../../agent/src/omni/domain/people.py) | 48 | Person, account and memory document rules; contains no I/O or ORM types. |
| [agent/src/omni/domain/qq.py](../../agent/src/omni/domain/qq.py) | 142 | Bounded OneBot text events and explicit conversation grants; no transport I/O. |
| [agent/src/omni/domain/recovery_authority.py](../../agent/src/omni/domain/recovery_authority.py) | 84 | Versioned authority for keeping an isolated predecessor fenced; no IO or claims about external effects. |
| [agent/src/omni/domain/reference_release.py](../../agent/src/omni/domain/reference_release.py) | 69 | Bounded artifact-reference release plans; validates identities without accessing storage or files. |
| [agent/src/omni/domain/release_fence.py](../../agent/src/omni/domain/release_fence.py) | 78 | Persisted startup exclusion contract shared by immutable installations; no IO or mutable state. |
| [agent/src/omni/domain/releases.py](../../agent/src/omni/domain/releases.py) | 275 | Reviewed immutable release/recovery plans; same-schema first delivery, with no platform or storage IO. |
| [agent/src/omni/domain/retention.py](../../agent/src/omni/domain/retention.py) | 101 | Execution-body retention policy; time is injected, no erasure or file operations. |
| [agent/src/omni/domain/routing.py](../../agent/src/omni/domain/routing.py) | 128 | Deterministic capability tiers and immutable route policy; neither permissions nor model/network calls. |
| [agent/src/omni/domain/scheduling.py](../../agent/src/omni/domain/scheduling.py) | 190 | Deterministic UTC scheduling and numeric cron rules; resolved timezone data is injected. |
| [agent/src/omni/domain/schema_upgrade.py](../../agent/src/omni/domain/schema_upgrade.py) | 81 | Explicit forward-only V2 schema routes bound to both immutable runtime inventories; no IO. |
| [agent/src/omni/domain/service_activation.py](../../agent/src/omni/domain/service_activation.py) | 83 | Bind unattended exchange activation to one reviewed boot; pure persisted contracts with no platform IO. |
| [agent/src/omni/domain/service_admin.py](../../agent/src/omni/domain/service_admin.py) | 286 | Exact operation or exchange privilege scope; never accepts arbitrary commands, paths or identities. |
| [agent/src/omni/domain/service_cleanup.py](../../agent/src/omni/domain/service_cleanup.py) | 180 | Exact quarantined-registration cleanup plans; no filesystem, native calls or reinterpretation of old effects. |
| [agent/src/omni/domain/service_control.py](../../agent/src/omni/domain/service_control.py) | 206 | Reviewed service start/drain identities and domain-separated HMAC messages; no network or process IO. |
| [agent/src/omni/domain/service_deployment.py](../../agent/src/omni/domain/service_deployment.py) | 568 | Pure, versioned service plans. Validation performs no filesystem or OS calls. |
| [agent/src/omni/domain/service_exchange.py](../../agent/src/omni/domain/service_exchange.py) | 329 | Reviewed same-schema service exchanges and ordered witnesses; no platform, process or storage IO. |
| [agent/src/omni/domain/service_isolation.py](../../agent/src/omni/domain/service_isolation.py) | 115 | Reviewed offline startup quarantine; retains unknown outcomes and authorizes only a separate recovery plan. |
| [agent/src/omni/domain/service_operations.py](../../agent/src/omni/domain/service_operations.py) | 140 | Fingerprint offline service changes; registration never implies a healthy running service. |
| [agent/src/omni/domain/settings.py](../../agent/src/omni/domain/settings.py) | 98 | Editable Agent settings and immutable resolved run configuration; no I/O. |
| [agent/src/omni/domain/skills.py](../../agent/src/omni/domain/skills.py) | 246 | Versioned textual skills, bounded disclosure and edit rules; skills confer no authority. |
| [agent/src/omni/domain/terminal_review.py](../../agent/src/omni/domain/terminal_review.py) | 87 | Owner observations of unknown terminal effects; never a receipt correction or replay authority. |
| [agent/src/omni/domain/terminals.py](../../agent/src/omni/domain/terminals.py) | 124 | Explicit owner terminal requests and metadata identities; no model tools, shell execution or transcript storage. |

文档：[docs/V2_REFACTOR.md](../../docs/V2_REFACTOR.md)

测试：[agent/tests/test_v2_architecture.py](../../agent/tests/test_v2_architecture.py)

直接静态依赖：无内部模块依赖

## V2 应用服务 · v2-application

分类：内核与模型。单实例调度、会话顺序、审批、取消与费用结算；仍在开发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/application/__init__.py](../../agent/src/omni/application/__init__.py) | 1 | V2 use cases consume ports and domain types, never infrastructure. |
| [agent/src/omni/application/action_review.py](../../agent/src/omni/application/action_review.py) | 111 | Owner reconciliation records observations, never dispatches an action or rewrites its result. |
| [agent/src/omni/application/android.py](../../agent/src/omni/application/android.py) | 802 | Owner Android device grants, ephemeral observations and shared approved actions; no platform I/O. |
| [agent/src/omni/application/bilibili.py](../../agent/src/omni/application/bilibili.py) | 210 | Owner Bilibili queries and publication preparation; all sending remains in shared approvals/outbox. |
| [agent/src/omni/application/configuration.py](../../agent/src/omni/application/configuration.py) | 186 | Owner configuration use cases; import only previews and never starts an adapter. |
| [agent/src/omni/application/context.py](../../agent/src/omni/application/context.py) | 236 | Token-aware bounded compression; external calls never hold a transaction. |
| [agent/src/omni/application/credentials.py](../../agent/src/omni/application/credentials.py) | 185 | Serialize audited owner credential changes; shield accepted mutations through caller cancellation. |
| [agent/src/omni/application/debugging.py](../../agent/src/omni/application/debugging.py) | 122 | Boot-scoped debug authorization, durable audit and bounded maintenance; no log files here. |
| [agent/src/omni/application/delivery.py](../../agent/src/omni/application/delivery.py) | 428 | Supervised, bounded outbound execution; never supplies a real transport by default. |
| [agent/src/omni/application/desktop.py](../../agent/src/omni/application/desktop.py) | 533 | Owner desktop observations with short-lived scoped identities; never authorizes or dispatches desktop writes. |
| [agent/src/omni/application/desktop_actions.py](../../agent/src/omni/application/desktop_actions.py) | 224 | Owner-selected semantic commands using the shared run, approval and action ledger. |
| [agent/src/omni/application/desktop_portal.py](../../agent/src/omni/application/desktop_portal.py) | 328 | Owner/view-scoped temporary share sessions with durable consent metadata; no image, native identity or input authorization. |
| [agent/src/omni/application/documents.py](../../agent/src/omni/application/documents.py) | 365 | Source documents, explicitly requested local indexes and registered retrieval dependencies. |
| [agent/src/omni/application/engine.py](../../agent/src/omni/application/engine.py) | 643 | One supervised runtime: admission, session FIFO, model/tool execution and shutdown. |
| [agent/src/omni/application/exchange_recovery.py](../../agent/src/omni/application/exchange_recovery.py) | 86 | Explicit offline exchange quarantine and new-directory rescue; no native changes or unknown-outcome erasure. |
| [agent/src/omni/application/exchange_recovery_steps.py](../../agent/src/omni/application/exchange_recovery_steps.py) | 47 | Reuse release snapshots and fences for explicit quarantine/rescue; never clear a child unknown or call a manager. |
| [agent/src/omni/application/exchange_steps.py](../../agent/src/omni/application/exchange_steps.py) | 139 | Compose existing release/registration/control use cases without holding instance locks across target startup. |
| [agent/src/omni/application/extensions.py](../../agent/src/omni/application/extensions.py) | 507 | Trusted extension installation, scoped catalogs and explicit authorization; no plugin imports or process creation. |
| [agent/src/omni/application/images.py](../../agent/src/omni/application/images.py) | 150 | Validated image admission, context selection and last-mile wire materialization over injected ports. |
| [agent/src/omni/application/local_vectors.py](../../agent/src/omni/application/local_vectors.py) | 40 | Shared local-vector preflight and computation; ownership stays with the composing lifecycle. |
| [agent/src/omni/application/maintenance.py](../../agent/src/omni/application/maintenance.py) | 253 | Own bounded durable maintenance executions; never runs exclusive offline cleanup or remote model/device actions. |
| [agent/src/omni/application/mcp.py](../../agent/src/omni/application/mcp.py) | 335 | Owner MCP servers: explicit connect, bounded catalog, per-tool authorization and run-scoped calls. |
| [agent/src/omni/application/media.py](../../agent/src/omni/application/media.py) | 471 | Owner media sources, bounded processing jobs and model-visible anchored transcripts. |
| [agent/src/omni/application/memory_organization.py](../../agent/src/omni/application/memory_organization.py) | 144 | Own bounded exact-fact proposals and explicit owner decisions; never calls a model or erases facts. |
| [agent/src/omni/application/memory_semantics.py](../../agent/src/omni/application/memory_semantics.py) | 224 | Owned local memory indexing, hybrid recall and explicit related-fact reviews; no automatic merge or deletion. |
| [agent/src/omni/application/memory_tools.py](../../agent/src/omni/application/memory_tools.py) | 107 | Memory capabilities share application transactions and verified source rules. |
| [agent/src/omni/application/model_calls.py](../../agent/src/omni/application/model_calls.py) | 293 | One preflight, authorization and settlement path for model and compression calls. |
| [agent/src/omni/application/model_output.py](../../agent/src/omni/application/model_output.py) | 57 | Bounded previews owned by active model calls; durable messages remain the only completed output. |
| [agent/src/omni/application/objects.py](../../agent/src/omni/application/objects.py) | 140 | Object publication and access use cases; no filesystem paths or ORM objects. |
| [agent/src/omni/application/operations.py](../../agent/src/omni/application/operations.py) | 127 | Bounded operational queries and privacy-preserving diagnostic export; no model or device actions. |
| [agent/src/omni/application/people.py](../../agent/src/omni/application/people.py) | 69 | Owner-facing people and personal-document use cases; no ORM or filesystem access. |
| [agent/src/omni/application/ports.py](../../agent/src/omni/application/ports.py) | 983 | Application boundaries. Implementations belong to adapters. |
| [agent/src/omni/application/qq.py](../../agent/src/omni/application/qq.py) | 230 | Own bounded QQ listeners and owner conversation grants; turns use the shared V2 kernel. |
| [agent/src/omni/application/release_drain.py](../../agent/src/omni/application/release_drain.py) | 30 | Bind release draining to the existing once-only boot control; reconciliation never sends another request. |
| [agent/src/omni/application/releases.py](../../agent/src/omni/application/releases.py) | 338 | Offline release preparation and single-directory selection; never elevates, registers or starts a service. |
| [agent/src/omni/application/routing.py](../../agent/src/omni/application/routing.py) | 19 | Tier configuration, query and bounded control use cases; model invocation remains in ModelCalls. |
| [agent/src/omni/application/scheduling.py](../../agent/src/omni/application/scheduling.py) | 207 | One bounded scheduler; durable identities and admission are committed together. |
| [agent/src/omni/application/service_cleanup.py](../../agent/src/omni/application/service_cleanup.py) | 78 | Review quarantined native definitions and reuse once-only registration use cases; never clears original unknowns. |
| [agent/src/omni/application/service_control.py](../../agent/src/omni/application/service_control.py) | 243 | Reviewed start and graceful stop use cases; external dispatch happens once and unknown work is never replayed. |
| [agent/src/omni/application/service_deployment.py](../../agent/src/omni/application/service_deployment.py) | 47 | Prepare reviewable service deployments and combine independent proofs; never installs or starts services. |
| [agent/src/omni/application/service_exchange.py](../../agent/src/omni/application/service_exchange.py) | 194 | Ordered service exchange ownership; interrupted native steps are observed, never automatically replayed. |
| [agent/src/omni/application/service_operations.py](../../agent/src/omni/application/service_operations.py) | 286 | Offline registration use cases with durable intent, no replay, and retained cancellation ownership. |
| [agent/src/omni/application/skills.py](../../agent/src/omni/application/skills.py) | 373 | Owned textual skill import/edit/disclosure; explicit trust and shared action authorization remain separate. |
| [agent/src/omni/application/terminal_review.py](../../agent/src/omni/application/terminal_review.py) | 127 | Owner terminal evidence use cases; no native actions, receipt changes or historical PID operations. |
| [agent/src/omni/application/terminals.py](../../agent/src/omni/application/terminals.py) | 514 | Bounded owner terminals with durable input receipts, single-writer leases and explicit output gaps. |

文档：[docs/V2_REFACTOR.md](../../docs/V2_REFACTOR.md)

测试：[agent/tests/test_v2_execution.py](../../agent/tests/test_v2_execution.py)

直接静态依赖：`v2-domain`

## V2 数据与平台适配 · v2-adapters

分类：平台与数据。单库事务、诊断、进程外能力与平台原语；平台桌面尚未接入。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/adapters/__init__.py](../../agent/src/omni/adapters/__init__.py) | 1 | V2 infrastructure and channel adapters; no import-time external activity. |
| [agent/src/omni/adapters/android.py](../../agent/src/omni/adapters/android.py) | 197 | Managed Android workers and bounded PNG loading; no main-process device API or application database. |
| [agent/src/omni/adapters/android_unicode.py](../../agent/src/omni/adapters/android_unicode.py) | 160 | Owned Unicode/IME transaction over managed workers; secrets stay in private IPC. |
| [agent/src/omni/adapters/bilibili.py](../../agent/src/omni/adapters/bilibili.py) | 554 | Bounded Bilibili reads and a single approved UI publication; credentials never enter diagnostics. |
| [agent/src/omni/adapters/build_identity.py](../../agent/src/omni/adapters/build_identity.py) | 5 | Shared inventory of executable V2 resources for runtime and deployment identities. |
| [agent/src/omni/adapters/credentials.py](../../agent/src/omni/adapters/credentials.py) | 179 | One bounded, serial credential executor; actual work outlives cancelled request waiters. |
| [agent/src/omni/adapters/diagnostic_bundle.py](../../agent/src/omni/adapters/diagnostic_bundle.py) | 341 | Bounded metadata and structured correlations; never opens business bodies or credential stores. |
| [agent/src/omni/adapters/diagnostics.py](../../agent/src/omni/adapters/diagnostics.py) | 343 | Bounded, single-writer JSON diagnostics. Business payloads belong in repositories. |
| [agent/src/omni/adapters/extensions/__init__.py](../../agent/src/omni/adapters/extensions/__init__.py) | 1 | Trusted Python extension files and process adapters; never opens the application database. |
| [agent/src/omni/adapters/extensions/hosts.py](../../agent/src/omni/adapters/extensions/hosts.py) | 208 | Bounded trusted Python worker ownership; no database, model, credential or arbitrary command access. |
| [agent/src/omni/adapters/extensions/packages.py](../../agent/src/omni/adapters/extensions/packages.py) | 564 | Bounded zero-execution package capture and immutable verified snapshots; never opens a database. |
| [agent/src/omni/adapters/extensions/worker.py](../../agent/src/omni/adapters/extensions/worker.py) | 326 | Static extension handshake and trusted Python invocation in an owned child; never opens the main database. |
| [agent/src/omni/adapters/http.py](../../agent/src/omni/adapters/http.py) | 2283 | Versioned HTTP contract for the V2 kernel; business access goes through use cases. |
| [agent/src/omni/adapters/images.py](../../agent/src/omni/adapters/images.py) | 79 | Bounded image verification and metadata-free low-detail PNG encoding; no database or remote URLs. |
| [agent/src/omni/adapters/local_vectors.py](../../agent/src/omni/adapters/local_vectors.py) | 287 | One bounded, supervised local embedding process; no native ML imports in the application process. |
| [agent/src/omni/adapters/log_query.py](../../agent/src/omni/adapters/log_query.py) | 320 | Bounded diagnostic pages with cursors that survive rename and process restart. |
| [agent/src/omni/adapters/mcp/__init__.py](../../agent/src/omni/adapters/mcp/__init__.py) | 1 | Bounded MCP transports: stdio JSON-RPC, Streamable HTTP and later explicit OAuth. |
| [agent/src/omni/adapters/mcp/hosts.py](../../agent/src/omni/adapters/mcp/hosts.py) | 196 | Bounded MCP session ownership keyed by server generation; no database or model access. |
| [agent/src/omni/adapters/mcp/http.py](../../agent/src/omni/adapters/mcp/http.py) | 316 | Bounded Streamable HTTP JSON-RPC transport with explicit bearer/OAuth tokens. |
| [agent/src/omni/adapters/mcp/oauth.py](../../agent/src/omni/adapters/mcp/oauth.py) | 484 | Explicit MCP OAuth 2.0 authorization-code flow with PKCE, state and a vault token store. |
| [agent/src/omni/adapters/mcp/stdio.py](../../agent/src/omni/adapters/mcp/stdio.py) | 315 | Bounded JSON-RPC over one owned stdio process per MCP server generation. |
| [agent/src/omni/adapters/media.py](../../agent/src/omni/adapters/media.py) | 192 | One bounded, supervised media processing subprocess; heavy libraries never load in the host. |
| [agent/src/omni/adapters/metrics.py](../../agent/src/omni/adapters/metrics.py) | 92 | Fixed-cardinality in-process measurements; never stores request identities or text. |
| [agent/src/omni/adapters/model_stream.py](../../agent/src/omni/adapters/model_stream.py) | 240 | One-shot LiteLLM chat stream with verified async upstream close; no persistence or global runtime. |
| [agent/src/omni/adapters/models.py](../../agent/src/omni/adapters/models.py) | 193 | Async model adapter. Credentials and vendor exceptions do not cross the port. |
| [agent/src/omni/adapters/native_credentials.py](../../agent/src/omni/adapters/native_credentials.py) | 368 | Serial owned OS-credential helpers; cancelled waiters never release native work or stale reads. |
| [agent/src/omni/adapters/object_store.py](../../agent/src/omni/adapters/object_store.py) | 142 | Private, immutable content-addressed files; never opens the application database. |
| [agent/src/omni/adapters/onebot.py](../../agent/src/omni/adapters/onebot.py) | 199 | V2 OneBot v11 text transport; no V1 runtime, database access or automatic write replay. |
| [agent/src/omni/adapters/persistence/__init__.py](../../agent/src/omni/adapters/persistence/__init__.py) | 1 | Only this package owns application SQL, connections and schema evolution. |
| [agent/src/omni/adapters/persistence/action_predicates.py](../../agent/src/omni/adapters/persistence/action_predicates.py) | 65 | One current owner-resolution predicate; never rewrites raw action observations or acknowledges model costs. |
| [agent/src/omni/adapters/persistence/action_review_repository.py](../../agent/src/omni/adapters/persistence/action_review_repository.py) | 446 | Owner observation ledger for uncertain actions; original actions, costs and delivery state remain unchanged. |
| [agent/src/omni/adapters/persistence/android_repository.py](../../agent/src/omni/adapters/persistence/android_repository.py) | 295 | Device grants bind immutable configuration/device identities; actions remain in the shared ledger. |
| [agent/src/omni/adapters/persistence/android_unicode_repository.py](../../agent/src/omni/adapters/persistence/android_unicode_repository.py) | 251 | Immutable helper profiles and durable IME restoration obligations; input truth stays in actions. |
| [agent/src/omni/adapters/persistence/audit_pruning.py](../../agent/src/omni/adapters/persistence/audit_pruning.py) | 56 | Offline audit-pruning bindings; shared infrastructure owns locks, verified backups and plan files. |
| [agent/src/omni/adapters/persistence/audit_pruning_repository.py](../../agent/src/omni/adapters/persistence/audit_pruning_repository.py) | 107 | Persist reviewed audit deletion and its proof atomically; preserve identities and business ledgers. |
| [agent/src/omni/adapters/persistence/audit_retention_repository.py](../../agent/src/omni/adapters/persistence/audit_retention_repository.py) | 218 | Bounded metadata-only audit retention preview; never deletes rows or creates executable plans. |
| [agent/src/omni/adapters/persistence/bilibili_authority.py](../../agent/src/omni/adapters/persistence/bilibili_authority.py) | 73 | One Bilibili authority implementation shared by owner reads, previews and final dispatch. |
| [agent/src/omni/adapters/persistence/bilibili_repository.py](../../agent/src/omni/adapters/persistence/bilibili_repository.py) | 311 | Immutable Bilibili form revisions and publications referencing the common delivery ledger. |
| [agent/src/omni/adapters/persistence/configuration_repository.py](../../agent/src/omni/adapters/persistence/configuration_repository.py) | 380 | Typed configuration revisions and Agent preferences; no connections, DDL or credentials. |
| [agent/src/omni/adapters/persistence/context_repository.py](../../agent/src/omni/adapters/persistence/context_repository.py) | 515 | Revision-bound summaries and context provenance; all operations stay inside a short transaction. |
| [agent/src/omni/adapters/persistence/credential_predicates.py](../../agent/src/omni/adapters/persistence/credential_predicates.py) | 7 | One unreviewed credential authority predicate for diagnostics and retention guards. |
| [agent/src/omni/adapters/persistence/credential_repository.py](../../agent/src/omni/adapters/persistence/credential_repository.py) | 462 | Owner credential references, operation state and review; no native I/O or connection creation. |
| [agent/src/omni/adapters/persistence/database.py](../../agent/src/omni/adapters/persistence/database.py) | 190 | Bounded, single-writer database actor with isolated read connections. |
| [agent/src/omni/adapters/persistence/delivery_predicates.py](../../agent/src/omni/adapters/persistence/delivery_predicates.py) | 32 | Shared outbound pending/review predicates for dispatch, scheduling and maintenance queries. |
| [agent/src/omni/adapters/persistence/delivery_repository.py](../../agent/src/omni/adapters/persistence/delivery_repository.py) | 927 | Transactional outbound authority, FIFO ownership and immutable attempt observations. |
| [agent/src/omni/adapters/persistence/deployed_instance.py](../../agent/src/omni/adapters/persistence/deployed_instance.py) | 30 | Check an initialized service home without migrations or business recovery. |
| [agent/src/omni/adapters/persistence/document_repository.py](../../agent/src/omni/adapters/persistence/document_repository.py) | 649 | Source revision, bounded search and derived index transactions; never reads files. |
| [agent/src/omni/adapters/persistence/event_pruning.py](../../agent/src/omni/adapters/persistence/event_pruning.py) | 56 | Offline event-pruning bindings; shared infrastructure owns locks, verified backups and plan files. |
| [agent/src/omni/adapters/persistence/event_retention_repository.py](../../agent/src/omni/adapters/persistence/event_retention_repository.py) | 332 | Preview and prune retired run events through the shared history cycle; never remove run identities. |
| [agent/src/omni/adapters/persistence/execution_pruning.py](../../agent/src/omni/adapters/persistence/execution_pruning.py) | 41 | Execution-body maintenance bindings; shared offline infrastructure owns files and lifecycle. |
| [agent/src/omni/adapters/persistence/execution_retention_repository.py](../../agent/src/omni/adapters/persistence/execution_retention_repository.py) | 464 | Execution-body eligibility, owner pins and transactional prune progress; preserves conversations and ledgers. |
| [agent/src/omni/adapters/persistence/extension_repository.py](../../agent/src/omni/adapters/persistence/extension_repository.py) | 806 | Immutable trusted-plugin versions, scoped grants and execution bindings; no plugin code or file I/O. |
| [agent/src/omni/adapters/persistence/history_protection.py](../../agent/src/omni/adapters/persistence/history_protection.py) | 69 | Shared provenance and maintenance protection for aged pruned history; never reads execution bodies. |
| [agent/src/omni/adapters/persistence/history_pruning_cycle.py](../../agent/src/omni/adapters/persistence/history_pruning_cycle.py) | 308 | One transactional history-pruning algorithm; stream policies own target eligibility and identity. |
| [agent/src/omni/adapters/persistence/history_repository.py](../../agent/src/omni/adapters/persistence/history_repository.py) | 86 | Read history pages and retention revisions in one transaction; does not delete history. |
| [agent/src/omni/adapters/persistence/history_sequence.py](../../agent/src/omni/adapters/persistence/history_sequence.py) | 27 | Allocate global history positions in the caller's transaction; never opens connections. |
| [agent/src/omni/adapters/persistence/image_repository.py](../../agent/src/omni/adapters/persistence/image_repository.py) | 331 | Scoped image metadata, model-revision policies and per-call references; never reads files or encodes images. |
| [agent/src/omni/adapters/persistence/lease_repository.py](../../agent/src/omni/adapters/persistence/lease_repository.py) | 283 | Persistent FIFO claims and fenced resource leases, within the caller's transaction. |
| [agent/src/omni/adapters/persistence/maintenance.py](../../agent/src/omni/adapters/persistence/maintenance.py) | 1066 | Exclusive offline snapshots and restore. Live SQLite files are never copied directly. |
| [agent/src/omni/adapters/persistence/maintenance_repository.py](../../agent/src/omni/adapters/persistence/maintenance_repository.py) | 543 | Bounded document-maintenance admission, fenced attempts and business-result settlement; no execution or I/O. |
| [agent/src/omni/adapters/persistence/mcp_repository.py](../../agent/src/omni/adapters/persistence/mcp_repository.py) | 1010 | MCP runtime generations, catalog snapshots, per-tool grants, run captures and call records. |
| [agent/src/omni/adapters/persistence/media_repository.py](../../agent/src/omni/adapters/persistence/media_repository.py) | 334 | Scoped media sources, bounded jobs and immutable artifact rows; never reads files. |
| [agent/src/omni/adapters/persistence/memory_organization_predicates.py](../../agent/src/omni/adapters/persistence/memory_organization_predicates.py) | 114 | One live approved-duplicate predicate for recall and new context capture; never edits memory truth. |
| [agent/src/omni/adapters/persistence/memory_organization_repository.py](../../agent/src/omni/adapters/persistence/memory_organization_repository.py) | 622 | Bounded exact-duplicate proposals and owner review; never changes, forgets or copies memory truth. |
| [agent/src/omni/adapters/persistence/memory_repository.py](../../agent/src/omni/adapters/persistence/memory_repository.py) | 375 | One memory truth for facts and personal documents, evidence, revisions and erasure. |
| [agent/src/omni/adapters/persistence/memory_semantic_repository.py](../../agent/src/omni/adapters/persistence/memory_semantic_repository.py) | 414 | Derived memory vectors and source-bound related-fact decisions; never suppresses or rewrites facts. |
| [agent/src/omni/adapters/persistence/migration_config.py](../../agent/src/omni/adapters/persistence/migration_config.py) | 23 | One migration-directory and expected-head lookup for startup and offline readers. |
| [agent/src/omni/adapters/persistence/migrations/__init__.py](../../agent/src/omni/adapters/persistence/migrations/__init__.py) | 1 | Fresh V2 baseline; V1 data is never imported. |
| [agent/src/omni/adapters/persistence/migrations/env.py](../../agent/src/omni/adapters/persistence/migrations/env.py) | 8 | Alembic environment uses the connection and exclusive ownership of its caller. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0001.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0001.py) | 93 | Initial V2 schema; immutable SQL snapshot, never imports current metadata. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0002.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0002.py) | 36 | Object pin revisions and action artifact references; frozen SQL, no current schema imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0003.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0003.py) | 150 | People, revision-bound evidence and memory context; frozen schema evolution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0004.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0004.py) | 99 | Persistent conversation summaries and context barriers; frozen SQL, no current metadata. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0005.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0005.py) | 254 | Typed business configuration and immutable run references; frozen migration. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0006.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0006.py) | 85 | Source document revisions, provenance and rebuildable local indexes; frozen sql_statements. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0007.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0007.py) | 152 | Immutable schedules, due identities and fenced work claims; frozen migration. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0008.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0008.py) | 101 | Outbound intent, attempt, approval and recovery baseline; frozen SQL only. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0009.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0009.py) | 30 | Instance-wide credential intent and outcome audit; no arbitrary Agent attribution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0010.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0010.py) | 120 | Credential authority state and immutable review. Frozen SQL; no V1 credential import. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0011.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0011.py) | 23 | Owner run retention pins, independent of run execution/configuration revisions. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0012.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0012.py) | 68 | Recoverable execution-body pruning; frozen SQL, no live model imports. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0013.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0013.py) | 61 | Historical reference-release evidence never points back to live object rows. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0014.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0014.py) | 69 | Persistent sequence allocation and scoped cursor identity precede all history deletion. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0015.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0015.py) | 74 | Immutable audit-retention plans and deletion evidence; never retain the deleted audit rows. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0016.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0016.py) | 73 | Immutable event-retention plans and deletion evidence; never retain the deleted event rows. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0017.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0017.py) | 98 | Durable local document maintenance with owned, fenced attempts and retained evidence. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0018.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0018.py) | 149 | Exact fact-duplicate organization stores immutable identities and revocable review relationships, never text. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0019.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0019.py) | 245 | Captured static Python extension revisions and epoch-bound authorization; never migrates old placeholder data. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0020.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0020.py) | 177 | Immutable owner observations for unknown writes, erasable evidence and source-invalidation fences. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0021.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0021.py) | 45 | Owner terminal metadata and immutable input receipts; no transcript or native PID execution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0022.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0022.py) | 27 | Scoped window capture artifacts and publication constraints. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0023.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0023.py) | 31 | Owner semantic commands: atomic queueing and one-way argument transfer. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0024.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0024.py) | 30 | Capture provenance for each implemented native backend; preserve existing references and guards. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0025.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0025.py) | 31 | Portal window frame provenance, retaining scoped references and immutability. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0026.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0026.py) | 29 | Owner terminal observations, immutable metadata and explicitly erasable evidence. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0027.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0027.py) | 30 | Explicit QQ conversation grants and atomic inbound run attribution. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0028.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0028.py) | 34 | Reviewed Bilibili profiles and publication references; no duplicate message body or sending state. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0029.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0029.py) | 51 | Scoped Android grants and shared owner-command/capture contracts; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0030.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0030.py) | 30 | Normalized per-run Android authority bound to the common approval ledger; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0031.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0031.py) | 60 | Pinned Unicode helper revisions and durable restoration obligations; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0032.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0032.py) | 43 | Verified images, immutable revision policies and cumulative call attachments; frozen DDL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0033.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0033.py) | 52 | Immutable tier policies, atomic run selection and per-call tool snapshots; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0034.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0034.py) | 51 | Derived memory embeddings and source-bound semantic relationship reviews; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0035.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0035.py) | 52 | Reviewed textual skills and immutable run disclosure; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0036.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0036.py) | 37 | Scoped media sources, bounded jobs and immutable anchored artifacts; frozen SQL. |
| [agent/src/omni/adapters/persistence/migrations/versions/v0037.py](../../agent/src/omni/adapters/persistence/migrations/versions/v0037.py) | 53 | MCP runtime generations, catalog fingerprints, per-tool grants, run captures and calls; frozen SQL. |
| [agent/src/omni/adapters/persistence/object_gc.py](../../agent/src/omni/adapters/persistence/object_gc.py) | 247 | Exclusive plan/apply collection; commit unreferenced metadata before unlinking files. |
| [agent/src/omni/adapters/persistence/object_inventory.py](../../agent/src/omni/adapters/persistence/object_inventory.py) | 129 | Offline object registry snapshots and bounded physical inventory validation. |
| [agent/src/omni/adapters/persistence/object_repository.py](../../agent/src/omni/adapters/persistence/object_repository.py) | 211 | Object metadata, pins and real foreign-key references; never touches files. |
| [agent/src/omni/adapters/persistence/offline_plans.py](../../agent/src/omni/adapters/persistence/offline_plans.py) | 189 | Exclusive reviewed maintenance plans; domain callbacks own eligibility and transactional progress. |
| [agent/src/omni/adapters/persistence/operations_read.py](../../agent/src/omni/adapters/persistence/operations_read.py) | 58 | Offline read-only operational snapshot; caller holds exclusive instance ownership. |
| [agent/src/omni/adapters/persistence/operations_repository.py](../../agent/src/omni/adapters/persistence/operations_repository.py) | 100 | Operational aggregates and owner credential audit; business bodies and secret values stay excluded. |
| [agent/src/omni/adapters/persistence/people_repository.py](../../agent/src/omni/adapters/persistence/people_repository.py) | 216 | People and manual account identities; personal documents reuse memory repositories. |
| [agent/src/omni/adapters/persistence/qq_authority.py](../../agent/src/omni/adapters/persistence/qq_authority.py) | 99 | Shared QQ audience and credential authority for context, model and outbound checks. |
| [agent/src/omni/adapters/persistence/qq_repository.py](../../agent/src/omni/adapters/persistence/qq_repository.py) | 242 | QQ admission and authority within the shared unit of work; no network or extra truth store. |
| [agent/src/omni/adapters/persistence/reference_release.py](../../agent/src/omni/adapters/persistence/reference_release.py) | 41 | Offline reference release uses the shared verified maintenance lifecycle; never unlinks objects. |
| [agent/src/omni/adapters/persistence/reference_release_repository.py](../../agent/src/omni/adapters/persistence/reference_release_repository.py) | 330 | Release only reviewed retired action-object references; history is not a live object reference. |
| [agent/src/omni/adapters/persistence/releases.py](../../agent/src/omni/adapters/persistence/releases.py) | 214 | Consistent release snapshots and quarantined new-directory restores under caller-owned instance locks. |
| [agent/src/omni/adapters/persistence/repository.py](../../agent/src/omni/adapters/persistence/repository.py) | 1096 | Transaction-scoped repositories. No connection creation, DDL or external calls. |
| [agent/src/omni/adapters/persistence/repository_base.py](../../agent/src/omni/adapters/persistence/repository_base.py) | 52 | Common transaction-scoped identity, ownership and audit primitives. |
| [agent/src/omni/adapters/persistence/retention_repository.py](../../agent/src/omni/adapters/persistence/retention_repository.py) | 135 | Context barriers invalidate derived views without deleting original conversations. |
| [agent/src/omni/adapters/persistence/routing_repository.py](../../agent/src/omni/adapters/persistence/routing_repository.py) | 300 | Scoped immutable tier policies, run choices, promotions and actual tool manifests; no model calls or I/O. |
| [agent/src/omni/adapters/persistence/schedule_repository.py](../../agent/src/omni/adapters/persistence/schedule_repository.py) | 434 | Immutable schedule definitions, durable due cursors and atomic occurrence admission. |
| [agent/src/omni/adapters/persistence/schema.py](../../agent/src/omni/adapters/persistence/schema.py) | 3277 | V2 relational schema. Repositories never issue DDL; migrations use frozen SQL snapshots. |
| [agent/src/omni/adapters/persistence/schema_upgrade.py](../../agent/src/omni/adapters/persistence/schema_upgrade.py) | 154 | Reviewed migration catalog and transactional upgrades only in a locked, fenced release target. |
| [agent/src/omni/adapters/persistence/search_schema.py](../../agent/src/omni/adapters/persistence/search_schema.py) | 8 | Derived FTS definitions. Applied only by versioned migrations. |
| [agent/src/omni/adapters/persistence/skill_repository.py](../../agent/src/omni/adapters/persistence/skill_repository.py) | 476 | Immutable skill versions, review state and per-run bindings; no package or filesystem access. |
| [agent/src/omni/adapters/persistence/sqlalchemy_access.py](../../agent/src/omni/adapters/persistence/sqlalchemy_access.py) | 27 | Shared SQLAlchemy SQLite transaction setup for the actor and exclusive offline migration. |
| [agent/src/omni/adapters/persistence/sqlite_access.py](../../agent/src/omni/adapters/persistence/sqlite_access.py) | 32 | Owned SQLite connections for offline inspection, backup and restore only. |
| [agent/src/omni/adapters/persistence/terminal_repository.py](../../agent/src/omni/adapters/persistence/terminal_repository.py) | 328 | Terminal intent transactions and immutable metadata receipts; never opens a native process or stores terminal bodies. |
| [agent/src/omni/adapters/persistence/terminal_review_repository.py](../../agent/src/omni/adapters/persistence/terminal_review_repository.py) | 238 | Append owner observations and erasable evidence; terminal receipts and native lifecycle remain unchanged. |
| [agent/src/omni/adapters/platform/__init__.py](../../agent/src/omni/adapters/platform/__init__.py) | 1 | Portable paths, ownership, credentials and platform capability implementations. |
| [agent/src/omni/adapters/platform/darwin_admin_commands.py](../../agent/src/omni/adapters/platform/darwin_admin_commands.py) | 276 | Single-threaded Darwin command ownership; pre-exec permit, kqueue no-fork proof and unreaped group anchor. |
| [agent/src/omni/adapters/platform/darwin_admin_files.py](../../agent/src/omni/adapters/platform/darwin_admin_files.py) | 184 | Darwin local-volume ACL and root file proofs; no application storage or arbitrary library lookup. |
| [agent/src/omni/adapters/platform/darwin_admin_peer.py](../../agent/src/omni/adapters/platform/darwin_admin_peer.py) | 30 | Kernel Unix peer UID/GID and PID; never accepts a TCP credential result or an empty native option. |
| [agent/src/omni/adapters/platform/darwin_admin_policy.py](../../agent/src/omni/adapters/platform/darwin_admin_policy.py) | 181 | Exact local-account Darwin administrator policy, protected root artifacts and shared audit bytes. |
| [agent/src/omni/adapters/platform/darwin_admin_probe.py](../../agent/src/omni/adapters/platform/darwin_admin_probe.py) | 154 | Bounded public CoreFoundation/ServiceManagement reads for a separately owned root worker; no mutation or GUI. |
| [agent/src/omni/adapters/platform/darwin_service_admin.py](../../agent/src/omni/adapters/platform/darwin_service_admin.py) | 147 | Darwin registration and single-start root backend; fixed launchctl operations, no GUI, database or force. |
| [agent/src/omni/adapters/platform/deployment_files.py](../../agent/src/omni/adapters/platform/deployment_files.py) | 631 | Capture installation identity and publish reviewed deployment bundles; never registers services or opens business storage. |
| [agent/src/omni/adapters/platform/desktop.py](../../agent/src/omni/adapters/platform/desktop.py) | 580 | Bounded, one-shot desktop metadata helpers; construction never probes a native desktop. |
| [agent/src/omni/adapters/platform/desktop_capture.py](../../agent/src/omni/adapters/platform/desktop_capture.py) | 83 | Host-owned screenshot staging. File decoding runs only on the desktop I/O worker. |
| [agent/src/omni/adapters/platform/desktop_native.py](../../agent/src/omni/adapters/platform/desktop_native.py) | 493 | Read-only desktop diagnostics and process/window identities; never reads UI contents or performs input. |
| [agent/src/omni/adapters/platform/event_loop.py](../../agent/src/omni/adapters/platform/event_loop.py) | 105 | Own the Windows TCP reset workaround without patching global asyncio classes. |
| [agent/src/omni/adapters/platform/exchange_deployments.py](../../agent/src/omni/adapters/platform/exchange_deployments.py) | 27 | Verify a reviewed source for removal or signed boot draining; never execute or import the source installation. |
| [agent/src/omni/adapters/platform/exchange_files.py](../../agent/src/omni/adapters/platform/exchange_files.py) | 278 | Private bounded exchange witnesses and maintenance reservations; no database, native calls or rollback. |
| [agent/src/omni/adapters/platform/linux_atspi.py](../../agent/src/omni/adapters/platform/linux_atspi.py) | 365 | Read-only AT-SPI windows in a proven logind session. No X11/Portal capture or input fallback. |
| [agent/src/omni/adapters/platform/linux_bus.py](../../agent/src/omni/adapters/platform/linux_bus.py) | 436 | Verified local D-Bus transport for desktop helpers; no environment-selected or network buses. |
| [agent/src/omni/adapters/platform/linux_fds.py](../../agent/src/omni/adapters/platform/linux_fds.py) | 95 | Owned received descriptors and bounded SCM_RIGHTS; no bus or desktop policy. |
| [agent/src/omni/adapters/platform/linux_input.py](../../agent/src/omni/adapters/platform/linux_input.py) | 184 | Verified Xorg input-change guard for addressed semantic actions; never grabs, synthesizes or stores input content. |
| [agent/src/omni/adapters/platform/linux_pipewire.py](../../agent/src/omni/adapters/platform/linux_pipewire.py) | 256 | One serial-targeted GStreamer PipeWire frame inside an owned helper, never the default remote or display. |
| [agent/src/omni/adapters/platform/linux_portal.py](../../agent/src/omni/adapters/platform/linux_portal.py) | 218 | Explicit ephemeral window selection on a pinned Wayland portal; never opens PipeWire FDs or dispatches input. |
| [agent/src/omni/adapters/platform/linux_semantic.py](../../agent/src/omni/adapters/platform/linux_semantic.py) | 235 | Addressed AT-SPI semantic proof and single mutation; no global input, focus acquisition or retry. |
| [agent/src/omni/adapters/platform/linux_session.py](../../agent/src/omni/adapters/platform/linux_session.py) | 348 | logind display-session and accessibility bus ownership; never trusts desktop environment variables. |
| [agent/src/omni/adapters/platform/linux_signals.py](../../agent/src/omni/adapters/platform/linux_signals.py) | 103 | Bounded, pinned D-Bus signals on the same pre-authenticated local connection; no service activation or FDs. |
| [agent/src/omni/adapters/platform/linux_x11.py](../../agent/src/omni/adapters/platform/linux_x11.py) | 248 | XRes-bound local X11 targets and existing Composite pixmaps; never captures the root or sends input. |
| [agent/src/omni/adapters/platform/linux_x11_socket.py](../../agent/src/omni/adapters/platform/linux_x11_socket.py) | 219 | Canonical local X11 sockets and private Xauthority; verify peers before sending credentials. |
| [agent/src/omni/adapters/platform/linux_x11_wire.py](../../agent/src/omni/adapters/platform/linux_x11_wire.py) | 242 | Small bounded X11 read protocol on an already verified socket; no discovery, input or reconnect. |
| [agent/src/omni/adapters/platform/macos_helper.py](../../agent/src/omni/adapters/platform/macos_helper.py) | 113 | Resolve the fixed, owner-installed Swift bundle; never build, install or request TCC permissions. |
| [agent/src/omni/adapters/platform/macos_service.py](../../agent/src/omni/adapters/platform/macos_service.py) | 109 | Inspect loaded launchd descriptions via the fixed Swift helper; never parses launchctl print as authority. |
| [agent/src/omni/adapters/platform/macos_service_state.py](../../agent/src/omni/adapters/platform/macos_service_state.py) | 101 | Pure bounded launchd definition and override comparison shared by owner and administrator ports. |
| [agent/src/omni/adapters/platform/paths.py](../../agent/src/omni/adapters/platform/paths.py) | 157 | Resolve writable instance paths independently from installed code and cwd. |
| [agent/src/omni/adapters/platform/permissions.py](../../agent/src/omni/adapters/platform/permissions.py) | 159 | Current-owner private files on POSIX and NTFS; never changes ancestor ACLs. |
| [agent/src/omni/adapters/platform/portal.py](../../agent/src/omni/adapters/platform/portal.py) | 506 | Owned long-lived Portal helper sessions with shared desktop capacity and bounded metadata pipes; no pixel or input dispatch. |
| [agent/src/omni/adapters/platform/processes.py](../../agent/src/omni/adapters/platform/processes.py) | 214 | Owned process groups and Windows Job Objects. Never terminate an unverified PID. |
| [agent/src/omni/adapters/platform/publication.py](../../agent/src/omni/adapters/platform/publication.py) | 70 | Atomic, exclusive directory publication on the supported hosts; never replaces an existing target. |
| [agent/src/omni/adapters/platform/release_files.py](../../agent/src/omni/adapters/platform/release_files.py) | 411 | Private release journals, installation bytes and startup fences; no database or native registration. |
| [agent/src/omni/adapters/platform/runtime_identity.py](../../agent/src/omni/adapters/platform/runtime_identity.py) | 237 | Publish private boot identity while the instance lock is held; never controls another process. |
| [agent/src/omni/adapters/platform/secrets.py](../../agent/src/omni/adapters/platform/secrets.py) | 159 | Explicit OS credential store or externally keyed encrypted vault; no plaintext fallback. |
| [agent/src/omni/adapters/platform/service_admin.py](../../agent/src/omni/adapters/platform/service_admin.py) | 364 | Fixed Linux system-service registration/start broker; no owner code execution, business DB, stop or shell. |
| [agent/src/omni/adapters/platform/service_admin_bundle.py](../../agent/src/omni/adapters/platform/service_admin_bundle.py) | 143 | Publish a reviewable stdlib-only administrator artifact; never installs it or elevates privileges. |
| [agent/src/omni/adapters/platform/service_admin_client.py](../../agent/src/omni/adapters/platform/service_admin_client.py) | 446 | Owner-side pinned root broker connection; no elevation, root code launch or automatic request retry. |
| [agent/src/omni/adapters/platform/service_admin_commands.py](../../agent/src/omni/adapters/platform/service_admin_commands.py) | 220 | Single-threaded root manager process ownership using pidfds and subreaping; never uses historical PIDs. |
| [agent/src/omni/adapters/platform/service_admin_policy.py](../../agent/src/omni/adapters/platform/service_admin_policy.py) | 329 | Root-only fixed Linux paths and append-only dispatch evidence; never opens owner business storage. |
| [agent/src/omni/adapters/platform/service_cleanup_files.py](../../agent/src/omni/adapters/platform/service_cleanup_files.py) | 259 | Separate cleanup journals and two-home reservations; preserves old unknown records, fences and business data. |
| [agent/src/omni/adapters/platform/service_control_files.py](../../agent/src/omni/adapters/platform/service_control_files.py) | 124 | Service lifecycle journal and once-only startup reservation; reuses registration audit and file-lock ownership. |
| [agent/src/omni/adapters/platform/service_handover.py](../../agent/src/omni/adapters/platform/service_handover.py) | 79 | Two pinned root phases, opened in order and owned until native lock release; never elevates or spawns a broker. |
| [agent/src/omni/adapters/platform/service_launch.py](../../agent/src/omni/adapters/platform/service_launch.py) | 88 | Once-only reviewed boot reservations under the instance lock; never starts a process or reads credentials. |
| [agent/src/omni/adapters/platform/service_operation_files.py](../../agent/src/omni/adapters/platform/service_operation_files.py) | 267 | Private offline maintenance journal; never migrates, replays or removes instance data. |
| [agent/src/omni/adapters/platform/service_query.py](../../agent/src/omni/adapters/platform/service_query.py) | 472 | Read-only OS service inspection with bounded output and retained process ownership. |
| [agent/src/omni/adapters/platform/service_readiness.py](../../agent/src/omni/adapters/platform/service_readiness.py) | 307 | Bounded, per-boot HMAC readiness proof. Never reads or sends an owner credential. |
| [agent/src/omni/adapters/platform/service_registration.py](../../agent/src/omni/adapters/platform/service_registration.py) | 420 | Native user-service registration with fixed commands and scoped macOS unload; no elevation or data deletion. |
| [agent/src/omni/adapters/platform/service_templates.py](../../agent/src/omni/adapters/platform/service_templates.py) | 219 | Deterministic service artifacts. Rendering never installs or controls a service. |
| [agent/src/omni/adapters/platform/systemd_state.py](../../agent/src/omni/adapters/platform/systemd_state.py) | 102 | Shared bounded systemd decoding; no command execution or filesystem access. |
| [agent/src/omni/adapters/platform/terminal.py](../../agent/src/omni/adapters/platform/terminal.py) | 588 | Owned terminal IPC. Native PTY libraries run only in the fixed helper process. |
| [agent/src/omni/adapters/platform/wheel_artifact.py](../../agent/src/omni/adapters/platform/wheel_artifact.py) | 61 | Read-only wheel artifact verification: hash, archive safety and V2 asset presence; never installs. |
| [agent/src/omni/adapters/platform/wheel_backup.py](../../agent/src/omni/adapters/platform/wheel_backup.py) | 68 | Execute only the backup step of a reviewed switch plan; no stop, switch, migration or rollback. |
| [agent/src/omni/adapters/platform/wheel_environment.py](../../agent/src/omni/adapters/platform/wheel_environment.py) | 136 | Create an offline candidate environment: a venv that installs the staged wheel without a network. |
| [agent/src/omni/adapters/platform/wheel_install.py](../../agent/src/omni/adapters/platform/wheel_install.py) | 366 | Execute the switch step: point the runtime at a prepared candidate; no stop, migrate or start. |
| [agent/src/omni/adapters/platform/wheel_prepare.py](../../agent/src/omni/adapters/platform/wheel_prepare.py) | 120 | Safely unpack one staged wheel into a candidate tree; never creates a venv, switches or migrates. |
| [agent/src/omni/adapters/platform/wheel_rollback.py](../../agent/src/omni/adapters/platform/wheel_rollback.py) | 71 | Rollback drill: restore the update backup into a new sibling directory and verify it; never touches live home. |
| [agent/src/omni/adapters/platform/wheel_smoke.py](../../agent/src/omni/adapters/platform/wheel_smoke.py) | 89 | Smoke-test a prepared candidate tree in a child interpreter; no install, switch or migration. |
| [agent/src/omni/adapters/platform/wheel_stage.py](../../agent/src/omni/adapters/platform/wheel_stage.py) | 76 | Atomic local staging of a verified wheel; never extracts, switches directories or runs migrations. |
| [agent/src/omni/adapters/platform/wheel_status.py](../../agent/src/omni/adapters/platform/wheel_status.py) | 113 | Read-only installation/update status projection; never mutates, plans, switches or migrates. |
| [agent/src/omni/adapters/platform/wheel_switch.py](../../agent/src/omni/adapters/platform/wheel_switch.py) | 85 | Read-only switch plan for a prepared candidate; never stops, switches, migrates or rolls back. |
| [agent/src/omni/adapters/platform/wheel_uninstall.py](../../agent/src/omni/adapters/platform/wheel_uninstall.py) | 139 | Remove one staged candidate or deactivate the runtime pointer; data and installation receipts stay. |
| [agent/src/omni/adapters/platform/workspaces.py](../../agent/src/omni/adapters/platform/workspaces.py) | 27 | Workspace admission keeps instance control, secrets and installed code private. |
| [agent/src/omni/adapters/primitives.py](../../agent/src/omni/adapters/primitives.py) | 20 | Real clocks and full UUID identities, injected by the composition root. |
| [agent/src/omni/adapters/process_diagnostics.py](../../agent/src/omni/adapters/process_diagnostics.py) | 112 | Process-owned logging and exception hooks; installed only by the executable host. |
| [agent/src/omni/adapters/skill_packages.py](../../agent/src/omni/adapters/skill_packages.py) | 127 | Bounded in-memory Markdown/ZIP parsing; never extracts files or executes package content. |
| [agent/src/omni/adapters/temporary_debug.py](../../agent/src/omni/adapters/temporary_debug.py) | 321 | Boot-bound, owner-enabled metadata capture; never opens business storage or records free text. |
| [agent/src/omni/adapters/timezones.py](../../agent/src/omni/adapters/timezones.py) | 29 | Pinned tzdata resolver; avoids OS-dependent timezone database drift. |
| [agent/src/omni/adapters/token_counter.py](../../agent/src/omni/adapters/token_counter.py) | 34 | Local conservative token estimate; it never fetches model metadata or credentials. |
| [agent/src/omni/adapters/vector_files.py](../../agent/src/omni/adapters/vector_files.py) | 130 | Verify explicit local vector files and startup selection; never imports a model or downloads data. |
| [agent/src/omni/adapters/workers/__init__.py](../../agent/src/omni/adapters/workers/__init__.py) | 1 | Owned capability workers; no application database or credentials access. |
| [agent/src/omni/adapters/workers/android.py](../../agent/src/omni/adapters/workers/android.py) | 406 | Fixed Android observation/input worker. No database, arbitrary shell or automatic device setup. |
| [agent/src/omni/adapters/workers/android_adb.py](../../agent/src/omni/adapters/workers/android_adb.py) | 127 | Bounded smart-socket ADB client. Never starts a server, pairs, installs, roots or accepts arbitrary commands. |
| [agent/src/omni/adapters/workers/android_unicode.py](../../agent/src/omni/adapters/workers/android_unicode.py) | 307 | Fixed IME transaction and authenticated localabstract IPC; no database or arbitrary shell. |
| [agent/src/omni/adapters/workers/bilibili.py](../../agent/src/omni/adapters/workers/bilibili.py) | 499 | One isolated Bilibili browser transaction; no database, arbitrary script, API writes or persisted cookies. |
| [agent/src/omni/adapters/workers/client.py](../../agent/src/omni/adapters/workers/client.py) | 358 | Versioned, bounded JSON IPC to a supervised capability process. |
| [agent/src/omni/adapters/workers/credentials.py](../../agent/src/omni/adapters/workers/credentials.py) | 76 | Fixed one-operation OS credential helper; no database, config lookup or plaintext fallback. |
| [agent/src/omni/adapters/workers/darwin_admin_probe.py](../../agent/src/omni/adapters/workers/darwin_admin_probe.py) | 45 | Root bundle's fixed read-only system probe. Never imported from an owner environment. |
| [agent/src/omni/adapters/workers/desktop.py](../../agent/src/omni/adapters/workers/desktop.py) | 132 | One bounded read-only desktop request per helper; stdout is IPC and never contains diagnostics. |
| [agent/src/omni/adapters/workers/linux_action.py](../../agent/src/omni/adapters/workers/linux_action.py) | 79 | One prepared Linux semantic action; the initial frame never permits a write, and no provider text is logged. |
| [agent/src/omni/adapters/workers/linux_desktop.py](../../agent/src/omni/adapters/workers/linux_desktop.py) | 261 | One Linux read-only request in the owned helper. No service activation, settings, input or database access. |
| [agent/src/omni/adapters/workers/local_vectors.py](../../agent/src/omni/adapters/workers/local_vectors.py) | 204 | CPU ONNX/tokenizers subprocess. No database, credentials, model downloads or arbitrary Python imports. |
| [agent/src/omni/adapters/workers/macos/Core/Package.swift](../../agent/src/omni/adapters/workers/macos/Core/Package.swift) | 12 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Core/Sources/ForegroundCore/Takeover.swift](../../agent/src/omni/adapters/workers/macos/Core/Sources/ForegroundCore/Takeover.swift) | 107 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Core/Tests/ForegroundCoreTests/TakeoverTests.swift](../../agent/src/omni/adapters/workers/macos/Core/Tests/ForegroundCoreTests/TakeoverTests.swift) | 107 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Package.swift](../../agent/src/omni/adapters/workers/macos/Package.swift) | 17 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/identity.c](../../agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/identity.c) | 129 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/include/NativeIdentity.h](../../agent/src/omni/adapters/workers/macos/Sources/NativeIdentity/include/NativeIdentity.h) | 11 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopFixture/Fixture.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopFixture/Fixture.swift) | 65 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Accessibility.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Accessibility.swift) | 202 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Actions.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Actions.swift) | 69 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Capture.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Capture.swift) | 52 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/FocusWatch.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/FocusWatch.swift) | 75 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Foreground.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Foreground.swift) | 190 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/HelperMain.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/HelperMain.swift) | 64 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Identity.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Identity.swift) | 92 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/InputWatch.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/InputWatch.swift) | 99 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Protocol.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/Protocol.swift) | 186 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/ServiceQuery.swift](../../agent/src/omni/adapters/workers/macos/Sources/OmniDesktopHelper/ServiceQuery.swift) | 139 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/macos/Tests/NativeIdentityTests/RegistrationPaths.swift](../../agent/src/omni/adapters/workers/macos/Tests/NativeIdentityTests/RegistrationPaths.swift) | 97 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/main.py](../../agent/src/omni/adapters/workers/main.py) | 285 | Capability subprocess. Requests carry scope; no database or Runtime is imported. |
| [agent/src/omni/adapters/workers/media.py](../../agent/src/omni/adapters/workers/media.py) | 367 | Bounded media worker: audio decode + local ASR and frame extraction in an owned subprocess. |
| [agent/src/omni/adapters/workers/portal.py](../../agent/src/omni/adapters/workers/portal.py) | 255 | Owned Wayland window-share helper with explicit one-frame requests; no database, restore token or input capability. |
| [agent/src/omni/adapters/workers/protocol.py](../../agent/src/omni/adapters/workers/protocol.py) | 169 | Strict, bounded IPC records shared by the host and capability worker; no dispatch or I/O. |
| [agent/src/omni/adapters/workers/service_admin_boot.py](../../agent/src/omni/adapters/workers/service_admin_boot.py) | 211 | Standalone administrator entry. Invoke a reviewed root-owned bundle with Python -I -S. |
| [agent/src/omni/adapters/workers/terminal.py](../../agent/src/omni/adapters/workers/terminal.py) | 720 | Fixed terminal host. Only this isolated, single-threaded startup may fork. |
| [agent/src/omni/adapters/workers/terminal_gate.py](../../agent/src/omni/adapters/workers/terminal_gate.py) | 104 | Trusted ConPTY gate: no user startup code before the parent owns its Job. |
| [agent/src/omni/adapters/workers/windows_foreground.cs](../../agent/src/omni/adapters/workers/windows_foreground.cs) | 250 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/windows_services.ps1](../../agent/src/omni/adapters/workers/windows_services.ps1) | 172 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/windows_uia.cs](../../agent/src/omni/adapters/workers/windows_uia.cs) | 348 | 原生源码、平台脚本或界面资源 |
| [agent/src/omni/adapters/workers/windows_uia.ps1](../../agent/src/omni/adapters/workers/windows_uia.ps1) | 78 | 原生源码、平台脚本或界面资源 |

文档：[docs/V2_REFACTOR.md](../../docs/V2_REFACTOR.md)

测试：[agent/tests/test_v2_kernel.py](../../agent/tests/test_v2_kernel.py)、[agent/tests/test_v2_reliability.py](../../agent/tests/test_v2_reliability.py)、[agent/tests/test_v2_diagnostics.py](../../agent/tests/test_v2_diagnostics.py)、[agent/tests/test_v2_workers.py](../../agent/tests/test_v2_workers.py)

直接静态依赖：`v2-application`、`v2-domain`

## V2 实例组装 · v2-composition

分类：内核与模型。独立新目录、生命周期和开发入口，不读取旧库。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/composition/__init__.py](../../agent/src/omni/composition/__init__.py) | 1 | V2 composition root. Constructors and lifecycle ownership are explicit here. |
| [agent/src/omni/composition/capabilities.py](../../agent/src/omni/composition/capabilities.py) | 166 | Explicit builtin capability registration; capabilities never construct the kernel. |
| [agent/src/omni/composition/cli.py](../../agent/src/omni/composition/cli.py) | 1008 | V2 development entrypoint. Online commands use the authenticated HTTP facade. |
| [agent/src/omni/composition/http.py](../../agent/src/omni/composition/http.py) | 238 | HTTP composition and owner bootstrap; tokens never appear in diagnostic output. |
| [agent/src/omni/composition/instance.py](../../agent/src/omni/composition/instance.py) | 799 | Build one isolated instance; never imports or opens the V1 runtime. |
| [agent/src/omni/composition/lifecycle.py](../../agent/src/omni/composition/lifecycle.py) | 166 | Own startup rollback and one shared shutdown budget; never dispatches business work. |
| [agent/src/omni/composition/releases.py](../../agent/src/omni/composition/releases.py) | 125 | Assemble explicit offline release commands. No service install, stop, model or default-entry switch. |
| [agent/src/omni/composition/service_cleanup.py](../../agent/src/omni/composition/service_cleanup.py) | 79 | Assemble quarantined registration cleanup, with an explicit root route for system definitions. |
| [agent/src/omni/composition/service_control.py](../../agent/src/omni/composition/service_control.py) | 69 | Assemble reviewed lifecycle commands, with no operation during import or plan inspection. |
| [agent/src/omni/composition/service_exchange.py](../../agent/src/omni/composition/service_exchange.py) | 186 | Assemble reviewed exchanges and their explicitly pinned root session; never elevates or switches default entrypoints. |
| [agent/src/omni/composition/service_operations.py](../../agent/src/omni/composition/service_operations.py) | 76 | Assemble one explicit offline service-registration command; production entry remains unchanged. |
| [agent/src/omni/composition/service_runner.py](../../agent/src/omni/composition/service_runner.py) | 67 | Start only a verified immutable deployment as its declared account; never registers a native service. |
| [agent/src/omni/composition/serving.py](../../agent/src/omni/composition/serving.py) | 68 | Compose the single-worker HTTP process for manual and pinned service startup. |

文档：[docs/V2_REFACTOR.md](../../docs/V2_REFACTOR.md)

测试：[agent/tests/test_v2_kernel.py](../../agent/tests/test_v2_kernel.py)

直接静态依赖：`v2-adapters`、`v2-application`、`v2-domain`

## V2 开发工作台 · v2-workbench

分类：Web 接口。基于现有主题的最小开发工作台，尚非完整功能迁移。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [web/ui/v2/action-reviews.js](../../web/ui/v2/action-reviews.js) | 424 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/android-unicode.js](../../web/ui/v2/android-unicode.js) | 158 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/android.js](../../web/ui/v2/android.js) | 403 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/bilibili.js](../../web/ui/v2/bilibili.js) | 295 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/capabilities.js](../../web/ui/v2/capabilities.js) | 57 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/chat-images.js](../../web/ui/v2/chat-images.js) | 175 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/configuration.js](../../web/ui/v2/configuration.js) | 544 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/credentials.js](../../web/ui/v2/credentials.js) | 182 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/deliveries.js](../../web/ui/v2/deliveries.js) | 367 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/desktop-actions.js](../../web/ui/v2/desktop-actions.js) | 148 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/desktop.js](../../web/ui/v2/desktop.js) | 568 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/documents.js](../../web/ui/v2/documents.js) | 487 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/extensions.js](../../web/ui/v2/extensions.js) | 656 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/index.html](../../web/ui/v2/index.html) | 1609 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/inspector.js](../../web/ui/v2/inspector.js) | 266 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/main.js](../../web/ui/v2/main.js) | 1690 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/maintenance.js](../../web/ui/v2/maintenance.js) | 126 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/mcp.js](../../web/ui/v2/mcp.js) | 362 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/media.js](../../web/ui/v2/media.js) | 365 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/memory-organization.js](../../web/ui/v2/memory-organization.js) | 465 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/memory-semantics.js](../../web/ui/v2/memory-semantics.js) | 252 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/model-output.js](../../web/ui/v2/model-output.js) | 30 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/models.js](../../web/ui/v2/models.js) | 487 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/operations.js](../../web/ui/v2/operations.js) | 352 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/people.js](../../web/ui/v2/people.js) | 319 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/qq.js](../../web/ui/v2/qq.js) | 181 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/routing.js](../../web/ui/v2/routing.js) | 185 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/schedules.js](../../web/ui/v2/schedules.js) | 374 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/settings-rail.js](../../web/ui/v2/settings-rail.js) | 84 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/skills.js](../../web/ui/v2/skills.js) | 460 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/terminal-reviews.js](../../web/ui/v2/terminal-reviews.js) | 254 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/terminals.js](../../web/ui/v2/terminals.js) | 564 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/tokens.js](../../web/ui/v2/tokens.js) | 81 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/v1/components.css](../../web/ui/v2/v1/components.css) | 655 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/v1/frame.css](../../web/ui/v2/v1/frame.css) | 932 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/v1/workbench.css](../../web/ui/v2/v1/workbench.css) | 4450 | 原生源码、平台脚本或界面资源 |
| [web/ui/v2/workbench.css](../../web/ui/v2/workbench.css) | 2733 | 原生源码、平台脚本或界面资源 |

文档：[docs/V2_REFACTOR.md](../../docs/V2_REFACTOR.md)

测试：[agent/tests/test_v2_http.py](../../agent/tests/test_v2_http.py)

直接静态依赖：无内部模块依赖

## 版本与升级事务 · updates

分类：平台与数据。Gitee 固定提交检查、源码预检、安装级维护、独立升级执行与本机恢复快照。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/update_probe.py](../../agent/src/omni/channels/update_probe.py) | 52 | 升级事务的数据副本迁移演练入口；不加载插件、不启动服务、不调用模型。 |
| [agent/src/omni/system/updates/__init__.py](../../agent/src/omni/system/updates/__init__.py) | 1 | 安装级版本检查与升级事务；不构造 Agent，不提供模型自更新工具。 |
| [agent/src/omni/system/updates/archive.py](../../agent/src/omni/system/updates/archive.py) | 99 | 源码包安全展开与发行清单；只处理受管源码，不读取用户配置或运行数据。 |
| [agent/src/omni/system/updates/common.py](../../agent/src/omni/system/updates/common.py) | 201 | 可随恢复执行器冻结的标准库协议、原子状态和路径约束；不导入运行中应用。 |
| [agent/src/omni/system/updates/gate.py](../../agent/src/omni/system/updates/gate.py) | 113 | 安装级维护闸门和运行入口租约；只管理协议文件，不构造或终止业务对象。 |
| [agent/src/omni/system/updates/gitee.py](../../agent/src/omni/system/updates/gitee.py) | 196 | 固定公开 Gitee 源的有界 HTTP 读取；不接受任意仓库、令牌或执行下载内容。 |
| [agent/src/omni/system/updates/manager.py](../../agent/src/omni/system/updates/manager.py) | 606 | 更新应用服务：缓存、预检和停写协调；实际替换交给独立冻结执行器。 |
| [agent/src/omni/system/updates/snapshot.py](../../agent/src/omni/system/updates/snapshot.py) | 94 | 停写后的升级恢复快照；保留本机原值，清单验证先于任何原位恢复。 |
| [agent/src/omni/system/updates/version.py](../../agent/src/omni/system/updates/version.py) | 104 | 源码身份与 SemVer 比较；不联网、不改变 Git 或安装环境。 |
| [agent/src/omni/system/updates/worker.py](../../agent/src/omni/system/updates/worker.py) | 314 | 冻结后以基础 Python 独立运行的升级/恢复执行器；只依赖标准库和冻结的平台原语。 |

文档：[docs/UPDATES.md](../../docs/UPDATES.md)

测试：[agent/tests/test_updates.py](../../agent/tests/test_updates.py)

直接静态依赖：`agent-directories`、`assembly`、`config`、`platform`

## 关于与更新 API · http-updates

分类：Web 接口。关于页版本、更新通道、人工安装确认与持久进度；沿用统一配对身份。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/updates.py](../../agent/src/omni/channels/web_api/updates.py) | 55 | 版本与更新 HTTP 适配；只消费 Runtime，统一鉴权由工作台负责。 |
| [web/ui/app/hub-about.js](../../web/ui/app/hub-about.js) | 187 | 原生源码、平台脚本或界面资源 |

文档：[docs/UPDATES.md](../../docs/UPDATES.md)

测试：[agent/tests/test_updates_api.py](../../agent/tests/test_updates_api.py)

直接静态依赖：`http-contracts`、`platform`、`updates`

## 项目存储与 Agent 目录 · agent-directories

分类：平台与数据。全局与独立空间目录解析、CAS 配置、运维路径解析与用途索引；不构造执行对象或搬移数据。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/system/agent_spaces/__init__.py](../../agent/src/omni/system/agent_spaces/__init__.py) | 22 | Agent 目录注册与纯路径解析；执行对象仍由 bootstrap 构造。 |
| [agent/src/omni/system/agent_spaces/catalog.py](../../agent/src/omni/system/agent_spaces/catalog.py) | 184 | 配置注册表：有界读取、CAS、跨进程互斥、原子写；不搬运用户数据。 |
| [agent/src/omni/system/agent_spaces/inspection.py](../../agent/src/omni/system/agent_spaces/inspection.py) | 58 | 只读运维入口共用的路径选择，不组装服务、不建库、不创建目录。 |
| [agent/src/omni/system/agent_spaces/inventory.py](../../agent/src/omni/system/agent_spaces/inventory.py) | 138 | 路径用途索引：纯声明，不扫描磁盘或读取记忆、凭据内容。 |
| [agent/src/omni/system/agent_spaces/layout.py](../../agent/src/omni/system/agent_spaces/layout.py) | 366 | 本机 Agent 布局的声明与校验，不创建目录或打开数据。 |

文档：[docs/STORAGE.md](../../docs/STORAGE.md)、[docs/AGENT_STORAGE.md](../../docs/AGENT_STORAGE.md)

测试：[agent/tests/test_agent_storage.py](../../agent/tests/test_agent_storage.py)、[agent/tests/test_storage_cli.py](../../agent/tests/test_storage_cli.py)、[agent/tests/test_application_storage.py](../../agent/tests/test_application_storage.py)

直接静态依赖：`config`、`platform`

## 项目目录与文件管理 · http-storage

分类：Web 接口。全局与 Agent 目录设置、运行与待重启路径、用途索引及当前空间公开文件区的有界浏览和人工上传。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py) | 250 | Agent 目录设置和当前公开文件区；只消费组装根注入的目录与路径策略。 |
| [web/storage/index.html](../../web/storage/index.html) | 195 | 原生源码、平台脚本或界面资源 |
| [web/storage/storage.css](../../web/storage/storage.css) | 490 | 原生源码、平台脚本或界面资源 |
| [web/storage/storage.js](../../web/storage/storage.js) | 493 | 原生源码、平台脚本或界面资源 |

文档：[docs/STORAGE.md](../../docs/STORAGE.md)、[docs/AGENT_STORAGE.md](../../docs/AGENT_STORAGE.md)

测试：[agent/tests/test_storage_settings.py](../../agent/tests/test_storage_settings.py)、[agent/tests/test_agent_storage_ui.py](../../agent/tests/test_agent_storage_ui.py)

直接静态依赖：`agent-directories`、`assembly`、`files`、`platform`、`toolkit`

## 上下文设置与统计 · http-context

分类：Web 接口。设置中心的只读窗口统计、供应商缓存样本和带版本检查的配置保存；不触发模型。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/context.py](../../agent/src/omni/channels/web_api/context.py) | 106 | 上下文设置与只读统计；不调用模型、不构造服务、不修改运行中的配置。 |
| [web/ui/app/hub-context.js](../../web/ui/app/hub-context.js) | 267 | 原生源码、平台脚本或界面资源 |

文档：[docs/CONTEXT_SETTINGS.md](../../docs/CONTEXT_SETTINGS.md)、[docs/PROMPT_CONTEXT.md](../../docs/PROMPT_CONTEXT.md)

测试：[agent/tests/test_context_settings.py](../../agent/tests/test_context_settings.py)

直接静态依赖：`assembly`、`config`、`platform`、`storage`

## 运行时组装 · assembly

分类：内核与模型。唯一组装根，构造依赖并暴露 Runtime 门面。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/bootstrap.py](../../agent/src/omni/bootstrap.py) | 941 | 组装根（Composition Root）：全项目唯一负责装配的地方。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/REPOSITORY_STRUCTURE.md](../../docs/REPOSITORY_STRUCTURE.md)

测试：[agent/tests/test_paths.py](../../agent/tests/test_paths.py)、[agent/tests/test_repository_layout.py](../../agent/tests/test_repository_layout.py)

直接静态依赖：`agent-directories`、`android`、`bilibili`、`browser`、`command-jobs`、`community`、`config`、`delegation`、`desktop`、`engine`、`files`、`mcp`、`models`、`personal`、`platform`、`plugins`、`qq-tools`、`safety`、`scheduling`、`shell`、`skills`、`storage`、`storyboard`、`system-tools`、`toolkit`、`updates`、`web-fetch`

## 配置与路径契约 · config

分类：平台与数据。配置模型、加载与 RuntimePaths；不包含业务编排。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/config.py](../../agent/src/omni/config.py) | 887 | 配置加载：.env 密钥 + config.yaml 主配置 + 运行期路径表。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_config.py](../../agent/tests/test_config.py)

直接静态依赖：无内部模块依赖

## 提示词与上下文 · prompt-context

分类：内核与模型。稳定系统前缀、回合上下文快照渲染与保留当前请求的摘要边界。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/core/compress.py](../../agent/src/omni/core/compress.py) | 328 | 上下文压缩 + 消息不变量（Hermes compression_facade 的最小版，NOTES §1.2）。 |
| [agent/src/omni/core/context.py](../../agent/src/omni/core/context.py) | 187 | 缓存友好的提示词布局：固定规则在前，每轮快照附于该轮用户正文末尾。 |

文档：[docs/PROMPT_CONTEXT.md](../../docs/PROMPT_CONTEXT.md)、[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_prompt_context.py](../../agent/tests/test_prompt_context.py)、[agent/tests/test_context.py](../../agent/tests/test_context.py)、[agent/tests/test_compress.py](../../agent/tests/test_compress.py)

直接静态依赖：`models`

## Agent 内核 · engine

分类：内核与模型。回合、上下文、工具循环、取消、委派约束、review 与调度。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/core/__init__.py](../../agent/src/omni/core/__init__.py) | 1 | Agent 核心：编排循环。工具注册表在 tools 包，审批在 safety 包。 |
| [agent/src/omni/core/agent.py](../../agent/src/omni/core/agent.py) | 1380 | Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。 |
| [agent/src/omni/core/budget_alert.py](../../agent/src/omni/core/budget_alert.py) | 102 | 费用额度的**阶梯告警**（P5-c，抄 surfcore `token-budget.js` 的判定逻辑）。 |
| [agent/src/omni/core/cron.py](../../agent/src/omni/core/cron.py) | 471 | 心跳调度器：tick 循环 + 至多一次派发 + 任务执行/投递（Hermes cron 最小版）。 |
| [agent/src/omni/core/evaluation.py](../../agent/src/omni/core/evaluation.py) | 36 | 固定样本检索评测；不把 mock 模型的排序表现当成真实语义收益。 |
| [agent/src/omni/core/journal.py](../../agent/src/omni/core/journal.py) | 424 | 记忆系统整理编排：日/周/月笔记的模型整理 worker（与 TurnEndReviewer 同构）。 |
| [agent/src/omni/core/modes.py](../../agent/src/omni/core/modes.py) | 206 | 按回合能力模式（chat / retrieval / agent）的声明与纯函数。 |
| [agent/src/omni/core/qq_policy.py](../../agent/src/omni/core/qq_policy.py) | 101 | 主动提醒策略：显式所有者、安静时段、独立每日额度，以及通道层冷却位；不做网络发送。 |
| [agent/src/omni/core/retrieval.py](../../agent/src/omni/core/retrieval.py) | 103 | FTS + 向量 RRF 融合；后台有界增量索引，网络请求不占数据库事务。 |
| [agent/src/omni/core/review.py](../../agent/src/omni/core/review.py) | 252 | 回合后异步 review：自进化最小闭环（Hermes background_review 的 P1 简化版， |
| [agent/src/omni/core/router.py](../../agent/src/omni/core/router.py) | 166 | 回合能力模式路由：先规则、后模型、最后回退。 |
| [agent/src/omni/core/run.py](../../agent/src/omni/core/run.py) | 285 | 运行（run）的身份、取消信号与上下文（D09）。 |
| [agent/src/omni/core/session.py](../../agent/src/omni/core/session.py) | 142 | 会话作用域：同一会话串行、不同会话并行、排队有界、跨进程有租约。 |
| [agent/src/omni/core/vision.py](../../agent/src/omni/core/vision.py) | 202 | 工具产物 → 模型图像块（D15/A13）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_agent.py](../../agent/tests/test_agent.py)、[agent/tests/test_runs.py](../../agent/tests/test_runs.py)、[agent/tests/test_parallel_tools.py](../../agent/tests/test_parallel_tools.py)

直接静态依赖：`config`、`models`、`prompt-context`、`qq-tools`、`safety`、`storage`、`toolkit`

## 模型网关与 embedding · models

分类：内核与模型。模型路由、能力、重试、流式、预算与 embedding 传输。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/llm/__init__.py](../../agent/src/omni/llm/__init__.py) | 1 | LLM 层：LiteLLM 统一网关。 |
| [agent/src/omni/llm/budget.py](../../agent/src/omni/llm/budget.py) | 216 | 费用预留与结算的契约（D10）。 |
| [agent/src/omni/llm/embedding.py](../../agent/src/omni/llm/embedding.py) | 135 | 可切换 embedding 出口：本地 Ollama / LiteLLM API；费用账本从 bootstrap 注入。 |
| [agent/src/omni/llm/errors.py](../../agent/src/omni/llm/errors.py) | 205 | 模型调用的错误分类与脱敏（D10）。 |
| [agent/src/omni/llm/gateway.py](../../agent/src/omni/llm/gateway.py) | 806 | LiteLLM 统一网关：一套 OpenAI 格式接所有供应商（海外/国产/本地 Ollama）。 |
| [agent/src/omni/llm/tiers.py](../../agent/src/omni/llm/tiers.py) | 179 | 费用额度的**口径化判定层**（P5-c，抄 surfcore `token-budget.js` 的判定逻辑）。 |
| [agent/src/omni/llm/usage.py](../../agent/src/omni/llm/usage.py) | 199 | 用量（tokens）与价格：把"这次调用大概/实际花了多少"变成可记账的数字。（D10） |

文档：[docs/RETRIEVAL.md](../../docs/RETRIEVAL.md)

测试：[agent/tests/test_gateway.py](../../agent/tests/test_gateway.py)、[agent/tests/test_usage.py](../../agent/tests/test_usage.py)

直接静态依赖：`config`

## 审批与权限 · safety

分类：内核与模型。风险分级、人工审批与无人值守拒绝。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/safety/__init__.py](../../agent/src/omni/safety/__init__.py) | 1 | 安全层：审批闸门（工具风险分级 + 按上下文选择审批策略）。 |
| [agent/src/omni/safety/activity.py](../../agent/src/omni/safety/activity.py) | 212 | 活跃时段统计与「建议安静时段」推导（抄 surfcore 的作息窗口**形态**，不是它的学习能力）。 |
| [agent/src/omni/safety/broker.py](../../agent/src/omni/safety/broker.py) | 117 | 审批代理：跨线程确认流（Web 工作台用）。 |
| [agent/src/omni/safety/cooldown.py](../../agent/src/omni/safety/cooldown.py) | 98 | 通道层冷却位：刚聊过就潜水，别急着主动搭话。 |
| [agent/src/omni/safety/gates.py](../../agent/src/omni/safety/gates.py) | 147 | 审批闸门：工具风险分级 + 按上下文选择审批策略（Strategy 模式）。 |
| [agent/src/omni/safety/proactive.py](../../agent/src/omni/safety/proactive.py) | 72 | 主动发言闸门：白名单 + 仅所有者 + 安静时段 + 每日额度上限。 |
| [agent/src/omni/safety/smart.py](../../agent/src/omni/safety/smart.py) | 78 | smart 审批档（Hermes approval_smart 同款）：辅助 LLM 先判风险，拿不准再问人。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_safety.py](../../agent/tests/test_safety.py)

直接静态依赖：无内部模块依赖

## 记忆与运行账本 · storage

分类：平台与数据。会话/事实/技能/产物及 state.db 事务、迁移、额度、投递。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/memory/__init__.py](../../agent/src/omni/memory/__init__.py) | 1 | 记忆层：SQLite 持久化（P0 版；P1 升级向量检索与三层记忆）。 |
| [agent/src/omni/memory/anchor.py](../../agent/src/omni/memory/anchor.py) | 163 | 沉淀锚定（P5-b）：后台写入的记忆必须能指回它依据的原文。 |
| [agent/src/omni/memory/artifacts.py](../../agent/src/omni/memory/artifacts.py) | 338 | 产物存储（D15 / A13）：截图、文件、报告统一按 artifact_id 管理。 |
| [agent/src/omni/memory/capacity.py](../../agent/src/omni/memory/capacity.py) | 184 | 记忆容量的**口径化档位判定**（抄 surfcore `src/memory-card.js` 的判定逻辑）。 |
| [agent/src/omni/memory/clarity.py](../../agent/src/omni/memory/clarity.py) | 268 | 事实记忆的清晰度阶梯（纯判定层，抄 surfcore `memory-card.js` 的思路）。 |
| [agent/src/omni/memory/command_jobs.py](../../agent/src/omni/memory/command_jobs.py) | 83 | 命令任务元数据仓储；不执行或终止操作系统进程。 |
| [agent/src/omni/memory/cron.py](../../agent/src/omni/memory/cron.py) | 62 | 定时任务模型与存储的公开入口（保留旧导入路径）。 |
| [agent/src/omni/memory/cron_store.py](../../agent/src/omni/memory/cron_store.py) | 105 | 定时任务域的两个门面（`JobStore` / `ExecutionLog`）。 |
| [agent/src/omni/memory/dialogue.py](../../agent/src/omni/memory/dialogue.py) | 399 | 对话全量记忆（M1）：完整对话文本一字不差的追加型存档。 |
| [agent/src/omni/memory/guard.py](../../agent/src/omni/memory/guard.py) | 136 | 记忆写入的确定性闸门（D12 / A15）。 |
| [agent/src/omni/memory/jobs.py](../../agent/src/omni/memory/jobs.py) | 139 | 定时任务模型与调度时间（纯数据 + 纯函数，不碰存储）。 |
| [agent/src/omni/memory/journal.py](../../agent/src/omni/memory/journal.py) | 416 | 笔记记忆（M3/M4/M5）：每日/每周/每月笔记的存储与到期判定。 |
| [agent/src/omni/memory/mcp_schemas.py](../../agent/src/omni/memory/mcp_schemas.py) | 81 | MCP 工具目录指纹仓储（D19，`state.db`）。 |
| [agent/src/omni/memory/outbox.py](../../agent/src/omni/memory/outbox.py) | 267 | QQ 投递 outbox 仓储（`state.db`）。 |
| [agent/src/omni/memory/owner.py](../../agent/src/omni/memory/owner.py) | 322 | 所有者记忆门面：档案行与常驻笔记统一进人物系统后的注入与维护面。 |
| [agent/src/omni/memory/owner_migrate.py](../../agent/src/omni/memory/owner_migrate.py) | 150 | 所有者记忆一次性迁移：profile 表与 MEMORY.md → owner 人物条目。 |
| [agent/src/omni/memory/people.py](../../agent/src/omni/memory/people.py) | 657 | 核心人物记忆（M6）：重要人物的账号、常驻档案、常驻记忆与临时事件。 |
| [agent/src/omni/memory/privacy.py](../../agent/src/omni/memory/privacy.py) | 79 | 记忆写入的隐私闸门（判定委托给共享原语 `system/text_guard.py`）。 |
| [agent/src/omni/memory/publications.py](../../agent/src/omni/memory/publications.py) | 134 | 人工发布草稿与发送身份账本；进程中断/结果未知不自动二次发布。 |
| [agent/src/omni/memory/qq.py](../../agent/src/omni/memory/qq.py) | 113 | QQ 入站去重、撤回墓碑与回复身份；只保存 ID/状态，不保存原文。 |
| [agent/src/omni/memory/resident.py](../../agent/src/omni/memory/resident.py) | 360 | 常驻小记忆（Hermes MEMORY.md 式，NOTES §1.3a）：跨会话冻结快照注入系统提示词。 |
| [agent/src/omni/memory/revisions.py](../../agent/src/omni/memory/revisions.py) | 235 | 内容修订历史（D08）：技能与常驻记忆的"可回滚快照"。 |
| [agent/src/omni/memory/runs.py](../../agent/src/omni/memory/runs.py) | 407 | 运行记录（run）与事件流（`state.db`）：一次"问—想—做—答"的可查账本。 |
| [agent/src/omni/memory/session_log.py](../../agent/src/omni/memory/session_log.py) | 418 | 会话全量记忆（M2）：对话 + 工具过程的完整流水（比 M1 多工具与系统边界事件）。 |
| [agent/src/omni/memory/skill_acks.py](../../agent/src/omni/memory/skill_acks.py) | 54 | 技能变更「已读」标记仓储（D18，`state.db`）。 |
| [agent/src/omni/memory/skill_inbox.py](../../agent/src/omni/memory/skill_inbox.py) | 531 | 外来技能包隔离安装（D18 后置项）：inbox 收包 → 隔离区校验 → 用户激活。 |
| [agent/src/omni/memory/skills.py](../../agent/src/omni/memory/skills.py) | 1040 | 技能存储（程序性记忆）：skills/<name>/SKILL.md，agentskills.io 兼容子集。 |
| [agent/src/omni/memory/state.py](../../agent/src/omni/memory/state.py) | 480 | 共享运行状态（`data/state.db`）：定时任务、执行账本、额度与迁移记录。 |
| [agent/src/omni/memory/state_errors.py](../../agent/src/omni/memory/state_errors.py) | 44 | state.db 的异常语义与错误翻译（唯一来源）。 |
| [agent/src/omni/memory/state_mixin_jobs.py](../../agent/src/omni/memory/state_mixin_jobs.py) | 259 | 定时任务域的 `state.db` 访问 mixin（任务定义 / revision / 原子领取）。 |
| [agent/src/omni/memory/state_mixin_leases.py](../../agent/src/omni/memory/state_mixin_leases.py) | 113 | 资源租约域的 `state.db` 访问 mixin（会话/浏览器 profile 等互斥资源）。 |
| [agent/src/omni/memory/state_mixin_ledger.py](../../agent/src/omni/memory/state_mixin_ledger.py) | 76 | 执行账本与写额度域的 `state.db` 访问 mixin。 |
| [agent/src/omni/memory/state_records.py](../../agent/src/omni/memory/state_records.py) | 56 | state.db 各域仓储返回值的数据形状（唯一来源）。 |
| [agent/src/omni/memory/store.py](../../agent/src/omni/memory/store.py) | 1310 | SQLite 记忆存储：会话 / 消息 / 事实 / 用户档案 / 全文检索 / review 日志。 |
| [agent/src/omni/memory/store_schema.py](../../agent/src/omni/memory/store_schema.py) | 165 | memory.db 的表结构、FTS 索引声明与额度常量（唯一来源）。 |
| [agent/src/omni/memory/system.py](../../agent/src/omni/memory/system.py) | 62 | 记忆系统门面：六个记忆模块对外的单点聚合（组合根纪律，无业务逻辑）。 |
| [agent/src/omni/memory/usage.py](../../agent/src/omni/memory/usage.py) | 363 | 模型调用的用量与费用账本（`state.db`，D10）。 |
| [agent/src/omni/memory/vectors.py](../../agent/src/omni/memory/vectors.py) | 111 | sqlite-vec 派生索引。与事实同库；遗忘/纠错触发器只用普通 SQL，无扩展依赖。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)、[docs/RETRIEVAL.md](../../docs/RETRIEVAL.md)

测试：[agent/tests/test_state.py](../../agent/tests/test_state.py)、[agent/tests/test_memory.py](../../agent/tests/test_memory.py)

直接静态依赖：`command-contracts`、`config`、`platform`

## 内置终端 · terminal

分类：交互与终端。ConPTY 后端、会话、会话池、WebSocket 四层；与 agent shell 工具分工。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/terminal/__init__.py](../../agent/src/omni/channels/terminal/__init__.py) | 7 | 工作台终端公共入口；平台适配、会话、会话池和 WS 路由分别维护。 |
| [agent/src/omni/channels/terminal/backend.py](../../agent/src/omni/channels/terminal/backend.py) | 40 | ConPTY 平台适配：检测可用性与创建进程；不管理会话池或 HTTP。 |
| [agent/src/omni/channels/terminal/managed.py](../../agent/src/omni/channels/terminal/managed.py) | 83 | 可恢复的人类终端：持续排空、有限输出环与单写者；不向模型暴露。 |
| [agent/src/omni/channels/terminal/pool.py](../../agent/src/omni/channels/terminal/pool.py) | 146 | 终端会话池：并发上限、关闭预算和残留报告。 |
| [agent/src/omni/channels/terminal/routes.py](../../agent/src/omni/channels/terminal/routes.py) | 79 | 内置终端 WebSocket 适配；复用工作台身份及注入的会话池。 |
| [agent/src/omni/channels/terminal/session.py](../../agent/src/omni/channels/terminal/session.py) | 55 | 单终端会话的读、写、窗口尺寸和退出确认。 |
| [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py) | 160 | 人工终端 v2：身份归属、结构化消息及只回放输出。旧 WS 独立兼容。 |
| [web/terminal/index.html](../../web/terminal/index.html) | 87 | 原生源码、平台脚本或界面资源 |
| [web/terminal/terminal.css](../../web/terminal/terminal.css) | 477 | 原生源码、平台脚本或界面资源 |
| [web/terminal/terminal.js](../../web/terminal/terminal.js) | 346 | 原生源码、平台脚本或界面资源 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[agent/tests/test_terminal.py](../../agent/tests/test_terminal.py)、[agent/tests/test_terminal_managed.py](../../agent/tests/test_terminal_managed.py)、[agent/tests/test_doctor_lifecycle.py](../../agent/tests/test_doctor_lifecycle.py)

直接静态依赖：`config`、`web-ui`

## 项目与能力工作台 · workbench

分类：交互与终端。项目索引、API、已注册工具和扩展状态的只读 HTTP 适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/workbench/__init__.py](../../agent/src/omni/channels/workbench/__init__.py) | 5 | 专题工作台路由；只消费注入的 Runtime，不在通道构造核心对象。 |
| [agent/src/omni/channels/workbench/routes.py](../../agent/src/omni/channels/workbench/routes.py) | 113 | 只读项目目录、接口与实时能力；由 web.py 的统一鉴权中间件保护。 |
| [web/project/index.html](../../web/project/index.html) | 26 | 原生源码、平台脚本或界面资源 |
| [web/project/panel.html](../../web/project/panel.html) | 49 | 原生源码、平台脚本或界面资源 |
| [web/project/project.css](../../web/project/project.css) | 486 | 原生源码、平台脚本或界面资源 |
| [web/project/project.js](../../web/project/project.js) | 450 | 原生源码、平台脚本或界面资源 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_project_catalog.py](../../agent/tests/test_project_catalog.py)

直接静态依赖：`assembly`、`catalog`、`config`

## 界面公共交互 · ui-shared

分类：交互与终端。统一导航、移动区域切换、有界请求、反馈和确认框；不执行工具或自动启动依赖。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [web/ui/app/api.js](../../web/ui/app/api.js) | 14 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/approvals.js](../../web/ui/app/approvals.js) | 65 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/artifacts.js](../../web/ui/app/artifacts.js) | 58 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/chat.js](../../web/ui/app/chat.js) | 198 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/desktop.js](../../web/ui/app/desktop.js) | 41 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/dom.js](../../web/ui/app/dom.js) | 45 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/header.js](../../web/ui/app/header.js) | 181 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-capabilities.js](../../web/ui/app/hub-capabilities.js) | 10 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-cron.js](../../web/ui/app/hub-cron.js) | 244 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-embodiment.js](../../web/ui/app/hub-embodiment.js) | 102 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-jobs.js](../../web/ui/app/hub-jobs.js) | 9 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-market.js](../../web/ui/app/hub-market.js) | 361 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-mcp-services.js](../../web/ui/app/hub-mcp-services.js) | 10 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-mcp.js](../../web/ui/app/hub-mcp.js) | 3 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-memory.js](../../web/ui/app/hub-memory.js) | 5 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-models.js](../../web/ui/app/hub-models.js) | 675 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-persona.js](../../web/ui/app/hub-persona.js) | 268 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-project.js](../../web/ui/app/hub-project.js) | 9 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-services.js](../../web/ui/app/hub-services.js) | 196 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-skills.js](../../web/ui/app/hub-skills.js) | 656 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub-usage.js](../../web/ui/app/hub-usage.js) | 63 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/hub.js](../../web/ui/app/hub.js) | 624 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/main.js](../../web/ui/app/main.js) | 136 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/memory.js](../../web/ui/app/memory.js) | 54 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/modals.js](../../web/ui/app/modals.js) | 30 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/publications.js](../../web/ui/app/publications.js) | 87 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/rail.js](../../web/ui/app/rail.js) | 39 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/runs.js](../../web/ui/app/runs.js) | 288 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/services.js](../../web/ui/app/services.js) | 133 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/sessions.js](../../web/ui/app/sessions.js) | 74 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/skills.js](../../web/ui/app/skills.js) | 250 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/state.js](../../web/ui/app/state.js) | 15 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/tabs.js](../../web/ui/app/tabs.js) | 58 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/tips.js](../../web/ui/app/tips.js) | 26 | 原生源码、平台脚本或界面资源 |
| [web/ui/app/tools.js](../../web/ui/app/tools.js) | 34 | 原生源码、平台脚本或界面资源 |
| [web/ui/base.css](../../web/ui/base.css) | 373 | 原生源码、平台脚本或界面资源 |
| [web/ui/components.css](../../web/ui/components.css) | 654 | 原生源码、平台脚本或界面资源 |
| [web/ui/dialog.js](../../web/ui/dialog.js) | 65 | 原生源码、平台脚本或界面资源 |
| [web/ui/embed-height.js](../../web/ui/embed-height.js) | 27 | 原生源码、平台脚本或界面资源 |
| [web/ui/frame.css](../../web/ui/frame.css) | 956 | 原生源码、平台脚本或界面资源 |
| [web/ui/icons.css](../../web/ui/icons.css) | 25 | 原生源码、平台脚本或界面资源 |
| [web/ui/markdown.js](../../web/ui/markdown.js) | 152 | 原生源码、平台脚本或界面资源 |
| [web/ui/sections.js](../../web/ui/sections.js) | 144 | 原生源码、平台脚本或界面资源 |
| [web/ui/shared.js](../../web/ui/shared.js) | 341 | 原生源码、平台脚本或界面资源 |
| [web/ui/terminal-theme.js](../../web/ui/terminal-theme.js) | 68 | 原生源码、平台脚本或界面资源 |
| [web/ui/theme.js](../../web/ui/theme.js) | 96 | 原生源码、平台脚本或界面资源 |
| [web/ui/tokens.css](../../web/ui/tokens.css) | 251 | 原生源码、平台脚本或界面资源 |
| [web/ui/workbench.css](../../web/ui/workbench.css) | 4443 | 原生源码、平台脚本或界面资源 |

文档：[docs/UI_QA.md](../../docs/UI_QA.md)

测试：[agent/tests/test_ui_flows.py](../../agent/tests/test_ui_flows.py)

直接静态依赖：无内部模块依赖

## Web 界面与身份 · web-ui

分类：交互与终端。工作台业务路由、统一鉴权与静态界面；新专题使用独立 APIRouter。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py) | 255 | Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。 |
| [agent/src/omni/channels/web_auth.py](../../agent/src/omni/channels/web_auth.py) | 340 | 工作台本地身份：配对码 → HttpOnly 会话 cookie，外加 Host/Origin/CSRF 校验。 |
| [web/index.html](../../web/index.html) | 1280 | 原生源码、平台脚本或界面资源 |
| [web/memory/index.html](../../web/memory/index.html) | 27 | 原生源码、平台脚本或界面资源 |
| [web/memory/memory.css](../../web/memory/memory.css) | 489 | 原生源码、平台脚本或界面资源 |
| [web/memory/memory.js](../../web/memory/memory.js) | 1060 | 原生源码、平台脚本或界面资源 |
| [web/memory/page.js](../../web/memory/page.js) | 2 | 原生源码、平台脚本或界面资源 |
| [web/memory/panel.html](../../web/memory/panel.html) | 330 | 原生源码、平台脚本或界面资源 |
| [web/vendor/xterm-addon-fit.js](../../web/vendor/xterm-addon-fit.js) | 2 | 原生源码、平台脚本或界面资源 |
| [web/vendor/xterm.js](../../web/vendor/xterm.js) | 6 | 原生源码、平台脚本或界面资源 |
| [web/vendor/xterm.min.css](../../web/vendor/xterm.min.css) | 8 | 原生源码、平台脚本或界面资源 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/UI_QA.md](../../docs/UI_QA.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)、[agent/tests/test_web_auth.py](../../agent/tests/test_web_auth.py)、[agent/tests/test_ui_flows.py](../../agent/tests/test_ui_flows.py)

直接静态依赖：`assembly`、`config`、`http-capabilities`、`http-contracts`、`http-routing`、`platform`、`safety`、`terminal`、`updates`、`workbench`

## 会话与待办 API · http-sessions

分类：Web 接口。会话选择、历史、状态和待办；消费注入 Runtime。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py) | 99 | 会话与待办 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)、[agent/tests/test_sessions.py](../../agent/tests/test_sessions.py)

直接静态依赖：`assembly`、`http-contracts`、`storage`

## 对话与流式 API · http-conversation

分类：Web 接口。普通/流式对话、显式运行入口；共用会话闸门、租约和取消。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py) | 194 | 对话与流式 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)、[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`engine`、`http-contracts`、`models`

## 状态库与命令账本声明 · command-contracts

分类：平台与数据。state.db 全部表结构与迁移脚本的唯一声明源；command_schema 仅保留旧 import 兼容。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/memory/command_schema.py](../../agent/src/omni/memory/command_schema.py) | 7 | 命令账本声明的旧 import 兼容层；真实声明见 `memory/state_schema.py`。 |
| [agent/src/omni/memory/state_schema.py](../../agent/src/omni/memory/state_schema.py) | 337 | `state.db` 的表结构与迁移声明唯一来源。 |

文档：[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[agent/tests/test_state.py](../../agent/tests/test_state.py)、[agent/tests/test_command_jobs.py](../../agent/tests/test_command_jobs.py)

直接静态依赖：`config`

## 后台命令任务 · command-jobs

分类：工具能力。审批后的独立命令任务、状态仓储、有限日志、会话隔离与取消；不复用人工 PTY。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/command_jobs.py](../../agent/src/omni/channels/web_api/command_jobs.py) | 31 | 后台命令管理适配；不提供绕过 Registry 审批的启动接口。 |
| [agent/src/omni/tools/shell/job_logs.py](../../agent/src/omni/tools/shell/job_logs.py) | 59 | 命令输出的有界 JSONL 环：序号续读、跨流串行、最近日志与缺口标记。 |
| [agent/src/omni/tools/shell/job_tools.py](../../agent/src/omni/tools/shell/job_tools.py) | 116 | 后台任务的 Registry 适配；不复用人工 PTY，不接受外部会话身份。 |
| [agent/src/omni/tools/shell/jobs.py](../../agent/src/omni/tools/shell/jobs.py) | 373 | 独立后台命令执行器：审批后入队、有界进程/日志、会话归属与诚实回收。 |
| [web/jobs/index.html](../../web/jobs/index.html) | 28 | 原生源码、平台脚本或界面资源 |
| [web/jobs/jobs.css](../../web/jobs/jobs.css) | 225 | 原生源码、平台脚本或界面资源 |
| [web/jobs/jobs.js](../../web/jobs/jobs.js) | 254 | 原生源码、平台脚本或界面资源 |
| [web/jobs/panel.html](../../web/jobs/panel.html) | 50 | 原生源码、平台脚本或界面资源 |

文档：[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[agent/tests/test_command_jobs.py](../../agent/tests/test_command_jobs.py)

直接静态依赖：`config`、`platform`、`shell`、`storage`、`toolkit`

## 运行与用量 API · http-runs

分类：Web 接口。运行查询、事件续读、协作取消和费用视图。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py) | 96 | 运行与用量 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`models`、`storage`

## 记忆与档案 API · http-memory

分类：Web 接口。事实、档案、常驻、review 和检索状态。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py) | 173 | 记忆与档案 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`platform`、`storage`

## 技能与审阅 API · http-skills

分类：Web 接口。技能编辑/修订/差异/回滚和待审阅状态，保留静态路由优先级。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py) | 484 | 技能 HTTP 适配：编辑/审阅/统计/归档/外来包，全部消费注入 Runtime。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)、[agent/tests/test_skills_history.py](../../agent/tests/test_skills_history.py)

直接静态依赖：`assembly`、`config`、`http-contracts`、`platform`、`storage`

## 产物 API · http-artifacts

分类：Web 接口。产物列表、指纹验证读取和清理，统一领域错误映射。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/artifacts.py](../../agent/src/omni/channels/web_api/artifacts.py) | 83 | 产物 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_artifacts.py](../../agent/tests/test_artifacts.py)

直接静态依赖：`assembly`、`storage`

## 审批 API · http-approvals

分类：Web 接口。待批准事项和人工决定；不构造或绕过审批器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/approvals.py](../../agent/src/omni/channels/web_api/approvals.py) | 31 | 人工审批 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_web_auth.py](../../agent/tests/test_web_auth.py)

直接静态依赖：`assembly`、`http-contracts`

## MCP 管理 API · http-mcp

分类：Web 接口。只读状态、显式发现、目录复核和重连。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py) | 164 | MCP 管理 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |
| [web/mcp/index.html](../../web/mcp/index.html) | 25 | 原生源码、平台脚本或界面资源 |
| [web/mcp/mcp.css](../../web/mcp/mcp.css) | 227 | 原生源码、平台脚本或界面资源 |
| [web/mcp/mcp.js](../../web/mcp/mcp.js) | 364 | 原生源码、平台脚本或界面资源 |
| [web/mcp/panel.html](../../web/mcp/panel.html) | 37 | 原生源码、平台脚本或界面资源 |

文档：[docs/MCP.md](../../docs/MCP.md)

测试：[agent/tests/test_mcp.py](../../agent/tests/test_mcp.py)

直接静态依赖：`assembly`、`http-contracts`、`mcp`

## 服务与诊断 API · http-operations

分类：Web 接口。系统概览、doctor、备份、服务启停和日志。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py) | 133 | 服务与诊断 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`platform`

## 调度与投递 API · http-automation

分类：Web 接口。任务创建/编辑/启停、历史和 outbox 人工处理。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py) | 191 | 调度与可靠投递 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_cron.py](../../agent/tests/test_cron.py)

直接静态依赖：`assembly`、`http-contracts`、`safety`、`storage`

## 发布草稿 API · http-publications

分类：Web 接口。草稿查询、乐观编辑和未知结果核查，不直接发布。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/publications.py](../../agent/src/omni/channels/web_api/publications.py) | 63 | 发布草稿 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[agent/tests/test_bilibili_publishing.py](../../agent/tests/test_bilibili_publishing.py)

直接静态依赖：`assembly`、`bilibili`、`storage`

## 能力与诊断面板 · http-capabilities

分类：Web 接口。被动诊断和能力汇总；显式探测分开执行，不自动启动依赖。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/capabilities.py](../../agent/src/omni/channels/web_api/capabilities.py) | 44 | 能力与诊断只读聚合：不连接 MCP、不访问手机、不启动终端/服务。 |
| [web/capabilities/capabilities.css](../../web/capabilities/capabilities.css) | 266 | 原生源码、平台脚本或界面资源 |
| [web/capabilities/capabilities.js](../../web/capabilities/capabilities.js) | 185 | 原生源码、平台脚本或界面资源 |
| [web/capabilities/index.html](../../web/capabilities/index.html) | 25 | 原生源码、平台脚本或界面资源 |
| [web/capabilities/panel.html](../../web/capabilities/panel.html) | 48 | 原生源码、平台脚本或界面资源 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_android_streams.py](../../agent/tests/test_android_streams.py)

直接静态依赖：无内部模块依赖

## 安卓设备 API 与面板 · http-android

分类：Web 接口。按设备固定目标、配对归属的观察与受控应用操作。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py) | 104 | 按设备寻址的人工安卓适配；归属取已验配对身份，动作仍需单次观察。 |
| [agent/src/omni/channels/web_api/android_stream.py](../../agent/src/omni/channels/web_api/android_stream.py) | 40 | MJPEG 适配：订阅共享帧，异步取消及时归还连接，不给预览帧授权令牌。 |
| [web/android/android.css](../../web/android/android.css) | 342 | 原生源码、平台脚本或界面资源 |
| [web/android/android.js](../../web/android/android.js) | 368 | 原生源码、平台脚本或界面资源 |
| [web/android/index.html](../../web/android/index.html) | 114 | 原生源码、平台脚本或界面资源 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)

测试：[agent/tests/test_android_devices.py](../../agent/tests/test_android_devices.py)

直接静态依赖：`web-ui`

## 桌面与安卓 API · http-devices

分类：Web 接口。窗口观察、设备树/锁帧、受观察约束的手动动作和 MJPEG。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py) | 141 | 桌面与安卓 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)、[docs/DESKTOP.md](../../docs/DESKTOP.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)、[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)

直接静态依赖：`assembly`、`http-android`、`http-contracts`

## HTTP 请求契约 · http-contracts

分类：Web 接口。公共请求模型；只承载数据声明，旧 web 模型名继续显式转发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py) | 240 | 工作台请求契约；路由共用，旧 web.py 继续显式转发公共模型名。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)

直接静态依赖：无内部模块依赖

## 市场 API（人设与插件） · http-market

分类：Web 接口。社区源配置、人设卡与插件索引代理，以及插件的安装/激活/启停/卸载；安装只落隔离区，不执行代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/market.py](../../agent/src/omni/channels/web_api/market.py) | 298 | 市场 HTTP 适配：人设卡与插件列表/安装/激活，全部消费注入 Runtime。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_market_api.py](../../agent/tests/test_market_api.py)、[agent/tests/test_market_client.py](../../agent/tests/test_market_client.py)

直接静态依赖：`assembly`、`community`、`updates`

## HTTP 路由装配 · http-routing

分类：Web 接口。聚合注入 Runtime 的领域路由，由应用入口统一挂载；不与请求契约合并为同一职责。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/__init__.py](../../agent/src/omni/channels/web_api/__init__.py) | 59 | 工作台领域路由聚合；应用生命周期与统一鉴权仍由 web.py 持有。 |
| [agent/src/omni/channels/web_api/embodiments.py](../../agent/src/omni/channels/web_api/embodiments.py) | 142 | 数字具身总览与B站账号状态：只读聚合，不代替各能力域自己的操作接口。 |
| [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py) | 287 | 记忆系统 HTTP 适配（M1–M6）：只消费注入 Runtime，路由聚合进既有鉴权闸门。 |
| [agent/src/omni/channels/web_api/settings.py](../../agent/src/omni/channels/web_api/settings.py) | 422 | 配对与设置HTTP适配；复用注入身份和Runtime，不承担应用生命周期。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)、[docs/REFACTORING.md](../../docs/REFACTORING.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)

直接静态依赖：`assembly`、`bilibili`、`command-jobs`、`config`、`engine`、`http-android`、`http-approvals`、`http-artifacts`、`http-automation`、`http-context`、`http-contracts`、`http-conversation`、`http-devices`、`http-market`、`http-mcp`、`http-memory`、`http-operations`、`http-publications`、`http-runs`、`http-sessions`、`http-skills`、`http-storage`、`http-updates`、`models`、`platform`、`safety`、`storage`、`web-ui`

## CLI 入口 · cli

分类：交互与终端。终端对话命令与 Runtime 适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/__main__.py](../../agent/src/omni/__main__.py) | 5 | python -m omni 入口。 |
| [agent/src/omni/channels/cli.py](../../agent/src/omni/channels/cli.py) | 147 | 本地终端 REPL 入口：uv run omni。 |

文档：[docs/HANDOFF.md](../../docs/HANDOFF.md)

测试：关联模块集成验证

直接静态依赖：`assembly`、`config`、`models`、`safety`

## QQ 入口 · qq-channel

分类：交互与终端。QQ 场景队列、事件去重和消息收发适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/qq.py](../../agent/src/omni/channels/qq.py) | 367 | QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。 |

文档：[docs/QQ_SCENARIOS.md](../../docs/QQ_SCENARIOS.md)

测试：[agent/tests/test_qq.py](../../agent/tests/test_qq.py)、[agent/tests/test_qq_scope.py](../../agent/tests/test_qq_scope.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`models`、`platform`、`qq-tools`、`safety`

## 心跳入口 · heartbeat

分类：交互与终端。定时领取、执行、投递与周期清理。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/heartbeat.py](../../agent/src/omni/channels/heartbeat.py) | 117 | 心跳入口：uv run omni-heartbeat（定时任务常驻调度，无人值守 fail-closed）。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_cron.py](../../agent/tests/test_cron.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`platform`、`safety`、`storage`

## 入口公共适配 · channels

分类：交互与终端。入口生命周期和只读诊断/检索评测命令。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/__init__.py](../../agent/src/omni/channels/__init__.py) | 1 | 入口层：QQ(NapCat)、CLI、Web（P0 只有 CLI）。 |
| [agent/src/omni/channels/doctor.py](../../agent/src/omni/channels/doctor.py) | 56 | 只读 doctor 命令；不组装运行态，以免诊断触发建库、迁移或后台任务。 |
| [agent/src/omni/channels/eval_retrieval.py](../../agent/src/omni/channels/eval_retrieval.py) | 80 | 在临时数据库评测合成样本；hybrid 模式须显式传配置，默认完全离线。 |
| [agent/src/omni/channels/lifecycle.py](../../agent/src/omni/channels/lifecycle.py) | 28 | 常驻入口响应本次启动的停止请求；不构造运行态、不自行终止其它进程。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：关联模块集成验证

直接静态依赖：`agent-directories`、`assembly`、`config`、`engine`、`platform`

## 个人记忆工具 · personal

分类：工具能力。时钟、事实、档案、常驻、todo、摘要与手动整理各自独立。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/personal/__init__.py](../../agent/src/omni/tools/personal/__init__.py) | 60 | 个人内置能力的注册入口：事实、档案、常驻、待办、摘要和整理。 |
| [agent/src/omni/tools/personal/clock.py](../../agent/src/omni/tools/personal/clock.py) | 25 | 个人能力：clock；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/digest.py](../../agent/src/omni/tools/personal/digest.py) | 68 | 个人能力：digest；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/facts.py](../../agent/src/omni/tools/personal/facts.py) | 259 | 个人能力：facts；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/journal.py](../../agent/src/omni/tools/personal/journal.py) | 64 | 记忆系统工具（二）：日/周/月笔记的读取（M3/M4/M5）。 |
| [agent/src/omni/tools/personal/people.py](../../agent/src/omni/tools/personal/people.py) | 247 | 记忆系统工具（三）：核心人物记忆（M6）——查询、记住与临时事件。 |
| [agent/src/omni/tools/personal/profile.py](../../agent/src/omni/tools/personal/profile.py) | 133 | 个人能力：profile；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/refine.py](../../agent/src/omni/tools/personal/refine.py) | 57 | 个人能力：refine；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/resident.py](../../agent/src/omni/tools/personal/resident.py) | 80 | 个人能力：resident；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/todos.py](../../agent/src/omni/tools/personal/todos.py) | 85 | 个人能力：todos；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/transcript.py](../../agent/src/omni/tools/personal/transcript.py) | 207 | 记忆系统工具（一）：全量记忆两个模块的检索与精确读取（M1/M2）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_tools.py](../../agent/tests/test_tools.py)、[agent/tests/test_profile_tools.py](../../agent/tests/test_profile_tools.py)、[agent/tests/test_resident.py](../../agent/tests/test_resident.py)

直接静态依赖：`storage`、`toolkit`

## 技能工具 · skills

分类：插件与扩展。技能查询/创建/编辑/回滚与外来技能隔离安装入口。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/skills/__init__.py](../../agent/src/omni/tools/skills/__init__.py) | 5 | skills 能力域注册入口。 |
| [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py) | 452 | 技能工具集（toolset=core）：技能的渐进披露与维护。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_skills.py](../../agent/tests/test_skills.py)、[agent/tests/test_skill_inbox.py](../../agent/tests/test_skill_inbox.py)

直接静态依赖：`storage`、`toolkit`

## 调度工具 · scheduling

分类：工具能力。周期/一次性任务、编辑、依赖和执行查询。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/scheduling/__init__.py](../../agent/src/omni/tools/scheduling/__init__.py) | 5 | scheduling 能力域注册入口。 |
| [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py) | 314 | 定时任务工具集（toolset=cron）：agent 可自主排程（Hermes cronjob_tools 精神）。 |

文档：[docs/HANDOFF.md](../../docs/HANDOFF.md)

测试：[agent/tests/test_cron.py](../../agent/tests/test_cron.py)

直接静态依赖：`safety`、`storage`、`toolkit`

## 委派工具 · delegation

分类：工具能力。受父预算/权限/取消约束的子任务注册入口。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/delegation/__init__.py](../../agent/src/omni/tools/delegation/__init__.py) | 5 | delegation 能力域注册入口。 |
| [agent/src/omni/tools/delegation/tools.py](../../agent/src/omni/tools/delegation/tools.py) | 122 | 子任务委派（Hermes delegate_task 同款）：把独立子任务交给一个干净会话去跑。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_delegate.py](../../agent/tests/test_delegate.py)

直接静态依赖：`storage`、`toolkit`

## 系统服务工具 · system-tools

分类：工具能力。状态查询和受审批的服务操作适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/system/__init__.py](../../agent/src/omni/tools/system/__init__.py) | 5 | system 能力域注册入口。 |
| [agent/src/omni/tools/system/tools.py](../../agent/src/omni/tools/system/tools.py) | 121 | 系统工具集（toolset=system）：AI 自查本地服务状态、引导用户处置。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_system.py](../../agent/tests/test_system.py)

直接静态依赖：`platform`、`toolkit`

## Agent shell 工具 · shell

分类：工具能力。只读命令语法、执行器、硬黑名单与人工确认命令；不复用交互式 PTY。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/shell/__init__.py](../../agent/src/omni/tools/shell/__init__.py) | 37 | shell 能力域：SAFE 观察（结构化 argv）与 CONFIRM 执行（shell 语义）。 |
| [agent/src/omni/tools/shell/danger.py](../../agent/src/omni/tools/shell/danger.py) | 46 | 硬性安全黑名单：灾难级操作命中即拒——即便用户已确认也不执行。 |
| [agent/src/omni/tools/shell/process.py](../../agent/src/omni/tools/shell/process.py) | 182 | 进程边界：argv 执行、输出上限、超时后的进程树回收与"副作用是否已知"。 |
| [agent/src/omni/tools/shell/spec.py](../../agent/src/omni/tools/shell/spec.py) | 539 | SAFE 命令解析：把命令串解析成**受控 argv**，解析不出来就拒绝。 |
| [agent/src/omni/tools/shell/tools.py](../../agent/src/omni/tools/shell/tools.py) | 264 | shell 工具集（toolset=shell）：peek_command（SAFE 观察）与 run_command（CONFIRM 执行）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_shell_tools.py](../../agent/tests/test_shell_tools.py)

直接静态依赖：`config`、`toolkit`

## 文件工具 · files

分类：工具能力。允许根内的列表/搜索/读取/差异与受控写入回滚。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/files/__init__.py](../../agent/src/omni/tools/files/__init__.py) | 20 | 结构化文件工具域（D13）：工作区内的有界读写、差异预览与回滚。 |
| [agent/src/omni/tools/files/history.py](../../agent/src/omni/tools/files/history.py) | 158 | 工作区文件的修订快照（D13）：写前备份、可回滚。 |
| [agent/src/omni/tools/files/io.py](../../agent/src/omni/tools/files/io.py) | 314 | 文本读写的有界实现（D13）：编码识别、二进制拒绝、窗口读取、原子替换。 |
| [agent/src/omni/tools/files/paths.py](../../agent/src/omni/tools/files/paths.py) | 280 | 工作区路径边界（D13）：规范化、根校验、敏感项与"检查后不变"。 |
| [agent/src/omni/tools/files/search.py](../../agent/src/omni/tools/files/search.py) | 169 | 有界目录遍历与内容搜索（D13）。 |
| [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py) | 674 | 结构化文件工具（D13，toolset=files）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_files.py](../../agent/tests/test_files.py)

直接静态依赖：`platform`、`toolkit`

## 网页读取工具 · web-fetch

分类：工具能力。公开网页的目标网络、重定向、体积限制与正文提取。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/web/__init__.py](../../agent/src/omni/tools/web/__init__.py) | 20 | 通用网页读取（D14）：有界 HTTP 抓取 + 正文提取。 |
| [agent/src/omni/tools/web/extract.py](../../agent/src/omni/tools/web/extract.py) | 248 | 正文提取（D14）：把 HTML 变成"能读的文本"，不执行任何东西。 |
| [agent/src/omni/tools/web/net.py](../../agent/src/omni/tools/web/net.py) | 413 | 有界 HTTP 抓取（D14）：协议、目标网络、重定向、大小、超时全部受控。 |
| [agent/src/omni/tools/web/tools.py](../../agent/src/omni/tools/web/tools.py) | 195 | 网页读取工具（D14，toolset=web）：`web_read`。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_web_fetch.py](../../agent/tests/test_web_fetch.py)

直接静态依赖：`toolkit`

## 浏览器工具 · browser

分类：工具能力。Playwright 所有权、表单协议、观察与写预算。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/browser/__init__.py](../../agent/src/omni/tools/browser/__init__.py) | 5 | 浏览器能力域（P3）：Playwright 驱动 + 写预算 + 工具。 |
| [agent/src/omni/tools/browser/budget.py](../../agent/src/omni/tools/browser/budget.py) | 68 | 每日写操作预算：写走 UI 的第一道闸（bili-whale 的 quota 精神，xuexi/NOTES.md §4.3）。 |
| [agent/src/omni/tools/browser/driver.py](../../agent/src/omni/tools/browser/driver.py) | 431 | Playwright 浏览器驱动：单一所有者线程 + profile 跨进程租约 + 拟人节奏。 |
| [agent/src/omni/tools/browser/forms.py](../../agent/src/omni/tools/browser/forms.py) | 273 | 固定配置的 UI 表单观察/提交；所有操作在 BrowserDriver 的所有者线程执行。 |
| [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py) | 199 | 浏览器工具集（toolset=browser）：读 SAFE，写 CONFIRM。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[agent/tests/test_browser.py](../../agent/tests/test_browser.py)、[agent/tests/test_browser_owner.py](../../agent/tests/test_browser_owner.py)

直接静态依赖：`config`、`storage`、`toolkit`

## 安卓工具 · android

分类：工具能力。ADB、防护、UI 树、观察令牌、锁帧和输入法。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/android/__init__.py](../../agent/src/omni/tools/android/__init__.py) | 7 | 安卓能力域（P4 地基）：纯 ADB 协议层 + 高层语义驱动 + 工具。 |
| [agent/src/omni/tools/android/adb.py](../../agent/src/omni/tools/android/adb.py) | 243 | 纯 ADB 协议层：设备无关的 adb 命令封装（不知道 LLM 与语义）。 |
| [agent/src/omni/tools/android/driver.py](../../agent/src/omni/tools/android/driver.py) | 450 | Android 观察令牌、单设备互斥、一次动作与回读。磁盘缓存仅用于展示。 |
| [agent/src/omni/tools/android/process.py](../../agent/src/omni/tools/android/process.py) | 48 | ADB 客户端的有界读取与超时；不把客户端退出当作设备操作已撤销。 |
| [agent/src/omni/tools/android/stream.py](../../agent/src/omni/tools/android/stream.py) | 201 | 按设备共享最新 JPEG；预览不产生观察授权，慢客户端跳过旧帧。 |
| [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py) | 317 | 安卓工具集（toolset=android）：观察 SAFE，动作 CONFIRM。 |
| [agent/src/omni/tools/android/ui.py](../../agent/src/omni/tools/android/ui.py) | 90 | UI 树解析（纯函数）：uiautomator XML → 结构化元素 → 文本树。 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)

测试：[agent/tests/test_android.py](../../agent/tests/test_android.py)、[agent/tests/test_android_observations.py](../../agent/tests/test_android_observations.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 桌面工具 · desktop

分类：工具能力。Windows UIAutomation、窗口观察、租约与受控语义动作。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/desktop/__init__.py](../../agent/src/omni/tools/desktop/__init__.py) | 1 | Windows 桌面域：观察绑定、语义后台动作、写后回读。 |
| [agent/src/omni/tools/desktop/driver.py](../../agent/src/omni/tools/desktop/driver.py) | 190 |  |
| [agent/src/omni/tools/desktop/native.py](../../agent/src/omni/tools/desktop/native.py) | 50 | 静态 UIAutomation 辅助程序。用户参数只经 stdin JSON 传递，不拼接 PowerShell。 |
| [agent/src/omni/tools/desktop/tools.py](../../agent/src/omni/tools/desktop/tools.py) | 115 |  |
| [agent/src/omni/tools/desktop/uia.ps1](../../agent/src/omni/tools/desktop/uia.ps1) | 198 | 原生源码、平台脚本或界面资源 |

文档：[docs/DESKTOP.md](../../docs/DESKTOP.md)

测试：[agent/tests/test_desktop.py](../../agent/tests/test_desktop.py)

直接静态依赖：`config`、`toolkit`

## B站工具 · bilibili

分类：工具能力。读 API/转写及注入浏览器协议的草稿与受控写。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/bilibili/__init__.py](../../agent/src/omni/tools/bilibili/__init__.py) | 1 | B站能力域（P1：只读）。api 层不知道 LLM，tools 层不知道 HTTP 细节。 |
| [agent/src/omni/tools/bilibili/api.py](../../agent/src/omni/tools/bilibili/api.py) | 414 | B站读 API 封装：只读端点 + 目标归一化 + WBI 签名。 |
| [agent/src/omni/tools/bilibili/asr.py](../../agent/src/omni/tools/bilibili/asr.py) | 183 | 无字幕视频的 ASR 兜底（faster-whisper，本地转写）。 |
| [agent/src/omni/tools/bilibili/frames.py](../../agent/src/omni/tools/bilibili/frames.py) | 135 | B站视频抽帧并拼成一张联络表（contact sheet）。 |
| [agent/src/omni/tools/bilibili/publishing.py](../../agent/src/omni/tools/bilibili/publishing.py) | 269 | B站草稿→人工批准→一次 UI 提交→记录回读；浏览器能力通过协议注入。 |
| [agent/src/omni/tools/bilibili/tools.py](../../agent/src/omni/tools/bilibili/tools.py) | 379 | B站读工具集（toolset=bilibili，全部 SAFE 只读）。 |
| [agent/src/omni/tools/bilibili/video_source.py](../../agent/src/omni/tools/bilibili/video_source.py) | 65 | B站取流适配器：实现分镜域的 VideoSource 协议。 |
| [agent/src/omni/tools/bilibili/wbi.py](../../agent/src/omni/tools/bilibili/wbi.py) | 99 | B站 WBI 签名（纯算法，无网络依赖）。 |
| [agent/src/omni/tools/bilibili/write_tools.py](../../agent/src/omni/tools/bilibili/write_tools.py) | 114 | B站草稿工具及逐次人工发布审批；所有浏览器依赖由组装根注入。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[agent/tests/test_bilibili_tools.py](../../agent/tests/test_bilibili_tools.py)、[agent/tests/test_bilibili_publishing.py](../../agent/tests/test_bilibili_publishing.py)

直接静态依赖：`config`、`storage`、`toolkit`

## 视频理解与分镜 · storyboard

分类：工具能力。切点检测、局部剪辑率格位分配、PyAV 取最锐帧、宫格渲染与可寻址索引；取流能力由平台适配器注入。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/storyboard/__init__.py](../../agent/src/omni/tools/storyboard/__init__.py) | 48 | 视频理解能力域：把一段视频变成模型能"引用"的画面索引。 |
| [agent/src/omni/tools/storyboard/decode.py](../../agent/src/omni/tools/storyboard/decode.py) | 409 | 用 PyAV 解视频帧（不依赖 ffmpeg 可执行文件）。 |
| [agent/src/omni/tools/storyboard/detect.py](../../agent/src/omni/tools/storyboard/detect.py) | 428 | 场景切点检测：把一段视频切成若干镜头。 |
| [agent/src/omni/tools/storyboard/render.py](../../agent/src/omni/tools/storyboard/render.py) | 260 | 宫格渲染：把镜头帧拼成一张模型能看、也能引用的图。 |
| [agent/src/omni/tools/storyboard/segments.py](../../agent/src/omni/tools/storyboard/segments.py) | 337 | 镜头分段与可寻址索引。 |
| [agent/src/omni/tools/storyboard/source.py](../../agent/src/omni/tools/storyboard/source.py) | 45 | 视频来源：把"一个目标"变成"一个本地可解码的视频文件 + 可选的带时间戳台词"。 |
| [agent/src/omni/tools/storyboard/tools.py](../../agent/src/omni/tools/storyboard/tools.py) | 254 | 分镜工具：把视频变成"能看、能引用、能回看"的画面索引。 |

文档：[docs/VIDEO_UNDERSTANDING.md](../../docs/VIDEO_UNDERSTANDING.md)

测试：[agent/tests/test_storyboard.py](../../agent/tests/test_storyboard.py)

直接静态依赖：`toolkit`

## QQ 工具 · qq-tools

分类：工具能力。OneBot 客户端与白名单内主动消息。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/qq/__init__.py](../../agent/src/omni/tools/qq/__init__.py) | 4 | QQ 能力域（P2）：OneBot v11 客户端（NapCat）与主动消息工具。 |
| [agent/src/omni/tools/qq/client.py](../../agent/src/omni/tools/qq/client.py) | 299 | OneBot v11 正向 WebSocket 客户端（对接 NapCat）。 |
| [agent/src/omni/tools/qq/tools.py](../../agent/src/omni/tools/qq/tools.py) | 123 | QQ 工具集（toolset=qq）：主动发消息。 |

文档：[docs/QQ_SCENARIOS.md](../../docs/QQ_SCENARIOS.md)

测试：[agent/tests/test_outbox.py](../../agent/tests/test_outbox.py)

直接静态依赖：`safety`、`toolkit`

## MCP 扩展适配 · mcp

分类：插件与扩展。外部 stdio 工具发现、授权、漂移撤权与子进程生命周期。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/mcp/__init__.py](../../agent/src/omni/tools/mcp/__init__.py) | 1 | 外部 MCP 能力域：本地 stdio 客户端与工具适配；不依赖 LLM 或内核。 |
| [agent/src/omni/tools/mcp/client.py](../../agent/src/omni/tools/mcp/client.py) | 733 | 同步 agent 到异步 MCP 的桥接：每服务一个会话所有者线程。 |
| [agent/src/omni/tools/mcp/remote.py](../../agent/src/omni/tools/mcp/remote.py) | 178 | 显式配置的远程MCP传输与人工OAuth；不扩大工具授权，不自动打开浏览器。 |
| [agent/src/omni/tools/mcp/tools.py](../../agent/src/omni/tools/mcp/tools.py) | 107 | MCP 工具适配：渐进发现、用户审核只读调用、人工确认调用；不决定传输细节。 |

文档：[docs/MCP.md](../../docs/MCP.md)

测试：[agent/tests/test_mcp.py](../../agent/tests/test_mcp.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 工具公共协议与兼容层 · toolkit

分类：工具能力。Registry、ToolResult、SessionRef/RunRef、路径策略、截图协议及旧 import 薄转发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/__init__.py](../../agent/src/omni/tools/__init__.py) | 8 | 工具层：registry（注册表）+ builtin（内置工具集）+ 后续各能力域包。 |
| [agent/src/omni/tools/builtin.py](../../agent/src/omni/tools/builtin.py) | 23 | 旧导入兼容层；新增个人工具请进入 tools/personal/。 |
| [agent/src/omni/tools/cron_tools.py](../../agent/src/omni/tools/cron_tools.py) | 5 | 旧导入兼容层；实现位于 tools/scheduling/。 |
| [agent/src/omni/tools/delegate_tools.py](../../agent/src/omni/tools/delegate_tools.py) | 5 | 旧导入兼容层；实现位于 tools/delegation/。 |
| [agent/src/omni/tools/pathpolicy.py](../../agent/src/omni/tools/pathpolicy.py) | 85 | 工作区路径与敏感项的单一策略（D13）。 |
| [agent/src/omni/tools/registry.py](../../agent/src/omni/tools/registry.py) | 275 | 工具注册表：LLM function calling 的统一入口（Registry 模式）。 |
| [agent/src/omni/tools/result.py](../../agent/src/omni/tools/result.py) | 137 | 工具结果的结构化契约（D09）。 |
| [agent/src/omni/tools/runctx.py](../../agent/src/omni/tools/runctx.py) | 50 | 低层可见的"当前运行"引用（D09）。 |
| [agent/src/omni/tools/shell_tools.py](../../agent/src/omni/tools/shell_tools.py) | 39 | 兼容薄层：旧的 `omni.tools.shell_tools` 导入路径继续可用。 |
| [agent/src/omni/tools/shots.py](../../agent/src/omni/tools/shots.py) | 84 | 截图工具与 ArtifactStore 的桥（D15/A13）。 |
| [agent/src/omni/tools/skill_tools.py](../../agent/src/omni/tools/skill_tools.py) | 5 | 旧导入兼容层；实现位于 tools/skills/。 |
| [agent/src/omni/tools/system_tools.py](../../agent/src/omni/tools/system_tools.py) | 5 | 旧导入兼容层；实现位于 tools/system/。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_tools.py](../../agent/tests/test_tools.py)

直接静态依赖：`delegation`、`personal`、`safety`、`scheduling`、`shell`、`skills`、`system-tools`

## 插件社区 · community

分类：插件与扩展。社区客户端、工具适配与独立分发服务器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/plugins/community/__init__.py](../../agent/src/omni/plugins/community/__init__.py) | 6 | 插件社区公共入口；客户端、工具与服务器分别维护。 |
| [agent/src/omni/plugins/community/client.py](../../agent/src/omni/plugins/community/client.py) | 323 | 社区客户端：插件与人设的索引 / 详情 / 安装 / 发布。 |
| [agent/src/omni/plugins/community/persona.py](../../agent/src/omni/plugins/community/persona.py) | 93 | 人设卡：社区分发的人设内容规格与收窄。 |
| [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py) | 254 | 插件社区参考服务器（FastAPI + 文件存储，单 owner 部署）。 |
| [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py) | 138 | 社区工具适配；HTTP/发布逻辑由注入客户端承担。 |
| [agent/src/omni/plugins/server.py](../../agent/src/omni/plugins/server.py) | 8 | 兼容 python -m omni.plugins.server；实现位于 plugins/community/server.py。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_plugin_community.py](../../agent/tests/test_plugin_community.py)

直接静态依赖：`platform`、`plugins`、`toolkit`

## 插件宿主 · plugins

分类：插件与扩展。manifest、隔离校验、人工激活、插件注册与生命周期；进程内受信代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/plugins/__init__.py](../../agent/src/omni/plugins/__init__.py) | 17 | 插件子系统（顶层包，与 channels/bootstrap 同级）。 |
| [agent/src/omni/plugins/host.py](../../agent/src/omni/plugins/host.py) | 412 | 插件宿主：外来功能包的隔离安装、激活、装载与卸载。 |
| [agent/src/omni/plugins/manifest.py](../../agent/src/omni/plugins/manifest.py) | 100 | 插件清单（manifest.json）：插件包的唯一身份与能力声明。 |
| [agent/src/omni/plugins/package_reader.py](../../agent/src/omni/plugins/package_reader.py) | 214 | 插件包读取与类型校验：纯数据检查，**零执行**。 |
| [agent/src/omni/plugins/quarantine.py](../../agent/src/omni/plugins/quarantine.py) | 242 | 插件隔离区与安装状态的文件层：隔离区落盘、清单记录、防篡改复核。 |
| [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py) | 178 | 插件生命周期工具（面向 agent）：安装 → 审阅 → 激活 → 启停 → 卸载。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_plugins.py](../../agent/tests/test_plugins.py)

直接静态依赖：`platform`、`safety`、`toolkit`

## 项目索引基础设施 · catalog

分类：开发与验证。只读 AST 扫描、分类和确定性文档生成；不会执行待索引代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/system/catalog/__init__.py](../../agent/src/omni/system/catalog/__init__.py) | 5 | 项目索引：只解析源码与显式分类，不导入被索引模块、不接触运行数据。 |
| [agent/src/omni/system/catalog/render.py](../../agent/src/omni/system/catalog/render.py) | 58 | 索引输出：从同一结构生成模块表、API 表和可机器查询的 JSON。 |
| [agent/src/omni/system/catalog/scan.py](../../agent/src/omni/system/catalog/scan.py) | 309 | 静态源码索引；扫描范围为本地 Agent 的源码包（兼容旧布局），不读取 config/data/.env。 |
| [agent/src/omni/system/catalog/schema.py](../../agent/src/omni/system/catalog/schema.py) | 692 | 模块分类与关键调用链；分层规则仍以 docs/ARCHITECTURE.md 为准。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_project_catalog.py](../../agent/tests/test_project_catalog.py)

直接静态依赖：`config`

## 平台基础设施 · platform

分类：平台与数据。服务、进程身份、配置写回、锁/原子文件、日志、doctor、备份与验收。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/system/__init__.py](../../agent/src/omni/system/__init__.py) | 6 | 本地系统域：服务管理（NapCat / 常驻通道启停与状态）+ 配置读写。 |
| [agent/src/omni/system/archive.py](../../agent/src/omni/system/archive.py) | 236 | 可恢复的项目归档事务；不自动挑选用户资料，不删除源数据库。 |
| [agent/src/omni/system/atomic.py](../../agent/src/omni/system/atomic.py) | 38 | 原子写文件（A09 的同源问题）。 |
| [agent/src/omni/system/backup.py](../../agent/src/omni/system/backup.py) | 567 | 离线备份与恢复（D25 的提前子项，D05 迁移前必须先能用）。 |
| [agent/src/omni/system/config_io.py](../../agent/src/omni/system/config_io.py) | 490 | config.yaml 安全读写：保留注释的定向修改（只动白名单键）。 |
| [agent/src/omni/system/doctor.py](../../agent/src/omni/system/doctor.py) | 139 | 只读诊断：配置、安装元数据、DB 完整性和进程身份；不修复、不调用模型。 |
| [agent/src/omni/system/filelock.py](../../agent/src/omni/system/filelock.py) | 157 | 跨进程文件锁（D08）。 |
| [agent/src/omni/system/observability.py](../../agent/src/omni/system/observability.py) | 114 | 有界诊断日志。事件仅保留关联字段，排除对话、工具参数与结果正文。 |
| [agent/src/omni/system/path_guard.py](../../agent/src/omni/system/path_guard.py) | 46 | 跨域共享的路径安全原语：zip/目录条目名规范化与内容摘要。 |
| [agent/src/omni/system/persona_presets.py](../../agent/src/omni/system/persona_presets.py) | 43 | 人设预设文件的版本化事务；不改变当前运行人格，不接触浏览器存储。 |
| [agent/src/omni/system/persona_rules.py](../../agent/src/omni/system/persona_rules.py) | 163 | 人格保存契约的判定规则（判定逻辑，不抄 surfcore 的形态）。 |
| [agent/src/omni/system/process_identity.py](../../agent/src/omni/system/process_identity.py) | 262 | 进程身份与 Windows 句柄终止；不以裸 PID 或端口身份授权终止。 |
| [agent/src/omni/system/protected_json.py](../../agent/src/omni/system/protected_json.py) | 60 | Windows当前用户DPAPI保护的JSON文件；不回显凭据，不跨用户解密。 |
| [agent/src/omni/system/provenance.py](../../agent/src/omni/system/provenance.py) | 48 | 服务启动源码指纹，不依赖 Git 可用性，不包含配置/凭据/运行数据。 |
| [agent/src/omni/system/release_gate.py](../../agent/src/omni/system/release_gate.py) | 98 | 校验真实证据与常驻结果；永不执行发布或代替用户批准。 |
| [agent/src/omni/system/service_overview.py](../../agent/src/omni/system/service_overview.py) | 134 | 把服务状态、守护记账与运行态快照拼成前端要的一份汇总。 |
| [agent/src/omni/system/service_state.py](../../agent/src/omni/system/service_state.py) | 124 | 服务运行态快照：子进程写，工作台读。 |
| [agent/src/omni/system/services.py](../../agent/src/omni/system/services.py) | 845 | 本地服务管理：NapCat 与 OmniOperator 常驻服务的 启动/停止/状态。 |
| [agent/src/omni/system/soak.py](../../agent/src/omni/system/soak.py) | 371 | 只读常驻采样与连续性核验；不会启动服务、调用模型或投递消息。 |
| [agent/src/omni/system/stderr_log.py](../../agent/src/omni/system/stderr_log.py) | 71 | 子进程 stderr 的有限管道泵：持续排空、跨块脱敏与文件轮转。 |
| [agent/src/omni/system/supervisor.py](../../agent/src/omni/system/supervisor.py) | 384 | 服务守护：让常驻服务在 agent 运行期间保持在线。 |
| [agent/src/omni/system/text_guard.py](../../agent/src/omni/system/text_guard.py) | 268 | 确定性文本守卫：凭据形态与高危标识的扫描（**单一真源**）。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)、[docs/ACCEPTANCE.md](../../docs/ACCEPTANCE.md)

测试：[agent/tests/test_backup.py](../../agent/tests/test_backup.py)、[agent/tests/test_process_identity.py](../../agent/tests/test_process_identity.py)

直接静态依赖：`agent-directories`、`config`、`updates`

## Python 包入口 · package

分类：开发与验证。安装包标识。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/__init__.py](../../agent/src/omni/__init__.py) | 3 | OmniOperator：像人一样生活在平台里的具身个人 agent。 |

文档：

测试：关联模块集成验证

直接静态依赖：无内部模块依赖
