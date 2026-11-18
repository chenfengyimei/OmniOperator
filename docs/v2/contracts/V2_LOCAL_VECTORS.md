# V2 本地向量运行时与文档混合检索

D6 接入显式本地 ONNX 模型、真实 CPU 推理及文档索引。默认入口仍为 V1；本批不代表完整 V2、记忆语义召回、预训练模型质量或三平台实机验收完成。当前状态以 [交接](../../HANDOFF.md)、[台账](../../tasks/v2.json) 和 [证据](../../evidence/v2-vectors.json) 为准。

## 部署与选择

新环境使用锁定依赖安装 `vectors` 可选项；已有核心环境不需要为启动系统安装原生 ML 组件。项目未选模型或组件不完整时仍提供全文检索。模型从用户审阅的本地目录读取，不下载权重、不查询模型站点、不读取 API 凭据。

启动参数文件为实例 `config/local-vector.json`，只保存安装包路径及 manifest 指纹。它属于本机部署选择，不是第二套可编辑业务配置。CLI 在实例独占锁内检查、选择或停用，已有主进程持锁时拒绝变更；变更作用于下次启动，不自动重启服务。

```powershell
uv run --no-sync python -m omni.composition.cli --home D:\OmniV2 vectors inspect D:\models\my-vector
uv run --no-sync python -m omni.composition.cli --home D:\OmniV2 vectors configure D:\models\my-vector --sha256 <inspect输出的sha256>
uv run --no-sync python -m omni.composition.cli --home D:\OmniV2 vectors disable
```

macOS/Linux 使用对应绝对目录，命令结构相同。`inspect` 校验文件指纹但不运行模型；`configure` 原子写入选择，保留此前有效选择直至新文件落盘。它们都不宣称已经加载或验收模型。指纹是规范化 manifest JSON 的 SHA256，不能用带任意排版的 JSON 文件字节指纹代替。

模型目录包含以下三个文件；其他文件不参与加载：

| 文件 | 限制与用途 |
| --- | --- |
| `manifest.json` | 最大 64 KiB，固定字段、维度和推理策略 |
| `model.onnx` | 最大 512 MiB，自包含 ONNX 图和权重；拒绝外部张量文件 |
| `tokenizer.json` | 最大 16 MiB，标准 Tokenizers JSON；不执行外部 Python 代码 |

示例 manifest 的 SHA/大小必须换成真实值：

```json
{
  "schema": 1,
  "model_id": "reviewed-local-model",
  "dimension": 384,
  "max_tokens": 256,
  "pooling": "mean_masked",
  "output": "last_hidden_state",
  "pad_id": 0,
  "pad_token": "[PAD]",
  "query_prefix": "",
  "document_prefix": "",
  "files": {
    "model.onnx": { "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "bytes": 1 },
    "tokenizer.json": { "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "bytes": 1 }
  }
}
```

维度限制 1–512，token 窗口 8–512；池化明确选择 `mean_masked`、`cls` 或已池化二维输出 `pooled`。输入必须为 `input_ids`、`attention_mask`，可加 `token_type_ids`，均为二维 int64；选定输出为 float32 的二维/三维张量。实际模型不符合此合同则报告不可用，不猜测其他输入或输出。

## 进程、来源与索引身份

`adapters/local_vectors.py`只负责有界请求、版本化 IPC 和实际进程归属；固定 worker 才导入 ONNX、ONNX Runtime 和 Tokenizers。所有平台共用已有 `OwnedProcess`，不在主进程加载原生推理库，子进程不打开数据库，也不继承服务凭据。

