"""Adattatore finto per i test delle sessioni remote (CM_RSESSION_ADAPTER): l'host e' una cartella locale.

FAKE_WIN_ROOT    la cartella che fa da disco dell'host: il prefisso `W:` dei percorsi remoti diventa questa cartella
FAKE_WIN_NOREG=1 la sessione parte ma non si registra mai (Claude fermo su un dialogo)
Il registro peer finto sta in FAKE_WIN_ROOT/registry.json; ogni chiamata si annota in FAKE_WIN_ROOT/calls.jsonl.
talk: un messaggio rende la sessione busy; 1,5 s dopo la lettura successiva scrive nel transcript finto la risposta
«RISPOSTA: <testo>» e la rimette idle. FAKE_WIN_STATUS=nome:stato forza lo stato di una sessione.
09/10: session_tail legge i byte del transcript finto; session_console disegna il dialogo di FAKE_WIN_ROOT/
dialog-<nome>.json come la console di Windows (cursore «>») e muove il cursore con i tasti; Invio scrive la
scelta nel transcript (tool_result) e chiude il dialogo."""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("cm_adapters", Path(__file__).resolve().parents[2] / "supervisor" / "scripts" / "cm-adapters.py")
_ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ad)

ROOT = Path(os.environ["FAKE_WIN_ROOT"])


def local(rdir):
    return Path(str(rdir).replace("W:", str(ROOT), 1).replace("\\", "/"))


def note(verb, **kw):
    with open(ROOT / "calls.jsonl", "a") as f:
        f.write(json.dumps({"verb": verb, **kw}) + "\n")


def registry():
    p = ROOT / "registry.json"
    return json.loads(p.read_text()) if p.exists() else []


class FakeWin:
    def __init__(self, name, host, cfg):
        self.name = name

    def status(self):
        note("status")
        return {"ok": True, "sessions": [{"alive": not e.get("closed"), "entry": e} for e in registry()]}

    def session_prepare(self, rdir):
        note("prepare", rdir=rdir)
        d = local(rdir)
        m = d / ".cm-session.json"
        r = {"exists": d.exists(), "marker": m.exists()}
        if m.exists():
            j = json.loads(m.read_text())
            r.update(base=j["base"], root=j["root"], ahead=int(subprocess.run(["git", "-C", str(d), "rev-list", "--count", j["root"] + "..HEAD"],
                                                                                capture_output=True, text=True).stdout or 0))
        return r

    def session_seed(self, rdir, tar, sha, who):
        note("seed", rdir=rdir, sha=sha, who=list(who))
        d = local(rdir)
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-xf", str(tar), "-C", str(d)], check=True)
        g = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True).stdout  # noqa: E731
        g("init", "-q")
        for k, v in _ad.WindowsNative.SEED_GIT_CONFIG.items():   # la stessa config del seme vero
            g("config", k, v)
        g("config", "user.name", who[0] or "supervisor")
        g("config", "user.email", who[1] or "supervisor@localhost")
        with open(d / ".git" / "info" / "exclude", "a") as f:
            f.write(".cm-session.json\n")
        g("add", "-A")
        g("commit", "-q", "-m", f"supervisor: base {sha}")
        root = g("rev-parse", "HEAD").strip()
        (d / ".cm-session.json").write_text(json.dumps({"base": sha, "root": root}))
        return {"ok": True, "root": root}

    def night_list(self, prefix, dirs, since):
        """21/10: i transcript di FAKE_WIN_ROOT/projects/<prefix>*/ e i .md di docs/notte e docs/proposte di `dirs`,
        con i percorsi in forma W:…, come quelli di la'."""
        note("night_list", prefix=prefix, dirs=list(dirs), since=since)
        w = lambda p: "W:" + str(p)[len(str(ROOT)):]   # noqa: E731
        row = lambda f: {"path": w(f), "size": f.stat().st_size, "mtime": int(f.stat().st_mtime)}   # noqa: E731
        t = [row(f) for f in sorted((ROOT / "projects").glob(f"{prefix}*/*.jsonl")) if f.stat().st_mtime >= since] if prefix else []
        d = [row(f) for x in dirs for s in ("notte", "proposte") for f in sorted((local(x) / "docs" / s).glob("*.md")) if f.stat().st_mtime >= since]
        return {"transcripts": t, "docs": d}

    def file_get(self, path, dest):
        note("file_get", path=path)
        import shutil
        shutil.copyfile(local(path), dest)

    def session_trust(self, rdir):
        note("trust", rdir=rdir)
        return {"ok": True}

    def session_start(self, name, rdir, args):
        note("start", name=name, rdir=rdir, args=list(args))
        if not os.environ.get("FAKE_WIN_NOREG"):
            reg = registry()
            reg.append({"name": name, "sessionId": f"sid-{name}", "cwd": str(rdir), "bridgeSessionId": f"session_FAKE{len(reg)}"})
            (ROOT / "registry.json").write_text(json.dumps(reg))
        return {"ok": True}

    def session_patches(self, rdir, local_mbox):
        note("patches", rdir=rdir)
        d = local(rdir)
        root = json.loads((d / ".cm-session.json").read_text())["root"]
        n = int(subprocess.run(["git", "-C", str(d), "rev-list", "--count", root + "..HEAD"], capture_output=True, text=True).stdout)
        dirty = len([l for l in subprocess.run(["git", "-C", str(d), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines() if l.strip()])
        if n:
            with open(local_mbox, "wb") as f:
                subprocess.run(["git", "-C", str(d), "format-patch", "--stdout", root + "..HEAD"], stdout=f, check=True)
        return {"count": n, "dirty": dirty}

    # --- fase 2.2
    def _entry(self, name):
        for e in registry():
            if e["name"] == name:
                return e
        return None

    def _save(self, e):
        reg = [x if x["name"] != e["name"] else e for x in registry()]
        (ROOT / "registry.json").write_text(json.dumps(reg))

    def _tr(self, name):
        return ROOT / f"transcript-{name}.jsonl"

    def session_post(self, name, text, from_name, from_addr):
        note("post", name=name, text=text, from_name=from_name)
        e = self._entry(name)
        if not e or e.get("closed"):
            raise RuntimeError("not running")
        off = self._tr(name).stat().st_size if self._tr(name).exists() else 0
        e.update(status="busy", pending=text, posted=time.time())
        self._save(e)
        return {"ok": True, "offset": off, "status": "idle"}

    def session_read(self, name, offset, last=False):
        e = self._entry(name)
        if not e or e.get("closed"):
            return {"status": "gone", "alive": False, "texts": [], "offset": 0}
        if e.get("pending") is not None and time.time() - e["posted"] > 1.5:
            with open(self._tr(name), "a") as f:
                f.write(json.dumps({"type": "user", "message": {"content": e["pending"]}}) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "RISPOSTA: " + e["pending"]}]}}) + "\n")
            e.update(status="idle", pending=None)
            self._save(e)
        forced = dict(x.split(":", 1) for x in os.environ.get("FAKE_WIN_STATUS", "").split(",") if ":" in x)
        status = forced.get(name) or e.get("status") or "idle"
        data = self._tr(name).read_bytes() if self._tr(name).exists() else b""
        off = len(data) if offset == "end" else int(offset)
        texts = []
        for l in data[off:].decode().splitlines():
            d = json.loads(l)
            if d["type"] == "assistant":
                texts += [c["text"] for c in d["message"]["content"]]
        return {"status": status, "alive": True, "texts": texts[-1:] if last else texts, "offset": len(data)}

    def session_close(self, name, force=False):
        note("close", name=name, force=force)
        e = self._entry(name)
        if not e or e.get("closed"):
            return {"ok": True, "already": True}
        forced = dict(x.split(":", 1) for x in os.environ.get("FAKE_WIN_STATUS", "").split(",") if ":" in x)
        st = forced.get(name) or e.get("status") or "idle"
        if not force and st in ("busy", "waiting"):
            raise RuntimeError("remote: status " + st)
        e["closed"] = True
        self._save(e)
        return {"ok": True}

