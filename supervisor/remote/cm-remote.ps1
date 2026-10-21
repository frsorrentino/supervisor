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

function Out-J($o) { Write-Output $SENT; Write-Output (ConvertTo-Json -InputObject $o -Compress -Depth 10) }
function Fail($m) { Out-J @{ ok = $false; error = "$m" }; exit 1 }
function JobDir($id) {
    if (-not $id -or $id -match '[\\/]' -or $id.StartsWith('.')) { Fail 'bad job id' }
    Join-Path (Join-Path $Root 'jobs') $id
}
function Gb($bytes) { [math]::Round([double]$bytes / 1GB, 1) }
function FreeGb($path) {
    $p = $path; while ($p -and -not (Test-Path $p)) { $p = Split-Path -Parent $p }
    if (-not $p) { $p = $env:USERPROFILE }
    $q = (Resolve-Path $p).Path.Substring(0, 1)
    try { Gb (Get-PSDrive -Name $q).Free } catch { $null }
}
function DiskOf($path) {
    # 1.75: free and total bytes of the drive holding $path (the sessions root, D: on win)
    $p = $path; while ($p -and -not (Test-Path $p)) { $p = Split-Path -Parent $p }
    if (-not $p) { $p = $env:USERPROFILE }
    $q = (Resolve-Path $p).Path.Substring(0, 1)
    try { $d = Get-PSDrive -Name $q; @{ path = "${q}:"; free_b = [int64]$d.Free; total_b = [int64]($d.Free + $d.Used) } } catch { $null }
}
function LoadPct { try { [int]((Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average) } catch { $null } }
function ReadJson($f) {
    try { $t = [IO.File]::ReadAllText($f); if ($t.Trim().StartsWith('{') -and $t.Trim().EndsWith('}')) { return ($t | ConvertFrom-Json) } } catch {}
    $null
}
function Ver($exe, $arg) {
    # solo eseguibili veri (.exe/.cmd/.bat): un .ps1 lanciato da cmd si apre nell'editor associato e il probe si blocca
    $c = Get-Command $exe -CommandType Application -ErrorAction SilentlyContinue | Where-Object { $_.Source -match '\.(exe|cmd|bat)$' } | Select-Object -First 1
    if (-not $c -or $c.Source -like '*\WindowsApps\*') { return $null }
    try {
        $o = (cmd /c "`"$($c.Source)`" $arg 2>&1" | Select-Object -First 1) -join ''
        $m = [regex]::Match("$o", '\d+(\.\d+)+'); if ($m.Success) { return $m.Value }
    } catch {}
    $null
}
function Sha($f) { (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower() }
function NowIso { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }

function Find-Chrome {
    $c = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
           "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    $c += @(Get-ChildItem -Path (Join-Path $Root 'cache\deps') -Filter chrome-headless-shell.exe -Recurse -Depth 8 -ErrorAction SilentlyContinue | ForEach-Object FullName)
    $c += @(Get-ChildItem -Path "$env:LOCALAPPDATA\ms-playwright" -Filter chrome.exe -Recurse -Depth 4 -ErrorAction SilentlyContinue | ForEach-Object FullName)
    $c += @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
    foreach ($x in $c) { if ($x -and (Test-Path $x)) { return $x } }
    $null
}

function WebGL {
    $exe = Find-Chrome
    if (-not $exe) { return $null }
    $page = Join-Path $Here 'cm-webgl.html'
    if (-not (Test-Path $page)) { return $null }
    $ud = Join-Path $env:TEMP ('cmw' + [guid]::NewGuid().ToString('N'))
    $of = Join-Path $env:TEMP ('cmw' + [guid]::NewGuid().ToString('N') + '.txt')
    $url = 'file:///' + ($page -replace '\\', '/')
    $a = "--headless=new --ignore-gpu-blocklist --use-angle=d3d11 --no-first-run --user-data-dir=`"$ud`" --dump-dom $url"
    $p = Start-Process -FilePath $exe -ArgumentList $a -RedirectStandardOutput $of -RedirectStandardError "$of.err" -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit(30000)) { try { $p.Kill() } catch {} }
    Start-Sleep -Milliseconds 300
    $o = ''; if (Test-Path $of) { $o = [IO.File]::ReadAllText($of) }
    Remove-Item -Recurse -Force $ud, $of, "$of.err" -ErrorAction SilentlyContinue
    $m = [regex]::Match($o, 'CMWEBGL(.*)CMWEBGL')
    if (-not $m.Success) { return @{ renderer = $null; hardware = $false; ms = $null; browser = $exe } }
    $r = $m.Groups[1].Value | ConvertFrom-Json
    $hw = $false; if ($r.renderer -and $r.renderer -notmatch '(?i)swiftshader|llvmpipe|softpipe|software|basic render') { $hw = $true }
    @{ renderer = $r.renderer; hardware = $hw; ms = $r.ms; browser = $exe; error = $r.error }
}

function Cmd-Probe {
    $bench = $Rest -contains '--bench'; $webgl = $Rest -contains '--webgl'
    $os = Get-CimInstance Win32_OperatingSystem
    $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction SilentlyContinue
    $build = [int]$cv.CurrentBuild
    $caption = $os.Caption; if ($build -ge 22000) { $caption = $caption -replace 'Windows 10', 'Windows 11' }
    $pretty = "$caption $($cv.DisplayVersion) ($build)".Trim()
    $cpu = @(Get-CimInstance Win32_Processor)
    $cs = Get-CimInstance Win32_ComputerSystem
    $gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object {
        $v = $null; if ($_.AdapterRAM -gt 0) { $v = Gb ([uint32]$_.AdapterRAM) }
        $dd = $null; if ($_.DriverDate) { $dd = $_.DriverDate.ToString('yyyy-MM-dd') }
        @{ name = $_.Name; vram_gb = $v; driver = $_.DriverVersion; driver_date = $dd } })
    $accs = @(Get-ChildItem -Path $env:USERPROFILE -Directory -Force -Filter '.claude*' -ErrorAction SilentlyContinue |
              Where-Object { Test-Path (Join-Path $_.FullName '.credentials.json') } | ForEach-Object Name)
    $creds = New-Object System.Collections.ArrayList
    if ((Test-Path "$env:USERPROFILE\.git-credentials") -and (Get-Item "$env:USERPROFILE\.git-credentials").Length -gt 0) { [void]$creds.Add('~\.git-credentials') }
    if (Select-String -Path "$env:APPDATA\GitHub CLI\hosts.yml" -Pattern 'oauth_token' -Quiet -ErrorAction SilentlyContinue) { [void]$creds.Add('gh auth (%APPDATA%\GitHub CLI\hosts.yml)') }
    Get-ChildItem -Path "$env:USERPROFILE\.ssh" -Filter 'id_*' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike '*.pub' } | ForEach-Object { [void]$creds.Add("~\.ssh\$($_.Name)") }
    if (Select-String -Path "$env:USERPROFILE\.npmrc" -Pattern '_authToken' -Quiet -ErrorAction SilentlyContinue) { [void]$creds.Add('~\.npmrc (token)') }
    $envNames = @([Environment]::GetEnvironmentVariables('User').Keys) + @([Environment]::GetEnvironmentVariables('Machine').Keys)
    $envNames | Sort-Object -Unique | Where-Object { $_ -match '(^|_)(TOKEN|SECRET|KEY|PASSWORD)($|_)' -and $_ -notmatch '^(CM_|CLAUDE_CODE_)|_KEYS$' } | ForEach-Object { [void]$creds.Add("env $_") }
    $ck = (cmd /c 'cmdkey /list 2>nul') -join "`n"
    foreach ($m in [regex]::Matches($ck, '(?im)^\s*\S+:\s*(\S*git:\S+)')) { [void]$creds.Add("Credential Manager $($m.Groups[1].Value)") }
    $admin = [bool](([Security.Principal.WindowsIdentity]::GetCurrent()).Groups | Where-Object { $_.Value -eq 'S-1-5-32-544' })
    $desk = [bool](Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction SilentlyContinue |
                   Where-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User -eq $env:USERNAME })
    $power = $null
    try {
        $q = (powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) -join "`n"
        $hx = @([regex]::Matches($q, '0x[0-9a-fA-F]{8}') | ForEach-Object Value)
        if ($hx.Count -ge 2) { if ([Convert]::ToInt32($hx[-2], 16) -eq 0) { $power = 'no-sleep-on-ac' } else { $power = "sleep-on-ac-after-$([Convert]::ToInt32($hx[-2], 16))s" } }
    } catch {}
    # eta' delle firme di Defender in giorni; 65535 = mai aggiornate (Defender spento o sostituito): si riporta null e il motivo
    $def = $null; $defNote = $null
    try { $mp = Get-MpComputerStatus -ErrorAction Stop; $def = $mp.AntivirusSignatureAge; if ($def -ge 65535) { $def = $null; $defNote = 'never-updated' }; if (-not $mp.AntivirusEnabled) { $defNote = 'disabled' } } catch { $defNote = 'unavailable' }
    New-Item -ItemType Directory -Force -Path $Root -ErrorAction SilentlyContinue | Out-Null
    $w = $null; if ($webgl) { $w = WebGL }
    $b = $null
    if ($bench -and (Get-Command node -ErrorAction SilentlyContinue)) {
        $bo = (cmd /c "node `"$(Join-Path $Here 'cm-bench.js')`" `"$Root`" 2>nul") | Select-Object -Last 1
        if ("$bo".StartsWith('{')) { $b = $bo | ConvertFrom-Json }
    }
    $chrome = Find-Chrome
    $arch = $env:PROCESSOR_ARCHITECTURE; if ($arch -eq 'AMD64') { $arch = 'x86_64' } elseif ($arch -eq 'ARM64') { $arch = 'aarch64' }
    Out-J @{ ok = $true; kind = 'windows-native'; os = $pretty; os_version = "$($os.Version)"; os_build = $build; arch = $arch
        cores = [int](($cpu | Measure-Object -Property NumberOfCores -Sum).Sum); threads = [int](($cpu | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
        cpu = $cpu[0].Name.Trim(); ram_gb = Gb $cs.TotalPhysicalMemory; ram_free_gb = Gb ([double]$os.FreePhysicalMemory * 1KB)
        disk_free_gb = @{ $Root = FreeGb $Root }; gpu = $gpus; webgl = $w; bench = $b
        tools = @{ node = Ver node '--version'; ffmpeg = Ver ffmpeg '-version'; python = Ver python '--version'; git = Ver git '--version'
                   tmux = $null; claude = Ver claude '--version'; tar = Ver tar '--version'; chrome = $chrome }
        accounts = $accs; foreign_credentials = @($creds); admin = $admin; linger = $null; interactive_desktop = $desk; power = $power
        defender_signature_age_d = $def; defender_note = $defNote; home = $env:USERPROFILE; root = $Root; load_pct = LoadPct }
}

function JobInfo($d) {
    $st = $null; $sf = Join-Path $d 'status.json'; if (Test-Path $sf) { $st = ReadJson $sf }
    $tail = ''
    $lf = Join-Path $d 'log.txt'
    if (Test-Path $lf) {
        try {
            $fs = [IO.File]::Open($lf, 'Open', 'Read', 'ReadWrite'); $n = [Math]::Min(400, $fs.Length)
            [void]$fs.Seek(-$n, 'End'); $buf = New-Object byte[] $n; [void]$fs.Read($buf, 0, $n); $fs.Close()
            $tail = (([Text.Encoding]::UTF8.GetString($buf) -replace "`r", "`n") -split "`n" | Where-Object { $_ } | Select-Object -Last 3) -join "`n"
        } catch {}
    }
    @{ status = $st; tail = $tail }
}

function Cmd-Status {
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
                [void]$sess.Add(@{ config_dir = $cd.Name; alive = $alive; entry = $e })
            }
        }
    }
    $quota = New-Object System.Collections.ArrayList
    Get-ChildItem -Path "$env:USERPROFILE\.claude\fable-director" -Filter 'quota-*.json' -File -ErrorAction SilentlyContinue |
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
    if (Test-Path (Join-Path $d 'env.txt')) {
        Get-Content -LiteralPath (Join-Path $d 'env.txt') -Encoding UTF8 | Where-Object { $_ -match '=' } | ForEach-Object {
            $k, $v = $_ -split '=', 2; [Environment]::SetEnvironmentVariable($k, $v, 'Process') }
    }
    $df = Join-Path $d 'deps.txt'
    if (Test-Path $df) {
        $dp = @{}; Get-Content -LiteralPath $df -Encoding UTF8 | ForEach-Object { $k, $v = $_ -split '=', 2; $dp[$k] = $v }
        $cache = Join-Path (Join-Path $Root 'cache\deps') $dp['key']
        $ddir = $dp['dir'] -replace '/', '\'
        if (-not (Test-Path (Join-Path $cache '.ok'))) {
            Write-Status $d 'deps' $PID $null
            $p = Start-Cmd $d 'deps' $dp['cmd'] $srcd $log -Wait
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
