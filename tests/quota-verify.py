#!/usr/bin/env python3
"""Verifica cm-quota.py su file quota finti (formato della statusline di fable-director).

Q1 file per account = quota-<sha256(config_dir)[:8]>.json nella cartella quota.source
Q2 percentuali e reset in tabella; eta' della lettura accanto
Q3 lettura vecchia (> 2 h) marcata; finestra settimanale scaduta dichiarata
Q4 account senza file → «mai visto» + nota; --json con i campi
Q5 (26/10/2026) settimana_punti: un campione per ora dal rinnovo, l'ultima lettura dell'ora sostituisce la prima, una
   lettura piu' vecchia non aggiunge niente, i campioni della settimana prima cadono, una finestra scaduta non campiona
Q6 settimana_origini: tutte le trascrizioni dell'account dal rinnovo, ogni turno alla sua voce (terminale e telefono →
   richieste; un'altra sessione e `claude -p` → sessioni; un'automazione in elenco, anche il recap lanciato con -p, e
   le compattazioni → automazioni; i risultati in sottofondo restano a chi li ha avviati), in punti con somma = pct;
   le righe prima del rinnovo non contano; completo True
Q7 la lettura si spezza: con un tempo esaurito completo False, la chiamata dopo finisce e da' lo stesso conto
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = T.tmpdir()
home = Path(tmp) / "home"
src = Path(tmp) / "quota"
src.mkdir()
for d in (".claude", ".claude-pixel"):
    (home / d).mkdir(parents=True)
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "quota": {"source": str(src)},
}))


def qfile(config_dir):
    return src / f"quota-{hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]}.json"


now = time.time()
qfile(home / ".claude").write_text(json.dumps({"five_hour_used_pct": 12.4, "weekly_used_pct": 55.6, "weekly_resets_at": now + 86400}))
f2 = qfile(home / ".claude-pixel")
f2.write_text(json.dumps({"five_hour_used_pct": 90, "weekly_used_pct": 99, "weekly_resets_at": now - 3600}))
os.utime(f2, (now - 3 * 3600, now - 3 * 3600))


def run(*args):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg)}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-quota.py")] + list(args), capture_output=True, text=True, env=env, timeout=30)


r = run()
T.check("Q1 both accounts listed", "personale" in r.stdout and "professionale" in r.stdout, r.stdout + r.stderr)
T.check("Q2 percentages rendered", "12%" in r.stdout and "56%" in r.stdout and "90%" in r.stdout, r.stdout)
T.check("Q2 age shown", "ora" in r.stdout, r.stdout)
T.check("Q3 old reading marked", "vecchia" in r.stdout, r.stdout)
T.check("Q3 expired weekly window declared", "professionale" in r.stdout and "ripartita" in r.stdout, r.stdout)
r = run("--json")
d = json.loads(r.stdout)
T.check("Q4 json fields", d["personale"]["stato"] == "ok" and d["personale"]["settimana_pct"] == 55.6 and d["professionale"]["vecchia"] is True
        and d["professionale"]["finestra_scaduta"] is True, r.stdout[:400])
f2.unlink()
r = run()
T.check("Q4 missing file → mai visto + note", "mai visto" in r.stdout and "statusline" in r.stdout, r.stdout)

# ------------------------------------------------------------------ Q5 i campioni della settimana
H = int(now // 3600) * 3600            # le ore contate dall'inizio della settimana cadono sulle ore intere
START = H - 3 * 86400
RESET = START + 7 * 86400
f1 = qfile(home / ".claude")
wk = home / ".local" / "state" / "cc-supervisor" / "quota-week.json"
wk.parent.mkdir(parents=True, exist_ok=True)
wk.write_text(json.dumps({"personale": {"reset": RESET - 7 * 86400, "points": [[START - 7200, 90.0], [START + 60, 1.0]]}}))


def reading(pct, at):
    f1.write_text(json.dumps({"five_hour_used_pct": 5, "weekly_used_pct": pct, "weekly_resets_at": RESET}))
    os.utime(f1, (at, at))
    return json.loads(run("--json", "--no-screen").stdout)["personale"]["settimana_punti"]


p1 = reading(40.0, H - 3600 + 600)
p2 = reading(41.26, H - 3600 + 1800)
p3 = reading(43.0, H + 300)
p4 = reading(39.0, H - 7200)
T.check("Q5 the previous week's sample drops, this week's stays; a second reading in the same hour replaces the first (rounded to one decimal)",
        p1 == [[START + 60, 1.0], [H - 3000, 40.0]] and p2 == [[START + 60, 1.0], [H - 1800, 41.3]], f"{p1} {p2}")
T.check("Q5 the next hour appends; an older reading adds nothing; the file keeps the reset",
        p3 == [[START + 60, 1.0], [H - 1800, 41.3], [H + 300, 43.0]] and p4 == p3 and json.loads(wk.read_text())["personale"]["reset"] == RESET, f"{p3} {p4}")
f2.write_text(json.dumps({"five_hour_used_pct": 90, "weekly_used_pct": 99, "weekly_resets_at": now - 3600}))
d = json.loads(run("--json", "--no-screen").stdout)
T.check("Q5 an expired weekly window takes no sample; settimana_punti [] and settimana_origini null",
        d["professionale"]["settimana_punti"] == [] and d["professionale"]["settimana_origini"] is None, json.dumps(d["professionale"])[:300])

# ------------------------------------------------------------------ Q6 da dove viene la settimana
def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, text, origin=None):
    d = {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": text}}
    if origin:
        d["turnOrigin"] = origin
    return json.dumps(d, ensure_ascii=False)


def peer(t, text):
    return user(t, f'<cross-session-message from="uds:/x.sock" from-name="master" from-mode="bypass">\n{text}\n</cross-session-message>', "peer")


def reply(t, mid, inp=100, out=10):
    return json.dumps({"type": "assistant", "timestamp": iso(t), "message": {"id": mid, "model": "claude-opus-5-5", "role": "assistant",
                       "usage": {"input_tokens": inp, "output_tokens": out}, "content": [{"type": "text", "text": "ok"}]}})


a = START + 3600
pa = home / ".claude" / "projects" / "-ws-a"
pb = home / ".claude" / "projects" / "-ws-b"
for d_ in (pa, pb, pa / "s1" / "subagents"):
    d_.mkdir(parents=True, exist_ok=True)
(pa / "s1.jsonl").write_text("\n".join([
    user(START - 600, "prima del rinnovo", "human"), reply(START - 500, "m0", inp=99999),   # non conta
    reply(START + 10, "m0b", inp=200, out=0),                    # dopo il rinnovo, ancora il turno di prima: richieste 200
    user(a, "fai la cosa", "human"), reply(a + 5, "m1"),         # richieste 150
    reply(a + 6, "m1"),                                           # lo stesso messaggio a pezzi: una volta
    user(a + 100, "Dall'utente via telefono. ciao"), reply(a + 105, "m2", inp=300),   # richieste 350
    user(a + 200, "<task-notification>finito</task-notification>", "task_notification"), reply(a + 205, "m3", inp=50, out=0),   # resta a richieste: 50
    peer(a + 300, "Giro dei suggerimenti delle 19"), reply(a + 305, "m4", inp=1000, out=0),   # automazioni 1000
    peer(a + 400, "fai il compito X"), reply(a + 405, "m5", inp=500, out=0),                  # sessioni 500
    json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": iso(a + 500), "compactMetadata": {"preTokens": 4000}}),   # 4000 * 0,05 = 200
    reply(a + 505, "m6", inp=70, out=0),                                                        # la prima dopo: automazioni 70
]) + "\n")
(pa / "s1" / "subagents" / "agent-1.jsonl").write_text(reply(a + 410, "s1", inp=30, out=0) + "\n")   # sul turno che l'ha avviato: sessioni 30
(pb / "s2.jsonl").write_text("\n".join([
    user(a + 600, "Per ogni progetto qui sotto hai i messaggi di fine turno di una sessione", "sdk"), reply(a + 605, "r1", inp=400, out=0),   # automazioni 400
    user(a + 700, "Apri la pagina e compila il form", "sdk"), reply(a + 705, "r2", inp=250, out=0),   # sessioni 250
]) + "\n")
f1.write_text(json.dumps({"five_hour_used_pct": 5, "weekly_used_pct": 30.0, "weekly_resets_at": RESET}))
os.utime(f1, (now, now))
(wk.parent / "week-usage.json").unlink()   # la lettura finita di Q5 (nessuna trascrizione) vale 5 minuti
o = json.loads(run("--json", "--no-screen").stdout)["personale"]["settimana_origini"]
EQ = {"richieste": 750, "automazioni": 1670, "sessioni": 780}
TOT = sum(EQ.values())
T.check("Q6 the week's eq by origin over every transcript of the account since the reset (requests, sessions, automations)",
        o and o["eq"] == EQ and o["completo"] is True, json.dumps(o))
T.check("Q6 the three in points of the weekly percentage, one decimal, summing to it",
        o and all(o[k] == round(30.0 * EQ[k] / TOT, 1) for k in EQ) and abs(sum(o[k] for k in EQ) - 30.0) <= 0.15, json.dumps(o))

# ------------------------------------------------------------------ Q7 la lettura a pezzi
spec = importlib.util.spec_from_file_location("mu", T.SCRIPTS / "cm-master-usage.py")
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg)})
mu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mu)
mu.week_path().unlink()
w1 = mu.week_origins({"personale": (str(home / ".claude"), RESET)}, budget=-1)
w2 = mu.week_origins({"personale": (str(home / ".claude"), RESET)})
T.check("Q7 out of time → completo False and nothing counted yet; the next call finishes with the same count",
        w1["personale"]["completo"] is False and w2["personale"]["completo"] is True
        and {k: w2["personale"][k] for k in EQ} == EQ, f"{w1} {w2}")

T.rm(tmp)
T.finish()
