# V2 开发部署

V2 尚未达到替换默认入口的门槛；实现和欠账见 [重构台账](../../docs/V2_REFACTOR.md)。

在新 checkout 与独立环境中使用 `uv sync --frozen`，随后运行：

```sh
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance init
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance serve
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance auth
```

在 `http://127.0.0.1:8766` 输入本机 `auth` 命令输出的令牌。请勿将令牌放进URL、截图或公开日志。
Windows路径同样使用显式的绝对新目录；默认V2数据目录与现有V1数据隔离。

当前令牌有效期30天；令牌续期、原生平台服务安装和不可变版本切换仍属于未交付项。
无桌面环境保存模型凭据时，使用 `--master-key-file` 指向实例目录之外、仅当前用户可读的Fernet密钥文件。
密钥由部署者独立保管；程序不会自动在密文旁边生成明文主密钥。

容器配方从仓库根构建：`docker build -f deploy/v2/Dockerfile -t omnioperator:v2-dev .`。
启动时显式传入 `serve --host 0.0.0.0 --external-url https://你的域名 --stdout-logs`，由可信反向代理提供HTTPS，
不要将后端HTTP端口直接公开。需要浏览器能力时，还须在派生镜像内安装Chromium及对应系统库；本配方仅验证核心运行。
本机尚未完成容器构建、macOS/Linux运行与真实桌面验收，不能将模板存在视为已经支持。

离线备份：停止该V2实例后执行 `... --home /absolute/new-instance backup /absolute/new-backup`。
恢复：`... restore /absolute/new-backup /absolute/new-restored-instance`。目标必须是新目录。
恢复会撤销备份中的登录令牌，不恢复凭据、浏览器登录态、缓存和诊断日志。
