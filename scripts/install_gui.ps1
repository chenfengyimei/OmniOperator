<#
.SYNOPSIS
  OmniOperator 图形安装器（WinForms，Windows 自带组件，零依赖；PowerShell 5.1 兼容）。

.DESCRIPTION
  双击仓库根目录 install.bat 即打开本窗口。安装逻辑在 scripts/installer_lib.ps1，
  本文件只做界面：步骤清单 + 日志 + 选项（代理 / PyPI 镜像 / 仅核心）。
  步骤在后台 Runspace 执行，UI 线程用 Timer 消费同步队列刷新，窗口不卡顿。
  预检发现服务正在运行会拦下来（blocked），停止服务后点「开始安装」重试即可。
  安装完成后点「启动工作台」：新窗口运行 omni-web 并自动打开配对链接；
  其余配置（模型密钥、QQ 白名单、B站 Cookie）全部在工作台「设置中心」完成。
#>
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

. "$PSScriptRoot\installer_lib.ps1"

$Root = $script:InstallerRoot

# 跨线程共享：Log 为同步队列（后台写、UI 读），Progress 为同步 hashtable（后台写状态）。
$sync = @{}
$sync.Log = New-SinkQueue
$sync.Progress = [hashtable]::Synchronized(@{})
$sync.StartProgress = [hashtable]::Synchronized(@{})
$sync.Busy = $false
$sync.Mode = ''
$sync.PS = $null
$sync.RS = $null
$sync.Handle = $null

$stepNames = @(
    '0  预检（服务未占用）',
    '1  uv 包管理器',
    '2  Python 3.12',
    '3  项目依赖',
    '4  Chromium 浏览器',
    '5  NapCat Shell（QQ 通道）',
    '6  ADB platform-tools（安卓）',
    '7  密钥文件 .env',
    '8  安装体检（只读 doctor）'
)
$stateText = @{ pending = '待开始'; running = '进行中…'; ok = '完成'; warn = '警告（可继续）'; fail = '失败'; skip = '跳过' }

# ---------- 控件 ----------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'OmniOperator 一键安装'
$form.ClientSize = New-Object System.Drawing.Size(780, 690)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)

$listSteps = New-Object System.Windows.Forms.ListView
$listSteps.View = [System.Windows.Forms.View]::Details
$listSteps.FullRowSelect = $true
$listSteps.HeaderStyle = [System.Windows.Forms.ColumnHeaderStyle]::Nonclickable
$listSteps.Location = New-Object System.Drawing.Point(12, 12)
$listSteps.Size = New-Object System.Drawing.Size(756, 232)
$null = $listSteps.Columns.Add('步骤', 540)
$null = $listSteps.Columns.Add('状态', 200)
foreach ($name in $stepNames) {
    $item = New-Object System.Windows.Forms.ListViewItem($name)
    $null = $item.SubItems.Add('待开始')
    $null = $listSteps.Items.Add($item)
}
$form.Controls.Add($listSteps)

$logBox = New-Object System.Windows.Forms.RichTextBox
$logBox.ReadOnly = $true
$logBox.Font = New-Object System.Drawing.Font('Consolas', 9)
$logBox.Location = New-Object System.Drawing.Point(12, 250)
$logBox.Size = New-Object System.Drawing.Size(756, 300)
$form.Controls.Add($logBox)

$chkProxy = New-Object System.Windows.Forms.CheckBox
$chkProxy.Text = 'GitHub 走 gh-proxy.com 代理（直连不畅时勾选）'
$chkProxy.Location = New-Object System.Drawing.Point(16, 560)
$chkProxy.Size = New-Object System.Drawing.Size(390, 24)
$form.Controls.Add($chkProxy)

$chkPypi = New-Object System.Windows.Forms.CheckBox
$chkPypi.Text = '依赖同步走清华 PyPI 镜像（国内加速）'
$chkPypi.Location = New-Object System.Drawing.Point(16, 586)
$chkPypi.Size = New-Object System.Drawing.Size(390, 24)
$form.Controls.Add($chkPypi)

$chkCore = New-Object System.Windows.Forms.CheckBox
$chkCore.Text = '仅装核心（跳过 Chromium / NapCat / ADB）'
$chkCore.Location = New-Object System.Drawing.Point(16, 612)
$chkCore.Size = New-Object System.Drawing.Size(390, 24)
$form.Controls.Add($chkCore)

