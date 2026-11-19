#!/usr/bin/env python3
"""cm-favicon — la favicon dei progetti che sono siti (18/11/2026, supervisor-favicon-siti-18-11: l'utente dalla web app,
«se il progetto riguarda un sito, al posto dell'icona accanto al nome potremmo mettere la favicon del sito»).

Un progetto e' un sito quando il nome della sua cartella e' un dominio (`sito.com`, `www.studio.it`). La favicon la
scarica questo PC dal sito stesso, mai da un servizio di terzi: l'elenco dei clienti non deve uscire. Ordine: i
`<link rel="icon">` della pagina principale (anche «shortcut icon»), poi `/favicon.ico`. L'icona di WordPress (quella che
WordPress mette quando il sito non ne ha una sua, wp-includes/images/w-logo-…) non conta. SVG no: non si rimpicciolisce
senza strumenti in piu'.

Salvata una volta, PNG 64×64, in <relay.dir>/favicons/<dominio>.json: {domain, at, src, hash, mime, data (base64), width,
height}; senza favicon {domain, at, why} (why: none, wordpress, fetch_failed, no_tool). Si rinfresca ogni settimana
(WEEK_S); dopo un errore di rete si riprova dopo un giorno. La push porta solo `hash`; l'immagine la chiede l'app una
volta con l'op `favicon` (by_hash) e la tiene.

19/11/2026 (supervisor-favicon-scaricate-19-11): non ne arrivava nessuna, tutte fetch_failed. SiteGround chiudeva la
connessione allo UA «Mozilla/5.0 (compatible; supervisor-favicon)» (e rifiuta anche lo UA completo di Chrome, per
l'impronta TLS); a «Mozilla/5.0» risponde. Quindi: UA semplice; se urllib fallisce, un secondo tentativo con curl (altra
impronta TLS); poche richieste e lente (PACE_S fra una e l'altra, al piu' MAX_LINKS icone per sito), perche' il blocco
per IP di SiteGround colpisce tutti i siti del PC; `refresh --force` rifa' subito anche quelli recenti.

Uso: cm-favicon.py refresh [--force] DOMINIO…   (il relay lo lancia in un processo a parte, senza --force)
     cm-favicon.py get DOMINIO | list
CM_FAVICON_URL (per i test): la radice del sito al posto di https://{domain}. CM_FAVICON_PACE_S: la pausa (test).
"""
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZE = 64
MAX_PAGE = 512 * 1024
MAX_ICON = 512 * 1024
MAX_OUT = 16 * 1024
TIMEOUT_S = 10
WEEK_S = 7 * 86400
RETRY_S = 86400
UA = "Mozilla/5.0"   # 19/11: con «(compatible; …)» SiteGround chiude la connessione
PACE_S = 2.0         # fra una richiesta e l'altra: il blocco per IP di SiteGround colpisce tutti i siti del PC
MAX_LINKS = 3        # icone dalla pagina provate al piu', poi /favicon.ico
_LAST = [0.0]
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
NOT_TLD = {"md", "py", "js", "ts", "json", "txt", "sh", "html", "htm", "css", "log", "bak", "old", "tmp", "git", "lock",
           "yml", "yaml", "zip", "tar", "gz", "pdf", "png", "jpg", "jpeg", "svg", "csv", "xml", "local", "test",
           "example", "invalid", "localhost", "orig", "new", "copy", "backup", "draft", "wip", "dev"}
WP_ICON = "/wp-includes/images/w-logo"


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)


def cache_dir():
    d = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay")) / "favicons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_of(cwd):
    """Il dominio se il nome della cartella lo e', altrimenti None."""
    name = os.path.basename(str(cwd or "").rstrip("/\\")).lower()
    if not DOMAIN_RE.match(name) or name.rsplit(".", 1)[-1] in NOT_TLD:
        return None
    return name


def cache_path(domain):
    return cache_dir() / f"{domain}.json"


def read(domain):
    try:
        x = json.loads(cache_path(domain).read_text())
        return x if isinstance(x, dict) else None
    except (OSError, ValueError):
        return None


def hash_for(domain):
    x = read(domain) if domain else None
    return (x or {}).get("hash") or None


