#!/usr/bin/env python3
"""Verifica cm-relay.py (fase 1 dell'app polso, design docs/plans/2026-09-12-app-polso-design.md) con un
Firebase finto (RTDB REST + SSE, token, FCM), il dispatcher finto e il claude finto.

R0   le fixture in tests/fixtures/relay sono identiche al contratto v1 dell'app (claude-master-watch/contract);
     il RTDB finto: PUT/GET/PATCH/DELETE, print=silent, shallow, stream SSE (put iniziale, poi patch/put)
R1   crypto: chiave 32 byte su file 0600, AES-256-GCM {"v":1,"enc"} con nonce diverso ogni volta, chiave
     sbagliata → ValueError; X25519 + HKDF simmetrico; check del codice; token del service account (JWT RS256
     firmato, scope firebase.database + messaging) con cache su file
R2   build_state dalle sorgenti == contratto (state-1, state-2, state-3): ordine waiting/busy/idle/gone poi
     alfabetico, short ≤ 60, full ≤ 600, domanda intera, n da 1, ≤ 8 KB (fit_state)
R3   eventi dal diff di due stati == forma di events-sample (question, answered, outcome, gone, launched, quota)
R4   relay push: --dry-run stampa il JSON in chiaro senza HTTP; push scrive /state cifrato (decifrabile = dry-run),
     /events, FCM; seconda push senza cambiamenti → niente eventi; --async con debounce 2 s (due richieste →
     una scrittura); stato oltre 8 KB ridotto
R5   relay pair: codice a 6 cifre, /pair/<code> con pc_pub, orologio finto che risponde → chiave condivisa
     salvata, uid in /allowed e devices.json, /pair cancellato; check sbagliato ×5 → esce 2; timeout → 3;
     ri-pair revoca l'uid vecchio. 1.15: lo stesso documento in /pair/<id>, il telefono finto risponde li' con
     uids e names; --text stampa il JSON del QR; il QR a mezzi blocchi si decodifica (OpenCV, se c'e')
R5b  i vettori del contratto 1.15 (pair-qr.json, pair-response.json: scalari 0..31 e 32..63) → pair_accept
     produce esattamente `ok`; qr_payload = pair-qr.json
R5c  prove isolate: CC_SUPERVISOR_CONFIG di prova → pair/push/serve solo li', la configurazione principale intatta
R6   relay serve: SSE su /cmd, i sette op del contratto eseguiti via dispatcher finto → /result, /cmd cancellato,
     /state ripubblicato; duplicati ignorati; op fuori allow-list rifiutato; launch fuori da projects rifiutato;
     RTDB giù → riconnessione; status/ensure/install/uninstall/off; install e pair rifiutati senza crontab (esce 5)
R7   cm-hook.py: waiting/<sid> in JSON con tool_input; PermissionRequest/Stop/SessionStart/SessionEnd → push
     --async (relay abilitata); disabilitata → niente; bot follow/unfollow → push
"""
import base64
import fcntl
import json
import re
import shutil
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

WATCH = next((p for p in (Path.home() / "Desktop" / "workspaces" / "personali" / n for n in ("supervisor-app", "team-supervisor-app", "claude-master-watch")) if p.is_dir()), Path.home() / "Desktop" / "workspaces" / "personali" / "supervisor-app")   # percorso vero della macchina, non un nome del set demo
FIX = T.ROOT / "tests" / "fixtures" / "relay"
# 1.21: le op che il relay esegue, lette dal sorgente (l'allow-list OPS), non ricopiate a mano
import ast as _ast, re as _re0  # noqa: E401
R_OPS = list(_ast.literal_eval(_re0.search(r"^OPS = (\(.*?\))$", (T.SCRIPTS / "cm-relay.py").read_text(), _re0.M).group(1)))

# R0: fixture identiche al contratto, quello del branch master dell'app (30/09): il worktree puo' stare su un branch di
# lavoro rimasto indietro, e i test del PC davano rosso per un contratto gia' pubblicato


def app_contract(name):
    if not (WATCH / ".git").exists():
        return None
    # origin/master e' il contratto pubblicato; il master locale del checkout dell'app puo' restare indietro (30/09:
    # 41 commit dopo un merge fatto da un altro worktree). Il master locale solo se il remoto non c'e'
    for ref in ("origin/master", "master"):
        r = subprocess.run(["git", "-C", str(WATCH), "show", f"{ref}:contract/{name}"], capture_output=True)
        if r.returncode == 0:
            return r.stdout
    return None


for f in ("state-1-question", "state-2-idle", "state-3-stale", "events-sample", "cmd-result-sample", "pair-qr", "pair-response", "pair-add", "pair-link", "file-parts", "share-parts", "local-api"):
    a = FIX / f"{f}.json"; b = app_contract(f"{f}.json")
    T.check(f"R0 fixture {f} identical to the app contract on its master (origin/master; skipped if the app repo is absent)", a.is_file() and (b is None or a.read_bytes() == b), f"{WATCH}@origin/master:contract/{f}.json")

# R0b: il RTDB finto
URL, CALLS, STORE = T.fake_rtdb()


def http(method, path, body=None, headers=None):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw else None)


st, body = http("PUT", "/state.json", {"v": 1, "enc": "abc"})
T.check("R0b PUT /state.json stores the document; GET reads it back", st == 200 and http("GET", "/state.json")[1] == {"v": 1, "enc": "abc"} and STORE["state"] == {"v": 1, "enc": "abc"}, str(STORE))
http("PATCH", "/cmd.json", {"u1": {"op": "answer"}})
http("PATCH", "/cmd.json", {"u2": {"op": "screen"}})
T.check("R0b PATCH adds children; shallow lists keys; DELETE removes one", set(http("GET", "/cmd.json?shallow=true")[1]) == {"u1", "u2"} and http("DELETE", "/cmd/u1.json")[0] == 200 and list(STORE["cmd"]) == ["u2"], str(STORE))
st, _ = http("PUT", "/x.json?print=silent", 1)
T.check("R0b print=silent → 204", st == 204, str(st))
events = []


def stream():
    req = urllib.request.Request(URL + "/cmd.json", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=10) as r:
        ev = None
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev != "keep-alive":
                events.append((ev, json.loads(line[6:])))
                if len(events) >= 3:
                    return


th = threading.Thread(target=stream, daemon=True); th.start()
T.wait_until(lambda: len(events) >= 1, 3)
http("PATCH", "/cmd.json", {"u3": {"op": "follow"}})
http("PUT", "/cmd/u4.json", {"op": "resume"})
T.wait_until(lambda: len(events) >= 3, 4)
T.check("R0b SSE: initial put with the node, then a patch and a put for writes under it", len(events) >= 3 and events[0][0] == "put" and events[0][1]["path"] == "/" and events[0][1]["data"] == {"u2": {"op": "screen"}} and events[1][0] == "patch" and events[1][1]["data"] == {"u3": {"op": "follow"}} and events[2] == ("put", {"path": "/u4", "data": {"op": "resume"}}), str(events))

# R1: crypto
import importlib.util as _ilu


def load(name):
    spec = _ilu.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py"); m = _ilu.module_from_spec(spec); spec.loader.exec_module(m); return m


C = load("cm-relay-crypto")
tmp = Path(T.tmpdir())
rdir = tmp / "relay"
k = C.new_key()
kp = C.save_key(rdir, k)
T.check("R1 key: 32 bytes, saved as hex in <dir>/key with mode 0600, loaded back equal; missing → None", len(k) == 32 and kp.name == "key" and oct(kp.stat().st_mode & 0o777) == "0o600" and C.load_key(rdir) == k and C.load_key(tmp / "nope") is None, str(kp))
doc = C.encrypt({"a": 1, "s": "é"}, k)
doc2 = C.encrypt({"a": 1, "s": "é"}, k)
T.check("R1 encrypt → {v:1, enc:base64}, a fresh nonce each time; decrypt round-trips", doc["v"] == 1 and set(doc) == {"v", "enc"} and doc["enc"] != doc2["enc"] and C.decrypt(doc, k) == {"a": 1, "s": "é"}, str(doc)[:80])
try:
    C.decrypt(doc, C.new_key()); bad = False
except ValueError:
    bad = True
try:
    C.decrypt({"v": 2, "enc": doc["enc"]}, k); bad2 = False
except ValueError:
    bad2 = True
T.check("R1 wrong key or wrong form → ValueError", bad and bad2, "")
a_priv, a_pub = C.pair_keys(); b_priv, b_pub = C.pair_keys()
ka, kb = C.shared_key(a_priv, b_pub), C.shared_key(b_priv, a_pub)
T.check("R1 X25519 + HKDF: both sides derive the same 32-byte key; another pair differs", ka == kb and len(ka) == 32 and ka != C.shared_key(a_priv, a_pub), "")
T.check("R1 check_code: 16 hex, depends on the key", len(C.check_code(ka, "123456")) == 16 and C.check_code(ka, "123456") != C.check_code(C.new_key(), "123456") and C.check_code(ka, "123456") == C.check_code(kb, 123456), "")
# service account di prova (RSA generata qui) e token endpoint finto
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
SA = tmp / "service-account.json"
SA.write_text(json.dumps({"type": "service_account", "project_id": "fake-project", "private_key": pem, "client_email": "relay@fake-project.iam.gserviceaccount.com", "token_uri": URL + "/token"}))
tok = C.sa_token(SA, URL + "/token", rdir / "token.json", now=1789210000)
import base64 as _b64
jwt_claims = json.loads(_b64.urlsafe_b64decode(CALLS["token"][-1].split(".")[1] + "=="))
T.check("R1 sa_token: JWT signed and exchanged (iss = client_email, scope has database + messaging, aud = token url), token cached 0600", tok == "fake-token" and jwt_claims["iss"] == "relay@fake-project.iam.gserviceaccount.com" and "firebase.database" in jwt_claims["scope"] and "firebase.messaging" in jwt_claims["scope"] and jwt_claims["aud"] == URL + "/token" and oct((rdir / "token.json").stat().st_mode & 0o777) == "0o600", str(jwt_claims))
n_tok = len(CALLS["token"])
T.check("R1 sa_token: second call within the lifetime → from the cache, no request; past it → a new request", C.sa_token(SA, URL + "/token", rdir / "token.json", now=1789210000 + 3000) == "fake-token" and len(CALLS["token"]) == n_tok and C.sa_token(SA, URL + "/token", rdir / "token.json", now=1789210000 + 3600) == "fake-token" and len(CALLS["token"]) == n_tok + 1, str(len(CALLS["token"])))

# R2: build_state == contratto
S = load("cm-relay-state")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


ROOT_WS = "/home/demo/workspaces"
SES = load("cm-sessions")   # 1.16: le letture dello schermo e del goal che alimentano /state
F1 = json.loads((FIX / "state-1-question.json").read_text())
F2 = json.loads((FIX / "state-2-idle.json").read_text())
F3 = json.loads((FIX / "state-3-stale.json").read_text())
SRC1 = {
    "host": "crostini-demo", "root": ROOT_WS, "prefixes": ["work-"], "high_words": None,
    "rows": [
        {"name": "work-ledger-api", "tmux": "work-ledger-api", "low_priority": "offered", "goal_status": None, "account": "work", "cwd": ROOT_WS + "/work/clients/ledger-api", "status": "waiting", "waiting": True, "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "link": "https://claude.ai/code/session_01CnGG8im7UG4KtjbPDx9fst", "attached": False, "started_at": 1789210000000},
        {"name": "atlas-shop", "tmux": "atlas-shop", "low_priority": "active", "suggestion": "stale text from before the turn", "goal_status": {"text": "All checkout tests green and the release tagged", "since": 1789210700, "met": False}, "account": "personal", "cwd": ROOT_WS + "/personal/atlas-shop", "status": "busy", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": True, "started_at": 1789209000000},
        {"name": "field-notes", "tmux": "field-notes", "low_priority": "off", "suggestion": "commit the README changes and open a PR", "goal_status": None, "account": "personal", "cwd": ROOT_WS + "/personal/field-notes", "status": "idle", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "link": "https://claude.ai/code/session_03fieldnotes", "attached": False, "started_at": 1789120000000},
        {"name": "work-orbit-docs", "tmux": "work-orbit-docs", "low_priority": None, "goal_status": None, "account": "work", "cwd": ROOT_WS + "/work/own/orbit-docs", "status": "dead", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000004", "link": "", "attached": False, "visto_ts": 1789200000},
    ],
    "ledger": [
        {"event": "start", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210000)},
        {"event": "prompt", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210380)},
        {"event": "waiting", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210500), "tool": "AskUserQuestion"},
        {"event": "start", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789209000)},
        {"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789210300), "last": "x", "esito": "Esito: migrations 008-011 applied, tests green.", "tail": "Esito: migrations 008-011 applied, tests green.\nThe test seeds and the admin page are still to review.\nProssimi: !ok to deploy on staging · review the test seeds\nWatch: Migrations 008-011 applied, tests green", "watch": "Watch: Migrations 008-011 applied, tests green"},
        {"event": "prompt", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789210700)},
        {"event": "start", "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "ts": iso(1789120000)},
        {"event": "stop", "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "ts": iso(1789121000), "last": "x", "esito": "Esito: README rewritten with the three sections asked for.", "tail": "Esito: README rewritten with the three sections asked for.\nThe preview build uses PREVIEW_TOKEN=pvw_4f3e2d1c0b9a.\nWatch: README rewritten", "watch": "Watch: README rewritten"},
    ],
    # 1.53: la consigliata e le spiegazioni dal payload dell'hook; 1.56: un token nel testo → question.preview
    "questions": {"work-ledger-api": {"tool": "AskUserQuestion", "text": "Deploy ready with DEPLOY_TOKEN=dpl_9f8e7d6c5b4a, waiting for the client's ok. Deploy now?",
                                      "options": ["yes (Recommended)", "no"], "asked_at": 1789210500,
                                      "descriptions": {"yes (Recommended)": "Deploy 2.4 to production now", "no": "Wait for the client's ok"}}},
    "quota": {"personal": {"cinque_ore_pct": 11, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": False},
              "work": {"cinque_ore_pct": None, "settimana_pct": 75.2, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": True}},
    "projects": [{"path": ROOT_WS + "/work/own/orbit-docs", "name": "orbit-docs", "account": "work", "last_used": 1789203600}, {"path": ROOT_WS + "/personal/atlas-shop", "name": "atlas-shop", "account": "personal", "last_used": 1789210700}, {"path": ROOT_WS + "/work/clients/ledger-api", "name": "ledger-api", "account": "work", "last_used": 1789210500}],
    # 1.17: le righe della coda come le scrive `night add` (added ISO locale, prompt intero)
    "night": {"queued": 2, "running": None, "master": True, "items": [
        {"id": "a3f09c1e", "dir": ROOT_WS + "/personal/atlas-shop", "prompt": "Go through the open issues labelled flaky, reproduce each one locally with the seed from its report, fix the ones that are real and write a short note in docs/notte for the others, then run the full suite twice", "account": "personal", "model": "", "effort": "", "max_turns": 40, "added": iso(1789207200)},
        {"id": "7b21d4e8", "dir": ROOT_WS + "/work/clients/ledger-api", "prompt": "Update the changelog for 2.4 and check the migration notes", "account": "work", "model": "", "effort": "", "max_turns": 40, "added": iso(1789210620)}]},
    "ops": list(R_OPS), "slash": ["compact", "clear", "exit", "context", "cost"], "recap": {"date": "2026-09-12", "items": [{"project": "atlas-shop", "done": "Migrations 008-011 applied, tests green", "next": "Review the seeds and the admin page"}, {"project": "ledger-api", "done": "Deploy ready", "next": "Wait for the go"}]},
    "follow": {"work-ledger-api"}, "awaiting": set(),
    "next": {"work-ledger-api": "Wait for the go", "atlas-shop": "Review the seeds and the admin page", "work-orbit-docs": "Pick up the pricing page"},
    "tools": {"atlas-shop": "Bash pytest -q tests"},
    "tool_notes": {"atlas-shop": "Run the test suite"},
    "icons": {"work-ledger-api": "🟦", "atlas-shop": "🟢", "field-notes": "🟡", "work-orbit-docs": "🟪"},
    "next_at": {"work-ledger-api": 1789171200, "atlas-shop": 1789171200, "work-orbit-docs": 1789171200}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]},
    # 1.11: quello che ogni sessione viva sta usando (la gone non ha trascrizione da rileggere)
    # 1.54: la modalita' dei permessi su due sessioni
    "runtime": {"work-ledger-api": {"model": {"id": "claude-opus-5[1m]", "label": "Opus 5"}, "effort": "high", "context": 62, "permission_mode": "plan"},
                "atlas-shop": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 18, "permission_mode": "bypassPermissions"},
                # 1.14: field-notes e' passata da sola a Sonnet 5 dopo un messaggio segnalato
                "field-notes": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "low", "context": 4,
                                "fallback": {"from": "claude-opus-5-5", "to": "claude-sonnet-5", "category": "cyber", "at": 1789210300}}},
}
KINDS = {"personal": "personal", "work": "work"}   # 1.8: come li calcola il relay dalla config del test
SRC1["account_kinds"] = KINDS
# 1.55: field-notes ha risolto al terminale la sua domanda di prima
SRC1["resolved"] = {"field-notes": {"id": "q-1789120500-1", "text": "Commit the README changes now?", "at": 1789210600}}
# 1.62: atlas-shop e' attaccata e qualcuno ha premuto un tasto 30 s fa → at_pc; 1.64: le cartelle madri dei progetti
SRC1["rows"][1]["pc_activity"] = 1789210770
SRC1["project_parents"] = [ROOT_WS + "/personal", ROOT_WS + "/work/clients", ROOT_WS + "/work/own"]
# 1.51: il comando dell'handoff per sessione — fable-director, quello di supervisor, nessuno
SRC1["handoffs"] = {"work-ledger-api": "/fable-director:handoff --here", "atlas-shop": "/supervisor:handoff --here", "field-notes": None}
# 1.37: il consiglio di fable-director per atlas-shop (gia' passato da advice_of) e un compito che aspetta l'ok
SRC1["advice"] = {"atlas-shop": {"model": "claude-fable-5-1", "effort": "high", "reason": "Checkout refactor across many files: Fable 5.1 high; switch after the current task",
                                 "switch_cost_tokens": 36000, "at": 1789210700, "source": "fable-director", "when": "next_task", "differs": True}}
SRC1["approvals"] = [{"task": "atlas-release-2-4", "title": "Release 2.4 of atlas-shop", "what": "tag v2.4 and push to origin main",
                      "where": "production (shop.example.com)", "deploy": True, "requested_at": 1789210600}]
# 1.32: i dispositivi accoppiati — telefono e orologio (kind dal pairing e dal nome), tablet e Chromebook (dal pairing),
# uno mai visto; seen in s (il relay converte i ms di /seen)
DEV_SRC = {"devices": {"phoneUid00000000000000000000": {"name": "Pixel 9", "paired_at": 1789100000, "kind": "phone"},
                       "watchUid0000000000000000000": {"name": "Pixel Watch 5", "paired_at": 1789100000},
                       "tabletUid0000000000000000000": {"name": "Pixel Tablet", "paired_at": 1789150000, "kind": "tablet"},
                       "crbookUid0000000000000000000": {"name": "Chromebook", "paired_at": 1789160000, "kind": "chromebook"},
                       "spareUid00000000000000000000": {"name": "Pixel 7", "paired_at": 1789170000},
                       "webUid000000000000000000000": {"name": "Safari on Mac", "paired_at": 1789180000, "kind": "web"}},
           "seen": {"phoneUid00000000000000000000": 1789210790.0, "watchUid0000000000000000000": 1789210500.4,
                    "tabletUid0000000000000000000": 1789209000.0, "crbookUid0000000000000000000": 1789190000.0}}
# 1.33: le azioni ricorrenti della master, gia' in ordine (l'ultima usata in cima), una con param
DEV_SRC["recurring"] = [{"id": "x-posts", "label": "Review this week's posts on X", "prompt": "Review this week's posts on X and suggest replies", "param": False},
                        {"id": "release-changelog", "label": "Changelog of a release", "prompt": "Write the changelog of release", "param": True},
                        {"id": "plugin-rivals", "label": "What competing plugins shipped", "prompt": "Check what the competing plugins shipped this week and sum it up", "param": False}]
SRC1.update(DEV_SRC)
st1 = S.build_state(SRC1, 1789210800)


def diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            d = diff(a.get(k), b.get(k), f"{path}.{k}")
            if d:
                return d
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    return "" if a == b else f"{path}: {a!r} != {b!r}"


# 1.43 (08/10): published_at lo mette _push subito prima della PUT, non build_state: nel confronto non conta
_np = lambda d: {kk: v for kk, v in d.items() if kk != "published_at"}  # noqa: E731
T.check("R2 build_state(src) == state-1-question.json (four sessions, two accounts, quota, projects, night, recap)", st1 == _np(F1), diff(st1, _np(F1)) or "equal")
# R12 (contratto 1.16, 25/09): low_priority («off» | «offered» | «active» | null) e goal ({text, since, met} | null) per sessione
# R13 (contratto 1.17, 29/09): la coda di stanotte nello stato, sempre presente; prompt a fine parola entro 160, senza «…»
T.check("R13 (1.17) state-1: night.items in queue order with id, dir, name (as projects[].name), prompt, added (epoch), started null",
        [(i["id"], i["name"], i["added"], i["started"]) for i in st1["night"]["items"]] == [("a3f09c1e", "atlas-shop", 1789207200, None), ("7b21d4e8", "ledger-api", 1789210620, None)]
        and set(st1["night"]["items"][0]) == {"id", "dir", "name", "prompt", "added", "started"}, str(st1["night"]))
_p0 = st1["night"]["items"][0]["prompt"]
T.check("R13 (1.17) a long prompt is cut at a word end within 160 characters, no «…»; a short one stays whole",
        len(_p0) <= 160 and SRC1["night"]["items"][0]["prompt"].startswith(_p0 + " ") and "…" not in _p0 and st1["night"]["items"][1]["prompt"] == SRC1["night"]["items"][1]["prompt"], _p0)
T.check("R13 (1.17) cut_words: newlines folded, exactly 160 kept, one word longer than 160 cut at 160",
        S.cut_words("a\n b", 160) == "a b" and S.cut_words("x" * 160, 160) == "x" * 160 and S.cut_words("y" * 200, 160) == "y" * 160 and S.cut_words(("w" * 9 + " ") * 16 + "z", 160) == " ".join(["w" * 9] * 16), S.cut_words(("w" * 9 + " ") * 16 + "z", 160))
T.check("R13 (1.17) an empty queue still carries night.items = [] (its presence tells the app the relay can edit the queue)",
        S.build_state({"night": {"queued": 0}}, 1)["night"] == {"queued": 0, "running": None, "items": []}, str(S.build_state({"night": {"queued": 0}}, 1)["night"]))
# R20 (contratto 1.23, 30/09): il prompt suggerito in grigio, per il chip del telefono — solo a sessione ferma al prompt
T.check("R20 (1.23) suggestion: the idle session carries its dim suggestion; the busy one gets null even if its row had one; waiting and gone null",
        [(x["name"], x["suggestion"]) for x in st1["sessions"]] == [("ledger-api", None), ("atlas-shop", None), ("field-notes", "commit the README changes and open a PR"), ("orbit-docs", None)], str([(x["name"], x["suggestion"]) for x in st1["sessions"]]))
T.check("R12 (1.16) state-1: low_priority offered/active/off/null and goal {text, since, met} on the busy session, null elsewhere",
        [x["low_priority"] for x in st1["sessions"]] == ["offered", "active", "off", None] and st1["sessions"][1]["goal"] == {"text": "All checkout tests green and the release tagged", "since": 1789210700, "met": False} and all(x["goal"] is None for i, x in enumerate(st1["sessions"]) if i != 1), str([(x["low_priority"], x["goal"]) for x in st1["sessions"]]))
_lp = SES.low_priority_on_screen
T.check("R12 (1.16) the screen decides: «Working at lower priority» → active, «/low-priority to continue now at lower priority» → offered, the menu line «Continue now at lower priority» → offered, a plain screen → off, no screen → null",
        _lp("x", "…\n  Working at lower priority · waiting for capacity\n") == "active" and _lp("x", "Usage limit reached · /low-priority to continue now at lower priority · uses your weekly limit") == "offered"
        and _lp("x", "  ❯ 1. Stop and wait for limit to reset\n    2. Continue now at lower priority\n") == "offered" and _lp("x", "  ❯ \n") == "off" and _lp("x", "") is None, "")
_gt = tmp / "goal-transcript.jsonl"
_gt.write_text('{"type":"attachment","uuid":"a","timestamp":"2026-09-25T12:00:00.000Z","attachment":{"type":"goal_status","met":false,"sentinel":true,"condition":"la suite passa"}}\n'
               '{"type":"attachment","uuid":"b","timestamp":"2026-09-25T12:10:00.000Z","attachment":{"type":"goal_status","met":false,"sentinel":false,"iterations":3,"condition":"la suite passa"}}\n')
_row = {"account": "personale", "session_id": "goal-transcript", "cwd": str(tmp)}
_orig = SES.transcript_of
SES.transcript_of = lambda r: _gt
g_open = SES.goal_of(_row)
_gt.write_text(_gt.read_text() + '{"type":"attachment","uuid":"c","timestamp":"2026-09-25T12:20:00.000Z","attachment":{"type":"goal_status","met":true,"sentinel":false,"iterations":4,"condition":"la suite passa"}}\n')
g_met = SES.goal_of(_row)
_gt.write_text(_gt.read_text() + '{"type":"attachment","uuid":"d","timestamp":"2026-09-25T12:21:00.000Z","attachment":{"type":"goal_status","met":true,"sentinel":true,"condition":"la suite passa"}}\n')
g_cleared = SES.goal_of(_row)
SES.transcript_of = _orig
T.check("R12 (1.16) goal_of: open → {condition, since = the set sentinel's timestamp (epoch), met false}; the check that finds it met → met true; the clear sentinel → none",
        g_open == {"condition": "la suite passa", "iterations": 3, "since": 1790337600, "met": False} and g_met is not None and g_met["met"] is True and g_met["since"] == 1790337600 and g_cleared is None, str((g_open, g_met, g_cleared)))
T.check("R2 rules: order waiting/busy/idle/gone then alphabetical, short ≤ 200 (1.6), full ≤ 600, n from 1, ≤ 8 KB", [x["state"] for x in st1["sessions"]] == ["waiting", "busy", "idle", "gone"] and all(len(x["outcome"]["short"]) <= 200 and len(x["outcome"]["full"]) <= 600 for x in st1["sessions"] if x["outcome"]) and [o["n"] for o in st1["sessions"][0]["question"]["options"]] == [1, 2] and S.size_of(st1) <= 8192, str(S.size_of(st1)))
SRC2 = {"host": "crostini-demo", "root": ROOT_WS, "prefixes": ["work-"],
        "rows": [{"name": "atlas-shop", "tmux": "atlas-shop", "low_priority": "off", "goal_status": None, "account": "personal", "cwd": ROOT_WS + "/personal/atlas-shop", "status": "idle", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": False, "started_at": 1789214000000}],
        "ledger": [{"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789213900), "last": "x", "esito": "Esito: seeds and admin page reviewed, 42 tests green.", "tail": "Esito: seeds and admin page reviewed, 42 tests green.\nWatch: Seeds and admin page reviewed", "watch": "Watch: Seeds and admin page reviewed"}],
        "questions": {}, "quota": {"personal": {"cinque_ore_pct": 24, "settimana_pct": 38, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": False}, "work": {"cinque_ore_pct": 3, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": False}},
        "projects": [{"path": ROOT_WS + "/personal/atlas-shop", "name": "atlas-shop", "account": "personal", "last_used": 1789213900}], "night": {"queued": 1, "running": None, "master": True, "items": [{"id": "c41d9e07", "dir": ROOT_WS, "name": "master", "prompt": "Do this agenda item: Move the docs site to the new host (orbit-docs)", "account": "personal", "model": "", "effort": "", "max_turns": 40, "added": iso(1789213000)}]}, "ops": list(R_OPS), "slash": ["compact", "clear", "exit", "context", "cost"], "recap": {"date": "2026-09-12", "items": []},
        "follow": set(), "awaiting": set(), "next": {"atlas-shop": "Test deploy on staging"}, "tools": {}, "icons": {"atlas-shop": "🟢"}, "next_at": {"atlas-shop": 1789171200}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]},
        "runtime": {"atlas-shop": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 18}}}
SRC2["account_kinds"] = KINDS
SRC2.update(DEV_SRC)
st2 = S.build_state(SRC2, 1789214400)
T.check("R2 build_state(src) == state-2-idle.json", st2 == _np(F2), diff(st2, _np(F2)) or "equal")
SRC3 = {"host": "crostini-demo", "root": ROOT_WS, "prefixes": [], "rows": [], "ledger": [], "questions": {},
        "quota": {"personal": {"cinque_ore_pct": 0, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": True}, "work": {"cinque_ore_pct": None, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": True}},
        "projects": [], "night": {"queued": 0, "running": None, "master": True}, "ops": list(R_OPS), "slash": ["compact", "clear", "exit", "context", "cost"], "recap": {"date": "2026-09-12", "items": []}, "follow": set(), "awaiting": set(), "next": {}, "tools": {}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]}}
SRC3["account_kinds"] = KINDS
SRC3.update(DEV_SRC)
st3 = S.build_state(SRC3, 1789200000)
T.check("R2 build_state(src) == state-3-stale.json", st3 == _np(F3), diff(st3, _np(F3)) or "equal")
T.check("R2 (1.33) no recurring list → no `recurring` field (the app shows no box)", "recurring" not in S.build_state(dict(SRC3, recurring=[]), 1789200000), "")
T.check("R2 (1.8) every session carries account_kind and every quota entry its kind (personal | work)",
        all(x["account_kind"] in ("personal", "work") for x in st1["sessions"]) and st1["quota"]["personal"]["kind"] == "personal" and st1["quota"]["work"]["kind"] == "work", str([(x["name"], x["account"], x["account_kind"]) for x in st1["sessions"]]))
T.check("R2 (1.8) kinds_of: an explicit kind wins; else the default account is personal and the others work; a single account is personal",
        S.kinds_of({"a": {}, "b": {}}, "a") == {"a": "personal", "b": "work"} and S.kinds_of({"x": {}}, "") == {"x": "personal"} and S.kinds_of({"a": {"kind": "work"}, "b": {"kind": "personal"}}, "a") == {"a": "work", "b": "personal"}, "")
big = dict(SRC1, devices={}, seen={}, recurring=[]); big["projects"] = [{"path": f"{ROOT_WS}/personal/p{i:03d}", "name": f"p{i:03d}", "account": "personal"} for i in range(120)]
stb = S.build_state(big, 1789210800)
T.check("R2 over 8 KB → fit_state trims (old gone sessions, projects past 10, …), the question stays whole", S.size_of(stb) <= 8192 and stb["sessions"][0]["question"]["text"] == F1["sessions"][0]["question"]["text"] and len(stb["projects"]) <= 10, str(S.size_of(stb)))
# (job-*: work- e' il prefisso tmux dell'account work; 60 progetti: lo stato resta oltre gli 8 KB finche' le sessioni
# finite non scendono a tre anche con i nomi inglesi, piu' corti)
# 14/09 dal vivo: tre sessioni al lavoro con un esito lungo + dieci finite → prima ogni full diventava short
live = dict(SRC1, devices={}, seen={}, recurring=[], approvals=[], advice={}, project_parents=[])   # 1.32 (e 1.37): questi casi misurano i tagli delle sessioni, senza dispositivi ne approvazioni
long_tail = "Ho messo in pausa a un punto pulito; i passi per riprendere sono nel piano. " * 9
live["rows"] = [{"name": f"job-{i}", "tmux": f"job-{i}", "account": "personal", "cwd": ROOT_WS + f"/personal/job-{i}", "status": "idle", "waiting": False, "session_id": f"sid-job-{i}", "link": "https://claude.ai/code/session_" + "x" * 24, "attached": False, "started_at": 1789200000000} for i in range(3)] + \
    [{"name": f"old-{i:02d}", "tmux": f"old-{i:02d}", "account": "personal", "cwd": ROOT_WS + f"/personal/old-progetto-{i:02d}", "status": "dead", "session_id": f"0000000{i:02d}-aaaa-bbbb-cccc-dddddddddddd", "link": "https://claude.ai/code/session_" + "y" * 24, "visto_ts": 1789100000 + i, "started_at": 1789000000000} for i in range(10)]
# 14/10: senza «on both suites» (la 1.58 allunga ops di 15 byte e lo stato stava a un byte dal tetto)
live["ledger"] = [{"event": "stop", "session_id": f"sid-job-{i}", "ts": iso(1789210000), "tail": long_tail + "\nWatch: " + "paused at a clean point; resume steps are in the plan and the tests are green", "watch": "Watch: paused at a clean point; resume steps are in the plan and the tests are green"} for i in range(3)]
live["questions"] = {}
live["projects"] = [{"path": f"{ROOT_WS}/personal/progetto-{i:02d}", "name": f"progetto-{i:02d}", "account": "personal"} for i in range(60)]
stl = S.build_state(live, 1789210800)
works = [x for x in stl["sessions"] if x["name"].startswith("job-")]
gones = [x["name"] for x in stl["sessions"] if x["state"] == "gone"]
T.check("R2 (14/09) over 8 KB with ten gone sessions: the outcomes keep a real full (> 300, not the short), the three most recent gone stay, under the cap", S.size_of(stl) <= 8192 and len(works) == 3 and all(len(x["outcome"]["full"]) > 300 and x["outcome"]["full"] != x["outcome"]["short"] for x in works) and gones == ["old-07", "old-08", "old-09"], f"{S.size_of(stl)} {[len(x['outcome']['full']) for x in works]} {gones}")
# 1.7 (14/09 dall'app: recap.items = 0 con 9 sessioni e 10 progetti): lo stesso stato con dieci voci di recap lunghe
recap_long = {"date": "2026-09-12", "items": [{"project": f"progetto-{i:02d}", "done": "Migrazioni applicate e verificate, test verdi su tutte e due le suite. " * 2, "next": "Rivedere i seed, la pagina admin e il deploy di prova su staging. " * 2} for i in range(10)]}
str_ = S.build_state(dict(live, recap=recap_long), 1789210800)
T.check("R2 (1.7) the recap is no longer the first thing to go: with ten gone sessions and thirty projects it keeps its items (done/next cut at a word), under the cap",
        S.size_of(str_) <= 8192 and len(str_["recap"]["items"]) >= 1 and all(len(i["done"]) <= S.RECAP_CUT and len(i["next"]) <= S.RECAP_CUT for i in str_["recap"]["items"]), f"{S.size_of(str_)} items={len(str_['recap']['items'])}")
