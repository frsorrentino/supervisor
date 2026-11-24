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

function SubagentsOf($cdPath, $e) {
    # 23/11/2026 (supervisor-sottoagenti-23-11): i sottoagenti della sessione, come summary di cm-subagents.py:
    # {n, running, at, items (al piu' 3: in corso prima, poi i piu' recenti), workflows}. Ogni sottoagente sta in
    # <projects>\<slug>\<sid>\subagents\agent-<id>.meta.json + .jsonl; quelli di un Workflow in
    # subagents\workflows\wf_*\, finiti quando journal.jsonl ha la loro riga result. Inizio = creazione della
    # trascrizione, fine = la sua ultima scrittura; le trascrizioni si aprono solo per i token delle voci mostrate.
    try {
        $slug = ([string]$e.cwd) -replace '[^A-Za-z0-9]', '-'
        $sub = Join-Path (Join-Path (Join-Path (Join-Path $cdPath 'projects') $slug) ([string]$e.sessionId)) 'subagents'
    } catch { return $null }
    if (-not (Test-Path -LiteralPath $sub)) { return $null }
    $rows = New-Object System.Collections.ArrayList
    foreach ($mp in @(Get-ChildItem -LiteralPath $sub -Filter 'agent-*.meta.json' -File -ErrorAction SilentlyContinue)) {
        $meta = ReadJson $mp.FullName
        [void]$rows.Add(@{ mp = $mp; meta = $meta; wf = $null; done = [bool]($meta -and $meta.finished -eq $true) })
    }
    $wfs = New-Object System.Collections.ArrayList
    foreach ($wd in @(Get-ChildItem -LiteralPath (Join-Path $sub 'workflows') -Directory -Filter 'wf_*' -ErrorAction SilentlyContinue)) {
        $results = @{}
        try {
            foreach ($l in [IO.File]::ReadLines((Join-Path $wd.FullName 'journal.jsonl'))) {
                try { $j = $l | ConvertFrom-Json } catch { continue }
                if ($j.agentId -and $j.type -eq 'result') { $results[[string]$j.agentId] = $true }
            }
        } catch {}
        $w = @{ id = $wd.Name; n = 0; running = 0 }
        foreach ($mp in @(Get-ChildItem -LiteralPath $wd.FullName -Filter 'agent-*.meta.json' -File -ErrorAction SilentlyContinue)) {
            $aid = $mp.Name.Substring(6, $mp.Name.Length - 6 - 10)
            $done = [bool]$results[$aid]
            [void]$rows.Add(@{ mp = $mp; meta = (ReadJson $mp.FullName); wf = $wd.Name; done = $done })
            $w.n++; if (-not $done) { $w.running++ }
        }
        [void]$wfs.Add($w)
    }
    if ($rows.Count -eq 0) { return $null }
    foreach ($r in $rows) {
        $r.jp = Join-Path $r.mp.DirectoryName ($r.mp.Name.Substring(0, $r.mp.Name.Length - 10) + '.jsonl')
        $r.jf = Get-Item -LiteralPath $r.jp -ErrorAction SilentlyContinue
        if ($r.jf) { $r.start = EpochOf $r.jf.CreationTimeUtc } else { $r.start = EpochOf $r.mp.LastWriteTimeUtc }
    }
    $ordered = @($rows | Sort-Object -Property @{ Expression = { [bool]$_.done } }, @{ Expression = { $_.start }; Descending = $true })
    $items = New-Object System.Collections.ArrayList
    foreach ($r in @($ordered | Select-Object -First 3)) {
        $m = $r.meta
        $it = @{ id = $r.mp.Name.Substring(6, $r.mp.Name.Length - 6 - 10); type = '?'; desc = ''; model = $null; bg = $false; depth = 1
                 started = $r.start; ended = $null; finished = $r.done; tokens = $null }
        if ($m) {
            if ($m.agentType) { $it.type = [string]$m.agentType }
            $desc = [string]$m.description; if ($desc.Length -gt 80) { $desc = $desc.Substring(0, 80) }; $it.desc = $desc
            if ($m.model) { $it.model = [string]$m.model }
            $it.bg = ($m.requestShape -eq 'background')
            if ($m.spawnDepth) { $it.depth = [int]$m.spawnDepth }
            if ($r.wf -and $m.workflowPhase) { $it.phase = [string]$m.workflowPhase }
        }
        if ($r.wf) { $it.wf = $r.wf }
        if ($r.jf) {
            if ($r.done) { $it.ended = EpochOf $r.jf.LastWriteTimeUtc }
            $tk = SubTokens $r.jp
            if ($tk) { $it.tokens = @{ in = $tk.in; out = $tk.out }; if ($tk.model) { $it.model = $tk.model } }
        }
        [void]$items.Add($it)
    }
    $at = $null; foreach ($r in $rows) { if ($null -eq $at -or $r.start -gt $at) { $at = $r.start } }
    $out = @{ n = $rows.Count; running = @($rows | Where-Object { -not $_.done }).Count; at = $at; items = @($items) }
    if ($wfs.Count -gt 0) { $out.workflows = @($wfs) }
    $out
}

