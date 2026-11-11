# supervisor - lo schermo e i tasti della console di una sessione Claude su questo host (09/10/2026). Solo ASCII.
#   powershell -File cm-console.ps1 -Name NOME -Out FILE [-KeysB64 B64]   (tasti: Up Down Left Right Enter Esc text:.. wait:ms)
# Gira come attivita' Interactive nella sessione del desktop (da sshd, sessione 0, AttachConsole non arriva alla
# console della sessione 1): si stacca dalla sua console, si attacca a quella del processo claude della sessione NOME
# (il piu' recente vivo in ~/.claude/sessions), manda i tasti (Up, Down, Enter, Esc, text:..., wait:ms) e legge la
# parte visibile dello schermo. Scrive {ok, pid, screen_b64} o {error} in FILE.
param([string]$Name, [string]$Out, [string]$KeysB64 = "")
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System; using System.Text; using System.Runtime.InteropServices; using Microsoft.Win32.SafeHandles;
public static class CmCon {
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool FreeConsole();
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(uint pid);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern IntPtr CreateFileW(string n, uint a, uint s, IntPtr sa, uint c, uint f, IntPtr t);
  [StructLayout(LayoutKind.Sequential)] public struct COORD { public short X, Y; }
  [StructLayout(LayoutKind.Sequential)] public struct SMALL_RECT { public short L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct CSBI { public COORD Size; public COORD Cursor; public ushort Attr; public SMALL_RECT Win; public COORD Max; }
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CSBI i);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool ReadConsoleOutputCharacterW(IntPtr h, StringBuilder b, uint n, COORD c, out uint r);
  [StructLayout(LayoutKind.Explicit, CharSet=CharSet.Unicode)] public struct KEY { [FieldOffset(0)] public ushort Type; [FieldOffset(4)] public int Down; [FieldOffset(8)] public ushort Rep; [FieldOffset(10)] public ushort VK; [FieldOffset(12)] public ushort Scan; [FieldOffset(14)] public char Ch; [FieldOffset(16)] public uint State; }
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool WriteConsoleInputW(IntPtr h, KEY[] r, uint n, out uint w);
  public static string Screen() {
    IntPtr h = CreateFileW("CONOUT$", 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
    CSBI i; if (!GetConsoleScreenBufferInfo(h, out i)) throw new Exception("csbi " + Marshal.GetLastWin32Error());
    var sb = new StringBuilder();
    for (short y = i.Win.T; y <= i.Win.B; y++) {
      int w = i.Win.R - i.Win.L + 1; var b = new StringBuilder(w + 1); uint r; var c = new COORD(); c.X = i.Win.L; c.Y = y;
      ReadConsoleOutputCharacterW(h, b, (uint)w, c, out r); sb.Append(b.ToString(0, (int)r).TrimEnd()).Append('\n');
    }
    return sb.ToString();
  }
  public static void Key(ushort vk, char ch) {
    IntPtr h = CreateFileW("CONIN$", 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
    var k = new KEY[2]; for (int j = 0; j < 2; j++) { k[j].Type = 1; k[j].Down = j == 0 ? 1 : 0; k[j].Rep = 1; k[j].VK = vk; k[j].Ch = ch; }
    uint w; if (!WriteConsoleInputW(h, k, 2, out w)) throw new Exception("write " + Marshal.GetLastWin32Error());
  }
}
"@
$r = @{}
try {
  if ($Name -notmatch '^[A-Za-z0-9._-]+$') { throw "bad session name" }
  $best = $null
  # 06/11/2026: le sessioni di ogni account (.claude, .claude-pixel, ...)
  $files = Get-ChildItem -Path $env:USERPROFILE -Directory -Force -Filter '.claude*' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\.claude(-[A-Za-z0-9_]+)?$' } |
    ForEach-Object { Get-ChildItem (Join-Path $_.FullName 'sessions') -Filter *.json -ErrorAction SilentlyContinue }
  foreach ($f in $files) {
    try { $e = Get-Content $f.FullName -Raw | ConvertFrom-Json } catch { continue }
    if ($e.name -ne $Name -or -not (Get-Process -Id $e.pid -ErrorAction SilentlyContinue)) { continue }
    if (-not $best -or $e.startedAt -gt $best.startedAt) { $best = $e }
  }
  if (-not $best) { throw "not running" }
  $TargetPid = [int]$best.pid; $r.pid = $TargetPid
  [void][CmCon]::FreeConsole()
  if (-not [CmCon]::AttachConsole([uint32]$TargetPid)) { throw "attach $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
  if ($KeysB64) {
    $keys = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($KeysB64)) | ConvertFrom-Json
    foreach ($k in $keys) {
      switch -regex ($k) {
        '^Up$'    { [CmCon]::Key(0x26, [char]0) }
        '^Down$'  { [CmCon]::Key(0x28, [char]0) }
        '^Left$'  { [CmCon]::Key(0x25, [char]0) }
        '^Right$' { [CmCon]::Key(0x27, [char]0) }
        '^Enter$' { [CmCon]::Key(0x0D, [char]13) }
        '^Esc$'   { [CmCon]::Key(0x1B, [char]27) }
        '^wait:(\d+)$' { Start-Sleep -Milliseconds ([int]$Matches[1]) }
        '^text:'  { foreach ($ch in $k.Substring(5).ToCharArray()) { [CmCon]::Key(0, $ch) } }
      }
      Start-Sleep -Milliseconds 150
    }
    Start-Sleep -Milliseconds 400
  }
  $r.screen = [CmCon]::Screen(); $r.ok = $true
} catch { $r.error = "$_" }
if ($r.screen) { $r.screen_b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($r.screen)); $r.Remove('screen') }
[IO.File]::WriteAllText($Out, (ConvertTo-Json -Compress $r), [Text.UTF8Encoding]::new($false))