def _dialog_path(name):
    return ROOT / f"dialog-{name}.json"


def _draw(d):
    if not d or d.get("chosen"):
        return "\n> \n  bypass permissions on\n"
    rows = ["", " [ ] " + d["header"], "", d["question"], ""]   # come la console di Windows (09/10, dal vivo)
    labels = d["options"] + ["Type something.", "Chat about this"]
    for i, lab in enumerate(labels, 1):
        rows.append(("> " if i == d["cursor"] else "  ") + f"{i}. {lab}" + (" " + d.get("typed", "") if i == d["cursor"] and lab == "Type something." and d.get("typed") else ""))
    rows.append("Enter to select - Up/Down to navigate - Esc to cancel")
    return "\n".join(rows) + "\n"


def _tail(self, name, offset, max_bytes=2 * 1024 * 1024):
    note("tail", name=name, offset=offset)
    e = self._entry(name)
    if not e:
        raise _ad.HostError("remote", "not found")
    data = self._tr(name).read_bytes() if self._tr(name).exists() else b""
    off = min(int(offset), len(data))
    import base64
    return {"sid": e.get("sessionId"), "cwd": e.get("cwd"), "status": e.get("status") or "idle", "alive": not e.get("closed"),
            "size": len(data), "offset": off, "data": base64.b64encode(data[off:off + int(max_bytes)]).decode()}


def _console(self, name, keys=None):
    note("console", name=name, keys=list(keys or []))
    p = _dialog_path(name)
    d = json.loads(p.read_text()) if p.exists() else None
    for k in keys or []:
        if not d or d.get("chosen"):
            continue
        n = len(d["options"]) + 2
        if k == "Down":
            d["cursor"] = min(n, d["cursor"] + 1)
        elif k == "Up":
            d["cursor"] = max(1, d["cursor"] - 1)
        elif k.startswith("text:"):
            d["typed"] = d.get("typed", "") + k[5:]
        elif k == "Enter":
            d["chosen"] = d["cursor"]
            with open(self._tr(name), "a") as f:
                f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": d["id"], "content": f"chosen {d['cursor']}"}]}}) + "\n")
            e = self._entry(name)
            e["status"] = "busy"
            self._save(e)
    if d is not None:
        p.write_text(json.dumps(d))
    return {"pid": 4242, "screen": _draw(d)}


def _file_size(self, path):
    note("file_size", path=path)
    f = local(path)
    return f.stat().st_size if f.is_file() else -1


def _file_get(self, path, dest):
    note("file_get", path=path)
    Path(dest).write_bytes(local(path).read_bytes())


def _file_put(self, src, path):
    note("file_put", path=path)
    local(path).parent.mkdir(parents=True, exist_ok=True)
    local(path).write_bytes(Path(src).read_bytes())


FakeWin.file_size = _file_size
FakeWin.file_put = _file_put
FakeWin.file_get = _file_get
FakeWin.session_tail = _tail
FakeWin.session_console = _console


def adapter_for(name, host, cfg):
    return FakeWin(name, host, cfg)
