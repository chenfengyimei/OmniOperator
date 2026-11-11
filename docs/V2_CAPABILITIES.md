# V2 能力报告（D7 首节点）

> 只读投影当前实例健康快照；**不表示 V2 已验收或已切换正式入口**。真值仍以任务台账、源码与对应证据为准。

## 1. 范围

- 端点：`GET /api/v1/capabilities`（owner token）。
- 工作台：导航“能力”页，390/1024 均可用；刷新按钮重新读取，切换实例会清空旧视图。
- 归属：`domain/capabilities.py`（纯投影，无 I/O）+ `adapters/http.py` 端点 + `web/ui/v2/capabilities.js`。

## 2. 报告格式

```json
{
  "format": "omni-capability-report-1",
  "api_version": 1,
  "schema_version": "v2_0037",
  "build_id": "…",
  "ready": true,
  "closing": false,
  "platform_acceptance": "pending",
  "entrypoint": "v1",
  "v2_accepted": false,
  "areas": [
    {"id": "database", "name": "数据库", "state": "available",
     "facts": {"closed": false, "pending_reads": 0, "pending_writes": 0},
     "limits": "单 SQLite 实例"}
  ],
  "counts": {"areas": 24, "attention": 0},
  "notes": ["默认正式入口仍是 V1；本报告不表示 V2 已切换或已验收。", "…"]
}
```

## 3. 边界

- 只允许白名单布尔/整数/短文本；列表、映射只输出长度；路径、标识、凭据、自由文本一律不出现。
- 健康快照缺失的能力域直接省略，不猜测为“可用”。
- `closing=true`、非空 `error_code`/`last_error` 或 `ready=false` 标记 `attention`；其余为 `available`。
- `entrypoint` 固定为 `v1`，`v2_accepted` 固定为 `false`，直到 D7 门槛完成并显式切换。
- `platform_acceptance` 原样取自实例（当前 `pending`）；真实 MCP/OAuth、真实市场源/第三方插件、真实账号/设备与三平台原生仍未验收。

## 4. 验证

- 域投影：白名单裁剪、缺失域省略、closing/error/ready 三类 attention、notes 与格式。
- HTTP：未授权 401、owner 200、无 token/路径泄漏、能力域集合完整。
- 工作台：390/1024 打开能力页，断言 V2 未验收、平台待验收、数据库/MCP/插件域、三条注意事项、无页面错误与横向溢出。
- 证据：`docs/evidence/v2-capabilities.json`（报告、门禁、源码指纹与恢复包）。
