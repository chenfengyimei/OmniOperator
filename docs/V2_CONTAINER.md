# V2 无桌面容器交付

当前已补齐核心镜像配方、显式 Compose 部署和隔离运行核验工具。Windows 主机只完成控制器专项、真实隔离 SQLite/HTTP 的探针及 Compose 静态解析；Docker 引擎不可连接，没有构建或运行镜像，也没有 Linux/容器正式验收结论。整个 V2 仍在开发，统一全量留开发完成后执行。

## 镜像与部署

从仓库根构建开发镜像：

```sh
docker build -f deploy/v2/Dockerfile -t omnioperator:v2-local .
```

构建上下文只允许锁文件、项目元数据、应用源码和 web 资源，不发送本地数据、配置、文档、PPT 或其他用户附件。构建阶段导出锁定依赖并打包 wheel，运行阶段只安装依赖和应用，不复制 editable checkout。构建需要正常的镜像/依赖源访问，默认离线 pytest 不执行它；此开发配方不构成可复现基础镜像 digest 或正式签名供应链证明。

运行用户固定 `10001:10001`。构建时预建并归属该用户的 0700 数据目录，使新的 Docker named volume 能继承正确权限；不尝试接管已有卷。`HOME` 和 `OMNI_HOME` 显式固定，应用使用独立 V2 数据。默认只监听容器内回环；远程监听仍要求已审阅的 HTTPS origin，不绕过原服务的边界。

使用可信反向代理提供 HTTPS，先将 `OMNI_EXTERNAL_URL` 设为精确 origin（无路径、凭据或查询），再运行：

```sh
docker compose --project-name omni-v2-example -f deploy/v2/compose.yaml up --build -d
docker compose --project-name omni-v2-example -f deploy/v2/compose.yaml exec omni python -m omni.composition.cli auth
```

第二条命令只在本机所有者终端显示登录令牌，不要放进 URL、截图或日志。Compose 将后端端口绑定宿主 `127.0.0.1:8766`；反代转发至此，后端不直接公开。缺少 HTTPS origin 时 Compose 拒绝启动。浏览器本机免粘贴引导不作为反代登录方式。

Compose 使用项目自己的 `instance` 卷、只读根文件系统、128 MiB 临时目录、init、去除 Linux capabilities 和禁止提权。正常停止给 45 秒宽限，覆盖应用原有 30 秒排空预算。项目名/端口已被占用时应显式选择其他部署，不删除或覆盖未知卷。没有自动导入 V1、自动签发模型凭据、设备挂载或桌面总线转发。

此镜像只交付无桌面核心及浏览器端工作台资源，未捆绑服务端 Chromium、原生桌面可选依赖、ADB、ONNX/ASR 模型或密钥。保存凭据仍须部署者提供实例外、仅 UID 10001 可读的主密钥文件，并通过显式 `--master-key-file` 和只读挂载传入；不得把明文主密钥放在实例卷旁或普通环境变量中。缺少这些资源应如实显示能力不可用。

## 独立镜像核验

镜像构建完成后，在 Docker 已运行的验收主机执行：

```sh
python scripts/check_v2_container.py --image omnioperator:v2-local --output temp/build/new-container-proof.json
```

工具不构建、拉取或启动 Docker，只把给定本地镜像解析为不可变 image ID 后创建随机名称的新容器/卷。容器 `--network none`、不映射宿主端口、不挂载宿主路径；探针通过容器内部回环检查 ready、未授权 401、实际所有者读取、UID/0700/0600 和无桌面环境。随后正常停止、重启同一容器，核对 Agent/实例/schema/build 保持、boot 更换，再核对正常退出码。探针读取的令牌不出容器，报告仅保存白名单元数据。

每次使用新报告文件，旧报告拒绝覆盖。只回收本次随机资源，失败不得变成通过；create 返回不明时在先行收据中保留资源名称与派发标记，`cleanup_complete=false`，不能推断从未创建或删除不明所有者。正常成功也不代表 72 小时、真实模型/账号/设备、其他平台或整个 V2 通过。对应 CI 任务已加入，但任务配置不等于已运行；实际报告另行验收保存。

本批 15 个新增专项和打包合同调用方局部验证，见[开发证据](evidence/v2-container-delivery.json)。系统元数据与 Docker 调用的注入明确标记，不计为真实容器证据。原 Compose 组装和清理错误期望的首轮失败保留，修复后指定节点通过。没有启动全量。
