"""安卓能力域（P4 地基）：纯 ADB 协议层 + 高层语义驱动 + 工具。

纪律（xuexi/NOTES.md §2/§3）：
- 三处防御写进协议层第一版：keycode 白名单、input text 转义、PNG 签名裁剪；
- 中文输入 ADBKeyboard base64 广播，finally 恢复原输入法；
- 每轮只做一个改变设备的动作，动作后由上层（agent）拿新 UI 树复核。
"""
