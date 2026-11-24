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
