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
