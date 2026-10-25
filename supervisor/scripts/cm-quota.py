#!/usr/bin/env python3
"""supervisor quota — quanto e' pieno il serbatoio di ciascun account, in un posto solo.

Gli account sono piani separati, quindi quote indipendenti. Finora l'unico modo
di sapere quale fosse piu' libero era iniziare un lavoro e vedere se si
schiantava contro il limite.

DA DOVE ARRIVA IL DATO: non lo chiediamo a nessuno. La statusline di
fable-director riceve `rate_limits` nel JSON di sessione e lo scrive in
`<quota.source>/quota-<hash>.json`, dove <hash> sono i primi 8 caratteri dello
sha256 di CLAUDE_CONFIG_DIR. Qui quei file si leggono e basta: nessuna
chiamata, nessun token, nessuna sessione avviata.

IL PREZZO DI QUESTA SCELTA, ED E' IL PUNTO: un file si aggiorna solo quando una
sessione DI QUELL'ACCOUNT rende la sua statusline. Se un account sta fermo due
giorni, il suo numero e' di due giorni fa. Un numero vecchio spacciato per
fresco e' peggio dell'ignoranza: si decide con sicurezza sulla cosa sbagliata.
Per questo l'eta' della lettura sta in tabella accanto al numero, e una
finestra gia' scaduta viene detta a chiare lettere.

Uso: cm-quota.py [--json] [--no-screen]   (--no-screen: non legge gli schermi tmux per la bassa priorita')
     cm-quota.py --master [--json] [--days N] [--suggest]   (il consumo della master per origine: cm-master-usage.py)
"""
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)

SOURCE = Path(cm.expand(CFG["quota"]["source"]))
# Oltre questa eta' la lettura viene marcata. Due ore sono tarate sulla
# finestra da 5 ore: e' la piu' veloce a muoversi.
SOGLIA_VECCHIA = 2 * 3600


def eta_umana(secondi):
    m = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
    if secondi < 90:
        return m("quota.age_now")
    if secondi < 5400:
        return m("quota.age_min", n=round(secondi / 60))
    if secondi < 172800:
        return m("quota.age_hours", n=round(secondi / 3600))
    return m("quota.age_days", n=round(secondi / 86400))


def leggi(config_dir):
    """Stato della quota per un account, o il motivo per cui non c'e'.

    L'account si identifica dalla cartella di configurazione, non dal nome: e'
    lo stesso criterio con cui la statusline sceglie il file, quindi i due non
    possono divergere.
    """
    acct = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    f = SOURCE / f"quota-{acct}.json"
    if not f.is_file():
        return {"stato": "mai visto", "file": str(f)}
    try:
        d = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"stato": f"illeggibile ({e.__class__.__name__})", "file": str(f)}
    eta = time.time() - f.stat().st_mtime
    reset = d.get("weekly_resets_at")
    scaduta = bool(reset) and reset < time.time()
    return {
        "stato": "ok", "file": str(f),
        "cinque_ore_pct": d.get("five_hour_used_pct"),
        "settimana_pct": d.get("weekly_used_pct"),
        "reset_settimanale": reset,
        # il reset della finestra di 5 ore c'e' nella sorgente e serve al polso (14/09): «riparte alle 12:30»
        "reset_cinque_ore": d.get("five_hour_resets_at"),
        "eta_secondi": round(eta),
        "vecchia": eta > SOGLIA_VECCHIA,
        "finestra_scaduta": scaduta,
    }


GIORNI = {"it": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
          "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


def data_breve(epoch):
    d = datetime.fromtimestamp(epoch)
    g = GIORNI.get(CFG["language"], GIORNI["en"])[d.weekday()]
    return f"{g} {d.strftime('%d/%m %H:%M')}"


def pct(v):
    return "-" if v is None else f"{round(v)}%"


def bassa_priorita():
    """Sessioni in modalita' a bassa priorita' per account (`/low-priority`: oltre il limite, lente ma vive). Lo dice
    solo lo schermo tmux (cm-sessions), quindi conta le sessioni con un riquadro; senza tmux il conteggio e' zero."""
    try:
        spec = importlib.util.spec_from_file_location("cm_sessions", HERE / "cm-sessions.py")
        ses = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ses)
        rows = ses.collect(read_screen=True)
    except Exception:  # noqa: BLE001 — l'informazione e' accessoria: la quota si stampa comunque
        return {}
    out = {}
    for r in rows:
        if r.get("low_priority") == "active":
            out[r["account"]] = out.get(r["account"], 0) + 1
    return out


