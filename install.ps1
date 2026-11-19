# supervisor - the install on Windows (19/11/2026, supervisor-installazione-windows-16-11):
#
#   irm https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.ps1 | iex
#
# supervisor runs inside Ubuntu (WSL), not on Windows itself. This script:
# 1. looks for Ubuntu in WSL; if it is there, it runs the Linux installer (install.sh) inside it, after asking;
# 2. if it is not, it installs WSL with Ubuntu after asking (Windows asks for the administrator's permission), then
#    says the three steps left: restart, open Ubuntu once, run this line again.
# SUPERVISOR_INSTALL_YES=1 answers yes, =0 answers no. SUPERVISOR_WSL_BIN replaces wsl.exe (tests).
# Everything is in a function: with `irm | iex` an `exit` would close the person's PowerShell window.
# The accented letters are made at run time: Windows PowerShell 5.1 reads a file without BOM as ANSI.

function Install-Supervisor {
    $UrlSh = 'https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.sh'
    $UrlPs = 'https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.ps1'
    $e = [char]0xE8; $ea = [char]0xE9; $a = [char]0xE0; $o = [char]0xF2; $i_ = [char]0xEC; $lq = [char]0xAB; $rq = [char]0xBB
    $lang = if ($env:SUPERVISOR_LANG) { $env:SUPERVISOR_LANG } else { (Get-Culture).TwoLetterISOLanguageName }
    $it = $lang -like 'it*'
    function Say([string]$i, [string]$n) { if ($it) { Write-Host $i } else { Write-Host $n } }
    function Ask([string]$i, [string]$n) {
        if ($env:SUPERVISOR_INSTALL_YES -eq '1') { return $true }
        if ($env:SUPERVISOR_INSTALL_YES -eq '0') { return $false }
        $q = if ($it) { "$i [S/n]" } else { "$n [Y/n]" }
        $r = Read-Host $q
        return -not ($r -match '^\s*(n|no)\s*$')
    }
    $wsl = if ($env:SUPERVISOR_WSL_BIN) { $env:SUPERVISOR_WSL_BIN } else { 'wsl.exe' }

    # the distributions: wsl.exe writes them in UTF-16, so the NULs go
    $distro = $null
    if (Get-Command $wsl -ErrorAction SilentlyContinue) {
        try {
            $names = & $wsl -l -q 2>$null
            if ($LASTEXITCODE -eq 0) {
                $distro = $names | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ -match '^ubuntu' } | Select-Object -First 1
            }
        } catch { $distro = $null }
    }

    Say "supervisor non gira direttamente su Windows: gira dentro Ubuntu (WSL), che Windows installa da s$ea." `
        "supervisor does not run on Windows itself: it runs inside Ubuntu (WSL), which Windows installs by itself."
    if ($distro) {
        Say "Ubuntu c'$e gi$a ($distro): l'installatore pu$o continuare l$i_ dentro." "Ubuntu is already here ($distro): the installer can go on in there."
        if (Ask "Continuo dentro $distro?" "Go on inside $distro?") {
            # a clean PATH: with the Windows one, the Linux installer would find Windows' claude and npm (and remove them)
            & $wsl -d $distro -- bash -c "export PATH=`$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin; curl -fsSL $UrlSh | bash"
            $global:LASTEXITCODE = $LASTEXITCODE
            return
        }
        Say "Per farlo a mano: apri $lq$distro$rq dal menu Start e incolla:" "To do it by hand: open $lq$distro$rq from the Start menu and paste:"
        Write-Host "    curl -fsSL $UrlSh | bash"
        $global:LASTEXITCODE = 3
        return
    }

    Say "Ubuntu non c'$e ancora." "Ubuntu is not here yet."
    if (Ask "Installo WSL con Ubuntu adesso? Windows chieder$a il permesso di amministratore." "Install WSL with Ubuntu now? Windows will ask for the administrator's permission.") {
        $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($admin -or $env:SUPERVISOR_WSL_BIN) {
            & $wsl --install -d Ubuntu
            $rc = $LASTEXITCODE
        } else {
            $p = Start-Process -FilePath $wsl -ArgumentList '--install', '-d', 'Ubuntu' -Verb RunAs -Wait -PassThru
            $rc = $p.ExitCode
        }
        if ($rc -ne 0) {
            Say "L'installazione di WSL non $e riuscita (codice $rc). Riprova da PowerShell aperto come amministratore:  wsl --install -d Ubuntu" `
                "The WSL install did not succeed (code $rc). Try again from PowerShell opened as administrator:  wsl --install -d Ubuntu"
            $global:LASTEXITCODE = $rc
            return
        }
        Say "Fatto. Restano tre passi:" "Done. Three steps are left:"
        Say "  1. Riavvia il computer." "  1. Restart the computer."
        Say "  2. Apri ${lq}Ubuntu$rq dal menu Start e scegli un nome utente e una password." "  2. Open ${lq}Ubuntu$rq from the Start menu and choose a user name and a password."
        Say "  3. Riapri PowerShell e rilancia:  irm $UrlPs | iex" "  3. Open PowerShell again and run:  irm $UrlPs | iex"
        $global:LASTEXITCODE = 0
        return
    }
    Say "Quattro passi, a mano:" "Four steps, by hand:"
    Say "  1. Apri PowerShell come amministratore (tasto destro sul menu Start) e scrivi:  wsl --install -d Ubuntu" `
        "  1. Open PowerShell as administrator (right click on the Start menu) and type:  wsl --install -d Ubuntu"
    Say "  2. Riavvia il computer." "  2. Restart the computer."
    Say "  3. Apri ${lq}Ubuntu$rq dal menu Start e scegli un nome utente e una password." "  3. Open ${lq}Ubuntu$rq from the Start menu and choose a user name and a password."
    Say "  4. In Ubuntu incolla:  curl -fsSL $UrlSh | bash" "  4. In Ubuntu paste:  curl -fsSL $UrlSh | bash"
    $global:LASTEXITCODE = 3
}

Install-Supervisor
