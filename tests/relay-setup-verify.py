#!/usr/bin/env python3
"""relay setup (R2, 24/09/2026): il progetto Firebase guidato e idempotente, provato su una CLI di Firebase finta
e su Identity Toolkit e IAM finti — nessuna chiamata vera.

S0  --help/-h stampa l'uso ed esce 0; un'opzione sconosciuta (o --project senza valore) esce 2; in entrambi i casi nulla
S1  --dry-run su un account vuoto: legge (login:list, projects:list, instances:list, apps:list), non crea, non scrive
S2  corsa vera con --yes: progetto, istanza, regole pubblicate (deploy con il file delle regole), accesso anonimo
    (PATCH), app Android senza SHA, google-services.json, chiave del service account 0600, config scritta (.bak)
S3  seconda corsa: idempotente, nessuna creazione (le regole si ripubblicano uguali), config identica
S4  senza il token della CLI: istanza, accesso anonimo e chiave diventano passi manuali con il link esatto; --yes non aspetta;
    S4b Authentication mai aperto (404) → passo manuale «Inizia»
S5  progetto scelto dall'elenco (stdin), relay.fcm_topic esistente rispettato; S5b la chiave di un altro progetto non si tocca
S6  relay.app_package vuoto → l'app Android di supervisor, detto perche' va bene anche col solo iPhone (12/11)
S8  --dry-run senza terminale e senza --yes: i default, nessun EOFError; dal vivo si ferma e lo dice (12/11)
S9  addFirebase 403 (termini di Firebase mai accettati): il messaggio con dove andare (12/11)
S10 pulizia automatica degli anonimi accesa: setup la spegne (12/11)
S7  la configurazione principale (quella di HOME) non esiste e non nasce: si scrive solo CC_SUPERVISOR_CONFIG
"""
import base64
import json
import time
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"; home.mkdir()
state = tmp / "fb-state.json"; state.write_text(json.dumps({"projects": {"old-proj": "Old project"}, "instances": {}, "apps": {}}))
fblog = tmp / "fb.log"
os.environ["FAKE_FB_STATE"] = str(state)
fake_fb = tmp / "firebase"
fake_fb.write_text('''#!/usr/bin/env python3
import json, os, sys, shutil
state_p = os.environ["FAKE_FB_STATE"]; log_p = os.environ["FAKE_FB_LOG"]
args = sys.argv[1:]
open(log_p, "a").write(json.dumps(args) + "\\n")
st = json.load(open(state_p))
def out(result): print(json.dumps({"status": "success", "result": result}))
a = [x for x in args if x not in ("--json", "--non-interactive", "--force")]
def opt(name): return a[a.index(name) + 1] if name in a else None
c = a[0]
if c == "login:list": print("Logged in as tester@example.com")
elif c == "projects:list": out([{"projectId": p, "displayName": d} for p, d in st["projects"].items()])
elif c == "projects:create" and a[1].startswith("noterms-"): print("Error: Failed to add Firebase to Google Cloud Platform project. HTTP Error: 403, The caller does not have permission", file=sys.stderr); sys.exit(1)
elif c == "projects:create": st["projects"][a[1]] = opt("-n"); print("created")
elif c == "database:instances:list": out([{"name": n, "location": l, "databaseUrl": f"https://{n}.{l}.firebasedatabase.app", "type": "DEFAULT_DATABASE"} for n, l in st["instances"].get(opt("--project"), {}).items()])
elif c == "database:instances:create": st["instances"].setdefault(opt("--project"), {})[a[1]] = opt("--location"); print("created")
elif c == "deploy": shutil.copy("database.rules.json", os.path.join(os.path.dirname(state_p), "deployed-rules.json")); shutil.copy("firebase.json", os.path.join(os.path.dirname(state_p), "deployed-firebase.json")); print("deployed")
elif c == "apps:list": out([{"appId": i, "packageName": p, "platform": "ANDROID"} for p, i in st["apps"].get(opt("--project"), {}).items()])
elif c == "apps:create": pid = opt("--project"); i = f"1:123:android:{len(st['apps'].get(pid, {})) + 1:02d}"; st["apps"].setdefault(pid, {})[opt("--package-name")] = i; out({"appId": i, "packageName": opt("--package-name")})
elif c == "apps:sdkconfig": pid = opt("--project"); pkg = next(p for p, i in st["apps"][pid].items() if i == a[2]); out({"fileName": "google-services.json", "fileContents": json.dumps({"project_info": {"project_id": pid, "firebase_url": "x"}, "client": [{"client_info": {"mobilesdk_app_id": a[2], "android_client_info": {"package_name": pkg}}, "api_key": [{"current_key": "AIza-fake-" + pid}]}]})})
else: print(json.dumps({"status": "error", "error": {"message": "unknown " + c}})); sys.exit(1)
json.dump(st, open(state_p, "w"))
''')
fake_fb.chmod(0o755)
configstore = tmp / "firebase-tools.json"
configstore.write_text(json.dumps({"tokens": {"access_token": "fake-cli-token", "expires_at": 4102444800000}}))