$btnInstall = New-Object System.Windows.Forms.Button
$btnInstall.Text = '开始安装'
$btnInstall.Location = New-Object System.Drawing.Point(430, 556)
$btnInstall.Size = New-Object System.Drawing.Size(150, 34)
$form.Controls.Add($btnInstall)

$btnStart = New-Object System.Windows.Forms.Button
$btnStart.Text = '启动工作台'
$btnStart.Location = New-Object System.Drawing.Point(596, 556)
$btnStart.Size = New-Object System.Drawing.Size(160, 34)
$btnStart.Enabled = (Test-Path (Join-Path $Root '.venv'))
$form.Controls.Add($btnStart)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = '关闭'
$btnClose.Location = New-Object System.Drawing.Point(596, 598)
$btnClose.Size = New-Object System.Drawing.Size(160, 26)
$form.Controls.Add($btnClose)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = '点击「开始安装」。装好后所有配置在工作台「设置中心」完成。'
$statusLabel.Location = New-Object System.Drawing.Point(12, 648)
$statusLabel.Size = New-Object System.Drawing.Size(756, 30)
$form.Controls.Add($statusLabel)

# ---------- 后台执行 ----------
function Invoke-InstallerRun {
    param([string]$Mode)
    if ($sync.Busy) { return }
    $sync.Log = New-SinkQueue
    $libPath = $PSScriptRoot + '\installer_lib.ps1'
    $libEscaped = $libPath -replace "'", "''"

    if ($Mode -eq 'install') {
        for ($i = 0; $i -le 8; $i++) { $sync.Progress[('s' + $i)] = 'pending' }
        $sync.Progress['result'] = ''
        foreach ($row in $listSteps.Items) {
            $row.SubItems[1].Text = '待开始'
            $row.SubItems[1].ForeColor = [System.Drawing.Color]::Black
        }
        $options = @{}
        if ($chkProxy.Checked) { $options['Proxy'] = 'https://gh-proxy.com/' }
        if ($chkPypi.Checked) { $options['PypiMirror'] = 'https://pypi.tuna.tsinghua.edu.cn/simple' }
        if ($chkCore.Checked) {
            $options['SkipChromium'] = $true
            $options['SkipNapCat'] = $true
            $options['SkipAdb'] = $true
        }
        $body = "param(`$q, `$p, `$o)`n. '$libEscaped'`nInvoke-Installer -Sink `$q -Progress `$p -Options `$o"
        $arguments = @($sync.Log, $sync.Progress, $options)
    } else {
        $sync.StartProgress['result'] = ''
        $rootEscaped = $Root -replace "'", "''"
        $body = "param(`$q, `$p)`n. '$libEscaped'`n`$r = Start-Workbench -Sink `$q -Root '$rootEscaped'`n`$p['result'] = `$r"
        $arguments = @($sync.Log, $sync.StartProgress)
    }

    $rs = [runspacefactory]::CreateRunspace()
    $rs.ThreadOptions = 'ReuseThread'
    $rs.Open()
    $psInstance = [powershell]::Create()
    $psInstance.Runspace = $rs
    $null = $psInstance.AddScript($body)
    foreach ($a in $arguments) { $null = $psInstance.AddArgument($a) }
    $sync.PS = $psInstance
    $sync.RS = $rs
    $sync.Handle = $psInstance.BeginInvoke()
    $sync.Busy = $true
    $sync.Mode = $Mode

    $btnInstall.Enabled = $false
    $btnStart.Enabled = $false
    $logBox.Clear()
    if ($Mode -eq 'install') { $statusLabel.Text = '正在安装…（下载量取决于网络，请耐心等日志滚动）' }
    else { $statusLabel.Text = '正在启动工作台…' }
}

