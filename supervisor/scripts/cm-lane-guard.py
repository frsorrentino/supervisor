#!/usr/bin/env python3
"""Hook PreToolUse (Bash): la guardia della corsia chiusa (fase 1, 03/10/2026).

Nega `git push`, `gh release create|upload|edit|delete`, `npm publish`, `claude plugin publish` e uno script
`release.sh` quando nella cartella del comando non c'e' un compito che li copre:
- un compito in corso (o approvato) di un piano approvato, senza `hold`, la cui cartella contiene quella del
  comando; oppure
- un compito con un ok registrato (`task approve`) nella stessa cartella, non ancora chiuso.
La cartella del comando (09/10/2026) e' quella in cui gira davvero: dopo un `cd <dir>` della stessa catena
(`&&`, `;`, `||`, `|`, a capo) o con `git -C <dir> push`, relativa alla cartella di prima e con ~ espansa; senza,
la cartella della sessione. Un `cd` che non si sa leggere (una variabile, `cd -`) lascia una cartella che nessun
compito copre: negato, mai concesso per sbaglio. Con piu' comandi chiusi nella catena, li deve coprire tutti.
Non chiede un secondo ok: controlla soltanto. Spenta di default (`tasks.guard`), e senza registro non fa nulla:
in quei casi il comportamento e' quello di prima. Un errore della guardia non blocca mai il comando.
Sempre accesa, invece, la parte sui tasti (23/10/2026): `tmux send-keys` e `paste-buffer` verso qualunque pannello sono
negati, perche' quello che compare in un terminale conta come scritto dalla persona (un ok).

Con `--strict` (12/10/2026, fail-closed solo per i push): hooks.json la lancia una seconda volta con un filtro `if`
sui comandi che pubblicano e `onFailure: "block"`. Li' un errore esce con 1 invece di lasciar passare, e Claude Code
(dalla 2.1.295) blocca il push; un comando che il filtro non prende non avvia nemmeno lo script, quindi un errore
della guardia non ferma mai gli altri comandi. Le versioni precedenti ignorano la chiave: il push passa, come prima.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# release.sh (16/10/2026) solo dove e' il comando: in testa al segmento, dopo assegnazioni, nice/nohup/time e bash/sh;
# come argomento (`grep -n x release.sh`, `cat release.sh`) e' una lettura, e veniva negata
CLOSED = re.compile(r"(?:^|[;&|(\s])(?:git\s+(?:-C\s+\S+\s+)?push\b(?!.*--dry-run)|gh\s+release\s+(?:create|upload|edit|delete)\b"
                    r"|npm\s+publish\b(?!.*--dry-run)|claude\s+plugin\s+publish\b)"
                    r"|(?:^|[;&|(\n])\s*(?:\w+=\S*\s+)*(?:(?:nice(?:\s+-n\s*-?\d+|\s+-\d+)?|nohup|time)\s+)*"
                    r"(?:(?:ba)?sh\s+(?:-\w+\s+)*)?(?:\S*/)?release\.sh\b(?!.*--check)")


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def covered(con, cwd):
    """(coperto, motivo). Il compito copre la cartella se il comando gira dentro la sua."""
    real = os.path.realpath(cwd)
    for r in con.execute("SELECT t.id, t.plan, t.state, t.project, t.contract, p.approved_at FROM tasks t "
                         "LEFT JOIN plans p ON p.id = t.plan WHERE t.state IN ('running', 'approved')"):
        proj = os.path.realpath(r["project"])
        if real != proj and not real.startswith(proj.rstrip("/") + "/"):
            continue
        task = json.loads(r["contract"])["task"]
        if r["plan"] and r["approved_at"] and not task.get("hold"):
            return True, f"task {r['id']} of approved plan {r['plan']}"
        if con.execute("SELECT 1 FROM approvals WHERE task=? AND by_ IS NOT NULL", (r["id"],)).fetchone():
            return True, f"task {r['id']} approved"
    return False, ""


SEP = re.compile(r"&&|\|\||[;|\n]")
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def targets(cmd, cwd):
    """[(cartella, leggibile)] dei comandi chiusi di una catena (heredoc gia' tolti), seguendo i `cd`. Leggibile
    False dopo un `cd` con una variabile, un `$(…)`, un backtick o `-`, finche' un `cd` assoluto non la rimette."""
    keep = []

    def hold(m):
        keep.append(m.group(0)[1:-1])
        return f"\x00{len(keep) - 1}\x00"

    def word(w):
        return os.path.expanduser(re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], w))

    cur, readable, out = cwd, True, []
    for seg in SEP.split(QUOTED.sub(hold, cmd)):
        seg = seg.strip().lstrip("(").strip()
        parts = seg.split()
        if parts and parts[0] in ("cd", "pushd"):
            arg = word(parts[1]) if len(parts) > 1 else os.path.expanduser("~")
            if arg == "-" or re.search(r"[$`]", arg):
                readable = False
            elif os.path.isabs(arg):
                readable = True
            cur = os.path.join(cur, arg)
            continue
        if CLOSED.search(re.sub(r"\x00\d+\x00", "''", seg)):
            m = re.search(r"\bgit\s+-C\s+(\S+)", seg)
            d = word(m.group(1)) if m else ""
            out.append((os.path.join(cur, d) if d else cur,
                        (readable or os.path.isabs(d)) and not re.search(r"[$`]", d)))
    return out


# 23/10/2026 18:53 (the maintainer from the phone: «se io scrivo un ok è sempre valido»): quello che compare in un
# terminale e' la persona, quindi nessuna sessione Claude digita nei pannelli: `tmux send-keys` (alias `send`) e
# `paste-buffer` (`pasteb`) sono negati sempre, anche a guardia spenta; le sessioni si parlano con `supervisor talk`
KEYS = re.compile(r"\btmux\b[^|;&\n]*\b(send-keys|send|paste-buffer|pasteb)\b")
KEYS_REASON = ("tmux send-keys/paste-buffer from a Claude session is blocked (23/10/2026): text typed into a pane counts as the person's. "
               "To reach a session: supervisor talk NAME \"…\"; to answer its question: supervisor answer; to close it: supervisor close NAME.")


def decide(payload):
    """None = lascia passare; altrimenti il motivo del rifiuto."""
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if KEYS.search(cmd):
        return KEYS_REASON
    bare = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\1$", "", cmd, flags=re.S | re.M)   # un heredoc non e' un comando
    bare = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", bare)   # ne' il testo fra virgolette (messaggi di commit…)
    if not CLOSED.search(bare):   # il caso comune: nessun caricamento di config
        return None
    cm = _load("cm-config")
    cfg = cm.load(warn=False)
    if not (cfg.get("tasks") or {}).get("guard"):
        return None
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return None
    cwd = payload.get("cwd") or os.getcwd()
    plain = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\1$", "", cmd, flags=re.S | re.M)
    bad = [(d, ok) for d, ok in (targets(plain, cwd) or [(cwd, True)]) if not (ok and covered(con, d)[0])]
    if not bad:
        return None
    if any(not ok for _, ok in bad):   # l'ok forse c'e': manca la cartella, non l'approvazione
        return cm.msg(cfg, "guard.lane_cd_unreadable", cmd=cmd.strip().splitlines()[0][:120])
    return cm.msg(cfg, "guard.lane_closed", cmd=cmd.strip().splitlines()[0][:120])


def main():
    strict = "--strict" in sys.argv[1:]
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return 0
        reason = decide(payload)
    except Exception as e:   # noqa: BLE001 — la guardia non deve mai rompere una sessione, salvo un push (--strict)
        if strict:
            print(f"supervisor lane guard failed, push blocked: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
