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

LA SETTIMANA (26/10/2026, grafici 2A e 2B di Utilizzo): ogni lettura lascia un campione della percentuale
settimanale in <state_dir>/quota-week.json, uno per ora dal rinnovo (l'ultima lettura di quell'ora), tenuti fino al
rinnovo dopo. L'ora del campione e' quella della lettura (il file della statusline), non quella in cui la si guarda:
un account fermo non aggiunge campioni, e la sua quota non si muove. --json da' per account settimana_punti
([[epoch, pct], ...]) e settimana_origini (sessioni, automazioni, richieste in punti della percentuale, dalle
trascrizioni dell'account: cm-master-usage.week_origins).

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
    mtime = f.stat().st_mtime
    eta = time.time() - mtime
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
        "letta_alle": int(mtime),   # 26/10: l'ora del campione della settimana
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
                      "reset_settimanale": reset, "reset_cinque_ore": d.get("five_hour_resets_at"), "eta_secondi": round(eta), "letta_alle": int(mtime),
                      "vecchia": eta > SOGLIA_VECCHIA, "finestra_scaduta": bool(reset) and reset < time.time(),
                      "provenienza": host})
    return q


WEEK_S = 7 * 86400


def campiona(letture, now=None):
    """Il campione orario della percentuale settimanale di ogni lettura valida; {account: [[epoch, pct], ...]} dal
    rinnovo. Dentro la stessa ora (contata dall'inizio della settimana) l'ultima lettura sostituisce la prima."""
    now = time.time() if now is None else now
    p = Path(cm.expand(CFG.get("state_dir") or "~/.local/state/cc-supervisor")) / "quota-week.json"
    try:
        db = json.loads(p.read_text())
    except (OSError, ValueError):
        db = {}
    changed = False
    out = {}
    for nome, q in letture:
        reset = q.get("reset_settimanale")
        a = db.get(nome) if isinstance(db.get(nome), dict) else {}
        pts = [x for x in (a.get("points") or []) if isinstance(x, list) and len(x) == 2]
        if not reset:
            out[nome] = pts
            continue
        start = int(reset) - WEEK_S
        keep = [x for x in pts if start <= x[0] <= reset]
        if keep != pts or a.get("reset") != reset:
            changed = True
        pct = q.get("settimana_pct")
        if q.get("stato") == "ok" and not q.get("finestra_scaduta") and isinstance(pct, (int, float)):
            t = int(q.get("letta_alle") or now - (q.get("eta_secondi") or 0))
            if start <= t <= reset:
                v = round(float(pct), 1)
                if keep and (keep[-1][0] - start) // 3600 == (t - start) // 3600:
                    if t > keep[-1][0] and keep[-1] != [t, v]:
                        keep[-1] = [t, v]
                        changed = True
                elif not keep or t > keep[-1][0]:
                    keep.append([t, v])
                    changed = True
        db[nome] = {"reset": reset, "points": keep}
        out[nome] = keep
    if changed:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(db))
            tmp.replace(p)
        except OSError:
            pass
    return out


def origini(letture):
    """settimana_origini di ogni account con un rinnovo: le tre voci in punti della percentuale settimanale, piu' i
    token eq e se la lettura delle trascrizioni e' finita. None per un account senza rinnovo o senza consumo."""
    try:
        spec = importlib.util.spec_from_file_location("cm_master_usage", HERE / "cm-master-usage.py")
        mu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mu)
        accs = {n: (CFG["accounts"][n].get("config_dir"), q.get("reset_settimanale")) for n, q in letture
                if q.get("stato") == "ok" and not q.get("finestra_scaduta")}
        eq = mu.week_origins(accs)
    except Exception:  # noqa: BLE001 — un extra: la quota si stampa comunque
        return {}
    out = {}
    for n, q in letture:
        e = eq.get(n)
        pts = mu.week_points(e, q.get("settimana_pct")) if e else None
        out[n] = dict(pts, eq={k: e[k] for k in ("sessioni", "automazioni", "richieste")}, completo=e["completo"]) if pts else None
    return out


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
    punti = campiona(letture)
    if "--json" in sys.argv:
        orig = origini(letture)
        for n, q in letture:
            q["settimana_punti"] = punti.get(n) or []
            q["settimana_origini"] = orig.get(n)
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