# ---------- Timer：消费日志、刷新步骤状态、收尾 ----------
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
    while ($sync.Log.Count -gt 0) {
        $item = $sync.Log.Dequeue()
        $text = [string]$item['text']
        $level = [string]$item['level']
        $color = [System.Drawing.Color]::Black
        if ($level -eq 'step') { $color = [System.Drawing.Color]::DarkBlue }
        elseif ($level -eq 'ok') { $color = [System.Drawing.Color]::Green }
        elseif ($level -eq 'note') { $color = [System.Drawing.Color]::DarkOrange }
        elseif ($level -eq 'bad') { $color = [System.Drawing.Color]::Red }
        if ($level -eq 'step') {
            $logBox.SelectionStart = $logBox.TextLength
            $logBox.SelectionLength = 0
            $logBox.SelectionColor = $logBox.ForeColor
            $logBox.AppendText("`r`n")
        }
        $logBox.SelectionStart = $logBox.TextLength
        $logBox.SelectionLength = 0
        $logBox.SelectionColor = $color
        $logBox.AppendText($text + "`r`n")
        $logBox.SelectionColor = $logBox.ForeColor
        $logBox.ScrollToCaret()
    }

    if ($sync.Mode -eq 'install') {
        for ($i = 0; $i -le 8; $i++) {
            $state = [string]$sync.Progress[('s' + $i)]
            if (-not $state) { $state = 'pending' }
            $row = $listSteps.Items[$i]
            $row.SubItems[1].Text = $stateText[$state]
            $fore = [System.Drawing.Color]::Black
            if ($state -eq 'ok') { $fore = [System.Drawing.Color]::Green }
            elseif ($state -eq 'running') { $fore = [System.Drawing.Color]::DodgerBlue }
            elseif ($state -eq 'warn') { $fore = [System.Drawing.Color]::DarkOrange }
            elseif ($state -eq 'fail') { $fore = [System.Drawing.Color]::Red }
            elseif ($state -eq 'skip') { $fore = [System.Drawing.Color]::Gray }
            $row.SubItems[1].ForeColor = $fore
        }
    }

    if ($sync.Busy) {
        $result = ''
        if ($sync.Mode -eq 'install') { $result = [string]$sync.Progress['result'] }
        else { $result = [string]$sync.StartProgress['result'] }
        if ($result) {
            $sync.Busy = $false
            if ($sync.Handle) {
                try {
                    $null = $sync.PS.EndInvoke($sync.Handle)
                } catch { }
                try { $sync.PS.Dispose() } catch { }
                try { $sync.RS.Dispose() } catch { }
                $sync.PS = $null; $sync.RS = $null; $sync.Handle = $null
            }
            $btnInstall.Enabled = $true
            if (Test-Path (Join-Path $Root '.venv')) { $btnStart.Enabled = $true }
            if ($sync.Mode -eq 'install') {
                if ($result -eq 'ok') {
                    $statusLabel.Text = '安装完成：点「启动工作台」，浏览器会自动打开配对链接；配置到「设置中心」完成。'
                    $statusLabel.ForeColor = [System.Drawing.Color]::Green
                } elseif ($result -eq 'blocked') {
                    $statusLabel.Text = '有服务正在运行占用安装目录：到工作台服务面板停止（或关掉对应窗口）后，再点「开始安装」。'
                    $statusLabel.ForeColor = [System.Drawing.Color]::DarkOrange
                } else {
                    $statusLabel.Text = '安装失败：看上方日志定位，可调整选项重试；反复失败请把日志反馈给维护者。'
                    $statusLabel.ForeColor = [System.Drawing.Color]::Red
                }
            } else {
                if ($result -eq 'ok') {
                    $statusLabel.Text = '工作台已启动：浏览器应已打开；没打开就看日志里的配对链接。'
                    $statusLabel.ForeColor = [System.Drawing.Color]::Green
                } else {
                    $statusLabel.Text = '启动失败：看上方日志（常见：安装未完成、端口被占用）。'
                    $statusLabel.ForeColor = [System.Drawing.Color]::Red
                }
            }
            $sync.Mode = ''
        }
    }
})

# ---------- 事件 ----------
$btnInstall.Add_Click({ Invoke-InstallerRun 'install' })
$btnStart.Add_Click({ Invoke-InstallerRun 'start' })
$btnClose.Add_Click({ $timer.Stop(); $form.Close() })
$form.Add_FormClosing({
    if ($sync.Busy -and $sync.PS) {
        try { $sync.PS.Stop() } catch { }
    }
    $timer.Stop()
})

$timer.Start()
$null = $form.ShowDialog()