def stale(domain, now=None):
    x = read(domain)
    if not x:
        return True
    age = (now or time.time()) - float(x.get("at") or 0)
    return age > (RETRY_S if x.get("why") == "fetch_failed" else WEEK_S)


def by_hash(h):
    h = str(h or "").strip()
    if not re.fullmatch(r"[0-9a-f]{6,64}", h):
        return None
    for p in cache_dir().glob("*.json"):
        try:
            x = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(x, dict) and x.get("hash") == h:
            return x
    return None


def _root(domain):
    t = os.environ.get("CM_FAVICON_URL")
    return (t.replace("{domain}", domain) if t else f"https://{domain}").rstrip("/")


def _join(root, href):
    href = href.strip()
    if re.match(r"^https?://", href, re.I):
        return href
    if href.startswith("//"):
        return urllib.parse.urlsplit(root).scheme + ":" + href
    if href.startswith("/"):
        return root + href
    return urllib.parse.urljoin(root + "/", href)


def _pace():
    wait = float(os.environ.get("CM_FAVICON_PACE_S") or PACE_S) - (time.monotonic() - _LAST[0])
    if wait > 0:
        time.sleep(wait)
    _LAST[0] = time.monotonic()


def _get_urllib(url, cap):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        data = r.read(cap + 1)
        return r.geturl(), (r.headers.get("Content-Type") or "").split(";")[0].strip().lower(), data[:cap] if len(data) <= cap else None


def _get_curl(url, cap):
    """Il secondo tentativo: curl ha un'altra impronta TLS, e alcuni hosting lasciano passare lui e non Python."""
    curl = shutil.which("curl")
    if not curl:
        raise OSError("no curl")
    with tempfile.NamedTemporaryFile(prefix="cm-fav-", delete=False) as f:
        out = f.name
    try:
        p = subprocess.run([curl, "-sS", "-L", "--max-redirs", "5", "-A", UA, "-H", "Accept: */*", "--max-time", str(TIMEOUT_S),
                            "--max-filesize", str(cap + 1), "-o", out, "-w", "%{http_code}\n%{url_effective}\n%{content_type}", url],
                           capture_output=True, text=True, timeout=TIMEOUT_S + 5)
        code, final, ctype = (p.stdout.split("\n") + ["", "", ""])[:3]
        if p.returncode != 0 or not code.startswith("2"):
            raise OSError(f"curl {p.returncode} http {code}")
        data = Path(out).read_bytes()
        return final or url, ctype.split(";")[0].strip().lower(), data[:cap] if len(data) <= cap else None
    finally:
        Path(out).unlink(missing_ok=True)


def _get(url, cap):
    """urllib, poi curl; una richiesta per volta, a PACE_S di distanza."""
    _pace()
    try:
        return _get_urllib(url, cap)
    except urllib.error.HTTPError as e:
        if e.code < 500:
            raise   # un 404 e' una risposta: rifarla con curl e' solo una richiesta in piu'
    except (OSError, urllib.error.URLError, ValueError):
        pass   # connessione chiusa, TLS rifiutato: il secondo tentativo con curl
    _pace()
    return _get_curl(url, cap)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        if "icon" in rel and a.get("href") and not a["href"].startswith("data:"):
            self.icons.append((a["href"], a.get("type", "").lower(), a.get("sizes", "").lower()))


def _size_key(sizes):
    m = re.search(r"(\d+)x(\d+)", sizes or "")
    n = int(m.group(1)) if m else 0
    return (0 if n >= SIZE else 1, abs(n - SIZE) if n else 999)   # prima le grandi almeno 64, la piu' vicina


