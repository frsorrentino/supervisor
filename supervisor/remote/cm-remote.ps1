# supervisor - aiutante remoto per host windows-native (PowerShell 5.1+). Solo ASCII: PowerShell 5.1 legge un
# .ps1 senza BOM come ANSI.
#
#   powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File cm-remote.ps1 ROOT VERB [ARG...]
#
# Stessi verbi e stesso protocollo di cm-remote.sh (sentinella, poi UNA riga JSON sullo stdout). Note del piano
# multi-PC (2.2, spike 1.0 del 27/09/2026):
# - un figlio di sshd muore con la sessione ssh (job object): detach passa da un'attivita' pianificata una tantum
#   S4U, senza finestra, che sopravvive alla caduta del trasporto; Win32_Process.Create e' il ripiego;
# - i file viaggiano solo con scp, mai sullo stdin;
# - $ProgressPreference zittisce i blocchi CLIXML di avanzamento; chi chiama legge solo lo stdout dopo la sentinella.
param([string]$Root, [string]$Verb, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) } catch {}
if (-not $Rest) { $Rest = @() }
$Root = [Environment]::ExpandEnvironmentVariables($Root)
if ($Root -eq '~' -or $Root.StartsWith('~/') -or $Root.StartsWith('~\')) { $Root = $env:USERPROFILE + $Root.Substring(1) }
$Root = $Root -replace '/', '\'
$SENT = '@@CM-JSON@@'
$Self = $MyInvocation.MyCommand.Path
$Here = Split-Path -Parent $Self

function Cmd-Detach {
    $id = $Rest[0]; $d = JobDir $id
    if (-not (Test-Path (Join-Path $d 'cmd.txt'))) { Fail 'cmd.txt missing' }
    $arg = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Self`" `"$Root`" run-job $id"
    $name = "cm-job-$id"
    try {
        $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $d
        $pr = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
        $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -Priority 5 -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $name -TaskPath '\supervisor\' -Action $a -Principal $pr -Settings $s -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskPath '\supervisor\' -TaskName $name -ErrorAction Stop
        Out-J @{ ok = $true; id = "task:$name" }; return
    } catch { $why = "$_" }
    # ripiego: il processo nasce dal servizio WMI, fuori dal job object di sshd
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "powershell.exe $arg"; CurrentDirectory = $d }
    if ($r.ReturnValue -ne 0) { Fail "detach: task ($why), Win32_Process.Create rc $($r.ReturnValue)" }
    Out-J @{ ok = $true; id = "pid:$($r.ProcessId)"; fallback = $why }
}

function Cmd-Pack {
    $id = $Rest[0]; $d = JobDir $id; $srcd = Join-Path $d 'src'; Set-Location $srcd
    $paths = @($Rest | Select-Object -Skip 1 | Where-Object { $_ -and -not $_.StartsWith('/') -and $_ -notmatch '\.\.' -and (Test-Path (Join-Path $srcd $_)) })
    if (-not $paths) { Fail 'no output' }
    $list = ($paths | ForEach-Object { "`"$_`"" }) -join ' '
    Tar "-cf ..\out.tar $list"
    # un file o una cartella per percorso: Get-ChildItem -Recurse su un file restituisce il file stesso, quindi un ramo solo
    $files = @(foreach ($p in $paths) {
        Get-ChildItem -LiteralPath (Join-Path $srcd $p) -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $rel = $_.FullName.Substring($srcd.Length + 1) -replace '\\', '/'
            @{ path = $rel; sha256 = Sha $_.FullName; size = $_.Length } }
    })
    Out-J @{ ok = $true; tar = (Join-Path $d 'out.tar'); files = $files }
}

function Cmd-Cancel {
    $id = $Rest[0]; $d = JobDir $id
    if (-not (Test-Path $d)) { Fail 'no job' }
    [IO.File]::WriteAllText((Join-Path $d 'cancelled'), (NowIso))
    Stop-ScheduledTask -TaskPath '\supervisor\' -TaskName "cm-job-$id" -ErrorAction SilentlyContinue
    foreach ($f in 'child.pid', 'wrapper.pid') { $pf = Join-Path $d $f; if (Test-Path $pf) { Kill-Tree ([IO.File]::ReadAllText($pf).Trim()) } }
    Start-Sleep -Seconds 1
    Write-Status $d 'cancelled' $null $null
    Unregister-ScheduledTask -TaskPath '\supervisor\' -TaskName "cm-job-$id" -Confirm:$false -ErrorAction SilentlyContinue
    Out-J @{ ok = $true }
}

function Cmd-Clean {
    if ($Rest[0] -eq '--older') {
        $days = 7; if ($Rest.Count -gt 1) { $days = [int]$Rest[1] }
        $n = 0
        Get-ChildItem -Path (Join-Path $Root 'jobs') -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $sf = Join-Path $_.FullName 'status.json'; $st = $null; if (Test-Path $sf) { $st = ReadJson $sf }
            if ($st -and $st.state -in @('done', 'failed', 'cancelled') -and (Get-Item $sf).LastWriteTime -lt (Get-Date).AddDays(-$days)) {
                Remove-Item -Recurse -Force $_.FullName; $n++ }
        }
        Out-J @{ ok = $true; removed = $n }; return
    }
    $id = $Rest[0]; $d = JobDir $id
    $sf = Join-Path $d 'status.json'
    if (Test-Path $sf) {
        $st = ReadJson $sf
        $wp = Join-Path $d 'wrapper.pid'
        if ($st -and $st.state -in @('running', 'deps', 'starting') -and (Test-Path $wp) -and (Get-Process -Id ([int][IO.File]::ReadAllText($wp).Trim()) -ErrorAction SilentlyContinue)) { Fail 'job running' }
    }
    Unregister-ScheduledTask -TaskPath '\supervisor\' -TaskName "cm-job-$id" -Confirm:$false -ErrorAction SilentlyContinue
    # le junction delle dipendenze si tolgono da sole, senza entrare nella cache a cui puntano
    Get-ChildItem -Path $d -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue | ForEach-Object { cmd /c "rmdir `"$($_.FullName)`" 2>nul" }
    if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
    if (Test-Path $d) { Fail "rm $d" }
    Out-J @{ ok = $true }
}

switch ($Verb) {
    'version' {
        $v = @{}; foreach ($f in 'cm-remote.ps1', 'cm-bench.js', 'cm-webgl.html') { $p = Join-Path $Here $f; if (Test-Path $p) { $v[$f] = Sha $p } }
        Out-J @{ ok = $true; files = $v }
    }
    'probe' { Cmd-Probe }
    'status' { Cmd-Status }
    'prepare' { Cmd-Prepare }
    'missing' { Cmd-Missing }
    'unpack' { Cmd-Unpack }
    'detach' { Cmd-Detach }
    'run-job' { Cmd-RunJob }
    'pack' { Cmd-Pack }
    'cancel' { Cmd-Cancel }
    'clean' { Cmd-Clean }
    default { Fail "unknown verb $Verb" }
}
