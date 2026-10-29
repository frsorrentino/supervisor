"""Part of cm-relay.py: the commands the phone may run (the allow-list of OPS) and their execution.
Not a module of its own: cm-relay.py executes this file in its own namespace, at this point of its body,
so the names here share globals with cm-relay.py (the tests patch them there). Split out on 25/10/2026 to keep
every script under the 256 KiB the plugin directory reads; the code is unchanged."""

# ------------------------------------------------------------------ esecuzione dei comandi (allow-list)
def names_map():
    """nome nel contratto → nome tmux (dalla push piu' recente)."""
    return (read_json(rdir() / "last-state.json", {}).get("names") or {})


def tmux_of(session):
    return names_map().get(str(session or ""), str(session or ""))


def checkpoint(cwd):
    if not cwd or not os.path.isdir(os.path.join(cwd, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", cwd, "stash", "create"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def turn_status(ledger, sid):
    """busy se l'ultimo evento di turno della sessione e' un prompt (o ripresa), idle se e' uno stop o non c'e'."""
    ev = [x.get("event") for x in ledger if sid and x.get("session_id") == sid and x.get("event") in ("prompt", "start", "queue-pop", "stop")]
    return "busy" if ev and ev[-1] != "stop" else "idle"


def last_sessions():
    last = read_json(rdir() / "last-state.json", {})
    return {s["name"]: s for s in (last.get("full") or last.get("state") or {}).get("sessions", [])}


def last_message(row, cap=LAST_MAX):
    """L'ultimo messaggio dell'assistente di una sessione, dal transcript, INTERO fino a `cap` caratteri (oltre,
    si taglia a fine frase: all'ascolto conta l'inizio). Testo grezzo, markdown compreso: chi legge ripulisce.
    «» se non c'e' transcript o nessun messaggio. Il registro tiene solo 600 caratteri della coda, quindi per il
    testo intero la sorgente e' il transcript (contratto 1.4, per il tasto ▶ del polso, 14/09)."""
    path = core.transcript_of(row)
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    texts = []
    # gli ultimi 512 KB bastano per un turno lungo; se non c'e' nulla si rilegge tutto il file
    for window in (512 * 1024, size):
        events, _ = core.transcript_events(path, max(0, size - window))
        texts = [e[1] for e in events if e[0] == "text" and str(e[1]).strip()]
        if texts or window >= size:
            break
    if not texts:
        return ""
    text = str(texts[-1]).strip()
    if len(text) <= cap:
        return text
    cut = text[:cap]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    return (cut[:end + 1] if end > cap // 2 else cut).rstrip()


SHARE_TTL_S = 600    # 1.19: un /share/<id> che nessun comando ha letto sparisce dopo 10 minuti
REPORT_TEXT_MAX = 4000
IMAGE_EXT = {"image/jpeg": "jpg", "image/png": "png"}


def share_report(cmd, tm, session):
    """1.19 (29/09): «Condividi» dal telefono — testo e/o un'immagine verso una sessione viva, con `supervisor report
    <cartella> <file|-> <testo> --session <tmux>`. L'immagine sta cifrata in /share/<arg> ({mime, data base64}) e si
    cancella sempre, riuscito o no. 1.50: `arg` puo' portare fino a S.SHARE_MULTI id separati da virgola — un solo
    messaggio alla sessione (share_multi); ogni /share/<id> si cancella comunque."""
    arg = str(cmd.get("arg") or "").strip()
    sids = [x.strip() for x in arg.split(",")] if "," in arg else ([arg] if arg else [])
    try:
        if len(sids) > 1:
            return share_multi(cmd, tm, session, sids)
        return _share_report(cmd, tm, session, arg)
    finally:
        try:
            mark_done_now()   # 17/10: segnato prima di cancellare /share (dopo un riavvio, niente report doppio)
        except OSError:
            pass
        for sid in sids:
            if sid and cmd.get("_local"):
                if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sid):
                    for ext in (".bin", ".json"):
                        (local_dir("share") / f"{sid}{ext}").unlink(missing_ok=True)
            elif sid and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sid):
                try:
                    rtdb("DELETE", f"share/{sid}")
                except (urllib.error.URLError, OSError, ValueError):
                    pass
                seen = read_json(rdir() / "share-seen.json", {})
                if seen.pop(sid, None) is not None:
                    write_json(rdir() / "share-seen.json", seen)


def share_fetch(cmd, sid):
    """L'allegato /share/<sid> (o quello della web locale): (True, (mime, ext, bytes, meta)) — ext solo per le
    immagini di IMAGE_EXT —, o (False, ragione) come nel report di sempre."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
        return False, M("relay.cmd_report_no_image")
    blob = None
    if cmd.get("_local"):
        # 1.35: l'allegato della web app locale, posato in chiaro da POST /api/share/<id>
        blob = read_json(local_dir("share") / f"{sid}.json", None)
        mime = str((blob or {}).get("mime") or "").strip().lower()
        ext = IMAGE_EXT.get(mime)
        try:
            data = (local_dir("share") / f"{sid}.bin").read_bytes() if blob and (ext or MIME_RE.fullmatch(mime)) else b""
        except OSError:
            data = b""
    elif isinstance(meta_doc := rtdb("GET", f"share/{sid}/meta"), dict):
        # 1.48: a pezzi, come /file della 1.34 al contrario
        ok_p, got = share_parts(sid, meta_doc)
        if not ok_p:
            return False, got
        blob, data = got
        mime = str(blob.get("mime") or "").strip().lower()
        ext = IMAGE_EXT.get(mime)
        if not (ext or MIME_RE.fullmatch(mime)):
            data = b""
    else:
        doc = rtdb("GET", f"share/{sid}")
        if isinstance(doc, dict) and len(str(doc.get("enc") or "")) > S.SHARE_MAX_BYTES:
            return False, M("relay.cmd_report_too_big", max=S.SHARE_MAX_BYTES)
        try:
            blob = C.decrypt(doc, key()) if isinstance(doc, dict) else None
            mime = str((blob or {}).get("mime") or "").strip().lower()
            ext = IMAGE_EXT.get(mime)
            data = base64.b64decode(str(blob["data"]), validate=True) if ext or MIME_RE.fullmatch(mime) else b""
        except (ValueError, KeyError, TypeError, binascii.Error):
            ext, data, mime = None, b"", ""
    if not data:
        return False, M("relay.cmd_report_no_image" if ext or not mime or mime.startswith("image/") else "relay.cmd_report_no_file")
    return True, (mime, ext, data, blob or {})


def _share_report(cmd, tm, session, sid):
    text = str(cmd.get("text") or "").strip()
    if not text and not sid:
        return False, M("relay.cmd_report_empty")
    if len(text) > REPORT_TEXT_MAX:
        return False, M("relay.cmd_report_long", max=REPORT_TEXT_MAX)
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if row and row.get("host") not in (None, "", "local"):
        return share_multi(cmd, tm, session, [sid] if sid else [], row)   # 10/10: una sessione su un altro host
    if not row or not row.get("cwd") or not os.path.isdir(row["cwd"]):
        return False, M("relay.cmd_report_no_session", name=session or "?")
    img = None
    if sid:
        ok_f, got = share_fetch(cmd, sid)
        if not ok_f:
            return False, got
        mime, ext, data, blob = got
        if not ext:
            return share_file(cmd, tm, session, row, mime, data, blob.get("name"), text)
        tmpd = rdir() / "share-tmp"
        tmpd.mkdir(parents=True, exist_ok=True)
        img = tmpd / f"{sid}.{ext}"
        img.write_bytes(data)
    t0 = time.time()
    claim = _claim(tm, None)
    try:
        rc, out = run_cm("report", row["cwd"], str(img) if img else "-", text or M("relay.report_image_only"), "--session", tm)
    finally:
        if img:
            img.unlink(missing_ok=True)
        if claim:
            claim.unlink(missing_ok=True)
    if rc != 0:
        return False, (out.splitlines() or ["report failed"])[0]
    try:   # 0319ab (21:43): anche la segnalazione e' un messaggio del relay; il testo lo scrive cm-report.sh
        _load("cm-inbox").mark_origin(tm, None, t0, "relay")
    except Exception:   # noqa: BLE001 — il messaggio e' partito: l'origine e' un'informazione in piu'
        pass
    saved = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().startswith(("image:", "immagine:"))), "")
    if saved:
        return True, M("relay.cmd_report_sent_image", name=session, file=os.path.relpath(saved, row["cwd"]))
    return True, M("relay.cmd_report_sent", name=session)


SHARE_PARTS_N = 50   # 1.48: /share/<id>/parts/<0..49>, as the RTDB rules


def share_parts(sid, meta_doc):
    """1.48 (09/10, asked by the app for files up to 50 MB from the phone): (True, (meta, bytes)) from /share/<sid>/meta,
    {v, enc} of {n, size, sha256, mime, name}, and /share/<sid>/parts/0..n-1, {v, enc} of the raw bytes
    (`C.encrypt_raw`; the app cuts FILE_PART_BYTES, at most SHARE_PARTS_N parts as the RTDB rules). Over S.SHARE_PARTS_MAX → «too large: <size> max <max>»
    before reading a part; a part missing or unreadable, n out of 1..SHARE_PARTS_N, more or fewer bytes than size, or
    another sha256 → «bad parts». The node goes away in share_report either way."""
    bad = (False, M("relay.cmd_report_bad_parts"))
    k_ = key()
    try:
        meta = C.decrypt(meta_doc, k_)
        n, size, sha = int(meta["n"]), int(meta["size"]), str(meta["sha256"])
    except (ValueError, KeyError, TypeError):
        return bad
    if size > S.SHARE_PARTS_MAX:
        return False, M("relay.cmd_file_too_large", size=size, max=S.SHARE_PARTS_MAX)
    if size < 0 or not 1 <= n <= SHARE_PARTS_N:
        return bad
    buf = bytearray()
    for i in range(n):
        try:
            buf += C.decrypt_raw(rtdb("GET", f"share/{sid}/parts/{i}"), k_)
        except (ValueError, TypeError):
            return bad
        if len(buf) > size:
            return bad
    if len(buf) != size or hashlib.sha256(buf).hexdigest() != sha:
        return bad
    return True, (meta, bytes(buf))


MIME_RE = re.compile(r"[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,126}")
SHARE_NAME_MAX = 120
SHARE_INBOX = ".claude-master-inbox"


def clean_name(name, mime):
    """1.28: il nome che arriva dal telefono, ripulito: solo l'ultimo pezzo del percorso, lettere, cifre e « ._-()+,@=»,
    spazi uniti, niente punto iniziale; None se ne resta niente o supera 120 caratteri. Senza nome: «file» con
    l'estensione del tipo."""
    if name is None or str(name).strip() == "":
        return "file" + (mimetypes.guess_extension(mime) or ".bin")
    n = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    n = "".join(ch for ch in n if ch.isalnum() or ch in " ._-()+,@=")
    n = " ".join(n.split()).lstrip(". ")
    return n if n and len(n) <= SHARE_NAME_MAX else None


def _human_size(n):
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


class Place:
    """Dove vanno gli allegati di una sessione: la sua cartella qui, o (10/10, dal telefono: «le sessioni win non
    ricevono allegati?») quella sull'host, scritta con l'adattatore (file_put, file_size) come i file della 0.8.9."""

    def __init__(self, row):
        self.cwd = str(row.get("cwd") or "")
        self.host = row.get("host") if row.get("host") not in (None, "", "local") else None
        self.a = _load("cm-hosts").adapter(self.host) if self.host else None
        self.sep = "\\" if self.host and core.WIN_ABS.match(self.cwd) else "/"
        self.cwd = self.cwd.replace("/", self.sep) if self.sep == "\\" else self.cwd

    def ok(self):
        return bool(self.cwd) and (hasattr(self.a, "file_put") if self.host else os.path.isdir(self.cwd))

    def join(self, *parts):
        return self.sep.join([self.cwd.rstrip("\\/")] + [str(x).replace("/", self.sep) for x in parts])

    def exists(self, path):
        return self.a.file_size(path) >= 0 if self.host else os.path.exists(path)

    def write(self, path, data, mode):
        if not self.host:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data)
            os.chmod(path, mode)
            return
        tmpd = rdir() / "share-tmp"
        tmpd.mkdir(parents=True, exist_ok=True)
        tmp = tmpd / f"put-{os.getpid()}-{time.time_ns()}"
        try:
            tmp.write_bytes(data)
            self.a.file_put(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def rel(self, path):
        return path[len(self.cwd.rstrip("\\/")) + 1:] if self.host else os.path.relpath(path, self.cwd)


def inbox_save(place, clean, data):
    """I byte di un file dal telefono in <cartella>/.claude-master-inbox/ (con un .gitignore «*»), solo per chi li ha."""
    ign = place.join(SHARE_INBOX, ".gitignore")
    if not place.exists(ign):
        place.write(ign, b"*\n", 0o644)
    stamp, n = time.strftime('%Y%m%d-%H%M%S'), 1
    dest = place.join(SHARE_INBOX, f"{stamp}-{clean}")
    while place.exists(dest):   # 1.50: due file con lo stesso nome nello stesso secondo
        n += 1
        dest = place.join(SHARE_INBOX, f"{stamp}-{n}-{clean}")
    place.write(dest, data, 0o600)
    return dest


def report_image_save(place, text, ext, data):
    """1.50: un'immagine archiviata come fa `supervisor report`: <cartella>/<report.subdir>/<data>-<slug>[-n].<ext>, 644."""
    slug = re.sub(r"[^a-z0-9àèéìòù]+", "-", text.lower())[:40].strip("-") or "report"
    sub = str((CFG.get("report") or {}).get("subdir") or "docs/reports")
    day, n = time.strftime("%Y-%m-%d"), 1
    dest = place.join(sub, f"{day}-{slug}.{ext}")
    while place.exists(dest):
        n += 1
        dest = place.join(sub, f"{day}-{slug}-{n}.{ext}")
    place.write(dest, data, 0o644)
    return dest


def share_multi(cmd, tm, session, sids, row=None):
    """1.50 (10/10, chiesto dall'app: «due allegati insieme vengono mostrati ancora su 2 post»): un report con 2..5
    allegati. Si leggono tutti prima di salvare qualcosa: uno mancante o rotto rifiuta il report intero con la sua
    ragione. Poi le immagini come `report`, gli altri file nella casella come share_file, e alla sessione UN messaggio:
    il testo e i percorsi, nell'ordine. 10/10: la stessa strada per ogni report verso una sessione su un altro host
    (anche 0 o 1 allegato), con i file scritti la' (Place)."""
    text = str(cmd.get("text") or "").strip()
    if len(sids) > S.SHARE_MULTI:
        return False, M("relay.cmd_report_too_many", max=S.SHARE_MULTI)
    if not text and not sids:
        return False, M("relay.cmd_report_empty")
    if len(text) > REPORT_TEXT_MAX:
        return False, M("relay.cmd_report_long", max=REPORT_TEXT_MAX)
    row = row or next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    place = Place(row) if row else None
    if not place or not place.ok():
        return False, M("relay.cmd_report_no_session", name=session or "?")
    got = []
    for sid in sids:
        ok_f, g = share_fetch(cmd, sid)
        if not ok_f:
            return False, g
        mime, ext, data, blob = g
        clean = None if ext else clean_name(blob.get("name"), mime)
        if not ext and not clean:
            return False, M("relay.cmd_report_bad_name")
        got.append((mime, ext, data, clean))
    lines, shown = [], []
    try:
        for mime, ext, data, clean in got:
            if ext:
                dest = report_image_save(place, text, ext, data)
                lines.append(M("report.prompt_image", path=str(dest)))
                shown.append(place.rel(dest))
            else:
                dest = inbox_save(place, clean, data)
                lines.append(M("relay.share_file", name=clean, mime=mime, size=_human_size(len(data)), path=str(dest)))
                shown.append(clean)
    except Exception as e:   # noqa: BLE001 — host irraggiungibile, scp fallito
        return False, M("relay.cmd_report_not_saved", error=str(e)[:200])
    msg = M("report.prompt", date=time.strftime("%d/%m/%Y"), text=text or M("relay.report_image_only"))
    rc, out = relay_talk(tm, prefix_for(cmd, tm) + " " + msg + ("\n" + "\n".join(lines) if lines else ""), "--no-wait")
    if rc != 0:
        return False, out.splitlines()[0] if out else "talk failed"
    if saved_in_inbox(out):
        return False, M("relay.cmd_inbox_only", name=session)
    if not got:
        return True, M("relay.cmd_report_sent", name=session)
    if all(ext for _, ext, _, _ in got):
        if len(got) == 1:
            return True, M("relay.cmd_report_sent_image", name=session, file=shown[0])
        return True, M("relay.cmd_report_sent_images", name=session, files=", ".join(shown))
    return True, M("relay.cmd_report_sent_file", name=session, file=", ".join(shown))


def share_file(cmd, tm, session, row, mime, data, name, text):
    """1.28 (03/10, chiesto dalla sessione dell'app, approvato dal maintainer): un file di qualunque formato dal
    telefono. I byte vanno nella cartella della sessione, in .claude-master-inbox/ (con un .gitignore «*»: mai in un
    commit), e la sessione riceve, con il prefisso del dispositivo, «ti ho mandato il file …: <percorso>» e il testo.
    Il file non si esegue: la sessione lo legge. Le immagini restano su `report` come prima."""
    clean = clean_name(name, mime)
    if not clean:
        return False, M("relay.cmd_report_bad_name")
    dest = inbox_save(Place(row), clean, data)
    msg = M("relay.share_file", name=clean, mime=mime, size=_human_size(len(data)), path=str(dest))
    rc, out = relay_talk(tm, prefix_for(cmd, tm) + " " + msg + ("\n\n" + text if text else ""), "--no-wait")
    if rc != 0:
        return False, out.splitlines()[0] if out else "talk failed"
    if saved_in_inbox(out):
        return False, M("relay.cmd_inbox_only", name=session)
    return True, M("relay.cmd_report_sent_file", name=session, file=clean)


def prune_share(now, node="share"):
    """Via i /share/<id> che nessun comando ha letto entro SHARE_TTL_S: le chiavi sono uuid senza istante, quindi si
    ricorda quando il relay li ha visti la prima volta (share-seen.json). 1.24: lo stesso per /file/<id> che il
    dispositivo non ha letto (e quindi non ha cancellato)."""
    try:
        keys = rtdb("GET", node, None, {"shallow": "true"}) or {}
    except (urllib.error.URLError, OSError, ValueError):
        return
    seen_p = rdir() / f"{node}-seen.json"
    seen = {k: v for k, v in read_json(seen_p, {}).items() if k in keys}
    for k in keys:
        seen.setdefault(k, now)
    old = [k for k, t in seen.items() if now - float(t) > SHARE_TTL_S]
    for k in old:
        try:
            rtdb("DELETE", f"{node}/{k}")
            seen.pop(k, None)
        except (urllib.error.URLError, OSError, ValueError):
            pass
    write_json(seen_p, seen)


def _fit_image(data, cap_enc):
    """Un'immagine troppo grande per la busta diventa JPEG, sempre piu' piccola, finche' la busta cifrata ci sta
    (base64 di base64 e AES-GCM: si stima dal JPEG e si verifica sulla busta vera). None se non ci sta mai."""
    from io import BytesIO
    try:
        from PIL import Image
        im = Image.open(BytesIO(data))
        im.load()
    except Exception:   # noqa: BLE001 — non e' un'immagine che PIL sa leggere
        return None
    im = im.convert("RGB")
    side = max(im.size)
    for edge, q in ((2048, 85), (1600, 80), (1280, 75), (1024, 70), (800, 65), (640, 60)):
        if edge < side:
            im2 = im.copy()
            im2.thumbnail((edge, edge))
        else:
            im2 = im
        buf = BytesIO()
        im2.save(buf, "JPEG", quality=q, optimize=True)
        out = buf.getvalue()
        if len(C.encrypt({"mime": "image/jpeg", "data": base64.b64encode(out).decode()}, key()).get("enc") or "") <= cap_enc:
            return out
    return None


FILE_PART_BYTES = 1024 * 1024        # 1.34: un pezzo, prima della cifratura (in /file circa 1,4 MB di base64)
FILE_PARTS_MAX = 25 * 1024 * 1024    # 1.34: il tetto di un file a pezzi
FILE_ONE_MAX = S.FILE_ENC_MAX * 9 // 16 - 100   # il file piu' grande che sta in un solo {v, enc} (due base64)


def file_parts(data, mime, name, k_, part=FILE_PART_BYTES):
    """1.34: (meta, [pezzi]) — meta = {v, enc} di {n, size, sha256, mime, name}; ogni pezzo = {v, enc} dei suoi byte
    grezzi (`C.encrypt_raw`)."""
    import hashlib
    chunks = [data[i:i + part] for i in range(0, len(data), part)] or [b""]
    meta = {"n": len(chunks), "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "mime": mime, "name": name}
    return C.encrypt(meta, k_), [C.encrypt_raw(c, k_) for c in chunks]


def file_open(cmd, session, tm, arg):
    """1.24 (02/10, chiesto dalla sessione dell'app, approvato dal maintainer alle 09:10): aprire dal telefono un file
    che compare nella conversazione. arg = il `path` esatto di transcript.entries[].files[]; si serve SOLO un percorso
    che compare nei `files` del transcript di quella sessione. Il file va in /file/<id del comando>, busta {v, enc}
    come /share, in chiaro {mime, data base64}; oltre S.FILE_ENC_MAX di `enc` un'immagine si riduce (JPEG), il
    resto si rifiuta. Il dispositivo lo cancella dopo averlo letto; il relay quelli non letti dopo SHARE_TTL_S (10 minuti)."""
    path = str(arg or "").strip()
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    tr = core.transcript_of(row) if row else None
    if not row or not tr:
        return False, M("relay.cmd_file_no_session", name=session or "?")
    size = os.path.getsize(tr)
    listed = False
    for window in TRANSCRIPT_WINDOWS:
        start = 0 if window is None or window >= size else size - window
        listed = any(fr.get("path") == path for e in core.transcript_entries(tr, start) for fr in (e.get("files") or []))
        if listed or not start:
            break
    if not path or not listed:
        return False, M("relay.cmd_file_not_listed")
    src, name = path, re.split(r"[\\/]", path)[-1]
    if row.get("host") and row.get("host") != "local":
        # 10/10: il file sta sull'host della sessione (D:\… su win): misurato la' contro FILE_PARTS_MAX, poi copiato
        # qui; da li' la strada di sempre, e la web locale riceve il percorso della copia
        try:
            got, size = _load("cm-hosts").fetch_file(row["host"], path, FILE_PARTS_MAX)
        except Exception:   # noqa: BLE001 — host irraggiungibile, scp fallito: per il telefono e' illeggibile
            got, size = None, -1
        if got is None:
            if size > FILE_PARTS_MAX:
                return False, M("relay.cmd_file_too_large", size=size, max=FILE_PARTS_MAX)
            return False, M("relay.cmd_file_unreadable")
        src = str(got)
    try:
        data = Path(src).read_bytes()
    except OSError:
        return False, M("relay.cmd_file_unreadable")
    import mimetypes
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    cid = str(cmd.get("id") or "")
    if cmd.get("_local"):
        # 1.35: la web app locale lo prende intero da GET /api/file/<id>, senza tetto ne' pezzi: qui solo il rimando
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", cid):
            return False, M("relay.cmd_file_unreadable")
        write_json(local_dir("file") / f"{cid}.json", {"path": src, "mime": mime, "name": name})
        return True, M("relay.cmd_file_ready", mime=mime, size=len(data))
    if cmd.get("parts") is True:
        # 1.34: a pezzi, fino a FILE_PARTS_MAX; senza `parts` (un'app vecchia) tutto come prima
        if len(data) > FILE_PARTS_MAX:
            return False, M("relay.cmd_file_too_large", size=len(data), max=FILE_PARTS_MAX)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", cid):
            return False, M("relay.cmd_file_unreadable")
        k_ = key()
        meta, parts = file_parts(data, mime, name, k_)
        for i, part in enumerate(parts):
            rtdb("PUT", f"file/{cid}/parts/{i}", part, {"print": "silent"})
        rtdb("PUT", f"file/{cid}/meta", meta, {"print": "silent"})   # per ultimo: con meta i pezzi ci sono gia' tutti
        return True, M("relay.cmd_file_ready", mime=mime, size=len(data))
    doc = C.encrypt({"mime": mime, "data": base64.b64encode(data).decode()}, key())
    if len(str(doc.get("enc") or "")) > S.FILE_ENC_MAX:
        small = _fit_image(data, S.FILE_ENC_MAX) if mime.startswith("image/") else None
        if small is None:
            return False, M("relay.cmd_file_too_large", size=len(data), max=FILE_ONE_MAX)
        mime, data = "image/jpeg", small
        doc = C.encrypt({"mime": mime, "data": base64.b64encode(data).decode()}, key())
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", cid):
        return False, M("relay.cmd_file_unreadable")
    rtdb("PUT", f"file/{cid}", doc, {"print": "silent"})
    return True, M("relay.cmd_file_ready", mime=mime, size=len(data))


SLASH_RE = re.compile(r"[a-z][a-z0-9-]{0,40}")
SLASH_TEXT_MAX = 2000
SLASH_NO_PANEL = ("compact", "clear", "exit")   # non aprono pannelli: niente attesa ne' Esc
SLASH_PANEL_MAX = 3500


def slash_allowed():
    return [str(x).strip().lstrip("/").lower() for x in (R.get("slash_commands") or []) if str(x).strip()]


def slash_send(cmd, session, tm, arg):
    """1.25 (02/10, chiesto dalla sessione dell'app, approvato dal maintainer alle 11:12): un comando slash dal
    telefono. arg = il comando senza «/», text = gli argomenti (o null). Solo i comandi di relay.slash_commands. Va
    DIGITATO nel pannello (talk --via tmux), senza prefisso: dal socket arriverebbe come messaggio di un'altra sessione e
    Claude Code non lo eseguirebbe. Una sessione al lavoro o ferma su un dialogo rifiuta: il testo finirebbe in coda o
    risponderebbe al dialogo. /exit si digita come gli altri: `close` rifiuta una sessione attaccata, dal telefono
    deve valere come la persona al terminale."""
    name = str(arg or "").strip().lstrip("/").lower()
    if not SLASH_RE.fullmatch(name) or name not in slash_allowed():
        return False, M("relay.cmd_slash_not_allowed", cmd=name or "?")
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if not row or not row.get("tmux"):
        return False, M("relay.cmd_slash_no_session", name=session or "?")
    if row.get("status") in ("busy", "waiting") or row.get("waiting"):
        return False, M("relay.cmd_slash_busy", name=session)
    text = " ".join(str(cmd.get("text") or "").split())[:SLASH_TEXT_MAX]
    saved = None
    if name == "exit":
        # 1.84 (24/10): /exit dall'app fa la strada di close — i controlli (anche il lavoro in sottofondo), lo stato
        # salvato, poi la chiusura; se il comando non parte il salvataggio va via
        chk, _ = close_check(session, [row], allow_attached=True)
        if not chk["closable"]:
            return False, M("relay.cmd_close_kept", name=session, why=M(f"relay.close_why_{chk['blocked_by']}"))
        saved = save_closed(chk, row, cmd.get("by"))
    rc, out = relay_talk(row["tmux"], f"/{name} {text}".rstrip(), "--via", "tmux", "--no-wait")
    if saved and rc != 0:
        closed_file(saved["tmux"]).unlink(missing_ok=True)
    if rc == 4:
        return False, M("relay.cmd_slash_typed", name=session)
    if rc != 0:
        return False, (out.splitlines() or ["slash failed"])[0]
    sent = M("relay.cmd_slash_sent", cmd=name, name=session)
    if name in SLASH_NO_PANEL:
        return True, sent
    # 02/10 (dal telefono): /cost, /usage, /status… aprono un pannello sopra la casella che resta aperto finche' qualcuno
    # preme Esc. `panel` lo legge, lo chiude con un Esc e il testo arriva al telefono nel risultato
    prc, pout = run_cm("panel", row["tmux"], "--wait", "6")
    if prc in (0, 4) and pout.strip():
        return True, sent + "\n\n" + pout.strip()[:SLASH_PANEL_MAX] + ("\n\n" + M("relay.cmd_slash_panel_open") if prc == 4 else "")
    return True, sent


def night_report_dir():
    return Path(cm.expand("~/.team-supervisor/night-report"))


def night_report_latest():
    """1.44 (08/10): {date, generated_at} dell'ultimo rapporto della notte, per night.report nello stato; None se non
    ce n'e' uno."""
    try:
        files = sorted(p for p in night_report_dir().glob("????-??-??.json"))
    except OSError:
        return None
    for p in reversed(files):
        rep_ = read_json(p, None)
        if isinstance(rep_, dict) and rep_.get("date"):
            return {"date": str(rep_["date"]), "generated_at": rep_.get("generated_at")}
    return None


def night_report(arg):
    """1.44 (chiesto dall'app per la pagina «Notte», approvata dal maintainer il 07/10 alle 21:50): il rapporto della notte
    (schema team-supervisor/night-report v1) cosi' com'e' su disco. arg = «AAAA-MM-GG», o null per l'ultimo."""
    day = str(arg or "").strip()
    if day:
        p = night_report_dir() / f"{day}.json"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not p.is_file():
            return False, f"no night report for {day}"
    else:
        files = sorted(night_report_dir().glob("????-??-??.json")) if night_report_dir().is_dir() else []
        if not files:
            return False, "no night report yet"
        p = files[-1]
    try:
        return True, p.read_text().strip()
    except OSError:
        return False, f"no night report for {day or p.stem}"


NIGHTS_MAX = 60


def nights_list():
    """1.74 (21/10, pagina «Notte» dell'app: prima l'ultima notte, sotto le precedenti): {nights: [{date, title,
    generated_at, v}]} dei rapporti su disco, il piu' recente per primo, al piu' NIGHTS_MAX. title e' quello del
    rapporto (v2, «9 progetti: 7 fatti, 2 in parte»), null per i v1. Nessun rapporto: nights vuoto, ok."""
    try:
        files = sorted(night_report_dir().glob("????-??-??.json"), reverse=True)
    except OSError:
        files = []
    out = []
    for p in files[:NIGHTS_MAX]:
        rep_ = read_json(p, None)
        if isinstance(rep_, dict) and rep_.get("date"):
            out.append({"date": str(rep_["date"]), "title": rep_.get("title"), "generated_at": rep_.get("generated_at"), "v": rep_.get("v")})
    return True, json.dumps({"nights": out}, ensure_ascii=False)


DONE_MAX_BYTES = 60 * 1024   # 1.77: le righe di `done` oltre questo si tagliano dal fondo (more: true)


def done_list(arg):
    """1.77 (22/10, blocco «Fatto» della pagina Lavori, mockup approvato dal maintainer alle 12:43): i compiti chiusi nel
    giorno `arg` (ISO, null = oggi) con la prova (release, commit, file o controllo) e fino a sei giorni prima con un
    conteggio, da `task closed --json`; project relativo come sessions[].project; senza registro ok false."""
    day = str(arg or "").strip()
    if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return False, M("relay.cmd_done_bad_date", arg=day)
    rc, out = run_cm("task", "closed", *([day] if day else []), "--json")
    if rc == 3:
        return False, M("relay.cmd_done_no_registry")
    if rc != 0 or not out.strip().startswith("{"):
        return False, (out.splitlines() or ["task closed failed"])[-1]
    d = json.loads(out)
    root = cm.expand(CFG["workspace"]["root"])
    rows = []
    for x in d.get("rows") or []:
        y = dict(x)
        y["project"] = S.project_of(x.get("project") or "", root)
        y["title"] = S.one_line(str(x.get("title") or ""))[:200]
        y["short"] = short_title(x.get("short") or y["title"])   # 1.79
        rows.append(y)
    res = {"date": d.get("date"), "rows": rows, "days": list(d.get("days") or []), "more": False}
    while rows and len(json.dumps(res, ensure_ascii=False).encode()) > DONE_MAX_BYTES:
        rows.pop()
        res["more"] = True
    return True, json.dumps(res, ensure_ascii=False)


AGENDA_FIELDS = ("state", "scope", "blocks", "title", "ref", "detail", "until", "short")   # 1.79: column 8, optional
AGENDA_CLOSED = ("fatto", "scartato")
AGENDA_ACTIONS = {"done": "fatto", "snooze": "sospeso", "remove": "scartato", "pass": None}
AGENDA_BACKUPS = 30
AGENDA_LOCK_S = 5


def agenda_owner():
    """1.47: the name the agenda gives its owner in the column «blocks» (relay.agenda_owner, «owner» when unset):
    Passa writes it or «claude»."""
    return str((CFG.get("relay") or {}).get("agenda_owner") or "owner").strip() or "owner"


def agenda_path():
    f = (CFG.get("relay") or {}).get("agenda_file") or ""
    p = Path(cm.expand(f)) if f else None
    return p if p and p.is_file() else None


def agenda_is_row(line):
    return bool(line.strip()) and not line.lstrip().startswith("#")


def agenda_keys(lines):
    """1.47: the key of each row, chosen here — the first 12 hex of the sha1 of the line as written, «-2», «-3»… on a
    repeated line. It stays the same while the line does, whatever happens to the others; a changed line has a new
    key, so a write with the old one fails and the app reloads."""
    seen, keys = {}, {}
    for i, line in enumerate(lines):
        if not agenda_is_row(line):
            continue
        h = hashlib.sha1(line.rstrip("\r\n").encode("utf-8")).hexdigest()[:12]
        seen[h] = seen.get(h, 0) + 1
        keys[i] = h if seen[h] == 1 else f"{h}-{seen[h]}"
    return keys


def agenda_row(line, key):
    """A TSV line as the op shows it: «chiuso» reads as «fatto»; detail with its «\n» turned into line breaks, or null;
    until only as AAAA-MM-GG, or null. 1.79: `short`, column 8 when written (the master writes it), else derived from
    the title (whole words, at most 28). Columns past the eighth are not read."""
    cells = [c.strip() for c in line.rstrip("\r\n").split("\t")]
    cells += [""] * (len(AGENDA_FIELDS) - len(cells))
    got = dict(zip(AGENDA_FIELDS, cells[:len(AGENDA_FIELDS)]))
    row = {}
    for k in AGENDA_FIELDS[:-1]:   # `short` right after `title`, as in commitments and done
        row[k] = got[k]
        if k == "title":
            row["short"] = short_title(got["short"] or got["title"])
    if row["state"] == "chiuso":
        row["state"] = "fatto"
    row["detail"] = row["detail"].replace("\\n", "\n") or None
    row["until"] = row["until"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["until"]) else None
    return dict(key=key, **row)


def agenda_list():
    """1.46 (08/10, asked by the app for its Recap, approved by the maintainer at 20:41): the decisions and work of
    relay.agenda_file, a TSV; lines starting with «#» and blank lines are comments. A read on request like `projects`,
    never in /state. text = JSON {rows, more, owner}, in file order. 1.47 (09/10, «Azioni» on the Recap cards, approved
    by the maintainer): columns 6 and 7, detail and until, the state «scartato», each row's key for `agenda_set`, and
    `owner`, the «blocks» value that means the owner (relay.agenda_owner). Past
    TRANSCRIPT_MAX_BYTES the closed rows (fatto, scartato) go first, from the end, then the others; more = true."""
    p = agenda_path()
    if not p:
        return False, "no agenda file"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    keys = agenda_keys(lines)
    rows = [agenda_row(lines[i], keys[i]) for i in sorted(keys)]
    more = False
    while rows and len(json.dumps({"rows": rows, "more": True, "owner": agenda_owner()}, ensure_ascii=False).encode()) > TRANSCRIPT_MAX_BYTES:
        closed = [i for i, r in enumerate(rows) if r["state"] in AGENDA_CLOSED]
        rows.pop(closed[-1] if closed else -1)
        more = True
    return True, json.dumps({"rows": rows, "more": more, "owner": agenda_owner()}, ensure_ascii=False)


def agenda_set(cmd, arg):
    """1.47 (09/10, approved by the maintainer): the Recap's Fatto, Rimanda, Rimuovi and Passa on one row of the agenda.
    arg = the row's key from `agenda`; `action`: done (state fatto), snooze (sospeso, with `until` AAAA-MM-GG in
    column 7), remove (scartato: kept in the file, the app hides it) or pass (`blocks`: claude, or the `owner` of
    `agenda`, in column 3).
    One writer at a time (relay dir agenda.lock); before each write a copy in relay dir agenda-backup/, the last
    AGENDA_BACKUPS kept; only that line changes — order, comments, blank lines and line ends stay as they are, and the
    file is replaced in one step. A key no longer in the file → «agenda row changed»: the app reloads.
    text = JSON {row} with the row as `agenda` now shows it, new key included."""
    key = str(arg or "").strip()
    action = str(cmd.get("action") or "")
    if action not in AGENDA_ACTIONS:
        return False, f"bad agenda action: {action or '?'}"
    until = str(cmd.get("until") or "").strip()
    blocks = str(cmd.get("blocks") or "").strip()
    if action == "snooze":
        try:
            ok_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", until)) and bool(time.strptime(until, "%Y-%m-%d"))
        except ValueError:
            ok_date = False
        if not ok_date:
            return False, f"bad until date: {until or '?'}"
    if action == "pass" and blocks not in ("claude", agenda_owner()):
        return False, f"bad blocks: {blocks or '?'}"
    p = agenda_path()
    if not p:
        return False, "no agenda file"
    lock = open(str(rdir() / "agenda.lock"), "w")
    try:
        t_end = time.time() + AGENDA_LOCK_S
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > t_end:
                    return False, "agenda busy"
                time.sleep(0.1)
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        keys = agenda_keys(lines)
        at = next((i for i, k in keys.items() if k == key), None)
        if at is None:
            return False, "agenda row changed"
        body = lines[at].rstrip("\r\n")
        end = lines[at][len(body):]
        cells = body.split("\t")
        if action == "pass":
            cells += [""] * (3 - len(cells))
            cells[2] = blocks
        else:
            cells[0] = AGENDA_ACTIONS[action]
            if action == "snooze":
                cells += [""] * (7 - len(cells))
                cells[6] = until
        lines[at] = "\t".join(cells) + end
        bdir = rdir() / "agenda-backup"
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 0
        while (bdir / f"agenda-{stamp}-{n:02d}.tsv").exists():   # names sort as written: the oldest go first
            n += 1
        (bdir / f"agenda-{stamp}-{n:02d}.tsv").write_bytes(raw)
        for old in sorted(bdir.glob("agenda-*.tsv"))[:-AGENDA_BACKUPS]:
            old.unlink(missing_ok=True)
        if p.read_bytes() != raw:   # a hand edit between the read and now: never overwrite it
            return False, "agenda row changed"
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        shutil.copymode(p, tmp)
        os.replace(tmp, p)
        new_lines = "".join(lines).splitlines()
        return True, json.dumps({"row": agenda_row(new_lines[at], agenda_keys(new_lines)[at])}, ensure_ascii=False)
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


STUDIO_DAYS = 60       # 1.73: le scadenze della pagina Studio e del blocco [studio], da oggi
STUDIO_OUTCOMES = 3    # 1.73: gli esiti per cartella di pratica


def studio_calendar():
    """cm-calendar con la sezione `calendar` di questa config (i test la cambiano su CFG)."""
    cal = _load("cm-calendar")
    cal.C = CFG.get("calendar") or cal.C
    return cal


def studio_cases(cal):
    """Le pratiche di calendar.cases nell'ordine del file: [{id, name, project, dir}] (dir espansa)."""
    root = cm.expand(CFG["workspace"]["root"])
    out = []
    for cid, v in (cal.C.get("cases") or {}).items():
        d = cm.expand(cal.case_dir(v)) if cal.case_dir(v) else ""
        out.append({"id": str(cid), "name": cal.case_name(v), "project": S.project_of(d, root), "dir": d})
    return out


def studio_outcomes(dirs, now):
    """Gli ultimi STUDIO_OUTCOMES esiti («Esito:» a fine turno, come la timeline) delle sessioni di ogni cartella, vive o
    chiuse, degli ultimi STUDIO_DAYS giorni: [{project, session, at, short}], cartella per cartella, il piu' nuovo prima.
    Si leggono solo le trascrizioni della cartella (projects/<cartella con «-»>), dalla piu' recente: una trascrizione
    toccata prima del terzo esito trovato non ne ha di piu' nuovi, e li' ci si ferma."""
    tl = _load("cm-timeline")
    since = int(now - STUDIO_DAYS * 86400)
    live = {}
    for r in _json_cmd("sessions", "--json", "--no-screen") or []:
        t = core.transcript_of(r)
        if t:
            live[os.path.realpath(t)] = S.short_name(r.get("name") or r.get("tmux") or "", prefixes())
    root = cm.expand(CFG["workspace"]["root"])
    out = []
    for d in dirs:
        here = os.path.realpath(d)
        files = []
        for acc in (CFG.get("accounts") or {}).values():
            for enc in {re.sub(r"[^A-Za-z0-9]", "-", x) for x in (d.rstrip("/"), here)}:   # Claude Code: ogni altro segno → «-»
                for f in (Path(cm.expand(acc.get("config_dir") or "~/.claude")) / "projects" / enc).glob("*.jsonl"):
                    try:
                        files.append((f.stat().st_mtime, os.path.realpath(str(f))))
                    except OSError:
                        continue
        found = []
        for mtime, f in sorted(set(files), reverse=True):
            if mtime < since or (len(found) >= STUDIO_OUTCOMES and mtime < found[STUDIO_OUTCOMES - 1]["at"]):
                break
            try:
                with open(f, "rb") as h:
                    m = re.search(rb'"cwd":\s*("(?:[^"\\]|\\.)*")', h.read(65536))
                cwd = json.loads(m.group(1)) if m else ""
                if os.path.realpath(cwd) != here:
                    continue   # un'altra cartella con lo stesso nome codificato («a/b» e «a-b»)
                ev = tl.transcript_events(f, since)
            except (OSError, ValueError):
                continue
            name = live.get(f) or os.path.basename(here)
            found += [{"project": S.project_of(here, root), "session": name, "at": e["at"], "short": e["text"]}
                      for e in ev if e["kind"] == "outcome"]
            found.sort(key=lambda e: -e["at"])
        out += found[:STUDIO_OUTCOMES]
    return out


def studio_page():
    """1.73 (18/10, pagina Studio dell'app, piano approvato dal maintainer): a richiesta come `agenda`, mai in /state.
    text = JSON {cases, deadlines, outcomes, error}: cases = calendar.cases nell'ordine del file {id, name, project};
    deadlines = le scadenze dei prossimi STUDIO_DAYS giorni {uid, summary, date, days_left, due, case}, la piu'
    vicina prima, anche senza pratica (case «»); outcomes = gli ultimi esiti di ogni cartella delle pratiche, una
    volta per cartella; error = il testo di cm-calendar se il feed non risponde (scadenze dalla copia in cache o
    nessuna). Calendario spento: ok lo stesso, pratiche ed esiti, nessuna scadenza, error «calendar off» (addendum
    dell'app del 18/10: la pagina mostra le pratiche anche senza calendario). Gli uid si possono ripetere (eventi
    ricorrenti): la chiave e' uid + date, mai si toglie un doppione per uid."""
    cal = studio_calendar()
    text, err = cal.fetch() if cal.url() else ("", "calendar off")
    cases = studio_cases(cal)
    deadlines = cal.upcoming(cal.parse(text), STUDIO_DAYS) if text else []
    dirs = list(dict.fromkeys(c["dir"] for c in cases if c["dir"]))
    now = float(os.environ.get("CM_RELAY_NOW") or time.time())
    page = {"cases": [{k: c[k] for k in ("id", "name", "project")} for c in cases], "deadlines": deadlines,
            "outcomes": studio_outcomes(dirs, now), "error": err}
    return True, json.dumps(page, ensure_ascii=False)


def studio_block():
    """1.73: il contesto di un prompt con scope «studio», messo prima del testo: le pratiche con la cartella, le
    scadenze dei prossimi STUDIO_DAYS giorni e la regola di calendar.studio_rule, fra [studio] e [/studio]."""
    cal = studio_calendar()
    cases = studio_cases(cal)
    names = {c["id"]: c["name"] for c in cases}
    lines = ["[studio]", M("relay.studio_cases")]
    lines += [f"- {c['name']} → {cm.contract(c['dir']) if c['dir'] else '?'}" for c in cases]
    if not cal.url():
        lines.append(M("relay.studio_deadlines_off"))
    else:
        text, err = cal.fetch()
        items = cal.upcoming(cal.parse(text), STUDIO_DAYS) if text else []
        lines.append(M("relay.studio_deadlines", n=STUDIO_DAYS) + (f" ({err})" if err else ""))
        for e in items:
            who = names.get(e["case"]) or e["case"]
            lines.append(f"- {e['date'][8:10]}/{e['date'][5:7]} {e['summary'] or '?'}" + (f" · {who}" if who else ""))
        if not items:
            lines.append("- " + M("relay.studio_none"))
    rule = " ".join(str(cal.C.get("studio_rule") or "").split())
    if rule:
        lines.append(M("relay.studio_rule", rule=rule))
    return "\n".join(lines + ["[/studio]"])


def with_scope(cmd, text):
    """1.73: `scope` del prompt: «studio» → il blocco [studio]…[/studio], una riga vuota, il testo. Uno scope
    sconosciuto, o assente, lascia il testo com'e'; il blocco non deve mai far perdere il prompt."""
    if cmd.get("scope") != "studio":
        return text
    try:
        return studio_block() + "\n\n" + text
    except Exception as e:   # noqa: BLE001 — config o calendario rotti: il prompt parte senza contesto
        log(f"studio: blocco non costruito ({type(e).__name__}): prompt senza scope")
        return text


def projects_list():
    """1.26 (02/10, chiesto dalla sessione dell'app, approvato dal maintainer alle 17:15): l'elenco completo dei progetti
    per «Lancia». /state li taglia a 10 e poi a 5 per stare negli 8 KB; qui ci sono tutti, di tutti gli account, con i
    campi di state.projects, dal piu' recente (last_used; i null in fondo) e poi per nome. text = JSON {projects, more};
    oltre TRANSCRIPT_MAX_BYTES si tolgono i meno recenti e more = true."""
    rows = [{"name": p.get("name", ""), "path": p.get("path", ""), "account": p.get("account", ""),
             "last_used": p.get("last_used") if isinstance(p.get("last_used"), int) else None} for p in inventory()]
    rows.sort(key=lambda p: (p["last_used"] is None, -(p["last_used"] or 0), p["name"]))
    more = False
    while rows and len(json.dumps({"projects": rows, "more": True}, ensure_ascii=False).encode()) > TRANSCRIPT_MAX_BYTES:
        rows.pop()
        more = True
    return True, json.dumps({"projects": rows, "more": more}, ensure_ascii=False)


SEARCH_MAX_HITS = 50
SEARCH_MAX_S = 10
SEARCH_DAYS = 7
SEARCH_SNIPPET = 160
_SEARCH_CACHE = {}   # path -> {size, mtime, end, cwd, entries: [(id, role, at, testo a una riga, testo piegato)]}
_SEARCH_LOCK = threading.Lock()   # la cache la toccano la ricerca e l'indicizzazione in background
_WARM = {"thread": None}


def _fold(t):
    """Il testo per il confronto: minuscole e niente accenti («Perché» -> «perche»)."""
    if t.isascii():
        return t.lower()
    return "".join(ch for ch in unicodedata.normalize("NFD", t) if not unicodedata.combining(ch)).casefold()


def _fold_map(t):
    """Come _fold, carattere per carattere: (piegato, indice nel testo originale di ogni carattere piegato)."""
    out, idx = [], []
    for i, ch in enumerate(t):
        f = _fold(ch)
        out.append(f)
        idx.extend([i] * len(f))
    return "".join(out), idx


def _search_file(path):
    """Le voci user e assistant di una trascrizione, dalla cache. Un file cresciuto si legge solo nella parte nuova,
    fino all'ultimo a capo (una riga a meta' si rilegge la volta dopo); uno cambiato in altro modo da capo."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    c = _SEARCH_CACHE.get(path)
    if c and c["size"] == st.st_size and c["mtime"] == st.st_mtime:
        return c
    if not c or st.st_size < c["end"]:
        c = {"end": 0, "entries": [], "cwd": None}
    with open(path, "rb") as f:
        f.seek(max(c["end"], st.st_size - 65536))
        tail = f.read(st.st_size - f.tell())
        nl = tail.rfind(b"\n")
        end = st.st_size - len(tail) + nl + 1 if nl >= 0 else c["end"]
        if c["cwd"] is None:
            f.seek(0)
            m = re.search(rb'"cwd":\s*("(?:[^"\\]|\\.)*")', f.read(65536))
            c["cwd"] = json.loads(m.group(1)) if m else ""
    if end > c["end"]:
        # offset un byte prima: transcript_entries scarta la prima riga (puo' essere a meta'), qui e' vuota
        for e in core.transcript_entries(path, max(0, c["end"] - 1), limit=end, text_max=10 ** 9, chat_only=True):
            t = re.sub(r"\s*\n\s*", " ", e["text"]).strip()
            if t:
                c["entries"].append((e["id"], e["role"], e["at"], t, _fold(t)))
    c.update(size=st.st_size, mtime=st.st_mtime, end=end)
    _SEARCH_CACHE[path] = c
    return c


def _snippet(text, folded_q):
    """(snippet, [inizio, fine]) intorno alla prima occorrenza: fino a SEARCH_SNIPPET caratteri, senza «…»."""
    folded, idx = _fold_map(text)
    i = folded.find(folded_q)
    if i < 0:   # piegato a pezzi non coincide col piegato intero (rarissimo): l'inizio del testo
        return text[:SEARCH_SNIPPET], [0, 0]
    a, b = idx[i], idx[i + len(folded_q) - 1] + 1
    if b - a >= SEARCH_SNIPPET:
        return text[a:a + SEARCH_SNIPPET], [0, SEARCH_SNIPPET]
    lo = max(0, min(a - (SEARCH_SNIPPET - (b - a)) // 2, len(text) - SEARCH_SNIPPET))
    return text[lo:lo + SEARCH_SNIPPET], [a - lo, b - lo]


def search_files(now=None):
    """(vive {trascrizione: riga}, insieme dei file, ordine dal piu' recente): le trascrizioni delle sessioni vive e
    quelle toccate negli ultimi SEARCH_DAYS giorni, dei due account."""
    now = now or time.time()
    live = {}
    for r in (_json_cmd("sessions", "--json", "--no-screen") or []):
        p = core.transcript_of(r)
        if p:
            live[os.path.realpath(p)] = r
    files = set(live)
    for acc in (CFG.get("accounts") or {}).values():
        for f in (Path(cm.expand(acc.get("config_dir") or "~/.claude")) / "projects").glob("*/*.jsonl"):
            try:
                if now - f.stat().st_mtime <= SEARCH_DAYS * 86400:
                    files.add(os.path.realpath(str(f)))
            except OSError:
                pass
    order = sorted(files, key=lambda p: (os.path.getmtime(p) if os.path.exists(p) else 0), reverse=True)
    return live, files, order


def _trim():
    """Rende al sistema la memoria della lettura (le trascrizioni intere passano in memoria un file alla volta): senza,
    il relay restava a ~150 MB dopo l'indicizzazione invece dei ~40 della sola cache (misurato il 04/10)."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass   # non glibc (macOS, Windows): resta com'e'


def search_warm():
    """04/10 (dall'utente: dopo un riavvio del relay la ricerca rispondeva «nessun risultato» senza aver letto tutto):
    riempie la cache della ricerca leggendo tutte le trascrizioni, dalla piu' recente, un file alla volta col lock
    preso solo per quel file (una ricerca puo' passare in mezzo). La lanciano l'avvio di `serve` e una ricerca che si
    ferma ai 10 s."""
    t0, n = time.monotonic(), 0
    try:
        _, _, order = search_files()
        for p in order:
            with _SEARCH_LOCK:
                if _search_file(p):
                    n += 1
        _trim()
        log(f"search: cache pronta, {n} trascrizioni in {time.monotonic() - t0:.0f} s")
    except Exception as e:   # noqa: BLE001 — un aiuto in piu': mai far cadere il relay
        log(f"search: indicizzazione interrotta ({e})")


def search_warm_async():
    """Un solo giro di indicizzazione alla volta, in un thread che non trattiene l'uscita del processo."""
    th = _WARM["thread"]
    if th is not None and th.is_alive():
        return False
    _WARM["thread"] = threading.Thread(target=search_warm, name="search-warm", daemon=True)
    _WARM["thread"].start()
    return True


def search(arg):
    """1.27 (03/10, chiesto dalla sessione dell'app, approvato dal maintainer alle 16:57): cerca un testo nelle
    conversazioni di tutte le sessioni dei due account — le vive e le chiuse con la trascrizione degli ultimi 7 giorni;
    solo le voci user e assistant di `transcript` (niente strumenti, niente subagenti, user senza il prefisso del
    relay); maiuscole e accenti non contano. text = JSON {hits, more}: al massimo 50 hit e 60 KB, dal piu' recente;
    oltre, o dopo 10 s, quello trovato e more = true. La cache si riempie in background dall'avvio del relay (circa
    30 s per ~1 GB a settimana); una ricerca che arriva prima puo' fermarsi ai 10 s con more = true, e la cache finisce
    di riempirsi da sola."""
    q = " ".join(str(arg or "").split())
    if not q or len(q) > 200:
        return False, M("relay.cmd_search_bad_query")
    fq = _fold(q)
    t0 = time.monotonic()
    live, files, order = search_files()
    hits, more = [], False
    for p in order:
        if len(hits) >= SEARCH_MAX_HITS and os.path.getmtime(p) < (hits[SEARCH_MAX_HITS - 1]["at"] or 0):
            more = True   # i file dopo sono tutti piu' vecchi dei 50 gia' trovati
            break
        if time.monotonic() - t0 > SEARCH_MAX_S:
            more = True
            search_warm_async()   # la cache finisce di riempirsi da sola: la ricerca dopo trova tutto
            break
        with _SEARCH_LOCK:
            c = _search_file(p)
        if not c:
            continue
        row = live.get(p)
        cwd = (row or {}).get("cwd") or c["cwd"] or ""
        name = S.short_name(row.get("name") or row.get("tmux") or "", prefixes()) if row else (os.path.basename(cwd.rstrip("/")) or Path(p).parent.name)
        for eid, role, at, text, folded in c["entries"]:
            if fq in folded:
                snip, span = _snippet(text, fq)
                hits.append({"session": name, "live": bool(row), "project": cwd, "entry": eid, "role": role, "at": at,
                             "snippet": snip, "match": span})
        hits.sort(key=lambda h: (h["at"] is None, -(h["at"] or 0)))
    with _SEARCH_LOCK:
        for p in list(_SEARCH_CACHE):
            if p not in files:
                del _SEARCH_CACHE[p]
    if len(hits) > SEARCH_MAX_HITS:
        hits, more = hits[:SEARCH_MAX_HITS], True
    while hits and len(json.dumps({"hits": hits, "more": True}, ensure_ascii=False).encode()) > TRANSCRIPT_MAX_BYTES:
        hits.pop()
        more = True
    return True, json.dumps({"hits": hits, "more": more}, ensure_ascii=False)


TIMELINE_MAX_S = 7 * 86400


def timeline_page(session, arg):
    """1.29 (03/10, fase 2 approvata dal maintainer alle 18:32): la cronologia di cosa hanno fatto le sessioni per il
    riepilogo del telefono, da `supervisor timeline`: per sessione prompt, test, commit, esiti e compiti in ordine di
    tempo. session = un nome di sessions[].name o null per tutte; project come state.sessions[].project; arg = «6h», «90m», «2d» (al massimo 7 giorni) o un
    epoch s da cui partire; null = 6h. text = JSON {since, sessions, more}; oltre 60 KB si tolgono gli eventi piu'
    vecchi della sessione piu' lunga e more = true."""
    a = str(arg or "6h").strip()
    now = float(os.environ.get("CM_RELAY_NOW") or time.time())   # CM_RELAY_NOW: solo per i test, con dati di prova datati
    m = re.fullmatch(r"(\d{1,4})([mhd])", a)
    if m:
        secs = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    elif re.fullmatch(r"\d{9,11}", a):
        secs = now - int(a)
    else:
        return False, M("relay.cmd_timeline_bad_arg", arg=a)
    if secs <= 0 or secs > TIMELINE_MAX_S:
        return False, M("relay.cmd_timeline_bad_arg", arg=a)
    tl = _load("cm-timeline").timeline(secs, str(session) if session else None, now=now,
                                       live=_json_cmd("sessions", "--json", "--no-screen") or [])
    root = cm.expand(CFG["workspace"]["root"])
    for r in tl["sessions"]:
        r["project"] = S.project_of(r["project"], root)   # come state.sessions[].project: relativo alla radice
    more = False
    while tl["sessions"] and len(json.dumps(dict(tl, more=True), ensure_ascii=False).encode()) > TRANSCRIPT_MAX_BYTES:
        big = max(tl["sessions"], key=lambda r: len(r["events"]))
        big["events"].pop(0)
        if not big["events"]:
            tl["sessions"].remove(big)
        more = True
    return True, json.dumps(dict(tl, more=more), ensure_ascii=False)


TRANSCRIPT_MAX_N = 200
TRANSCRIPT_MAX_BYTES = 60000   # 1.22: il JSON di una pagina in /result; oltre si toglie dalla parte vecchia e more=true
TRANSCRIPT_WINDOWS = (2 * 1024 * 1024, 16 * 1024 * 1024, None)   # si legge dalla coda, e si allarga solo se serve


LIVE_ROWS_TTL_S = 10
_LIVE = {"at": 0.0, "rows": None}
_LIVE_LOCK = threading.Lock()


def live_row(tm):
    """16/10 (relay al 50% di CPU): la riga viva di una sessione per le letture che il telefono e la web app ripetono
    (transcript, diff, changes, file). Le righe di `sessions --json --no-screen` restano in processo LIVE_ROWS_TTL_S;
    un nome che non c'e' (sessione appena lanciata) le rilegge subito. Una copia, o None."""
    with _LIVE_LOCK:
        rows = _LIVE["rows"] if _LIVE["rows"] is not None and time.time() - _LIVE["at"] < LIVE_ROWS_TTL_S else None
    hit = next((r for r in rows or [] if (r.get("tmux") or r.get("name")) == tm), None)
    if hit is None:
        rows = _json_cmd("sessions", "--json", "--no-screen")
        if rows is not None:
            with _LIVE_LOCK:
                _LIVE.update(at=time.time(), rows=rows)
        hit = next((r for r in rows or [] if (r.get("tmux") or r.get("name")) == tm), None)
    return dict(hit) if hit else None


SID_RE = re.compile(r"[A-Za-z0-9-]{1,80}")


def transcript_by_sid(sid):
    """1.82 (24/10, «Nel riepilogo della notte, ogni scheda deve essere cliccabile e portarmi alla sessione che ha
    lavorato»): la trascrizione di una conversazione anche chiusa, dal suo id — in projects/ di ogni account, poi
    nelle copie locali delle sessioni sugli altri host. None se non c'e'."""
    if not SID_RE.fullmatch(sid or ""):
        return None
    dirs = [Path(cm.expand(a.get("config_dir") or "~/.claude")) / "projects" for a in (CFG.get("accounts") or {}).values()] or [Path(cm.expand("~/.claude")) / "projects"]
    for d in dirs:
        hit = next(iter(sorted(d.glob(f"*/{sid}.jsonl"))), None) if d.is_dir() else None
        if hit:
            return str(hit)
    mirror = Path(cm.expand(CFG["state_dir"])) / "hosts" / "transcripts"
    hit = next(iter(sorted(mirror.glob(f"*/{sid}.jsonl"))), None) if mirror.is_dir() else None
    return str(hit) if hit else None


def transcript_page(session, tm, arg, sid=None):
    """1.22 (30/09): la chat di una sessione per il telefono. arg «n» = le ultime n voci; «n:before=<id>» = le n
    prima di quella voce; «n:after=<id>» = le prime n dopo (il telefono lo ripete a scheda aperta, e costa poco:
    basta la coda del file). text = JSON {entries, more}. 1.82: con `sid` (session null) la conversazione di quell'id,
    anche chiusa, in sola lettura."""
    m = re.fullmatch(r"\s*(\d{1,4})\s*(?::\s*(before|after)=([A-Za-z0-9._-]{1,80}))?\s*", str(arg or "50"))
    if not m or not int(m.group(1)):
        return False, M("relay.cmd_transcript_bad_arg", arg=str(arg or ""))
    n, mode, ref = min(int(m.group(1)), TRANSCRIPT_MAX_N), m.group(2), m.group(3)
    if sid is not None:
        path = transcript_by_sid(sid)
        if not path:
            return False, M("relay.cmd_transcript_no_sid", sid=S.one_line(str(sid))[:80] or "?")
        return transcript_read(path, n, mode, ref)
    row = live_row(tm)
    if not row:
        # 28/10 (note di claude-observe, «X is not running» su decine di sessioni): la chat di una sessione chiusa,
        # chiesta per nome, si legge dal registro in sola lettura come quella per id (1.82)
        row = session_row(tm)
        path = core.transcript_of(row) if row else None
        if not path:
            return False, M("relay.cmd_interrupt_gone", name=session)
        return transcript_read(path, n, mode, ref)
    if row.get("host") and row.get("host") != "local":
        # 09/10: la chat di una sessione su un altro host: prima i byte nuovi del suo transcript, nella copia locale
        try:
            got = _load("cm-hosts").sync_transcript(row["host"], row.get("remote_name") or str(row.get("name") or "").split(":", 1)[-1], row.get("session_id") or "")
            if got:
                row["transcript"] = str(got)
        except Exception:   # noqa: BLE001 — host irraggiungibile: si mostra la copia che c'e'
            pass
    path = core.transcript_of(row)
    if not path:
        # 28/10: una sessione appena partita non ha ancora la trascrizione (nasce col primo turno): una pagina vuota
        return True, json.dumps({"entries": [], "more": False})
    return transcript_read(path, n, mode, ref)


def transcript_read(path, n, mode, ref):
    """La pagina di voci di una trascrizione (vedi transcript_page)."""
    size = os.path.getsize(path)
    for window in TRANSCRIPT_WINDOWS:
        start = 0 if window is None or window >= size else size - window
        entries = core.transcript_entries(path, start, diff_cap=(DF.ENTRY_LINES, DF.ENTRY_BYTES))
        ids = [e["id"] for e in entries]
        if ref and ref not in ids and start:
            continue   # la voce e' piu' indietro: si allarga la finestra
        if not ref and len(entries) < n and start:
            continue
        break
    reset = False
    if ref and ref not in ids:
        if mode != "after":
            return False, M("relay.cmd_transcript_unknown", id=ref)
        # 1.93 (28/10, «no entry … in the transcript» ×525 nelle note di claude-observe): la chat aperta chiede le voci
        # dopo l'ultima che ha; dopo un riavvio o un /clear quella voce non c'e' piu' e ogni giro falliva. Le ultime n
        # voci con reset true: l'app sostituisce la lista
        mode, ref, reset = None, None, True
    if mode == "after":
        i = ids.index(ref) + 1
        page, more = entries[i:i + n], len(entries) > i + n
    else:
        end = ids.index(ref) if mode == "before" else len(entries)
        page = entries[max(0, end - n):end]
        more = end - n > 0 or bool(start)
    page = [dict(e, diff=DF.redact_diff(e["diff"])) if e.get("diff") else e for e in page]   # 1.72: solo le voci che escono
    while page and len(json.dumps({"entries": page, "more": True}, ensure_ascii=False).encode()) > TRANSCRIPT_MAX_BYTES:
        if mode == "after":
            page = page[:-1]
        else:
            page = page[1:]
        more = True
    return True, json.dumps({"entries": page, "more": bool(more), **({"reset": True} if reset else {})}, ensure_ascii=False)


def session_row(tm):
    """1.72: la riga di una sessione, viva da `sessions --json` o chiusa dal registro: la vista «Modifiche» funziona
    anche a sessione chiusa, perche' la cartella c'e' ancora. None se non si trova."""
    row = live_row(tm)
    if row:
        return row
    s_ = next((x for x in (_json_cmd("registry", "--good", expect="{") or {}).get("sessioni", []) if x.get("nome") == tm), None)
    if not s_:
        return None
    return {"tmux": tm, "name": tm, "status": "dead", "cwd": s_.get("cartella") or "", "account": s_.get("account") or "",
            "session_id": s_.get("session_id") or ""}


def diff_op(session, tm, arg):
    """1.72 (15/10, il diff dal telefono): «Mostra tutto» sotto un passaggio. arg = l'id della voce di `transcript`;
    text = il suo `diff` intero, fino a DF.FULL_LINES righe e DF.RESULT_BYTES byte, mascherato."""
    ref = str(arg or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", ref):
        return False, M("relay.cmd_diff_bad_arg", arg=ref)
    row = session_row(tm)
    if not row:
        return False, M("relay.cmd_file_no_session", name=session or "?")
    path = core.transcript_of(row)
    if not path:
        return False, M("relay.cmd_transcript_none", name=session)
    size, hit = os.path.getsize(path), None
    for window in TRANSCRIPT_WINDOWS:
        start = 0 if window is None or window >= size else size - window
        hit = next((e for e in core.transcript_entries(path, start, diff_cap=(DF.FULL_LINES, DF.RESULT_BYTES)) if e["id"] == ref), None)
        if hit or not start:
            break
    if not hit:
        return False, M("relay.cmd_transcript_unknown", id=ref)
    if not hit.get("diff"):
        return False, M("relay.cmd_diff_none", id=ref)
    return True, json.dumps(DF.redact_diff(hit["diff"]), ensure_ascii=False)


def changes_op(session, tm, arg):
    """1.72: la vista «Modifiche» di una sessione. arg null = tutta la cartella (50 file, 2000 righe), «file=<path>» = un
    file solo come lo elenca `files` (5000 righe). Con git tutto rispetto a HEAD (cm-diff.view); fuori da git, o per
    una sessione su un altro host, i passaggi di modifica della conversazione (source «transcript»)."""
    a = str(arg or "").strip()
    want = a[5:].strip() if a.startswith("file=") else None
    if a and not want:
        return False, M("relay.cmd_changes_bad_arg", arg=a)
    row = session_row(tm)
    if not row or not row.get("cwd"):
        return False, M("relay.cmd_file_no_session", name=session or "?")
    now = time.time()
    try:
        v = DF.view(row["cwd"], want, now) if not row.get("host") or row.get("host") == "local" else None
        if v is None:
            path = core.transcript_of(row)
            # 17/10/2026: solo le righe delle scritture e dei loro risultati, non tutta la chat
            diffs = core.transcript_diffs(path, (DF.FULL_LINES, DF.RESULT_BYTES)) if path else []
            v = DF.view_from_passages(diffs, row["cwd"], want, now)
    except DF.GitTimeout:
        return False, M("relay.cmd_changes_timeout")
    except DF.NotListed:
        return False, M("relay.cmd_changes_not_listed", path=want)
    return True, json.dumps(v, ensure_ascii=False)


DECISION_TEXT_MAX = 2000


def from_device(cmd):
    """1.37: da dove arriva, a parole, per il registro e per la master («dal telefono», «dalla web app»…)."""
    dev = str(cmd.get("device") or "")
    return M(f"relay.from_{dev}") if dev in ("phone", "watch", "web") else M("relay.from_app")


def approve_task(cmd, arg):
    """1.37 (05/10, «Da approvare» nell'app): l'ok a un compito del registro in awaiting_ok, registrato come quello
    scritto a mano — `task approve <task> --by <chi> --text "<testo> (dal telefono)"`. Solo un compito che aspetta
    davvero un ok; arrivano solo dai dispositivi accoppiati (il bus) e dalla web app locale (il token)."""
    tid = str(arg or "").strip()
    # 1.42 (07/10, modalita' live): l'ok dato al polso vale solo con la doppia conferma; `confirmations` intero, mai un bool
    live = cmd.get("via") == "live"
    n = cmd.get("confirmations")
    if live and (isinstance(n, bool) or not isinstance(n, int) or n < 2):
        return False, M("relay.cmd_approve_live_needs_two", task=tid or "?")
    entry = next((a for a in approvals_list() if a["task"] == tid), None) if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", tid) else None
    if entry is None:
        return False, M("relay.cmd_approve_not_waiting", task=tid or "?")
    # 1.81 (23/10): un compito che chiede una scelta vuole `choice`, una delle sue `options`; senza opzioni, niente choice
    opts = entry.get("options") or []
    choice = S.one_line(str(cmd.get("choice") or "")).strip()[:24] if isinstance(cmd.get("choice"), str) else ""
    if opts and not choice:
        return False, M("relay.cmd_approve_needs_choice", task=tid, options=", ".join(opts))
    if opts and choice not in opts:
        return False, M("relay.cmd_approve_bad_choice", task=tid, choice=choice, options=", ".join(opts))
    if choice and not opts:
        return False, M("relay.cmd_approve_no_options", task=tid)
    text = S.one_line(str(cmd.get("text") or "").strip())[:500] or "ok"
    where = from_device(cmd) + (", " + M("relay.approve_live") if live else "")
    # 16/10/2026: il demone eredita CLAUDECODE dalla sessione che l'ha avviato; `task approve` lo prenderebbe per una
    # sessione Claude e vorrebbe --inbox. L'ok che arriva qui viene dal dispositivo
    rc, out = run_cm("task", "approve", tid, "--by", str(cmd.get("by") or "app"), "--text", f"{text} ({where})", *(["--choice", choice] if choice else []),
                     env={"CM_APPROVE_VIA": "relay"})
    if rc != 0:
        return False, (out.splitlines() or ["task approve failed"])[0]
    wake_after_approve(tid, text)
    return True, M("relay.cmd_approved_choice", task=tid, choice=choice) if choice else M("relay.cmd_approved", task=tid)


def wake_after_approve(tid, text):
    """P4 (22/10/2026): «Approva» risveglia. 21/10 alle 20:38 l'ok c'era e nessuno se n'e' accorto per 46 minuti: la
    sessione aspettava la master, la master si fidava della riga «Ti aspettano». Ora la sessione responsabile (dal
    registro: tasks.session o where.session) riceve subito il via con `talk` (staccato: il bus non aspetta; chiusa →
    resta nella casella), la master lo sa se non e' lei, e il «ferma per te» della sessione (needs-you/<sid>.json) si
    spegne, cosi' «Ti aspettano» cade alla push seguente."""
    info = _json_cmd("task", "show", tid, "--json", expect="{") or {}
    task = (info.get("contract") or {}).get("task") or {}
    sess = str(info.get("session") or (task.get("where") or {}).get("session") or "")
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    what = next((a.get("what") for a in reversed(info.get("approvals") or []) if isinstance(a, dict) and a.get("what") and a.get("what") != "confirmed"), "") or text
    choice = next((a.get("choice") for a in reversed(info.get("approvals") or []) if isinstance(a, dict) and a.get("by_") and a.get("choice")), None)
    if choice:   # 1.81: la scelta della persona arriva alla sessione nel messaggio di ok
        what = f"{S.one_line(str(what))[:170]}; " + M("relay.approve_choice", choice=S.one_line(str(choice))[:24])
    kw = {"person": M("tasks.person"), "task": tid, "when": time.strftime("%H:%M"), "what": S.one_line(str(what))[:200]}

    def spawn(*args):
        try:
            subprocess.Popen([CM_BIN, *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
    if sess:
        spawn("talk", sess, M("relay.approve_wake", **kw), "--no-wait")
        clear_needs_you(sess)
    if sess != master:
        spawn("talk", master, M("relay.approve_master", session=sess or "-", **kw), "--no-wait")


def clear_needs_you(tm):
    """Il file needs-you/<session_id>.json della sessione tmux `tm` (quello che «Ti aspettano» legge), se c'e'."""
    for r in _json_cmd("sessions", "--json", "--no-screen") or []:
        if (r.get("tmux") or r.get("name")) == tm and r.get("session_id"):
            (Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"{r['session_id']}.json").unlink(missing_ok=True)


def decision_send(cmd, arg):
    """1.37: «Salva come decisione» — il testo va alla master con `talk master`, che lo scrive nella sua memoria
    (un file e una riga nell'indice) e conferma. arg = il progetto, facoltativo. Master chiusa: resta nella casella."""
    text = str(cmd.get("text") or "").strip()
    if not text:
        return False, M("relay.cmd_decision_empty")
    if len(text) > DECISION_TEXT_MAX:
        return False, M("relay.cmd_decision_long", max=DECISION_TEXT_MAX)
    project = S.one_line(str(arg or "").strip())[:200]
    msg = M("relay.decision_to_master", device=from_device(cmd), text=text,
            project=M("relay.decision_project", project=project) if project else "")
    rc, out = relay_talk("master", msg, "--no-wait")
    if rc != 0:
        return False, (out.splitlines() or ["talk failed"])[0]
    return True, M("relay.cmd_decision_inbox" if saved_in_inbox(out) else "relay.cmd_decision_sent")


def prefix_for(cmd, tm=""):
    """1.22 (30/09): il prefisso del prompt dice da dove arriva — `device` del comando («phone» | «watch» | «web», 1.36);
    senza, uno neutro («via app»: prima della 1.36 diceva «polso» anche per la web app). `transcript` lo riconosce,
    lo toglie e ne fa `origin`. 10/10 (una sessione su win ha letto il divieto di SendMessage come divieto di passare un
    compito alla master): il prefisso dice come passarglielo; a una sessione su un altro host (HOST:nome, dove
    supervisor non c'e') dice di scriverlo per intero nella risposta."""
    dev = str(cmd.get("device") or "")
    p = M(f"relay.prompt_prefix_{dev}") if dev in ("phone", "watch", "web") else M("relay.prompt_prefix")
    return p.replace(M("relay.prompt_handoff"), M("relay.prompt_handoff_remote")) if ":" in str(tm or "") else p


def is_live(tm):
    """La sessione tmux c'e' adesso (sessions --json): `talk` su una chiusa salva nella casella ed esce 0."""
    return any((r.get("tmux") or r.get("name")) == tm for r in (_json_cmd("sessions", "--json", "--no-screen") or []))


def saved_in_inbox(out):
    """`talk` ha solo messo il messaggio nella casella (sessione chiusa fra il controllo e la consegna): la sua riga
    rimanda a `talk --status <id>`, in ogni lingua."""
    return "talk --status" in (out or "")


RESOLVED_KEEP_S = 300   # 1.55: per quanto una domanda risolta al PC resta nello stato


def mark_answered(session, row):
    """1.55: l'id della domanda a cui un dispositivo ha appena risposto, perche' resolved_of non la dica «risolta al
    terminale». answered.json = {id: epoch}, tenuto un giorno."""
    cur = open_question_at(session, row)
    if cur is None:
        return
    p = rdir() / "answered.json"
    now = time.time()
    done = {k: v for k, v in read_json(p, {}).items() if now - float(v or 0) < 86400}
    done[f"q-{cur}-1"] = now
    write_json(p, done)


def resolved_of(questions, rows, now):
    """1.55 (14/10, dagli spunti della valutazione di Kaya): una domanda che era nello stato e non c'e' piu', senza una
    risposta da un dispositivo, e' stata risolta al PC. Resta {id, text, at} per RESOLVED_KEEP_S sotto il nome della
    sessione, cosi' il telefono la mostra «risolta al terminale» invece di farla sparire; spiega anche un rifiuto
    relay.cmd_question_changed. Solo sessioni ancora vive. resolved.json = {nome: {id, text, at}}."""
    p = rdir() / "resolved.json"
    store = {k: v for k, v in read_json(p, {}).items() if isinstance(v, dict) and now - float(v.get("at") or 0) < RESOLVED_KEEP_S}
    answered = read_json(rdir() / "answered.json", {})
    alive = {S.short_name(r.get("name") or r.get("tmux") or "", prefixes()) for r in rows if r.get("status") != "dead"}
    current = {S.short_name(tm, prefixes()): f"q-{int(q.get('asked_at') or 0)}-1" for tm, q in questions.items()}
    for name, s_ in last_sessions().items():
        q = s_.get("question") or {}
        if not q.get("id") or name not in alive or current.get(name) == q["id"] or q["id"] in answered:
            continue
        if (store.get(name) or {}).get("id") != q["id"]:
            store[name] = {"id": q["id"], "text": str(q.get("text") or ""), "at": int(now)}
    if store != read_json(p, {}):
        write_json(p, store)
    return store


def mark_queued(tm, text):
    """0319ab (21:43): la voce appena messa in coda da un prompt programmato prende origin «relay», cosi' quando
    `queue --due` la consegna il record nella casella e' del relay. Scritta qui, nel file della coda: la CLI di queue
    non ha un'opzione per farlo."""
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if not row or not row.get("session_id"):
        return
    p = Path(cm.expand(CFG["state_dir"])) / "queue" / row["session_id"]
    try:
        items = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return
    for it in reversed(items):
        if it.get("text") == text and not it.get("origin"):
            it["origin"] = "relay"
            break
    p.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))


DUPLICATE_PROMPT_S = 60


def duplicate_prompt(tm, text, record=False):
    """14/10 (spunti della valutazione di Kaya): lo stesso testo alla stessa sessione entro DUPLICATE_PROMPT_S (la coda
    offline del telefono, un doppio tocco) parte una volta sola. Torna i secondi dall'invio di prima, o None; con
    `record` annota l'invio (solo dopo una consegna riuscita: un tentativo fallito si puo' ripetere subito).
    recent-prompts.json = {sessione: [[epoch, sha1 del testo]]}."""
    import hashlib
    p = rdir() / "recent-prompts.json"
    now = time.time()
    h = hashlib.sha1(" ".join(text.split()).encode()).hexdigest()
    window = float(R.get("duplicate_prompt_s", DUPLICATE_PROMPT_S))
    if window <= 0:
        return None
    recent = {k: [x for x in v if now - float(x[0]) < window] for k, v in read_json(p, {}).items() if isinstance(v, list)}
    hit = next((x for x in recent.get(tm, []) if x[1] == h), None)
    if hit and not record:
        return max(1, int(now - float(hit[0])))
    if not record:
        return None
    recent.setdefault(tm, []).append([now, h])
    write_json(p, {k: v for k, v in recent.items() if v})
    return None


QUESTION_SKEW_S = 60   # 1.52: margine per l'orologio del dispositivo nel ripiego senza `question`


def open_question_at(session, row):
    """L'asked_at della domanda aperta adesso, con la regola dello stato (ultimo evento waiting del ledger, altrimenti
    l'asked_at dell'ultima push); None se non se ne conosce nessuna. Letto all'esecuzione, non dall'ultima push: una
    domanda nuova fa il suo evento waiting subito, anche se la push non e' ancora partita."""
    sid = (row or {}).get("session_id") or ""
    ledger = core.ledger_rows()
    if row and row.get("host") and row.get("host") != "local":
        ledger += core.remote_events(row)
    asked = [S.epoch(x.get("ts")) for x in ledger if sid and x.get("session_id") == sid and x.get("event") == "waiting"]
    if asked:
        return int(asked[-1])
    q = (last_sessions().get(session) or {}).get("question") or {}
    return int(q["asked_at"]) if q.get("asked_at") else None


def question_changed(cmd, session, row):
    """1.52 (14/10, chiesto dal manutentore): una risposta vale solo per la domanda per cui e' stata data.
    Un comando rimasto in coda sul telefono offline arrivava dopo e premeva il tasto sulla domanda nuova. Con `question`
    (Question.id, «q-<asked_at>-1») deve coincidere con la domanda aperta; senza (app precedente) si rifiuta se la
    domanda aperta e' stata posta dopo `issued` piu' QUESTION_SKEW_S. Il testo del rifiuto, o None se si puo' rispondere."""
    cur = open_question_at(session, row)
    if cur is None:
        return None   # nessuna domanda nota: decide cm-answer («no open question»)
    qid = str(cmd.get("question") or "")
    if qid:
        m = re.fullmatch(r"q-(\d+)-\d+", qid)
        return None if m and int(m.group(1)) == cur else M("relay.cmd_question_changed", name=session)
    try:
        issued = float(cmd.get("issued") or 0)
    except (TypeError, ValueError):
        issued = 0.0
    if issued > 1e9 and cur > issued + QUESTION_SKEW_S:
        return M("relay.cmd_question_changed", name=session)
    return None


def execute(cmd):
    """(ok, testo) per un comando del contratto: {op, session, arg, by}. Allow-list fissa, tutto via la CLI."""
    op = str(cmd.get("op") or "")
    session = str(cmd.get("session") or "")
    arg = cmd.get("arg")
    if op not in OPS:
        return False, M("relay.cmd_not_allowed", op=op or "?")
    tm = tmux_of(session)
    info = last_sessions().get(session) or {}
    try:
        if op == "answer":
            # 1.10 (15/09): arg «n» = l'opzione n; «text:<testo>» = «Type something.» + il testo; «chat» = «Chat about this»
            a = str(arg or "").strip()
            if a == "chat":
                pick = ["--chat"]
            elif a.startswith("text:"):
                if not a[5:].strip():
                    return False, M("relay.cmd_empty_text")
                pick = ["--text", a[5:].strip()]
            elif a.isdigit() and int(a):
                pick = [a]
            else:
                return False, M("relay.cmd_bad_answer", arg=a or "?")
            row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
            changed = question_changed(cmd, session, row)
            if changed:
                return False, changed
            if row:
                checkpoint(row.get("cwd"))
            rc, out = run_cm("answer", tm, *pick)
            if rc != 0:
                return False, out.splitlines()[0] if out else "answer failed"
            mark_answered(session, row)
            clear_waiting((row or {}).get("session_id"))
            m = re.search(r"risposto\s+(\d+)\.\s+(.*?)\s{2,}", out + "  ") or re.search(r"risposto\s+(\d+)\.\s+(\S.*)$", out.splitlines()[0])
            label = m.group(2).strip() if m else ""
            return True, M("relay.cmd_answered", n=m.group(1) if m else a, label=label)
        if op == "prompt":
            text = str(arg or "").strip()
            if not text:
                return False, "empty prompt"
            if not is_live(tm) and cmd.get("reopen") is True:
                # 1.65 (14/10, dall'analisi dei concorrenti): una sessione chiusa si riapre per session_id e riceve subito il
                # prompt (`talk --reopen`); se il rilancio non riesce il messaggio resta nella sua casella
                rc, out = relay_talk(tm, prefix_for(cmd, tm) + " " + with_scope(cmd, text), "--no-wait", "--reopen")
                if rc != 0:
                    return False, (out.splitlines() or ["talk failed"])[0]
                if saved_in_inbox(out):
                    return False, M("relay.cmd_inbox_only", name=session)
                return True, M("relay.cmd_reopened_delivered", name=session)
            if not is_live(tm):
                return False, M("relay.cmd_not_running", name=session)
            if cmd.get("at") is not None:
                # 1.63 (14/10): «manda alle HH:MM» — l'ora l'ha calcolata il dispositivo (epoch); va nella coda della sessione
                try:
                    at = float(cmd["at"])
                except (TypeError, ValueError):
                    at = 0.0
                if not time.time() - 60 <= at <= time.time() + 7 * 86400:
                    return False, M("relay.cmd_prompt_bad_at")
                voice = " " + M("relay.prompt_voice") if cmd.get("voice") is True else ""
                full_text = prefix_for(cmd, tm) + voice + " " + with_scope(cmd, text)
                rc, out = run_cm("queue", tm, full_text, "--at-epoch", str(at))
                if rc != 0:
                    return False, (out.splitlines() or ["queue failed"])[0]
                mark_queued(tm, full_text)
                return True, M("relay.cmd_prompt_scheduled", name=session, t=time.strftime("%H:%M", time.localtime(at)))
            ago = duplicate_prompt(tm, text)
            if ago is not None:
                return False, M("relay.cmd_prompt_duplicate", name=session, s=ago)
            row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
            # 1.42: un prompt dettato in modalita' live porta le istruzioni vocali dopo il prefisso (timeline e transcript le tolgono)
            voice = " " + M("relay.prompt_voice") if cmd.get("voice") is True else ""
            rc, out = relay_talk(tm, prefix_for(cmd, tm) + voice + " " + with_scope(cmd, text), "--no-wait")
            if rc != 0:
                return False, out.splitlines()[0] if out else "talk failed"
            if saved_in_inbox(out):
                return False, M("relay.cmd_inbox_only", name=session)
            aw = read_json(rdir() / "awaiting.json", {}); aw[tm] = int(time.time()); write_json(rdir() / "awaiting.json", aw)
            duplicate_prompt(tm, text, record=True)
            try:   # 1.33: un'azione ricorrente mandata dal telefono sale in cima alla lista
                _load("cm-recurring").mark_used(text)
            except Exception:   # noqa: BLE001 — un extra: mai far fallire il prompt
                pass
            # 14/10 (spunti della valutazione di Kaya): l'esito dice se la sessione lo legge adesso o a fine turno
            if row and (row.get("status") in ("busy", "waiting") or row.get("waiting")):
                return True, M("relay.cmd_prompt_queued", name=session)
            return True, M("relay.cmd_delivered")
        if op == "launch":
            path = str(arg or "")
            proj = next((p for p in inventory() if os.path.realpath(p["path"]) == os.path.realpath(path)), None) if path else None
            if not proj:
                return False, M("relay.cmd_no_project", path=path or "?")
            # 1.13 (16/09, dall'utente): «Nuova sessione» dal polso con il primo messaggio, nel campo `text` del comando. Un relay
            # che non conosce `text` lo ignora e lancia senza messaggio: mai un prompt a meta'
            prompt = " ".join(str(cmd.get("text") or "").split())
            here = os.path.realpath(proj["path"])
            before = {r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])}
            # 1.9.2 (15/09, decisione dell'utente): la scheda sul desktop come reopen; senza desktop, senza finestra
            rc, out = run_cm("launch", proj["path"], "--window")
            if rc != 0:
                return False, out.splitlines()[0] if out else "launch failed"
            # il nome vero della sessione nata (launch prende il primo libero: «orbit-docs-2»), cosi' il polso apre la
            # Scheda giusta senza indovinare; e' lo stesso nome corto di sessions[].name
            new = [r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])
                   if (r.get("tmux") or r.get("name")) not in before and r.get("cwd") and os.path.realpath(r["cwd"]) == here]
            extra = {"session": S.short_name(new[0], prefixes())} if new else {}
            if not prompt:
                return True, M("relay.cmd_launched", name=proj["name"], account=proj["account"]), extra
            if not new:
                return False, M("relay.cmd_launch_no_prompt", name=proj["name"], account=proj["account"], line="session not found"), extra
            rc, out = relay_talk(new[0], prefix_for(cmd, new[0]) + " " + prompt, "--no-wait")
            if rc != 0:
                return False, M("relay.cmd_launch_no_prompt", name=proj["name"], account=proj["account"],
                                line=(out.splitlines() or ["talk failed"])[0]), extra
            aw = read_json(rdir() / "awaiting.json", {}); aw[new[0]] = int(time.time()); write_json(rdir() / "awaiting.json", aw)
            return True, M("relay.cmd_launched_prompt", name=proj["name"], account=proj["account"]), extra
        if op in ("follow", "unfollow"):
            fl = set(read_json(rdir() / "follow.json", []))
            (fl.add if op == "follow" else fl.discard)(tm)
            write_json(rdir() / "follow.json", sorted(fl))
            return True, M("relay.cmd_following" if op == "follow" else "relay.cmd_unfollowed", name=session)
        if op == "resume":
            if info.get("state") == "gone":
                return False, M("relay.cmd_gone", name=session)
            if not is_live(tm):
                return False, M("relay.cmd_not_running", name=session)
            rc, out = relay_talk(tm, prefix_for(cmd, tm) + " " + str((CFG.get("guard") or {}).get("resume_prompt") or "riprendi da dove eri"), "--no-wait")
            if rc != 0:
                return False, out.splitlines()[0] if out else "talk failed"
            if saved_in_inbox(out):
                return False, M("relay.cmd_inbox_only", name=session)
            return True, M("relay.cmd_resumed", name=session)
        if op == "reopen":
            # 1.9 (15/09): una sessione gone rilanciata nella sua cartella (la logica e' una sola, anche per il bot)
            # 1.84 (24/10): una sessione chiusa dall'app riparte con --resume del session_id salvato, mai --continue
            saved = closed_saved().get(tm)
            ok, code, f = core.reopen(tm, run_cm, saved=saved)
            if ok and saved:
                closed_file(tm).unlink(missing_ok=True)
            return ok, M(f"relay.cmd_reopen_{code}", name=session, **f)
        if op in ("model", "effort"):
            # 1.12 (16/09): dal selettore della sessione, SOLO per quella sessione — mai il default delle sessioni nuove.
            # Le verifiche (ferma al prompt, valore fra le scelte, conferma nel riquadro) le fa `supervisor model|effort`;
            # il testo di un rifiuto e' la sua prima riga, breve, e il polso la mostra cosi' com'e'.
            if info.get("state") == "gone":
                return False, M("relay.cmd_gone", name=session)
            rc, out = run_cm(op, tm, str(arg or "").strip())
            text = (out.splitlines() or [f"{op} failed"])[0]
            if rc == 0:
                push_async()   # lo stato riporta subito il valore nuovo (annotato dal comando, cm-core lo usa)
            return rc == 0, text
        if op == "permission_mode":
            # 1.69 (15/10): Shift+Tab finche' il piè di pagina mostra la modalita' chiesta; le verifiche e i rifiuti li
            # fa `supervisor permissions`, la cui prima riga e' il testo per il telefono
            if info.get("state") == "gone":
                return False, M("relay.cmd_gone", name=session)
            rc, out = run_cm("permissions", tm, str(arg or "").strip())
            text = (out.splitlines() or ["permission change failed"])[0]
            if rc == 0:
                push_async()
            return rc == 0, text
        if op == "night_add":
            # 1.17 (29/09): un lavoro nella coda di stanotte, dalla cartella di un progetto pubblicato come launch;
            # 1.49: o nella cartella della master, arg = il suo nome
            path = str(arg or "")
            proj = night_master() if path == night_master()["name"] else None
            proj = proj or (next((p for p in inventory() if os.path.realpath(p["path"]) == os.path.realpath(path)), None) if path else None)
            if not proj:
                return False, M("relay.cmd_no_project", path=path or "?")
            prompt = str(cmd.get("text") or "").strip()
            if not prompt:
                return False, M("relay.cmd_night_empty")
            rc, out = run_cm("night", "add", proj["path"], prompt)
            if rc == 4:
                return False, M("relay.cmd_night_full", max=night_max())
            if rc != 0:
                return False, (out.splitlines() or ["night add failed"])[0]
            m = re.search(r"\b([0-9a-f]{8})\b", out)
            job = m.group(1) if m else "?"
            return True, M("relay.cmd_night_added", id=job, name=proj["name"], account=proj["account"]), {"job": job}
        if op == "night_remove":
            job = str(arg or "").strip()
            if not job:
                return False, M("relay.cmd_night_unknown", id="?")
            rc, out = run_cm("night", "remove", job)
            if rc == 1:
                return False, M("relay.cmd_night_unknown", id=job)
            if rc == 5:
                return False, M("relay.cmd_night_started", id=job)
            if rc != 0:
                return False, (out.splitlines() or ["night remove failed"])[0]
            return True, M("relay.cmd_night_removed", id=job)
        if op == "report":
            return share_report(cmd, tm, session)
        if op == "transcript":
            if not session and cmd.get("sid"):   # 1.82: una conversazione chiusa, dal rapporto della notte
                return transcript_page(None, None, arg, sid=str(cmd.get("sid")))
            return transcript_page(session, tm, arg)
        if op == "diff":
            return diff_op(session, tm, arg)
        if op == "changes":
            return changes_op(session, tm, arg)
        if op == "file":
            return file_open(cmd, session, tm, arg)
        if op == "slash":
            return slash_send(cmd, session, tm, arg)
        if op == "approve":
            return approve_task(cmd, arg)
        if op == "elicit":   # 1.83
            return elicit_answer(cmd, arg)
        if op == "suggestion":   # 1.84
            return suggestion_act(cmd, arg)
        if op == "close_preview":   # 1.84
            return close_preview(cmd, arg, session)
        if op == "close":   # 1.84
            return close_sessions(cmd, arg, session)
        if op == "sys_kill":   # 1.90
            return sys_kill(cmd, arg)
        if op == "sys_unit_stop":   # 1.91
            return sys_unit_stop(cmd, arg)
        if op in ("plugins", "plugin_check", "plugin_update", "plugin_update_all"):   # 1.95
            return plugins_op(op, arg)
        if op == "kpi":   # 1.96
            return kpi_op()
        if op == "attention_done":   # 1.80
            return attention_done(cmd, arg)
        if op == "decision":
            return decision_send(cmd, arg)
        if op == "projects":
            return projects_list()
        if op == "night":
            return night_report(arg)
        if op == "nights":
            return nights_list()
        if op == "done":
            return done_list(arg)
        if op == "agenda":
            return agenda_list()
        if op == "agenda_set":
            return agenda_set(cmd, arg)
        if op == "studio":
            return studio_page()
        if op == "quota_route":
            return quota_route(cmd, session, tm, arg)
        if op == "project_new":
            return project_new(cmd, arg, cmd.get("text"))
        if op == "search":
            return search(arg)
        if op == "timeline":
            return timeline_page(cmd.get("session"), arg)
        if op == "pair_add":
            return pair_add_invite()
        if op == "unpair":
            return unpair(arg)
        if op == "interrupt":
            # 1.21 (30/09): il tasto Stop — `supervisor interrupt` manda un solo Esc, e solo a turno in corso
            if info.get("state") == "gone" or not is_live(tm):
                return False, M("relay.cmd_interrupt_gone", name=session)
            # 16/10: Stop subito dopo un invio dal telefono → il turno non e' ancora partito: si aspetta che parta
            sent = float(read_json(rdir() / "awaiting.json", {}).get(tm) or 0)
            rc, out = run_cm("interrupt", tm, *(("--wait", str(INTERRUPT_WAIT_S)) if time.time() - sent < 60 else ()))
            if rc == 0:
                aw = read_json(rdir() / "awaiting.json", {})
                if aw.pop(tm, None) is not None:
                    write_json(rdir() / "awaiting.json", aw)
                push_async()   # lo stato dice subito che il turno non gira piu'
                return True, M("relay.cmd_stopped", name=session)
            if rc == 1:
                return False, M("relay.cmd_nothing_to_stop", name=session)
            if rc == 4:
                return False, M("relay.cmd_still_running", name=session)
            return False, (out.splitlines() or ["interrupt failed"])[0]
        if op == "last":
            row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
            text = last_message(row) if row else ""
            return (bool(text), text or M("relay.cmd_no_last", name=session))
        if op == "screen":
            # --join: tmux riunisce le righe mandate a capo, cosi' il polso non riceve parole spezzate
            rc, out = run_cm("screen", tm, "--lines", "30", "--join")
            return (rc == 0), ("\n".join(out.splitlines()[-30:]) if rc == 0 else (out.splitlines()[0] if out else "screen failed"))
        if op == "allow_all":
            q = info.get("question") or {}
            if not q:
                return False, M("relay.cmd_no_question", name=session)
            row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
            changed = question_changed(cmd, session, row)
            if changed:
                return False, changed
            if q.get("tier") == "high":
                return False, M("relay.cmd_high")
            opt = next((o for o in q.get("options", []) if re.search(r"don.?t ask again|non chiedere|sempre|always", str(o.get("label") or ""), re.I)), None)
            if not opt:
                return False, M("relay.cmd_no_allow_all")
            rc, out = run_cm("answer", tm, str(opt["n"]))
            if rc == 0:
                mark_answered(session, row)
                clear_waiting((row or {}).get("session_id"))
            return (rc == 0), (M("relay.cmd_answered", n=opt["n"], label=opt["label"]) if rc == 0 else (out.splitlines()[0] if out else "answer failed"))
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return False, M("relay.cmd_not_allowed", op=op)