def candidates(domain):
    """Gli URL da provare, in ordine: i link rel=icon (non SVG; prima i piu' adatti a 64 px), poi /favicon.ico."""
    root = _root(domain)
    out = []
    try:
        final, ctype, page = _get(root + "/", MAX_PAGE)
    except (OSError, urllib.error.URLError, ValueError):
        final, page = None, None
    if final and not os.environ.get("CM_FAVICON_URL"):
        sp = urllib.parse.urlsplit(final)   # https://sito.com → https://www.sito.com/: i percorsi valgono sul sito finale
        root = f"{sp.scheme}://{sp.netloc}"
    if page:
        p = _Links()
        try:
            p.feed(page.decode("utf-8", "replace"))
        except Exception:   # noqa: BLE001 — HTML rotto: si va a /favicon.ico
            pass
        icons = [x for x in p.icons if "svg" not in x[1] and not x[0].lower().split("?")[0].endswith(".svg")]
        for href, _t, _s in sorted(icons, key=lambda x: _size_key(x[2]))[:MAX_LINKS]:
            out.append(_join(root, href))
    out.append(root + "/favicon.ico")
    seen = set()
    return [u for u in out if not (u in seen or seen.add(u))], page is not None


def _png64(data):
    """(png, w, h) 64×64 da un'immagine qualsiasi che PIL apre (ICO: la piu' grande), o None."""
    try:
        from PIL import Image
    except ImportError:
        return "no_tool"
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "format", "") == "ICO" and hasattr(im, "ico"):
            try:
                im.size = max(im.ico.sizes())
            except Exception:   # noqa: BLE001
                pass
        im = im.convert("RGBA")
        im.thumbnail((SIZE, SIZE), Image.LANCZOS) if max(im.size) > SIZE else None
        if max(im.size) < SIZE:
            k = SIZE / max(im.size)
            im = im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))), Image.LANCZOS)
        out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        out.paste(im, ((SIZE - im.size[0]) // 2, (SIZE - im.size[1]) // 2))
        b = io.BytesIO()
        out.save(b, "PNG", optimize=True)
        return b.getvalue()
    except Exception:   # noqa: BLE001 — un formato che PIL non apre: si prova il prossimo
        return None


def fetch(domain):
    """Il record da salvare: con hash e data, o con why."""
    urls, reached = candidates(domain)
    why = "none" if reached else "fetch_failed"
    for u in urls:
        if WP_ICON in u:
            why = "wordpress"
            continue
        try:
            final, ctype, data = _get(u, MAX_ICON)
        except (OSError, urllib.error.URLError, ValueError):
            continue
        if WP_ICON in final:
            why = "wordpress"
            continue
        if not data or ctype.startswith("text/") or "svg" in ctype:
            continue
        png = _png64(data)
        if png == "no_tool":
            why = "no_tool"
            break
        if not png or len(png) > MAX_OUT:
            continue
        return {"domain": domain, "at": time.time(), "src": final, "hash": hashlib.sha256(png).hexdigest()[:12],
                "mime": "image/png", "data": base64.b64encode(png).decode(), "width": SIZE, "height": SIZE}
    return {"domain": domain, "at": time.time(), "why": why}


def refresh(domain):
    rec = fetch(domain)
    p = cache_path(domain)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, p)
    return rec


def refresh_due(domains, force=False):
    """Rinfresca quelli vecchi (piu' di una settimana, o un giorno dopo un errore); con force tutti. Un solo processo alla
    volta."""
    import fcntl
    lk = open(cache_dir() / ".lock", "w")
    try:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return []
        done = []
        for d in dict.fromkeys(x for x in domains if x and DOMAIN_RE.match(x)):
            if force or stale(d):
                refresh(d)
                done.append(d)
        return done
    finally:
        lk.close()


def main(argv):
    if argv[:1] == ["refresh"] and [a for a in argv[1:] if a != "--force"]:
        print(json.dumps(refresh_due([a for a in argv[1:] if a != "--force"], force="--force" in argv[1:])))
        return 0
    if argv[:1] == ["get"] and len(argv) == 2:
        x = read(argv[1]) or {}
        print(json.dumps({k: v for k, v in x.items() if k != "data"}, ensure_ascii=False))
        return 0
    if argv[:1] == ["list"]:
        for p in sorted(cache_dir().glob("*.json")):
            x = read(p.stem) or {}
            print(f"{p.stem}\t{x.get('hash') or x.get('why') or '?'}\t{time.strftime('%d/%m %H:%M', time.localtime(float(x.get('at') or 0)))}")
        return 0
    print(__doc__.split("Uso:", 1)[1].split("CM_FAVICON_URL", 1)[0].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
