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
    # the version of each tool, each one called by its own file name
    $tools = New-Object System.Collections.Hashtable
    $tools['node'] = $null; $tools['ffmpeg'] = $null; $tools['python'] = $null; $tools['git'] = $null
    $tools['tmux'] = $null; $tools['claude'] = $null; $tools['tar'] = $null; $tools['chrome'] = $chrome
    try { if (IsTool 'node.exe') { $tools['node'] = VersionIn (node.exe --version 2>&1) } } catch {}
    try { if (IsTool 'tar.exe') { $tools['tar'] = VersionIn (tar.exe --version 2>&1) } } catch {}
    try {
    } catch {}
    $arch = $env:PROCESSOR_ARCHITECTURE; if ($arch -eq 'AMD64') { $arch = 'x86_64' } elseif ($arch -eq 'ARM64') { $arch = 'aarch64' }
    Out-J @{ ok = $true; kind = 'windows-native'; os = $pretty; os_version = "$($os.Version)"; os_build = $build; arch = $arch
        cores = [int](($cpu | Measure-Object -Property NumberOfCores -Sum).Sum); threads = [int](($cpu | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
        cpu = $cpu[0].Name.Trim(); ram_gb = Gb $cs.TotalPhysicalMemory; ram_free_gb = Gb ([double]$os.FreePhysicalMemory * 1KB)
        disk_free_gb = @{ $Root = FreeGb $Root }; gpu = $gpus; webgl = $w; bench = $b
        tools = $tools
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