# Identity Toolkit e IAM finti
API = {"anon": {}, "calls": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        out = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def _go(self, method):
        API["calls"].append((method, self.path, self.headers.get("Authorization")))
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if self.headers.get("Authorization") != "Bearer fake-cli-token":
            return self._json(401, {"error": "bad token"})
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts[-1].endswith(":enable") and method == "POST":
            API.setdefault("enabled", []).append(parts[-1].split(":")[0]); return self._json(200, {"name": "operations/x", "done": True})
        if parts[:1] == ["v1beta"] and parts[-1] == "instances":
            pid = parts[2]; st = json.load(open(os.environ["FAKE_FB_STATE"]))
            if method == "POST":
                name = self.path.split("databaseId=")[1].split("&")[0]; loc = parts[4]
                if not API.get("auth_ready", {}).get(pid, True):
                    return self._json(403, {"error": {"message": "API not ready"}})
                st["instances"].setdefault(pid, {})[name] = loc; json.dump(st, open(os.environ["FAKE_FB_STATE"], "w"))
                return self._json(200, {"name": f"projects/1/locations/{loc}/instances/{name}", "databaseUrl": f"https://{name}.{loc}.firebasedatabase.app", "type": body.get("type"), "state": "ACTIVE"})
            return self._json(200, {"instances": [{"name": n} for n in st["instances"].get(pid, {})]})
        if parts[:2] == ["admin", "v2"] and parts[-1] == "config":
            if API.get("auth_missing", {}).get(parts[3]):
                return self._json(404, {"error": {"code": 404, "message": "CONFIGURATION_NOT_FOUND", "status": "NOT_FOUND"}})
            pid = parts[3]
            if method == "PATCH" and "updateMask=autodeleteAnonymousUsers" in self.path:
                API.setdefault("autodel", {})[pid] = bool(body.get("autodeleteAnonymousUsers"))
            elif method == "PATCH":
                API["anon"][pid] = bool(((body.get("signIn") or {}).get("anonymous") or {}).get("enabled"))
            return self._json(200, dict({"signIn": {"anonymous": {"enabled": API["anon"].get(pid, False)}}},
                                        **({"autodeleteAnonymousUsers": True} if API.get("autodel", {}).get(pid) else {})))
        if parts[:1] == ["v1"] and parts[-1] == "serviceAccounts" and method == "GET":
            pid = parts[2]
            return self._json(200, {"accounts": [{"email": f"other@{pid}.iam.gserviceaccount.com"}, {"email": f"firebase-adminsdk-ab12c@{pid}.iam.gserviceaccount.com"}]})
        if parts[-1] == "keys" and method == "POST":
            pid = parts[2]
            key = {"type": "service_account", "project_id": pid, "private_key": "-----BEGIN PRIVATE KEY-----\\nfake\\n-----END PRIVATE KEY-----\\n", "client_email": parts[4]}
            return self._json(200, {"name": "k1", "privateKeyData": base64.b64encode(json.dumps(key).encode()).decode()})
        self._json(404, {"error": "no such route " + self.path})

    def do_GET(self):
        self._go("GET")

    def do_PATCH(self):
        self._go("PATCH")

    def do_POST(self):
        self._go("POST")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
API_URL = f"http://127.0.0.1:{srv.server_port}"

rdir = tmp / "relay-trial"
cfg = tmp / "config-trial.json"
cfg.write_text(json.dumps({"language": "it", "relay": {"enabled": False, "dir": str(rdir), "service_account": str(rdir / "service-account.json"), "app_package": "it.example.cmwatch"}}))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_FIREBASE_CMD": str(fake_fb),
       "FAKE_FB_STATE": str(state), "FAKE_FB_LOG": str(fblog), "CM_FIREBASE_CONFIGSTORE": str(configstore), "CM_SETUP_IDENTITY_URL": API_URL, "CM_SETUP_IAM_URL": API_URL, "CM_SETUP_RTDB_API_URL": API_URL, "CM_SETUP_SERVICEUSAGE_URL": API_URL}


