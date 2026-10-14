#!/usr/bin/env python3
"""supervisor relay setup — il progetto Firebase del relay, guidato e idempotente (R2 del design dell'app telefono,
docs/plans/2026-09-24-app-telefono-fondamenta-design.md in claude-master-watch, 24/09/2026).

  supervisor relay setup [--project ID] [--dry-run] [--yes]

Passi, ognuno saltato se gia' fatto:
  1. la CLI di Firebase (`firebase`, npm) e il suo login;
  2. il progetto: quello della configurazione, o --project, o scelto da un elenco, o creato (projects:create);
  3. l'istanza di default del Realtime Database, <progetto>-default-rtdb in relay.setup_location, con la Management API
     (REST con il token della CLI: `database:instances:create` fa solo le istanze aggiuntive); senza token, la console;
  4. le regole del README (RULES qui sotto) con `firebase deploy --only database` (ripubblicate uguali a ogni corsa);
  5. l'accesso anonimo (Identity Toolkit, REST con il token della CLI; senza token: il link della console e Invio;
     su un progetto nuovo Firebase Auth va aperto una volta dalla console, «Inizia»: nessuna API lo fa senza fatturazione);
  6. l'app Android del pacchetto relay.app_package, senza impronta SHA (apps:create), e i suoi dati in
     relay.firebase_app (apps:sdkconfig, che salva anche <relay.dir>/google-services.json);
  7. la chiave del service account firebase-adminsdk (IAM, REST con il token della CLI) in relay.service_account,
     0600; senza token: il link della console e Invio, finche' il file non c'e';
  8. relay.firebase_url, relay.fcm_topic, relay.firebase_app e relay.enabled nella configurazione in uso
     (CC_SUPERVISOR_CONFIG compresa).
Alla fine un riepilogo come doctor. --dry-run legge (login, elenchi) e non crea, non scarica, non scrive nulla.

Il token OAuth della CLI (~/.config/configstore/firebase-tools.json) serve solo per i due passi che la CLI non copre:
se manca o e' scaduto quei passi diventano manuali, con il link esatto. Nessuna dipendenza nuova.
Prove: CM_FIREBASE_CMD (la CLI finta), CM_FIREBASE_CONFIGSTORE (il suo file dei token), CM_SETUP_IDENTITY_URL,
CM_SETUP_IAM_URL, CM_SETUP_RTDB_API_URL e CM_SETUP_SERVICEUSAGE_URL (le API finte).
"""
import base64
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
R = CFG["relay"]
FIREBASE = os.environ.get("CM_FIREBASE_CMD") or "firebase"
CONFIGSTORE = Path(os.environ.get("CM_FIREBASE_CONFIGSTORE") or (cm.home() / ".config" / "configstore" / "firebase-tools.json"))
IDENTITY_URL = (os.environ.get("CM_SETUP_IDENTITY_URL") or "https://identitytoolkit.googleapis.com").rstrip("/")
IAM_URL = (os.environ.get("CM_SETUP_IAM_URL") or "https://iam.googleapis.com").rstrip("/")
RTDB_API_URL = (os.environ.get("CM_SETUP_RTDB_API_URL") or "https://firebasedatabase.googleapis.com").rstrip("/")
SERVICEUSAGE_URL = (os.environ.get("CM_SETUP_SERVICEUSAGE_URL") or "https://serviceusage.googleapis.com").rstrip("/")
APP_NAME = "supervisor watch"
ALLOWED = "auth != null && root.child('allowed').child(auth.uid).val() === true"
# Le regole del README: /state, /events e /result leggibili solo da un uid presente in /allowed; /cmd scrivibile solo
# da quelli, come /share/<id> (fino a 10 MB cifrati); /pair/<code> e /pair/<id> leggibili dall'accesso anonimo (pc_pub e la conferma ok), il loro `watch`
# scrivibile da lui; /allowed lo scrive solo il PC con il service account, che le regole non limitano.
RULES = {"rules": {
    ".read": False, ".write": False,
    "state": {".read": ALLOWED}, "events": {".read": ALLOWED}, "result": {".read": ALLOWED},
    "tail": {".read": ALLOWED},   # 1.59 (14/10): le ultime voci della chat di ogni sessione, scritte solo dal PC
    "cmd": {".read": ALLOWED, ".write": ALLOWED},
    # 1.19: l'immagine di «Condividi», scritta dal telefono e letta (poi cancellata) dal PC con il service account
    # 1.48: o a pezzi, /share/<id>/parts/<0..49> e /share/<id>/meta, ognuno entro 1,5 MB (50 pezzi da 1 MB = 50 MB)
    "share": {"$id": {".write": ALLOWED,
                      ".validate": "(newData.child('enc').isString() && newData.child('enc').val().length <= 10000000) || newData.hasChild('meta') || newData.hasChild('parts')",
                      "meta": {".validate": "newData.child('enc').isString() && newData.child('enc').val().length <= 1500000"},
                      "parts": {"$k": {".validate": "$k.matches(/^([0-9]|[1-4][0-9])$/) && newData.child('enc').isString() && newData.child('enc').val().length <= 1500000"}}}},
    # 1.24: il file aperto dal telefono, scritto dal PC con il service account; il dispositivo lo legge e lo cancella
    "file": {"$id": {".read": ALLOWED, ".write": ALLOWED + " && !newData.exists()"}},
    # 1.20: ogni dispositivo scrive solo il proprio /seen/<uid>, l'ora (del server) dell'ultima lettura di /state
    "seen": {"$uid": {".write": "auth != null && auth.uid === $uid && root.child('allowed').child($uid).val() === true", ".validate": "newData.isNumber()"}},
    "pair": {"$node": {".read": "auth != null", "watch": {".write": "auth != null"}}},
}}


