#!/usr/bin/env python3
"""supervisor suggest (contratto 1.88, 27/10/2026): la coda dei suggerimenti — i tipi nuovi, rank e source, la finestra
di 12 voci senza prompt, «Non ora» che regge allo script orario, choose e discuss, i tipi spenti, e `suggest fill`, lo
script a costo zero che tiene in coda le cose aperte (seguiti a meta', lavoro non salvato, attese esterne) e scrive il
riassunto per i giri della master. Tutto su file temporanei: suggestions.json, lo stato e il registro veri non si
toccano."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-suggest-"))
SG = tmp / "suggestions.json"
STATE = tmp / "state.json"
DIGEST = tmp / "suggest-digest.md"
LEDGER = tmp / "ledger.jsonl"
(tmp / "config.json").write_text(json.dumps({"language": "it", "state_dir": str(tmp)}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE")}
ENV.update(CM_SUGGESTIONS=str(SG), CM_RELAY_STATE=str(STATE), CM_SUGGEST_DIGEST=str(DIGEST), CM_TASKS_DB=str(tmp / "tasks.db"),
           CC_SUPERVISOR_CONFIG=str(tmp / "config.json"), CM_OBSERVE_DIR=str(tmp / "observe-none"))   # mai le note vere
os.environ.update(ENV)
spec = importlib.util.spec_from_file_location("cm_suggest", T.SCRIPTS / "cm-suggest.py")
SGM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SGM)


def sg(*a):
    p = subprocess.run([str(T.SCRIPTS / "supervisor"), "suggest", *a], capture_output=True, text=True, env=ENV, timeout=30)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def items():
    return json.loads(SG.read_text())["items"] if SG.exists() else []


def by_key():
    return {x["key"]: x for x in items()}


def reset(d=None):
    SG.write_text(json.dumps(d or {"items": [], "never": []}))


NOW = int(time.time())

# ------------------------------------------------------------------ S1: un file di prima (1.84) si legge come allora
reset({"items": [{"id": "s-0000000001", "kind": "cleanup", "key": "cleanup:x", "text": "Archivia i rami vecchi", "prompt": "archivia",
                  "at": NOW - 10, "expires_at": NOW + 3600}], "never": ["k-old"]})
st1 = SGM.for_state(NOW)
T.check("S1 a 1.84 file: read as before, the item with rank 5 and source master, no prompt in the state",
        [(x["id"], x["rank"], x["source"]) for x in st1] == [("s-0000000001", 5, "master")] and "prompt" not in st1[0], str(st1))

# ------------------------------------------------------------------ S3: la finestra dello stato
reset()
for i in range(13):
    sg("add", "--kind", "proposal", "--key", f"proposal:p{i:02d}", "--text", f"Proposta numero {i}", "--prompt", f"fai la proposta {i}", "--rank", str(i % 10))
sg("add", "--kind", "idea", "--key", "idea:dopo", "--text", "Un'idea per piu' avanti", "--prompt", "l'idea", "--after", "2099-01-01", "--rank", "0")
st3 = SGM.for_state(NOW + 1)
T.check("S3 suggest_queue_refill: the state carries 12 items, ordered by rank then newest, never the prompt; an idea with --after in the future stays in the file and out of the state",
        len(st3) == 12 and [x["rank"] for x in st3] == sorted(x["rank"] for x in st3) and all("prompt" not in x for x in st3)
        and "idea:dopo" in by_key() and all(x["key"] != "idea:dopo" for x in st3) and len(items()) == 14, str([(x["key"], x["rank"]) for x in st3]))
rc3 = [sg("add", "--kind", "nope", "--key", "k", "--text", "t", "--prompt", "p")[0], sg("add", "--kind", "idea", "--key", "k", "--text", "t", "--prompt", "p", "--rank", "10")[0],
       sg("add", "--kind", "idea", "--key", "k", "--text", "t", "--prompt", "p", "--source", "robot")[0], sg("add", "--kind", "idea", "--key", "k", "--text", "t", "--prompt", "p", "--after", "29/10")[0]]
T.check("S3 refused: an unknown kind, a rank outside 0-9, a source other than script|master, --after not AAAA-MM-GG", all(r != 0 for r in rc3), str(rc3))
reset()
sg("add", "--kind", "idea", "--key", "idea:cb", "--text", "Prova chrome-bridge sul telefono", "--prompt", "prova", "--after", time.strftime("%Y-%m-%d", time.localtime(NOW + 2 * 86400)), "--ttl", "12h")
x3 = by_key()["idea:cb"]
T.check("S3 --after: the ttl runs from that day, not from now (an idea with a date does not expire before it shows)", x3["expires_at"] >= x3["after"] + 12 * 3600 - 1 and x3["after"] > NOW, json.dumps(x3))

# ------------------------------------------------------------------ S4: «Non ora» regge allo script orario
reset()
id4 = sg("add", "--kind", "followup", "--key", "followup:field-notes:aa", "--text", "field-notes e' ferma", "--prompt", "p1", "--source", "script")[1]
code4, _ = SGM.act(id4, "later", NOW)
id4b = sg("add", "--kind", "followup", "--key", "followup:field-notes:aa", "--text", "field-notes e' ferma da ieri", "--prompt", "p2", "--source", "script")[1]
x4 = by_key()["followup:field-notes:aa"]
T.check("S4 suggest_queue_refill: an add --source script on the same key keeps id, at and hidden, and takes the new text",
        code4 == "hidden" and id4b == id4 and x4["id"] == id4 and x4.get("hidden") and x4["text"] == "field-notes e' ferma da ieri" and x4["prompt"] == "p2"
        and SGM.for_state(NOW) == [], json.dumps(x4))
rc4 = sg("reshow")
T.check("S4 reshow (the master's round): the hidden items show again, same id", rc4[0] == 0 and [x["id"] for x in SGM.for_state()] == [id4], str(rc4))
SGM.act(id4, "later", NOW)
# un testo diverso da id4: l'id nasce da key, secondo e testo, e nello stesso secondo coinciderebbe per caso (28/10)
id4c = sg("add", "--kind", "followup", "--key", "followup:field-notes:aa", "--text", "field-notes e' ferma, dalla master", "--prompt", "p3")[1]
T.check("S4 an add of the master on the same key renews it as in 1.84: new id, shown again, source master",
        id4c != id4 and not by_key()["followup:field-notes:aa"].get("hidden") and by_key()["followup:field-notes:aa"]["source"] == "master", json.dumps(by_key()))

# ------------------------------------------------------------------ S5: choose e discuss
reset()
id5 = sg("add", "--kind", "choice", "--key", "choice:privacy", "--text", "Privacy del sito: quale testo?", "--prompt", "Per la privacy del sito la persona sceglie {choice}: procedi",
         "--options", "Breve|Completa")[1]
c5a = SGM.act(id5, "choose", NOW, choice="Forse")
c5b = SGM.act(id5, "discuss", NOW)
x5 = by_key().get("choice:privacy")
c5c = SGM.act(id5, "choose", NOW, choice="Completa")
h5 = json.loads(SG.read_text()).get("history") or []
T.check("S5 choose: a label outside options → bad_choice and the item stays; discuss → discussed and the item stays; a valid label → chosen, the item gone, history {key, kind, source, action: chosen}",
        c5a[0] == "bad_choice" and c5b[0] == "discussed" and x5 and x5["options"] == ["Breve", "Completa"] and c5c[0] == "chosen" and c5c[1]["id"] == id5
        and "choice:privacy" not in by_key() and [(h["key"], h["kind"], h["source"], h["action"]) for h in h5] == [("choice:privacy", "choice", "master", "chosen")],
        str((c5a, c5b, c5c, h5)))
rc5 = [sg("add", "--kind", "idea", "--key", "k1", "--text", "t", "--prompt", "p", "--options", "A|B")[0],
       sg("add", "--kind", "choice", "--key", "k2", "--text", "t", "--prompt", "p", "--options", "A")[0],
       sg("add", "--kind", "choice", "--key", "k3", "--text", "t", "--prompt", "p", "--options", "A|B|C|D|E")[0],
       sg("add", "--kind", "choice", "--key", "k4", "--text", "t", "--prompt", "p")[0]]
T.check("S5 --options only with choice, 2 to 4 labels, and a choice needs them", all(r != 0 for r in rc5) and not items(), str(rc5))
T.check("S5 for_state carries options on a choice", (SGM.act(sg("add", "--kind", "choice", "--key", "k5", "--text", "t", "--prompt", "p", "--options", "A|B")[1], "discuss", NOW)
                                                     and SGM.for_state()[0]["options"] == ["A", "B"]), str(SGM.for_state()))

# ------------------------------------------------------------------ S9: i tipi spenti
reset({"items": [], "never": [], "history": [{"key": f"idea:i{i}", "kind": "idea", "source": "master", "action": "never" if i % 2 else "expired", "at": NOW - 3600 * i}
                                             for i in range(8)] + [{"key": "proposal:p0", "kind": "proposal", "source": "master", "action": "expired", "at": NOW - 20 * 86400}] * 9})
off9 = SGM.kind_off(SGM.load(), NOW)
rc9 = sg("add", "--kind", "idea", "--key", "idea:new", "--text", "Ancora un'idea", "--prompt", "p")
T.check("S9 a kind with 8 items closed in 14 days and none accepted is off: add of it exits 0 and writes nothing; closings older than 14 days do not count",
        off9 == {"idea"} and rc9[0] == 0 and "idea:new" not in by_key(), str((off9, rc9)))
rc9b = sg("kind", "on", "idea")
sg("add", "--kind", "idea", "--key", "idea:new", "--text", "Ancora un'idea", "--prompt", "p")
rc9c = sg("kind", "off", "proposal")
T.check("S9 kind on brings it back (only closings after it count); kind off switches one off by hand; the digest says which are off",
        rc9b[0] == 0 and "idea:new" in by_key() and rc9c[0] == 0 and SGM.kind_off(SGM.load(), NOW) == {"proposal"}, str((rc9b, rc9c, SGM.kind_off(SGM.load(), NOW))))
sg("kind", "on", "proposal")

# ------------------------------------------------------------------ fill: la prova di fatto della spec (§10)
H = 3600


def fixture_state(dirty=2):
    return {"v": 1, "sessions": [
        {"name": "field-notes", "state": "idle", "since": NOW - 5 * H,
         "next_steps": [{"text": "Aprire la PR del README", "blocking": False}, {"text": "Scegliere il titolo della release", "blocking": True}]},
        {"name": "atlas-shop", "state": "idle", "since": NOW - 1 * H, "next_steps": [{"text": "Rifare i test", "blocking": False}]},
        {"name": "ledger-api", "state": "idle", "since": NOW - 100 * H, "next_steps": [{"text": "Vecchio seguito", "blocking": False}]},
        {"name": "master", "state": "idle", "since": NOW - 6 * H, "next_steps": [{"text": "Il giro", "blocking": False}]},
        {"name": "lint-bot", "state": "busy", "since": NOW - 9 * H, "next_steps": [{"text": "Lavora", "blocking": False}]},
        {"name": "orbit-docs", "state": "gone", "since": NOW - 7 * H,
         "closed": {"at": NOW - 7 * H, "session_id": "0b5e7c1d-0000-4000-8000-00000000000d", "dirty_n": dirty, "dirty": ["docs/a.md", "docs/b.md"][:dirty]}},
        {"name": "old-gone", "state": "gone", "since": NOW - 30 * H, "closed": {"at": NOW - 30 * H, "session_id": None}},
    ]}


COMMITS = [{"task": "ticket-4411", "title": "Ticket del fornitore", "session": "atlas-shop",
            "paused": {"reason": "ticket 4411 del fornitore", "at": NOW - 3 * 86400, "by": "master", "waits": "external", "recheck": NOW - 60}},
           {"task": "risposta-cliente", "title": "Risposta del cliente", "session": "atlas-shop",
            "paused": {"reason": "risposta del cliente", "at": NOW - 86400, "by": "master", "waits": "external", "recheck": NOW + 86400}},
           {"task": "in-pausa", "title": "Pausa normale", "session": "atlas-shop", "paused": {"reason": "dopo la 0.9", "at": NOW, "by": None, "waits": None, "recheck": None}}]
reset({"items": [{"id": "s-master0001", "kind": "proposal", "key": "proposal:ga4", "text": "Proponi GA4 ai siti ancora su Universal Analytics", "prompt": "prepara la proposta GA4",
                  "at": NOW - 100, "expires_at": NOW + 30 * H, "source": "master", "rank": 6},
                 {"id": "s-master0002", "kind": "recurring", "key": "recurring:old", "text": "Una ricorrente scaduta", "prompt": "x",
                  "at": NOW - 99999, "expires_at": NOW - 10, "source": "master"}],
       "never": ["followup:ledger-api:zz"]})
r1 = SGM.fill(NOW, state=fixture_state(), commitments=COMMITS)
mine = {x["kind"]: x for x in items() if x.get("source") == "script"}
f1 = SG.read_bytes()
T.check("S2 suggest_queue_refill: fill on the fixture makes exactly three items — the stopped session's next step (the blocking one first), the closed session's two unsaved files, the external wait past its recheck",
        sorted(mine) == ["followup", "unsaved", "waiting"] and len(mine) == 3
        and mine["followup"]["key"].startswith("followup:field-notes:") and "Scegliere il titolo della release" in mine["followup"]["text"]
        and mine["unsaved"]["key"] == "unsaved:orbit-docs:0b5e7c1d-0000-4000-8000-00000000000d" and "orbit-docs" in mine["unsaved"]["text"] and "2" in mine["unsaved"]["text"]
        and mine["waiting"]["key"] == "waiting:task:ticket-4411" and "ticket 4411" in mine["waiting"]["text"]
        and [mine[k]["rank"] for k in ("followup", "unsaved", "waiting")] == [4, 4, 5]
        and all(mine[k]["expires_at"] == NOW + 26 * H for k in mine) and all(len(x["text"]) <= 200 and "…" not in x["text"] and "\n" not in x["text"] for x in mine.values())
        and all(x["prompt"] for x in mine.values()), json.dumps(items(), ensure_ascii=False)[:900])
T.check("S8 fill never touches the master's items; an expired one goes, counted as expired in history and in the ledger",
        by_key()["proposal:ga4"]["id"] == "s-master0001" and "recurring:old" not in by_key()
        and any(h["key"] == "recurring:old" and h["action"] == "expired" for h in json.loads(SG.read_text())["history"])
        and any(json.loads(ln).get("event") == "suggestion" and json.loads(ln).get("action") == "expired" and json.loads(ln).get("kind") == "recurring" for ln in LEDGER.read_text().splitlines()),
        str(r1))
r2 = SGM.fill(NOW, state=fixture_state(), commitments=COMMITS)
T.check("S2 suggest_queue_refill: a second fill with nothing changed leaves the file byte for byte (same ids, same at)", SG.read_bytes() == f1, str(r2))
ids = {k: x["id"] for k, x in mine.items()}
SGM.act(ids["followup"], "later", NOW)
SGM.fill(NOW + H, state=fixture_state(), commitments=COMMITS)
mine2 = {x["kind"]: x for x in items() if x.get("source") == "script"}
T.check("S4 suggest_queue_refill: an hour later the same ids, expires_at moved on by an hour, the item in «Not now» still hidden",
        {k: x["id"] for k, x in mine2.items()} == ids and all(x["expires_at"] == NOW + 27 * H for x in mine2.values()) and mine2["followup"].get("hidden")
        and all(x["key"] != mine2["followup"]["key"] for x in SGM.for_state(NOW + H)), json.dumps(mine2, ensure_ascii=False)[:600])
SGM.fill(NOW + 2 * H, state=fixture_state(dirty=0), commitments=COMMITS)
d7 = json.loads(SG.read_text())
T.check("S7 suggest_queue_refill: dirty_n down to 0 → the unsaved item goes, closed as resolved (not expired); the others stay",
        "unsaved:orbit-docs:0b5e7c1d-0000-4000-8000-00000000000d" not in by_key() and sorted(x["kind"] for x in items() if x.get("source") == "script") == ["followup", "waiting"]
        and [h["action"] for h in d7["history"] if h["key"].startswith("unsaved:")] == ["resolved"]
        and any(json.loads(ln).get("action") == "resolved" and json.loads(ln).get("kind") == "unsaved" and json.loads(ln).get("source") == "script" for ln in LEDGER.read_text().splitlines()),
        json.dumps(d7["history"], ensure_ascii=False)[:500])
st6 = fixture_state()
st6["sessions"][2]["since"] = NOW - 5 * H   # ledger-api ferma da 5 ore: il suo seguito e' in «Mai»
SGM.load()["never"]
k6 = SGM.followup_key("ledger-api", "Vecchio seguito")
d6 = json.loads(SG.read_text()); d6["never"].append(k6); SG.write_text(json.dumps(d6))
SGM.fill(NOW + 2 * H, state=st6, commitments=COMMITS)
T.check("S6 a key in never is never written by fill", k6 not in by_key() and k6 in json.loads(SG.read_text())["never"], k6)
many = {"v": 1, "sessions": [{"name": f"s{i:02d}", "state": "idle", "since": NOW - 5 * H - i, "next_steps": [{"text": f"Passo {i}", "blocking": False}]} for i in range(12)]}
SGM.fill(NOW + 2 * H, state=many, commitments=[])
T.check("S2 at most 8 items of the script (the ones that waited longest first); the master's stay", len([x for x in items() if x.get("source") == "script"]) == 8
        and "proposal:ga4" in by_key() and f"followup:s11:" in " ".join(by_key()), str(len(items())))
# S12 (28/10/2026): il testo e' una domanda di una riga che dice cosa fa Accetta, col progetto per primo; una voce gia'
# in coda col testo di prima prende quello nuovo al fill dopo (stesso id)
st12 = fixture_state()
st12["sessions"][0]["project"] = "work/field-notes"
st12["sessions"][5]["project"] = "work/orbit-docs"
c12 = [dict(COMMITS[0], project="/home/u/work/atlas-shop")]
reset()
SGM.fill(NOW, state=st12, commitments=c12)
d12 = json.loads(SG.read_text())
for x in d12["items"]:
    x["text"] = "orbit-docs: 2 file non committati alla chiusura" if x["kind"] == "unsaved" else x["text"]
SG.write_text(json.dumps(d12))
old12 = {x["kind"]: x["id"] for x in d12["items"]}
SGM.fill(NOW + H, state=st12, commitments=c12)
t12 = {x["kind"]: x for x in items() if x.get("source") == "script"}
T.check("S12 suggest_fill_text_asks: followup, unsaved and waiting say as a one-line question what Accept does, the project first; an item already queued takes the new text on the next fill",
        sorted(t12) == ["followup", "unsaved", "waiting"]
        and t12["followup"]["text"] == "field-notes: la sessione è ferma su «Scegliere il titolo della release», glielo mando?"
        and t12["unsaved"]["text"] == "orbit-docs ha 2 file fuori da git: li faccio committare?"
        and t12["waiting"]["text"] == "atlas-shop: ricontrollo «ticket 4411 del fornitore» e, se è arrivato, riprendo il compito?"
        and {k: x["id"] for k, x in t12.items()} == old12, json.dumps(t12, ensure_ascii=False)[:700])
st12b = fixture_state(dirty=1)
reset()
SGM.fill(NOW, state=st12b, commitments=[])
T.check("S12 suggest_fill_text_asks: one file is «un file», «lo faccio committare?»; without a project the session's name leads",
        by_key()["unsaved:orbit-docs:0b5e7c1d-0000-4000-8000-00000000000d"]["text"] == "orbit-docs ha un file fuori da git: lo faccio committare?",
        json.dumps(items(), ensure_ascii=False)[:500])
before10 = SG.read_bytes()
STATE.unlink(missing_ok=True)
rc10 = sg("fill")
STATE.write_text("{rotto")
rc10b = sg("fill")
T.check("S10 no state file or a broken one: fill exits 0 and changes nothing (the script's items are not dropped for lack of data)",
        rc10[0] == 0 and rc10b[0] == 0 and SG.read_bytes() == before10, str((rc10, rc10b)))
STATE.write_text(json.dumps(fixture_state()))
t0 = time.time()
rc11 = sg("fill")
dt = time.time() - t0
dg = DIGEST.read_text() if DIGEST.exists() else ""
d11 = json.loads(SG.read_text())
T.check("S11 suggest_queue_refill: fill from the CLI reads the state file and the registry, under 2 s, and writes the digest",
        rc11[0] == 0 and dt < 2 and dg and sorted(x["kind"] for x in d11["items"] if x.get("source") == "script") == ["followup", "unsaved"], str((rc11, round(dt, 2))))
d11["never_at"] = dict(d11.get("never_at") or {}, **{"proposal:vecchia": NOW - 20 * 86400, "proposal:siti-lenti": NOW - 3600})
d11["never"] += ["proposal:vecchia", "proposal:siti-lenti"]
SG.write_text(json.dumps(d11))
dg = SGM.digest(NOW)
T.check("S11 the digest: under 2 KB, the queue by kind, the 14-day rate by kind, the kinds off, the never keys of the last 14 days only, no prompt",
        len(dg.encode()) <= 2048 and "followup" in dg and "unsaved" in dg and "proposal:siti-lenti" in dg and "proposal:vecchia" not in dg
        and "prepara la proposta GA4" not in dg and all(x["prompt"] not in dg for x in d11["items"]) and sg("digest")[1].strip() == SGM.digest().strip(), dg)


# ------------------------------------------------------------------ S13 suggest_observe (28/10): le note di claude-observe
# si accumulavano senza che nessuno le leggesse (190 per supervisor). fill propone lo smistamento per plugin
OBS = tmp / "observe"
OBS.mkdir()
os.environ["CM_OBSERVE_DIR"] = str(OBS)
D = 86400


def notes(plugin, specs):
    with open(OBS / f"{plugin}.jsonl", "w") as f:
        for i, (status, age, count, err) in enumerate(specs):
            f.write(json.dumps({"id": f"{plugin}-{i:08x}", "status": status, "first_seen": NOW - age, "last_seen": NOW - 60,
                                "count": count, "error": err, "call": "x"}) + "\n")


notes("alfa", [("new", D, 40, "No tab with id 5")] + [("new", D // 2, 1, f"altro {i}") for i in range(11)])
notes("beta", [("new", 4 * D, 1, "uno"), ("new", D, 2, "due")])
notes("gamma", [("new", D, 1, "g")] * 3)
notes("delta", [("triaged", 9 * D, 3, "d")] * 15)
notes("claude-master", [("new", 30 * D, 1, "vecchio nome")] * 20)
reset()
SGM.fill(NOW, state={"sessions": []}, commitments=[])
ob = {x["key"]: x for x in items() if x["kind"] == "observe"}
week = time.strftime("%G-W%V", time.localtime(NOW))
T.check("S13 suggest_observe: one item per plugin with at least 10 new notes (alfa) or the oldest new one 3 days old or more (beta); not gamma (3, young), not delta (none new), not claude-master (an old name, skipped)",
        sorted(ob) == [f"observe:alfa:{week}", f"observe:beta:{week}"], str(sorted(ob)))
a13 = ob.get(f"observe:alfa:{week}") or {}
since = time.strftime("%d/%m", time.localtime(NOW - D))
T.check("S13 suggest_observe: the text is a question with the plugin first, how many, since when and the most frequent error",
        a13.get("text") == f"alfa: 12 note di errore nuove dal {since}, la più frequente «No tab with id 5»: le passo alla sessione per lo smistamento?",
        a13.get("text"))
T.check("S13 suggest_observe: the master's prompt hands the sorting to the plugin's session, with the commands; source script, rank 5",
        "supervisor observe list alfa" in a13.get("prompt", "") and "mark" in a13.get("prompt", "") and a13.get("source") == "script" and a13.get("rank") == 5,
        a13.get("prompt", "")[:300])
SGM.fill(NOW + 3600, state={"sessions": []}, commitments=[])
T.check("S13 suggest_observe: the key is stable within the week: one more pass keeps the same item",
        sorted(x["key"] for x in items() if x["kind"] == "observe") == sorted(ob) and by_key()[f"observe:alfa:{week}"]["id"] == a13.get("id"), str(items()))
notes("alfa", [("triaged", D, 40, "No tab with id 5")] * 12)
SGM.fill(NOW + 7200, state={"sessions": []}, commitments=[])
T.check("S13 suggest_observe: once the notes are sorted (none new) the item leaves the queue",
        f"observe:alfa:{week}" not in by_key() and f"observe:beta:{week}" in by_key(), str(sorted(by_key())))
# dal vivo (28/10): le note scritte a mano (kind note) hanno error vuoto e il testo in call; la voce diceva «?»
with open(OBS / "eps.jsonl", "w") as f:
    for i in range(10):
        f.write(json.dumps({"id": f"eps-{i}", "status": "new", "kind": "note", "first_seen": NOW - D, "count": 1 + (i == 3), "error": "",
                            "call": f"seo: lettura dalle API mai riuscita {i}"}) + "\n")
SGM.fill(NOW + 9000, state={"sessions": []}, commitments=[])
e13 = by_key().get(f"observe:eps:{week}") or {}
T.check("S13 suggest_observe: a note written by hand (empty error, its text in call) is named by its text, never «?»",
        "«seo: lettura dalle API mai riuscita 3»" in e13.get("text", ""), e13.get("text"))
os.environ["CM_OBSERVE_DIR"] = ENV["CM_OBSERVE_DIR"]

T.finish()
