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
    $npmTok = Select-String -Path "$env:USERPROFILE\.npmrc" -Pattern '_authToken' -Quiet -ErrorAction SilentlyContinue
    $npmLabel = '~\.npmrc (token)'
    if ($npmTok) {
        [void]$creds.Add($npmLabel)
    }
    $envNames = @([Environment]::GetEnvironmentVariables('User').Keys) + @([Environment]::GetEnvironmentVariables('Machine').Keys)
    $envNames | Sort-Object -Unique | Where-Object { $_ -match '(^|_)(TOKEN|SECRET|KEY|PASSWORD)($|_)' -and $_ -notmatch '^(CM_|CLAUDE_CODE_)|_KEYS$' } | ForEach-Object { [void]$creds.Add("env $_") }
    $ck = (cmdkey.exe /list 2>$null) -join "`n"
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
        $benchJs = Join-Path $Here 'cm-bench.js'
        $bo = (node.exe $benchJs $Root 2>$null) | Select-Object -Last 1
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