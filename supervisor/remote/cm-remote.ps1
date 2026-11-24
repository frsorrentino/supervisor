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