def setup(*args, env=None, stdin=""):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "setup", *args], capture_output=True, text=True, env=env or ENV, input=stdin, timeout=120)


def calls():
    return [json.loads(l)[0] for l in fblog.read_text().splitlines()] if fblog.exists() else []


# S0 (30/09): --help e le opzioni sconosciute non avviano la configurazione vera
cfg0 = cfg.read_text()
outs = [setup(h) for h in ("--help", "-h")] + [setup("--yes", "--help")]
T.check("S0 --help, -h (also after --yes) → exit 0 with the usage, no call to the CLI or the APIs, nothing written",
        all(o.returncode == 0 and "uso: supervisor relay setup" in o.stdout for o in outs) and calls() == [] and API["calls"] == [] and cfg.read_text() == cfg0 and not rdir.exists(),
        str([(o.returncode, o.stdout[:80], o.stderr[:80]) for o in outs]) + str(calls()))
outs = [setup("--hepl"), setup("--yes", "--force"), setup("--project"), setup("--project", "--yes")]
T.check("S0 an unknown option or --project without a value → exit 2 naming it, the usage on stderr, nothing done",
        all(o.returncode == 2 and "opzione sconosciuta" in o.stderr and "uso:" in o.stderr for o in outs) and "--hepl" in outs[0].stderr and "--force" in outs[1].stderr
        and calls() == [] and API["calls"] == [] and cfg.read_text() == cfg0 and not rdir.exists(), str([(o.returncode, o.stderr[:120]) for o in outs]))

# S1
r = setup("--dry-run", "--yes")
cfg_before = cfg.read_text()
T.check("S1 --dry-run on an empty account: exit 0, WARN «would create» for project, database, rules, anonymous sign-in, app, service account key and config; only list calls to the CLI; nothing written",
        r.returncode == 0 and r.stdout.count("verrebbe") >= 6 and "verrebbero scritti" in r.stdout and set(calls()) <= {"login:list", "projects:list", "database:instances:list", "apps:list"} and not rdir.exists() and cfg.read_text() == cfg_before and not (Path(str(cfg) + ".bak")).exists() and all(m == "GET" for m, _, _ in API["calls"]), r.stdout + r.stderr + str(calls()))
fblog.unlink(); API["calls"].clear()

