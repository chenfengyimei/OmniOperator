# V2 B站读缓存与凭据换代失效（K18.read_cache）

状态：已实现并专项通过（2026-11-22）。学习参考 dsh-bili-whale `bili-api.mjs` 的读缓存（`view`/`acc-info` 24h、`nav` 10min、400 条 LRU），独立实现于 V2 四层 B 站链，不复用 V1 `tools/bilibili`。补齐 K18 审计「读 API 广度」中的**缓存策略**项；读操作广度、风控暂停已由前批交付，本批不重复。

## 范围与决策

1. **只缓存两个稳定端点**：`video`（`x/web-interface/view` 投影）与 `whois`（`space/wbi/acc/info` 投影），TTL 24h。其余 13 个读操作**永不缓存**——`account`/`comments`/`replies`/`search`/`relation`/`danmaku`/`popular`/`ranking`/`following`/`favorites`/`tags`/`history`/`msgfeed` 每次现读。`READ_CACHE_TTL_S` 只含这两键；`READ_SPEC` 的 operation 枚举与可缓存集合的差集由回归钉住，新增端点必须显式决定。
2. **nav 有意不缓存**（比参考更严）：每次读都以真实凭据回源校验登录身份；参考的 `nav` 10min 缓存会让凭据吊销/换代在窗口内不可见，故不实现并登记为「有意更严」，不计缺口。
3. **作用域键** = `(agent_id, config_id, credential authority, operation, target, limit)`。`authority` 由既有 `bilibili_read_authorized` 计算（配置 revision + 凭据 revision + `authority_epoch`），**凭据换代即换桶**；新一代首次读入时 `discard_generations` 丢弃该配置所有旧代快照，绝不跨代复用。
4. **命中仍需核验**：命中后以缓存键内的 `revision_id` 重新 `bilibili_read_authorized` 比对 authority；不一致则丢弃该配置全部快照并回退为现读。命中路径不跳过风险暂停门（暂停检查在缓存查询之前），也不绕过配置停用/归档（`bilibili_configuration_revoked`）。
5. **有界**：`READ_CACHE_MAX_ENTRIES=400`、`READ_CACHE_MAX_BYTES=8 MiB`、单条 `READ_CACHE_MAX_ENTRY_BYTES=64 KiB`；TTL + LRU；过期条目在读取或淘汰时移除；超大、非 JSON 值不缓存（读本身仍成功）。
6. **快照语义**：按 canonical JSON 文本存储（保留插入序，非排序键），命中返回独立副本——调用方改写不污染缓存，且模型工具文本与现读**逐字节一致**（回归钉住）。
7. **失败不缓存**：网关错误、风控码（另有暂停持久化）、任何异常一律不进缓存；unknown 不重放语义不变。
8. **关闭即清空**：`stop_accepting` 清空缓存；`health()` 暴露 `read_cache_entries`/`read_cache_bytes` 只读计数（不含目标与正文）。
9. **无新表/迁移/路由/worker 协议/共享端口签名改动**；`BilibiliService` 构造仅加可选关键字 `clock`/`cache`（组合根不变）。`media` 直连网关的 `read_source`（subtitle/streams）不经此缓存。

## 实现位置

- `agent/src/omni/domain/bilibili.py`：`READ_CACHE_TTL_S`/上限常量/`read_cache_key`/`ReadCache`（`get`/`put`/`discard_scope`/`discard_generations`/`clear`）。
- `agent/src/omni/application/bilibili.py`：`read` 的命中复核、旧代清理、落盘与日志；`health`/`stop_accepting`。
- 适配器、持久层、HTTP、组合根未改（网关每次仍现取 cookie；凭据权威 CAS、风控暂停、admission≤8、shield 全复用）。

## 验证（2026-11-22）

- 新增 **38 项**回归（28 领域 + 10 应用集成）：只缓存 `video`/`whois`、键含 owner/config/authority、非法键、独立副本、TTL 边界、LRU、字节预算、超大/非 JSON 拒绝、旧代清理、按配置清理、防御性非对象快照、清空、构造/`ttl` 参数拒绝；真实隔离 SQLite 实例：`video`/`whois` 二次命中、13 个端点每次现读、凭据换代换桶并清旧代、按配置隔离、暂停优先于缓存、失败不缓存、所有者停用配置后拒绝、注入时钟 TTL、模型工具文本逐字节一致。
- 专项：本文件 **38 过 / 37.31s**；bilibili 全调用方（19 个读+发表+风控+网关+worker+HTTP 套件含本文件）**495 过 / 167.84s**；media/架构/能力调用方 **12 过 / 21.22s**。
- strict（`pyrightconfig.strict.json`，仓库根）**0 错误 0 警告**；`ruff check`/`format --check` 净。
- 未验：真实 B 站账号的缓存命中率与 24h 新鲜度、进程重启后的命中（内存缓存天然不持久）、`nav` 10min、读侧速率预算、真实风控触发/恢复。

## 明确不做

- `nav` 10min 缓存（有意更严）；读侧参考速率预算 600/h·3000/d 与 120s 冷却（需共享预算账本设计，列决策登记）；写广度、主动观看/双日记/沉默衰减、受控分享策略、直播、AI-share 守卫（见 [剩余差异决策登记](V2_REMAINING_DECISIONS.md)）；真实账号/模型质量与 72 小时验收另验。
