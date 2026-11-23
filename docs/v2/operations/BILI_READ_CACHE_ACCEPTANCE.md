# B 站读缓存验收（K18.read_cache 上线后核对）

> 目的：在**已合并的 v2 源码**上，用真实 B 站账号（只读）核对 `bilibili_read` 缓存的实际行为，并记录观察窗口与结果。写入、主动观看、分享、直播不在本页范围（按 A 选定范围另行分批）。
>
> 状态（2026-11-23）：验收脚本与离线自检已交付；**真实账号观察窗口未开始**——本机未找到可用的 B 站账号凭据（见「凭据来源」）。

## 验收对象

`origin/v2` 已合入的 K18 读缓存（合并 `3655d269`，实现 `341041a6`；D8 `85716859`、D9 `46c098f6` 同批）。只缓存 `video`/`whois`（各 24h），13 个端点永不缓存、`nav` 有意不缓存；缓存键含 `(agent, config_id, credential authority, operation, target, limit)`，凭据换代换桶并清旧代；风险暂停先于缓存。

## 凭据来源（真实模式）

脚本需要一个隔离的 B 站登录 Cookie JSON（至少 `SESSDATA`，可含 `bili_jct`/`DedeUserID`）与对应 UID：

- **主实例现有账号**：V1 主实例的 Cookie 约定位置是 `data/bili_cookies.json`（`agent/src/omni/config.py:45`）。本机 2026-11-23 检查该文件**不存在**；V2 默认/演示实例的 `channel` 配置与 `credential_references` 均无 B 站条目，`~/.bili-ai/credentials.json` 也不存在。
- 因此真实运行前需所有者提供以下任一：
  1. 把隔离账号 Cookie JSON 放到 `D:\xm\OmniOperator\data\bili_cookies.json`（或任意仓库外路径），并给出 UID；
  2. 在隔离 V2 实例的凭据工作台登记 B 站 Cookie，并给出实例目录与 `config_id`（本脚本另需 cookie 文件路径以便注入只读 SecretStore）。
- Cookie 文件**不进入仓库、不打印、不写入证据**；脚本只通过只读 `SecretStore` 读取，网关负责把 Cookie 值登记到 Redactor。

## 运行

```powershell
# 0) 离线自检（无网络；验证脚本与缓存语义的 harness 本身）
uv run --no-sync python agent/scripts/accept_bilibili_read_cache.py --self-test `
  --home C:/v2-acceptance/bili-cache-verify-<时间戳>/home `
  --output C:/v2-acceptance/bili-cache-verify-<时间戳>/out

# 1) 真实账号（只读；cookie 文件在仓库外）
uv run --no-sync python agent/scripts/accept_bilibili_read_cache.py `
  --home C:/v2-acceptance/bili-cache-live-<时间戳>/home `
  --output C:/v2-acceptance/bili-cache-live-<时间戳>/out `
  --cookie-file <repo外cookie.json> --account <隔离UID> [--video BV...]
```

- `--home` 必须是**新目录或空目录**；脚本自建独立实例，不读写主实例数据、不停用户服务。
- 不传 `--video` 时从公开 `popular` feed 取第一条作为视频目标；`whois` 目标默认是该账号 UID。
- 退出码 0 仅当全部检查通过；`result.json` 写入 `--output`。

## 检查项与判据

| 检查 | 判据 |
|---|---|
| `video_cache_hit` / `whois_cache_hit` | 连续两次同参读取，网关 `read` 调用数 == 1，两次结果相等 |
| `credential_rotation_misses` | 凭据 revision +1 后再次读取 video/whois，调用数各 +1；旧代条目被清（缓存条目数 = 2） |
| `risk_pause_precedes_cache` | 仓库层合成暂停后读 video：错误码 `bilibili_risk_paused`，网关调用数不增（缓存未被服务） |
| `cache_resumes_after_owner_resume` | 所有者 resume 后再读 video：命中缓存（调用数不增） |
| `history/dynamic_list/msgfeed_always_live` | 每个永不缓存端点连续两次读取，网关调用数 +2；错误记录到 `live_errors` 但不回退为旧缓存 |
| `never_cached_ops_add_no_entries` | 上述读取后缓存条目数不变（仍只有 video/whois） |
| `video_ttl_24h_boundary` | 受控时钟 +86 399s 仍命中、+1s（恰 24h）未命中并回源 |

## 证据与观察窗口

`result.json` 记录：`mode`（`real_account`/`self_test`）、`started_at`/`finished_at`/`elapsed_seconds`/`timezone`（观察窗口）、脱敏账号（`***末两位`）、video BV、whois mid、`cacheable_operations`、`ttl_seconds`、逐项 checks（含调用数与缓存条目）、`cache_after`、`live_errors`、`instance_home`、`closed_complete`、`result` 与 limits。

真实运行后把结果摘要写入 `docs/evidence/v2-bili-read-cache-live.json`（脱敏、不含 Cookie/完整账号）并更新任务台账；脚本自检结果见 `docs/evidence/v2-bili-read-cache-acceptance-harness.json`。

## 边界（不宣称）

- 只读：不发布、不发送、不改 B 站账号状态。
- TTL 边界用**受控时钟**验证；不等待真实 24 小时墙钟。
- 风险暂停为**合成暂停**（仓储层插入），用于验证「暂停门先于缓存」的顺序，不是真实风控触发。
- 「永不缓存端点不返回旧数据」由网关调用计数与缓存条目计数证明；不修改远端数据来对比内容。
- 真实账号的一次观察窗口不代表模型质量、72 小时或全量验收。