# S2
r = setup("--yes")
st = json.loads(state.read_text())
new_ids = [p for p in st["projects"] if p.startswith("supervisor-relay-")]
pid = new_ids[0] if new_ids else ""
cfg_after = json.loads(cfg.read_text())["relay"]
sa = rdir / "service-account.json"
rules = json.loads((tmp / "deployed-rules.json").read_text()) if (tmp / "deployed-rules.json").exists() else {}
T.check("S2 --yes on an empty account: exit 0, a project supervisor-relay-xxxxxx created, its DEFAULT instance in europe-west1 through the Management API (the three APIs enabled first, no database:instances:create), the rules deployed from a temp dir (deploy --only database)",
        r.returncode == 0 and len(new_ids) == 1 and st["instances"].get(pid) == {f"{pid}-default-rtdb": "europe-west1"} and "database:instances:create" not in calls() and any(m == "POST" and "/v1beta/" in p and f"databaseId={pid}-default-rtdb" in p for m, p, _ in API["calls"]) and {"firebasedatabase.googleapis.com", "identitytoolkit.googleapis.com", "iam.googleapis.com"} <= set(API.get("enabled", [])) and "deploy" in calls() and json.loads((tmp / "deployed-firebase.json").read_text())["database"][0]["instance"] == f"{pid}-default-rtdb", r.stdout + r.stderr + str(st) + str(API["calls"]))
T.check("S2 the rules: /state /events /result readable only by a uid in /allowed, /cmd writable by them, /pair/$node readable and its watch writable by any signed-in (anonymous) user, root closed",
        rules.get("rules", {}).get(".read") is False and "allowed" in rules["rules"]["state"][".read"] and rules["rules"]["events"][".read"] == rules["rules"]["state"][".read"] == rules["rules"]["result"][".read"] and rules["rules"]["cmd"][".write"] == rules["rules"]["state"][".read"] and rules["rules"]["pair"]["$node"][".read"] == "auth != null" and rules["rules"]["pair"]["$node"]["watch"][".write"] == "auth != null", json.dumps(rules))
T.check("S2 anonymous sign-in enabled through Identity Toolkit (GET then PATCH with the CLI's token), the Android app registered without SHA (apps:create --package-name), google-services.json saved in relay.dir",
        API["anon"].get(pid) is True and any(m == "PATCH" and "signIn.anonymous.enabled" in p for m, p, _ in API["calls"]) and st["apps"].get(pid) == {"it.example.cmwatch": "1:123:android:01"} and json.loads((rdir / "google-services.json").read_text())["project_info"]["project_id"] == pid, str(API["calls"]) + str(st["apps"]))
T.check("S2 the firebase-adminsdk key created through IAM (not the other account) and written 0600 with the project's id",
        sa.is_file() and oct(sa.stat().st_mode & 0o777) == "0o600" and json.loads(sa.read_text())["project_id"] == pid and any(m == "POST" and "firebase-adminsdk-ab12c@" in p and p.endswith("/keys") for m, p, _ in API["calls"]), str(API["calls"]))
T.check("S2 the config in use (CC_SUPERVISOR_CONFIG) gets relay.enabled, firebase_url, fcm_topic «watch», service_account, firebase_app {api_key, project_id, app_id}; the rest of the file is kept; a .bak copy exists; the summary ends with 0 FAIL and the next steps",
        cfg_after["enabled"] is True and cfg_after["firebase_url"] == f"https://{pid}-default-rtdb.europe-west1.firebasedatabase.app" and cfg_after["fcm_topic"] == "watch" and cfg_after["service_account"] == str(sa) and cfg_after["firebase_app"] == {"api_key": f"AIza-fake-{pid}", "project_id": pid, "app_id": "1:123:android:01"} and cfg_after["app_package"] == "it.example.cmwatch" and cfg_after["dir"] == str(rdir) and Path(str(cfg) + ".bak").is_file() and "0 FAIL, 0 WARN" in r.stdout and "relay pair" in r.stdout, r.stdout + r.stderr + json.dumps(cfg_after))
fblog.unlink(); API["calls"].clear()

