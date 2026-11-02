# 会话记录：心跳调度日志里的 QQ 断线噪音——环境代理劫持本机回环 WebSocket

- 时间：2026-11-02 15:31–16:10（全部数字为本次现场实测，不是引用旧记录）
- 触发：用户贴出工作台「服务与运行 → 日志 → 心跳调度」的日志，里面成片出现
  `[QQ] 连接断开（InvalidProxyStatus）` 与 `EOFError: stream ended` 的 transport 回调堆栈。
- 一句话结论：**不是 NapCat 挂了，也不是心跳调度坏了——是 `websockets>=17.3` 默认
  `proxy=True`，把连本机 NapCat 的回环 WebSocket 也塞进了 IDE 的环境代理隧道。**
  回环目标显式 `proxy=None` 后，两类噪音同时消失。

---

## 1. 现场证据链（三重独立闭合）

### 1.1 堆栈行号与 venv 源码逐行吻合

用户日志里的两处行号，指向的是**代理专属**代码路径：

| 日志中的位置 | venv 实际源码 | 所属类 |
|---|---|---|
| `websockets/asyncio/client.py", line 770, in connection_lost` → `self.reader.feed_eof()` | `client.py:769-771` | `HTTPProxyConnection`（**代理协议类**） |
| `websockets/streams.py", line 159, in feed_eof` → `raise EOFError("stream ended")` | `streams.py:157-160` | `StreamReader.feed_eof` 的二次调用保护 |

对照**直连**路径：`websockets/asyncio/connection.py:1015` 的 `ClientConnection.connection_lost`
走的是 `self.protocol.receive_eof()`，**根本不调用 `feed_eof`**。也就是说——
这段 `EOFError` 堆栈在直连下**不可能出现**，它的存在本身就是"连接走了代理"的铁证。

`InvalidProxyStatus` 同理：全仓只在一处抛出（`client.py:746`，`HTTPProxyConnection.run_parser`），
即"代理回了非 2xx 状态"。

### 1.2 环境里确实有代理变量，且绕过判定不生效

```
HTTP_PROXY=http://127.0.0.1:49780   （IDE 的透明代理）
HTTP_PROXY / http_proxy / HTTPS_PROXY / https_proxy 四个都在
```

`urllib.request.proxy_bypass("127.0.0.1:3001")` → **False**
（Windows 上 `proxy_bypass` 读注册表 + `no_proxy` 环境变量；本机两者都没覆盖 127.0.0.1）。
于是 `websockets.proxy.get_proxy()` 返回 `http://127.0.0.1:49780`，回环目标被判定为"该走代理"。

### 1.3 端到端复现 + 对照实验

在同一台机器、同一套故障环境下跑同一脚本（起一个会对回环目标返回 403 的代理，
再让 QQ 客户端连本机 WS 服务）：

| 实现 | `InvalidProxyStatus` 次数 | 是否连上 | `self_id` |
|---|---|---|---|
| 旧行为（`proxy=True`） | **3** | ❌ | `None` |
| 修复后（回环 `proxy=None`） | **0** | ✅ | `3919700658` |

修复后另跑一次完整复刻：连接建立、拿到 self_id、**零** `InvalidProxyStatus`、**零**
`EOFError` 堆栈。

### 1.4 佐证：代理只是"多余的一跳"，并非唯一障碍

对真实代理 `127.0.0.1:49780` 直接发 `CONNECT 127.0.0.1:3001`，返回
`HTTP/1.1 200 Connection Established`——说明这条代理隧道**有时也能通**。
这解释了用户日志里"连断开很久后最终又连上了"的表象：不是稳定失败，是**时通时不通**，
取决于代理当时对回环目标的态度。这比稳定失败更难自查，也更值得在代码层根治。

---

## 2. 根因（单一真源）

`websockets` 17.3 把 `connect()` 的 `proxy` 默认值改成了 `True`：

```python
proxy: str | Literal[True] | None = True,     # client.py:276
...
if proxy is True:
    proxy = get_proxy(self.ws_uri)           # client.py:357 → 读环境变量
```

`agent/pyproject.toml` 声明 `websockets>=17.3`，所以升级后**自动**继承了宿主的代理环境变量。
而 `agent/src/omni/tools/qq/client.py` 的 `websockets.connect(self._ws_url, ...)` 从未显式
指定 `proxy`，于是本机 NapCat 的连接默认走代理。

**这是 QQ 通道独有的问题**：全仓只有 `tools/qq/client.py` 用 `websockets.connect`；
其余出网客户端早就有明确纪律——

| 客户端 | 既有做法 |
|---|---|
| `tools/web/net.py` | 模块 docstring 明写「**不读环境变量里的代理**」 |
| `tools/mcp/remote.py` | `httpx2.AsyncClient(..., trust_env=False)` |
| `llm/embedding.py` | `build_opener(urllib.request.ProxyHandler({}), ...)`，注释：「回环连接不读代理环境，禁止把私人记忆意外转发给代理」 |

QQ 通道是唯一漏掉这条纪律的出网点——而且它带的是 NapCat 的 `Authorization` token，
漏掉的代价还不止是噪音。

---

## 3. 改动

### 3.1 `agent/src/omni/tools/qq/client.py`

- 新增 `LOOPBACK_HOSTS` 与 `is_loopback_url(url)`：判定"目标是否本机"。
- `run()` 里按目标分流：

