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

function Cmd-Prepare {
    $d = JobDir $Rest[0]
    foreach ($x in @($d, (Join-Path $Root 'cache\assets'), (Join-Path $Root 'cache\deps'))) { New-Item -ItemType Directory -Force -Path $x | Out-Null }
    Out-J @{ ok = $true; dir = $d; free_gb = FreeGb $d; sep = '\' }
}

function Manifest($d) {
    $f = Join-Path $d 'manifest.txt'
    if (-not (Test-Path $f)) { return @() }
    @(Get-Content -LiteralPath $f -Encoding UTF8 | Where-Object { $_ } | ForEach-Object { $p = $_ -split "`t", 3; @{ sha = $p[0]; size = $p[1]; path = $p[2] } })
}

function Cmd-Missing {
    $d = JobDir $Rest[0]
    if (-not (Test-Path (Join-Path $d 'manifest.txt'))) { Fail 'manifest missing' }
    $ca = Join-Path $Root 'cache\assets'
    $m = @(Manifest $d | Where-Object { -not (Test-Path (Join-Path $ca $_.sha)) } | ForEach-Object { $_.sha })
    Out-J @{ ok = $true; missing = $m }
}

function Tar($argList) {
    $o = cmd /c "tar.exe $argList 2>&1"
    if ($LASTEXITCODE -ne 0) { Fail "tar $argList : $($o -join ' ')" }
}

function Cmd-Unpack {
    $d = JobDir $Rest[0]; Set-Location $d
    Tar '-xf bundle.tar'
    New-Item -ItemType Directory -Force -Path (Join-Path $d 'src') | Out-Null
    Tar '-xf src.tar -C src'
    $ca = Join-Path $Root 'cache\assets'
    if (Test-Path (Join-Path $d 'assets.tar')) { Tar "-xf assets.tar -C `"$ca`"" }
    $n = 0
    foreach ($e in (Manifest $d)) {
        $srcf = Join-Path $ca $e.sha
        if (-not (Test-Path $srcf)) { Fail "asset $($e.path) not in cache" }
        $dst = Join-Path (Join-Path $d 'src') ($e.path -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        if (Test-Path $dst) { Remove-Item -Force $dst }
        try { New-Item -ItemType HardLink -Path $dst -Target $srcf -ErrorAction Stop | Out-Null } catch { Copy-Item -LiteralPath $srcf -Destination $dst }
        $n++
    }
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $d 'bundle.tar'), (Join-Path $d 'src.tar'), (Join-Path $d 'assets.tar')
    Out-J @{ ok = $true; linked = $n }
}

function Write-Status($d, $state, $procId, $rc) {
    $o = @{ state = $state; pid = $procId; rc = $rc; heartbeat = NowIso; started = $script:Started }
    $t = Join-Path $d 'status.json.tmp'
    [IO.File]::WriteAllText($t, (ConvertTo-Json -InputObject $o -Compress))
    Move-Item -Force $t (Join-Path $d 'status.json')
}

# il comando va in un .cmd e si lancia `cmd /d /c file >> log 2>&1`: accodato a un comando composto (a & b > f), il
# rinvio del log varrebbe solo per l'ultimo e ne scavalcherebbe il suo (visto il 27/09/2026)
function Start-Cmd($d, $name, $cmd, $wd, $log, [switch]$Wait) {
    $bat = Join-Path $d "$name.cmd"
    [IO.File]::WriteAllText($bat, "@echo off`r`n$cmd`r`n", (New-Object Text.UTF8Encoding($false)))
    $a = @('/d', '/s', '/c', "`"`"$bat`" >> `"$log`" 2>&1`"")
    if ($Wait) { return Start-Process -FilePath 'cmd.exe' -ArgumentList $a -WorkingDirectory $wd -WindowStyle Hidden -PassThru -Wait }
    Start-Process -FilePath 'cmd.exe' -ArgumentList $a -WorkingDirectory $wd -WindowStyle Hidden -PassThru
}

function Kill-Tree($procId) { if ($procId) { cmd /c "taskkill /T /F /PID $procId >nul 2>&1" } }

function Cmd-RunJob {
    $d = JobDir $Rest[0]; Set-Location $d
    $script:Started = NowIso
    [IO.File]::WriteAllText((Join-Path $d 'wrapper.pid'), "$PID")
    Write-Status $d 'starting' $PID $null
    $log = Join-Path $d 'log.txt'
    $srcd = Join-Path $d 'src'
    # env.txt: the job's own environment, one NAME=VALUE per line, written by cm-offload from the job's "env".
    # The name is what comes before the first "=", the value everything after it.
    if (Test-Path (Join-Path $d 'env.txt')) {
        foreach ($line in @(Get-Content -LiteralPath (Join-Path $d 'env.txt') -Encoding UTF8)) {
            $at = $line.IndexOf('=')
            if ($at -lt 0) { continue }
            $name = $line.Substring(0, $at)
            $value = $line.Substring($at + 1)
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
    # deps.txt has three lines, written by cm-offload: key (the lockfile hash), dir (the folder the command
    # fills), cmd (the command the job's own spec gives).
    $df = Join-Path $d 'deps.txt'
    if (Test-Path $df) {
        $depsLines = @(Get-Content -LiteralPath $df -Encoding UTF8)
        $depsKey = DepsField $depsLines 'key'
        $depsDir = DepsField $depsLines 'dir'
        $depsCmd = DepsField $depsLines 'cmd'
        $cache = Join-Path (Join-Path $Root 'cache\deps') $depsKey
        $ddir = $depsDir -replace '/', '\'
        if (-not (Test-Path (Join-Path $cache '.ok'))) {
            Write-Status $d 'deps' $PID $null
            $p = Start-Cmd $d 'deps' $depsCmd $srcd $log -Wait
            if ($p.ExitCode -ne 0) { Write-Status $d 'failed' $PID 90; exit 90 }
            New-Item -ItemType Directory -Force -Path $cache | Out-Null
            $tgt = Join-Path $cache $ddir
            if (Test-Path $tgt) { Remove-Item -Recurse -Force $tgt }
            Move-Item -LiteralPath (Join-Path $srcd $ddir) -Destination $tgt
            [IO.File]::WriteAllText((Join-Path $cache '.ok'), (NowIso))
        }
        $lnk = Join-Path $srcd $ddir
        if (-not (Test-Path $lnk)) { New-Item -ItemType Junction -Path $lnk -Target (Join-Path $cache $ddir) | Out-Null }
    }
    $to = 0; if (Test-Path (Join-Path $d 'timeout.txt')) { $to = [int](Get-Content (Join-Path $d 'timeout.txt') -Raw) }
    $cmd = [IO.File]::ReadAllText((Join-Path $d 'cmd.txt')).Trim()
    $p = Start-Cmd $d 'job' $cmd $srcd $log
    $h = $p.Handle
    [IO.File]::WriteAllText((Join-Path $d 'child.pid'), "$($p.Id)")
    Write-Status $d 'running' $p.Id $null
    $el = 0
    while (-not $p.HasExited) {
        Start-Sleep -Seconds 5; $el += 5
        if ($el % 15 -eq 0) { Write-Status $d 'running' $p.Id $null }
        if ($to -gt 0 -and $el -ge $to) { Add-Content -LiteralPath $log "[supervisor] timeout ${to}s"; Kill-Tree $p.Id }
    }
    $p.WaitForExit()
    $rc = $p.ExitCode
    if (Test-Path (Join-Path $d 'cancelled')) { Write-Status $d 'cancelled' $p.Id $rc }
    elseif ($rc -eq 0) { Write-Status $d 'done' $p.Id 0 } else { Write-Status $d 'failed' $p.Id $rc }
    Unregister-ScheduledTask -TaskPath '\supervisor\' -TaskName "cm-job-$($Rest[0])" -Confirm:$false -ErrorAction SilentlyContinue
}

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