# S3
cfg_text = cfg.read_text()
r = setup("--yes")
T.check("S3 a second run is idempotent: exit 0, every step PASS, no create/PATCH/POST but the API enables (the rules are deployed again, identical), the config file identical",
        r.returncode == 0 and "0 FAIL, 0 WARN" in r.stdout and not ({"projects:create", "database:instances:create", "apps:create"} & set(calls())) and all(m == "GET" or p.endswith(":enable") for m, p, _ in API["calls"]) and cfg.read_text() == cfg_text, r.stdout + str(calls()) + str(API["calls"]))
fblog.unlink(); API["calls"].clear()

# S4: senza token → passi manuali con il link esatto; la chiave manca → FAIL, --yes non resta ad aspettare
state.write_text(json.dumps({"projects": {"old-proj": "Old project"}, "instances": {}, "apps": {}}))
rdir4 = tmp / "relay-4"; cfg4 = tmp / "config-4.json"
cfg4.write_text(json.dumps({"language": "it", "relay": {"dir": str(rdir4), "service_account": str(rdir4 / "sa.json"), "app_package": "it.example.cmwatch"}}))
ENV4 = dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg4), CM_FIREBASE_CONFIGSTORE=str(tmp / "no-configstore.json"))
r = setup("--yes", "--project", "manual-proj", env=ENV4)
T.check("S4 without the CLI's token: the database instance, anonymous sign-in and the key become manual steps with the console links (database, authentication/providers, settings/serviceaccounts/adminsdk); the instance missing after the wait → stopped, exit 1, --yes does not wait; no API call",
        r.returncode == 1 and "https://console.firebase.google.com/project/manual-proj/database" in r.stdout and "manuale:" in r.stdout and "1 FAIL" in r.stdout and "rilancia relay setup" in r.stdout and not API["calls"], r.stdout[-1200:] + r.stderr)
# la stessa corsa con l'istanza gia' creata a mano: restano manuali accesso anonimo e chiave, la config si scrive
st4 = json.loads(state.read_text()); st4["instances"]["manual-proj"] = {"manual-proj-default-rtdb": "europe-west1"}; state.write_text(json.dumps(st4))
r = setup("--yes", "--project", "manual-proj", env=ENV4)
T.check("S4 with the instance made by hand: anonymous sign-in and the key manual with their links, the key missing → FAIL sa_missing, exit 1; the config still written for the rest",
        r.returncode == 1 and "https://console.firebase.google.com/project/manual-proj/authentication/providers" in r.stdout and "https://console.firebase.google.com/project/manual-proj/settings/serviceaccounts/adminsdk" in r.stdout and "1 FAIL" in r.stdout and json.loads(cfg4.read_text())["relay"]["firebase_url"].startswith("https://manual-proj-default-rtdb") and not API["calls"], r.stdout[-1200:] + r.stderr)
