$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,WindowsBase,System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class OmniWindow {
 [DllImport("user32.dll")] public static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
 [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr handle);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern bool GetUserObjectInformation(IntPtr h,int index,System.Text.StringBuilder name,int size,out int needed);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
 [StructLayout(LayoutKind.Sequential)] public struct LastInput { public uint size; public uint tick; }
 [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LastInput value);
 public static uint InputTick() { var value=new LastInput(); value.size=(uint)Marshal.SizeOf(value); if(!GetLastInputInfo(ref value)) throw new Exception("无法核验用户输入"); return value.tick; }
}
'@
[void][OmniWindow]::SetProcessDpiAwarenessContext([IntPtr](-4))
$script:startedWrite = $false
function Assert-Desktop {
    $handle = [OmniWindow]::OpenInputDesktop(0,$false,1)
    if ($handle -eq [IntPtr]::Zero) { throw '桌面被锁定或受保护，拒绝操作' }
    try {
        $buffer = New-Object System.Text.StringBuilder 256
        $needed = 0
        if (-not [OmniWindow]::GetUserObjectInformation($handle,2,$buffer,512,[ref]$needed) -or $buffer.ToString() -ne 'Default') { throw '不是普通交互桌面，拒绝操作' }
    } finally { [void][OmniWindow]::CloseDesktop($handle) }
}
function Identity($element) {
    $current = $element.Current
    $process = Get-Process -Id $current.ProcessId -ErrorAction Stop
    [ordered]@{
        hwnd=[long]$current.NativeWindowHandle; pid=$current.ProcessId
        created=$process.StartTime.ToUniversalTime().Ticks.ToString(); exe=$process.Path
        title=$current.Name; class=$current.ClassName
    }
}
function Snapshot([long]$window) {
    Assert-Desktop
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$window)
    if ($null -eq $root) { throw '窗口已退出' }
    $identity = Identity $root
    if ($request.allowed_apps -and $identity.exe -notin $request.allowed_apps) { throw '应用身份变化或不在允许范围，未读取正文' }
    $rect = $root.Current.BoundingRectangle
    $nodes = New-Object System.Collections.ArrayList
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($root)
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $script:elements = @{}
    while ($queue.Count -gt 0 -and $nodes.Count -lt [int]$request.max_nodes) {
        $element = $queue.Dequeue()
        $c = $element.Current
        $id = ($element.GetRuntimeId() -join '.')
        $value = ''; $canSet = $false; $truncated = $false; $pattern = $null
        if (-not $c.IsPassword) {
            if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)) {
                $value = $pattern.Current.Value
                $canSet = -not $pattern.Current.IsReadOnly
            } elseif ($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$pattern)) {
                $value = $pattern.DocumentRange.GetText(4001)
            }
            if ($value.Length -gt 4000) { $truncated=$true; $value=$value.Substring(0,4000) }
        }
        $invoke = $null
        $canInvoke = $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)
        $selection=$null
        $canSelect=$element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$selection)
        $selected=$false
        if ($canSelect) { $selected=$selection.Current.IsSelected }
        $bounds = $c.BoundingRectangle
        $parentId=''
        if ($element -ne $root) {
            $parent=$walker.GetParent($element)
            if ($null -ne $parent) { $parentId=($parent.GetRuntimeId() -join '.') }
        }
        [void]$nodes.Add([ordered]@{
            id=$id; parent_id=$parentId; name=$(if ($c.IsPassword) {'[密码控件]'} else {$c.Name})
            role=$c.ControlType.ProgrammaticName; automation_id=$c.AutomationId
            value=$value; truncated=$truncated; password=$c.IsPassword; enabled=$c.IsEnabled
            offscreen=$c.IsOffscreen; set_value=$canSet; invoke=$canInvoke
            select=$canSelect; selected=$selected
            bounds=@($bounds.X,$bounds.Y,$bounds.Width,$bounds.Height)
        })
        $script:elements[$id] = $element
        $child = $walker.GetFirstChild($element)
        while ($null -ne $child -and ($queue.Count+$nodes.Count) -lt [int]$request.max_nodes) {
            $queue.Enqueue($child)
            $child=$walker.GetNextSibling($child)
        }
    }
    $state=[ordered]@{
        identity=$identity; minimized=[OmniWindow]::IsIconic([IntPtr]$window)
        enabled=[OmniWindow]::IsWindowEnabled([IntPtr]$window); dpi=[OmniWindow]::GetDpiForWindow([IntPtr]$window)
        bounds=@($rect.X,$rect.Y,$rect.Width,$rect.Height); nodes=@($nodes.ToArray())
        truncated=($nodes.Count -ge [int]$request.max_nodes)
    }
    $raw = $state | ConvertTo-Json -Depth 15 -Compress
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { $fingerprint = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($raw)))).Replace('-','').ToLower() }
    finally { $hash.Dispose() }
    @{state=$state; fingerprint=$fingerprint}
}
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    Assert-Desktop
    switch ($request.command) {
        'windows' {
            $found=New-Object System.Collections.ArrayList
            $roots=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
            foreach ($item in $roots) {
                try { [void]$found.Add((Identity $item)) } catch { }
            }
            $result=@{ok=$true; windows=@($found.ToArray()); backend='Windows UIAutomation'; foreground_input=$false}
        }
        'observe' {
            $snapshot=Snapshot ([long]$request.window)
            $result=@{ok=$true; state=$snapshot.state; fingerprint=$snapshot.fingerprint}
        }
        { $_ -in @('action','foreground_action') } {
            $snapshot=Snapshot ([long]$request.window)
            if ($snapshot.fingerprint -ne $request.fingerprint) { throw '观察已变化，未执行；请重新观察' }
            if ($snapshot.state.minimized -or -not $snapshot.state.enabled) { throw '窗口最小化或被弹窗禁用，未执行' }
            $node=@($snapshot.state.nodes | Where-Object { $_.id -eq $request.element })
            if ($node.Count -ne 1 -or $node[0].password -or $node[0].truncated -or -not $node[0].enabled -or $node[0].offscreen) { throw '目标控件不可安全操作' }
            $element=$script:elements[[string]$request.element]
            $foreground = $request.command -eq 'foreground_action'
            $inputTick = [OmniWindow]::InputTick()
            if ($foreground) {
                if (-not $request.run_id) { throw '前台接管缺少当前运行身份' }
                foreach ($key in @('hwnd','pid','created','exe')) {
                    if ([string]$snapshot.state.identity[$key] -cne [string]$request.expected_identity.$key) { throw '前台接管窗口身份变化' }
                }
                $script:startedWrite=$true
                if (-not [OmniWindow]::SetForegroundWindow([IntPtr]([long]$request.window))) { throw '系统未允许前台接管，停止本次操作' }
                $element.SetFocus()
                if ([OmniWindow]::GetForegroundWindow() -ne [IntPtr]([long]$request.window) -or [OmniWindow]::InputTick() -ne $inputTick) { throw '用户输入或前台目标变化，已暂停操作' }
                $fresh=Snapshot ([long]$request.window)
                if ($fresh.fingerprint -ne $request.fingerprint) { throw '前台接管后观察变化，停止本次操作' }
            }

            $pattern=$null
            if ($request.action -eq 'set_value') {
                if (-not $node[0].set_value -or -not $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)) { throw '控件不支持后台 ValuePattern 编辑，未执行；需用户手动编辑' }
                $script:startedWrite=$true
                $pattern.SetValue([string]$request.text)
            } elseif ($request.action -eq 'invoke') {
                if (-not $node[0].invoke -or -not $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { throw '控件不支持后台 InvokePattern，未执行' }
                $script:startedWrite=$true
                $pattern.Invoke()
            } elseif ($request.action -eq 'select') {
                if (-not $node[0].select -or -not $element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)) { throw '控件不支持后台选择，未执行' }
                $script:startedWrite=$true
                $pattern.Select()
            } else { throw '未支持的动作' }
            if ($foreground -and ([OmniWindow]::InputTick() -ne $inputTick -or [OmniWindow]::GetForegroundWindow() -ne [IntPtr]([long]$request.window))) { throw '动作期间用户接管或焦点变化，效果需人工核查' }
            $after=Snapshot ([long]$request.window)
            $verified=$false
            if ($request.action -eq 'set_value') {
                $same=@($after.state.nodes | Where-Object { $_.id -eq $request.element })
                $verified=($same.Count -eq 1 -and $same[0].value -ceq [string]$request.text -and -not $same[0].truncated)
            } elseif ($request.action -eq 'select') {
                $same=@($after.state.nodes | Where-Object { $_.id -eq $request.element })
                $verified=($same.Count -eq 1 -and $same[0].selected)
            }
            $result=@{ok=$true; state=$after.state; fingerprint=$after.fingerprint; verified=$verified; dispatched=$true}
        }
        'screenshot' {
            $snapshot=Snapshot ([long]$request.window)
            if ($snapshot.fingerprint -ne $request.fingerprint -or $snapshot.state.minimized) { throw '观察变化或窗口最小化，未截图' }
            $width=[int]$snapshot.state.bounds[2]; $height=[int]$snapshot.state.bounds[3]
            if ($width -le 0 -or $height -le 0 -or $width*$height -gt 16000000) { throw '窗口截图尺寸不合法' }
            $bitmap=New-Object System.Drawing.Bitmap $width,$height
            $graphics=[System.Drawing.Graphics]::FromImage($bitmap)
            $dc=$graphics.GetHdc()
            try { $captured=[OmniWindow]::PrintWindow([IntPtr]([long]$request.window),$dc,2) }
            finally { $graphics.ReleaseHdc($dc); $graphics.Dispose() }
            try {
                if (-not $captured) { throw '此窗口不支持后台截图' }
                $stream=New-Object System.IO.MemoryStream
                try { $bitmap.Save($stream,[System.Drawing.Imaging.ImageFormat]::Png); $encoded=[Convert]::ToBase64String($stream.ToArray()) }
                finally { $stream.Dispose() }
            } finally { $bitmap.Dispose() }
            $after=Snapshot ([long]$request.window)
            if ($after.fingerprint -ne $snapshot.fingerprint) { throw '截图期间窗口变化，已丢弃图像' }
            $result=@{ok=$true; png=$encoded; fingerprint=$snapshot.fingerprint}
        }
        default { throw '未支持的命令' }
    }
    $result | ConvertTo-Json -Depth 20 -Compress
} catch {
    @{ok=$false; error=$_.Exception.Message; unknown=$script:startedWrite} | ConvertTo-Json -Compress
}