def unisci_remote(nome, q):
    """Lettura unita (piano multi-PC 6): i limiti sono per account, non per macchina. Fra la lettura locale e quelle
    degli altri host (negli snapshot del sondatore) vince la piu' fresca, e se ne dice la provenienza."""
    if not CFG.get("hosts"):
        return q
    try:
        spec = importlib.util.spec_from_file_location("cm_hosts", HERE / "cm-hosts.py")
        hm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hm)
        remote = hm.remote_quota(nome)
        q["consumatori"] = hm.remote_consumers().get(nome, {})
    except Exception:  # noqa: BLE001 — la quota locale si stampa comunque
        return q
    q.setdefault("provenienza", "local")
    for host, mtime, d in remote:
        eta = time.time() - mtime
        if q.get("stato") != "ok" or eta < q["eta_secondi"]:
            reset = d.get("weekly_resets_at")
            q.update({"stato": "ok", "cinque_ore_pct": d.get("five_hour_used_pct"), "settimana_pct": d.get("weekly_used_pct"),
                      "reset_settimanale": reset, "reset_cinque_ore": d.get("five_hour_resets_at"), "eta_secondi": round(eta),
                      "vecchia": eta > SOGLIA_VECCHIA, "finestra_scaduta": bool(reset) and reset < time.time(),
                      "provenienza": host})
    return q


def main():
    if "--master" in sys.argv:   # 25/10/2026: il consumo della master per ora e per origine
        import runpy
        sys.argv = [str(Path(__file__).with_name("cm-master-usage.py"))] + [a for a in sys.argv[1:] if a != "--master"]
        runpy.run_path(sys.argv[0], run_name="__main__")
        return 0
    letture = [(name, unisci_remote(name, leggi(cm.expand(a["config_dir"])))) for name, a in CFG["accounts"].items()]
    lente = {} if "--no-screen" in sys.argv else bassa_priorita()
    for n, q in letture:
        q["bassa_priorita"] = lente.get(n, 0)
    if "--json" in sys.argv:
        print(json.dumps({n: q for n, q in letture}, indent=2))
        return 0
    m = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
    print()
    print(m("quota.header"))
    print(m("quota.header_rule"))
    for nome, q in letture:
        if q["stato"] != "ok":
            print(f"{nome:<14} {q['stato']}")
            continue
        r = data_breve(q["reset_settimanale"]) if q["reset_settimanale"] else "-"
        eta = eta_umana(q["eta_secondi"]) + ("  " + m("quota.old_mark") if q["vecchia"] else "")
        if q.get("provenienza") not in (None, "local"):
            eta += "  " + m("quota.from_host", host=q["provenienza"])
        print(f"{nome:<14} {pct(q['cinque_ore_pct']):>7} {pct(q['settimana_pct']):>10}  {r:<17} {eta}")
    if CFG.get("hosts"):
        loc = {}
        try:
            spec = importlib.util.spec_from_file_location("cm_sessions", HERE / "cm-sessions.py")
            ses = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ses)
            for r in ses.collect(read_screen=False):
                if r.get("registry") and r["status"] in ("busy", "idle", "waiting"):
                    loc[r["account"]] = loc.get(r["account"], 0) + 1
        except Exception:  # noqa: BLE001
            pass
        for n, q in letture:
            parts = ([f"{loc[n]} {m('quota.on')} local"] if loc.get(n) else []) + \
                    [f"{k} {m('quota.on')} {h}" for h, k in (q.get("consumatori") or {}).items()]
            if parts:
                print(f"  {n}: " + " · ".join(parts))
    for n, q in letture:
        if q.get("bassa_priorita"):
            print()
            print("  " + m("quota.low_priority", account=n, n=q["bassa_priorita"]))
    avvisi = [n for n, q in letture if q["stato"] == "ok" and q["finestra_scaduta"]]
    if avvisi:
        print()
        for n in avvisi:
            print("  " + m("quota.window_expired", account=n))
    if any(q["stato"] == "ok" and q["vecchia"] for _, q in letture):
        print()
        print("  " + m("quota.old_note"))
    if any(q["stato"] != "ok" for _, q in letture):
        print()
        print("  " + m("quota.missing_note"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
