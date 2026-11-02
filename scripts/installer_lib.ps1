<#
共享实现：安装步骤、预检与启动工作台。
被两个前端 dot-source：
- scripts/install.ps1     （CLI 安装：Sink 为 scriptblock，立即打印）
- scripts/start.ps1        （CLI 启动）
（原图形安装器 install_gui.ps1 已废弃，见 temp/retired-installer-2026-11-01/）

约定：
- Send-Say 的 $Sink 同时接受 scriptblock（参数：文本, 级别）或 System.Collections.Queue
  的同步队列（元素为 @{ text; level }），后者用于跨线程刷新（保留能力，当前无 GUI 使用）。
- $Progress 是（可同步的）hashtable：s0..s8 为步骤状态
  （pending/running/ok/warn/fail/skip），result 为 ok/blocked/fail。
- 全程 PowerShell 5.1 兼容：不使用 PS7 专用的链式 and/or 运算符与三元表达式。
#>
param()

$script:InstallerRoot = Split-Path -Parent $PSScriptRoot

# NapCat v4.18.41 NapCat.Shell.zip 的官方 sha256（GitHub release digest）。
# 版本钉死才可能钉哈希；换版本时需同步更新（docs/INSTALLER.md 有获取方法）。
$script:NapCatZipSha256 = '6801f93e533846cea05f4f531874a0e84371ab2331c5585b57cd7e3b869cf96b'

function New-SinkQueue {
    return [System.Collections.Queue]::Synchronized((New-Object System.Collections.Queue))
}

function Send-Say {
    param($Sink, [string]$Text, [string]$Level = 'info')
    if ($Sink -is [scriptblock]) {
        & $Sink $Text $Level
    } elseif ($Sink) {
        $Sink.Enqueue(@{ text = $Text; level = $Level })
    }
}

function Save-FromUrls {
    # 依次尝试候选 URL 下载到 $Destination；成功返回 $true（大于 1KB 即视为落盘）。
    param($Sink, [string[]]$Urls, [string]$Destination)
    $parent = Split-Path -Parent $Destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    foreach ($u in $Urls) {
        try {
            Invoke-WebRequest -Uri $u -OutFile $Destination -UseBasicParsing -TimeoutSec 300
            if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 1024)) { return $true }
        } catch {
            Send-Say $Sink ('下载失败，换下一个源：' + $u) 'note'
        }
    }
    return $false
}

function Get-GhUrls {
    # GitHub 资产：给了 -Proxy 就代理优先、直连兜底；没给就只直连。
    param([string]$Proxy, [string]$Url)
    if ($Proxy) { return @($Proxy.TrimEnd('/') + '/' + $Url, $Url) }
    return @($Url)
}

function Add-Path {
    # 持久化加入用户 PATH（幂等），并同步当前会话。
    param([string]$Dir)
    if (-not (Test-Path $Dir)) { return }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    if (@($userPath -split ';') -notcontains $Dir) {
        $parts = @($userPath -split ';' | Where-Object { $_ }) + $Dir
        [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
    }
    if (@($env:Path -split ';') -notcontains $Dir) { $env:Path = $env:Path + ';' + $Dir }
}

function Test-LockedEntries {
    # 独占打开探测：omni*.exe 被占用说明服务在跑，uv 动 .venv 会失败（HANDOFF 平台坑）。
    param([string]$VenvScripts)
    $locked = @()
    if (-not $VenvScripts -or -not (Test-Path $VenvScripts)) { return $locked }
    foreach ($name in @('omni.exe', 'omni-web.exe', 'omni-qq.exe', 'omni-heartbeat.exe')) {
        $p = Join-Path $VenvScripts $name
        if (-not (Test-Path $p)) { continue }
        $stream = $null
        try {
            $stream = [System.IO.File]::Open($p, 'Open', 'ReadWrite', 'None')
        } catch {
            $locked += $name
        } finally {
            if ($stream) { $stream.Close() }
        }
    }
    return $locked
}

function Test-NapCatReady {
    param([string]$Dir)
    if (-not $Dir) { return $false }
    return (Test-Path (Join-Path $Dir 'napcat.bat'))
}

function Test-PortListening {
    param([int]$Number)
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync('127.0.0.1', $Number)
        if ($task.Wait(500) -and $client.Connected) { return $true }
        return $false
    } catch { return $false } finally {
        if ($client) { $client.Close() }
    }
}