import copy
tiny = S.fit_state(copy.deepcopy(dict(S.build_state(dict(live, recap=recap_long), 1789210800))), max_kb=2)
# 23/09 (dal polso: «Session closed» per sessioni vive e ferme): fit_state toglieva sessioni VIVE dal fondo quando lo
# stato sfiorava gli 8 KB, e gli eventi calcolati sullo stato tagliato davano «gone»
crowd = S.build_state(live, 1789210800, fit=False)
for i in range(3):   # un caso in cui i tagli leggeri bastano; oltre (13 sessioni da ~600 byte) una sessione deve cadere
    crowd["sessions"].append(dict(copy.deepcopy(crowd["sessions"][0]), name=f"viva-{i}", state="idle", question=None,
                                  tool_note="nota " * 20, next="prossimo passo " * 8))
crowd["projects"] = crowd["projects"] * 3
cut = S.fit_state(copy.deepcopy(crowd), max_kb=8)
live_names = {x["name"] for x in crowd["sessions"] if x["state"] != "gone"}
T.check("R2 (23/09) a state over the cap: the lighter cuts (idle sessions' tool_note/next, projects to five) keep every live session",
        S.size_of(crowd) > 8192 and live_names <= {x["name"] for x in cut["sessions"]} and S.size_of(cut) <= 8192,
        f"{S.size_of(crowd)} → {S.size_of(cut)} {sorted(live_names - {x['name'] for x in cut['sessions']})}")
tight = S.fit_state(copy.deepcopy(crowd), max_kb=3)
ev_full, _ = S.events_between(crowd, crowd, 1789210900, 1)
dropped = live_names - {x["name"] for x in tight["sessions"]}
ev_cut, _ = S.events_between(crowd, tight, 1789210900, 1)
T.check("R2 (23/09, 06/10) even under a tight cap no live session is trimmed, so none is ever «gone» by the cut; events come from the full state",
        not dropped and not [e for e in ev_full if e["kind"] == "gone"] and not [e for e in ev_cut if e["kind"] == "gone"],
        f"dropped={sorted(dropped)} gone_full={[e['session'] for e in ev_full if e['kind'] == 'gone']}")
relay_src = (T.SCRIPTS / "cm-relay.py").read_text()
T.check("R2 (23/09) _push diffs full states and keeps the full one in last-state.json",
        "full = S.build_state(src, now, fit=False)" in relay_src and 'last.get("full") or last.get("state")' in relay_src
        and '"full": full' in relay_src, "")
# 06/10 (regola del maintainer delle 10:57; rino sparita dall'app): oltre il tetto, con molte sessioni vive, nessuna
# viva esce mai — via le finite, i campi facoltativi e i progetti, e se non basta lo stato resta sopra il tetto
many = copy.deepcopy(crowd)
for i in range(3, 14):
    many["sessions"].append(dict(copy.deepcopy(crowd["sessions"][0]), name=f"viva-{i}", state="idle", question=None, tool_note="nota " * 20))
_live_many = {x["name"] for x in many["sessions"] if x["state"] != "gone"}
fat = S.fit_state(copy.deepcopy(many), max_kb=8)
T.check("R2 (06/10) a state that cannot fit: every LIVE session stays (never removed without consent), the gone ones, the optional fields and the projects go first",
        {x["name"] for x in fat["sessions"]} == _live_many and not any(x["state"] == "gone" for x in fat["sessions"]) and fat["projects"] == []
        and all(x["outcome"] is None or x["outcome"]["full"] == x["outcome"]["short"] for x in fat["sessions"]), f"{len(_live_many)} live, {len(fat['sessions'])} kept, {S.size_of(fat)} bytes")
T.check("R2 (1.7) even under a tiny cap one recap item stays, cut to RECAP_CUT at a word (items go from the end, never the last)",
        len(tiny["recap"]["items"]) == 1 and tiny["recap"]["items"][0]["project"] == "progetto-00" and len(tiny["recap"]["items"][0]["done"]) <= S.RECAP_CUT, str(tiny["recap"]))
T.check("R2 (1.6) short = the whole Watch line up to 200, cut at a word, no «…»", works[0]["outcome"]["short"] == "paused at a clean point; resume steps are in the plan and the tests are green" and S.short_of("parola " * 40, S.SHORT_MAX) == ("parola " * 40)[:200].rsplit(" ", 1)[0].rstrip() and "…" not in S.short_of("parola " * 40, S.SHORT_MAX), works[0]["outcome"]["short"])
T.check("R2 cut_at_word keeps newlines and cuts at a word", S.cut_at_word("uno due\ntre quattro", 12) == "uno due\ntre" and S.cut_at_word("corto", 50) == "corto", repr(S.cut_at_word("uno due\ntre quattro", 12)))
T.check("R2 tier_of: dangerous words in a PERMISSION → high; Read → low; Bash → medium; ask/plan → always medium", S.tier_of("permission", "Bash", "rm -rf build") == "high" and S.tier_of("permission", "Read", "cat x") == "low" and S.tier_of("permission", "Bash", "ls") == "medium" and S.tier_of("ask", "AskUserQuestion", "Deploy now?") == "medium" and S.tier_of("ask", None, "git push origin main?") == "medium" and S.tier_of("permission", "Bash", "git push origin main") == "high", "")
T.check("R2 (1.5) tool_note carries the intent of the running command (null when the session is not working)", st1["sessions"][1]["tool_note"] == "Run the test suite" and st1["sessions"][2]["tool_note"] is None, str([(x["name"], x["tool_note"]) for x in st1["sessions"]]))
T.check("R2 (1.3) quota carries reset_h5, when the 5-hour window restarts (before, the watch showed the weekly reset under the 5-hour figure)", st1["quota"]["personal"]["reset_h5"] == 1789228800 and st1["quota"]["work"]["reset_h5"] == 1789225200 and S.build_quota({"x": {}})["x"]["reset_h5"] is None, str(st1["quota"]))
T.check("R2 (1.2) next_at: the date of the recap line that produced «next» (null when there is no next)", st1["sessions"][0]["next_at"] == 1789171200 and st1["sessions"][2]["next"] is None and st1["sessions"][2]["next_at"] is None, str([(x["name"], x["next_at"]) for x in st1["sessions"]]))
T.check("R2 (1.1) color_of: circle/square/heart of the same hue → the same hex; unknown or empty → None; relay.colors overrides", S.color_of("🟠") == "#F5A623" and S.color_of("🟧") == "#F5A623" and S.color_of("🧡") == "#F5A623" and S.color_of("❤️") == "#E74C3C" and S.color_of("⬜") == "#BDC3C7" and S.color_of("") is None and S.color_of("🐙") is None and S.color_of("🟠", {"🟠": "#111111"}) == "#111111", "")
T.check("R2 (1.1) a session without an icon → icon and color null (old readers: grey)", S.build_session({"name": "x", "tmux": "x", "status": "idle"}, {"root": "/", "prefixes": []})["icon"] is None and S.build_session({"name": "x", "tmux": "x", "status": "idle"}, {"root": "/", "prefixes": []})["color"] is None, "")
T.check("R2 awaiting state: a session with a wrist prompt pending is «awaiting» (ordered with busy)", S.state_of({"status": "idle", "tmux": "x"}, {"x"}) == "awaiting" and S.ORDER["awaiting"] == S.ORDER["busy"], "")

# R3: eventi dal diff
EV = json.loads((FIX / "events-sample.json").read_text())
# 1.56-1.58: preview (quando qualcosa e' nascosto) e i campi di quota_limit sono facoltativi; gli altri sempre
EV_OPT = {"preview", "reset_at", "window", "other_account", "actions", "reason", "continue_text", "quiet"}
ev_ok = lambda e: set(EV[0]) <= set(e) <= set(EV[0]) | EV_OPT  # noqa: E731
ev, seq = S.events_between(F2, F1, 1789210800, 1)
T.check("R3 state-2 → state-1: launched ledger-api, question ledger-api, outcome atlas-shop, launched field-notes; keys <ts>_<seq>; shape of events-sample", [(e["kind"], e["session"]) for e in ev] == [("launched", "ledger-api"), ("question", "ledger-api"), ("outcome", "atlas-shop"), ("launched", "field-notes")] and ev[0]["key"] == "1789210800_001" and ev[3]["key"] == "1789210800_004" and seq == 5 and all(ev_ok(e) for e in ev) and ev[1]["title"] == "❓ ledger-api" and ev[1]["body"] == F1["sessions"][0]["question"]["text"] and ev[1]["ref"] == "q-1789210500-1" and ev[2]["body"] == "Migrations 008-011 applied, tests green" and ev[0]["body"] == "work/clients/ledger-api", str(ev))
ev2, _ = S.events_between(F1, F2, 1789214400, 1)
T.check("R3 state-1 → state-2: outcome atlas-shop (new at), gone ledger-api and field-notes (vanished), nothing for orbit-docs (already gone)", [(e["kind"], e["session"]) for e in ev2] == [("outcome", "atlas-shop"), ("gone", "ledger-api"), ("gone", "field-notes")] and ev2[1]["title"] == "✗ ledger-api", str(ev2))
q_prev = {"sessions": [F1["sessions"][0]], "quota": {"personal": {"h5": 90, "w7": 30, "reset_w7": 1789610400, "stale": False}}}
q_cur = {"sessions": [dict(F1["sessions"][0], question=None)], "quota": {"personal": {"h5": 96, "w7": 30, "reset_w7": 1789610400, "stale": False}}}
ev3, _ = S.events_between(q_prev, q_cur, 1789210900, 7)
T.check("R3 question gone → answered (ref = question id); quota crossing warn_pct → «⚠ 96 % personal» with the reset time", [(e["kind"], e["key"]) for e in ev3] == [("answered", "1789210900_007"), ("quota", "1789210900_008")] and ev3[0]["ref"] == "q-1789210500-1" and ev3[1]["title"] == "⚠ 96 % personal" and ev3[1]["account"] == "personal" and ev3[1]["body"].startswith("reset "), str(ev3))

# R4: relay push contro il Firebase finto, con dispatcher finto e stato finto della macchina
home = tmp / "home"; (home / ".claude" / "waiting").mkdir(parents=True)
ws = home / "ws"
for d in ("personal/atlas-shop/docs", "personal/field-notes", "work/clients/ledger-api", "work/own/orbit-docs", ".claude"):
    (ws / d).mkdir(parents=True)
(ws / "personal" / "atlas-shop" / "docs" / "recap.md").write_text("# Recap\n\n- 2026-09-12: Migrazioni applicate · prossimo: Review the seeds and the admin page\n")
state_dir = home / ".claude"
argslog = tmp / "cm-args.log"
alive = tmp / "alive.json"
good_json = tmp / "good.json"      # la fotografia del registro (registry --good)
launch_adds = tmp / "launch-adds.json"   # se c'e', launch la copia su alive: la sessione nata dal lancio
GOOD_ORBIT = {"nome": "work-orbit-docs", "cartella": str(ws / "work" / "own" / "orbit-docs"), "account": "work", "visto": "2026-09-12T09:00:00"}
good_json.write_text(json.dumps({"sessioni": [GOOD_ORBIT]}))
# 0319ab: il talk finto scrive il record nella casella come quello vero (cm-inbox.put, origin «session»)
fake_talk = tmp / "fake-talk.py"
fake_talk.write_text(f"""import importlib.util, sys
spec = importlib.util.spec_from_file_location("cm_inbox", {str(T.SCRIPTS / "cm-inbox.py")!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.put(sys.argv[1], sys.argv[2], "supervisor")
""")
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  sessions) cat "{alive}" ;;
  registry) if [ "$2" != "--closed" ]; then cat "{good_json}"; fi ;;
  quota) echo '{{"personal": {{"cinque_ore_pct": 11, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": false}}, "work": {{"cinque_ore_pct": null, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": true}}}}' ;;
  answer) if [ "$3" = "--show" ]; then if [ "$2" = "work-ledger-api" ]; then echo "«$2» chiede — Deploy: Deploy ready, waiting for the client ok. Deploy now?"; echo "  ❯ 1. yes"; echo "    2. no"; else echo "nessuna domanda aperta sullo schermo"; exit 1; fi; else case "$3" in 1|2) echo "«$2»: risposto $3. yes  (Deploy now?)" ;; --text) echo "«$2»: risposto 3. $4  (Deploy now?)" ;; --chat) echo "«$2»: risposto 4. Chat about this  (Deploy now?)" ;; *) echo "opzione $3 inesistente" >&2; exit 2 ;; esac; fi ;;
  screen) i=1; while [ $i -le 30 ]; do echo "riga $i dello schermo"; i=$((i+1)); done ;;
  panel) if [ "$2" = "field-notes" ]; then echo "   Session"; echo "   Total cost:            \$0.42"; else exit 1; fi ;;
  talk) if [ -f "{tmp / 'talk-saved'}" ]; then echo "supervisor talk: «$2» è chiusa; messaggio m1 salvato nella casella, le arriva quando riparte (stato: supervisor talk --status m1)" >&2; else python3 "{fake_talk}" "$2" "$3"; echo "consegnato"; fi ;;
  model) echo "$2: model Sonnet 5, this session only" ;;
  effort) if [ "$2" = "atlas-shop" ]; then echo "atlas-shop is working: try again when it is idle"; exit 3; else echo "$2: effort $3, this session only"; fi ;;
  report) python3 "{fake_talk}" "$6" "segnalazione: $4"; if [ "$3" != "-" ]; then cp "$3" "{tmp / 'report-img'}"; echo "segnalazione consegnata a «$6»"; echo "  immagine: $2/docs/segnalazioni/2026-09-12-the-client-says-the-checkout-button-is-g.jpg"; else echo "segnalazione consegnata a «$6»"; fi ;;
  interrupt) case "$2" in atlas-shop) echo "$2: fermata" ;; field-notes) echo "$2: niente da fermare" >&2; exit 1 ;; *) echo "supervisor interrupt: «$2» non è viva" >&2; exit 3 ;; esac ;;
  night) case "$2" in
      add) if [ "$4" = "FULL" ]; then echo "coda piena: 8 lavori, night.max_queued è 8" >&2; exit 4; fi; echo "in coda: 7b21d4e8 · $3 · account «work» (2 in coda)" ;;
      remove) case "$3" in 5d0e6b92) echo "tolta: $3" ;; a3f09c1e) echo "$3 è già partito: non si può togliere" >&2; exit 5 ;; *) echo "nessuna voce con id $3" >&2; exit 1 ;; esac ;;
    esac ;;
  task) case "$2" in list) if [ -f "{tmp / 'tasks.json'}" ]; then cat "{tmp / 'tasks.json'}"; else echo '[]'; fi ;; approve) echo "" ;; esac ;;
  launch) echo "sessione avviata"; echo "  link: https://claude.ai/code/session_01NEW"; if [ -f "{launch_adds}" ]; then cp "{launch_adds}" "{alive}"; fi ;;
esac
""")
fake_cm.chmod(0o755)


def rows_alive(*names, **over):
    base = {"ledger-api": {"pid": 7, "name": "work-ledger-api", "tmux": "work-ledger-api", "cwd": str(ws / "work" / "clients" / "ledger-api"), "status": "waiting", "waiting": True, "link": "https://claude.ai/code/session_01L", "account": "work", "session_id": "S-L", "attached": False, "started_at": 1789210000000},
            "atlas-shop": {"pid": 8, "name": "atlas-shop", "tmux": "atlas-shop", "cwd": str(ws / "personal" / "atlas-shop"), "status": "busy", "waiting": False, "link": "https://claude.ai/code/session_01A", "account": "personal", "session_id": "S-A", "attached": True, "started_at": 1789209000000},
            "field-notes": {"pid": 9, "name": "field-notes", "tmux": "field-notes", "cwd": str(ws / "personal" / "field-notes"), "status": "idle", "waiting": False, "link": "https://claude.ai/code/session_01F", "account": "personal", "session_id": "S-F", "attached": False, "started_at": 1789120000000}}
    rows = [dict(base[n], **over.get(n, {})) for n in names]
    alive.write_text(json.dumps(rows))


rows_alive("ledger-api", "atlas-shop", "field-notes")
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {"questions": [{"question": "Deploy now?"}]}}))
ledger = state_dir / "ledger.jsonl"
ledger.write_text("\n".join(json.dumps(r) for r in [
    {"ts": iso(1789210380), "event": "prompt", "session_id": "S-L"},
    {"ts": iso(1789210500), "event": "waiting", "session_id": "S-L", "tool": "AskUserQuestion"},
    {"ts": iso(1789210300), "event": "stop", "session_id": "S-A", "last": "x", "esito": "Esito: migrations 008-011 applied, tests green.", "tail": "Esito: migrations 008-011 applied, tests green.\nRestano da rivedere i seed.\nWatch: Migrazioni applicate, test verdi", "watch": "Watch: Migrazioni applicate, test verdi"},
    {"ts": iso(1789210700), "event": "prompt", "session_id": "S-A"},
]) + "\n")
tgdir = home / ".claude" / "channels" / "telegram"
tgdir.mkdir(parents=True, exist_ok=True)
(tgdir / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tgdir / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
TG_API, TG_CALLS, _ = T.fake_telegram()
rdir2 = tmp / "relay-live"
C.save_key(rdir2, k)
cfg = tmp / "config.json"


def write_cfg(enabled=True, **extra):
    d = {"language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state_dir), "default_account": "personal",
         "workspace": {"root": str(ws), "excluded_dirs": [".git"], "project_dirs": ["personal", "work/clients", "work/own"]},
         "folder_map": [{"path": str(ws / "work"), "account": "work"}, {"path": str(ws / "personal"), "account": "personal"}],
         "bot": {"api_base": TG_API, "token_file": str(tgdir / ".env"), "access_file": str(tgdir / "access.json")},
         "accounts": {"personal": {"config_dir": str(home / ".claude")}, "work": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "work-"}},
         "relay": {"enabled": enabled, "firebase_url": URL, "service_account": str(SA), "token_url": URL + "/token", "fcm_url": URL,
                   "dir": str(rdir2), "host": "crostini-test", "debounce_s": 1, "fcm_topic": "watch", "duplicate_prompt_s": 0,
                   "adb": str(Path(__file__).resolve().parent / "lib" / "fake-adb.py"), **extra},   # 1.32: mai l'adb vero
         # i modelli delle fixture del contratto (Opus 5, 1.12): dal 22/09 il default e' Opus 5.5 (2.1.280), e le fixture
         # restano identiche a quelle dell'app (R0), che si aggiornano dal suo lato
         "tune": {"models": [{"id": m["id"], "label": m["label"], "pick": m["label"]}
                             for m in json.loads((FIX / "state-1-question.json").read_text())["choices"]["models"]]}}
    cfg.write_text(json.dumps(d))


write_cfg()
rdir2.mkdir(parents=True, exist_ok=True)
(rdir2 / "follow.json").write_text(json.dumps(["work-ledger-api"]))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
       "CM_RELAY_NOW": "1789210840"}   # 1.29: solo la cronologia lo legge (i dati di prova sono del 12/09/2026)


def relay(*args, timeout=60, env=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), *args], capture_output=True, text=True, env=env or ENV, timeout=timeout)


def cm_calls():
    return argslog.read_text().splitlines() if argslog.exists() else []


# 14/09 (dall'app): attesa vista solo sullo schermo, senza file dell'hook e senza nulla da estrarre → niente «?»
rows_alive("ledger-api", "atlas-shop", "field-notes", **{"field-notes": {"status": "waiting", "waiting": True}})
fq = next(x for x in json.loads(relay("push", "--dry-run").stdout)["sessions"] if x["name"] == "field-notes")
T.check("R4 waiting seen only on screen, no waiting/<sid>, nothing extracted → no question, the session back to idle (no open turn in the ledger), never «?»", fq["state"] == "idle" and fq["question"] is None, str(fq))
# senza evento waiting nel ledger l'asked_at e' il primo avvistamento (la push precedente), non «adesso»
rows_alive("ledger-api", "atlas-shop", "field-notes")
ledger_bak = ledger.read_text()
ledger.write_text("\n".join(l for l in ledger_bak.splitlines() if '"waiting"' not in l) + "\n")
(rdir2 / "last-state.json").write_text(json.dumps({"state": {"sessions": [{"name": "ledger-api", "question": {"asked_at": 1789210123}}]}}))
lq = json.loads(relay("push", "--dry-run").stdout)["sessions"][0]
T.check("R4 no waiting event in the ledger → asked_at = first sighting (the previous push), not now", lq["name"] == "ledger-api" and lq["question"]["asked_at"] == 1789210123, str(lq.get("question")))
(rdir2 / "last-state.json").unlink()
ledger.write_text(ledger_bak)
n_req = len(CALLS["requests"])
r = relay("push", "--dry-run")
dry = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("{") else {}
T.check("R4 push --dry-run: clear JSON on stdout, no HTTP; sessions ordered ❓ ▶ ✓ ✗ with short names, the question whole with kind ask and options 1-2, the busy session's outcome from the ledger (short = Watch line), gone from the snapshot, quota, projects with accounts from folder_map, recap, night", r.returncode == 0 and len(CALLS["requests"]) == n_req and [(x["name"], x["state"]) for x in dry.get("sessions", [])] == [("ledger-api", "waiting"), ("atlas-shop", "busy"), ("field-notes", "idle"), ("orbit-docs", "gone")] and dry["sessions"][0]["question"]["text"] == "Deploy ready, waiting for the client ok. Deploy now?" and dry["sessions"][0]["question"]["kind"] == "ask" and [o["label"] for o in dry["sessions"][0]["question"]["options"]] == ["yes", "no"] and dry["sessions"][0]["question"]["asked_at"] == 1789210500 and dry["sessions"][0]["followed"] is True and dry["sessions"][0]["project"] == "work/clients/ledger-api" and dry["sessions"][1]["outcome"]["short"] == "Migrazioni applicate, test verdi" and dry["sessions"][1]["turn_started"] == 1789210700 and dry["sessions"][1]["next"] == "Review the seeds and the admin page" and dry["sessions"][3]["since"] == S.epoch("2026-09-12T09:00:00") and dry["quota"]["work"] == {"h5": None, "w7": 75, "reset_w7": 1789444800, "reset_h5": 1789225200, "stale": True, "kind": "work"} and {(p["name"], p["account"]) for p in dry["projects"]} == {("atlas-shop", "personal"), ("field-notes", "personal"), ("ledger-api", "work"), ("orbit-docs", "work")} and dry["host"] == "crostini-test" and dry["night"] == {"queued": 0, "running": None, "items": [], "master": True} and dry["v"] == 1, r.stdout[:600] + r.stderr)
T.check("R4 (1.1) every live session carries icon (from cm-color's registry, stable) and color «#RRGGBB»; the gone one has none on the first push", all(x["icon"] and re.match(r"^#[0-9A-F]{6}$", x["color"] or "") for x in dry["sessions"] if x["state"] != "gone") and dry["sessions"][3]["icon"] is None, str([(x["name"], x["icon"], x["color"]) for x in dry["sessions"]]))
r = relay("push")
_pub4 = C.decrypt(STORE["state"], k) if r.returncode == 0 and STORE.get("state") else {}
T.check("R4 push: exit 0, /state on the bus is {v:1, enc} and decrypts to the same document as the dry-run (but ts and published_at)", r.returncode == 0 and set(STORE.get("state", {})) == {"v", "enc"} and STORE["state"]["v"] == 1 and {kk: v for kk, v in _pub4.items() if kk not in ("ts", "published_at")} == {kk: v for kk, v in dry.items() if kk != "ts"}, r.stdout + r.stderr + str(STORE.get("state"))[:100])
evs = {kk: C.decrypt(v, k) for kk, v in list((STORE.get("events") or {}).items())}
T.check("R4 first push: /events has launched ×3 and the question (encrypted, key <ts>_<seq>); FCM sent one data message per event with kind and session; last-state.json written", sorted(e["kind"] for e in evs.values()) == ["launched", "launched", "launched", "question"] and all(kk == evs[kk]["key"] for kk in evs) and len(CALLS["fcm"]) == 4 and any(m["message"]["data"]["kind"] == "question" and m["message"]["data"]["session"] == "ledger-api" and m["message"]["topic"] == "watch" for m in CALLS["fcm"]) and (rdir2 / "last-state.json").is_file(), str(evs) + str(CALLS["fcm"])[:300])
n_fcm, n_ev = len(CALLS["fcm"]), len(STORE["events"])
ts1 = C.decrypt(STORE["state"], k)["ts"]
time.sleep(1.1)
r = relay("push")
T.check("R4 second push without changes: new ts, no new event, no FCM", r.returncode == 0 and C.decrypt(STORE["state"], k)["ts"] > ts1 and len(STORE["events"]) == n_ev and len(CALLS["fcm"]) == n_fcm, r.stdout + r.stderr)
# la domanda risposta altrove → evento answered; una sessione sparisce → gone
rows_alive("atlas-shop", "field-notes", **{"atlas-shop": {"status": "idle"}})
(state_dir / "waiting" / "S-L").unlink()
r = relay("push")
evs = {kk: C.decrypt(v, k) for kk, v in list(STORE["events"].items())}
new = [e for e in evs.values() if e["key"] not in [] and e["ts"] >= ts1]
T.check("R4 third push: ledger-api vanished → gone; the FCM for it", any(e["kind"] == "gone" and e["session"] == "ledger-api" for e in evs.values()) and CALLS["fcm"][-1]["message"]["data"]["kind"] == "gone", str([(e["kind"], e["session"]) for e in evs.values()]))
# async con debounce: due richieste in mezzo secondo → una sola scrittura di /state
n_put = len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])
t0 = time.time(); r1 = relay("push", "--async"); r2 = relay("push", "--async"); dt = time.time() - t0
puts_back = len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) - n_put   # 08/10: «subito» = la push non e' ancora partita
T.wait_until(lambda: len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) > n_put, 5)
time.sleep(1.5)
T.check("R4 push --async: returns at once (no /state write yet when both calls are back; the time alone depends on the machine's load), one /state write for two requests within the debounce", r1.returncode == 0 and r2.returncode == 0 and puts_back == 0 and dt < 6 and len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) == n_put + 1, f"dt={dt:.2f} puts={len([x for x in CALLS['requests'] if x == ('PUT', '/state.json')]) - n_put}")
# oltre 8 KB: molti progetti → fit_state
for i in range(150):
    (ws / "personal" / f"progetto-con-un-nome-lungo-{i:03d}").mkdir()
r = relay("push", "--dry-run")
big = json.loads(r.stdout)
T.check("R4 a state over 8 KB is trimmed under the cap (projects cut to 10), the question untouched", r.returncode == 0 and S.size_of(big) <= 8192 and len(big["projects"]) <= 10, str(S.size_of(big)))
import shutil
for i in range(150):
    shutil.rmtree(ws / "personal" / f"progetto-con-un-nome-lungo-{i:03d}")
write_cfg(enabled=False)
r = relay("push")
T.check("R4 relay.enabled=false: push exits 2 saying so; --dry-run still works", r.returncode == 2 and "spento" in r.stdout and relay("push", "--dry-run").returncode == 0, r.stdout + r.stderr)
write_cfg()

# crontab finto (pair e install lo cercano prima di fare qualsiasi cosa, 0.4.20)
cron = tmp / "crontab"; cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
ENV["CM_CRONTAB_CMD"] = str(fake_crontab)

# R5: pair con un orologio finto (codice) e un telefono finto (QR, 1.15)
CODE_LINE = [""]   # 1.32: l'ultima riga del codice stampata da pair


def pair_run(timeout=8, *extra):
    """Lancia `pair`, legge stdout fino alla riga del codice; torna (processo, codice, righe prima del codice)."""
    pr = subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--timeout", str(timeout), *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    import re as _re
    head = []
    for _ in range(200):
        line = pr.stdout.readline()
        if not line:
            break
        m = _re.search(r"Codice.*\b(\d{6})\b", line)
        if m:
            CODE_LINE[0] = line.rstrip("\n")
            return pr, m.group(1), head
        head.append(line.rstrip("\n"))
    return pr, "", head


def pair_nodes(code):
    """I nodi di questo giro: quelli con la stessa pc_pub del codice (una conferma `ok` di un giro vecchio non conta)."""
    pub = ((STORE.get("pair") or {}).get(code) or {}).get("pc_pub")
    return {n: v for n, v in (STORE.get("pair") or {}).items() if isinstance(v, dict) and pub and v.get("pc_pub") == pub}


def pair_stop(pr):
    """Ctrl-C al pair in corso: il suo `finally` toglie i nodi dal bus."""
    import signal as _sig
    pr.send_signal(_sig.SIGINT); pr.wait(timeout=10)


pr, code, head = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
nodes = pair_nodes(code)
pid_node = next((n for n in nodes if n != code), "")
T.check("R5 pair: a six-digit code on stdout; /pair/<code> has the PC's public key, host and exp", len(code) == 6 and nodes.get(code, {}).get("pc_pub") and nodes[code]["host"] == "crostini-test" and nodes[code]["exp"] > time.time(), str(STORE.get("pair")))
T.check("R5 (1.15) the same {pc_pub, host, exp} under /pair/<id>, the id 22 base64url characters; without the app's Firebase data no QR, said in one line above the code", len(pid_node) == 22 and re.fullmatch(r"[A-Za-z0-9_-]{22}", pid_node) and nodes[pid_node] == nodes[code] and len(nodes) == 2 and any("niente QR" in l and "relay.firebase_app" in l for l in head), f"nodes={list(nodes)} head={head}")
w_priv, w_pub = C.pair_keys()
k_watch = C.shared_key(w_priv, STORE["pair"][code]["pc_pub"])
for i in range(3):   # tre sul codice, due sull'id: i tentativi sono in comune
    http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w_pub, "uid": "u-bad", "name": "intruder", "check": f"{i:016x}"})
    T.wait_until(lambda: not (STORE.get("pair") or {}).get(code, {}).get("watch"), 3)
for i in range(2):
    http("PUT", f"/pair/{pid_node}/watch.json", {"watch_pub": w_pub, "uid": "u-bad", "name": "intruder", "check": f"{i:016x}"})
    T.wait_until(lambda: not (STORE.get("pair") or {}).get(pid_node, {}).get("watch"), 3)
pr.wait(timeout=10)
T.check("R5 five wrong checks (three on the code, two on the id: attempts shared) → exit 2, no key change, no uid allowed, both nodes cleaned", pr.returncode == 2 and C.load_key(rdir2) == k and not (STORE.get("allowed") or {}) and code not in (STORE.get("pair") or {}) and pid_node not in (STORE.get("pair") or {}), f"rc={pr.returncode} " + pr.stdout.read() + pr.stderr.read())
pr, code, head = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
w_priv, w_pub = C.pair_keys()
k_watch = C.shared_key(w_priv, STORE["pair"][code]["pc_pub"])
http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w_pub, "uid": "u1", "name": "watch-pixel5", "check": C.check_code(k_watch, code)})
pr.wait(timeout=10)
out5 = pr.stdout.read()
T.wait_until(lambda: set((STORE.get("pair") or {}).get(code, {})) == {"ok"}, 3)
T.check("R5 right check on the code → exit 0, the shared key saved (0600) and equal to the watch's, u1 in /allowed and devices.json, only the confirmation left under /pair/<code> (the watch still has to read it), /pair/<id> gone, «accoppiato» printed", pr.returncode == 0 and C.load_key(rdir2) == k_watch and STORE.get("allowed") == {"u1": True} and json.loads((rdir2 / "devices.json").read_text())["u1"]["name"] == "watch-pixel5" and set((STORE.get("pair") or {}).get(code, {})) == {"ok"} and STORE["pair"][code]["ok"]["check"] == C.check_code(k_watch, code + ":pc") and pid_node not in STORE["pair"] and "accoppiato: watch-pixel5 (uid u1)" in out5, f"rc={pr.returncode} {out5} {pr.stderr.read()} {STORE.get('allowed')} {STORE.get('pair')}")
# 1.15: il telefono risponde sull'id del QR, con uids e names: tutti gli uid in /allowed, i nomi in devices.json
pr, code, head = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
w2_priv, w2_pub = C.pair_keys(); k2 = C.shared_key(w2_priv, STORE["pair"][pid_node]["pc_pub"])
http("PUT", f"/pair/{pid_node}/watch.json", {"watch_pub": w2_pub, "uid": "u2", "name": "Pixel 9", "check": C.check_code(k2, pid_node),
                                              "uids": ["u2", "u3"], "names": {"u2": "Pixel 9", "u3": "Pixel Watch 5"}})
pr.wait(timeout=10); out5b = pr.stdout.read()
dev = json.loads((rdir2 / "devices.json").read_text())
T.check("R5 (1.15) pairing again from the phone on /pair/<id> with uids and names → exit 0, /allowed = {u2, u3} (u1 revoked), devices.json with both names, new key, ok.check = HMAC(key, id + «:pc») under /pair/<id>, /pair/<code> gone and the previous confirmation swept", pr.returncode == 0 and STORE.get("allowed") == {"u2": True, "u3": True} and {u: d["name"] for u, d in dev.items()} == {"u2": "Pixel 9", "u3": "Pixel Watch 5"} and C.load_key(rdir2) == k2 and set(STORE["pair"]) == {pid_node} and STORE["pair"][pid_node] == {"ok": {"host": "crostini-test", "check": C.check_code(k2, pid_node + ":pc")}} and "Pixel 9 (uid u2)" in out5b and "Pixel Watch 5 (uid u3)" in out5b, f"rc={pr.returncode} {out5b} {STORE.get('allowed')} {STORE.get('pair')} {dev}")
pr, code, head = pair_run(2)
pr.wait(timeout=10)
T.check("R5 nobody answers → exit 3 at the timeout, both nodes cleaned", pr.returncode == 3 and not pair_nodes(code), f"rc={pr.returncode} {STORE.get('pair')}")
# R5d (contratto 1.30, 04/10, chiesto dall'app per il tablet): pair --help non accoppia; pair --add aggiunge senza revocare
_n5d = len(STORE.get("pair") or {})
_h = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--help"], capture_output=True, text=True, env=ENV, timeout=30)
_u = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--bogus"], capture_output=True, text=True, env=ENV, timeout=30)
T.check("R5d pair --help prints the usage (with --add) and exits 0; an unknown argument prints it on stderr and exits 2; neither touches /pair or the key",
        _h.returncode == 0 and "relay pair [--add]" in _h.stdout and "Codice" not in _h.stdout and _u.returncode == 2 and "relay pair [--add]" in _u.stderr
        and len(STORE.get("pair") or {}) == _n5d and C.load_key(rdir2) == k2, _h.stdout + _h.stderr + _u.stderr)
_kp = C.key_path(rdir2); _kp.rename(_kp.with_suffix(".away"))
_na = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--add", "--timeout", "3"], capture_output=True, text=True, env=ENV, timeout=30)
_kp.with_suffix(".away").rename(_kp)
T.check("R5d pair --add with no saved key → exit 2 «nothing to add to», no node on /pair", _na.returncode == 2 and "niente a cui aggiungersi" in _na.stdout and len(STORE.get("pair") or {}) == _n5d, _na.stdout + _na.stderr)
_dev_before = (rdir2 / "devices.json").read_text()
http("PUT", "/events/e-keep.json", {"v": 1, "enc": "kept"}); http("PUT", "/result/r-keep.json", {"v": 1, "enc": "kept"})
pr, code, head = pair_run(8, "--add")
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
T.check("R5d (1.30) pair --add: both nodes carry mode «add» besides pc_pub, host, exp", all((STORE["pair"][n] or {}).get("mode") == "add" for n in (code, pid_node)), str(STORE.get("pair"))[:300])
t_priv, t_pub = C.pair_keys(); kt = C.shared_key(t_priv, STORE["pair"][pid_node]["pc_pub"])
http("PUT", f"/pair/{pid_node}/watch.json", {"watch_pub": t_pub, "uid": "u4", "name": "Pixel Tablet", "check": C.check_code(kt, pid_node)})
pr.wait(timeout=10); out5d = pr.stdout.read()
T.wait_until(lambda: set((STORE.get("pair") or {}).get(pid_node, {})) == {"ok"}, 3)
okd = (STORE.get("pair") or {}).get(pid_node, {}).get("ok") or {}
dev5d = json.loads((rdir2 / "devices.json").read_text())
T.check("R5d (1.30) the tablet answers → exit 0; the relay's key UNCHANGED; /allowed = u2, u3 (still there) + u4; devices.json merged; events and results kept; «aggiunto … 3» printed",
        pr.returncode == 0 and C.load_key(rdir2) == k2 and STORE.get("allowed") == {"u2": True, "u3": True, "u4": True} and set(dev5d) == {"u2", "u3", "u4"} and dev5d["u4"]["name"] == "Pixel Tablet"
        and (STORE.get("events") or {}).get("e-keep") and (STORE.get("result") or {}).get("r-keep") and "aggiunto: Pixel Tablet (uid u4)" in out5d and "dispositivi accoppiati: 3" in out5d, f"rc={pr.returncode} {out5d} {STORE.get('allowed')}")
T.check("R5d (1.30) the confirmation: check = HMAC(pairing key, id + «:pc») as before, and key = {v, enc} that the tablet opens with the pairing key → {key: the relay's key in hex}",
        okd.get("check") == C.check_code(kt, pid_node + ":pc") and C.decrypt(okd.get("key"), kt) == {"key": k2.hex()} and code not in (STORE.get("pair") or {}), str(okd)[:200])
# 1.39: otto posti — altri due gia' accoppiati (5), e una risposta con 4 uid (il massimo di una risposta) fa 9
http("PUT", "/allowed.json", dict(STORE.get("allowed") or {}, u13=True, u14=True))
(rdir2 / "devices.json").write_text(json.dumps(dict(json.loads((rdir2 / "devices.json").read_text()), u13={"name": "u13", "paired_at": 1}, u14={"name": "u14", "paired_at": 1})))
pr, code, head = pair_run(8, "--add")
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
f_priv, f_pub = C.pair_keys(); kf = C.shared_key(f_priv, STORE["pair"][code]["pc_pub"])
http("PUT", f"/pair/{code}/watch.json", {"watch_pub": f_pub, "uid": "u5", "name": "Phone 2", "check": C.check_code(kf, code), "uids": ["u5", "u6", "u9", "u10"]})
pr.wait(timeout=10); out5e = pr.stdout.read()
T.check("R5d (1.30, 1.39: eight places) over 8 devices (5 + 4) → exit 4, the device reads {error: full} on its node, /allowed, devices.json and key unchanged",
        pr.returncode == 4 and (STORE.get("pair") or {}).get(code) == {"error": "full"} and STORE.get("allowed") == {"u2": True, "u3": True, "u4": True, "u13": True, "u14": True}
        and set(json.loads((rdir2 / "devices.json").read_text())) == {"u2", "u3", "u4", "u13", "u14"} and C.load_key(rdir2) == k2 and "RIFIUTATO" in out5e, f"rc={pr.returncode} {out5e} {STORE.get('pair')}")
# torna come prima di R5d: i test dopo contano su u2, u3 e su eventi che si aprono con la chiave
(rdir2 / "devices.json").write_text(_dev_before)
http("PUT", "/allowed.json", {"u2": True, "u3": True}); http("DELETE", "/events/e-keep.json"); http("DELETE", "/result/r-keep.json"); http("DELETE", f"/pair/{code}.json")
# con i dati dell'app Firebase: --text stampa il JSON del QR su una riga (contratto 1.15, pair-qr.json), poi il codice
APP = {"api_key": "AIzaSyD-test-key", "project_id": "fake-project", "app_id": "1:123456789012:android:0a1b2c3d4e5f6a7b"}
write_cfg(firebase_app=APP)
pr, code, head = pair_run(8, "--text")
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
try:
    qr_doc = json.loads(head[-1]) if head else {}
except ValueError:
    qr_doc = {}
T.check("R5 (1.15) pair --text: one JSON line {v:1, i:<id node>, c:pc_pub, h, e:exp, f:{k,p,a,d,t}} above the code, no QR drawn", len(head) == 1 and qr_doc.get("v") == 1 and qr_doc.get("i") == pid_node and qr_doc.get("c") == STORE["pair"][pid_node]["pc_pub"] and qr_doc.get("h") == "crostini-test" and qr_doc.get("e") == STORE["pair"][pid_node]["exp"] and qr_doc.get("f") == {"k": APP["api_key"], "p": APP["project_id"], "a": APP["app_id"], "d": URL, "t": "watch"}, str(head)[:400])
pair_stop(pr)
T.wait_until(lambda: code not in (STORE.get("pair") or {}) and pid_node not in (STORE.get("pair") or {}), 3)


# R5h (13/10, chiesto dal maintainer): accanto al codice il link della web app con l'invito dentro —
# <relay.web_app_url>/#pair=<base64url della riga di --text, senza padding>, e la riga che lo dice segreto
def _after_code(pr_):
    """Le righe dopo il codice: il pair scade da solo (timeout corto) e si legge fino alla fine. Un select sul fd non
    basta: le righe stampate insieme al codice sono gia' nel buffer di Python."""
    out = pr_.stdout.read().splitlines()
    pr_.wait(timeout=10)
    return out