# S4b: progetto nuovo dove Authentication non e' mai stato aperto (404 CONFIGURATION_NOT_FOUND): passo manuale «Inizia», con --yes resta WARN
API["auth_missing"] = {"fresh-proj": True}
cfg4b = tmp / "config-4b.json"; rdir4b = tmp / "relay-4b"
cfg4b.write_text(json.dumps({"language": "it", "relay": {"dir": str(rdir4b), "service_account": str(rdir4b / "sa.json"), "app_package": "it.example.cmwatch"}}))
r = setup("--yes", "--project", "fresh-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg4b)))
T.check("S4b Authentication never opened (404 CONFIGURATION_NOT_FOUND) → WARN with the «Inizia» console link, no PATCH, the rest done, exit 0 with the manual step listed",
        r.returncode == 0 and "premi «Inizia»" in r.stdout and "fresh-proj/authentication/providers" in r.stdout and not any(m == "PATCH" for m, _, _ in API["calls"]) and (rdir4b / "sa.json").is_file() and "manuale:" in r.stdout, r.stdout[-1000:] + r.stderr)
API["auth_missing"] = {}
# S4c (11/11): con un terminale relay setup non chiede Invio per «Inizia»: stampa il link e guarda da solo; il clic della
# persona (qui: Authentication che diventa aperto dopo 3 s) basta, e l'accesso anonimo si accende
import threading  # noqa: E402
API["auth_missing"] = {"click-proj": True}
threading.Timer(3, lambda: API["auth_missing"].pop("click-proj", None)).start()
cfg4c = tmp / "config-4c.json"; rdir4c = tmp / "relay-4c"
cfg4c.write_text(json.dumps({"language": "it", "relay": {"dir": str(rdir4c), "service_account": str(rdir4c / "sa.json"), "app_package": "it.example.cmwatch"}}))
API["calls"].clear()
t4c = time.time()
r = setup("--yes", "--project", "click-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg4c), CM_SETUP_ANON_WAIT_S="30"))
T.check("S4c «Inizia» watched by setup itself: the exact link, no Enter asked, seen within seconds of the click, anonymous sign-in turned on (PATCH), no manual step left for it",
        r.returncode == 0 and "click-proj/authentication/providers" in r.stdout and "non serve premere Invio" in r.stdout and "Authentication aperto" in r.stdout
        and API["anon"].get("click-proj") is True and "premi Invio" not in r.stdout and time.time() - t4c < 25, r.stdout[-1200:] + r.stderr)
API["auth_missing"] = {}
T.check("S4 --project names a project that does not exist yet → created with that id", "manual-proj" in json.loads(state.read_text())["projects"], str(json.loads(state.read_text())["projects"]))
fblog.unlink()

# S5: progetto scelto dall'elenco, topic esistente rispettato
rdir5 = tmp / "relay-5"; cfg5 = tmp / "config-5.json"
cfg5.write_text(json.dumps({"language": "it", "relay": {"dir": str(rdir5), "service_account": str(rdir5 / "sa.json"), "fcm_topic": "watch-trial", "app_package": "it.example.cmwatch"}}))
r = setup(env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg5)), stdin="1\n")
c5 = json.loads(cfg5.read_text())["relay"]
T.check("S5 interactive: «1» picks the first listed project (old-proj), no project created; relay.fcm_topic «watch-trial» kept; the key at relay.service_account",
        r.returncode == 0 and "progetto old-proj" in r.stdout and "projects:create" not in calls() and c5["fcm_topic"] == "watch-trial" and c5["firebase_url"] == "https://old-proj-default-rtdb.europe-west1.firebasedatabase.app" and c5["service_account"] == str(rdir5 / "sa.json") and (rdir5 / "sa.json").is_file(), r.stdout[-800:] + r.stderr)
fblog.unlink()
# S5b: al percorso della chiave c'e' quella di un altro progetto (la vera, con una config di prova distratta): non si tocca
sa_main = tmp / "sa-main.json"; sa_main.write_text(json.dumps({"type": "service_account", "project_id": "other-real-project", "private_key": "real"}))
cfg5b = tmp / "config-5b.json"
cfg5b.write_text(json.dumps({"language": "it", "relay": {"dir": str(tmp / "relay-5b"), "service_account": str(sa_main), "app_package": "it.example.cmwatch"}}))
API["calls"].clear()
r = setup("--yes", "--project", "old-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg5b)))
T.check("S5b the key file at relay.service_account belongs to another project → FAIL «non la tocco», the file byte-identical, exit 1, no key requested from IAM",
        r.returncode == 1 and "other-real-project" in r.stdout and "non la tocco" in r.stdout and json.loads(sa_main.read_text())["private_key"] == "real" and not any(m == "POST" and p.endswith("/keys") for m, p, _ in API["calls"]), r.stdout[-700:])
fblog.unlink()

# S6: senza relay.app_package (12/11, dal collega con l'iPhone: prima si fermava senza spiegare)
cfg6 = tmp / "config-6.json"; cfg6.write_text(json.dumps({"language": "it", "relay": {"dir": str(tmp / "relay-6")}}))
r = setup("--yes", "--project", "old-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg6)))
_c6 = json.loads(cfg6.read_text())["relay"]
T.check("S6 relay.app_package empty → a neutral package (web.supervisor.relay) with a line that says it is enough for the web app and the iPhone, and what to do with the Android app; registered, written in the config, setup goes on",
        r.returncode == 0 and "web.supervisor.relay" in r.stdout and "solo l'iPhone" in r.stdout and "app Android" in r.stdout and _c6.get("app_package") == "web.supervisor.relay"
        and _c6.get("firebase_app", {}).get("project_id") == "old-proj" and "web.supervisor.relay" in json.dumps(json.loads(state.read_text())["apps"].get("old-proj", {})), r.stdout[-700:])
fblog.unlink()

# S8 (12/11, dal collega su Mac): --dry-run senza terminale e senza --yes andava in crash con EOFError alla scelta del progetto
cfg8 = tmp / "config-8.json"; cfg8.write_text(json.dumps({"language": "it", "relay": {"dir": str(tmp / "relay-8"), "service_account": str(tmp / "relay-8" / "sa.json"), "app_package": "it.example.cmwatch"}}))
r8 = setup("--dry-run", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg8)), stdin="")
r8b = setup(env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg8)), stdin="")
T.check("S8 --dry-run with no terminal and no --yes: no EOFError, the defaults as with --yes (a new project «would be created»), exit 0",
        r8.returncode == 0 and "EOFError" not in r8.stderr and "Traceback" not in r8.stderr and "verrebbe creato" in r8.stdout, r8.stdout[-500:] + r8.stderr[-500:])
