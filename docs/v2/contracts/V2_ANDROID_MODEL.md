# V2 安卓运行权限与扩展输入

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

## 使用路径

1. 所有者在[安卓面板](V2_ANDROID.md)核验并授权设备，将面板展示的`grant_id、target_serial、target_user`引用按任务提供给模型。模型不能列举未提供的设备、创建永久设备授权或配对。
2. 模型调用`android_request_access`，列出应用、动作和期限，进入统一人工审批。
3. 审批完成返回`access_id`，绑定本次所有者模型运行、Agent、会话内容代次、策略和原设备授权。最多16个明确应用，动作是固定枚举的子集；期限60–900秒，运行结束即失效。`kinds=[]`表示只读。QQ会话没有这些模型工具。
4. `android_observe`读取授权应用的当前界面，`operation=apps`仅查询范围内的已安装包名。前台应用越界时，在UI XML读取前拒绝。
5. `android_act`绑定同运行、同权限的观察及节点，实际输入再次单独审批。目标序列号、用户、应用、资源ID和观察指纹必须匹配宿主观察，模型填写的标签不能改换目标。

示例权限申请：

```json
{
  "grant_id": "替换为设备授权UUID",
  "target_serial": "替换为实际序列号",
  "target_user": 0,
  "packages": ["org.example.app"],
  "kinds": ["append_ascii", "swipe"],
  "ttl_seconds": 600
}
```

设备范围批准不代替动作批准。模型权限不能跨运行、会话或Agent复用，所有者观察不能直接转成模型节点。工作台可查看范围、期限及不可用原因，并按revision撤权；列表查询不访问设备。取消或撤权不能保证撤回已派发输入。

## 数据与授权真值

当前106张业务表、`v2_0030`。新增三张规范化关系表：

| 表 | 保存内容 |
|---|---|
| android_access | 设备授权、运行、批准动作、会话代次、策略、范围指纹、到期和撤权修订 |
| android_access_packages | 允许的包名；按权限/包名唯一，Agent复合作用域外键 |
| android_access_kinds | 允许的动作；唯一约束、数据库枚举与Agent作用域外键 |

授权范围使用独立行，不用任意JSON作为权限真值。根记录只能撤销，范围行不能更新/删除；读取时重新组装有界集合并核验批准指纹。每个批准动作最多建立一份权限，根、范围和审计同事务提交，不建立第二套审批或动作状态。

来源动作必须属于同运行、是实际完成的`android_request_access`，且状态ok、效果applied。取消或响应丢失使来源为unknown时，即使权限行已提交也不可使用。根设备/配置变化、恢复历史代次、会话内容失效、策略撤回、期限到达或运行终止均拒绝访问。

`approved_dispatch`统一当前运行、动作参数、策略和审批期限；所有者另查命令移交，模型另查运行/应用/动作范围。执行前再次核验。任务、子进程、文件及租约仍由既有生命周期拥有，不因请求等待者取消而丢失。

## 新操作

全部动作继续绑定有限观察，不提供任意坐标或shell：

| 动作 | 条件 | 完成口径 |
|---|---|---|
| swipe | 可滚动控件，方向上下左右，100–1500ms，端点在控件内部 | 同应用/容器内至少两个唯一稳定子元素沿批准轴移动；仅文字变化、反向或无法证明则unknown |
| long_press | 非密码、可长按控件，500–1500ms | 缺少通用语义后置证明，派发后unknown，不重试 |
| key | 当前聚焦的非密码控件，仅BACK/HOME/ENTER/TAB及四方向键 | 不把事件发送或退出码当业务成功，通常unknown |
| launch | 指定包名，读取当前Android用户的唯一Launcher组件，模型目的包也须在白名单 | 再核验设备、用户、解锁及精确前台组件；只证明进入前台，不证明应用任务完成 |
| append_ascii / toggle / tap | 沿原合同 | 文本/勾选回读；普通点击保持unknown |

应用查询使用固定`pm list packages --user`，最多解析4096包，每页200项，包名字典序游标。模型最多看到批准的16个应用。启动不接受任意Intent extras，不安装/卸载或root。单独批准的Unicode输入可临时选择已启用的配对IME，先登记恢复义务，详见[Unicode合同](V2_ANDROID_UNICODE.md)。

未知输入仍阻止同设备后续输入，直至所有者独立核查，不能以新运行或重新授权绕过。已核验操作之后，模型须重新观察，不复用旧节点。

## 接口与工作台

新增所有者认证接口：

- `GET /api/v1/agents/{agent_id}/android/access`：有界游标、范围、当前不可用原因。
- `POST /api/v1/agents/{agent_id}/android/access/{id}/revoke`：精确revision。
- `POST /api/v1/agents/{agent_id}/android/grants/{id}/apps`：`consent=true`、可选after包名游标。

原动作接口增加严格params；面板提供方向、时长、键、启动包名、设备引用和运行权限撤销，草稿按Agent隔离，所有服务端文本安全渲染。输入与界面正文只进入受保护业务记录，不进入诊断/指标；授权审计记录ID与指纹。

离线覆盖实际受管Python、SQLite和假ADB/模型，包括双重审批、越界拒绝、应用过滤、撤权、来源未知、范围行变化、期限、滑动证明、启动及工作台。历史测试数字保留所属源码版本，不宣称全量、打包或真实平台通过。

受控Unicode组件现已接入独立Android应用、宿主与工作台，开发和离线验证见[Unicode合同](V2_ANDROID_UNICODE.md)及其证据；真实设备仍待验收。后续开发进入D6模型/媒体/MCP等扩展和D7产品切换，历史72项仅代表本文件原批次。

参考[AOSP InputShellCommand](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/input/InputShellCommand.java)及[Android ADB](https://developer.android.com/tools/adb)。厂商命令、权限字段和组件行为仍需实机核验。

## 历史批次状态（非当前结论）

以下原记录中的默认入口与完成度仅属于当时源码；当前默认入口为 V2，实际进度以当前交接和任务为准。

> 本批接入模型本次运行权限，以及滑动、长按、按键、应用查询和启动。没有操作真实设备；中文输入辅助组件和真实验收仍待。整个D5和V2未完成，默认入口V1。