def _web_q(lines, origin):
    m = re.search(re.escape(origin) + r"/#pair=([A-Za-z0-9_-]+)$", lines[0] if lines else "")
    return base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode() if m and "=" not in m.group(1) else None


for _extra, _origin in (({}, "https://supervisor.francescosorrentino.com"), ({"web_app_url": "https://example.web.app/"}, "https://example.web.app")):
    write_cfg(firebase_app=APP, **_extra)
    pr, code, head = pair_run(3, "--text")
    _w = _after_code(pr)
    T.check(f"R5h pair --text prints after the code «Web app: {_origin}/#pair=<base64url of the --text line, no padding>» and a line that says it is secret, {_extra or 'default origin'}",
            head and _web_q(_w, _origin) == head[-1] and _w[0].startswith("Web app: ") and len(_w) > 1 and "Segreto come il QR" in _w[1] and "vale 1 min" in _w[1], str(_w))
write_cfg(firebase_app=APP, web_app_url="")
pr, code, head = pair_run(3, "--add", "--text")
_w = _after_code(pr)
T.check("R5h relay.web_app_url = \"\" → no web app link", code and not any("#pair=" in l for l in _w), str(_w))
write_cfg(firebase_app=APP)
pr, code, head = pair_run(3, "--add", "--text")
_w = _after_code(pr)
T.check("R5h pair --add --text prints the web app link too", any(_web_q([l], "https://supervisor.francescosorrentino.com") == head[-1] for l in _w if "#pair=" in l), str(_w))
T.wait_until(lambda: not any(isinstance(v, dict) and v.get("pc_pub") for v in (STORE.get("pair") or {}).values()), 3)
# senza --text: il QR a mezzi blocchi, margine di 2 moduli, decodificato (OpenCV, se c'e') = quel JSON
pr, code, head = pair_run(8)
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
rows = [l for l in head if l and set(l) <= set("█▀▄ ")]


