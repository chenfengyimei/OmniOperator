# Fixed V2 UIA entry. The owning process enforces approval, time, pipes and Job cleanup.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Write-TestPhase($phase) {
    if ($env:OMNI_TEST_OFFLINE -eq '1') { [Console]::Error.WriteLine("omni-uia:$phase") }
}
Write-TestPhase 'startup'
# Own the encoding of the redirected pipes without changing a console code page.
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$stdin = [IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8, $false)
$stdout = [IO.StreamWriter]::new([Console]::OpenStandardOutput(), $utf8)
Write-TestPhase 'pipes'
$request = $null
try {
    $buffer = [char[]]::new(65537)
    $length = 0
    Write-TestPhase 'reading'
    while ($length -lt $buffer.Length) {
        $value = $stdin.Read()
        if ($value -lt 0) { break }
        $buffer[$length++] = [char]$value
        if ($value -eq 10) { break }
    }
    $line = [string]::new($buffer, 0, $length)
    if ($length -eq 0 -or $utf8.GetByteCount($line) -gt 65536 -or -not $line.EndsWith("`n")) { exit 2 }
    Write-TestPhase 'frame'
    # Use the fixed Windows PowerShell utility module, without user module discovery.
    $moduleRoot = [IO.Path]::Combine($PSHOME, 'Modules')
    $env:PSModulePath = $moduleRoot
    $PSModuleAutoLoadingPreference = 'None'
    Import-Module -Name ([IO.Path]::Combine($moduleRoot, 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1'))
    Write-TestPhase 'utility-loaded'
    $request = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $line
    if ($request.version -ne 'omni-desktop-ipc-6' -or $request.operation -notin @('observe','capture','act','foreground') -or $request.limit -ne 1 -or $request.target.platform -ne 'win32') { exit 2 }
    $identity = [guid]::ParseExact($request.request_id, 'D')
    Write-TestPhase 'request'
    Microsoft.PowerShell.Utility\Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,WindowsBase,System.Drawing,System.Numerics
    $sources = @([IO.Path]::Combine($PSScriptRoot, 'windows_uia.cs'))
    if ($request.operation -eq 'foreground') {
        $sources += [IO.Path]::Combine($PSScriptRoot, 'windows_foreground.cs')
    }
    Microsoft.PowerShell.Utility\Add-Type -Path $sources -ReferencedAssemblies UIAutomationClient,UIAutomationTypes,WindowsBase,System.Core,System.Drawing,System.Numerics
    Write-TestPhase 'compiled'
    $t = $request.target
    if ($request.operation -eq 'foreground') {
        $a = $request.action
        if ($a.policy -ne 'desktop-foreground-1' -or $a.takeover -ne $true) { exit 2 }
        $authorize = [Func[bool]] {
            $ready = [ordered]@{version=$request.version;request_id=$request.request_id;operation='foreground';phase='ready';grant_id=$a.grant_id;run_id=$a.run_id;operation_id=$a.operation_id}
            $stdout.Write((Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $ready -Depth 4 -Compress) + "`n")
            $stdout.Flush()
            Write-TestPhase 'ready'
            $control = [char[]]::new(1024)
            $used = 0
            while ($used -lt $control.Length) {
                $next = $stdin.Read()
                if ($next -lt 0) { return $false }
                $control[$used++] = [char]$next
                if ($next -eq 10) { break }
            }
            if ($used -eq $control.Length -or $control[$used-1] -ne [char]10) { return $false }
            try {
                $permit = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject ([string]::new($control, 0, $used))
                $expected = @('version','request_id','operation','phase','grant_id','run_id','operation_id')
                if (@($permit.PSObject.Properties).Count -ne $expected.Count -or @($permit.PSObject.Properties.Name | Where-Object { $_ -notin $expected }).Count -ne 0) { return $false }
                return ($permit.version -eq $request.version -and $permit.request_id -eq $request.request_id -and $permit.operation -eq 'foreground' -and $permit.phase -eq 'permit' -and $permit.grant_id -eq $a.grant_id -and $permit.run_id -eq $a.run_id -and $permit.operation_id -eq $a.operation_id)
            } catch { return $false }
        }
        $payload = [OmniV2Desktop.Observer]::Foreground([long]$t.native_id, [uint32]$t.pid, [string]$t.process_created, [string]$t.application_id, [string]$t.session_id, [int[]]$a.node.root, [int[]]$a.node.runtime, [string]$a.node.role, [string]$a.node.name_sha256, [string]$a.kind, [string]$a.text, $authorize)
    } elseif ($request.operation -eq 'act') {
        $a = $request.action
        if ($a.policy -ne 'desktop-semantic-1') { exit 2 }
        $payload = [OmniV2Desktop.Observer]::Act([long]$t.native_id, [uint32]$t.pid, [string]$t.process_created, [string]$t.application_id, [string]$t.session_id, [int[]]$a.node.root, [int[]]$a.node.runtime, [string]$a.node.role, [string]$a.node.name_sha256, [string]$a.kind, [string]$a.text)
    } elseif ($request.operation -eq 'capture') {
        $payload = [OmniV2Desktop.Observer]::Capture([long]$t.native_id, [uint32]$t.pid, [string]$t.process_created, [string]$t.application_id, [string]$t.session_id, [string]$request.output_path)
        $payload['target'] = $t
    } else {
        $result = [OmniV2Desktop.Observer]::Observe([long]$t.native_id, [uint32]$t.pid, [string]$t.process_created, [string]$t.application_id, [string]$t.session_id)
        $payload = @{target=$t;nodes=@($result.Nodes.ToArray());truncated=$result.Truncated}
    }
    Write-TestPhase 'native-result'
    $response = [ordered]@{version=$request.version;request_id=$request.request_id;operation=$request.operation;ok=$true;result=$payload;error_code=$null}
} catch {
    if ($null -eq $request) { exit 2 }
    $code = 'desktop_native_unavailable'
    $errorValue = $_.Exception
    while ($null -ne $errorValue.InnerException) { $errorValue = $errorValue.InnerException }
    if ($errorValue.Message -in @('desktop_target_changed','desktop_permission_denied','desktop_observation_unavailable','desktop_capture_unavailable','desktop_capture_failed','desktop_capture_protected','desktop_capture_too_large')) { $code = $errorValue.Message }
    $response = [ordered]@{version=$request.version;request_id=$request.request_id;operation=$request.operation;ok=$false;result=$null;error_code=$code}
}
try {
    $frame = (Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $response -Depth 12 -Compress) + "`n"
    while ($utf8.GetByteCount($frame) -gt 65536 -and $response.ok -and $response.result.nodes.Count -gt 1) {
        $response.result.nodes = @($response.result.nodes[0..($response.result.nodes.Count - 2)])
        $response.result.truncated = $true
        $frame = (Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $response -Depth 12 -Compress) + "`n"
    }
    if ($utf8.GetByteCount($frame) -gt 65536) { exit 2 }
    $stdout.Write($frame)
    $stdout.Flush()
    Write-TestPhase 'response'
} catch { exit 2 }