T.check("S8 a real run with no terminal and no --yes stops and says to use a terminal or --yes; no project created",
        r8b.returncode == 1 and "Traceback" not in r8b.stderr and "nessun terminale" in r8b.stdout and "projects:create" not in calls(), r8b.stdout[-400:] + r8b.stderr[-300:])
fblog.unlink(missing_ok=True)

# S9 (12/11, dal collega): un account Google che non ha mai usato Firebase → projects:create crea il progetto GCP, addFirebase 403
cfg9 = tmp / "config-9.json"; cfg9.write_text(json.dumps({"language": "it", "relay": {"dir": str(tmp / "relay-9"), "app_package": "it.example.cmwatch"}}))
r9 = setup("--yes", "--project", "noterms-abc123", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg9)))
T.check("S9 projects:create fails with 403 on addFirebase → a message that says to accept the Firebase terms, where (console.firebase.google.com) and the command to run again; exit 1",
        r9.returncode == 1 and "termini di Firebase" in r9.stdout and "https://console.firebase.google.com/" in r9.stdout and "--project noterms-abc123" in r9.stdout, r9.stdout[-500:])
fblog.unlink(missing_ok=True)

# S10 (12/11, dal collega): la pulizia automatica degli anonimi stacca il telefono dopo 30 giorni → setup la spegne
API.setdefault("autodel", {})["old-proj"] = True
cfg10 = tmp / "config-10.json"; cfg10.write_text(json.dumps({"language": "it", "relay": {"dir": str(tmp / "relay-10"), "app_package": "it.example.cmwatch"}}))
r10d = setup("--dry-run", "--project", "old-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg10)))
_still = API["autodel"]["old-proj"]
r10 = setup("--yes", "--project", "old-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg10)))
r10b = setup("--yes", "--project", "old-proj", env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg10)))
T.check("S10 automatic clean-up of anonymous accounts on: --dry-run says it would be turned off and leaves it; the real run turns it off (PATCH autodeleteAnonymousUsers false); the next run says to keep it off",
        "verrebbe spenta" in r10d.stdout and _still is True and "spenta ora" in r10.stdout and API["autodel"]["old-proj"] is False
        and "lasciala così" in r10b.stdout, r10.stdout[-500:])
fblog.unlink(missing_ok=True)

# S7
T.check("S7 the main config of HOME never appears: only the CC_SUPERVISOR_CONFIG files were written", not (home / ".config" / "cc-supervisor").exists(), str(list(home.iterdir())))
srv.shutdown()
T.finish()
