param(
    [Parameter(Mandatory=$true)][ValidateSet('inspect','install','uninstall','start')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$TaskName,
    [Parameter(Mandatory=$true)][string]$Definition,
    [Parameter(Mandatory=$true)][string]$ExpectedSHA256,
    [Parameter(Mandatory=$true)][string]$OwnerSID,
    [Parameter(Mandatory=$true)][string]$PlanSHA256
)
# Fixed Task Scheduler broker. Reviewed start only; no stop, arbitrary command, update or elevation.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$result = [ordered]@{
    format='omni-service-native-1'; plan_sha256=$PlanSHA256; action=$Mode
    available=$true; installed=$null; definition_present=$null; definition_matches=$null
    enabled=$null; state='unknown'; effect='none'; error_code=''
}
$dispatched = $false
$service = $null; $folder = $null; $task = $null
function Read-XML([string]$Text) {
    $settings = New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver = $null
    $settings.MaxCharactersInDocument = 65536
    $reader = [System.Xml.XmlReader]::Create((New-Object System.IO.StringReader($Text)), $settings)
    try {
        $document = New-Object System.Xml.XmlDocument
        $document.XmlResolver = $null
        $document.Load($reader)
        return ,$document
    } finally { $reader.Dispose() }
}
function Same-Definition($Actual, $Expected) {
    $ns = New-Object System.Xml.XmlNamespaceManager($Actual.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    $ens = New-Object System.Xml.XmlNamespaceManager($Expected.NameTable)
    $ens.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    if ($Actual.DocumentElement.NamespaceURI -cne 'http://schemas.microsoft.com/windows/2004/02/mit/task' -or $Actual.DocumentElement.SelectNodes('*').Count -ne 5) { return $false }
    foreach ($name in @('RegistrationInfo','Triggers','Principals','Settings','Actions')) {
        if ($Actual.SelectNodes('/t:Task/t:'+$name,$ns).Count -ne 1) { return $false }
    }
    foreach ($query in @('/t:Task/t:Actions','/t:Task/t:Actions/t:Exec','/t:Task/t:Principals/t:Principal','/t:Task/t:Triggers/t:LogonTrigger')) {
        if ($Actual.SelectNodes($query,$ns).Count -ne 1) { return $false }
    }
    if ($Actual.SelectNodes('/t:Task/t:Actions/*',$ns).Count -ne 1 -or $Actual.SelectNodes('/t:Task/t:Triggers/*',$ns).Count -ne 1) { return $false }
    foreach ($contract in @(
        @('/t:Task/t:Actions/t:Exec',3),
        @('/t:Task/t:Principals/t:Principal',3),
        @('/t:Task/t:Triggers/t:LogonTrigger',2)
    )) {
        $element = $Actual.SelectSingleNode($contract[0],$ns)
        if ($element.SelectNodes('*').Count -ne $contract[1]) { return $false }
    }
    foreach ($query in @(
        '/t:Task/t:RegistrationInfo/t:Description','/t:Task/t:RegistrationInfo/t:URI',
        '/t:Task/t:Actions/t:Exec/t:Command','/t:Task/t:Actions/t:Exec/t:Arguments','/t:Task/t:Actions/t:Exec/t:WorkingDirectory',
        '/t:Task/t:Principals/t:Principal/t:UserId','/t:Task/t:Principals/t:Principal/t:LogonType','/t:Task/t:Principals/t:Principal/t:RunLevel',
        '/t:Task/t:Triggers/t:LogonTrigger/t:Enabled','/t:Task/t:Triggers/t:LogonTrigger/t:UserId',
        '/t:Task/t:Settings/t:MultipleInstancesPolicy','/t:Task/t:Settings/t:AllowHardTerminate','/t:Task/t:Settings/t:ExecutionTimeLimit',
        '/t:Task/t:Settings/t:Enabled','/t:Task/t:Settings/t:StartWhenAvailable','/t:Task/t:Settings/t:WakeToRun'
    )) {
        $a = $Actual.SelectNodes($query,$ns); $e = $Expected.SelectNodes($query,$ens)
        if ($a.Count -ne 1 -or $e.Count -ne 1 -or $a[0].InnerText -cne $e[0].InnerText) { return $false }
    }
    foreach ($node in $Expected.SelectNodes('/t:Task/t:Settings/*',$ens)) {
        $query = '/t:Task/t:Settings/t:' + $node.LocalName
        $actualNodes = $Actual.SelectNodes($query,$ns)
        if ($actualNodes.Count -ne 1 -or $actualNodes[0].InnerText -cne $node.InnerText -or $actualNodes[0].SelectNodes('*').Count -ne 0) { return $false }
    }
    # Task Scheduler may materialize these harmless defaults during export.
    # Unrecognized settings or changed defaults remain a definition mismatch.
    $defaults = @{
        Priority='7'; RunOnlyIfIdle='false'; AllowStartOnDemand='true'
        UseUnifiedSchedulingEngine='false'; DisallowStartOnRemoteAppSession='false'
    }
    $seen = @{}
    foreach ($node in $Actual.SelectNodes('/t:Task/t:Settings/*',$ns)) {
        if ($node.NamespaceURI -cne 'http://schemas.microsoft.com/windows/2004/02/mit/task') { return $false }
        if ($seen.ContainsKey($node.LocalName)) { return $false }
        $seen[$node.LocalName]=$true
        if ($Expected.SelectNodes('/t:Task/t:Settings/t:'+$node.LocalName,$ens).Count -eq 1) { continue }
        if ($defaults.ContainsKey($node.LocalName)) {
            if ($node.InnerText -cne $defaults[$node.LocalName] -or $node.SelectNodes('*').Count -ne 0) { return $false }
        } elseif ($node.LocalName -eq 'IdleSettings') {
            $idleDefaults=@{Duration='PT10M'; WaitTimeout='PT1H'; StopOnIdleEnd='true'; RestartOnIdle='false'}
            $idleSeen=@{}
            foreach ($child in $node.SelectNodes('*')) {
                if ($child.NamespaceURI -cne 'http://schemas.microsoft.com/windows/2004/02/mit/task') { return $false }
                if (-not $idleDefaults.ContainsKey($child.LocalName) -or $idleSeen.ContainsKey($child.LocalName) -or $child.InnerText -cne $idleDefaults[$child.LocalName] -or $child.SelectNodes('*').Count -ne 0) { return $false }
                $idleSeen[$child.LocalName]=$true
            }
        } else { return $false }
    }
    if ($Actual.SelectNodes('/t:Task/t:Settings/t:RestartOnFailure',$ns).Count -ne 0) { return $false }
    if ($Actual.SelectSingleNode('/t:Task/t:Actions',$ns).GetAttribute('Context') -cne 'Owner' -or $Actual.SelectSingleNode('/t:Task/t:Principals/t:Principal',$ns).GetAttribute('id') -cne 'Owner') { return $false }
    return $true
}
function Get-TaskOrAbsent {
    try { return $folder.GetTask($TaskName) }
    catch {
        $cause = $_.Exception
        while ($null -ne $cause.InnerException) { $cause = $cause.InnerException }
        if ($cause.HResult -eq -2147024894) { return $null } # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
        throw
    }
}
function Inspect-Task {
    $script:task = Get-TaskOrAbsent
    $result.installed = $null -ne $task
    $result.definition_present = $null -ne $task
    if ($null -eq $task) {
        $result.state='stopped'; $result.definition_matches=$null; $result.enabled=$false
        return
    }
    $result.definition_matches = Same-Definition (Read-XML $task.Xml) $expected
    $result.enabled = [bool]$task.Enabled
    $instances = $null
    try {
        $instances = $task.GetInstances(0)
        if ($instances.Count -gt 0 -or [int]$task.State -in @(2,4)) { $result.state='running' }
        elseif ([int]$task.State -in @(1,3)) { $result.state='stopped' }
        else { $result.state='unknown' }
    } finally { if ($null -ne $instances) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($instances) } }
}
try {
    if ($TaskName -cnotmatch '^OmniOperator-V2-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$' -or $ExpectedSHA256 -cnotmatch '^[0-9a-f]{64}$' -or $PlanSHA256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid scope' }
    if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $OwnerSID) { throw 'Wrong owner' }
    $file = Get-Item -LiteralPath $Definition -Force
    if ($file.PSIsContainer -or $file.Length -gt 65536 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid definition' }
    $bytes = [IO.File]::ReadAllBytes($file.FullName)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($hash -cne $ExpectedSHA256) { throw 'Changed definition' }
    $xml = [Text.Encoding]::UTF8.GetString($bytes)
    $expected = Read-XML $xml
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    Inspect-Task
    if ($Mode -eq 'install') {
        if ($result.installed -ne $false) { $result.error_code='service_registration_changed' }
        else {
            $dispatched=$true
            # TASK_CREATE | TASK_IGNORE_REGISTRATION_TRIGGERS; never CREATE_OR_UPDATE.
            $created=$folder.RegisterTask($TaskName,$xml,34,$OwnerSID,$null,3,$null)
            if ($null -ne $created) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($created) }
            $result.effect='applied'
        }
    } elseif ($Mode -eq 'start') {
        if ($result.installed -ne $true -or $result.definition_matches -ne $true -or $result.enabled -ne $true -or $result.state -ne 'stopped' -or -not $task.Definition.Settings.AllowDemandStart) { $result.error_code='service_registration_changed' }
        else {
            $dispatched=$true
            $running=$task.Run($null)
            if ($null -ne $running) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($running) }
            # Run acceptance does not prove startup or readiness; owner verifies the reserved boot by HMAC.
            $result.effect='applied'
        }
    } elseif ($Mode -eq 'uninstall') {
        if ($result.installed -ne $true -or $result.definition_matches -ne $true -or $result.state -ne 'stopped') { $result.error_code='service_registration_changed' }
        else { $dispatched=$true; $folder.DeleteTask($TaskName,0); $result.effect='applied' }
    }
    if ($null -ne $task) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($task); $task=$null }
    Inspect-Task
} catch {
    # Never return exception text, native stdout or file contents.
    $result.error_code='service_native_unverified'
    $result.installed=$null; $result.definition_present=$null; $result.definition_matches=$null; $result.enabled=$null; $result.state='unknown'
    if ($dispatched) { $result.effect='unknown' }
} finally {
    foreach ($item in @($task,$folder,$service)) { if ($null -ne $item) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($item) } }
}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 4))
