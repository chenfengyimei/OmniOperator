# macOS arm64 候选安装与首次使用（2026-11-23）

基线 `b29409d0`；本机 macOS 27.2.1（26B131）arm64、Swift 6.4.2、SDK 27.2、Python 3.12.16。

## 产物

- Wheel：`temp/build/wheel/omnioperator-0.1.0-py3-none-any.whl`
  SHA256 `d478d0e6ff365ba8d62fb9fdbc698c62cc18acc4290d9dae03e4b29e20c6e136`
- Helper：`temp/build/mac-native-release/OmniOperator Desktop Helper.app`
  可执行 SHA256 `1ab02d9c3e327882a59591384561383959b761d8be58de49c3150bf690fdd99f`（ad hoc 签名、`codesign --verify --strict` 通过）

## 在另一台 Mac 上执行

```sh
# 1. 隔离安装（不写 /Applications，不动日常实例）
REAL_PY=~/.local/share/uv/python/cpython-3.12.16-macos-aarch64-none/bin/python3.12
"$REAL_PY" -m venv ~/omni-candidate/venv
~/omni-candidate/venv/bin/python -m pip install --no-cache-dir omnioperator-0.1.0-py3-none-any.whl

# 2. 初始化独立 home 并启动工作台
~/omni-candidate/venv/bin/python -m omni.composition.cli --home ~/omni-candidate/home init
~/omni-candidate/venv/bin/python -m omni.composition.cli --home ~/omni-candidate/home serve \
  --host 127.0.0.1 --port 8878 --stdout-logs

# 3. 浏览器打开 http://127.0.0.1:8878/ ，用 ~/omni-candidate/home/run/owner.token 认证
```

技能/桌面能力需要 helper 时，把 `OmniOperator Desktop Helper.app` 放到固定路径并重新在系统设置里授权（TCC 与路径/签名绑定）。

## 本机已实测

工作台 200、`/health/ready` true、无 token 401 / 有 token 200、创建 `release-smoke` Agent、重启后同一 instance、Agent 持久化。

## 未验

Model 任务（隔离 home 无模型凭据）、LaunchAgent/Keychain/桌面能力（候选 helper 未替换已授权 helper）、升级/回滚、Developer ID 签名/公证、第二台 Mac、72 小时。