def qr_decode(rows, scale=6):
    """Le righe a mezzi blocchi tornano immagine (chiaro = blocco) e OpenCV le legge; None senza cv2. Il detector e'
    QRCodeDetectorAruco (OpenCV >= 4.8): quello classico perde circa un QR su dieci a qualunque scala e livello di
    correzione, sempre sugli stessi contenuti (misurato su 40 payload di pair, 25/09), e il test era aleatorio."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    Det = getattr(cv2, "QRCodeDetectorAruco", None)
    if Det is None:   # 08/10: OpenCV < 4.8 (WSL di win, apt) ha solo il classico, aleatorio: come senza cv2
        return None
    img = np.zeros((len(rows) * 2 * scale, len(rows[0]) * scale), np.uint8)
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            img[y * 2 * scale:(y * 2 + 1) * scale, x * scale:(x + 1) * scale] = 255 if ch in "█▀" else 0
            img[(y * 2 + 1) * scale:(y * 2 + 2) * scale, x * scale:(x + 1) * scale] = 255 if ch in "█▄" else 0
    return Det().detectAndDecode(img)[0]


decoded = qr_decode(rows) if rows else ""
T.check("R5 (1.15) pair draws the QR in half blocks: «Inquadra» line, square rows of equal width, a light margin of 2 modules (1 row and 2 columns of full blocks) on every side, then the code", any("Inquadra il QR" in l for l in head) and len(rows) >= 20 and len({len(r) for r in rows}) == 1 and rows[0] == "█" * len(rows[0]) and rows[-1] == "█" * len(rows[0]) and rows[1] != rows[0] and all(r.startswith("██") and r.endswith("██") for r in rows) and abs(len(rows) * 2 - len(rows[0])) <= 1, f"rows={len(rows)} width={len(rows[0]) if rows else 0} head={head[:3]}")
T.check("R5 (1.15) the drawn QR decodes (OpenCV) to the JSON of the QR, with i = the id node on the bus (skipped without cv2)", decoded is None or (decoded and json.loads(decoded)["i"] == pid_node and json.loads(decoded)["c"] == STORE["pair"][pid_node]["pc_pub"]), f"decoded={str(decoded)[:120]}")
# R5e (contratto 1.32, 04/10): zero tocchi sul Chromebook — pair --add apre l'invito nell'app via adb (finto qui)
_adblog = tmp / "adb.log"
ENV.update({"FAKE_ADB_LOG": str(_adblog), "FAKE_ADB_MODE": "ok"})
write_cfg(firebase_app=APP, app_package="com.example.cmaster")
def _pair_lines(*extra, wait_s=6):
    pr_, code_, head_ = pair_run(8, *extra)
    after = []
    t_end = time.time() + wait_s
    import select as _sel
    while time.time() < t_end:
        r_, _, _ = _sel.select([pr_.stdout], [], [], 0.5)
        if r_:
            ln = pr_.stdout.readline()
            if not ln:
                break
            after.append(ln.rstrip("\n"))
            if "invito" in ln:
                break
    pair_stop(pr_)
    return code_, head_, after
_adblog.write_text(""); (rdir2 / "relay.log").write_text("")
code5e, head5e, after5e = _pair_lines("--add", "--text")
_cl5e = CODE_LINE[0]
_line5e = head5e[-1] if head5e else ""
_calls5e = _adblog.read_text().splitlines()
_am = next((c for c in _calls5e if " shell am start " in c), "")
_q = re.search(r"'cmwatch://pair\?q=([A-Za-z0-9_-]+)'", _am)
_dec = base64.urlsafe_b64decode(_q.group(1) + "=" * (-len(_q.group(1)) % 4)).decode() if _q else ""
T.check("R5e (1.32) pair --add with an adb that sees emulator-5554 → get-state, then am start -W VIEW of cmwatch://pair?q=<base64url of the --text line, no padding> for relay.app_package; «invito aperto» in the log",
        _calls5e[:1] == ["-s emulator-5554 get-state"] and _am.startswith("-s emulator-5554 shell am start -W -a android.intent.action.VIEW -d '") and _am.endswith(" com.example.cmaster")
        and _dec == _line5e and "=" not in _q.group(1) and "pair --add: invito aperto su emulator-5554" in (rdir2 / "relay.log").read_text(), str(_calls5e) + str(after5e))
T.check("R5e (1.32) with --add the code line speaks of the app (QR or the line above) and of the minutes, not «scrivilo sull'orologio»",
        "Sul telefono, tablet o Chromebook" in _cl5e and "orologio, 0 min" not in _cl5e and "scrivilo sull'orologio" not in _cl5e and code5e in _cl5e, _cl5e)
ENV["FAKE_ADB_MODE"] = "offline"; _adblog.write_text("")
code5f, head5f, after5f = _pair_lines("--add", "--text", wait_s=4)
T.check("R5e (1.32) adb offline → no am start, nothing opened (not a terminal: no clipboard either), the pairing waits as usual",
        _adblog.read_text().splitlines() == ["-s emulator-5554 get-state"] and not any("invito aperto" in a for a in after5f) and re.fullmatch(r"\d{6}", code5f or ""), _adblog.read_text() + str(after5f))
ENV["FAKE_ADB_MODE"] = "ok"; _adblog.write_text("")
code5g, head5g, _ = _pair_lines("--text", wait_s=2)
T.check("R5e (1.32) a plain pair (not --add) never calls adb and keeps «scrivilo sull'orologio»", _adblog.read_text() == "" and "scrivilo sull'orologio" in CODE_LINE[0], _adblog.read_text() + CODE_LINE[0])
write_cfg(firebase_app=APP)
pair_stop(pr)
T.wait_until(lambda: code not in (STORE.get("pair") or {}) and pid_node not in (STORE.get("pair") or {}), 3)
write_cfg()

# R5b: i vettori del contratto 1.15 — pair-qr.json e pair-response.json (chiave privata del PC = scalare 0..31,
# del telefono = 32..63): pair_accept sull'id della fixture accetta la risposta e produce esattamente `ok`
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
RL = load("cm-relay")
from cryptography.hazmat.primitives.asymmetric import x25519 as _x
from cryptography.hazmat.primitives import serialization as _ser
FQ = json.loads((FIX / "pair-qr.json").read_text()); FR = json.loads((FIX / "pair-response.json").read_text())
pc_priv = _x.X25519PrivateKey.from_private_bytes(bytes(range(32)))
ph_priv = _x.X25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
pc_pub_fx = base64.b64encode(pc_priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)).decode()
k_fx = C.shared_key(ph_priv, FQ["c"])
T.check("R5b fixture pair-qr.json: c is the public key of scalar 0..31, i has 22 base64url chars, v = 1, f has k p a d t", FQ["c"] == pc_pub_fx and len(FQ["i"]) == 22 and FQ["v"] == 1 and set(FQ["f"]) == {"k", "p", "a", "d", "t"}, FQ["c"])
T.check("R5b fixture pair-response.json: watch_pub is the public key of scalar 32..63 and check = HMAC(key, i) of the shared key", FR["watch"]["watch_pub"] == base64.b64encode(ph_priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)).decode() and FR["watch"]["check"] == C.check_code(k_fx, FQ["i"]), FR["watch"]["check"])
got = RL.pair_accept(pc_priv, FQ["i"], FR["watch"], FQ["h"])
T.check("R5b pair_accept(id of the fixture, watch of pair-response.json) → the same key, exactly pair-response.json's `ok`, the devices with uids and names", got is not None and got[0] == k_fx and got[1] == FR["ok"] and {u: d["name"] for u, d in got[2].items()} == FR["watch"]["names"] and list(got[2]) == FR["watch"]["uids"], str(got[1:] if got else got))
T.check("R5b pair_accept with the check computed on the code instead of the id → refused (the check binds the node)", RL.pair_accept(pc_priv, "123456", FR["watch"], FQ["h"]) is None, "")
FA = json.loads((FIX / "pair-add.json").read_text())
tb_priv = _x.X25519PrivateKey.from_private_bytes(bytes(range(64, 96)))
k_add = C.shared_key(tb_priv, FA["qr"]["c"])
got_a = RL.pair_accept(pc_priv, FA["qr"]["i"], FA["watch"], FA["qr"]["h"])
T.check("R5b (1.30) fixture pair-add.json: qr = pair-qr.json with m «add»; the tablet's watch_pub is scalar 64..95; pair_accept gives its check's `ok`; ok.key opens with the pairing key to {key: relay_key}",
        FA["qr"] == dict(FQ, m="add") and FA["watch"]["watch_pub"] == base64.b64encode(tb_priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)).decode()
        and got_a is not None and got_a[0] == k_add and {k_: v_ for k_, v_ in FA["ok"].items() if k_ != "key"} == got_a[1]
        and C.decrypt(FA["ok"]["key"], k_add) == {"key": FA["relay_key"]} and C.decrypt(RL.add_ok(got_a[1], k_add, bytes.fromhex(FA["relay_key"]))["key"], k_add) == {"key": FA["relay_key"]}, json.dumps(FA)[:300])
pla = RL.qr_payload(FQ["i"], FQ["c"], FQ["h"], FQ["e"], {"api_key": FQ["f"]["k"], "project_id": FQ["f"]["p"], "app_id": FQ["f"]["a"]}, add=True)
T.check("R5b (1.30) qr_payload(add=True) = the QR with m «add»; without add no m (the 1.15 QR unchanged)", pla.get("m") == "add" and "m" not in RL.qr_payload(FQ["i"], FQ["c"], FQ["h"], FQ["e"], {"api_key": "k", "project_id": "p", "app_id": "a"}), str(pla))
FL = json.loads((FIX / "pair-link.json").read_text())
T.check("R5b (1.32) fixture pair-link.json: line = pair-add.json's QR compact, uri = pair_link(line) (base64url, no padding)",
        FL["line"] == json.dumps(FA["qr"], ensure_ascii=False, separators=(",", ":")) and RL.pair_link(FL["line"]) == FL["uri"] and "=" not in FL["uri"].split("q=", 1)[1], FL["uri"][:80])
T.check("R5b (1.32) pair_accept keeps the kind the device declares (pair-add.json: tablet) in its devices; a kind outside the list is dropped",
        got_a[2][FA["watch"]["uid"]].get("kind") == "tablet" and "kind" not in RL.pair_accept(pc_priv, FA["qr"]["i"], dict(FA["watch"], kind="toaster"), FA["qr"]["h"])[2][FA["watch"]["uid"]], str(got_a[2]))
T.check("R5b (1.32) kind_of for pairings before 1.32: watch-pixel5 → watch, Pixel Tablet → tablet, Pixel 11 Pro XL → null",
        S.kind_of("watch-pixel5") == "watch" and S.kind_of("Pixel Tablet") == "tablet" and S.kind_of("Pixel 11 Pro XL") is None, "")
pl = RL.qr_payload(FQ["i"], FQ["c"], FQ["h"], FQ["e"], {"api_key": FQ["f"]["k"], "project_id": FQ["f"]["p"], "app_id": FQ["f"]["a"]})
T.check("R5b qr_payload with the fixture's values = pair-qr.json (d and t from this config: firebase_url and fcm_topic)", pl == dict(FQ, f=dict(FQ["f"], d=URL, t="watch")), json.dumps(pl))
fx_rows = RL.qr_lines(json.dumps(FQ, ensure_ascii=False, separators=(",", ":")))
fx_dec = qr_decode(fx_rows)
T.check("R5b the fixture's JSON drawn as QR and decoded (OpenCV) gives back the same document (skipped without cv2)", fx_dec is None or (fx_dec and json.loads(fx_dec) == FQ), str(fx_dec)[:100])
for kk in ("CC_SUPERVISOR_CONFIG", "HOME", "CM_HOME"):
    os.environ.pop(kk, None)

# R5c (1.15): prove isolate — con CC_SUPERVISOR_CONFIG su una configurazione di prova (relay.dir, service account e
# firebase_url suoi) pair, push e serve lavorano solo li': chiave, devices.json, /allowed e crontab della
# configurazione principale restano come sono (le prove dell'app non devono scollegare l'orologio vero)
URL_ISO, CALLS_ISO, STORE_ISO = T.fake_rtdb("iso-project")
rdir_iso = tmp / "relay-iso"
SA_ISO = tmp / "sa-iso.json"; SA_ISO.write_text(SA.read_text().replace("fake-project", "iso-project"))
cfg_iso = tmp / "config-iso.json"
cfg_iso.write_text(json.dumps(dict(json.loads(cfg.read_text()), relay={"enabled": True, "firebase_url": URL_ISO, "service_account": str(SA_ISO), "token_url": URL_ISO + "/token",
                                                                         "fcm_url": URL_ISO, "dir": str(rdir_iso), "host": "iso-host", "debounce_s": 1, "fcm_topic": "watch-iso", "firebase_app": APP})))
ENV_ISO = dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg_iso))
snap = lambda: ((rdir2 / "key").read_bytes(), (rdir2 / "devices.json").read_bytes(), json.dumps(STORE.get("allowed"), sort_keys=True), cron.read_text(), (rdir2 / "serve.pid").exists())  # noqa: E731
before_iso = snap()
pr = subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--timeout", "8", "--text"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV_ISO)
T.wait_until(lambda: any(isinstance(v, dict) and v.get("pc_pub") for v in (STORE_ISO.get("pair") or {}).values()), 4)
iso_nodes = {n: v for n, v in (STORE_ISO.get("pair") or {}).items() if isinstance(v, dict) and v.get("pc_pub")}
iso_id = next((n for n in iso_nodes if len(n) == 22), "")
wi_priv, wi_pub = C.pair_keys(); k_iso = C.shared_key(wi_priv, iso_nodes[iso_id]["pc_pub"]) if iso_id else b""
if iso_id:
    import urllib.request as _ur
    req = _ur.Request(f"{URL_ISO}/pair/{iso_id}/watch.json", data=json.dumps({"watch_pub": wi_pub, "uid": "u-iso", "name": "Phone iso", "check": C.check_code(k_iso, iso_id)}).encode(), method="PUT", headers={"Content-Type": "application/json"})
    _ur.urlopen(req, timeout=5).read()
pr.wait(timeout=12); out_iso = pr.stdout.read()
r_push = relay("push", env=ENV_ISO)
r_ens = relay("ensure", env=ENV_ISO)
iso_pid = int((rdir_iso / "serve.pid").read_text().strip() or 0) if (rdir_iso / "serve.pid").exists() else 0
r_off = relay("off", env=ENV_ISO)
T.check("R5c the trial config pairs on its own bus and dir: exit 0, key and devices.json under its relay.dir, u-iso in ITS /allowed, its /state pushed (exit 0), its serve started and stopped", pr.returncode == 0 and C.load_key(rdir_iso) == k_iso and "u-iso" in json.loads((rdir_iso / "devices.json").read_text()) and STORE_ISO.get("allowed") == {"u-iso": True} and r_push.returncode == 0 and "enc" in (STORE_ISO.get("state") or {}) and r_ens.returncode == 0 and iso_pid > 0 and r_off.returncode == 0, f"rc={pr.returncode} {out_iso[-200:]} {pr.stderr.read()[-200:]} push={r_push.returncode} {r_push.stderr[-200:]} ens={r_ens.stdout}{r_ens.stderr} pid={iso_pid}")
T.check("R5c meanwhile the main config is untouched: same key bytes, same devices.json, same /allowed on its bus, same crontab, no serve pid in its dir", snap() == before_iso and json.loads((rdir2 / "devices.json").read_text()) == dev and STORE.get("allowed") == {"u2": True, "u3": True}, f"before={before_iso[2:]} after={snap()[2:]}")
T.wait_until(lambda: not (rdir_iso / "serve.pid").exists() or not os.path.exists(f"/proc/{iso_pid}"), 5)
k = k2   # da qui la chiave viva e' quella dell'ultimo pairing

# R6: serve — i comandi del contratto
rows_alive("ledger-api", "atlas-shop", "field-notes")
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
relay("push")


def _stop_test_serve():
    """05/10: un test caduto a meta' lasciava vivo il suo daemon di prova (HOME e config in /tmp): all'uscita, sempre via."""
    try:
        os.kill(int((rdir2 / "serve.pid").read_text().strip()), 15)
    except (OSError, ValueError):
        pass


import atexit
atexit.register(_stop_test_serve)


def serve_pid():
    try:
        pid = int((rdir2 / "serve.pid").read_text().strip() or 0); os.kill(pid, 0); return pid
    except (OSError, ValueError):
        return 0


r = relay("ensure")
T.check("R6 relay ensure starts serve (pidfile, live process); a second ensure keeps the same pid", r.returncode == 0 and serve_pid() > 0 and relay("ensure").returncode == 0 and serve_pid() == serve_pid(), r.stdout + r.stderr)
T.check("R6 the status file is written at startup with the new pid and zeroed counters (else `status` shows the dead process's numbers)", T.wait_until(lambda: json.loads((rdir2 / "serve.json").read_text()).get("pid") == serve_pid(), 5) and json.loads((rdir2 / "serve.json").read_text())["reconnects"] == 0, (rdir2 / "serve.json").read_text() if (rdir2 / "serve.json").exists() else "assente")
pid1 = serve_pid()
CMDS = json.loads((FIX / "cmd-result-sample.json").read_text())["cmd"]


def send_cmd(cmd, wait=20):   # sotto carico il daemon impiega di piu' (build in parallelo)
    cid = cmd["id"]
    http("PUT", f"/cmd/{cid}.json", C.encrypt(cmd, k))
    T.wait_until(lambda: (STORE.get("result") or {}).get(cid), wait)
    res = STORE.get("result", {}).get(cid)
    return C.decrypt(res, k) if res else None


n_state_puts = len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])
res = send_cmd(CMDS[0])   # answer ledger-api 1
T.check("R6 answer → `answer work-ledger-api 1` (name mapped to tmux), /result {ok, text «answered 1. yes», at}, /cmd/<id> deleted", res and res["ok"] is True and res["text"] == "answered 1. yes" and isinstance(res["at"], int) and "answer work-ledger-api 1" in cm_calls() and T.wait_until(lambda: CMDS[0]["id"] not in (STORE.get("cmd") or {}), 5), str(res) + str(cm_calls()[-4:]))   # il daemon scrive /result e POI cancella /cmd: si aspetta
T.check("R6 a successful answer removes the hook's waiting flag (else «waiting» until the next prompt: answered and outcome 3 min late, 14/09)", not (state_dir / "waiting" / "S-L").exists(), str(list((state_dir / "waiting").iterdir())))
led_rows = lambda: [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]  # noqa: E731
T.check("R6 the command is annotated in the ledger: event watch-cmd with op, name, by (who answered) and ok", any(x.get("event") == "watch-cmd" and x.get("op") == "answer" and x.get("name") == "ledger-api" and x.get("by") == "watch-pixel5" and x.get("ok") is True for x in led_rows()), str([x for x in led_rows() if x.get("event") == "watch-cmd"][-2:]))
T.check("R6 after a command /state is republished (a new PUT of /state, from the push in background)", T.wait_until(lambda: len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) > n_state_puts, 30), "")
res = send_cmd(CMDS[1])   # prompt atlas-shop
T.check("R6 prompt without device → talk atlas-shop with the neutral prefix «via app», not «polso» (1.36; Watch: line requested) --no-wait; atlas-shop is busy → «queued: … reads it when the turn ends» (14/10), atlas-shop awaiting", res and res["ok"] and res["text"] == "queued: atlas-shop is working, it reads it when the turn ends" and any(c.startswith("talk atlas-shop Dall'utente via app.") and "Watch:" in c and c.endswith("review the test seeds --no-wait") for c in cm_calls()) and "atlas-shop" in json.loads((rdir2 / "awaiting.json").read_text()), str(res) + str(cm_calls()[-3:]))
box_a = sorted((state_dir / "inbox" / "atlas-shop").glob("*.json"), key=lambda q: json.loads(q.read_text())["created"])
rec_a = json.loads(box_a[-1].read_text()) if box_a else {}
T.check("R6 (0319ab) the relay's message in the recipient's inbox has origin «relay» (talk writes «session»: the master tells an ok from the phone from a session's message)",
        rec_a.get("origin") == "relay" and rec_a.get("text", "").startswith("Dall'utente via app.") and rec_a.get("from") == "supervisor", str(rec_a))
res = send_cmd(CMDS[2])   # launch path fuori dai progetti pubblicati
T.check("R6 launch of a path outside the published projects → ok false, no launch", res and res["ok"] is False and "not a published project" in res["text"] and not any(c.startswith("launch ") for c in cm_calls()), str(res))
res = send_cmd(dict(CMDS[2], id="6f1c2d3e-0003-4000-8000-0000000000aa", arg=str(ws / "work" / "own" / "orbit-docs")))
T.check("R6 launch of a published project → `launch PATH --window` (its tab on the desktop, 1.9.2), «launched orbit-docs (work)»", res and res["ok"] is True and res["text"] == "launched orbit-docs (work)" and any(c.startswith("launch ") and c.endswith("orbit-docs --window") for c in cm_calls()), str(res) + str(cm_calls()[-3:]))
# 1.4: «last» — l'ultimo messaggio dell'assistente per intero dal transcript (il ledger ne tiene solo la coda)
tdir_a = home / ".claude" / "projects" / "".join(ch if ch.isalnum() else "-" for ch in os.path.realpath(str(ws / "personal" / "atlas-shop")))
tdir_a.mkdir(parents=True, exist_ok=True)
lungo = "Prima frase del messaggio lungo. " + ("Dettaglio del lavoro fatto. " * 200) + "Ultima frase."
(tdir_a / "S-A.jsonl").write_text("\n".join([
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Messaggio vecchio da ignorare."}]}}),
    json.dumps({"type": "user", "message": {"role": "user", "content": "fai"}}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": lungo}, {"type": "tool_use", "name": "Bash", "input": {"command": "x"}}]}}),
]) + "\n")
res = send_cmd({"id": "6f1c2d3e-0010-4000-8000-000000000010", "op": "last", "session": "atlas-shop", "arg": None, "issued": 1, "by": "watch-pixel5"})
T.check("R6 (1.4) last → the whole last assistant message from the transcript, up to 4000 chars cut at a sentence end (the ledger only keeps 600 of the tail)", res and res["ok"] is True and res["text"].startswith("Prima frase del messaggio lungo.") and len(res["text"]) <= 4000 and len(res["text"]) > 3000 and res["text"].rstrip().endswith(".") and "Messaggio vecchio" not in res["text"], str(len(res["text"] if res else "")) + " " + (res or {}).get("text", "")[:60])
(tdir_a / "S-A.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Corto e intero."}]}}) + "\n")
res = send_cmd({"id": "6f1c2d3e-0011-4000-8000-000000000011", "op": "last", "session": "atlas-shop", "arg": None, "issued": 1, "by": "watch-pixel5"})
T.check("R6 (1.4) a short message comes back whole, untouched", res and res["ok"] and res["text"] == "Corto e intero.", str(res))
res = send_cmd({"id": "6f1c2d3e-0012-4000-8000-000000000012", "op": "last", "session": "field-notes", "arg": None, "issued": 1, "by": "watch-pixel5"})
T.check("R6 (1.4) a session without a transcript → ok false with «nessun messaggio da leggere»", res and res["ok"] is False and "nessun messaggio" in res["text"], str(res))
res = send_cmd(CMDS[3])   # screen
T.check("R6 screen → `screen atlas-shop --lines 30 --join` (tmux reunites wrapped lines: no words cut on the wrist), 30 lines in text", res and res["ok"] and len(res["text"].splitlines()) == 30 and "screen atlas-shop --lines 30 --join" in cm_calls(), str(res)[:200] + str([c for c in cm_calls() if c.startswith("screen ")]))
res = send_cmd(CMDS[4])   # follow
T.check("R6 follow → follow.json has atlas-shop, «following atlas-shop»; the next /state marks it followed", res and res["ok"] and res["text"] == "following atlas-shop" and "atlas-shop" in json.loads((rdir2 / "follow.json").read_text()) and T.wait_until(lambda: any(s_["name"] == "atlas-shop" and s_["followed"] for s_ in C.decrypt(STORE["state"], k)["sessions"]), 30), str(res))
res = send_cmd(dict(CMDS[4], id="6f1c2d3e-0005-4000-8000-0000000000bb", op="unfollow"))
T.check("R6 unfollow → removed", res and res["ok"] and "atlas-shop" not in json.loads((rdir2 / "follow.json").read_text()), str(res))
res = send_cmd(CMDS[5])   # resume orbit-docs (gone)
T.check("R6 resume of a gone session → ok false «orbit-docs is gone: use launch»", res and res["ok"] is False and res["text"] == "orbit-docs is gone: use launch", str(res))
# R6 (1.9, 15/09, dall'orologio: una sessione chiusa non si poteva riprendere): reopen rilancia una gone
orbit = ws / "work" / "own" / "orbit-docs"


def reopen_cmd(n, session="orbit-docs"):
    return {"id": f"6f1c2d3e-0010-4000-8000-0000000001{n:02d}", "op": "reopen", "session": session, "arg": None, "issued": 1789210960, "by": "watch-pixel5"}


def launches():
    return [c for c in cm_calls() if c.startswith("launch ")]


res = send_cmd(reopen_cmd(1))
T.check("R6 reopen of a gone session, no conversation id, nobody live in its folder → `launch <path> --window --account work --continue`, «reopened orbit-docs (work): last conversation in the folder»", res and res["ok"] is True and res["text"] == "reopened orbit-docs (work): last conversation in the folder" and launches()[-1] == f"launch {orbit} --window --account work --continue", str(res) + str(cm_calls()[-3:]))
T.check("R6 reopen is annotated in the ledger (watch-cmd, op reopen, ok)", any(x.get("event") == "watch-cmd" and x.get("op") == "reopen" and x.get("name") == "orbit-docs" and x.get("ok") is True for x in led_rows()), "")
tdir = home / ".claude-pixel" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(orbit))
tdir.mkdir(parents=True, exist_ok=True)
(tdir / "S-O.jsonl").write_text("{}\n")
good_json.write_text(json.dumps({"sessioni": [dict(GOOD_ORBIT, session_id="S-O")]}))
res = send_cmd(reopen_cmd(2))
T.check("R6 reopen with the snapshot's conversation id and its transcript on disk → `--resume S-O` (never -c), «…: same conversation»", res and res["ok"] is True and res["text"] == "reopened orbit-docs (work): same conversation" and launches()[-1] == f"launch {orbit} --window --account work --resume S-O", str(res) + str(launches()[-1:]))
good_json.write_text(json.dumps({"sessioni": [dict(GOOD_ORBIT, session_id="S-GONE")]}))
rows_alive("ledger-api", "atlas-shop", "field-notes", **{"field-notes": {"cwd": str(orbit)}})
n_l = len(launches())
res = send_cmd(reopen_cmd(3))
T.check("R6 reopen with an id whose transcript is missing and another live session in the folder → ok false, no launch (-c would take the other one's conversation)", res and res["ok"] is False and res["text"] == "orbit-docs: conversation unknown and 1 live session(s) in its folder: use launch" and len(launches()) == n_l, str(res))
rows_alive("ledger-api", "atlas-shop", "field-notes")
res = send_cmd(reopen_cmd(4, "atlas-shop"))
T.check("R6 reopen of a live name → ok false «atlas-shop is already running», no launch", res and res["ok"] is False and res["text"] == "atlas-shop is already running" and len(launches()) == n_l, str(res))
res = send_cmd(reopen_cmd(5, "nope"))
T.check("R6 reopen of a name outside the registry snapshot → ok false «nope is not a closed session»", res and res["ok"] is False and res["text"] == "nope is not a closed session" and len(launches()) == n_l, str(res))
good_json.write_text(json.dumps({"sessioni": [GOOD_ORBIT]}))
launch_adds.write_text(json.dumps(json.loads(alive.read_text()) + [{"pid": 11, "name": "work-orbit-docs-2", "tmux": "work-orbit-docs-2", "cwd": str(orbit), "status": "idle", "waiting": False, "link": "", "account": "work", "session_id": "S-O2", "attached": False, "started_at": 1789211000000}]))
res = send_cmd(reopen_cmd(6))
T.check("R6 reopen that comes back under another name (launch takes the first free one) → the old name leaves the snapshot (`registry --closed work-orbit-docs`), else it stays ✗ for ever", res and res["ok"] is True and "registry --closed work-orbit-docs" in cm_calls(), str(res) + str(cm_calls()[-4:]))
launch_adds.unlink()
rows_alive("ledger-api", "atlas-shop", "field-notes")
# una domanda nuova su ledger-api: la risposta di prima ha tolto il flag, l'hook del dialogo nuovo lo riscrive
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
relay("push")
res = send_cmd(CMDS[6])   # allow_all ledger-api
T.check("R6 allow_all without a «don't ask again» option → ok false with the contract's text", res and res["ok"] is False and res["text"] == "no «don't ask again» option on this question", str(res))
# R6 (1.52, 14/10): una risposta vale solo per la sua domanda. Il telefono offline teneva il comando in coda, la sessione
# faceva una domanda nuova e la risposta vecchia ci premeva il tasto. Domanda nuova su ledger-api alle 1789211000.
ledger_before = ledger.read_text()
with ledger.open("a") as f:
    f.write(json.dumps({"ts": iso(1789211000), "event": "waiting", "session_id": "S-L", "tool": "AskUserQuestion"}) + "\n")
CHANGED = {json.loads((T.ROOT / "supervisor" / "messages" / f"{lg}.json").read_text())["relay.cmd_question_changed"].format(name="ledger-api") for lg in ("en", "it")}


def answers():
    return [c for c in cm_calls() if c.startswith("answer work-ledger-api ") and "--show" not in c]


def ans(n, **kw):
    return dict({"id": f"6f1c2d3e-0052-4000-8000-0000000005{n:02d}", "op": "answer", "session": "ledger-api", "arg": "2", "issued": 1789211005, "by": "phone-pixel9"}, **kw)


n_a = len(answers())
res = send_cmd(ans(1, question="q-1789210500-1", issued=1789210890))
T.check("R6 (1.52) answer with the id of the previous question → ok false relay.cmd_question_changed, no key pressed", res and res["ok"] is False and res["text"] in CHANGED and len(answers()) == n_a, str(res) + str(answers()[-2:]))
res = send_cmd(ans(2, issued=1789210890))
T.check("R6 (1.52) answer without `question`, issued 110 s before the open question (over the 60 s margin) → refused, no key pressed", res and res["ok"] is False and res["text"] in CHANGED and len(answers()) == n_a, str(res))
res = send_cmd(ans(3, op="allow_all", arg=None, question="q-1789210500-1"))
T.check("R6 (1.52) allow_all with the id of the previous question → refused before anything else, no key pressed", res and res["ok"] is False and res["text"] in CHANGED and len(answers()) == n_a, str(res))
res = send_cmd(ans(4, question="garbage"))
T.check("R6 (1.52) answer with an id that is not «q-<asked_at>-<n>» → refused", res and res["ok"] is False and res["text"] in CHANGED and len(answers()) == n_a, str(res))
res = send_cmd(ans(5, question="q-1789211000-1"))
T.check("R6 (1.52) answer with the open question's id (asked_at read from the ledger, even before a push) → `answer work-ledger-api 2`", res and res["ok"] is True and answers()[-1:] == ["answer work-ledger-api 2"] and len(answers()) == n_a + 1, str(res) + str(answers()[-2:]))
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
res = send_cmd(ans(6, issued=1789210950))
T.check("R6 (1.52) answer without `question` issued 50 s before the question (phone clock within the margin) → answered", res and res["ok"] is True and len(answers()) == n_a + 2, str(res))
ledger.write_text(ledger_before)
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
# R6 (1.10, 15/09, da claude-master-watch): answer con «text:<testo>» e «chat»
# R10 (contratto 1.12, 16/09): modello ed effort dal polso, solo per la sessione, via `supervisor model|effort`;
# il testo di un rifiuto e' la prima riga del comando, breve, così com'è
RES = json.loads((FIX / "cmd-result-sample.json").read_text())["result"]
res = send_cmd(CMDS[9])   # model field-notes claude-sonnet-5
T.check("R10 (1.12) model → `model field-notes claude-sonnet-5`, /result ok with the command's line (as in the fixture)",
        res and res["ok"] is True and res["text"] == RES[9]["text"] and "model field-notes claude-sonnet-5" in cm_calls(), str(res) + str(cm_calls()[-3:]))
res = send_cmd(CMDS[10])   # effort atlas-shop low: occupata
T.check("R10 (1.12) effort on a busy session → /result ok=false with the short reason, as the watch shows it",
        res and res["ok"] is False and res["text"] == RES[10]["text"], str(res))
r = relay("push", "--dry-run"); dry10 = json.loads(r.stdout)
T.check("R10 (1.12) /state carries choices: full model ids (as in session.model.id) with labels, and the five efforts",
        dry10.get("choices") == json.loads((FIX / "state-1-question.json").read_text())["choices"], str(dry10.get("choices")))
# R11 (contratto 1.13, 16/09): «Nuova sessione» dal polso col primo messaggio — launch, poi talk sulla sessione NATA
# (che puo' chiamarsi diversamente dal progetto), e nel /result il suo nome come in sessions[].name
fn = ws / "personal" / "field-notes"
launch_adds.write_text(json.dumps(json.loads(alive.read_text()) + [{"pid": 12, "name": "field-notes-2", "tmux": "field-notes-2", "cwd": str(fn), "status": "idle", "waiting": False, "link": "", "account": "personal", "session_id": "S-F2", "attached": False, "started_at": 1789211000000}]))
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[11], arg=str(fn)))
calls = cm_calls()[n_calls:]
T.check("R11 (1.13) launch with text → `launch PATH --window`, then `talk` with the watch prefix to the NEW session field-notes-2, --no-wait",
        any(c.startswith("launch ") and str(fn) in c for c in calls) and any(c.startswith("talk field-notes-2 Dall'utente via app.") and "check the draft for typos" in c and c.endswith("--no-wait") for c in calls), str(calls))
T.check("R11 (1.13) /result ok with the fixture's text and `session` = field-notes-2, the name the watch will see in sessions[].name",
        res and res["ok"] is True and res["text"] == RES[11]["text"] and res.get("session") == RES[11]["session"] == "field-notes-2", str(res))
launch_adds.unlink()
# R13 (contratto 1.17, 29/09): la coda di stanotte dall'app, via `supervisor night add|remove`
led = ws / "work" / "clients" / "ledger-api"
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[12], arg=str(led)))
T.check("R13 (1.17) night_add → `night add PATH PROMPT`, /result ok with the fixture's text and `job` = the new id",
        res and res["ok"] is True and res["text"] == RES[12]["text"] and res.get("job") == RES[12]["job"] == "7b21d4e8"
        and f"night add {led} Update the changelog for 2.4 and check the migration notes" in cm_calls()[n_calls:], str(res) + str(cm_calls()[n_calls:]))
res = send_cmd(dict(CMDS[12], id="6f1c2d3e-0123-4000-8000-000000000201"))
res2 = send_cmd(dict(CMDS[12], id="6f1c2d3e-0123-4000-8000-000000000202", arg=str(led), text="   "))
res3 = send_cmd(dict(CMDS[12], id="6f1c2d3e-0123-4000-8000-000000000203", arg=str(led), text="FULL"))
T.check("R13 (1.17) night_add refusals in plain words: a folder outside the published projects, an empty prompt (no `night` run for either), a full queue",
        res and res["ok"] is False and "not a published project" in res["text"] and res2 and res2["ok"] is False and res2["text"] == "empty prompt: nothing to queue"
        and res3 and res3["ok"] is False and res3["text"] == "tonight's queue is full (8 jobs): remove one first"
        and len([c for c in cm_calls()[n_calls:] if c.startswith("night add")]) == 2, str(res) + str(res2) + str(res3))
# 1.49 (09/10): arg «master» (workspace.root_session_name) → the job goes in the master's folder, workspace.root
_c49 = next(c for c in CMDS if c["id"] == "6f1c2d3e-0490-4000-8000-000000000490")
_r49 = next(r for r in RES if r["id"] == _c49["id"])
n_calls = len(cm_calls())
res = send_cmd(_c49)
T.check("R13 (1.49) night_add with arg «master» → `night add <workspace.root> PROMPT`, /result as the fixture's …0490 (the master's name and account, job)",
        res and res["ok"] is True and res["text"] == _r49["text"] and res.get("job") == _r49["job"]
        and f"night add {os.path.realpath(ws)} {_c49['text']}" in cm_calls()[n_calls:], str(res) + str(cm_calls()[n_calls:]))
res = send_cmd(CMDS[13])
T.check("R13 (1.17) night_remove → `night remove ID`, /result ok with the fixture's text",
        res and res["ok"] is True and res["text"] == RES[13]["text"] and "night remove 5d0e6b92" in cm_calls(), str(res))
res = send_cmd(dict(CMDS[13], id="6f1c2d3e-0124-4000-8000-000000000201", arg="deadbeef"))
res2 = send_cmd(dict(CMDS[13], id="6f1c2d3e-0124-4000-8000-000000000202", arg="a3f09c1e"))
T.check("R13 (1.17) night_remove refusals in plain words: unknown id, job already started",
        res and res["ok"] is False and res["text"] == "no job deadbeef in tonight's queue" and res2 and res2["ok"] is False and res2["text"] == "job a3f09c1e has already started: it cannot be removed", str(res) + str(res2))
nq = state_dir / "night-queue.jsonl"
nq.write_text(json.dumps({"id": "a3f09c1e", "dir": str(ws / "personal" / "atlas-shop"), "prompt": "fix the flaky tests", "account": "personal", "added": iso(1789207200), "started": 1789215000}) + "\n"
              + json.dumps({"id": "7b21d4e8", "dir": str(led), "prompt": "Update the changelog", "account": "work", "added": iso(1789210620)}) + "\n")
r = relay("push", "--dry-run"); dryn = json.loads(r.stdout)["night"]
nq.unlink()
T.check("R13 (1.17) push reads the queue file: queued 2, running = the started job's folder name, items in file order with started as epoch",
        dryn["queued"] == 2 and dryn["running"] == "atlas-shop" and [(i["id"], i["name"], i["started"]) for i in dryn["items"]] == [("a3f09c1e", "atlas-shop", 1789215000), ("7b21d4e8", "ledger-api", None)] and dryn["items"][0]["added"] == 1789207200, str(dryn))
# R14 (contratto 1.18, 29/09): diario, resoconto della notte e ripresa della quota anche all'app, come eventi con la sveglia
def new_events(kind, before):
    return [C.decrypt(v, k) for kk, v in sorted((STORE.get("events") or {}).items()) if kk not in before and C.decrypt(v, k)["kind"] == kind]


today = time.strftime("%Y-%m-%d")
before, n_fcm = set(STORE.get("events") or {}), len(CALLS["fcm"])
seq0 = int(json.loads((rdir2 / "last-state.json").read_text()).get("seq") or 0)
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--send"], capture_output=True, text=True, env=ENV, timeout=120)
ev = new_events("recap", before)
T.check("R14 (1.18) recap --send → one `recap` event: title «Diario del dd/mm», body = the plain diary as printed (no Telegram HTML), ref = the day, key after the push seq (a push in background may take a number first); FCM with kind recap",
        r.returncode == 0 and len(ev) == 1 and ev[0]["title"] == "Diario del " + time.strftime("%d/%m") and ev[0]["ref"] == today and "<b>" not in ev[0]["body"]
        and ev[0]["body"].splitlines()[0] == r.stdout.splitlines()[0] and int(ev[0]["key"].split("_")[1]) > seq0 and ev[0]["session"] is None
        and ev_ok(ev[0]) and CALLS["fcm"][n_fcm:] and CALLS["fcm"][-1]["message"]["data"]["kind"] == "recap", r.stdout[-300:] + r.stderr[-300:] + str(ev))
nq = state_dir / "night-queue.jsonl"
nq.write_text(json.dumps({"id": "c0ffee01", "dir": str(ws / "personal" / "atlas-shop"), "prompt": "fix the flaky tests", "account": "personal", "model": "", "effort": "", "max_turns": 5, "added": iso(1789207200)}) + "\n")
before = set(STORE.get("events") or {})
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night.py"), "run", "--send"], capture_output=True, text=True, timeout=120,
                   env=dict(ENV, CM_CLAUDE_BIN=str(T.ROOT / "tests" / "lib" / "fake-claude.sh"), CM_NIGHT_FREE_MB="4000"))
ev = new_events("night_report", before)
T.check("R14 (1.18) night run --send → one `night_report` event: title «Notte: 1 lavori, 1 riusciti», body = the Telegram summary, ref = today",
        r.returncode == 0 and len(ev) == 1 and ev[0]["title"] == "Notte: 1 lavori, 1 riusciti" and ev[0]["ref"] == today
        and ev[0]["body"].startswith("Turno di notte: 1 lavori eseguiti") and "✓ atlas-shop" in ev[0]["body"], r.stdout[-300:] + r.stderr[-300:] + str(ev))
nq.unlink(missing_ok=True)
CORE = f"import importlib.util as u; s = u.spec_from_file_location('c', {str(T.SCRIPTS / 'cm-core.py')!r}); m = u.module_from_spec(s); s.loader.exec_module(m); "
before = set(STORE.get("events") or {})
r = subprocess.run([sys.executable, "-c", CORE + "print(m.relay_event('quota', '✓ quota personal tornata', 'reset 13:10\\n' + 'riga\\n' * 3000, account='personal'))"],
                   capture_output=True, text=True, env=ENV, timeout=60)
ev = new_events("quota", before)
T.check("R14 (1.18) relay_event quota (the guard's resume): account set, body cut at a line end within 4000 characters, no «…»",
        r.stdout.strip() == "True" and len(ev) == 1 and ev[0]["account"] == "personal" and len(ev[0]["body"]) <= 4000 and ev[0]["body"].endswith("riga") and "…" not in ev[0]["body"], r.stdout + r.stderr + str(ev)[:300])
T.check("R14 (1.18) cut_lines: whole when it fits; else whole lines only; one line longer than the cap cut at a word end",
        S.cut_lines("a\nb", 10) == "a\nb" and S.cut_lines("aaaa\nbbbb\ncccc", 10) == "aaaa\nbbbb" and S.cut_lines("one two three", 8) == "one two", S.cut_lines("aaaa\nbbbb\ncccc", 10))
write_cfg(enabled=False)
before = set(STORE.get("events") or {})
r = subprocess.run([sys.executable, "-c", CORE + "print(m.relay_event('quota', 't', 'b'))"], capture_output=True, text=True, env=ENV, timeout=60)
write_cfg()
T.check("R14 (1.18) relay off → relay_event does nothing and says so (Telegram goes on as before)", r.stdout.strip() == "False" and set(STORE.get("events") or {}) == before, r.stdout + r.stderr)
# R15 (contratto 1.19, 29/09): «Condividi» dal telefono — op report, immagine cifrata in /share/<id>, `share` nello stato
IMG = bytes(range(256)) * 40
atlas = ws / "personal" / "atlas-shop"
sid = CMDS[14]["arg"]
http("PUT", f"/share/{sid}.json", C.encrypt({"mime": "image/jpeg", "data": base64.b64encode(IMG).decode()}, k))
n_calls = len(cm_calls())
res = send_cmd(CMDS[14])
calls = [c for c in cm_calls()[n_calls:] if c.startswith("report ")]
T.check("R15 (1.19) report with an image → `report <session folder> <temp .jpg> <text> --session <tmux>`, the bytes intact, /result = the fixture's text",
        res and res["ok"] is True and res["text"] == RES[14]["text"] and len(calls) == 1 and calls[0].startswith(f"report {atlas} ") and ".jpg the client says" in calls[0]
        and calls[0].endswith("--session atlas-shop") and (tmp / "report-img").read_bytes() == IMG, str(res) + str(calls))
box15 = sorted((state_dir / "inbox" / "atlas-shop").glob("*.json"), key=lambda q: json.loads(q.read_text())["created"])
rec15 = json.loads(box15[-1].read_text()) if box15 else {}
T.check("R15 (0319ab, 21:43) the report's message in the session's inbox has origin «relay», like the prompts",
        rec15.get("text", "").startswith("segnalazione:") and rec15.get("origin") == "relay", str(rec15))
T.check("R15 (1.19) after the command /share/<id> is deleted and the temporary file is gone",
        sid not in (STORE.get("share") or {}) and not any((rdir2 / "share-tmp").glob("*")), str(list((STORE.get("share") or {}))) + str(list((rdir2 / "share-tmp").glob("*"))))
res = send_cmd(CMDS[15])
T.check("R15 (1.19) text only → `report <folder> - <text> --session field-notes`, «sent to field-notes»",
        res and res["ok"] is True and res["text"] == RES[15]["text"] and f"report {ws / 'personal' / 'field-notes'} - add the Tuesday meeting notes to the draft --session field-notes" in cm_calls(), str(res) + str(cm_calls()[-2:]))
n_calls = len(cm_calls())
res = send_cmd(CMDS[16])
bad = "6f1c2d3e-0125-4000-8000-00000000b001"
http("PUT", f"/share/{bad}.json", C.encrypt({"mime": "nonsense", "data": "aGk="}, k))   # 1.28: ogni formato, ma un tipo vero (tipo/sottotipo)
res2 = send_cmd(dict(CMDS[14], id="6f1c2d3e-0125-4000-8000-000000000201", arg=bad))
res3 = send_cmd(dict(CMDS[14], id="6f1c2d3e-0125-4000-8000-000000000202", arg="0000-missing"))
big = "6f1c2d3e-0125-4000-8000-00000000b002"
http("PUT", f"/share/{big}.json", {"v": 1, "enc": "A" * 10000001})
res4 = send_cmd(dict(CMDS[14], id="6f1c2d3e-0125-4000-8000-000000000203", arg=big))
res5 = send_cmd(dict(CMDS[15], id="6f1c2d3e-0126-4000-8000-000000000201", text="  "))
T.check("R15 (1.19) refusals in plain words, no report run: a gone session (as in the fixture), a mime that is not type/subtype, a missing node, a blob over 10 MB, nothing to send; the bad nodes deleted",
        res and res["ok"] is False and res["text"] == RES[16]["text"] and res2 and res2["text"] == "file missing or unreadable" and res3 and res3["text"] == "image missing or unreadable"
        and res4 and res4["text"] == "too large" and res5 and res5["text"] == "empty report: nothing to send"
        and not any(c.startswith("report ") for c in cm_calls()[n_calls:]) and bad not in (STORE.get("share") or {}) and big not in (STORE.get("share") or {}), str([res, res2, res3, res4, res5]))
old, fresh = "6f1c2d3e-0125-4000-8000-00000000b003", "6f1c2d3e-0125-4000-8000-00000000b004"
http("PUT", f"/share/{old}.json", {"v": 1, "enc": "x"}); http("PUT", f"/share/{fresh}.json", {"v": 1, "enc": "x"})
(rdir2 / "share-seen.json").write_text(json.dumps({old: time.time() - 700}))
(rdir2 / "prune-at").unlink(missing_ok=True)   # 08/10: le pulizie una volta ogni 10 minuti; qui e' ora
relay("push")
T.check("R15 (1.19) push prunes a /share node first seen more than 10 minutes ago, keeps a fresh one (remembered in share-seen.json)",
        old not in (STORE.get("share") or {}) and fresh in (STORE.get("share") or {}) and fresh in json.loads((rdir2 / "share-seen.json").read_text()), str(list(STORE.get("share") or {})))
_SH15 = {"max_bytes": 10000000, "any": True, "parts": True, "max_parts_bytes": 52428800, "multi": 5}   # 1.48: parts and max_parts_bytes; 1.50: multi
T.check("R15 (1.19) /state carries share {max_bytes: 10000000, any: true, parts: true, max_parts_bytes: 52428800, multi: 5}, also when there is nothing else (its presence turns «Share» on in the app)",
        json.loads(relay("push", "--dry-run").stdout).get("share") == _SH15 and S.build_state({}, 1)["share"] == _SH15 and F1["share"] == _SH15, "")
# R17 (30/09, dal telefono): «x» chiusa e «work-x» viva nella stessa cartella hanno lo stesso nome corto — vince la
# viva, la chiusa non entra; e un prompt a una sessione non viva non e' «delivered»
good_bak = good_json.read_text()
good_json.write_text(json.dumps({"sessioni": [GOOD_ORBIT, {"nome": "work-atlas-shop", "cartella": str(atlas), "account": "work", "visto": "2026-09-30T17:30:33"}]}))
relay("push")
names17 = json.loads((rdir2 / "last-state.json").read_text())["names"]
ses17 = [(x["name"], x["state"]) for x in json.loads(relay("push", "--dry-run").stdout)["sessions"] if x["name"] == "atlas-shop"]
good_json.write_text(good_bak)
T.check("R17 a closed session with the same short name as a live one (other account, same folder) stays out: one atlas-shop, live (not gone), and its name maps to the live tmux",
        names17.get("atlas-shop") == "atlas-shop" and len(ses17) == 1 and ses17[0][1] != "gone", str(names17) + str(ses17))
rows_alive("ledger-api", "atlas-shop")
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[1], id="6f1c2d3e-0002-4000-8000-000000000301", session="field-notes"))
res2 = send_cmd(dict(CMDS[1], id="6f1c2d3e-0002-4000-8000-000000000302", op="resume", session="field-notes", arg=None))
T.check("R17 prompt and resume to a session that is not running → ok false «field-notes is not running: nothing sent», no talk",
        res and res["ok"] is False and res["text"] == "field-notes is not running: nothing sent" and res2 and res2["ok"] is False and res2["text"] == res["text"]
        and not any(c.startswith("talk field-notes") for c in cm_calls()[n_calls:]), str(res) + str(res2))
(tmp / "talk-saved").write_text("x")
res = send_cmd(dict(CMDS[1], id="6f1c2d3e-0002-4000-8000-000000000303"))
(tmp / "talk-saved").unlink()
T.check("R17 talk only saved it in the inbox (the session closed meanwhile) → ok false, the message waits there",
        res and res["ok"] is False and res["text"] == "atlas-shop closed meanwhile: the message waits in its inbox and arrives when it restarts", str(res))
rows_alive("ledger-api", "atlas-shop", "field-notes")
# R18 (contratto 1.21, 30/09): il tasto Stop — op interrupt via `supervisor interrupt`; `ops` nello stato
aw18 = json.loads((rdir2 / "awaiting.json").read_text()) if (rdir2 / "awaiting.json").exists() else {}
(rdir2 / "awaiting.json").write_text(json.dumps(dict(aw18, **{"atlas-shop": int(time.time())})))
n_calls = len(cm_calls())
res = send_cmd(CMDS[17])
res2 = send_cmd(CMDS[18])
T.check("R18 (1.21) interrupt → `interrupt <tmux>`: ok «atlas-shop: stopped» and the session leaves awaiting; nothing running → ok false «field-notes: nothing to stop» (as in the fixture)",
        res and res["ok"] is True and res["text"] == RES[17]["text"] and "interrupt atlas-shop" in cm_calls()[n_calls:]
        and "atlas-shop" not in json.loads((rdir2 / "awaiting.json").read_text()) and res2 and res2["ok"] is False and res2["text"] == RES[18]["text"], str(res) + str(res2))
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[17], id="6f1c2d3e-0128-4000-8000-000000000301", session="orbit-docs"))
T.check("R18 (1.21) interrupt on a gone session → ok false «orbit-docs is not running», no interrupt run",
        res and res["ok"] is False and res["text"] == "orbit-docs is not running" and not any(c.startswith("interrupt") for c in cm_calls()[n_calls:]), str(res))
T.check("R18 (1.21) /state carries ops = the relay's allow-list, with interrupt (the app shows Stop only when it is there)",
        json.loads(relay("push", "--dry-run").stdout).get("ops") == R_OPS and "interrupt" in R_OPS and F1["ops"] == R_OPS, str(R_OPS))
# R19 (contratto 1.22, 30/09): la chat vera per il telefono — op transcript dal jsonl della sessione, a pagine
import re as _re19
tdir19 = home / ".claude" / "projects" / _re19.sub(r"[^A-Za-z0-9]", "-", str((ws / "personal" / "field-notes").resolve()))
tdir19.mkdir(parents=True, exist_ok=True)
(tdir19 / "S-F.jsonl").write_bytes((FIX / "transcript-sample.jsonl").read_bytes())
res = send_cmd(CMDS[19])
res2 = send_cmd(CMDS[20])
T.check("R19 (1.22) transcript «20» → the fixture's JSON: user prompts only (no reminders, notifications, sidechain), assistant texts, one tool entry per call with note and error, the closed turn with start, end and tokens",
        res and res["ok"] is True and res["text"] == RES[19]["text"], (res or {}).get("text", "")[:300])
T.check("R19 (1.22) transcript «20:after=a4.0» → only what came after that entry (the fixture's second page), more false",
        res2 and res2["ok"] is True and res2["text"] == RES[20]["text"] and [e["id"] for e in json.loads(res2["text"])["entries"]] == ["u2.0", "q1.0", "a6.0", "a6.1", "a6.2", "a7.0", "a7.1", "p1.0", "w1.0", "q1789207361000", "img1.0", "q1789207365000", "a5.0"], (res2 or {}).get("text", "")[:200])
p19 = json.loads(send_cmd(dict(CMDS[19], id="6f1c2d3e-0130-4000-8000-000000000301", arg="2:before=a4.0"))["text"])
bad = [send_cmd(dict(CMDS[19], id=f"6f1c2d3e-0130-4000-8000-00000000030{i}", arg=a)) for i, a in ((2, "x"), (3, "5:after=nope"))]
none19 = send_cmd(dict(CMDS[19], id="6f1c2d3e-0130-4000-8000-000000000304", session="atlas-shop-none"))
f19 = {e["id"]: e["files"] for e in json.loads(res["text"])["entries"]}
T.check("R19 (1.22) files: a png written (path made absolute from the session's cwd, mime, size null when absent), no file for a .py edit or a failed write, the PDF of SendUserFile, the image archived by `supervisor report`",
        [x["mime"] for x in f19["a6.0"]] == ["image/png"] and f19["a6.0"][0]["path"].endswith("/field-notes/docs/cover.png") and f19["a6.0"][0]["size"] is None
        and f19["a6.1"] is None and f19["a6.2"] is None and [x["mime"] for x in f19["a7.0"]] == ["application/pdf"]
        and f19["a7.1"][0]["path"].endswith("/docs/reports/2026-09-12-checkout-is-grey.jpg") and all(v is None for k, v in f19.items() if not k.startswith(("a6", "a7"))), str(f19))
u19 = [(e["id"], e["origin"], e["text"]) for e in json.loads(res["text"])["entries"] if e["role"] == "user"]
T.check("R19 (1.22) origin: pc for what was typed in the terminal; the relay's prompt delivered through the socket (a peer message, twice) is one user entry, prefix stripped, origin phone; another session's message is left out",
        u19[:4] == [("u1.0", "pc", "Add the Tuesday meeting notes to the draft"), ("u2.0", "pc", "Also fix the typo in the title"), ("q1.0", "pc", "and push when done"), ("p1.0", "phone", "Also add the attendees list")]
        and all(e["origin"] is None for e in json.loads(res["text"])["entries"] if e["role"] != "user"), str(u19))
T.check("R19 (1.36) the prompt from the web app (device «web», through the socket) is one user entry, prefix stripped, origin web",
        ("w1.0", "web", "Also add the room booking") in u19, str(u19))
n_calls = len(cm_calls())
res21 = send_cmd(CMDS[21])
T.check("R19 (1.22) a prompt with device «phone» → talk with the phone's prefix (not the watch's), «delivered» as in the fixture",
        res21 and res21["ok"] is True and res21["text"] == RES[21]["text"] and any(c.startswith("talk atlas-shop Dall'utente via telefono.") and c.endswith("also add the attendees list --no-wait") for c in cm_calls()[n_calls:]), str(res21) + str(cm_calls()[n_calls:]))
n_calls = len(cm_calls())
res31 = send_cmd(CMDS[31])
T.check("R19 (1.36) a prompt with device «web» → talk with the web app's prefix (never «polso»), «delivered» as in the fixture",
        res31 and res31["ok"] is True and res31["text"] == RES[31]["text"] and any(c.startswith("talk atlas-shop Dall'utente via web app.") and c.endswith("check the cart totals too --no-wait") for c in cm_calls()[n_calls:]), str(res31) + str(cm_calls()[n_calls:]))
q19 = {e["id"]: (e["queued"], e["note"], e["text"]) for e in json.loads(res["text"])["entries"] if e["role"] == "user"}
T.check("R19 (1.22) typed while a turn runs: shown from the enqueue, with the time it was written; when it enters the turn the same entry stops being queued (no duplicate); one still waiting stays queued; a pasted image gives its text and «1 image(s)»; «[Request interrupted …]» and «[Cross-session delivery notice]» are not the person's messages",
        q19.get("q1789207361000") == (False, None, "then tag the release") and q19.get("q1789207365000") == (True, None, "still waiting in the queue")
        and q19.get("img1.0") == (False, "1 image(s)", "this is how the header looks now") and not any(t.startswith("[") for _, _, t in q19.values())
        and sum(1 for _, _, t in q19.values() if t == "then tag the release") == 1, str(q19))
T.check("R19 (1.22) «2:before=a4.0» → the two entries before it, more true; a bad arg, an unknown id and a session that is not running → ok false in plain words",
        [e["id"] for e in p19["entries"]] == ["a1.1", "a3.0"] and p19["more"] is True and bad[0]["ok"] is False and "expected n" in bad[0]["text"]
        and bad[1]["text"] == "no entry nope in the transcript" and none19["ok"] is False and none19["text"] == "atlas-shop-none is not running", str(p19) + str(bad) + str(none19))
# R22 (contratto 1.24, 02/10): op file — il telefono apre un file che compare nei `files` della trascrizione
import base64 as _b64, os as _os22
_fx = (FIX / "transcript-sample.jsonl").read_text().replace("/home/demo/workspaces", str(ws.resolve()))
(tdir19 / "S-F.jsonl").write_text(_fx)
fn22 = (ws / "personal" / "field-notes").resolve()
(fn22 / "docs").mkdir(parents=True, exist_ok=True)
png22 = _b64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
(fn22 / "docs" / "cover.png").write_bytes(png22)
fcmd = [dict(c, arg=c["arg"].replace("/home/demo/workspaces", str(ws.resolve()))) for c in CMDS[22:24]]
r22 = send_cmd(fcmd[0])
doc22 = (STORE.get("file") or {}).get(fcmd[0]["id"])
blob22 = C.decrypt(doc22, k) if doc22 else {}
T.check("R22 (1.24) file: a path listed in the session's transcript → /file/<cmd id> as {v, enc}, plain {mime, data base64} byte for byte; /result as in the fixture",
        r22 and r22["ok"] is True and r22["text"] == RES[22]["text"] and set(doc22 or {}) == {"v", "enc"}
        and blob22.get("mime") == "image/png" and _b64.b64decode(blob22.get("data") or "") == png22, str(r22) + str(blob22)[:120])
r22b = send_cmd(fcmd[1])
T.check("R22 (1.24) a path not in the transcript (as in the fixture) → «not in the transcript», nothing written to /file",
        r22b and r22b["ok"] is False and r22b["text"] == RES[23]["text"] and fcmd[1]["id"] not in (STORE.get("file") or {}), str(r22b))
pdf22 = str(fn22 / "docs" / "minutes.pdf")
r22c = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000201", arg=pdf22))
(fn22 / "docs" / "minutes.pdf").write_bytes(_os22.urandom(1_300_000))
r22d = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000202", arg=pdf22))
r22e = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000203", session="atlas-shop-none"))
T.check("R22 (1.24) refusals in plain words: a listed file that is missing, a listed non-image over the cap («too large: <bytes> max <bytes>», 1.34), a session that is not running",
        r22c and r22c["text"] == "missing or unreadable" and r22d and r22d["text"] == f"too large: 1300000 max {RL.FILE_ONE_MAX}" and r22e and r22e["text"] == "no session atlas-shop-none", str([r22c, r22d, r22e]))
from PIL import Image as _Im22
rep22 = (ws / "personal" / "atlas-shop").resolve() / "docs" / "reports"   # l'immagine archiviata da `supervisor report`
rep22.mkdir(parents=True, exist_ok=True)
_Im22.frombytes("RGB", (1600, 1600), _os22.urandom(1600 * 1600 * 3)).save(rep22 / "2026-09-12-checkout-is-grey.jpg", "JPEG", quality=95)
jpg22 = str(rep22 / "2026-09-12-checkout-is-grey.jpg")
r22f = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000204", arg=jpg22))
doc22f = (STORE.get("file") or {}).get("6f1c2d3e-0140-4000-8000-000000000204") or {}
blob22f = C.decrypt(doc22f, k) if doc22f else {}
T.check("R22 (1.24) a listed image over the cap is reduced to a JPEG that fits (enc ≤ 1 500 000)",
        _os22.path.getsize(jpg22) > 1_200_000 and r22f and r22f["ok"] is True and blob22f.get("mime") == "image/jpeg"
        and len(doc22f.get("enc") or "") <= 1_500_000 and _b64.b64decode(blob22f.get("data") or "")[:2] == b"\xff\xd8", f"{_os22.path.getsize(jpg22)} {r22f} {len(doc22f.get('enc') or '')}")
T.check("R22 (1.24) /state ops carries file", "file" in json.loads(relay("push", "--dry-run").stdout).get("ops", []), "")
# R29 (contratto 1.34, 04/10): il file a pezzi — con parts true meta e pezzi da ~1 MB, fino a 25 MB
big29 = _os22.urandom(2 * 1024 * 1024 + 12345)
(fn22 / "docs" / "minutes.pdf").write_bytes(big29)
r29 = send_cmd(dict(fcmd[0], id="6f1c2d3e-0210-4000-8000-000000000210", arg=pdf22, parts=True), wait=40)
node29 = (STORE.get("file") or {}).get("6f1c2d3e-0210-4000-8000-000000000210") or {}
meta29 = C.decrypt(node29.get("meta"), k) if node29.get("meta") else {}
parts29 = node29.get("parts") or {}
parts29 = [parts29[str(i)] if isinstance(parts29, dict) else parts29[i] for i in range(len(parts29))]
import hashlib as _h29
joined29 = b"".join(C.decrypt_raw(x, k) for x in parts29)
T.check("R29 (1.34) parts true → /file/<id>/meta {v, enc} of {n, size, sha256, mime, name} and /file/<id>/parts/0..n-1, raw bytes of 1 MB each; joined they are the file (sha256 checks)",
        r29 and r29["ok"] is True and meta29 == {"n": 3, "size": len(big29), "sha256": _h29.sha256(big29).hexdigest(), "mime": "application/pdf", "name": "minutes.pdf"}
        and len(parts29) == 3 and joined29 == big29 and all(set(x) == {"v", "enc"} for x in parts29), str(r29) + str(meta29))
with open(fn22 / "docs" / "minutes.pdf", "wb") as _f29:
    _f29.truncate(25 * 1024 * 1024 + 1)
r29b = send_cmd(dict(fcmd[0], id="6f1c2d3e-0210-4000-8000-000000000211", arg=pdf22, parts=True), wait=40)
T.check("R29 (1.34) over 25 MB → «too large: <bytes> max 26214400», nothing written", r29b and r29b["ok"] is False and r29b["text"] == f"too large: {25 * 1024 * 1024 + 1} max 26214400"
        and "6f1c2d3e-0210-4000-8000-000000000211" not in (STORE.get("file") or {}), str(r29b))
FP = json.loads((FIX / "file-parts.json").read_text())
_kfp = bytes.fromhex(FP["key"])
T.check("R29 (1.34) fixture file-parts.json: meta opens with the test key to its plain, the parts open to the file in order, sha256 of the joined bytes = meta.sha256",
        C.decrypt(FP["meta"], _kfp) == FP["meta_plain"] and b"".join(C.decrypt_raw(x, _kfp) for x in FP["parts"]) == FP["file"].encode()
        and _h29.sha256(FP["file"].encode()).hexdigest() == FP["meta_plain"]["sha256"] and FP["meta_plain"]["n"] == len(FP["parts"]) == 2, "")
_one = _os22.urandom(RL.FILE_ONE_MAX)
T.check("R29 (1.34) FILE_ONE_MAX, the «max» of the old way, is a file that still fits one {v, enc} under the cap", len(C.encrypt({"mime": "application/octet-stream", "data": _b64.b64encode(_one).decode()}, k)["enc"]) <= 1_500_000, str(RL.FILE_ONE_MAX))
old22, fresh22 = "6f1c2d3e-0140-4000-8000-00000000f001", "6f1c2d3e-0140-4000-8000-00000000f002"
http("PUT", f"/file/{old22}.json", {"v": 1, "enc": "x"}); http("PUT", f"/file/{fresh22}.json", {"v": 1, "enc": "x"})
(rdir2 / "file-seen.json").write_text(json.dumps({old22: time.time() - 700}))
(rdir2 / "prune-at").unlink(missing_ok=True)
relay("push")
T.check("R22 (1.24) push prunes a /file node not read for more than 10 minutes, keeps a fresh one",
        old22 not in (STORE.get("file") or {}) and fresh22 in (STORE.get("file") or {}), str(list(STORE.get("file") or {})))
# R31 (contratto 1.37, 05/10, la master al servizio dell'app): approvazioni, decisioni, consiglio di fable-director,
# doppioni e sessioni finite
tasks31 = tmp / "tasks.json"
tasks31.write_text(json.dumps([
    {"id": "atlas-release-2-4", "plan": None, "state": "awaiting_ok", "lane": "closed", "title": "Release 2.4 of atlas-shop", "project": str(ws / "personal" / "atlas-shop"),
     "request": {"what": "tag v2.4 and push to origin main", "where": "production (shop.example.com)", "at": 1789210600}},
    {"id": "notes-pr", "plan": None, "state": "awaiting_ok", "lane": "closed", "title": "Open the PR of the notes", "project": str(ws / "personal" / "field-notes"),
     "request": {"what": "push the branch notes", "where": "github fork", "at": 1789210500}}]))
n_calls = len(cm_calls())
r31a = send_cmd(CMDS[32])
r31b = send_cmd(CMDS[33])
T.check("R31 (1.37) approve of a task waiting for an ok → `task approve <task> --by <by> --text \"<text> (dal telefono)\"`, as in the fixture; a task not waiting → refused, nothing recorded",
        r31a and r31a["ok"] is True and r31a["text"] == RES[32]["text"] and "task approve atlas-release-2-4 --by phone-pixel8 --text ok, go ahead (dal telefono)" in cm_calls()[n_calls:]
        and r31b and r31b["ok"] is False and r31b["text"] == RES[33]["text"] and not any(c.startswith("task approve atlas-old-task") for c in cm_calls()), str([r31a, r31b]) + str(cm_calls()[n_calls:]))
n_calls = len(cm_calls())
r31c = send_cmd(CMDS[34])
talk31 = [c for c in cm_calls()[n_calls:] if c.startswith("talk master ")]
T.check("R31 (1.37) decision → `talk master` with the text, the project and where it comes from, --no-wait; «decision sent to the master» as in the fixture",
        r31c and r31c["ok"] is True and r31c["text"] == RES[34]["text"] and len(talk31) == 1 and "«Prices always include VAT on the checkout page»" in talk31[0]
        and "dal telefono (progetto atlas-shop)" in talk31[0] and talk31[0].endswith("--no-wait"), str(r31c) + str(talk31))
(tmp / "talk-saved").write_text("x")
r31d = send_cmd(dict(CMDS[34], id="6f1c2d3e-0372-4000-8000-000000000373", arg=None, device="web"))
(tmp / "talk-saved").unlink()
r31e = send_cmd(dict(CMDS[34], id="6f1c2d3e-0372-4000-8000-000000000374", text="  "))
T.check("R31 (1.37) decision with the master closed → kept in its inbox, said so; an empty decision → refused",
        r31d and r31d["ok"] is True and "inbox" in r31d["text"] and r31e and r31e["ok"] is False and r31e["text"] == "empty decision: nothing to save", str([r31d, r31e]))
# R35 (contratto 1.42, 07/10, modalita' live dell'app): il prompt a voce porta le istruzioni vocali dopo il prefisso del
# dispositivo; l'approve dal polso in modalita' live vale solo con la doppia conferma
_voice34 = json.loads((T.SCRIPTS.parent / "messages" / "it.json").read_text()).get("relay.prompt_voice")
_alive_bak34 = alive.read_text()
alive.write_text(json.dumps(json.loads(_alive_bak34) + [{"pid": 10, "name": "master", "tmux": "master", "cwd": str(ws / "personal" / "atlas-shop"), "status": "idle", "waiting": False,
                                                       "link": None, "account": "personal", "session_id": "S-M", "attached": False, "started_at": 1789120000000}]))
n_calls = len(cm_calls())
r34a = send_cmd(CMDS[37])
talk34 = [c for c in cm_calls()[n_calls:] if c.startswith("talk master ")]
T.check("R35 (1.42) prompt with voice true → talk with the phone's prefix, then the voice instructions, then the text, --no-wait; «delivered» as in the fixture",
        r34a and r34a["ok"] is True and r34a["text"] == RES[37]["text"] and _voice34 == "Dall'utente a voce, in modalità live: rispondi in 2 o 3 frasi, senza tabelle, percorsi né codice; se proponi seguiti, Prossimi numerati; chiudi con la riga Watch."
        and len(talk34) == 1 and talk34[0] == "talk master " + json.loads((T.SCRIPTS.parent / "messages" / "it.json").read_text())["relay.prompt_prefix_phone"] + " " + _voice34 + " which session is closest to a release? --no-wait",
        str(r34a) + str(talk34))
n_calls = len(cm_calls())
r34b = send_cmd(dict(CMDS[37], id="6f1c2d3e-0380-4000-8000-000000000383", voice=False))
talk34b = [c for c in cm_calls()[n_calls:] if c.startswith("talk master ")]
T.check("R35 (1.42) voice false or absent → the prompt as before, no voice instructions",
        r34b and r34b["ok"] is True and len(talk34b) == 1 and "modalità live" not in talk34b[0], str(r34b) + str(talk34b))
alive.write_text(_alive_bak34)
n_calls = len(cm_calls())
r34c = send_cmd(CMDS[38])
T.check("R35 (1.42) approve via live with 2 confirmations → `task approve <task> --by <by> --text \"<text> (dal telefono, modalità live, doppia conferma)\"`, as in the fixture",
        r34c and r34c["ok"] is True and r34c["text"] == RES[38]["text"] and "task approve atlas-release-2-4 --by phone-pixel8 --text ok (dal telefono, modalità live, doppia conferma)" in cm_calls()[n_calls:],
        str(r34c) + str(cm_calls()[n_calls:]))
n_calls = len(cm_calls())
r34d = send_cmd(CMDS[39])
r34e = [send_cmd(dict(CMDS[39], id=f"6f1c2d3e-0382-4000-8000-00000000038{i}", **kw)) for i, kw in ((4, {"confirmations": None}), (5, {"confirmations": True}), (6, {"confirmations": "2"}))]
_c34 = dict(CMDS[39]); _c34.pop("confirmations"); _c34["id"] = "6f1c2d3e-0382-4000-8000-000000000387"
r34e.append(send_cmd(_c34))
T.check("R35 (1.42) approve via live with 1 confirmation → refused as in the fixture; missing, null, true or a string → refused the same; nothing recorded",
        r34d and r34d["ok"] is False and r34d["text"] == RES[39]["text"] and all(r and r["ok"] is False and r["text"] == RES[39]["text"] for r in r34e)
        and not any(c.startswith("task approve") for c in cm_calls()[n_calls:]), str([r34d] + r34e) + str(cm_calls()[n_calls:]))
adv31 = tmp / "fd-sessions"; adv31.mkdir(exist_ok=True)
_now31 = int(time.time())
_m31 = json.loads((FIX / "state-1-question.json").read_text())["choices"]["models"]
(adv31 / "S-A.json").write_text(json.dumps({"session_id": "S-A", "ctx_tokens": 36000, "advice": {"model": _m31[1]["id"], "effort": "high", "reason": "Checkout refactor:\nFable high",
                                            "switch_cost_tokens": 36000, "at": _now31, "source": "fable-director", "when": "now", "differs": True}}))
(adv31 / "S-F.json").write_text(json.dumps({"session_id": "S-F", "advice": {"model": "claude-unknown-9", "effort": "high", "reason": "x", "switch_cost_tokens": 1, "at": _now31, "when": "now", "differs": True}}))
_m1m31 = next((m["id"] for m in _m31 if "[" in m["id"]), None)
(adv31 / "S-L.json").write_text(json.dumps({"session_id": "S-L", "advice": {"model": _m1m31.split("[")[0], "effort": "low", "reason": "base id", "switch_cost_tokens": 1, "at": _now31, "when": "next_task", "differs": False}}))
write_cfg(advice_dir=str(adv31))
rows_alive("ledger-api", "atlas-shop", "field-notes")
d31 = json.loads(relay("push", "--dry-run").stdout)
s31 = {x["name"]: x for x in d31["sessions"]}
T.check("R31 (1.37) advice from fable-director's snapshot <advice_dir>/<session_id>.json: {model, effort, reason on one line, switch_cost_tokens, at, source, when, differs}; a model outside the choices and an advice older than advice_max_age_s → no field",
        s31["atlas-shop"].get("advice") == {"model": _m31[1]["id"], "effort": "high", "reason": "Checkout refactor: Fable high", "switch_cost_tokens": 36000, "at": _now31, "source": "fable-director", "when": "now", "differs": True}
        and "advice" not in s31["field-notes"], json.dumps({k: v.get("advice") for k, v in s31.items()}))
T.check("R31 (07/10) an advice with the base id («claude-opus-5») is kept when the choices carry the window («claude-opus-5[1m]»), as Tune.sameModel in the app",
        _m1m31 is not None and (s31["ledger-api"].get("advice") or {}).get("model") == _m1m31.split("[")[0], json.dumps(s31["ledger-api"].get("advice")))
(adv31 / "S-F.json").write_text(json.dumps({"session_id": "S-F", "advice": {"model": _m31[1]["id"], "effort": "low", "reason": "old", "switch_cost_tokens": 1, "at": _now31 - 30000, "when": "now", "differs": False}}))
T.check("R31 (1.37) an advice older than advice_max_age_s (6 h) → no field",
        "advice" not in {x["name"]: x for x in json.loads(relay("push", "--dry-run").stdout)["sessions"]}["field-notes"], "")
T.check("R31 (1.37) approvals from the registry's tasks in awaiting_ok, the oldest first, deploy true when what or where names production",
        d31["approvals"] == [{"task": "notes-pr", "title": "Open the PR of the notes", "what": "push the branch notes", "where": "github fork", "deploy": False, "requested_at": 1789210500},
                             {"task": "atlas-release-2-4", "title": "Release 2.4 of atlas-shop", "what": "tag v2.4 and push to origin main", "where": "production (shop.example.com)", "deploy": True, "requested_at": 1789210600}], str(d31["approvals"]))
tasks31.unlink()
T.check("R31 (1.37) without tasks waiting approvals is an empty list (always present); ops carries approve and decision",
        json.loads(relay("push", "--dry-run").stdout)["approvals"] == [] and {"approve", "decision"} <= set(d31["ops"]), "")
rows_alive("ledger-api", "atlas-shop", "field-notes", **{"field-notes": {"started_at": 1789200000000}})
_two31 = json.loads(alive.read_text())
_two31.append(dict(_two31[2], pid=10, name="field-notes-2", tmux="field-notes-2", started_at=1789210000000, link=""))
alive.write_text(json.dumps(_two31))
d31b = json.loads(relay("push", "--dry-run").stdout)
s31b = {x["name"]: x for x in d31b["sessions"]}
T.check("R31 (1.37) duplicate_of: the session opened later on the same folder and the same conversation points to the first; the first and the others without the field",
        s31b["field-notes-2"].get("duplicate_of") == "field-notes" and "duplicate_of" not in s31b["field-notes"] and "duplicate_of" not in s31b["atlas-shop"], json.dumps({k: v.get("duplicate_of") for k, v in s31b.items()}))
write_cfg()
rows_alive("ledger-api", "atlas-shop", "field-notes")
T.check("R32 (1.38) next_steps from the «Prossimi:» line: [{text, blocking}], «!» marks the item that unblocks stalled work and leaves the text; outcome.full carries the line without «!»; no line → no field",
        [x for x in F1["sessions"] if x["name"] == "atlas-shop"][0].get("next_steps") == [{"text": "ok to deploy on staging", "blocking": True}, {"text": "review the test seeds", "blocking": False}]
        and "Prossimi: ok to deploy on staging · review the test seeds" in [x for x in F1["sessions"] if x["name"] == "atlas-shop"][0]["outcome"]["full"]
        and all("next_steps" not in x for x in F1["sessions"] if x["name"] != "atlas-shop")
        and S.next_steps("**Prossimi:** `!a` · b · c · d") == [{"text": "a", "blocking": True}, {"text": "b", "blocking": False}, {"text": "c", "blocking": False}], "")
# R33 (contratto 1.39, 05/10): i browser come dispositivi — kind web, otto posti, op unpair
_dev33 = (rdir2 / "devices.json").read_text() if (rdir2 / "devices.json").exists() else "{}"
_all33 = STORE.get("allowed") or {}
(rdir2 / "devices.json").write_text(json.dumps({"phoneUid00000000000000000000": {"name": "Pixel 9", "paired_at": 1, "kind": "phone"},
                                                "webUid000000000000000000000": {"name": "Safari on Mac", "paired_at": 2, "kind": "web"}}))
http("PUT", "/allowed.json", {"phoneUid00000000000000000000": True, "webUid000000000000000000000": True})
http("PUT", "/seen/webUid000000000000000000000.json", 1789210000000)
r33a = send_cmd(CMDS[36])
r33b = send_cmd(CMDS[35])
_left33 = json.loads((rdir2 / "devices.json").read_text())
r33c = send_cmd(dict(CMDS[35], id="6f1c2d3e-0390-4000-8000-000000000392", arg="phoneUid00000000000000000000"))
T.check("R33 (1.39) unpair: an unknown uid refused as in the fixture; the browser's uid out of /allowed, /seen and devices.json, «removed <name>» as in the fixture; the last device refused",
        r33a and r33a["ok"] is False and r33a["text"] == RES[36]["text"] and r33b and r33b["ok"] is True and r33b["text"] == RES[35]["text"]
        and list(STORE.get("allowed") or {}) == ["phoneUid00000000000000000000"] and "webUid000000000000000000000" not in (STORE.get("seen") or {})
        and list(_left33) == ["phoneUid00000000000000000000"] and r33c and r33c["ok"] is False and r33c["text"] == "cannot remove the last device"
        and list(STORE.get("allowed") or {}) == ["phoneUid00000000000000000000"], str([r33a, r33b, r33c, STORE.get("allowed")]))
(rdir2 / "devices.json").write_text(_dev33)
http("PUT", "/allowed.json", _all33 or None) if _all33 else http("DELETE", "/allowed.json")
T.check("R33 (1.39) eight places (PAIR_MAX_DEVICES) and kind web: a browser's pairing answer keeps its kind; state.devices shows it",
        RL.PAIR_MAX_DEVICES == 8 and "web" in S.DEVICE_KINDS and any(d["kind"] == "web" and d["name"] == "Safari on Mac" for d in F1["devices"]) and "unpair" in F1["ops"], "")
_ev31 = lambda tail: [{"event": "stop", "session_id": "s", "ts": iso(1), "last": "x", "esito": "", "tail": tail}]  # noqa: E731
T.check("R31 (1.37) finished: the last message has «Esito:» and no «Prossimi:» (kernel rule 10); with «Prossimi:», without «Esito:» or with no stop → not finished",
        S._finished(_ev31("Done.\nEsito: README rewritten")) and not S._finished(_ev31("Esito: half done\nProssimi: tests · docs"))
        and not S._finished(_ev31("just talking")) and not S._finished([]), "")
# R30 (contratto 1.35, 05/10, chiesto dall'app per il maintainer): la web app e l'API locale su 127.0.0.1 dentro serve —
# ogni scambio di local-api.json rifatto sul relay vero, con la porta di prova al posto della 8765
import socket as _so30, http.client as _hc30
LA = json.loads((FIX / "local-api.json").read_text())
_s30 = _so30.socket(); _s30.bind(("127.0.0.1", 0)); port30 = _s30.getsockname()[1]; _s30.close()
web30 = tmp / "web-dist"; (web30 / "assets").mkdir(parents=True, exist_ok=True)
(web30 / "index.html").write_text("<!doctype html><title>cm</title>"); (web30 / "assets" / "app.js").write_text("console.log(1)")
(rdir2 / "web-token").write_text(LA["token"] + "\n")
write_cfg(web={"enabled": True, "port": port30, "dir": str(web30)})
relay("off"); relay("ensure")


def web30_up():
    try:
        _so30.create_connection(("127.0.0.1", port30), timeout=1).close(); return True
    except OSError:
        return False


T.check("R30 (1.35) relay.web.enabled → serve listens on 127.0.0.1:<relay.web.port> only", T.wait_until(web30_up, 8) and serve_pid() > 0, (rdir2 / "relay.log").read_text()[-300:])
(rdir2 / "local").mkdir(exist_ok=True)
(rdir2 / "local" / "state.json").write_text((FIX / "state-2-idle.json").read_text())


def ws30(o):   # i percorsi demo della fixture sulla cartella di prova
    return json.loads(json.dumps(o).replace("/home/demo/workspaces", str(ws.resolve())))


(rdir2 / "local" / "events.json").write_text(json.dumps({e["key"]: e for e in ws30(json.loads((FIX / "events-sample.json").read_text()))}))
bus30 = (set(STORE.get("result") or {}), set(STORE.get("file") or {}))


def call30(req):
    c = _hc30.HTTPConnection("127.0.0.1", port30, timeout=10)
    h = {"Host": f"127.0.0.1:{port30}", **{k: v.replace(":8765", f":{port30}") for k, v in req["headers"].items()}}
    body = json.dumps(ws30(req["body"])).encode() if "body" in req else None
    c.request(req["method"], req["path"], body=body, headers=h)
    r = c.getresponse()
    if r.getheader("Content-Type", "").startswith("text/event-stream"):
        lines = []
        while len(lines) < 3:
            lines.append(r.fp.readline().decode().rstrip("\n"))
        c.close()
        return r, lines
    data = r.read(); c.close()
    return r, data


for ex in LA["exchanges"]:
    r, data = call30(ex["request"])
    want = ex["response"]
    exp = json.loads((FIX / want["body_ref"]).read_text()) if "body_ref" in want else ws30(want.get("body"))
    if want.get("first_event"):
        got_ok = data[0] == f"event: {want['first_event']}" and json.loads(data[1][6:]) == exp
    elif "body_base64" in want:
        got_ok = data == base64.b64decode(want["body_base64"]) and r.getheader("Content-Disposition") == want["content_disposition"]
    else:
        got = json.loads(data)
        got_ok = {k: v for k, v in got.items() if k not in want.get("vary", [])} == {k: v for k, v in exp.items() if k not in want.get("vary", [])} if isinstance(exp, dict) else got == exp
        got_ok = got_ok and all(isinstance(got.get(k), int) for k in want.get("vary", []))
    T.check(f"R30 (1.35) local-api.json «{ex['name']}»: {ex['request']['method']} {ex['request']['path'].split('?')[0]} → {want['status']} {want['content_type'].split(';')[0]}, body as in the fixture",
            r.status == want["status"] and r.getheader("Content-Type") == want["content_type"] and got_ok and r.getheader("Access-Control-Allow-Origin") is None, f"{r.status} {r.getheader('Content-Type')} {str(data)[:300]}")
n_calls = len(cm_calls())
r30p, d30p = call30({"method": "POST", "path": "/api/cmd", "headers": {"Authorization": "Bearer " + LA["token"]}, "body": {"op": "prompt", "session": "atlas-shop", "arg": "local hello"}})
T.check("R30 (1.36) a prompt through the local API without device is from the web app: the web app's prefix, by «web»",
        r30p.status == 200 and json.loads(d30p)["ok"] is True and any(c.startswith("talk atlas-shop Dall'utente via web app.") and "local hello" in c for c in cm_calls()[n_calls:])
        and "da web (locale)" in (rdir2 / "relay.log").read_text(), str(d30p) + str(cm_calls()[n_calls:]))
_sid30 = LA["exchanges"][7]["request"]["path"].rsplit("/", 1)[1]
T.check("R30 (1.35) the local attachment is used by report and then deleted; the report got the bytes", not list((rdir2 / "local" / "share").glob(f"{_sid30}*")) and (tmp / "report-img").read_bytes() == base64.b64decode(LA["exchanges"][7]["request"]["body"]["data"]), str(list((rdir2 / "local" / "share").iterdir())))
T.check("R30 (1.35) a local command goes through the same path (relay.log marks it «locale»), and leaves nothing on the bus (/result, /file)",
        f"cmd {LA['exchanges'][8]['request']['body']['id']}: report atlas-shop da phone-pixel8 (locale)" in (rdir2 / "relay.log").read_text()
        and (set(STORE.get("result") or {}), set(STORE.get("file") or {})) == bus30, (rdir2 / "relay.log").read_text()[-400:])
tok30 = LA["token"]
_get = lambda path, h=None: call30({"method": "GET", "path": path, "headers": h if h is not None else {"Authorization": "Bearer " + tok30}})  # noqa: E731
r1, d1 = _get("/"); r2, d2 = _get("/assets/app.js", {}); r3, d3 = _get("/chat/atlas-shop", {}); r4, _ = _get("/assets/missing.js", {}); r5, _ = _get("/../config.json", {})
T.check("R30 (1.35) static files from relay.web.dir on / without a token: index.html, assets with their type, app routes → index.html, a missing asset 404, no way out of the folder",
        r1.status == 200 and d1.startswith(b"<!doctype") and r1.getheader("Content-Type").startswith("text/html") and r2.status == 200 and "javascript" in r2.getheader("Content-Type")
        and r3.status == 200 and d3 == d1 and r4.status == 404 and r5.status == 404, f"{r1.status} {r2.status} {r3.status} {r4.status} {r5.status}")
r6, d6 = call30({"method": "POST", "path": "/api/cmd", "headers": {"Authorization": "Bearer " + tok30, "Origin": "http://evil.example"}, "body": {"op": "screen", "session": "atlas-shop"}})
r7, d7 = call30({"method": "POST", "path": "/api/cmd", "headers": {"Authorization": "Bearer nope"}, "body": {"op": "screen", "session": "atlas-shop"}})
r8, d8 = call30({"method": "POST", "path": "/api/cmd", "headers": {"Authorization": "Bearer " + tok30}, "body": {"op": "rm", "session": "atlas-shop"}})
r9, d9 = _get("/api/events?since=x"); r10, d10 = call30({"method": "POST", "path": "/api/state?t=" + tok30, "headers": {}, "body": {}})
T.check("R30 (1.35) refusals: another Origin 403, a wrong token 401, the token in the query only for GET, an op outside the allow-list → CmdResult ok false with a fresh id, a bad since 400",
        r6.status == 403 and r7.status == 401 and r10.status == 401 and r8.status == 200 and json.loads(d8)["ok"] is False and len(json.loads(d8)["id"]) == 36 and r9.status == 400, f"{r6.status} {r7.status} {r10.status} {d8} {r9.status}")
c30 = _hc30.HTTPConnection("127.0.0.1", port30, timeout=10)
c30.request("GET", "/api/stream?t=" + tok30, headers={"Host": f"127.0.0.1:{port30}"})
rs30 = c30.getresponse()
first30 = [rs30.fp.readline() for _ in range(3)]
relay("push")
nxt30 = [rs30.fp.readline().decode() for _ in range(2)]
c30.close()
T.check("R30 (1.35) a push rewrites the local state and the stream sends it (event: state, the state just pushed)",
        nxt30[0].startswith("event: state") and json.loads(nxt30[1][6:])["host"] == "crostini-test"
        and {"ledger-api", "atlas-shop", "field-notes"} <= {x["name"] for x in json.loads(nxt30[1][6:])["sessions"]}, str(nxt30)[:200])
_ev30 = json.loads((rdir2 / "local" / "events.json").read_text())
T.check("R30 (1.35) the events of a push go to the local copy too, the old ones (beyond events_days) pruned", all(e["ts"] > time.time() - 8 * 86400 for e in _ev30.values()), str(list(_ev30)[:5]))
fid30 = "6f1c2d3e-0350-4000-8000-000000000350"
(rdir2 / "local" / "file").mkdir(exist_ok=True)
(rdir2 / "local" / "file" / f"{fid30}.json").write_text(json.dumps({"path": str(fn22 / "docs" / "cover.png"), "mime": "image/png", "name": "cover.png"}))
os.utime(rdir2 / "local" / "file" / f"{fid30}.json", (time.time() - 700, time.time() - 700))
T.check("R30 (1.35) a requested file older than 10 minutes is no longer served", _get(f"/api/file/{fid30}")[0].status == 404, "")
r = relay("web", "--no-open")
T.check("R30 (1.35) relay web prints http://127.0.0.1:<port>/?t=<token>", r.returncode == 0 and f"http://127.0.0.1:{port30}/?t={tok30}" in r.stdout, r.stdout + r.stderr)
opened30, opener30 = tmp / "opened", tmp / "fake-open.sh"
opener30.write_text(f'#!/bin/sh\necho "$@" > {opened30}\n'); opener30.chmod(0o755)
r = relay("web", env=dict(ENV, CM_RELAY_OPEN=str(opener30)))
T.check("R30 (1.35) relay web opens the address in the browser", r.returncode == 0 and T.wait_until(lambda: opened30.exists() and f"/?t={tok30}" in opened30.read_text(), 4), r.stdout + r.stderr)
write_cfg()
relay("off"); relay("ensure")
T.check("R30 (1.35) without relay.web.enabled nothing listens, and relay web says how to turn it on (exit 2)", T.wait_until(lambda: not web30_up(), 5) and relay("web", "--no-open").returncode == 2, "")
pid1 = serve_pid()   # il daemon riavviato due volte qui sopra: R6 piu' avanti controlla che resti questo
# R23 (contratto 1.25, 02/10): op slash — un comando slash digitato nel pannello della sessione, solo dalla lista
rows_alive("ledger-api", "atlas-shop", "field-notes")
n_calls = len(cm_calls())
r23 = send_cmd(CMDS[24])
r23b = send_cmd(CMDS[25])
T.check("R23 (1.25) slash compact with its text → typed in the pane: `talk field-notes /compact <text> --via tmux --no-wait`, no prefix; /result as in the fixture",
        r23 and r23["ok"] is True and r23["text"] == RES[24]["text"]
        and "talk field-notes /compact keep the meeting notes and the open questions --via tmux --no-wait" in cm_calls()[n_calls:], str(r23) + str(cm_calls()[n_calls:]))
T.check("R23 (1.25) a command not in relay.slash_commands (as in the fixture) → «not allowed: model», nothing typed",
        r23b and r23b["ok"] is False and r23b["text"] == RES[25]["text"] and not any(c.startswith("talk field-notes /model") for c in cm_calls()[n_calls:]), str(r23b))
n_calls = len(cm_calls())
r23c = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000201", session="atlas-shop"))
r23d = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000202", session="ledger-api"))
r23e = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000203", session="nessuna"))
r23f = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000204", arg="/exit", text=None))
T.check("R23 (1.25) a busy session and one on a dialog → «<name> is busy», a missing one → «no session <name>», nothing typed; /exit (with its slash) is typed like the others",
        r23c and r23c["text"] == "atlas-shop is busy" and r23d and r23d["text"] == "ledger-api is busy" and r23e and r23e["text"] == "no session nessuna"
        and r23f and r23f["ok"] is True and r23f["text"] == "sent /exit to field-notes"
        and [c for c in cm_calls()[n_calls:] if c.startswith("talk ")] == ["talk field-notes /exit --via tmux --no-wait"], str([r23c, r23d, r23e, r23f]) + str(cm_calls()[n_calls:]))
n_calls = len(cm_calls())
r23g = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000205", arg="cost", text=None))
T.check("R23 a command that opens a panel (cost): `panel field-notes --wait 6` reads and closes it, its text comes back in /result; compact and exit never wait for a panel",
        r23g and r23g["ok"] is True and r23g["text"].startswith("sent /cost to field-notes\n\n") and "Total cost:            $0.42" in r23g["text"]
        and "panel field-notes --wait 6" in cm_calls()[n_calls:] and not any(c.startswith("panel") for c in cm_calls()[:n_calls][-12:]), str(r23g) + str(cm_calls()[n_calls:]))
st23 = json.loads(relay("push", "--dry-run").stdout)
T.check("R23 (1.25) /state carries slash = relay.slash_commands without «/», and ops carries slash",
        st23.get("slash") == ["compact", "clear", "exit", "context", "cost"] and "slash" in st23.get("ops", []) and F1["slash"] == st23["slash"], str(st23.get("slash")))
# R24 (contratto 1.26, 02/10): op projects — l'elenco completo per «Lancia», dal piu' recente; fit_state tiene i recenti
import os as _os24, re as _re24
(ws / "personal" / "sketchbook").mkdir(parents=True, exist_ok=True)   # nessuna trascrizione: last_used null
for _proj, _conf, _t in (("personal/atlas-shop", ".claude", 1789210700), ("work/clients/ledger-api", ".claude-pixel", 1789210500),
                         ("work/own/orbit-docs", ".claude-pixel", 1789203600), ("personal/field-notes", ".claude", 1789120000)):
    _d = home / _conf / "projects" / _re24.sub(r"[^A-Za-z0-9]", "-", str((ws / _proj).resolve()))
    _d.mkdir(parents=True, exist_ok=True)
    if not list(_d.glob("*.jsonl")):
        (_d / "r24.jsonl").write_text("{}\n")
    for _f in _d.glob("*.jsonl"):
        _os24.utime(_f, (_t, _t))
r24 = send_cmd(CMDS[26])
_got24 = json.loads((r24 or {}).get("text") or "{}")
for _p in _got24.get("projects", []):
    _p["path"] = _p["path"].replace(str(ws.resolve()), ROOT_WS).replace(str(ws), ROOT_WS)
T.check("R24 (1.26) projects → every project of every account, most recent first and null last, the fields of state.projects, more false; as in the fixture",
        r24 and r24["ok"] is True and _got24 == json.loads(RES[26]["text"]), json.dumps(_got24)[:400])
_many = [{"path": f"/p/{i:03d}", "name": f"p{i:03d}", "account": "personal", "last_used": (1789000000 + i if i % 7 == 0 else None)} for i in range(98)]
_fit24 = S.fit_state({"v": 1, "ts": 1, "host": "", "sessions": [], "quota": {}, "projects": [dict(p) for p in _many], "night": {"queued": 0, "running": None, "items": []},
                      "recap": {"date": "", "items": []}, "choices": None, "share": {"max_bytes": 1}, "ops": [], "slash": []}, 1)
_names24 = [p["name"] for p in _fit24["projects"]]
T.check("R24 (1.26) fit_state cutting projects keeps the most recently used (not the first by name), still listed by name",
        len(_names24) in (5, 10) and _names24 == sorted(_names24) and all(int(n[1:]) % 7 == 0 for n in _names24)
        and int(_names24[-1][1:]) == max(i for i in range(98) if i % 7 == 0), str(_names24))
T.check("R24 (1.26) /state ops carries projects", "projects" in json.loads(relay("push", "--dry-run").stdout).get("ops", []), "")
# R25 (contratto 1.27, 03/10): op search — un testo in tutte le conversazioni, vive e chiuse degli ultimi 7 giorni
import time as _t25
_od25 = (ws / "work" / "own" / "orbit-docs").resolve()
_d25 = home / ".claude-pixel" / "projects" / _re24.sub(r"[^A-Za-z0-9]", "-", str(_od25))
_pfx25 = json.loads((T.SCRIPTS.parent / "messages" / "it.json").read_text())["relay.prompt_prefix_phone"]
def _l25(kind, uid, ts, content, **kw):
    return json.dumps(dict({"type": kind, "uuid": uid, "timestamp": ts, "isSidechain": False, "cwd": str(_od25),
                            **({"origin": {"kind": "human"}} if kind == "user" else {}), "message": {"role": kind, "content": content}}, **kw), separators=(",", ":"))
_long25 = "We went through the whole backlog again and the only thing that still blocks the release is the review of the migration scripts, which moved to TUESDAY after the client asked for one more pass on the data model and the exports."
(_d25 / "C-O.jsonl").write_text("\n".join([
    _l25("user", "c1", "2026-09-13T08:00:00.000Z", "Perché la riunione di Tuesday è saltata?\nRispondi breve."),
    _l25("assistant", "c2", "2026-09-13T08:00:10.000Z", [{"type": "text", "text": _long25}]),
    _l25("assistant", "c3", "2026-09-13T08:00:20.000Z", [{"type": "tool_use", "id": "x1", "name": "Bash", "input": {"command": "grep -rn Tuesday ."}}]),
    _l25("assistant", "c4", "2026-09-13T08:00:30.000Z", [{"type": "text", "text": "subagent: Tuesday is fine"}], isSidechain=True),
    _l25("user", "c5", "2026-09-13T08:01:00.000Z", _pfx25 + "\n\nmove the review to tuesday"),
    _l25("user", "c6", "2026-09-13T08:01:30.000Z", [{"type": "tool_result", "tool_use_id": "x1", "content": "notes.md: Tuesday"}]),
]) + "\n")
(_d25 / "C-OLD.jsonl").write_text(_l25("user", "o1", "2026-09-01T08:00:00.000Z", "Tuesday, eight days ago") + "\n")
_now25 = _t25.time()
_os24.utime(_d25 / "C-O.jsonl", (_now25 - 86400, _now25 - 86400))
_os24.utime(_d25 / "C-OLD.jsonl", (_now25 - 8 * 86400, _now25 - 8 * 86400))
(tdir19 / "S-F.jsonl").write_text((FIX / "transcript-sample.jsonl").read_text().replace("/home/demo/workspaces", str(ws.resolve())))
_os24.utime(tdir19 / "S-F.jsonl", (1789120000, 1789120000))   # vecchia ma viva: entra lo stesso
rows_alive("field-notes")
r25 = send_cmd(CMDS[27])
T.check("R25 (04/10) serve fills the search cache in the background at start: the log says «cache pronta» with how many transcripts",
        bool(re.search(r"search: cache pronta, \d+ trascrizioni in \d+ s", (rdir2 / "relay.log").read_text())), (rdir2 / "relay.log").read_text()[-300:])
_got25 = json.loads((r25 or {}).get("text") or "{}")
for _h in _got25.get("hits", []):
    _h["project"] = _h["project"].replace(str(ws.resolve()), ROOT_WS).replace(str(ws), ROOT_WS)
T.check("R25 (1.27) search «tuesday» → user and assistant entries of the live session (even with an old transcript) and of a closed one of the last 7 days, newest first; no tool call, tool result, subagent or 8-day-old transcript; the relay's prefix stripped; case and accents ignored; as in the fixture",
        r25 and r25["ok"] is True and _got25 == json.loads(RES[27]["text"]), json.dumps(_got25, ensure_ascii=False)[:600])
_h25 = {h["entry"]: h for h in _got25.get("hits", [])}
T.check("R25 (1.27) snippet: at most 160 characters around the word, newlines joined into spaces, no «…», match = [start, end] of the word in it",
        all(len(h["snippet"]) <= 160 and "\n" not in h["snippet"] and "…" not in h["snippet"] and h["snippet"][h["match"][0]:h["match"][1]].lower() == "tuesday" for h in _got25.get("hits", []))
        and _h25.get("c2.0", {}).get("snippet", "") != _long25 and len(_h25.get("c2.0", {}).get("snippet", "")) == 160 and _h25.get("c1.0", {}).get("snippet") == "Perché la riunione di Tuesday è saltata? Rispondi breve.", str(_h25)[:400])
_acc25 = json.loads(send_cmd(dict(CMDS[27], id="6f1c2d3e-0170-4000-8000-000000000171", arg="PERCHE"))["text"])
_bad25 = [send_cmd(dict(CMDS[27], id=f"6f1c2d3e-0170-4000-8000-00000000017{i}", arg=a)) for i, a in ((2, "   "), (3, "x" * 201))]
T.check("R25 (1.27) «PERCHE» finds «Perché»; an empty text and one over 200 characters → ok false «bad query»",
        [h["entry"] for h in _acc25["hits"]] == ["c1.0"] and _acc25["hits"][0]["match"] == [0, 6] and all(b and b["ok"] is False and b["text"].startswith("bad query") for b in _bad25), str(_acc25) + str(_bad25))
_many25 = "\n".join(_l25("assistant", f"m{i:03d}", f"2026-09-14T08:{i // 60:02d}:{i % 60:02d}.000Z", [{"type": "text", "text": f"zanzibar note number {i}"}]) for i in range(70))
(_d25 / "C-MANY.jsonl").write_text(_many25 + "\n")
_os24.utime(_d25 / "C-MANY.jsonl", (_now25 - 3600, _now25 - 3600))
_m25 = json.loads(send_cmd(dict(CMDS[27], id="6f1c2d3e-0170-4000-8000-000000000174", arg="zanzibar"))["text"])
T.check("R25 (1.27) over 50 hits → the 50 newest, more true", len(_m25["hits"]) == 50 and _m25["more"] is True and _m25["hits"][0]["entry"] == "m069.0" and _m25["hits"][-1]["entry"] == "m020.0", str([h["entry"] for h in _m25["hits"]][:3]))
T.check("R25 (1.27) /state ops carries search", "search" in json.loads(relay("push", "--dry-run").stdout).get("ops", []), "")
# R26 (contratto 1.28, 03/10): file di ogni formato dal telefono — i byte nella cartella della sessione, la sessione avvisata
PDF = b"%PDF-1.4\n" + bytes(range(256)) * 8
rows_alive("ledger-api", "atlas-shop", "field-notes")
sid26 = CMDS[28]["arg"]
http("PUT", f"/share/{sid26}.json", C.encrypt({"mime": "application/pdf", "data": base64.b64encode(PDF).decode(), "name": "../../Preventivo  cliente?.pdf"}, k))
n_calls = len(cm_calls())
r26 = send_cmd(CMDS[28])
inbox26 = atlas / ".claude-master-inbox"
saved26 = sorted(inbox26.glob("*-Preventivo cliente.pdf"))
_log26 = "\n".join(cm_calls()[n_calls:])   # il messaggio va a capo: una chiamata occupa piu' righe del registro
talk26 = [_log26[_log26.index("talk atlas-shop "):]] if "talk atlas-shop " in _log26 else []
T.check("R26 (1.28) a PDF with a dirty name → /result as in the fixture; the bytes intact in <session folder>/.claude-master-inbox/<stamp>-<clean name>, not readable by others; no report run",
        r26 and r26["ok"] is True and r26["text"] == RES[28]["text"] and len(saved26) == 1 and saved26[0].read_bytes() == PDF and (saved26[0].stat().st_mode & 0o077) == 0
        and not any(c.startswith("report ") for c in cm_calls()[n_calls:]), str(r26) + str(list(inbox26.glob("*"))))
T.check("R26 (1.28) the inbox has a .gitignore «*» (never in a commit), and /share/<id> is deleted",
        (inbox26 / ".gitignore").read_text() == "*\n" and sid26 not in (STORE.get("share") or {}), "")
T.check("R26 (1.28) the session is told with the phone's prefix: file name, type, size, absolute path, then the text",
        len(talk26) == 1 and "Dall'utente via telefono." in talk26[0] and f"ti ho mandato il file Preventivo cliente.pdf (application/pdf, 2 KB): {saved26[0] if saved26 else '?'}" in talk26[0]
        and (CMDS[28]["text"] + " --no-wait") in talk26[0], str(talk26))
sid26b = "6f1c2d3e-0180-4000-8000-00000000c001"
http("PUT", f"/share/{sid26b}.json", C.encrypt({"mime": "text/plain", "data": base64.b64encode(b"hello").decode()}, k))
r26b = send_cmd(dict(CMDS[28], id="6f1c2d3e-0180-4000-8000-000000000181", arg=sid26b, text=""))
sid26c = "6f1c2d3e-0180-4000-8000-00000000c002"
http("PUT", f"/share/{sid26c}.json", C.encrypt({"mime": "application/zip", "data": base64.b64encode(b"PK").decode(), "name": "..//\x01"}, k))
r26c = send_cmd(dict(CMDS[28], id="6f1c2d3e-0180-4000-8000-000000000182", arg=sid26c))
sid26d = "6f1c2d3e-0180-4000-8000-00000000c003"
http("PUT", f"/share/{sid26d}.json", C.encrypt({"mime": "application/zip", "data": base64.b64encode(b"PK").decode(), "name": "x" * 121}, k))
r26d = send_cmd(dict(CMDS[28], id="6f1c2d3e-0180-4000-8000-000000000183", arg=sid26d))
T.check("R26 (1.28) no name → «file» with the type's extension, no text needed; a name that cleans to nothing or over 120 characters → ok false «bad name»",
        r26b and r26b["ok"] is True and r26b["text"] == "sent file.txt to atlas-shop" and any(inbox26.glob("*-file.txt"))
        and r26c and r26c["ok"] is False and r26c["text"] == "bad name" and r26d and r26d["text"] == "bad name", str([r26b, r26c, r26d]))
T.check("R26 (1.28) /state share.any = true (the app shows «File» only with this relay)", json.loads(relay("push", "--dry-run").stdout).get("share", {}).get("any") is True and F1["share"]["any"] is True, "")
# R42 (contratto 1.48, 09/10): un file dal dispositivo a pezzi — /share/<id>/parts/<k> e /share/<id>/meta, fino a 50 MB
import hashlib as _h42
SP = json.loads((FIX / "share-parts.json").read_text())
_ksp = bytes.fromhex(SP["key"])
T.check("R42 (1.48) fixture share-parts.json: meta opens with the test key to its plain, the parts open to the file in order, sha256 and size of the joined bytes = meta",
        C.decrypt(SP["meta"], _ksp) == SP["meta_plain"] and b"".join(C.decrypt_raw(x, _ksp) for x in SP["parts"]) == SP["file"].encode()
        and _h42.sha256(SP["file"].encode()).hexdigest() == SP["meta_plain"]["sha256"] and SP["meta_plain"]["size"] == len(SP["file"].encode())
        and SP["meta_plain"]["n"] == len(SP["parts"]) == 3, "")


def _parts42(sid, data, mime, name, part, skip=(), **over):
    chunks = [data[i:i + part] for i in range(0, len(data), part)] or [b""]
    for i, c in enumerate(chunks):
        if i not in skip:
            http("PUT", f"/share/{sid}/parts/{i}.json", C.encrypt_raw(c, k))
    meta = dict({"n": len(chunks), "size": len(data), "sha256": _h42.sha256(data).hexdigest(), "mime": mime, "name": name}, **over)
    http("PUT", f"/share/{sid}/meta.json", C.encrypt(meta, k))


_c42 = {c["id"]: c for c in CMDS}
_r42 = {r["id"]: r for r in RES}
_ok42, _bad42 = "6f1c2d3e-0480-4000-8000-000000000480", "6f1c2d3e-0481-4000-8000-000000000481"
_parts42(_c42[_ok42]["arg"], SP["file"].encode(), "text/plain", "notes.txt", SP["part_bytes"])
r42a = send_cmd(_c42[_ok42])
saved42 = sorted(inbox26.glob("*-notes.txt"))
T.check("R42 (1.48) a text file in three parts → put back together, in the session's inbox byte for byte, /result as in the fixture's …0480; /share/<id> gone",
        r42a and r42a["ok"] is True and r42a["text"] == _r42[_ok42]["text"] and len(saved42) == 1 and saved42[0].read_bytes() == SP["file"].encode()
        and _c42[_ok42]["arg"] not in (STORE.get("share") or {}), str(r42a) + str(list(STORE.get("share") or {})))
_parts42(_c42[_bad42]["arg"], b"x" * 40, "text/plain", "b.txt", 16, skip=(1,))
r42b = send_cmd(_c42[_bad42])
_bad42s = []
for _n42, _over42 in enumerate(({"sha256": "0" * 64}, {"size": 39}, {"n": 51}, {"n": 0})):
    _sid42 = f"6f1c2d3e-0482-4000-8000-0000000b{_n42:04d}"
    _parts42(_sid42, b"y" * 40, "text/plain", "c.txt", 16, **_over42)
    _bad42s.append((send_cmd(dict(_c42[_bad42], id=f"6f1c2d3e-0482-4000-8000-00000000c{_n42:03d}", arg=_sid42)) or {}).get("text"))
_sid42 = "6f1c2d3e-0483-4000-8000-00000000b483"
_parts42(_sid42, b"z", "application/zip", "big.zip", 16, size=50 * 1024 * 1024 + 1)
r42c = send_cmd(dict(_c42[_bad42], id="6f1c2d3e-0483-4000-8000-000000000483", arg=_sid42))
T.check("R42 (1.48) a part missing → «bad parts» as the fixture's …0481; another sha256, another size, n over 50 or 0 → «bad parts»; over 50 MB → «too large: 52428801 max 52428800»; every node gone",
        r42b and r42b["ok"] is False and r42b["text"] == _r42[_bad42]["text"] == "bad parts" and _bad42s == ["bad parts"] * 4
        and r42c and r42c["text"] == "too large: 52428801 max 52428800" and not [x for x in (STORE.get("share") or {}) if x.startswith("6f1c2d3e-048")],
        str([r42b, _bad42s, r42c, list(STORE.get("share") or {})]))
_big42 = _os22.urandom(2 * 1024 * 1024 + 12345)
_sid42 = "6f1c2d3e-0484-4000-8000-00000000b484"
_parts42(_sid42, _big42, "application/zip", "photos.zip", RL.FILE_PART_BYTES)
r42d = send_cmd(dict(_c42[_ok42], id="6f1c2d3e-0484-4000-8000-000000000484", arg=_sid42, text=""), wait=40)
saved42d = sorted(inbox26.glob("*-photos.zip"))
T.check("R42 (1.48) a 2 MB zip in three 1 MB parts → the same bytes in the inbox", r42d and r42d["ok"] is True and len(saved42d) == 1 and saved42d[0].read_bytes() == _big42, str(r42d))
_RU42 = load("cm-relay-setup").RULES["rules"]["share"]["$id"]
_kre42 = re.compile(_RU42["parts"]["$k"][".validate"].split(".matches(/")[1].split("/)")[0])
T.check("R42 (1.48) RTDB rules: meta and parts/<0..49> each within 1.5 MB, the single node of before still within 10 MB",
        "1500000" in _RU42["meta"][".validate"] and "1500000" in _RU42["parts"]["$k"][".validate"] and "10000000" in _RU42[".validate"]
        and all(_kre42.fullmatch(str(i)) for i in range(50)) and not any(_kre42.fullmatch(x) for x in ("50", "01", "-1", "a")), str(_RU42))
# R44 relay-prefix-handoff (10/10, il maintainer: «Aggiungi la regola al relay» — una sessione su win ha letto il divieto di
# SendMessage come divieto di passare un compito alla master): il prefisso dice come passarlo; a una sessione su un
# altro host dice di scriverlo nella risposta; i prompt di prima, senza la frase, restano riconosciuti
_co44 = load("cm-core")
_h44, _hr44 = RL.M("relay.prompt_handoff"), RL.M("relay.prompt_handoff_remote")
_loc44, _rem44 = RL.prefix_for({"device": "phone"}, "atlas-shop"), RL.prefix_for({"device": "phone"}, "win:video-supervisor")
T.check("R44 relay-prefix-handoff: the phone's prefix to a local session says «supervisor talk master»; to win:… the remote sentence instead (write it in the answer)",
        _h44 in _loc44 and "supervisor talk master" in _h44 and _hr44 in _rem44 and _h44 not in _rem44 and _rem44 == _loc44.replace(_h44, _hr44), _rem44)
_old44 = {lang: json.loads((T.SCRIPTS.parent / "messages" / f"{lang}.json").read_text()) for lang in ("it", "en")}
_forms44 = [(m["relay.prompt_prefix_phone"].replace(m["relay.prompt_handoff"], v), lang) for lang, m in _old44.items()
            for v in ("", m["relay.prompt_handoff"], m["relay.prompt_handoff_remote"])]
T.check("R44 relay-prefix-handoff: the transcript strips the prefix in its three forms (new, remote, before 10/10), it and en → origin phone, the bare text",
        all(_co44._origin_of(f + " ciao") == ("ciao", "phone") for f, _ in _forms44), str([(_co44._origin_of(f + " ciao"), l) for f, l in _forms44])[:500])
# R43 report-multi (contratto 1.50, 10/10, dall'app: «due allegati insieme vengono mostrati ancora su 2 post»): un
# report con piu' id di /share in `arg` — tutti letti prima, poi un messaggio solo alla sessione, ogni nodo cancellato
_c43 = {c["id"]: c for c in CMDS}
_r43 = {r["id"]: r for r in RES}
_ok43, _bad43 = "6f1c2d3e-0500-4000-8000-000000000500", "6f1c2d3e-0501-4000-8000-000000000501"
_PNG43, _JPG43 = b"\x89PNG\r\n\x1a\n" + b"a" * 300, b"\xff\xd8\xff\xe0" + b"b" * 200
_a43, _b43 = _c43[_ok43]["arg"].split(",")
http("PUT", f"/share/{_a43}.json", C.encrypt({"mime": "image/png", "data": base64.b64encode(_PNG43).decode()}, k))
_parts42(_b43, _JPG43, "image/jpeg", "b.jpg", 64)   # il secondo a pezzi (1.48): le due strade insieme
n_calls = len(cm_calls())
r43 = send_cmd(_c43[_ok43])
_log43 = "\n".join(cm_calls()[n_calls:])
_talks43 = _log43.count("talk atlas-shop ")
_seg43 = atlas / "docs" / "segnalazioni"
_day43 = time.strftime("%Y-%m-%d")
_p43 = [_seg43 / f"{_day43}-two-screens-of-the-checkout.png", _seg43 / f"{_day43}-two-screens-of-the-checkout.jpg"]
T.check("R43 report-multi (1.50): two ids (one single node, one in parts) → both images saved in the report folder, /result as the fixture's …0500 (today's date); both /share nodes gone",
        r43 and r43["ok"] is True and r43["text"] == _r43[_ok43]["text"].replace("2026-10-10", _day43)
        and [x.read_bytes() if x.is_file() else None for x in _p43] == [_PNG43, _JPG43]
        and not {_a43, _b43} & set(STORE.get("share") or {}), str(r43) + str(list(_seg43.glob("*two-screens*"))))
T.check("R43 report-multi (1.50): ONE message to the session — the phone's prefix, the text, then both paths in order; no report run",
        _talks43 == 1 and "Dall'utente via telefono." in _log43 and "two screens of the checkout" in _log43
        and str(_p43[0]) in _log43 and str(_p43[1]) in _log43 and _log43.index(str(_p43[0])) < _log43.index(str(_p43[1]))
        and not any(c.startswith("report ") for c in cm_calls()[n_calls:]), _log43[-600:])
_c43m, _e43m = _c43[_bad43]["arg"].split(",")
http("PUT", f"/share/{_c43m}.json", C.encrypt({"mime": "image/png", "data": base64.b64encode(_PNG43).decode()}, k))
n_calls = len(cm_calls())
_before43 = sorted(_seg43.glob("*"))
r43b = send_cmd(_c43[_bad43])
T.check("R43 report-multi (1.50): the second id missing → the whole report refused as the fixture's …0501, nothing saved, no message; the first node deleted anyway",
        r43b and r43b["ok"] is False and r43b["text"] == _r43[_bad43]["text"] and sorted(_seg43.glob("*")) == _before43
        and not any(c.startswith("talk ") for c in cm_calls()[n_calls:]) and _c43m not in (STORE.get("share") or {}), str(r43b))
_ids43 = [f"6f1c2d3e-0502-4000-8000-00000000c{i:03d}" for i in range(6)]
for _i43 in _ids43:
    http("PUT", f"/share/{_i43}.json", C.encrypt({"mime": "image/png", "data": base64.b64encode(_PNG43).decode()}, k))
r43c = send_cmd(dict(_c43[_ok43], id="6f1c2d3e-0502-4000-8000-000000000502", arg=",".join(_ids43)))
_d43, _f43 = "6f1c2d3e-0503-4000-8000-00000000d503", "6f1c2d3e-0503-4000-8000-00000000f503"
http("PUT", f"/share/{_d43}.json", C.encrypt({"mime": "image/png", "data": base64.b64encode(_PNG43).decode()}, k))
http("PUT", f"/share/{_f43}.json", C.encrypt({"mime": "application/pdf", "data": base64.b64encode(PDF).decode(), "name": "offerta.pdf"}, k))
r43d = send_cmd(dict(_c43[_ok43], id="6f1c2d3e-0503-4000-8000-000000000503", arg=f"{_d43},{_f43}", text="screen and offer"))
T.check("R43 report-multi (1.50): six ids → «too many attachments: at most 5», every node gone; an image and a PDF → «sent <image path>, offerta.pdf to atlas-shop», the PDF in the inbox",
        r43c and r43c["ok"] is False and r43c["text"] == "too many attachments: at most 5" and not set(_ids43) & set(STORE.get("share") or {})
        and r43d and r43d["ok"] is True and r43d["text"] == f"sent docs/segnalazioni/{_day43}-screen-and-offer.png, offerta.pdf to atlas-shop"
        and any(inbox26.glob("*-offerta.pdf")), str([r43c, r43d]))
T.check("R43 report-multi (1.50): /state share.multi = 5, as in the three state fixtures",
        json.loads(relay("push", "--dry-run").stdout).get("share", {}).get("multi") == 5 and F1["share"]["multi"] == 5, "")
# R27 (contratto 1.29, 03/10): op timeline — la cronologia delle sessioni per il riepilogo del telefono
# ledger-api (chiusa, account work): due test (verde e rosso), un commit, un esito e un compito, fra since e l'istante
_la = (ws / "work" / "clients" / "ledger-api").resolve()
_tl = home / ".claude-pixel" / "projects" / _re24.sub(r"[^A-Za-z0-9]", "-", str(_la))
_tl.mkdir(parents=True, exist_ok=True)
def _l27(kind, uid, t, content, **kw):
    from datetime import datetime as _dt, timezone as _tz
    return json.dumps(dict({"type": kind, "uuid": uid, "timestamp": _dt.fromtimestamp(t, _tz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "isSidechain": False,
                            "cwd": str(_la), "message": {"role": kind, "content": content}}, **kw), separators=(",", ":"))
(_tl / "S-L27.jsonl").write_text("\n".join([
    _l27("user", "l1", 1789205000, "add the invoices endpoint", origin={"kind": "human"}),
    _l27("assistant", "l2", 1789205600, [{"type": "tool_use", "id": "x1", "name": "Bash", "input": {"command": "python3 tests/api-verify.py 2>&1 | tail -1"}}]),
    _l27("user", "l3", 1789205660, [{"type": "tool_result", "tool_use_id": "x1", "is_error": False, "content": "3/4 OK, FAIL: A2 invoices"}]),
    _l27("assistant", "l4", 1789206600, [{"type": "tool_use", "id": "x2", "name": "Bash", "input": {"command": "python3 tests/api-verify.py"}}]),
    _l27("user", "l5", 1789206660, [{"type": "tool_result", "tool_use_id": "x2", "is_error": False, "content": "4/4 OK"}]),
    _l27("assistant", "l6", 1789207800, [{"type": "text", "text": "Done.\n\nEsito: invoices endpoint added, api suite green"}]),
]) + "\n")
_g27 = lambda *a, t=None: subprocess.run(["git", "-C", str(_la), *a], capture_output=True, text=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", **({"GIT_AUTHOR_DATE": f"@{t}", "GIT_COMMITTER_DATE": f"@{t}"} if t else {})))  # noqa: E731
if not (_la / ".git").exists():
    _g27("init", "-q", "-b", "main")
    (_la / "api.py").write_text("x")
    _g27("add", "api.py")
    _g27("commit", "-qm", "feat(api): invoices endpoint", t=1789207000)
import sqlite3 as _sq27
_db27 = Path(str(cfg)).parent / "tasks.db"
_tk27 = {"schema_version": 1, "task": {"id": "invoices", "title": "Invoices endpoint", "plan": None, "where": {"project": str(_la), "session": None, "host": "local", "account": None},
         "check": {"cmd": "true", "cwd": None, "timeout_s": 10}, "perimeter": [], "lane": "open", "depends_on": [], "route": "session", "hold": False,
         "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
subprocess.run([str(T.SCRIPTS / "supervisor"), "task", "add", "-"], input=json.dumps(_tk27), capture_output=True, text=True, env=dict(os.environ, **ENV))
subprocess.run([str(T.SCRIPTS / "supervisor"), "task", "done", "invoices"], capture_output=True, text=True, env=dict(os.environ, **ENV))
_c27 = _sq27.connect(str(_db27)); _c27.execute("UPDATE results SET at=1789208000 WHERE task='invoices'"); _c27.commit(); _c27.close()
rows_alive("field-notes")
r27 = send_cmd(CMDS[29])
_got27 = json.loads((r27 or {}).get("text") or "{}")
T.check("R27 (1.29) timeline from an epoch → the live session (prompts typed, queued and from the phone) and a closed one with a red and a green test, a commit, an outcome and a task; project relative to the root as in state.sessions; nothing after the instant asked; as in the fixture",
        r27 and r27["ok"] is True and _got27 == json.loads(RES[29]["text"]), json.dumps(_got27, ensure_ascii=False)[:500])
_one27 = json.loads(send_cmd(dict(CMDS[29], id="6f1c2d3e-0190-4000-8000-000000000191", session="field-notes"))["text"])
_bad27 = [send_cmd(dict(CMDS[29], id=f"6f1c2d3e-0190-4000-8000-00000000019{i}", arg=a)) for i, a in ((2, "yesterday"), (3, "8d"))]
T.check("R27 (1.29) one session by its name; a bad arg and more than 7 days → ok false in plain words",
        [x["session"] for x in _one27["sessions"]] == ["field-notes"] and all(b and b["ok"] is False and "expected 90m, 6h, 2d" in b["text"] for b in _bad27), str(_one27)[:200] + str(_bad27))
T.check("R27 (1.29) /state ops carries timeline", "timeline" in json.loads(relay("push", "--dry-run").stdout).get("ops", []), "")
# R28 (contratto 1.31, 04/10): pair_add dal telefono — il relay apre da solo un pair --add e risponde con QR e codice
_dev28 = (rdir2 / "devices.json").read_text(); _all28 = STORE.get("allowed") or {}
(rdir2 / "devices.json").write_text(json.dumps({"u2": {"name": "Pixel 9", "paired_at": 1}, "u3": {"name": "Pixel Watch 5", "paired_at": 1}}))
http("PUT", "/allowed.json", {"u2": True, "u3": True})
_k28 = C.load_key(rdir2)
CMD28 = {"id": "6f1c2d3e-0200-4000-8000-000000000200", "op": "pair_add", "session": None, "arg": None, "issued": 1789210850, "by": "phone-pixel8"}
r28 = send_cmd(CMD28, wait=40)
_inv = json.loads((r28 or {}).get("text") or "{}") if (r28 or {}).get("ok") else {}
_node28 = _inv.get("code") or ""
T.check("R28 (1.31) pair_add → ok, text {qr, code, exp}: a six-digit code whose /pair node carries mode «add», exp in the future; qr null or with m «add»",
        r28 and r28["ok"] is True and re.fullmatch(r"\d{6}", _node28) and ((STORE.get("pair") or {}).get(_node28) or {}).get("mode") == "add"
        and _inv.get("exp", 0) > time.time() and (_inv.get("qr") is None or _inv["qr"].get("m") == "add"), str(r28))
r28b = send_cmd(dict(CMD28, id="6f1c2d3e-0200-4000-8000-000000000201"), wait=40)
T.check("R28 (1.31) a second pair_add while the first is open → ok false «a pairing is already open»", r28b and r28b["ok"] is False and "already open" in r28b["text"], str(r28b))
if _node28:
    _tp, _tpub = C.pair_keys(); _kt = C.shared_key(_tp, STORE["pair"][_node28]["pc_pub"])
    http("PUT", f"/pair/{_node28}/watch.json", {"watch_pub": _tpub, "uid": "u7", "name": "Pixel Tablet", "check": C.check_code(_kt, _node28)})
    T.wait_until(lambda: set((STORE.get("pair") or {}).get(_node28, {})) == {"ok"}, 15)
    _ok28 = (STORE.get("pair") or {}).get(_node28, {}).get("ok") or {}
    T.check("R28 (1.31) the tablet answers the invite → the 1.30 confirmation with the relay's key, the key unchanged, u7 added to u2 and u3",
            _ok28.get("key") and C.decrypt(_ok28["key"], _kt) == {"key": _k28.hex()} and C.load_key(rdir2) == _k28
            and STORE.get("allowed") == {"u2": True, "u3": True, "u7": True}, str(_ok28)[:200] + str(STORE.get("allowed")))
    T.wait_until(lambda: subprocess.run(["pgrep", "-f", "cm-relay.py pair --add"], capture_output=True).returncode != 0, 10)
_u28 = ("u2", "u3", "u7", "u8", "u9", "u10", "u11", "u12")   # 1.39: otto posti
http("PUT", "/allowed.json", {u: True for u in _u28})
(rdir2 / "devices.json").write_text(json.dumps({u: {"name": u, "paired_at": 1} for u in _u28}))
r28c = send_cmd(dict(CMD28, id="6f1c2d3e-0200-4000-8000-000000000202"), wait=40)
T.check("R28 (1.31) already 8 devices (1.39) → ok false «already 8 devices», no pairing started", r28c and r28c["ok"] is False and "already 8 devices" in r28c["text"], str(r28c))
(rdir2 / "devices.json").write_text(_dev28); http("PUT", "/allowed.json", _all28 or {})
T.check("R28 (1.31) /state ops carries pair_add", "pair_add" in json.loads(relay("push", "--dry-run").stdout).get("ops", []), "")
T.check("R14 (1.18) events-sample: a recap, a night_report and the quota resume, with the shape of the other events",
        [e["kind"] for e in EV if e["kind"] in ("recap", "night_report", "quota")][-3:] == ["recap", "night_report", "quota"] and all(ev_ok(e) for e in EV)
        and next(e for e in EV if e["kind"] == "recap")["ref"] == "2026-09-12", str([e["kind"] for e in EV])[:300])
T.check("R34 (1.40) events-sample: restart_failed, a restart that did not succeed (no session, ref = the session's name), with the shape of the other events",
        (_rf := next((e for e in EV if e["kind"] == "restart_failed"), {})) and _rf["session"] is None and _rf["ref"] == "field-notes" and ev_ok(_rf), str(_rf))
_rs = next((e for e in EV if e["kind"] == "relay_stale"), {})
T.check("R38 (1.45) events-sample: relay_stale, no session and no ref, with the shape of the other events",
        _rs and _rs["session"] is None and _rs["ref"] is None and _rs["title"].startswith("⚠") and ev_ok(_rs), str(_rs))
rows_alive("ledger-api", "atlas-shop", "field-notes")
r = relay("push", "--dry-run"); dry11 = json.loads(r.stdout)
p_atlas = next(p_ for p_ in dry11["projects"] if p_["name"] == "atlas-shop")
newest = int(max(f.stat().st_mtime for f in tdir_a.iterdir() if f.suffix == ".jsonl"))
T.check("R11 (1.13) projects[].last_used = the newest transcript of that folder (epoch s); null where there is none",
        p_atlas.get("last_used") == newest and all("last_used" in p_ for p_ in dry11["projects"]) and any(p_["last_used"] is None for p_ in dry11["projects"]), str(dry11["projects"]))
relay("push")   # 08/10: la mappa nome → tmux viene dall'ultima push vera; il --dry-run sopra non la scrive (su WSL, piu' veloce, nessuna push del demone cadeva in mezzo)
ans = dict(CMDS[0], id="6f1c2d3e-0011-4000-8000-000000000101", arg="text:ship it tonight")
res = send_cmd(ans)
T.check("R6 answer text:<text> → `answer work-ledger-api --text <text>`, «answered 3. ship it tonight»", res and res["ok"] is True and res["text"] == "answered 3. ship it tonight" and "answer work-ledger-api --text ship it tonight" in cm_calls(), str(res) + str(cm_calls()[-2:]) + " names=" + str(json.loads((rdir2 / "last-state.json").read_text()).get("names")) + " log=" + "|".join((rdir2 / "relay.log").read_text().splitlines()[-6:]))
res = send_cmd(dict(ans, id="6f1c2d3e-0011-4000-8000-000000000102", arg="chat"))
T.check("R6 answer chat → `answer work-ledger-api --chat`, «answered 4. Chat about this»", res and res["ok"] is True and res["text"] == "answered 4. Chat about this" and "answer work-ledger-api --chat" in cm_calls(), str(res))
n_a = len([c for c in cm_calls() if c.startswith("answer work-ledger-api") and "--show" not in c])
res = send_cmd(dict(ans, id="6f1c2d3e-0011-4000-8000-000000000103", arg="text:  "))
res2 = send_cmd(dict(ans, id="6f1c2d3e-0011-4000-8000-000000000104", arg="maybe"))
T.check("R6 answer «text:» empty → ok false «empty text»; an arg that is neither a number, text: nor chat → ok false, nothing run, the daemon stays up", res and res["ok"] is False and res["text"] == "empty text" and res2 and res2["ok"] is False and res2["text"] == "answer maybe: expected a number, text:<text> or chat" and len([c for c in cm_calls() if c.startswith("answer work-ledger-api") and "--show" not in c]) == n_a and serve_pid() == pid1, str(res) + str(res2))
n_ans = len([c for c in cm_calls() if c == "answer work-ledger-api 1"])
http("PUT", f"/cmd/{CMDS[0]['id']}.json", C.encrypt(CMDS[0], k))
T.wait_until(lambda: CMDS[0]["id"] not in (STORE.get("cmd") or {}), 8)
time.sleep(1)
T.check("R6 the same id again → ignored (no second `answer`), /cmd cleaned", len([c for c in cm_calls() if c == "answer work-ledger-api 1"]) == n_ans and CMDS[0]["id"] not in (STORE.get("cmd") or {}), str(cm_calls()[-3:]))
res = send_cmd({"id": "6f1c2d3e-0009-4000-8000-000000000009", "op": "mode", "session": "atlas-shop", "arg": "plan", "issued": 1, "by": "watch"})
T.check("R6 an op outside the allow-list → ok false «op mode not allowed», nothing run", res and res["ok"] is False and res["text"] == "op mode not allowed", str(res))
http("PUT", "/cmd/garbage.json", {"v": 1, "enc": "bm90aGluZw=="})
T.wait_until(lambda: "garbage" not in (STORE.get("cmd") or {}), 5)
T.check("R6 an undecipherable command is discarded (deleted, no result)", "garbage" not in (STORE.get("cmd") or {}) and "garbage" not in (STORE.get("result") or {}), str(STORE.get("cmd")))
# rete giu': il daemon riconnette e poi serve ancora
fail = tmp / "rtdb-fail"; fail.write_text("x")
os.environ["FAKE_RTDB_FAIL"] = str(fail)
time.sleep(1)
r = relay("status")
fail.unlink()
T.wait_until(lambda: "riconness" in (rdir2 / "relay.log").read_text(), 8)
T.check("R6 RTDB down (503) → the daemon logs the reconnection with backoff and stays alive", "riconnessione" in (rdir2 / "relay.log").read_text() and serve_pid() == pid1, (rdir2 / "relay.log").read_text()[-400:])
os.environ.pop("FAKE_RTDB_FAIL", None)
T.wait_until(lambda: "riconnesso" in (rdir2 / "relay.log").read_text(), 12)
res = send_cmd(dict(CMDS[3], id="6f1c2d3e-0004-4000-8000-0000000000cc"), wait=12)
T.check("R6 after the network is back a command is served again", res and res["ok"], str(res) + (rdir2 / "relay.log").read_text()[-300:])
r = relay("status")
T.check("R6 status: enabled, firebase url, key ok, service account ok, serve alive with the pid, last push time", r.returncode == 0 and "abilitato" in r.stdout and URL in r.stdout and f"VIVO pid {pid1}" in r.stdout and "chiave:           ok" in r.stdout and "service account:  ok" in r.stdout, r.stdout + r.stderr)
r = relay("install")
T.check("R6 install: two cron lines (relay ensure, and the heartbeat as a plain `touch` of push-req/cron: no Python every minute)", r.returncode == 0 and "relay ensure" in cron.read_text() and "touch " + str(rdir2 / "push-req" / "cron") in cron.read_text() and "relay push" not in cron.read_text() and cron.read_text().count("* * * * *") == 2, cron.read_text() + r.stdout)
r = relay("off")
T.check("R6 off: the daemon stops, the cron stays", r.returncode == 0 and T.wait_until(lambda: serve_pid() == 0, 4) and "relay ensure" in cron.read_text(), r.stdout + r.stderr)
relay("ensure")
r = relay("uninstall")
T.check("R6 uninstall: daemon stopped and cron lines removed", r.returncode == 0 and T.wait_until(lambda: serve_pid() == 0, 4) and "supervisor relay" not in cron.read_text(), r.stdout + cron.read_text())
# 0.4.20: senza crontab (o cryptography) install e pair si fermano prima di scrivere o chiedere: esce 5, comando da lanciare
ENV_NOCRON = dict(ENV, CM_CRONTAB_CMD=str(tmp / "no-such-crontab"))
before = cron.read_text()
r = relay("install", env=ENV_NOCRON)
T.check("R6 install without crontab → exit 5, message with `apt install cron`, crontab untouched", r.returncode == 5 and "apt install cron" in r.stderr and cron.read_text() == before, f"rc={r.returncode} " + r.stdout + r.stderr)
r = relay("pair", "--timeout", "2", env=ENV_NOCRON)
T.check("R6 pair without crontab → exit 5 before printing a code, /pair untouched", r.returncode == 5 and "apt install cron" in r.stderr and not re.search(r"\b\d{6}\b", r.stdout), f"rc={r.returncode} " + r.stdout + r.stderr)

# R7: hook e bot chiamano push --async
def hook(ev, payload, env=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True, env=env or ENV, timeout=30)


def state_puts():
    return len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])


n0 = state_puts()
r = hook("PermissionRequest", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "tool_name": "Bash", "tool_input": {"command": "rm -rf build", "description": "clean"}})
wf = json.loads((state_dir / "waiting" / "S-A").read_text())
T.check("R7 PermissionRequest: waiting/<sid> is JSON {tool, input{command, description}}; a push follows (async) → a new PUT of /state", r.returncode == 0 and wf["tool"] == "Bash" and wf["input"]["command"] == "rm -rf build" and T.wait_until(lambda: state_puts() > n0, 15), r.stdout + r.stderr + str(wf))
r = relay("push", "--dry-run")
dry7 = json.loads(r.stdout)
a7 = next(s_ for s_ in dry7["sessions"] if s_["name"] == "atlas-shop")
T.check("R7 …and /state now shows atlas-shop waiting on a permission with tier high (rm -rf) and the question text from the screen", a7["state"] == "waiting" and a7["question"]["kind"] == "permission" and a7["question"]["tier"] == "high", str(a7["question"]))
(state_dir / "waiting" / "S-A").unlink()
r = hook("PermissionRequest", {"session_id": "S-F", "cwd": str(ws / "personal" / "field-notes"), "tool_name": "AskUserQuestion",
                               "tool_input": {"questions": [{"question": "Procedo con il deploy di prova?", "header": "Deploy", "options": [{"label": "Sì", "description": "vai"}, {"label": "No", "description": "aspetta"}]}]}})
wf2 = json.loads((state_dir / "waiting" / "S-F").read_text())
r = relay("push", "--dry-run"); dry7b = json.loads(r.stdout)
f7 = next(s_ for s_ in dry7b["sessions"] if s_["name"] == "field-notes")
T.check("R7b AskUserQuestion before the dialog is on screen (answer --show finds nothing): waiting/<sid> keeps question + option labels from the payload, and /state carries text and options 1-2 with kind ask", wf2["input"]["question"] == "Procedo con il deploy di prova?" and wf2["input"]["options"] == ["Sì", "No"] and f7["state"] == "waiting" and f7["question"]["text"] == "Procedo con il deploy di prova?" and [o["label"] for o in f7["question"]["options"]] == ["Sì", "No"] and f7["question"]["kind"] == "ask", str(wf2) + str(f7["question"]))
(state_dir / "waiting" / "S-F").unlink()
n0 = state_puts()
r = hook("UserPromptSubmit", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "prompt": "vai"})
T.check("R7 UserPromptSubmit: a «prompt» row in the ledger (turn_started), no push", r.returncode == 0 and any(json.loads(l).get("event") == "prompt" and json.loads(l).get("session_id") == "S-A" for l in ledger.read_text().splitlines()) and state_puts() == n0, ledger.read_text()[-200:])
n0 = state_puts()
r = hook("Stop", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "last_assistant_message": "Fatto.\nEsito: seed rivisti.\nWatch: Seed rivisti"})
T.check("R7 Stop: ledger row with watch, then a push", r.returncode == 0 and T.wait_until(lambda: state_puts() > n0, 15) and any(json.loads(l).get("watch") == "Watch: Seed rivisti" for l in ledger.read_text().splitlines()), r.stdout + r.stderr)
n0 = state_puts()
hook("SessionEnd", {"session_id": "S-F", "cwd": str(ws / "personal" / "field-notes"), "reason": "other"})
T.check("R7 SessionEnd → push", T.wait_until(lambda: state_puts() > n0, 15), "")
# PostToolUse (14/09, dall'app): la domanda risposta da tastiera o telefono deve sparire anche dall'orologio in
# background, che si sveglia solo con FCM, cioe' con un evento: il push dopo l'hook ha lo stato senza domanda → answered
# il finto bus scrive STORE da un altro thread mentre il test lo scorre: si scorre una copia (22-23/09: la release
# si e' fermata due volte su «dictionary changed size during iteration»)
def answered_count(name):
    return sum(1 for v in list((STORE.get("events") or {}).values()) if (lambda e: e.get("kind") == "answered" and e.get("session") == name)(C.decrypt(v, k) or {}))


(state_dir / "waiting" / "S-A").write_text(json.dumps({"tool": "Bash", "input": {"command": "make test"}}))
relay("push")
a0, n0 = answered_count("atlas-shop"), state_puts()
r = hook("PostToolUse", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "tool_name": "Bash"})
T.check("R7 PostToolUse with the waiting flag → flag removed, a push, and an «answered» event for the session (FCM wakes the watch)",
        r.returncode == 0 and not (state_dir / "waiting" / "S-A").exists() and T.wait_until(lambda: answered_count("atlas-shop") > a0, 15) and state_puts() > n0, r.stdout + r.stderr)
def settle(rounds=24):
    """Aspetta che il conto delle PUT resti fermo: le push asincrone gia' partite vanno esaurite prima di
    misurare, altrimenti la loro scrittura sembra quella dell'hook."""
    prev = -1
    for _ in range(rounds):
        cur = state_puts()
        if cur == prev:
            return
        prev = cur
        time.sleep(1.5)