第一次向量请求才校验两个文件的实际 SHA、长度及文件身份，拒绝符号链接、联接和多链接文件。加载前递归检查所有 ONNX 张量，包括函数/子图/稀疏张量，拒绝外部数据；随后只将内存中的模型字节交给 CPU Runtime。ONNX 解析使用 [官方序列化 API](https://onnx.ai/onnx/api/serialization.html)，外部张量字段依据 [ONNX 数据合同](https://onnx.ai/onnx/repo-docs/ExternalData)。

模型、tokenizer、池化、查询/文档前缀、分窗算法、CPU provider 和锁定运行时共同生成 `onnx-v1:<SHA256>` 版本。版本/维度沿用现有 `document_index_builds`、`document_vectors` 及活动索引指针，不新增数据库或兼容导入。不同模型或运行时的旧索引不会进入向量比较，必须明确重建；原始来源和全文索引仍保留。

Windows、Linux、Apple Silicon 使用 ONNX Runtime 1.31.1。Intel Mac 单独锁定 1.23.2，该版本提供 [官方 macOS x64 轮子](https://pypi.org/project/onnxruntime/1.23.2/)；其索引身份单独计算，模型图仍须通过对应运行时验证。此依赖选择不等于 Mac 已验收。

每次推理前后复核已加载文件的身份。文件被替换、删除或修改后停用该运行时并回收进程，不继续使用缓存权重冒充当前模型；排查后重新选择并启动。原生模型属于用户主动信任的文件，进程隔离提供故障回收，不宣称操作系统安全沙箱或硬内存配额。

## 推理与降级

最多八项已接收请求，同一模型进程串行计算；每批最多八段文本。CPU intra-op 线程 2、inter-op 线程 1，关闭线程空转。相关选项来自 [ONNX Runtime 线程合同](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)。单次请求含启动、加载和推理总限额 10 秒，超时/取消后等待实际回收；关闭失败保留进程、清理任务及上层实例资源，不能报告已关闭。

查询与文档使用各自前缀。超长 token 序列使用 [Tokenizers overflow](https://huggingface.co/docs/tokenizers/python/latest/api/reference.html) 分窗，每段最多十六窗，每次计算最多八窗；按有效 token 数加权聚合，再验证并归一化。不静默截去文档末尾，超过明确上限时报错。拒绝零向量、非有限值、数量/形状不符、错误版本或迟到的其他请求响应。

只有显式重建才产生文档向量，普通保存仍只建全文索引。来源编辑或删除会使在途构建失效，已经计算出的向量不能重新发布。查询结果继续经过 Agent、文档修订、实际对象与删除传播复核，工具引用继续登记原始来源锚点。

模型缺失、版本不符、文件变化、推理失败或索引缺失时，全文检索继续可用，并返回具体降级原因。工作台区分“已配置待首次核验”“已加载”和“不可用”；模型 ID 与错误状态按文本渲染。启动时记录所选运行时的版本、维度、启动编号和状态审计；诊断不记录正文、查询、向量数组、原生异常文本或模型目录。

## 备份与验收

数据库与对象继续沿既有一致快照备份；模型权重和本机选择文件属于安装材料，需要单独保存。恢复到新目录后未选择模型会降级全文检索；重新选择匹配包可继续使用同版本派生索引，版本变化需重建。停用组件不删除消息、记忆、来源或索引。

普通离线测试覆盖文件/清单/配置、真实受管进程 IPC、坏帧、取消与回收失败。`native_v2_local_vectors.py`是显式可选依赖门禁：使用真实锁定 ONNX/Tokenizers/Runtime 及合成图，在隔离数据库验证分窗、前缀、索引、检索、隔离、删除和模型变化；没有下载或调用预训练模型，不能用这些结果宣称真实语义质量。

CI 增加 Windows/Linux、Apple Silicon 与 Intel Mac 的独立向量任务；本机未执行远程 CI。三平台真实模型、语义质量、参考负载和长时间运行仍需固定构建验收。后续 D6 继续档位路由、记忆语义接线/整理、技能媒体、MCP/OAuth 与插件市场，D7 再切换正式入口。


后续档位已接规则与固定模型升档，合同见[V2档位](V2_ROUTING.md)；当前继续记忆语义，以上验证仍仅属于原批次。


记忆索引、混合召回与相关核查现已接入同一本地端口，见[记忆语义合同](V2_MEMORY_SEMANTICS.md)。向量批次原证据仍只属于该固定源码。