```python
proxy = None if is_loopback_url(self._ws_url) else True
async with websockets.connect(
    self._ws_url, additional_headers=headers, max_size=2**22, proxy=proxy
) as ws:
```

**回环 → 完全禁用代理；非回环 → 保持系统配置探测（行为不变）。**
远程 NapCat 的既有部署路径不受影响（`proxy=True` 就是原来的默认值）。

判定放在 QQ 工具域内而非 `system/`：本轮只有 QQ 一个调用方，跨层共享会牵动
`system` 的共享原语白名单与 `docs/ARCHITECTURE.md §3.1`（memory/llm/safety/config 不许
依赖 system），为单一调用方放宽一格不划算。若日后出现第二个调用方，再按
`system/text_guard` 的先例下沉到 `system/` 并同步 §3.1。

### 3.2 `agent/tests/test_qq.py`（+3 项）

- `test_is_loopback_url_recognises_local_targets`：回环判定边界（`127.0.0.1`/`localhost`/`::1`/
  大小写；远程与非法 URL 不算）。
- `test_qq_loopback_connection_ignores_environment_proxy`：把 `HTTP_PROXY` 指向一个拒绝代理，
  断言回环连接传给 `websockets.connect` 的 `proxy` 是 `None`。
- `test_qq_remote_target_keeps_system_proxy_behaviour`：断言非回环目标仍是 `True`，
  锁住"不顺手改掉远程行为"。

### 3.3 `docs/OPERATIONS.md`

新增「心跳调度日志里的 QQ 断线（2026-11-02）」小节：现象、根因、自检方法，
以及"这段堆栈只在代理路径出现"这个判别依据——方便下次遇到直接对号入座。

---

## 4. 验证纪律：测试确实锁住了行为

按项目纪律，新增测试必须**验证它在旧实现上确实失败**，否则可能什么都没锁住。做了两轮：

1. **整体回退** `client.py` 到 HEAD → 收集期 `ImportError`（`is_loopback_url` 不存在）——
   证明测试确实依赖新实现，但这**只**说明"函数存在"，不足以证明锁住了代理行为。
2. **只回退代理判定**（保留新函数，把 `proxy = None if is_loopback_url(...)` 换成
   `proxy = True`）→ `test_qq_loopback_connection_ignores_environment_proxy`
   **失败**，断言输出 `assert ([True] and True is None)`：
   旧行为下传给 `connect` 的确实是 `True`（走代理）。

恢复修复后 14 项全绿。

---

## 5. 测试结果

| 范围 | 结果 |
|---|---|
| `agent/tests/test_qq.py`（隔离 worktree，`PYTHONPATH` 指向 worktree 源码） | **14 passed**（原 11 + 新 3） |
| `agent/tests/test_arch.py` + `test_qq.py` + `test_service_overview.py` | **54 passed** |
| 端到端复刻脚本（故障环境 + 真实回环 WS 服务） | 修复后连接成功、零 `InvalidProxyStatus`、零 `EOFError` |
| **真机**：`omni-qq` / `omni-heartbeat` 重启后日志 | **新增 `InvalidProxyStatus=0`、`EOFError=0`、`连接断开=0`**，各一次连上；QQ 消息实收实回（`replied_chars=457`） |

## 6. 剩余限制（未做/待真机）

- **真机已验收（2026-11-02）**：经工作台 API（`POST /api/auth/pair` 换 session cookie →
  `POST /api/system/services/{name}/restart`）重启 `omni-qq`（pid 21464）与
  `omni-heartbeat`（pid 70208）后，按「最后一次『已连接 ws://127.0.0.1:3001』」切分日志核实：
  **修复后新增 `InvalidProxyStatus=0`、`EOFError=0`、`连接断开=0`，两个服务各一次连上**
  （修复前历史累计：`omni-qq` 131 次 / 131 次、`omni-heartbeat` 449 次 / 449 次）。
  更进一步：`omni-qq` 修复后**确实收到了真实 QQ 消息并回复**——
  `[QQ] scene=dm:2079937150 session=15 events=1 replied_chars=457`（及 `362`），
  说明不只是日志变干净，消息收发链路也真的通了。
- **`docs/index/` 未更新**：HEAD 里提交的索引本身已过期（提交进去的是并行会话在途的
  60 模块/335 文件版本，而 HEAD 实际源码只有 58 模块/323 文件）。在 HEAD 原样状态下跑
  `project_index.py --check` 同样报 `stale=MODULES.md,API.md,project.json`，
  重新生成会**抹掉并行会话的成果**（`system/updates/*` 等 12 个文件会从索引里消失）。
  因此本轮不动索引——那是并行会话的收口范围，不是本任务的连带项。
- **只修 QQ 通道**：`tools/mcp/remote.py` 与 `llm/embedding.py` 本来就不读环境代理，
  无需改；但若日后新增出网客户端，**须沿用同一条纪律**（本机自管服务不读环境代理），
  否则同类问题会换个地方复现。
- **`proxy_bypass` 未改**：Windows 上 `urllib.request.proxy_bypass` 依赖注册表与
  `no_proxy` 环境变量，本机都不覆盖 127.0.0.1。改注册表是用户机器的全局副作用，
  不在本次范围内；代码层已不依赖它。
- **远程 NapCat 仍走系统代理**：这是**有意保留**的既有行为（`proxy=True` 即原默认值）。
  若远程 NapCat 场景下代理反而添乱，需另开一轮处理。