settle()
write_cfg(enabled=False)
settle(8)   # e anche dopo lo spegnimento: un figlio partito un attimo prima ha gia' letto la config
n0 = state_puts()
r = hook("Stop", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "last_assistant_message": "x"})
time.sleep(2.5)
T.check("R7 relay.enabled=false: the hook does not push", r.returncode == 0 and state_puts() == n0, "")
write_cfg()
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_RELAY_CM": str(fake_cm)})
n0 = state_puts()
(rdir2 / "awaiting.json").write_text(json.dumps({"atlas-shop": int(time.time())}))
r = relay("push", "--dry-run"); dryaw = json.loads(r.stdout)
a_aw = next(s_ for s_ in dryaw["sessions"] if s_["name"] == "atlas-shop")
T.check("R7b (1.2) a session awaiting a wrist prompt is «awaiting» and its tool is read too (before: only busy ones, so the card had no activity)", a_aw["state"] == "awaiting" and "tool" in a_aw, str((a_aw["state"], a_aw["tool"])))
# la voce scade quando il turno finisce (uno stop dopo il prompt) o dopo awaiting_max_s: senza questo la
# sessione restava «awaiting» per sempre, col turno e il tool di ore prima (dal vivo 14/09 08:22)
sent = time.time() - 60
(rdir2 / "awaiting.json").write_text(json.dumps({"atlas-shop": sent}))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": iso(time.time()), "event": "stop", "session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "account": "personal", "pid": 8, "last": "fine turno", "tail": "fine turno", "esito": ""}) + "\n")
r = relay("push", "--dry-run"); dry_done = json.loads(r.stdout)
a_done = next(s_ for s_ in dry_done["sessions"] if s_["name"] == "atlas-shop")
T.check("R7c the awaiting entry expires when the turn ends (a stop after the prompt): the session is no longer «awaiting» and awaiting.json is cleaned", a_done["state"] != "awaiting" and json.loads((rdir2 / "awaiting.json").read_text()) == {}, str(a_done["state"]) + str((rdir2 / "awaiting.json").read_text()))
(rdir2 / "awaiting.json").write_text(json.dumps({"atlas-shop": time.time() - 3600}))
r = relay("push", "--dry-run"); dry_old = json.loads(r.stdout)
a_old = next(s_ for s_ in dry_old["sessions"] if s_["name"] == "atlas-shop")
T.check("R7c an entry older than relay.awaiting_max_s expires as well", a_old["state"] != "awaiting" and json.loads((rdir2 / "awaiting.json").read_text()) == {}, str(a_old["state"]))
(rdir2 / "awaiting.json").write_text("{}")

