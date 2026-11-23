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