$ShellNames = @('bash.exe', 'sh.exe', 'zsh.exe', 'cmd.exe', 'powershell.exe', 'pwsh.exe', 'wsl.exe')
function ShellKids($procId) {
    # 23/11/2026 (supervisor-chiusura-remota-23-11): the shells started by the claude process of a session, each
    # {pid, name, cmd (at most 600 chars)}. The relay decides which ones are background work (the Bash and PowerShell
    # tool shells); the status bar and the MCP servers are shells too. One process list per call.
    if ($null -eq $script:Procs) {
        $script:Procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Select-Object ProcessId, ParentProcessId, Name, CommandLine)
    }
    $out = New-Object System.Collections.ArrayList
    foreach ($p in $script:Procs) {
        if ([int]$p.ParentProcessId -ne [int]$procId) { continue }
        $n = ([string]$p.Name).ToLower()
        if ($ShellNames -notcontains $n) { continue }
        $c = [string]$p.CommandLine
        if ($c.Length -gt 600) { $c = $c.Substring(0, 600) }
        [void]$out.Add(@{ pid = [int]$p.ProcessId; name = $n; cmd = $c })
    }
    , @($out)
}

function Cmd-Status {
    $script:Procs = $null
    $os = Get-CimInstance Win32_OperatingSystem
    $jobs = @{}
    $jd = Join-Path $Root 'jobs'
    if (Test-Path $jd) { Get-ChildItem -Path $jd -Directory | ForEach-Object { $jobs[$_.Name] = JobInfo $_.FullName } }
    $sess = New-Object System.Collections.ArrayList
    Get-ChildItem -Path $env:USERPROFILE -Directory -Force -Filter '.claude*' -ErrorAction SilentlyContinue | ForEach-Object {
        $cd = $_
        Get-ChildItem -Path (Join-Path $cd.FullName 'sessions') -Filter '*.json' -File -ErrorAction SilentlyContinue | ForEach-Object {
            $e = ReadJson $_.FullName
            if ($e) {
                $alive = [bool](Get-Process -Id ([int]$_.BaseName) -ErrorAction SilentlyContinue)
                $item = @{ config_dir = $cd.Name; alive = $alive; entry = $e }
                if ($alive) {
                    $api = ApiState $cd.FullName $e; if ($api) { $item.api = $api }
                    try { $sa = SubagentsOf $cd.FullName $e; if ($sa) { $item.subagents = $sa } } catch {}
                    try { $item.shells = ShellKids ([int]$_.BaseName) } catch {}
                }
                [void]$sess.Add($item)
            }
        }
    }
    $quota = New-Object System.Collections.ArrayList
    # 06/11/2026: la quota di ogni account, nella cartella fable-director della sua config (il nome del file dice quale)
    Get-ChildItem -Path $env:USERPROFILE -Directory -Force -Filter '.claude*' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -Path (Join-Path $_.FullName 'fable-director') -Filter 'quota-*.json' -File -ErrorAction SilentlyContinue } |
        Where-Object { $_.Name -notlike '*history*' } | ForEach-Object {
            $e = ReadJson $_.FullName
            if ($e) { [void]$quota.Add(@{ file = $_.Name; mtime = [int][double]::Parse(($_.LastWriteTimeUtc - [datetime]'1970-01-01').TotalSeconds.ToString([Globalization.CultureInfo]::InvariantCulture), [Globalization.CultureInfo]::InvariantCulture); data = $e }) }
        }
    $threads = [int]((Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
    Out-J @{ ok = $true; at = [int][double]::Parse(((Get-Date).ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds.ToString([Globalization.CultureInfo]::InvariantCulture), [Globalization.CultureInfo]::InvariantCulture)
        load_pct = LoadPct; threads = $threads
        ram_gb = Gb ([double]$os.TotalVisibleMemorySize * 1KB); ram_free_gb = Gb ([double]$os.FreePhysicalMemory * 1KB)
        disk_free_gb = FreeGb $Root; jobs = $jobs; sessions = @($sess); quota = @($quota)
        ram_total_b = [int64]([double]$os.TotalVisibleMemorySize * 1KB); ram_free_b = [int64]([double]$os.FreePhysicalMemory * 1KB)
        disk = DiskOf $(if ($Rest.Count -gt 0 -and $Rest[0]) { $Rest[0] -replace '/', '\' } else { $Root }) }
}

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