# R8 (16/09, passo 3 del ritiro di Telegram): il bus non prende → il polso non riceve, e dopo la soglia l'avviso
# passa da Telegram; quando torna a funzionare la scorta si spegne
write_cfg(telegram_fallback_after_s=1)
(rdir2 / "last-state.json").unlink(missing_ok=True)   # senza stato precedente ogni sessione e' un evento «launched»
(rdir2 / "fallback.json").unlink(missing_ok=True)
TG_CALLS["sendMessage"].clear()
failr = tmp / "rtdb-fail-2"; failr.write_text("x")
os.environ["FAKE_RTDB_FAIL"] = str(failr)   # il finto RTDB gira in QUESTO processo: l'ambiente e' il suo
r = relay("push")
down = json.loads((rdir2 / "fallback.json").read_text()) if (rdir2 / "fallback.json").is_file() else {}
T.check("R8 the bus refuses → fallback.json records since when the watch is not receiving, and the push fails loudly", r.returncode != 0 and float(down.get("since") or 0) > 0, r.stdout + r.stderr + str(down))
time.sleep(1.2)
r = relay("push")
T.check("R8 past relay.telegram_fallback_after_s the notice goes to Telegram, with the minutes and the events", any("ledger-api" in c.get("text", "") for c in TG_CALLS["sendMessage"]), str(TG_CALLS["sendMessage"])[:400])
failr.unlink(); os.environ.pop("FAKE_RTDB_FAIL", None)
r = relay("push")
T.check("R8 the bus works again → fallback.json is gone (the fallback switches off)", r.returncode == 0 and not (rdir2 / "fallback.json").exists(), r.stdout + r.stderr)