function Get-FreshPairUrl {
    # 只读 pair_url.json 且必须未过期；文件缺失/损坏/过期一律返回 $null。
    # 配对码文件在换码那一刻才刷新，可能滞后（HANDOFF 平台坑）。
    param([string]$Root)
    $pairJsonPath = Join-Path $Root 'data\run\pair_url.json'
    if (-not (Test-Path $pairJsonPath)) { return $null }
    try {
        $data = Get-Content -LiteralPath $pairJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch { return $null }
    if (-not $data -or -not $data.url -or -not $data.expires_at) { return $null }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ([double]$data.expires_at -lt ($now + 5)) { return $null }   # 留 5 秒余量
    if (-not $data.boot_id -or -not $data.pid) { return $null }
    try {
        $pairUri = [Uri]$data.url
        if ($pairUri.Scheme -ne 'http' -or $pairUri.Host -notin @('127.0.0.1', 'localhost', '[::1]')) { return $null }
        if ($pairUri.Port -ne [int]$data.port -or $pairUri.UserInfo -or $pairUri.Query -or $pairUri.Fragment -or $pairUri.AbsolutePath -notmatch '^/pair/[A-Za-z0-9_-]+$') { return $null }
        $request = [System.Net.WebRequest]::Create(('http://127.0.0.1:' + $data.port + '/api/auth/status?startup=1'))
        $request.Proxy = $null
        $request.Timeout = 1200
        $response = $request.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try { $health = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
        if (-not $health.ready -or $health.boot_id -ne $data.boot_id -or $health.pid -ne $data.pid) { return $null }
        if (-not (Get-Process -Id ([int]$data.pid) -ErrorAction SilentlyContinue)) { return $null }
    } catch { return $null }
    return $data.url
}

function Invoke-Installer {
    # 9 个状态位（0 预检 + 8 步）。返回 'ok' / 'blocked' / 'fail'。
    param($Sink, [hashtable]$Progress, [hashtable]$Options)

    $Proxy = ''
    $PypiMirror = ''
    $NapCatVersion = 'v4.18.41'
    $ToolsDir = ''
    $SkipNapCat = $false
    $SkipAdb = $false
    $SkipChromium = $false
    if ($Options) {
        if ($Options['Proxy']) { $Proxy = [string]$Options['Proxy'] }
        if ($Options['PypiMirror']) { $PypiMirror = [string]$Options['PypiMirror'] }
        if ($Options['NapCatVersion']) { $NapCatVersion = [string]$Options['NapCatVersion'] }
        if ($Options['ToolsDir']) { $ToolsDir = [string]$Options['ToolsDir'] }
        if ($Options['SkipNapCat']) { $SkipNapCat = $true }
        if ($Options['SkipAdb']) { $SkipAdb = $true }
        if ($Options['SkipChromium']) { $SkipChromium = $true }
    }
    $Root = $script:InstallerRoot
    if ($Options -and $Options['Root']) { $Root = [string]$Options['Root'] }
    if (-not $ToolsDir) { $ToolsDir = Join-Path $Root 'tools' }

    for ($i = 0; $i -le 8; $i++) { $Progress[('s' + $i)] = 'pending' }
    $Progress['result'] = ''

    # ---------- 0/8 预检 ----------
    $Progress['s0'] = 'running'
    Send-Say $Sink '0/8 预检（确保没有服务占用安装目录）' 'step'
    $locked = Test-LockedEntries (Join-Path $Root '.venv\Scripts')
    if (@($locked).Count -gt 0) {
        Send-Say $Sink ('检测到 OmniOperator 服务正在运行：' + (@($locked) -join '、')) 'bad'
        Send-Say $Sink '请先停止全部服务：打开工作台 → 服务面板逐个停止，或直接关闭对应的命令行窗口；停止后重试安装。' 'bad'
        $Progress['s0'] = 'fail'
        $Progress['result'] = 'blocked'
        return 'blocked'
    }
    $Progress['s0'] = 'ok'
    Send-Say $Sink '无服务占用' 'ok'

    # ---------- 1/8 uv ----------
    $Progress['s1'] = 'running'
    Send-Say $Sink '1/8 检查 uv 包管理器' 'step'
    try {
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if ($uv) {
            Send-Say $Sink ('已安装：' + $uv.Source) 'ok'
        } else {
            $binDir = Join-Path $env:USERPROFILE '.local\bin'
            $zip = Join-Path $env:TEMP 'uv-x86_64-pc-windows-msvc.zip'
            $urls = Get-GhUrls $Proxy 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
            if (-not (Save-FromUrls $Sink $urls $zip)) {
                Send-Say $Sink 'uv 下载失败（GitHub 直连不畅时，勾选"走代理"重试）。' 'bad'
                Send-Say $Sink ('手动方案：浏览器下载 ' + $urls[0] + ' 解压到 %USERPROFILE%\.local\bin 后重试。') 'bad'
                $Progress['s1'] = 'fail'; $Progress['result'] = 'fail'
                return 'fail'
            }
            Expand-Archive -Path $zip -DestinationPath $binDir -Force
            Add-Path $binDir
            $uv = Get-Command uv -ErrorAction SilentlyContinue
            if (-not $uv) {
                Send-Say $Sink 'uv 解压后仍不可用，请检查 %USERPROFILE%\.local\bin 是否有 uv.exe' 'bad'
                $Progress['s1'] = 'fail'; $Progress['result'] = 'fail'
                return 'fail'
            }
            Send-Say $Sink ('已安装到 ' + $uv.Source) 'ok'
        }
        $Progress['s1'] = 'ok'
    } catch {
        Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
        $Progress['s1'] = 'fail'; $Progress['result'] = 'fail'
        return 'fail'
    }

    # ---------- 2/8 Python ----------
    $Progress['s2'] = 'running'
    Send-Say $Sink '2/8 准备 Python 3.12（uv 托管）' 'step'
    try {
        if ($Proxy) {
            # uv 的 Python 发行版也在 GitHub release 上；-Proxy 一并给它走代理。
            $env:UV_PYTHON_INSTALL_MIRROR = $Proxy.TrimEnd('/') + '/https://github.com/astral-sh/python-build-standalone/releases/download'
        }
        & uv python install 3.12
        if ($LASTEXITCODE -ne 0) {
            Send-Say $Sink 'Python 3.12 下载失败（GitHub 直连不畅时，勾选"走代理"重试）。' 'bad'
            $Progress['s2'] = 'fail'; $Progress['result'] = 'fail'
            return 'fail'
        }
        $Progress['s2'] = 'ok'
        Send-Say $Sink 'Python 3.12 就绪' 'ok'
    } catch {
        Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
        $Progress['s2'] = 'fail'; $Progress['result'] = 'fail'
        return 'fail'
    }

    # ---------- 3/8 依赖 ----------
    $Progress['s3'] = 'running'
    Send-Say $Sink '3/8 安装项目依赖（uv sync --frozen）' 'step'
    try {
        if ($PypiMirror) { $env:UV_DEFAULT_INDEX = $PypiMirror }
        Push-Location $Root
        & uv sync --frozen
        $syncExit = $LASTEXITCODE
        Pop-Location
        if ($syncExit -ne 0) {
            if ($PypiMirror) {
                Send-Say $Sink '依赖同步失败（已启用镜像源），请把日志反馈给维护者。' 'bad'
            } else {
                Send-Say $Sink '依赖同步失败。国内网络可勾选 PyPI 镜像重试，或用命令行：' 'bad'
                Send-Say $Sink '  scripts\install.ps1 -PypiMirror https://pypi.tuna.tsinghua.edu.cn/simple' 'bad'
            }
            $Progress['s3'] = 'fail'; $Progress['result'] = 'fail'
            return 'fail'
        }
        $Progress['s3'] = 'ok'
        Send-Say $Sink '依赖就绪（.venv）' 'ok'
    } catch {
        Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
        $Progress['s3'] = 'fail'; $Progress['result'] = 'fail'
        return 'fail'
    }

    # ---------- 4/8 Chromium ----------
    if ($SkipChromium) {
        $Progress['s4'] = 'skip'
        Send-Say $Sink '4/8 Playwright Chromium（按选项跳过）' 'step'
    } else {
        $Progress['s4'] = 'running'
        Send-Say $Sink '4/8 安装 Playwright Chromium（浏览器能力需要）' 'step'
        try {
            & uv run --no-sync python -m playwright install chromium
            if ($LASTEXITCODE -ne 0) {
                Send-Say $Sink '官方源下载失败，改用 npmmirror 镜像重试…' 'note'
                $env:PLAYWRIGHT_DOWNLOAD_HOST = 'https://npmmirror.com/mirrors/playwright/'
                & uv run --no-sync python -m playwright install chromium
                if ($LASTEXITCODE -ne 0) {
                    Send-Say $Sink 'Chromium 安装失败：不影响工作台启动，但浏览器相关能力不可用。' 'note'
                    Send-Say $Sink '可稍后手动重试：uv run --no-sync python -m playwright install chromium' 'note'
                    $Progress['s4'] = 'warn'
                } else {
                    $Progress['s4'] = 'ok'
                    Send-Say $Sink 'Chromium 就绪（镜像源）' 'ok'
                }
            } else {
                $Progress['s4'] = 'ok'
                Send-Say $Sink 'Chromium 就绪' 'ok'
            }
        } catch {
            Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
            $Progress['s4'] = 'warn'
            Send-Say $Sink 'Chromium 步骤异常，跳过（不影响工作台启动）。' 'note'
        }
    }

    # ---------- 5/8 NapCat ----------
    Send-Say $Sink '5/8 NapCat Shell（QQ 通道需要）' 'step'
    if ($SkipNapCat) {
        $Progress['s5'] = 'skip'
        Send-Say $Sink '按选项跳过 NapCat 安装。' 'note'
    } else {
        $Progress['s5'] = 'running'
        try {
            $configPath = Join-Path $Root 'config\config.yaml'
            $configRaw = ''
            if (Test-Path $configPath) { $configRaw = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 }
            $napcatDir = ''
            if ($configRaw -and ($configRaw -match '(?m)^\s*napcat_dir:\s*"([^"]+)"')) { $napcatDir = $Matches[1] }

            if (Test-NapCatReady $napcatDir) {
                $Progress['s5'] = 'ok'
                Send-Say $Sink ('NapCat 已就绪：' + $napcatDir) 'ok'
            } else {
                $target = Join-Path $ToolsDir 'napcat'
                $zip = Join-Path $env:TEMP 'NapCat.Shell.zip'
                $ghUrl = 'https://github.com/NapNeko/NapCatQQ/releases/download/' + $NapCatVersion + '/NapCat.Shell.zip'
                if (Save-FromUrls $Sink (Get-GhUrls $Proxy $ghUrl) $zip) {
                    $hash = ''
                    try { $hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower() } catch { }
                    if ($hash -ne $script:NapCatZipSha256) {
                        Send-Say $Sink ('NapCat zip sha256 校验不符（期望 ' + $script:NapCatZipSha256 + '，实际 ' + $hash + '）。') 'note'
                        Send-Say $Sink '可能是版本变动或下载源异常：跳过安装（不影响工作台），详情见 docs/INSTALLER.md。' 'note'
                        $Progress['s5'] = 'warn'
                    } else {
                        $tmp = Join-Path $env:TEMP ('napcat-extract-' + [Guid]::NewGuid().ToString('N'))
                        Expand-Archive -Path $zip -DestinationPath $tmp -Force
                        $items = @(Get-ChildItem $tmp)
                        if (($items.Count -eq 1) -and ($items[0].PSIsContainer)) { $src = $items[0].FullName } else { $src = $tmp }
                        New-Item -ItemType Directory -Force -Path $target | Out-Null
                        Copy-Item -Path (Join-Path $src '*') -Destination $target -Force
                        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                        if (Test-Path (Join-Path $target 'napcat.bat')) {
                            $Progress['s5'] = 'ok'
                            Send-Say $Sink ('NapCat 解包完成：' + $target) 'ok'
                            if ($configRaw) {
                                $targetYaml = $target.Replace('\', '/')
                                $re = [regex]'(?m)^(\s*napcat_dir:\s*")[^"]*(")'
                                $newText = $re.Replace($configRaw, ('${1}' + $targetYaml + '${2}'), 1)
                                if ($newText -ne $configRaw) {
                                    # 无 BOM 写回：config.yaml 原本无 BOM，PyYAML 也按无 BOM 对待
                                    [System.IO.File]::WriteAllText($configPath, $newText, (New-Object System.Text.UTF8Encoding($false)))
                                    Send-Say $Sink ('config.yaml 已指向 napcat_dir: ' + $targetYaml) 'ok'
                                }
                            } else {
                                Send-Say $Sink ('未找到 config/config.yaml，请手工把 system.napcat_dir 指向：' + $target) 'note'
                            }
                        } else {
                            $Progress['s5'] = 'warn'
                            Send-Say $Sink '解包后未找到 napcat.bat：请按 docs/INSTALLER.md 的手动方案安装。' 'note'
                        }
                    }
                } else {
                    $Progress['s5'] = 'warn'
                    Send-Say $Sink 'NapCat 下载失败：QQ 通道暂不可用（不影响工作台启动）。' 'note'
                    Send-Say $Sink ('可勾选"走代理"重试；或手动下载 ' + $ghUrl + ' 解压到 ' + $target) 'note'
                }
            }
        } catch {
            Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
            $Progress['s5'] = 'warn'
            Send-Say $Sink 'NapCat 步骤异常，跳过（不影响工作台启动）。' 'note'
        }
    }
    # NapCat Shell 依赖本机已安装 QQ NT 桌面版——尽力检测，缺了只提醒不拦截。
    $qqCandidates = @()
    if ($env:LOCALAPPDATA) { $qqCandidates += (Join-Path $env:LOCALAPPDATA 'Programs\Tencent\QQNT\QQ.exe') }
    if ($env:ProgramFiles) { $qqCandidates += (Join-Path $env:ProgramFiles 'Tencent\QQNT\QQ.exe') }
    if (${env:ProgramFiles(x86)}) { $qqCandidates += (Join-Path ${env:ProgramFiles(x86)} 'Tencent\QQNT\QQ.exe') }
    $qqFound = $false
    foreach ($p in $qqCandidates) { if ($p -and (Test-Path $p)) { $qqFound = $true; break } }
    if (-not $qqFound) {
        Send-Say $Sink '未检测到 QQ NT 桌面版：NapCat 启动需要先从 im.qq.com 安装 QQ 并登录过一次。' 'note'
    }

    # ---------- 6/8 ADB ----------
    if ($SkipAdb) {
        $Progress['s6'] = 'skip'
        Send-Say $Sink '6/8 ADB platform-tools（按选项跳过）' 'step'
    } else {
        $Progress['s6'] = 'running'
        Send-Say $Sink '6/8 ADB platform-tools（安卓能力需要）' 'step'
        try {
            if (Get-Command adb -ErrorAction SilentlyContinue) {
                $Progress['s6'] = 'ok'
                Send-Say $Sink ('adb 已在 PATH：' + (Get-Command adb).Source) 'ok'
            } else {
                $zip = Join-Path $env:TEMP 'platform-tools-latest-windows.zip'
                # 腾讯云镜像为本机实测可达（与官方同文件大小）；官方源做兜底。
                $urls = @(
                    'https://mirrors.cloud.tencent.com/AndroidSDK/platform-tools-latest-windows.zip',
                    'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'
                )
                if (Save-FromUrls $Sink $urls $zip) {
                    $tmp = Join-Path $env:TEMP ('platform-tools-extract-' + [Guid]::NewGuid().ToString('N'))
                    Expand-Archive -Path $zip -DestinationPath $tmp -Force
                    $srcDir = Join-Path $tmp 'platform-tools'
                    $target = Join-Path $ToolsDir 'platform-tools'
                    if (-not (Test-Path $srcDir)) {
                        Send-Say $Sink 'zip 结构异常（缺少 platform-tools 顶层目录），跳过安装。' 'note'
                        $Progress['s6'] = 'warn'
                    } else {
                        if (Test-Path $target) { Remove-Item $target -Recurse -Force }
                        Move-Item -Path $srcDir -Destination $target
                        if (Test-Path (Join-Path $target 'adb.exe')) {
                            Add-Path $target
                            $Progress['s6'] = 'ok'
                            Send-Say $Sink ('adb 安装到 ' + $target + '（新开的终端/服务才会继承新 PATH）') 'ok'
                        } else {
                            $Progress['s6'] = 'warn'
                            Send-Say $Sink '解包后未找到 adb.exe，请按 docs/INSTALLER.md 手动安装。' 'note'
                        }
                    }
                    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                } else {
                    $Progress['s6'] = 'warn'
                    Send-Say $Sink 'adb 下载失败：安卓能力暂不可用（不影响工作台启动）。手动方案见 docs/INSTALLER.md。' 'note'
                }
            }
        } catch {
            Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
            $Progress['s6'] = 'warn'
            Send-Say $Sink 'ADB 步骤异常，跳过（不影响工作台启动）。' 'note'
        }
    }

    # ---------- 7/8 .env ----------
    $Progress['s7'] = 'running'
    Send-Say $Sink '7/8 密钥文件 .env' 'step'
    try {
        $envFile = Join-Path $Root '.env'
        if (Test-Path $envFile) {
            Send-Say $Sink '.env 已存在，保留不动。' 'ok'
        } elseif (Test-Path (Join-Path $Root '.env.example')) {
            Copy-Item (Join-Path $Root '.env.example') $envFile
            Send-Say $Sink '已从 .env.example 生成 .env；模型密钥也可以之后在工作台「设置中心」里填。' 'ok'
        } else {
            Send-Say $Sink '未找到 .env.example，跳过（不影响工作台启动）。' 'note'
        }
        $Progress['s7'] = 'ok'
    } catch {
        Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
        $Progress['s7'] = 'fail'; $Progress['result'] = 'fail'
        return 'fail'
    }

    # ---------- 8/8 体检 ----------
    $Progress['s8'] = 'running'
    Send-Say $Sink '8/8 安装体检（只读 doctor）' 'step'
    try {
        Push-Location $Root
        & uv run --no-sync python -m omni.channels.doctor
        $doctorExit = $LASTEXITCODE
        Pop-Location
        if ($doctorExit -ne 0) {
            Send-Say $Sink 'doctor 报告存在 error 级问题：请把日志反馈给维护者。' 'bad'
            $Progress['s8'] = 'fail'; $Progress['result'] = 'fail'
            return 'fail'
        }
        $Progress['s8'] = 'ok'
        Send-Say $Sink '体检通过（warning 项代表可选组件缺失，可按需补齐）。' 'ok'
    } catch {
        Send-Say $Sink ('步骤异常：' + $_.Exception.Message) 'bad'
        $Progress['s8'] = 'fail'; $Progress['result'] = 'fail'
        return 'fail'
    }

    $Progress['result'] = 'ok'
    return 'ok'
}

function Start-Workbench {
    # 启动工作台（新窗口）并自动打开未过期的配对链接。返回 'ok' / 'fail'。
    param($Sink, [string]$Root)

    if (-not (Test-Path (Join-Path $Root '.venv'))) {
        Send-Say $Sink '未检测到 .venv：请先完成安装。' 'bad'
        return 'fail'
    }

    $port = 8000
    if ($env:OMNI_WEB_PORT) { $port = [int]$env:OMNI_WEB_PORT }

    if (Test-PortListening $port) {
        Send-Say $Sink ('工作台已在运行（端口 ' + $port + '）。') 'info'
        $url = Get-FreshPairUrl $Root
        if ($url) {
            Send-Say $Sink ('打开配对链接：' + $url) 'info'
            Start-Process $url
        } else {
            Send-Say $Sink '配对链接已过期或缺失：请在工作台窗口里找最新打印的 [工作台] 配对链接，在浏览器打开。' 'note'
        }
        return 'ok'
    }

    Send-Say $Sink '启动工作台（新窗口；关闭该窗口即停止服务）…' 'info'
    $banner = "`$Host.UI.RawUI.WindowTitle = 'OmniOperator 工作台服务（保持开启）'; "
    $banner = $banner + "Write-Host '====================================================' -ForegroundColor Yellow; "
    $banner = $banner + "Write-Host ' 这是 OmniOperator 工作台服务窗口' -ForegroundColor Yellow; "
    $banner = $banner + "Write-Host ' 保持此窗口开启——关闭本窗口 = 停止工作台' -ForegroundColor Yellow; "
    $banner = $banner + "Write-Host ' 下方的配对链接用于进入工作台（正常会自动打开浏览器）' -ForegroundColor Yellow; "
    $banner = $banner + "Write-Host '====================================================' -ForegroundColor Yellow; "
    $inner = $banner + "Set-Location -LiteralPath '$Root'; `$env:PYTHONUTF8='1'; uv run --no-sync omni-web"
    Start-Process powershell.exe -ArgumentList ('-NoExit -ExecutionPolicy Bypass -Command "' + $inner + '"')

    Send-Say $Sink '等待配对链接生成（最多 90 秒）…' 'info'
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 800
        $url = Get-FreshPairUrl $Root
        if ($url) {
            Send-Say $Sink ('打开配对链接：' + $url) 'info'
            Start-Process $url
            return 'ok'
        }
    }

    if (Test-PortListening $port) {
        Send-Say $Sink '端口已监听，但工作台身份、就绪状态或当前配对记录未验证，不能报告启动成功。' 'bad'
        return 'fail'
    }
    Send-Say $Sink '工作台可能启动失败：请查看刚打开窗口里的报错（常见：安装未完成、端口被占用）。' 'bad'
    return 'fail'
}