class SetupError(Exception):
    pass


class Setup:
    def __init__(self, project=None, dry_run=False, yes=False, ask=input):
        self.project = project
        self.dry_run = dry_run
        self.yes = yes
        self.ask = ask
        self.rows = []        # (livello, chiave del messaggio, kwargs) per il riepilogo
        self.manual = []      # i passi rimasti manuali
        self.writes = {}      # cosa finisce nella configurazione

    # ------------------------------------------------------------------ strumenti
    def say(self, key, **kw):
        print(M(key, **kw)); sys.stdout.flush()

    def row(self, level, key, **kw):
        self.rows.append((level, key, kw))
        self.say(f"setup.{level.lower()}", detail=M(key, **kw))

    def fb(self, *args, as_json=True, check=True, cwd=None):
        """Una chiamata alla CLI di Firebase; con as_json torna `result` di {"status","result"} (o solleva)."""
        cmd = [FIREBASE, *args] + (["--json"] if as_json else []) + ["--non-interactive"]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=600, env=dict(os.environ, NODE_NO_WARNINGS="1"))
        if as_json:
            raw = p.stdout.strip()
            try:
                doc = json.loads(raw[raw.index("{"):]) if "{" in raw else {}
            except ValueError:
                doc = {}
            if doc.get("status") == "success":
                return doc.get("result")
            if check:
                err = (doc.get("error") or {}).get("message") if isinstance(doc.get("error"), dict) else doc.get("error")
                raise SetupError(M("setup.cli_failed", cmd=" ".join(args[:2]), error=str(err or p.stderr.strip() or p.stdout.strip())[:300]))
            return None
        if check and p.returncode != 0:
            raise SetupError(M("setup.cli_failed", cmd=" ".join(args[:2]), error=(p.stderr.strip() or p.stdout.strip())[:300]))
        return p.returncode == 0

    def token(self):
        """L'access token della CLI dal suo configstore; se sta per scadere una chiamata qualsiasi lo rinfresca.
        None se non c'e': i passi REST diventano manuali."""
        def read():
            try:
                t = json.loads(CONFIGSTORE.read_text()).get("tokens") or {}
                if t.get("access_token") and float(t.get("expires_at") or 0) / 1000 - time.time() > 60:
                    return t["access_token"]
            except (OSError, ValueError):
                pass
            return None
        tok = read()
        if not tok:
            self.fb("projects:list", check=False)
            tok = read()
        return tok

    def rest(self, method, url, body=None):
        tok = self.token()
        if not tok:
            raise SetupError("no token")
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            raise SetupError(f"{method} {url.split('?')[0]}: HTTP {e.code} {e.read().decode()[:200]}") from e
        return json.loads(raw) if raw else {}

    def wait_enter(self, key, **kw):
        self.say(key, **kw)
        if self.dry_run or self.yes:
            return
        self.ask(M("setup.press_enter"))

    def choose(self, key, default, **kw):
        if self.yes:
            return default
        a = self.ask(M(key, default=default, **kw)).strip()
        return a or default

    # ------------------------------------------------------------------ passi
    def step_cli(self):
        if not shutil.which(FIREBASE):
            raise SetupError(M("setup.no_cli"))
        p = subprocess.run([FIREBASE, "login:list"], capture_output=True, text=True, timeout=120, env=dict(os.environ, NODE_NO_WARNINGS="1"))
        m = re.search(r"Logged in as (\S+)", p.stdout + p.stderr)
        if not m:
            raise SetupError(M("setup.no_login"))
        self.row("PASS", "setup.cli_ok", user=m.group(1))

    def step_project(self):
        known = self.project or str((R.get("firebase_app") or {}).get("project_id") or "").strip()
        if not known and R.get("firebase_url"):
            m = re.match(r"https://([a-z0-9-]+?)(-default-rtdb)?\.", str(R["firebase_url"]))
            known = m.group(1) if m else ""
        projects = self.fb("projects:list") or []
        ids = [p.get("projectId") for p in projects if p.get("projectId")]
        if known and known in ids:
            self.project = known
            self.row("PASS", "setup.project_ok", project=known); return
        if known and not self.dry_run:
            # nominato ma assente: si crea con quell'id
            self.create_project(known); return
        if known:
            self.project = known
            self.row("WARN", "setup.project_would_create", project=known); return
        if self.yes:
            self.create_project(self.new_id()); return
        for i, p in enumerate(projects, 1):
            print(f"  {i}) {p.get('projectId')}  {p.get('displayName') or ''}")
        a = self.ask(M("setup.project_pick", n=len(projects))).strip().lower()
        if a.isdigit() and 1 <= int(a) <= len(projects):
            self.project = ids[int(a) - 1]
            self.row("PASS", "setup.project_ok", project=self.project); return
        pid = self.choose("setup.project_new_id", self.new_id())
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", pid):
            raise SetupError(M("setup.project_bad_id", project=pid))
        self.create_project(pid)

    def persist_project(self):
        """Il progetto scelto o creato finisce subito in relay.firebase_app.project_id: una corsa interrotta dopo
        la creazione riparte da li' e non ne crea un altro (successo il 25/09 con --yes: due progetti)."""
        if self.dry_run or not self.project:
            return
        target = cm.config_path()
        try:
            raw = json.loads(target.read_text()) if target.is_file() else {}
        except ValueError:
            return
        relay = raw.get("relay") if isinstance(raw.get("relay"), dict) else {}
        app = relay.get("firebase_app") if isinstance(relay.get("firebase_app"), dict) else {}
        if app.get("project_id") == self.project:
            return
        relay["firebase_app"] = dict(app, project_id=self.project)
        raw["relay"] = relay
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def new_id():
        return f"claude-master-relay-{secrets.randbelow(10000):04d}"

    def create_project(self, pid):
        self.project = pid
        if self.dry_run:
            self.row("WARN", "setup.project_would_create", project=pid); return
        self.fb("projects:create", pid, "-n", "supervisor relay", as_json=False)
        self.row("PASS", "setup.project_created", project=pid)

    def enable_api(self, service):
        """Accende un'API di Google sul progetto (Service Usage): idempotente, e un progetto appena nato ne ha spente
        quelle del database, di Identity Toolkit e di IAM."""
        if self.dry_run:   # accendere un'API e' una scrittura: a secco si prova la chiamata cosi' com'e'
            return
        self.rest("POST", f"{SERVICEUSAGE_URL}/v1/projects/{self.project}/services/{service}:enable", {})

    def step_database(self):
        loc = str(R.get("setup_location") or "europe-west1")
        name = f"{self.project}-default-rtdb"
        rows = self.fb("database:instances:list", "--project", self.project, check=False) or []
        inst = next((x for x in rows if isinstance(x, dict) and x.get("name") == name), None) if isinstance(rows, list) else None
        if not inst and isinstance(rows, list) and rows:
            inst = next((x for x in rows if isinstance(x, dict) and x.get("type") == "DEFAULT_DATABASE"), None)
        if inst:
            self.db_url = inst.get("databaseUrl") or f"https://{inst['name']}.{inst.get('location', loc)}.firebasedatabase.app"
            self.row("PASS", "setup.db_ok", url=self.db_url); return
        self.db_url = f"https://{name}.{loc}.firebasedatabase.app"
        link = f"https://console.firebase.google.com/project/{self.project}/database"
        if self.dry_run:
            self.row("WARN", "setup.db_would_create", name=name, location=loc); return
        # L'istanza di default nasce solo dalla console o dalla Management API (la CLI ne crea le aggiuntive):
        # REST con il token della CLI; senza token, la console e Invio.
        try:
            self.enable_api("firebasedatabase.googleapis.com")
            for attempt in range(24):   # l'API appena accesa risponde 403 anche per un minuto (visto il 25/09: ok dopo ~90 s)
                try:
                    inst = self.rest("POST", f"{RTDB_API_URL}/v1beta/projects/{self.project}/locations/{loc}/instances?databaseId={name}", {"type": "DEFAULT_DATABASE"})
                    break
                except SetupError as e:
                    if "HTTP 403" not in str(e) or attempt == 23:
                        raise
                    if attempt == 0:
                        self.say("setup.db_waiting_api")
                    time.sleep(5)
            self.db_url = inst.get("databaseUrl") or self.db_url
            self.row("PASS", "setup.db_created", url=self.db_url); return
        except SetupError as e:
            self.row("WARN", "setup.db_no_token", error=str(e)[:160])
        self.manual.append(M("setup.db_manual", name=name, location=loc, link=link))
        self.wait_enter("setup.db_manual", name=name, location=loc, link=link)
        rows = self.fb("database:instances:list", "--project", self.project, check=False) or []
        inst = next((x for x in rows if isinstance(x, dict) and x.get("type") == "DEFAULT_DATABASE"), None) if isinstance(rows, list) else None
        if inst:
            self.db_url = inst.get("databaseUrl") or self.db_url
            self.row("PASS", "setup.db_ok", url=self.db_url)
        else:
            raise SetupError(M("setup.db_missing", link=link))

    def step_rules(self):
        if self.dry_run:
            self.row("WARN", "setup.rules_would_deploy"); return
        with tempfile.TemporaryDirectory(prefix="cm-relay-rules-") as d:
            (Path(d) / "database.rules.json").write_text(json.dumps(RULES, indent=2) + "\n")
            (Path(d) / "firebase.json").write_text(json.dumps({"database": [{"instance": self.db_url.split("//")[1].split(".")[0], "rules": "database.rules.json"}]}))
            self.fb("deploy", "--only", "database", "--project", self.project, "--force", as_json=False, cwd=d)
        self.row("PASS", "setup.rules_ok")

    def step_anonymous(self):
        link = f"https://console.firebase.google.com/project/{self.project}/authentication/providers"
        url = f"{IDENTITY_URL}/admin/v2/projects/{self.project}/config"
        try:
            self.enable_api("identitytoolkit.googleapis.com")
            conf = self.rest("GET", url)
        except SetupError as e:
            if "CONFIGURATION_NOT_FOUND" in str(e) or "HTTP 404" in str(e):
                # Firebase Auth non e' mai stato aperto: la console lo inizializza («Inizia»), nessuna API pubblica
                # lo fa senza fatturazione (initializeAuth e' Identity Platform)
                self.manual.append(M("setup.anon_init_manual", link=link))
                self.row("WARN", "setup.anon_not_initialized")
                self.wait_enter("setup.anon_init_manual", link=link)
                try:
                    conf = self.rest("GET", url)
                except SetupError:
                    self.row("WARN", "setup.anon_still_missing", link=link); return
            else:
                self.manual.append(M("setup.anon_manual", link=link))
                self.row("WARN", "setup.anon_no_token", error=str(e)[:120])
                self.wait_enter("setup.anon_manual", link=link); return
        if ((conf.get("signIn") or {}).get("anonymous") or {}).get("enabled"):
            self.row("PASS", "setup.anon_ok"); return
        if self.dry_run:
            self.row("WARN", "setup.anon_would_enable"); return
        self.rest("PATCH", url + "?updateMask=signIn.anonymous.enabled", {"signIn": {"anonymous": {"enabled": True}}})
        self.row("PASS", "setup.anon_enabled")

    def step_app(self):
        pkg = str(R.get("app_package") or "").strip()
        if not pkg:
            raise SetupError(M("setup.no_package"))
        apps = self.fb("apps:list", "ANDROID", "--project", self.project, check=False) or []
        app = next((a for a in apps if isinstance(a, dict) and a.get("packageName") == pkg), None) if isinstance(apps, list) else None
        if app:
            app_id = app.get("appId")
            self.row("PASS", "setup.app_ok", package=pkg, app_id=app_id)
        elif self.dry_run:
            self.row("WARN", "setup.app_would_create", package=pkg); self.app = None; return
        else:
            res = self.fb("apps:create", "ANDROID", APP_NAME, "--package-name", pkg, "--project", self.project)
            app_id = (res or {}).get("appId") if isinstance(res, dict) else None
            if not app_id:
                apps = self.fb("apps:list", "ANDROID", "--project", self.project) or []
                app_id = next((a.get("appId") for a in apps if a.get("packageName") == pkg), None)
            if not app_id:
                raise SetupError(M("setup.app_no_id", package=pkg))
            self.row("PASS", "setup.app_created", package=pkg, app_id=app_id)
        res = self.fb("apps:sdkconfig", "ANDROID", app_id, "--project", self.project)
        contents = (res or {}).get("fileContents") if isinstance(res, dict) else None
        doc = json.loads(contents) if isinstance(contents, str) else (contents or {})
        client = next((c for c in doc.get("client") or [] if ((c.get("client_info") or {}).get("android_client_info") or {}).get("package_name") == pkg), None)
        try:
            self.app = {"api_key": str(client["api_key"][0]["current_key"]), "project_id": str(doc["project_info"]["project_id"]),
                        "app_id": str(client["client_info"]["mobilesdk_app_id"])}
        except (TypeError, KeyError, IndexError):
            raise SetupError(M("setup.app_bad_config", package=pkg)) from None
        gs = self.rdir() / "google-services.json"
        if not self.dry_run:
            cm_write(gs, json.dumps(doc, indent=2) + "\n")
        self.row("PASS", "setup.app_config_ok", path=str(gs))

    def rdir(self):
        p = Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay"))
        if not self.dry_run:
            p.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        return p

    def sa_path(self):
        return Path(cm.expand(R.get("service_account") or "")) if R.get("service_account") else self.rdir() / "service-account.json"

    def step_service_account(self):
        path = self.sa_path()
        link = f"https://console.firebase.google.com/project/{self.project}/settings/serviceaccounts/adminsdk"
        try:
            have = json.loads(path.read_text()).get("project_id")
        except (OSError, ValueError):
            have = None
        if have == self.project:
            self.row("PASS", "setup.sa_ok", path=str(path)); return
        if have:
            # la chiave di un altro progetto (quella vera, con una configurazione di prova senza relay.service_account
            # suo): non si tocca mai
            self.row("FAIL", "setup.sa_other_project", path=str(path), project=have); return
        if self.dry_run:
            self.row("WARN", "setup.sa_would_create", path=str(path)); return
        try:
            self.enable_api("iam.googleapis.com")
            accounts = (self.rest("GET", f"{IAM_URL}/v1/projects/{self.project}/serviceAccounts") or {}).get("accounts") or []
            email = next((a.get("email") for a in accounts if str(a.get("email") or "").startswith("firebase-adminsdk-")), None)
            if not email:
                raise SetupError(M("setup.sa_not_found", project=self.project))
            key = self.rest("POST", f"{IAM_URL}/v1/projects/{self.project}/serviceAccounts/{email}/keys", {"privateKeyType": "TYPE_GOOGLE_CREDENTIALS_FILE"})
            data = base64.b64decode(key["privateKeyData"])
            json.loads(data.decode())   # e' il JSON della chiave, o non lo scriviamo
            cm_write(path, data.decode())
            self.row("PASS", "setup.sa_created", email=email, path=str(path)); return
        except (SetupError, KeyError, ValueError) as e:
            self.row("WARN", "setup.sa_no_token", error=str(e)[:160])
        self.manual.append(M("setup.sa_manual", link=link, path=str(path)))
        for _ in range(10):
            self.wait_enter("setup.sa_manual", link=link, path=str(path))
            try:
                if json.loads(path.read_text()).get("project_id") == self.project:
                    os.chmod(path, 0o600)
                    self.row("PASS", "setup.sa_ok", path=str(path)); return
            except (OSError, ValueError):
                pass
            if self.yes:
                break
        self.row("FAIL", "setup.sa_missing", path=str(path))

    def step_config(self):
        self.writes = {"enabled": True, "firebase_url": self.db_url, "fcm_topic": str(R.get("fcm_topic") or "watch"),
                       "service_account": str(self.sa_path())}
        if getattr(self, "app", None):
            self.writes["firebase_app"] = self.app
        target = cm.config_path()
        if self.dry_run:
            self.row("WARN", "setup.config_would_write", path=str(target), keys=", ".join(f"relay.{k}" for k in self.writes)); return
        try:
            raw = json.loads(target.read_text()) if target.is_file() else {}
        except ValueError as e:
            raise SetupError(M("setup.config_bad", path=str(target), error=str(e))) from e
        relay = raw.get("relay") if isinstance(raw.get("relay"), dict) else {}
        if relay.get("fcm_topic"):
            self.writes["fcm_topic"] = relay["fcm_topic"]
        relay.update(self.writes)
        raw["relay"] = relay
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            shutil.copy(target, str(target) + ".bak")
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        self.row("PASS", "setup.config_written", path=str(target), keys=", ".join(f"relay.{k}" for k in self.writes))

    # ------------------------------------------------------------------ corsa
    def run(self):
        self.say("setup.header", mode=M("setup.mode_dry") if self.dry_run else M("setup.mode_live"))
        try:
            for step in (self.step_cli, self.step_project, self.step_database, self.step_rules, self.step_anonymous,
                         self.step_app, self.step_service_account, self.step_config):
                step()
                if step == self.step_project:   # bound method: `is` sarebbe sempre falso
                    self.persist_project()
        except SetupError as e:
            self.row("FAIL", "setup.stopped", error=str(e))
        fails = sum(1 for lv, _, _ in self.rows if lv == "FAIL")
        warns = sum(1 for lv, _, _ in self.rows if lv == "WARN")
        print()
        self.say("setup.summary_header", project=self.project or "-")
        for lv, key, kw in self.rows:
            self.say(f"setup.{lv.lower()}", detail=M(key, **kw))
        for line in self.manual:
            self.say("setup.manual_left", line=line)
        self.say("setup.summary", fails=fails, warns=warns)
        if not fails and not self.dry_run:
            self.say("setup.next")
        return 1 if fails else 0


def cm_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    os.chmod(path, mode)


def main(argv):
    # 30/09: prima --help e ogni opzione sconosciuta venivano ignorate e partiva la configurazione vera sul progetto
    if any(a in ("--help", "-h") for a in argv):
        print(M("setup.usage"))
        return 0
    project, dry_run, yes, i = None, False, False, 0
    while i < len(argv):
        a = argv[i]
        if a == "--project" and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            project = argv[i + 1]; i += 2; continue
        if a in ("--dry-run", "--yes"):
            dry_run, yes = dry_run or a == "--dry-run", yes or a == "--yes"; i += 1; continue
        print(M("setup.bad_option", opt=a), file=sys.stderr)
        print(M("setup.usage"), file=sys.stderr)
        return 2
    return Setup(project=project, dry_run=dry_run, yes=yes).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