# R16 (contratto 1.20, 29/09): il bus funziona ma nessun dispositivo legge → dopo la soglia Telegram. Letto = /seen/<uid>
# scritto dopo l'evento (ora del server, in ms); solo gli uid accoppiati; senza nessun /seen resta il ripiego R8
def unseen_run(seen):
    STORE["seen"] = seen
    (rdir2 / "last-state.json").unlink(missing_ok=True)   # ogni sessione torna «launched»: eventi nuovi
    TG_CALLS["sendMessage"].clear()
    r1 = relay("push")
    time.sleep(1.2)
    r2 = relay("push")
    return r1, r2, [c.get("text", "") for c in TG_CALLS["sendMessage"]]


devs16 = list(json.loads((rdir2 / "devices.json").read_text()))
STORE.pop("seen", None)
r1, r2, tg = unseen_run({})
T.check("R16 (1.20) no device writes /seen (an app that does not know it) → no Telegram, no pending.json: only the R8 fallback applies",
        r1.returncode == 0 and r2.returncode == 0 and tg == [] and not (rdir2 / "pending.json").exists(), str(tg) + r1.stderr[-200:])
r1, r2, tg = unseen_run({devs16[0]: int(time.time() * 1000) + 5000})
T.check("R16 (1.20) a paired device read after the events (server time in ms) → seen, no Telegram, pending.json empty",
        tg == [] and json.loads((rdir2 / "pending.json").read_text()) == [], str(tg))
r1, r2, tg = unseen_run({devs16[0]: int((time.time() - 3600) * 1000), "u-revoked": int(time.time() * 1000)})
T.check("R16 (1.20) no paired device read since the events (an unpaired uid does not count) → past the threshold one Telegram notice with the events, pending emptied",
        len(tg) == 1 and tg[0].startswith("Nessun dispositivo legge gli avvisi da 60 minuti") and "ledger-api" in tg[0] and json.loads((rdir2 / "pending.json").read_text()) == [], str(tg)[:400])
TG_CALLS["sendMessage"].clear()
relay("push")
T.check("R16 (1.20) the same events are not sent twice", TG_CALLS["sendMessage"] == [], str(TG_CALLS["sendMessage"])[:200])
STORE.pop("seen", None)
(rdir2 / "pending.json").unlink(missing_ok=True)


# R9 (contratto 1.11, 16/09): ogni sessione porta modello, effort e contesto, letti dalla sua trascrizione;
# senza trascrizione i tre campi ci sono e sono null — mai stimati
import re as _re9
tdir9 = home / ".claude" / "projects" / _re9.sub(r"[^A-Za-z0-9]", "-", str((ws / "personal" / "atlas-shop").resolve()))
tdir9.mkdir(parents=True, exist_ok=True)
with open(tdir9 / "S-A.jsonl", "w") as f9:
    f9.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-opus-5[1m]", "marketingName": "Opus 5 (1M context)"}}}) + "\n")
    f9.write(json.dumps({"type": "assistant", "effort": "high", "message": {"model": "claude-opus-5", "usage": {"input_tokens": 0, "cache_read_input_tokens": 250_000, "cache_creation_input_tokens": 0}}}) + "\n")
r = relay("push", "--dry-run"); dry9 = json.loads(r.stdout)
a9 = next(s_ for s_ in dry9["sessions"] if s_["name"] == "atlas-shop")
l9 = next(s_ for s_ in dry9["sessions"] if s_["name"] == "ledger-api")
T.check("R9 (1.11) the session with a transcript carries model {id,label}, effort and context (250k of 1M → 25 %)",
        a9["model"] == {"id": "claude-opus-5[1m]", "label": "Opus 5"} and a9["effort"] == "high" and a9["context"] == 25, str({k: a9.get(k) for k in ("model", "effort", "context")}))
T.check("R9 (1.11) a session without a readable transcript has the three fields as null, not missing and not guessed",
        ("model" in l9 and "effort" in l9 and "context" in l9) and l9["model"] is None and l9["effort"] is None and l9["context"] is None, str({k: l9.get(k) for k in ("model", "effort", "context")}))

# R21 (30/09, dal telefono: prompt e risposte non nell'ordine dato): i comandi che arrivano insieme (il put iniziale dopo
# una riconnessione) si eseguono in ordine di `issued`, non di chiave (uuid casuali)
_env21 = dict(os.environ)
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
RL = load("cm-relay")
os.environ.clear(); os.environ.update(_env21)
cmds21 = {"z-first-key": C.encrypt({"op": "prompt", "issued": 1789210903}, k), "a-last-key": C.encrypt({"op": "answer", "issued": 1789210905}, k),
          "m-mid": C.encrypt({"op": "prompt", "issued": 1789210904}, k), "b-plain": {"op": "screen", "issued": 1789210901}}
T.check("R21 commands that arrive together run in the order they were issued, not in key order — the person's commands first, the passive reads (screen) after",
        [c for c, _ in RL.in_order(cmds21)] == ["z-first-key", "m-mid", "a-last-key", "b-plain"], str([c for c, _ in RL.in_order(cmds21)]))
cmds21b = {"t-old": C.encrypt({"op": "transcript", "session": "atlas-shop", "arg": "20", "issued": 1789210910}, k),
           "t-new": C.encrypt({"op": "transcript", "session": "atlas-shop", "arg": "20:after=x", "issued": 1789210914}, k),
           "t-other": C.encrypt({"op": "transcript", "session": "field-notes", "arg": "20", "issued": 1789210911}, k),
           "p-1": C.encrypt({"op": "prompt", "session": "atlas-shop", "arg": "go", "issued": 1789210912}, k)}
_env21 = dict(os.environ); os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
got21 = [c for c, _ in RL.in_order(cmds21b)]
os.environ.clear(); os.environ.update(_env21)
sup = (STORE.get("result") or {}).get("t-old")
T.check("R21 several reads of the same session waiting together → only the newest runs; the older one gets «superseded» at once; the prompt goes first",
        got21 == ["p-1", "t-other", "t-new"] and sup and C.decrypt(sup, k)["ok"] is False and "superseded" in C.decrypt(sup, k)["text"], str(got21) + str(sup)[:80])


# R31 (07/10, piano prestazioni, fase 0): le righe di misura nel log dei test vivi e `relay stats` su un log di esempio
_log31 = (rdir2 / "relay.log").read_text()
_push31 = [l for l in _log31.splitlines() if " push: " in l and " · origine " in l]
T.check("R31 every push writes one measure line: origin, wait, lock, collect, publish, overhead, ts→put, cpu (own + children)",
        _push31 and all(RL.STATS_PUSH.match(l) for l in _push31), (_push31 or ["-"])[-1])
T.check("R31 the debounced push (--async) says where it came from (cron by default, hook from the hooks)",
        any(" · origine cron · " in l or " · origine hook · " in l or " · origine cmd · " in l for l in _push31), str(_push31[-3:]))
_cmd31 = [l for l in _log31.splitlines() if " cmd " in l and " · fila " in l]
T.check("R31 every command line separates the queue from the run (fila, esecuzione); issued, when there, marked as the device clock",
        _cmd31 and all(RL.STATS_CMD.match(l) for l in _cmd31) and any("(orologio del dispositivo)" in l for l in _cmd31), (_cmd31 or ["-"])[-1])
_log31b = tmp / "stats31" / "relay.log"; _log31b.parent.mkdir(parents=True, exist_ok=True)
_log31b.write_text(
    "2026-10-07T16:20:00 push: 6 sessioni, 0 eventi · origine cron · attesa 2,0 s · lock 0,0 s · raccolta 8,0 s · pubblicazione 1,0 s · contorno 1,0 s · ts→put 8,0 s · cpu 3,0+4,0 s\n"
    "2026-10-07T16:20:30 push: 6 sessioni, 1 eventi · origine hook · attesa 16,5 s · lock 14,5 s · raccolta 10,0 s · pubblicazione 1,5 s · contorno 0,5 s · ts→put 10,0 s · cpu 5,0+6,0 s\n"
    "2026-10-07T16:21:00 cmd a1: prompt master da Pixel → ok delivered · fila 12,0 s · esecuzione 1,0 s · issued 13,0 s (orologio del dispositivo)\n"
    "2026-10-07T16:21:01 cmd a1: esito scritto · prompt · dall'arrivo 13,4 s · put 0,4 s\n"
    "2026-10-07T16:21:05 arrivo: abc123 2,5 s dopo la push\n"
    "2026-10-07T17:00:00 push: 6 sessioni, 0 eventi · origine cron · attesa 2,0 s · lock 0,0 s · raccolta 5,0 s · pubblicazione 1,0 s · contorno 0,0 s · ts→put 5,0 s · cpu 2,0+2,0 s\n")
_cfg31 = json.loads(cfg.read_text()); _cfg31.setdefault("relay", {})["log"] = str(_log31b)
_cfg31p = tmp / "config31.json"; _cfg31p.write_text(json.dumps(_cfg31))
_r31 = relay("stats", "--since", "2026-10-07T16:00", "--until", "2026-10-07T16:30", env={**os.environ, "CC_SUPERVISOR_CONFIG": str(_cfg31p)})
_o31 = _r31.stdout
T.check("R31 relay stats: the window keeps two pushes (16:20-16:30), duration = lock + collect + publish + overhead (10,0 and 26,5), lock max 14,5, cpu 7,0/11,0",
        _r31.returncode == 0 and "n     2" in _o31 and "max    26.5" in _o31 and "max    14.5" in _o31 and "max    11.0" in _o31 and "origine: cron 1, hook 1" in _o31, _o31 + _r31.stderr)
T.check("R31 relay stats: a prompt's queue, run and arrival-to-result; the state's age at arrival from /seen",
        "prompt: fila s" in _o31 and "max    12.0" in _o31 and "prompt: dall'arrivo all'esito s" in _o31 and "max    13.4" in _o31 and "max     2.5" in _o31, _o31)
_old31 = RL.LOG_MAX_BYTES
try:
    RL.LOG_MAX_BYTES = 10
    _rl31 = tmp / "rot31" / "relay.log"; _rl31.parent.mkdir(parents=True, exist_ok=True); _rl31.write_text("x" * 50 + "\n")
    _R31 = dict(RL.R); RL.R["log"] = str(_rl31)
    RL.log("riga nuova")
finally:
    RL.LOG_MAX_BYTES = _old31; RL.R.clear(); RL.R.update(_R31)
T.check("R31 the log rotates past LOG_MAX_BYTES: the old lines go to relay.log.1, the new line starts relay.log",
        _rl31.with_name("relay.log.1").read_text().startswith("x" * 50) and _rl31.read_text().endswith("riga nuova\n"), "")


# R35 (08/10, dall'app: una risposta di 10.721 caratteri arrivava tagliata a 4000, a meta' parola)
CO35 = load("cm-core")
_w35 = ("parola " * 4000).strip()   # 27.999 caratteri
_t35, _c35 = CO35.cut_text(_w35, CO35.ENTRY_TEXT_MAX)
_e35 = CO35._entry("x.0", "assistant", "a" * 10721, 1)
T.check("R35 an entry of 10,721 characters arrives whole (the cap is 20,000), cut false",
        CO35.ENTRY_TEXT_MAX == 20000 and len(_e35["text"]) == 10721 and _e35["cut"] is False, str(len(_e35["text"])))
T.check("R35 over the cap the text stops at the last whole word, cut true",
        _c35 is True and len(_t35) <= 20000 and _t35.endswith("parola") and _w35.startswith(_t35), _t35[-20:])
_b35, _bc35 = CO35.cut_text("è" * 25000, CO35.ENTRY_TEXT_MAX)
T.check("R35 never over ENTRY_BYTES_MAX in UTF-8, so one entry alone always fits a 60 KB page",
        _bc35 is True and len(_b35.encode()) <= CO35.ENTRY_BYTES_MAX, str(len(_b35.encode())))


# R36 (08/10, piano prestazioni, fase 1): un worker per le push, il ripiego che non accoda, il lavoro dopo il lock
import fcntl as _f36
import threading as _th36
_push36, _R36, _retry36, _push_fn36 = RL._push, dict(RL.R), RL.PUSH_RETRY_S, RL.push
_calls36 = []
try:
    RL.R["debounce_s"] = 0.2
    def _slow36(origin="?", requested=None, **kw):
        _calls36.append(origin); time.sleep(1.2)
    RL.push = _slow36
    for _f in RL.push_req_dir().iterdir():
        _f.unlink()
    _w36 = RL.PushWorker(); _w36.start()
    RL.push_request("hook")
    T.wait_until(lambda: len(_calls36) == 1, 5)
    for _i in range(10):   # dieci richieste durante la push lenta
        RL.push_request("cron" if _i % 2 else "hook"); time.sleep(0.05)
    time.sleep(3.5)
    _n36 = len(_calls36)
    _w36.stop.set(); _w36.join(2)
    T.check("R36 ten requests during a slow push → exactly one more push (one running, at most one waiting)", _n36 == 2, str(_calls36))
    _calls36.clear()
    _fail36 = [True]
    def _flaky36(origin="?", requested=None, **kw):
        _calls36.append(origin)
        if _fail36[0]:
            _fail36[0] = False; raise OSError("bus giu'")
    RL.push, RL.PUSH_RETRY_S = _flaky36, (0.2,)
    _w36 = RL.PushWorker(); _w36.start()
    RL.push_request("cmd")
    T.wait_until(lambda: len(_calls36) >= 2, 5); time.sleep(0.6)
    _w36.stop.set(); _w36.join(2)
    T.check("R36 a failed push is retried until it goes through, then the worker rests (no request lost, no loop)", len(_calls36) == 2, str(_calls36))
finally:
    RL.push, RL.PUSH_RETRY_S = _push_fn36, _retry36
    RL.R.clear(); RL.R.update(_R36)
_ran36 = []
RL._push = lambda dry_run=False, now=None, m=None: _ran36.append(1)
try:
    (RL.rdir() / "push-request").write_text("2.000000")
    _got36 = RL.push(origin="hook", stamp="1.000000")
finally:
    RL._push = _push36
T.check("R36 without the daemon a queued push that finds a newer request after the lock leaves without collecting", _got36 is None and not _ran36, str(_ran36))
_post36 = []
def _fake36(dry_run=False, now=None, m=None):
    t = time.time()
    m.update(ts=t, collected=t, put_at=t, published=t, events=0)
    def post():
        with open(str(RL.rdir() / "push.lock"), "w") as _l:
            try:
                _f36.flock(_l, _f36.LOCK_EX | _f36.LOCK_NB); _post36.append("free"); _f36.flock(_l, _f36.LOCK_UN)
            except OSError:
                _post36.append("held")
    m["post"] = post
    return {"sessions": []}
RL._push = _fake36
try:
    RL.push(origin="cmd")
finally:
    RL._push = _push36
T.check("R36 FCM, notices and prunes run after the push lock is released", _post36 == ["free"], str(_post36))
(RL.rdir() / "prune-at").unlink(missing_ok=True)
T.check("R36 prunes at most once every 10 minutes", RL.prune_due(time.time()) is True and RL.prune_due(time.time()) is False, "")
T.check("R36 (1.43) the published state carries published_at, at or after ts", _pub4.get("published_at") and float(_pub4["published_at"]) >= float(_pub4["ts"]), str({x: _pub4.get(x) for x in ("ts", "published_at")}))
_hk36 = rdir2 / "push-req" / "hook"
_t36 = time.time() - 1
_r36 = hook("UserPromptSubmit", {"session_id": "sid-36", "cwd": str(home), "prompt": "ciao"})
T.check("R36 UserPromptSubmit asks for a push (push-req/hook touched)", _r36.returncode == 0 and _hk36.exists() and _hk36.stat().st_mtime >= _t36, _r36.stderr[-300:])
OF36 = load("cm-offload")
T.check("R36 heavy run on this machine runs at low priority: nice -n 10 and ionice -c3 around the command",
        OF36.low_priority("make test") == ("nice -n 10 " + ("ionice -c3 " if shutil.which("ionice") else "") + "sh -c 'make test'"), OF36.low_priority("make test"))


# R37 (1.44, 08/10): op night, il rapporto della notte cosi' com'e', e night.report nello stato
_nr37 = tmp / "night37"; _nr37.mkdir(exist_ok=True)
_dir37 = RL.night_report_dir
RL.night_report_dir = lambda: _nr37
try:
    _empty37 = RL.night_report(None)
    _sample37 = (FIX / "night-report-sample.json").read_text()
    (_nr37 / "2026-10-07.json").write_text(_sample37)
    _ok37 = RL.night_report(None)
    _no37 = RL.night_report("2026-10-01")
    _day37 = RL.night_report("2026-10-07")
    _latest37 = RL.night_report_latest()
finally:
    RL.night_report_dir = _dir37
