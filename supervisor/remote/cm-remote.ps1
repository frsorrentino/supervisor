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
function DepsField($lines, $field) {
    # the value of the last line "FIELD=..." of deps.txt, or $null
    $found = $null
    foreach ($line in $lines) { if ($line.StartsWith("$field=")) { $found = $line.Substring($field.Length + 1) } }
    $found
}
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
function IsTool($fileName) {
    # true when a program with exactly this file name (node.exe, claude.cmd) is on the PATH and is not the WindowsApps
    # placeholder; the name carries its extension, so a .ps1 of the same name is never what runs
    $c = Get-Command $fileName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    [bool]($c -and $c.Source -notlike '*\WindowsApps\*')
}
function VersionIn($output) {
    # the version number in the first line a tool printed, or $null
    $first = (@($output) | Select-Object -First 1) -join ''
    $m = [regex]::Match("$first", '\d+(\.\d+)+')
    if ($m.Success) { $m.Value } else { $null }
}
function Sha($f) { (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower() }
function NowIso { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }

}

function WebGL {
    $exe = Find-Chrome
    if (-not $exe) { return $null }
    $page = Join-Path $Here 'cm-webgl.html'
    if (-not (Test-Path $page)) { return $null }
    $ud = Join-Path $env:TEMP ('cmw' + [guid]::NewGuid().ToString('N'))
    $of = Join-Path $env:TEMP ('cmw' + [guid]::NewGuid().ToString('N') + '.txt')
    $url = 'file:///' + ($page -replace '\\', '/')
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
    # the version of each tool, each one called by its own file name
    $tools = New-Object System.Collections.Hashtable
    $tools['node'] = $null; $tools['ffmpeg'] = $null; $tools['python'] = $null; $tools['git'] = $null
    $tools['tmux'] = $null; $tools['claude'] = $null; $tools['tar'] = $null; $tools['chrome'] = $chrome
    try { if (IsTool 'node.exe') { $tools['node'] = VersionIn (node.exe --version 2>&1) } } catch {}
    try { if (IsTool 'ffmpeg.exe') { $tools['ffmpeg'] = VersionIn (ffmpeg.exe -version 2>&1) } } catch {}
    try { if (IsTool 'python.exe') { $tools['python'] = VersionIn (python.exe --version 2>&1) } } catch {}
    try { if (IsTool 'git.exe') { $tools['git'] = VersionIn (git.exe --version 2>&1) } } catch {}
    try { if (IsTool 'tar.exe') { $tools['tar'] = VersionIn (tar.exe --version 2>&1) } } catch {}
    try {
        if (IsTool 'claude.exe') { $tools['claude'] = VersionIn (claude.exe --version 2>&1) }
        elseif (IsTool 'claude.cmd') { $tools['claude'] = VersionIn (claude.cmd --version 2>&1) }
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

function ApiState($cdPath, $e) {
    # 16/11/2026 (supervisor-blocco-permanente-16-11): l'ultima voce assistant della trascrizione della sessione (non di
    # un sottoagente). Se e' un errore dell'API, block = {kind limit|login|error, error, text, at, resets_at, limit_type}:
    # la sessione e' ferma li'. La stessa lettura di api_error (cm-relay-state.py) e di apiError (cm-session.js); si
    # leggono gli ultimi 512 KB. last_at = l'ora di quella voce. $null se la trascrizione non c'e'.
    try {
        $slug = ([string]$e.cwd) -replace '[^A-Za-z0-9]', '-'
        $f = Join-Path (Join-Path (Join-Path $cdPath 'projects') $slug) ([string]$e.sessionId + '.jsonl')
        if (-not (Test-Path -LiteralPath $f)) { return $null }
        $fs = [IO.File]::Open($f, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
            $len = $fs.Length
            $n = [int][Math]::Min($len, 524288)
            [void]$fs.Seek(-$n, [IO.SeekOrigin]::End)
            $buf = New-Object byte[] $n
            $got = 0
            while ($got -lt $n) { $r = $fs.Read($buf, $got, $n - $got); if ($r -le 0) { break }; $got += $r }
        } finally { $fs.Close() }
        $lines = [Text.Encoding]::UTF8.GetString($buf, 0, $got) -split "`n"
        $first = if ($len -le 524288) { 0 } else { 1 }
        for ($i = $lines.Count - 1; $i -ge $first; $i--) {
            $l = $lines[$i]
            if ($l -notmatch '"type"\s*:\s*"assistant"') { continue }
            if ($l -match '"isSidechain"\s*:\s*true') { continue }
            $at = if ($l -match '"timestamp"\s*:\s*"([^"]+)"') { $Matches[1] } else { $null }
            if ($l -notmatch '"isApiErrorMessage"\s*:\s*true' -and $l -notmatch '"model"\s*:\s*"<synthetic>"') { return @{ block = $null; last_at = $at } }
            try { $d = $l | ConvertFrom-Json } catch { return @{ block = $null; last_at = $at } }
            $text = ''
            foreach ($c in @($d.message.content)) { if ($c -and $c.type -eq 'text' -and $c.text) { $text = [string]$c.text; break } }
            $limitText = $text -match "You've (hit|reached) your|out of (extra )?usage|usage credits|usage limit"
            $loginText = $text -match '/login|not logged in|invalid api key|oauth token|authentication'
            if (-not $d.isApiErrorMessage -and -not $limitText -and -not $loginText) { return @{ block = $null; last_at = $at } }
            $code = ([string]$d.error + ' ' + [string]$d.apiError).Trim()
            $q = $d.quotaLimits
            if (-not $q -and $d.apiErrorParams) { $q = $d.apiErrorParams.rate_limit_info }
            $kind = 'error'
            if ($code -match 'usage_limit|billing|credit' -or ($q -and $q.status -eq 'rejected') -or $limitText) { $kind = 'limit' }
            elseif ($code -match 'auth|oauth|login' -or $loginText) { $kind = 'login' }
            $resets = $null
            if ($q -and $q.resetsAt) { $resets = [int64]$q.resetsAt }
            $lt = $null
            if ($q -and $q.rateLimitType) { $lt = [string]$q.rateLimitType }
            $err = 'api_error'
            if ($d.error) { $err = [string]$d.error } elseif ($d.apiError) { $err = [string]$d.apiError }
            if ($text.Length -gt 300) { $text = $text.Substring(0, 300) }
            return @{ block = @{ kind = $kind; error = $err; text = $text; at = $at; resets_at = $resets; limit_type = $lt }; last_at = $at }
        }
        return @{ block = $null; last_at = $null }
    } catch { return $null }
}

function EpochOf($dt) { [int64](($dt.ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds) }

function SubTokens($jp) {
    # {in, out, model} di una trascrizione di sottoagente: l'ultima usage di ogni message.id (Claude Code la ripete su
    # ogni blocco del messaggio); in = input + cache scritta + cache letta. Come _usage di cm-subagents.py
    $byId = @{}; $model = $null
    try {
        foreach ($l in [IO.File]::ReadLines($jp)) {
            if ($l -notmatch '"type"\s*:\s*"assistant"') { continue }
            try { $d = $l | ConvertFrom-Json } catch { continue }
            if ($d.type -ne 'assistant' -or -not $d.message) { continue }
            if ($d.message.model -and $d.message.model -ne '<synthetic>') { $model = [string]$d.message.model }
            if ($d.message.usage) { $byId[[string]$d.message.id] = $d.message.usage }
        }
    } catch { return $null }
    $tin = [int64]0; $tout = [int64]0
    foreach ($u in $byId.Values) {
        $tin += [int64]$u.input_tokens + [int64]$u.cache_creation_input_tokens + [int64]$u.cache_read_input_tokens
        $tout += [int64]$u.output_tokens
    }
    @{ in = $tin; out = $tout; model = $model }
}