_crs37 = json.loads((FIX / "cmd-result-sample.json").read_text())
_res37 = {r["id"]: r for r in _crs37["result"] if isinstance(r, dict)}
T.check("R37 (1.44) night with no report → «no night report yet»", _empty37 == (False, "no night report yet"), str(_empty37))
T.check("R37 (1.44) night null → the last report as it is on disk; the same JSON as the fixture's …0383 result",
        _ok37[0] is True and json.loads(_ok37[1]) == json.loads(_sample37) == json.loads(_res37["6f1c2d3e-0383-4000-8000-000000000383"]["text"]), str(_ok37)[:120])
T.check("R37 (1.44) night for a day without a report → «no night report for 2026-10-01», as the fixture's …0384",
        _no37 == (False, _res37["6f1c2d3e-0384-4000-8000-000000000384"]["text"]) and _day37[0] is True, str(_no37))
T.check("R37 (1.44) night is a passive read (it wakes nobody, it never triggers a push) and is in ops", "night" in RL.PASSIVE_OPS and "night" in RL.OPS, "")
_st37 = S.build_state({"night_report": _latest37}, 1)
T.check("R37 (1.44) /state night.report = {date, generated_at} of the last report; absent without reports",
        _st37["night"].get("report") == {"date": "2026-10-07", "generated_at": json.loads(_sample37)["generated_at"]} and "report" not in S.build_state({}, 1)["night"], str(_st37["night"]))


# R38 (08/10, dopo lo stato fermo dalle 10:47 alle 11:49): ensure riavvia un demone con il codice vecchio; la guardia
import fcntl as _f38
_save38 = {n: getattr(RL, n) for n in ("serve_alive", "serve_stop", "push", "emit", "_load")}
_calls38 = {"stop": 0, "push": [], "emit": [], "tg": []}
class _Bot38:
    def send(self, text, watch_quiet=False):
        _calls38["tg"].append(text)
_popen38 = RL.subprocess.Popen
_rdir38 = RL.rdir
_d38 = tmp / "relay38"; _d38.mkdir(exist_ok=True)
try:
    RL.rdir = lambda: _d38   # una cartella sua: il relay di prova di R6/R30 e' ancora vivo e pubblica in rdir2
    RL.serve_alive = lambda: 0 if _calls38["stop"] else 4242
    def _stop38():
        _calls38["stop"] += 1; return True
    RL.serve_stop = _stop38
    RL.subprocess.Popen = lambda *a, **kw: None
    RL.write_json(RL.serve_status_path(), {"pid": 4242, "code": "cm-relay.py:1:1"})
    RL.guard_path().unlink(missing_ok=True)
    RL.ensure()
    _g38 = RL.read_json(RL.guard_path(), {})
    T.check("R38 ensure: the live daemon runs older code than the disk → stopped and started again, «codice cambiato» in the log",
            _calls38["stop"] == 1 and _g38.get("cause") == "code" and "codice cambiato" in RL.log_path().read_text(), str(_g38))
    RL.push = lambda **kw: _calls38["push"].append(kw.get("origin"))
    RL.emit = lambda kind, title, body, **kw: _calls38["emit"].append((kind, title, body))
    RL._load = lambda name: _Bot38() if name == "cm-bot" else _save38["_load"](name)
    RL.guard_path().unlink(missing_ok=True)
    _now38 = time.time()
    RL.write_json(RL.rdir() / "last-state.json", {"state": {"published_at": _now38 - 400}, "pushed_at": _now38 - 412})
    RL.guard(_now38)
    RL.guard(_now38 + 60)
    T.check("R38 guard: the state older than 180 s → a direct push (origin «guardia»), Telegram told once, not every minute",
            _calls38["push"] == ["guardia", "guardia"] and len(_calls38["tg"]) == 1 and "fermo" in _calls38["tg"][0], str(_calls38))
    RL.write_json(RL.rdir() / "last-state.json", {"state": {"published_at": _now38 + 100}})
    RL.guard(_now38 + 120)
    T.check("R38 guard: when the state moves again → one event relay_stale (1.45) with since when and why; the episode closed",
            len(_calls38["emit"]) == 1 and _calls38["emit"][0][0] == "relay_stale" and "8 min" in _calls38["emit"][0][1]
            and "push diretta" in _calls38["emit"][0][2] and not RL.guard_path().exists(), str(_calls38["emit"]))
    _calls38["push"].clear(); _calls38["tg"].clear()
    RL.write_json(RL.rdir() / "last-state.json", {"state": {"published_at": _now38 - 400}})
    RL.write_json(RL.guard_path(), {"since": _now38 - 400, "cause": "push", "lock_since": _now38 - 300})
    with open(str(RL.rdir() / "push.lock"), "w") as _l38:
        _f38.flock(_l38, _f38.LOCK_EX)
        RL.guard(_now38)
        _f38.flock(_l38, _f38.LOCK_UN)
    _g38 = RL.read_json(RL.guard_path(), {})
    T.check("R38 guard: the push lock held for more than 180 s → no waiting and no push, cause «lock», said once",
            not _calls38["push"] and _g38.get("cause") == "lock" and len(_calls38["tg"]) == 1 and "lock" in _calls38["tg"][0], str(_g38) + str(_calls38["tg"]))
finally:
    for _n, _v in _save38.items():
        setattr(RL, _n, _v)
    RL.subprocess.Popen = _popen38
    RL.guard_path().unlink(missing_ok=True)
    RL.rdir = _rdir38


# R39 (08/10, battito leggero): il battito del cron ripubblica lo stato pronto se le sorgenti non sono cambiate
_save39 = {n: getattr(RL, n) for n in ("rdir", "rtdb", "key", "_push", "push")}
_d39 = tmp / "relay39"; _d39.mkdir(exist_ok=True)
_puts39, _full39 = [], []
try:
    RL.rdir = lambda: _d39
    RL.key = lambda: k
    RL.rtdb = lambda method, path, body=None, params=None: _puts39.append((method, path, body))
    def _fullpush39(dry_run=False, now=None, m=None):
        _full39.append(1); t = time.time()
        m.update(ts=t, collected=t, put_at=t, published=t, events=0)
        return {"sessions": []}
    RL._push = _fullpush39
    def _setup39(state="idle", age=10):
        st = {"v": 1, "ts": time.time() - age, "sessions": [{"name": "x", "state": state}]}
        RL.write_json(_d39 / "last-state.json", {"state": st, "full": dict(st), "seq": 3})
        RL.write_json(RL.beat_path(), {"fp": RL.sources_print(), "full_at": time.time() - age})
    _setup39()
    _t39 = time.time()
    RL.push(origin="cron", light=True)
    _pub39 = C.decrypt(_puts39[-1][2], k) if _puts39 else {}
    T.check("R39 heartbeat with nothing changed: no collect, the ready state put again with a new published_at, origin «battito» in the log",
            not _full39 and _puts39 and _puts39[-1][1] == "state" and float(_pub39.get("published_at") or 0) >= _t39 - 0.01
            and "origine battito" in RL.log_path().read_text(), str(_full39) + str(_pub39)[:120])
    _setup39()
    (Path(cm_state_dir39 := RL.cm.expand(RL.CFG["state_dir"])) / "waiting").mkdir(parents=True, exist_ok=True)
    (Path(cm_state_dir39) / "waiting" / "sid-39").write_text("{}")
    RL.push(origin="cron", light=True)
    (Path(cm_state_dir39) / "waiting" / "sid-39").unlink()
    T.check("R39 a source changed (a question waiting) → the full collect", len(_full39) == 1, str(_full39))
    _setup39(age=400)
    RL.push(origin="cron", light=True)
    T.check("R39 the last full collect older than 5 minutes → the full collect anyway", len(_full39) == 2, str(_full39))
    _setup39(state="busy", age=150)
    RL.push(origin="cron", light=True)
    T.check("R39 a session at work and the last collect older than 2 minutes → the full collect", len(_full39) == 3, str(_full39))
    _setup39(state="busy", age=60)
    RL.push(origin="cron", light=True)
    T.check("R39 a session at work but a collect 1 minute ago → the light heartbeat", len(_full39) == 3, str(_full39))
    _setup39()
    with open(Path(cm_state_dir39) / "ledger.jsonl", "a") as _lf39:
        _lf39.write('{"event": "watch-cmd", "op": "transcript"}\n')
    RL.push(origin="cron", light=True)
    T.check("R39 a new ledger line alone (a chat read) does not force the full collect: the state changes come with a hook", len(_full39) == 3, str(_full39))
    _light39 = []
    RL.push = lambda **kw: _light39.append(kw.get("light"))
    for _f in RL.push_req_dir().iterdir():
        _f.unlink()
    _save_deb39 = RL.R.get("debounce_s"); RL.R["debounce_s"] = 0.1
    _w39 = RL.PushWorker(); _w39.start()
    RL.push_request("cron"); T.wait_until(lambda: len(_light39) == 1, 5)
    RL.push_request("hook"); RL.push_request("cron"); T.wait_until(lambda: len(_light39) == 2, 5)
    _w39.stop.set(); _w39.join(2)
    RL.R["debounce_s"] = _save_deb39
    T.check("R39 the worker tries the light heartbeat only when the cron alone asked (a hook in between → full push)", _light39 == [True, False], str(_light39))
finally:
    for _n, _v in _save39.items():
        setattr(RL, _n, _v)

# R40 (1.46, 08/10): op agenda, the open decisions and work of relay.agenda_file (TSV), on request, never in /state;
# 1.47 (09/10): columns 6 and 7 (detail, until), the state «scartato», «chiuso» read as «fatto», and each row's key
_AG40 = ("# Decisions and open work\n#\n# state\tscope\tblocks\ttitle\tref\tdetail\tuntil\n"
         "aperto\tagenzia\towner\tConfirm the 6 client ids with a candidate\t.claude/to-decide-client-id.md\n\n"
         "aperto\tpersonale\tclaude\tMove the docs site to the new host\torbit-docs\r\n"
         "  # an indented comment\n"
         "sospeso\tagenzia\tterzi\tStaging of atlas-shop not reachable from the CLI\t\tThe host blocks SSH from the office.\\nAsk the provider to open port 22.\t2026-10-12\n"
         "chiuso\tpostazione\tnessuno\tBackups of the workstation every night\tcrontab\n"
         "scartato\tpersonale\tnessuno\tA second dashboard for the quotas\t\t\tnot-a-date\n")
_ag40 = tmp / "agenda40.tsv"
_ag40.write_bytes(_AG40.encode())
_rel40 = RL.CFG.setdefault("relay", {})
_saved40 = _rel40.get("agenda_file")
try:
    _rel40["agenda_file"] = str(_ag40)
    _ok40 = RL.agenda_list()
    _rel40["agenda_file"] = ""
    _none40 = RL.agenda_list()
    _rel40["agenda_file"] = str(tmp / "missing-agenda.tsv")
    _gone40 = RL.agenda_list()
finally:
    _rel40["agenda_file"] = _saved40
_res40 = {r["id"]: r for r in json.loads((FIX / "cmd-result-sample.json").read_text())["result"] if isinstance(r, dict)}
_cmd40 = {c["id"]: c for c in json.loads((FIX / "cmd-result-sample.json").read_text())["cmd"] if isinstance(c, dict)}
T.check("R40 (1.47) agenda → {rows: [{key, state, scope, blocks, title, ref, detail, until}], more} in file order, comments and blank lines out, "
        "detail with real line breaks, until only as a date, «chiuso» as «fatto»; the fixture's …0386 result",
        _ok40[0] is True and json.loads(_ok40[1]) == json.loads(_res40["6f1c2d3e-0386-4000-8000-000000000386"]["text"]), str(_ok40)[:300])
T.check("R40 (1.46) no agenda_file, or a file that is not there → «no agenda file», as the fixture's …0387",
        _none40 == _gone40 == (False, _res40["6f1c2d3e-0387-4000-8000-000000000387"]["text"]), str((_none40, _gone40)))
T.check("R40 (1.46) agenda is a passive read in ops, and /state does not carry it", "agenda" in RL.PASSIVE_OPS and "agenda" in RL.OPS
        and "agenda" not in json.loads(relay("push", "--dry-run").stdout), "")

# R41 (1.47, 09/10): op agenda_set — Fatto, Rimanda, Rimuovi, Passa on a copy of the agenda; backup, lock, nothing else moves
_ag41 = tmp / "agenda41.tsv"
_ag41.write_bytes(_AG40.encode())
_rel41 = RL.CFG.setdefault("relay", {})
_saved41 = (_rel41.get("agenda_file"), RL.AGENDA_BACKUPS, RL.AGENDA_LOCK_S)
_bk41 = RL.rdir() / "agenda-backup"
shutil.rmtree(_bk41, ignore_errors=True)
try:
    _rel41["agenda_file"] = str(_ag41)
    _keys41 = [r["key"] for r in json.loads(RL.agenda_list()[1])["rows"]]
    _out41 = {}
    for _cid in sorted(c for c in _cmd40 if _cmd40[c]["op"] == "agenda_set"):
        _before = _ag41.read_bytes()
        _out41[_cid] = (RL.execute(_cmd40[_cid]), _before, _ag41.read_bytes())
    _lines41 = _ag41.read_bytes().decode().splitlines(keepends=True)
    _orig41 = _AG40.splitlines(keepends=True)
    _bks41 = sorted(b.read_bytes() for b in _bk41.glob("agenda-*.tsv"))
    # one writer at a time: with the lock held elsewhere, «agenda busy» and the file untouched
    _held = open(str(RL.rdir() / "agenda.lock"), "w")
    fcntl.flock(_held, fcntl.LOCK_EX)
    RL.AGENDA_LOCK_S = 0.3
    _k_now = json.loads(RL.agenda_list()[1])["rows"][0]["key"]
    _busy41 = (RL.agenda_set({"action": "done"}, _k_now), _ag41.read_bytes() == "".join(_lines41).encode())
    fcntl.flock(_held, fcntl.LOCK_UN); _held.close()
    RL.AGENDA_BACKUPS = 2
    _newest41 = _ag41.read_bytes()
    RL.agenda_set({"action": "pass", "blocks": "owner"}, _k_now)
    _left41 = sorted(_bk41.glob("agenda-*.tsv"))
    _capped41 = (len(_left41), _left41[-1].read_bytes() == _newest41)
    # the owner's name comes from relay.agenda_owner: Passa takes it, and «owner» is then refused
    _rel41["agenda_owner"] = "someone"
    _k_now = json.loads(RL.agenda_list()[1])["rows"][1]["key"]
    _owner41 = (json.loads(RL.agenda_list()[1])["owner"], RL.agenda_set({"action": "pass", "blocks": "owner"}, _k_now),
                json.loads(RL.agenda_set({"action": "pass", "blocks": "someone"}, _k_now)[1])["row"]["blocks"])
finally:
    _rel41["agenda_file"], RL.AGENDA_BACKUPS, RL.AGENDA_LOCK_S = _saved41
    _rel41.pop("agenda_owner", None)
_okids41 = [c for c in sorted(_out41) if _res40[c]["ok"]]
T.check("R41 (1.47) the keys of agenda are the ones the fixture's agenda_set commands use",
        all(_cmd40[c]["arg"] in _keys41 for c in _okids41), str((_keys41, [_cmd40[c]["arg"] for c in _okids41])))
T.check("R41 (1.47) agenda_set: every fixture command gives the fixture's result (…0470-…0477)",
        all(_out41[c][0] == (_res40[c]["ok"], _res40[c]["text"]) for c in _out41) and len(_out41) == 8,
        str({c[-4:]: _out41[c][0] for c in _out41})[:600])
T.check("R41 (1.47) done → state fatto, snooze → sospeso with until in column 7 (padded), remove → scartato, pass → column 3; "
        "only those lines change, comments, blank lines and the CRLF line end stay",
        _lines41[3] == "fatto\tagenzia\towner\tConfirm the 6 client ids with a candidate\t.claude/to-decide-client-id.md\n"
        and _lines41[5] == "sospeso\tpersonale\tclaude\tMove the docs site to the new host\torbit-docs\t\t2026-10-20\r\n"
        and _lines41[7] == "sospeso\tagenzia\tclaude\tStaging of atlas-shop not reachable from the CLI\t\tThe host blocks SSH from the office.\\nAsk the provider to open port 22.\t2026-10-12\n"
        and _lines41[9] == "scartato\tpersonale\tnessuno\tA second dashboard for the quotas\t\t\tnot-a-date\n"
        and _lines41[8] == "scartato\tpostazione\tnessuno\tBackups of the workstation every night\tcrontab\n"
        and [l for i, l in enumerate(_lines41) if i not in (3, 5, 7, 8)] == [l for i, l in enumerate(_orig41) if i not in (3, 5, 7, 8)],
        "".join(_lines41))
T.check("R41 (1.47) a refused command (stale key, bad action, bad date, bad blocks) leaves the file as it was",
        all(_out41[c][1] == _out41[c][2] for c in _out41 if not _res40[c]["ok"]), "")
T.check("R41 (1.47) a backup before each write, byte for byte the file as it was",
        len(_bks41) == len(_okids41) and _bks41 == sorted(_out41[c][1] for c in _okids41), str(len(_bks41)))
T.check("R41 (1.47) with the lock held elsewhere → «agenda busy», file untouched; backups capped at AGENDA_BACKUPS, the newest kept",
        _busy41 == ((False, "agenda busy"), True) and _capped41 == (2, True), str((_busy41, _capped41)))
T.check("R41 (1.47) relay.agenda_owner names the owner: agenda says it in `owner`, Passa writes it, «owner» is then refused",
        _owner41 == ("someone", (False, "bad blocks: owner"), "someone"), str(_owner41))
T.check("R41 (1.47) agenda_set is a command in ops, not a passive read", "agenda_set" in RL.OPS and "agenda_set" not in RL.PASSIVE_OPS, "")

# R44 (14/10, gli spunti della valutazione di Kaya, «Passa tutti e 7»): contratti 1.53-1.58
# 1.53 opzioni spiegate
o44 = [S.option_of(i, lb, {"Merge now (Recommended)": "Fast-forward main and push", "Wait": ""}) for i, lb in enumerate(["Merge now (Recommended)", "Wait"])]
T.check("R44 (1.53) an option keeps its description and «(Recommended)» becomes recommended: true with a clean label; without them the two fields are absent",
        o44 == [{"n": 1, "label": "Merge now", "description": "Fast-forward main and push", "recommended": True}, {"n": 2, "label": "Wait"}], str(o44))
# 1.54 modalita' dei permessi
t44 = tmp / "t44.jsonl"
t44.write_text(json.dumps({"type": "user", "permissionMode": "default", "message": {"role": "user", "content": "x"}}) + "\n"
               + json.dumps({"type": "permission-mode", "permissionMode": "acceptEdits", "sessionId": "S"}) + "\n")
_tof = RL.core.transcript_of
RL.core.transcript_of = lambda row: str(t44) if row.get("session_id") == "S" else ""
pm44 = (RL.core.permission_mode({"session_id": "S"}), RL.core.permission_mode({"session_id": "none"}))
RL.core.transcript_of = _tof
st44 = S.build_session({"name": "x", "tmux": "x", "status": "idle", "session_id": "S"}, {"runtime": {"x": {"permission_mode": "acceptEdits"}}})
st44b = S.build_session({"name": "x", "tmux": "x", "status": "idle", "session_id": "S"}, {})
T.check("R44 (1.54) permission_mode: the last value in the transcript's tail (acceptEdits after default), None without a transcript; in the state only when known",
        pm44 == ("acceptEdits", None) and st44.get("permission_mode") == "acceptEdits" and "permission_mode" not in st44b, str(pm44) + str(st44.get("permission_mode")))
# 1.55 risolta al terminale
r44 = tmp / "r44"
r44.mkdir()
_rd, _ls, _pf = RL.rdir, RL.last_sessions, RL.prefixes
RL.rdir, RL.prefixes = (lambda: r44), (lambda: ())
RL.last_sessions = lambda: {"alfa": {"question": {"id": "q-100-1", "text": "Deploy now?"}}, "beta": {"question": {"id": "q-200-1", "text": "Push?"}},
                            "gamma": {"question": {"id": "q-300-1", "text": "Old?"}}}
(r44 / "answered.json").write_text(json.dumps({"q-200-1": 1789210000}))
rows44 = [{"name": "alfa", "status": "idle"}, {"name": "beta", "status": "idle"}, {"name": "gamma", "status": "waiting"}]
res44 = RL.resolved_of({"gamma": {"asked_at": 300}}, rows44, 1789211000)
later44 = RL.resolved_of({}, [{"name": "beta", "status": "idle"}], 1789211000 + RL.RESOLVED_KEEP_S + 1)
RL.rdir, RL.last_sessions, RL.prefixes = _rd, _ls, _pf
s44 = S.build_session({"name": "alfa", "tmux": "alfa", "status": "idle"}, {"resolved": res44})
T.check("R44 (1.55) a question gone without an answer from a device → resolved {id, text, at}; one answered from the phone or still open is not; after RESOLVED_KEEP_S it leaves; in the state with by «terminal»",
        res44 == {"alfa": {"id": "q-100-1", "text": "Deploy now?", "at": 1789211000}} and later44 == {}
        and s44.get("resolved") == {"id": "q-100-1", "text": "Deploy now?", "at": 1789211000, "by": "terminal"}, str(res44) + str(later44))
# 1.56 anteprime senza segreti
red44 = [S.redact_preview(x) for x in ("curl -H 'Authorization: Bearer abcdefghijklmnop123' https://x", "export API_KEY=sk-abcdefghijklmnopqrstuv",
                                         "mysql --password hunter22 db", "git push https://me:pw1234@github.com/a/b", "git log --oneline -3")]
T.check("R44 (1.56) redact_preview hides a bearer token, a key in a variable, an option's password and the password in a URL; a plain command is untouched",
        red44 == ["curl -H 'Authorization: Bearer •••' https://x", "export API_KEY=•••", "mysql --password ••• db", "git push https://•••@github.com/a/b", "git log --oneline -3"], str(red44))
q44 = S.build_session({"name": "x", "tmux": "x", "status": "waiting", "waiting": True},
                      {"questions": {"x": {"tool": "Bash", "text": "Bash export TOKEN=abcdef123456", "options": ["Yes", "No"], "asked_at": 1789210000}}})["question"]
T.check("R44 (1.56) a question with a secret carries preview (redacted), text stays whole; an event gets preview only when something was hidden",
        q44["preview"] == "Bash export TOKEN=•••" and q44["text"] == "Bash export TOKEN=abcdef123456"
        and S.preview_of("▶ x", "work/x") == {} and S.preview_of("? x", "token=abcdef1234") == {"preview": {"title": "? x", "body": "token=•••"}}, str(q44))
# 2 prompt doppioni
d44 = tmp / "d44"
d44.mkdir()
_rd, _win = RL.rdir, RL.R.get("duplicate_prompt_s")
RL.rdir = lambda: d44
RL.R["duplicate_prompt_s"] = 60
first = RL.duplicate_prompt("alfa", "fai  il deploy")
RL.duplicate_prompt("alfa", "fai il deploy", record=True)
again, other = RL.duplicate_prompt("alfa", "fai il deploy"), RL.duplicate_prompt("beta", "fai il deploy")
RL.R["duplicate_prompt_s"] = 0
off = RL.duplicate_prompt("alfa", "fai il deploy")
RL.rdir = _rd
RL.R["duplicate_prompt_s"] = _win
T.check("R44 (14/10) the same text to the same session within 60 s after a delivery → seconds since (not sent again); before the delivery, to another session or with the window at 0 → None",
        first is None and isinstance(again, int) and again >= 1 and other is None and off is None, str((first, again, other, off)))
# 1.58 quota: le tre strade
q58 = tmp / "q58"
q58.mkdir()
_rd, _jc, _cfgsd = RL.rdir, RL._json_cmd, RL.CFG["state_dir"]
RL.rdir = lambda: q58
RL.CFG["state_dir"] = str(q58)
RL._json_cmd = lambda *a, **kw: ([{"name": "work-ledger-api", "tmux": "work-ledger-api", "account": "work", "cwd": str(ws), "status": "idle"}]
                                 if a[0] == "sessions" else {"work": {"cinque_ore_pct": 100, "settimana_pct": 40, "reset_cinque_ore": 1789220000, "reset_settimanale": 1789600000}})
ok58 = RL.quota_route({}, "ledger-api", "work-ledger-api", "at_reset")
g58 = json.loads((q58 / "guard.json").read_text())
bad58 = RL.quota_route({}, "ledger-api", "work-ledger-api", "later")
win58 = RL.quota_window("work")
RL.rdir, RL._json_cmd = _rd, _jc
RL.CFG["state_dir"] = _cfgsd
T.check("R44 (1.58) quota_route at_reset → the guard resumes this session at the 5-hour reset (guard.json resume_at, resume_names), said with the time; an unknown route → the contract's text; other_account picks the other configured account",
        ok58[0] is True and g58["work"]["resume_at"] == 1789220000 and g58["work"]["resume_names"] == ["work-ledger-api"] and win58 == ("5h", 1789220000)
        and bad58 == (False, "quota_route later: expected other_account, at_reset or pass:<session>") and RL.other_account("work") == "personal", str(ok58) + str(g58) + str(bad58))
T.check("R44 (1.58) quota_route is a command in ops", "quota_route" in RL.OPS and "quota_route" not in RL.PASSIVE_OPS, "")

# R45 (contratto 1.59, 14/10, il manutentore: «quando cambio una chat vedo per qualche secondo il testo vecchio»): /tail/<id>
rows_alive("ledger-api", "atlas-shop", "field-notes")
(tdir_a / "S-A.jsonl").write_text("\n".join(json.dumps({"type": "assistant", "uuid": f"t{i}", "timestamp": "2026-09-12T10:00:00.000Z",
                                                       "message": {"content": [{"type": "text", "text": f"voce {i}"}]}}) for i in range(40)) + "\n")
(rdir2 / "tail-seen.json").unlink(missing_ok=True)
r45 = relay("push")
tail45 = {kk: C.decrypt(v, k) for kk, v in (STORE.get("tail") or {}).items()}
a45 = tail45.get("S-A") or {}
T.check("R45 (1.59) after a push /tail/<id> of a live session with a transcript: encrypted {session, id, tx, at, entries, more}, the last 30 entries, more true",
        r45.returncode == 0 and a45.get("session") == "atlas-shop" and a45.get("id") == "S-A" and [e["text"] for e in a45.get("entries") or []] == [f"voce {i}" for i in range(10, 40)]
        and a45.get("more") is True and a45.get("tx"), r45.stderr[-300:] + str(list(tail45)) + str({x: a45.get(x) for x in ("session", "id", "more", "tx")}))
n45 = len([x for x in CALLS["requests"] if x == ("PATCH", "/tail.json")])
relay("push")
T.check("R45 (1.59) a second push with the transcript unchanged writes no /tail", len([x for x in CALLS["requests"] if x == ("PATCH", "/tail.json")]) == n45, str(CALLS["requests"][-6:]))
rows_alive("ledger-api", "field-notes")
relay("push")
T.check("R45 (1.59) a session that is no longer live loses its /tail node", "S-A" not in (STORE.get("tail") or {}), str(list(STORE.get("tail") or {})))


# R46 (contratto 1.61, 14/10, dall'analisi dei concorrenti): turno fallito al telefono, con il motivo e «continua»
f46 = tmp / "f46"
f46.mkdir()
_rd, _jc, _em = RL.rdir, RL._json_cmd, RL.emit
got46 = []
RL.rdir = lambda: f46
RL._json_cmd = lambda *a, **kw: [{"name": "atlas-shop", "tmux": "atlas-shop", "account": "personal"}]
RL.emit = lambda kind, title, body, **kw: got46.append((kind, title, body, kw))
RL.turn_failed("atlas-shop", "api_error: 529 overloaded_error (request id req_abc, key sk-abcdefghijklmnopqrstuv)")
RL.turn_failed("atlas-shop", "api_error: 529 overloaded_error again")
RL.rdir, RL._json_cmd, RL.emit = _rd, _jc, _em
k46 = got46[0][3] if got46 else {}
T.check("R46 (1.61) turn_failed: one event per session per minute, the reason in one line with the key hidden, continue_text for the button",
        len(got46) == 1 and got46[0][0] == "turn_failed" and "529 overloaded_error" in got46[0][1] and "sk-abc" not in got46[0][1]
        and k46["extra"]["reason"].endswith("key •••)") and k46["extra"]["continue_text"] and k46["session"] == "atlas-shop" and k46["ref"] == "atlas-shop", str(got46))


# R47 (contratto 1.62, 14/10): telefono e orologio zitti quando la sessione e' usata al PC
row47 = {"name": "atlas-shop", "tmux": "atlas-shop", "status": "busy", "session_id": "S-A", "account": "personal"}
s47 = lambda **kw: S.build_session(dict(row47, **kw), {"_now": 1789210800})  # noqa: E731
T.check("R47 (1.62) at_pc only when attached with a key pressed in the last 120 s; detached, idle for longer or without the datum → absent (it rings)",
        s47(attached=True, pc_activity=1789210750).get("at_pc") is True and "at_pc" not in s47(attached=False, pc_activity=1789210750)
        and "at_pc" not in s47(attached=True, pc_activity=1789210600) and "at_pc" not in s47(attached=True), "")
prev47 = {"sessions": [dict(S.build_session(dict(row47, status="busy"), {"_now": 1789210800}), state="busy")]}
cur47 = {"sessions": [dict(S.build_session(dict(row47, status="idle", attached=True, pc_activity=1789210790), {"_now": 1789210800}), state="idle",
                           outcome={"short": "done", "full": "done", "at": 1789210790})]}
ev47, _ = S.events_between(prev47, cur47, 1789210800, 1)
T.check("R47 (1.62) an event of a session used at the PC carries quiet: true", ev47 and all(e.get("quiet") is True for e in ev47 if e["session"] == "atlas-shop"), str(ev47))


# R48 (contratto 1.63, 14/10): «manda alle HH:MM» — il prompt con `at` va nella coda della sessione
rows_alive("ledger-api", "atlas-shop", "field-notes")
relay("ensure")   # R6 uninstall ha fermato il demone: R48 e R49 mandano comandi sul bus
T.wait_until(lambda: serve_pid() > 0, 10)
at48 = int(time.time()) + 3600
n48 = len(cm_calls())
res48 = send_cmd({"id": "6f1c2d3e-0630-4000-8000-000000000630", "op": "prompt", "session": "atlas-shop", "arg": "tag the release", "at": at48, "device": "phone", "issued": int(time.time()), "by": "phone-pixel8"})
bad48 = send_cmd({"id": "6f1c2d3e-0631-4000-8000-000000000631", "op": "prompt", "session": "atlas-shop", "arg": "tag the release", "at": int(time.time()) + 30 * 86400, "issued": int(time.time()), "by": "phone-pixel8"})
T.check("R48 (1.63) a prompt with `at` → `queue atlas-shop <prefix + text> --at-epoch <at>`, «scheduled for HH:MM»; an `at` past 7 days → refused, nothing queued",
        res48 and res48["ok"] is True and res48["text"].startswith("scheduled for " + time.strftime("%H:%M", time.localtime(at48)))
        and any(c.startswith("queue atlas-shop Dall'utente via telefono.") and c.endswith(f"tag the release --at-epoch {float(at48)}") for c in cm_calls()[n48:])
        and bad48 and bad48["ok"] is False and bad48["text"] == "at: expected an epoch in the next 7 days"
        and sum(1 for c in cm_calls()[n48:] if c.startswith("queue ")) == 1, str(res48) + str(bad48) + str(cm_calls()[n48:]))
Q48 = load("cm-queue")
_t48 = time.mktime((2026, 10, 14, 20, 0, 0, 0, 0, -1))
T.check("R48 (1.63) queue --at HH:MM: today when still ahead, tomorrow when past; a bad time → None",
        Q48.at_of("21:30", _t48) == time.mktime((2026, 10, 14, 21, 30, 0, 0, 0, -1)) and Q48.at_of("07:15", _t48) == time.mktime((2026, 10, 15, 7, 15, 0, 0, 0, -1))
        and Q48.at_of("25:00", _t48) is None and Q48.at_of("nove", _t48) is None, "")


# R49 (contratto 1.64, 14/10): cartella nuova dal telefono, creata solo con la conferma
par49 = str((ws / "personal").resolve())
new49 = Path(par49) / "brand-new"
p49 = lambda n, **kw: send_cmd(dict({"id": f"6f1c2d3e-0640-4000-8000-0000000006{n:02d}", "op": "project_new", "session": None, "arg": par49, "text": "brand-new",  # noqa: E731
                                     "issued": int(time.time()), "by": "phone-pixel8"}, **kw))
n49 = len(cm_calls())
ask49 = p49(1)
T.check("R49 (1.64) project_new without confirm → ok false «confirm: create <full path>», nothing created, nothing launched",
        ask49 and ask49["ok"] is False and ask49["text"] == f"confirm: create {new49}" and not new49.exists() and not any(c.startswith("launch ") for c in cm_calls()[n49:]), str(ask49))
bad49 = (p49(2, arg="/tmp", confirm=True), p49(3, text="../escape", confirm=True), p49(4, text=".hidden", confirm=True))
T.check("R49 (1.64) a parent outside the published projects' parents, a name with a path or starting with a dot → refused, nothing created",
        [b["ok"] for b in bad49] == [False] * 3 and "is not a parent folder" in bad49[0]["text"] and bad49[1]["text"].startswith("bad project name") and bad49[2]["text"].startswith("bad project name")
        and not (Path("/tmp") / "brand-new").exists(), str(bad49))
ok49 = p49(5, confirm=True)
again49 = p49(6, confirm=True)
T.check("R49 (1.64) with confirm: the folder is created and launched (`launch <path> --window`); the same name again → «already exists»",
        ok49 and ok49["ok"] is True and new49.is_dir() and ok49["text"].startswith(f"created {new49}") and any(c == f"launch {new49} --window" for c in cm_calls()[n49:])
        and again49 and again49["ok"] is False and again49["text"] == f"{new49} already exists", str(ok49) + str(again49) + str(cm_calls()[n49:]))
T.check("R49 (1.64) the state lists project_parents and project_new is in ops", par49 in (json.loads(relay("push", "--dry-run").stdout).get("project_parents") or []) and "project_new" in RL.OPS, "")

T.finish()
