#!/usr/bin/env python3
"""supervisor tile / merge / move / layout — le sessioni sullo schermo (ChromeOS + chrome-bridge).

    supervisor tile [nomi] [--rows|--grid] [--on POSTO] [--area L,T,W,H] [--padding N] [--dry-run]
    supervisor merge [nomi]                   tutte le sessioni a schede di UNA finestra del Terminale
    supervisor move POSTO [nomi]              porta le sessioni su un altro monitor e le affianca li'
    supervisor tile --where                   elenca i monitor riconosciuti
    supervisor layout save|restore|list [NOME]   layout con nome (window_layout di chrome-bridge, 2.6)
    cm-tile.py open-tab NOME [ephemeral] [--dry-run]   (da cm-terminal.sh) la sessione come SCHEDA di una
                                                 finestra app del Terminale gia' aperta; exit 5 = nessun bridge
    (alias: --righe --griglia --su --prova --dove --unisci; `riaffianca` = tile)

Richiede chrome-bridge in ascolto (una sessione Claude con l'MCP attivo basta) e il
backend terminale `chromeos`: senza, esce dicendo cosa manca (mai un ripiego silenzioso).

Due modi, per due situazioni:
- **tile**: una sessione, una finestra, tutte affiancate in colonne uguali (piu' monitor).
- **merge**: tutte a schede di un'unica finestra del Terminale (schermo incorporato).

Nessuno dei due versi sposta schede di sessione (T59/T79): Chrome accetta schede in
ingresso solo su finestre `normal`, e quella del Terminale e' una app della SWA. Staccare =
una finestra app nuova via garcon (`attach NOME`, ~20 s; MAI piu' popup). La #home che ogni
finestra di garcon porta con se' si SFRATTA (T80): da sola in un popup e chiusa li'. Tornare a schede = si duplica una scheda gia' dentro la finestra app (nasce una
shell pulita nel container), lasciandole prima un segnaposto (`tile.placeholder_file`) che
dice a quale sessione attaccarsi; lo consuma la shell (shell/supervisor.sh). ~20 s a sessione.

Su quale monitor (T30, T41): `native` e' quello con origine (0,0), gli altri prendono
coordinate negative e si distinguono per posizione. Una finestra massimizzata COMBACIA
col suo monitor; `screen.avail*` letto da una scheda descrive il monitor di quella
finestra; se su un monitor non c'e' nessuna pagina, GLIELA SI PRESTA. L'area del monitor
di destinazione si misura DOPO esserci arrivati. Sotto `tile.min_column_px` per colonna
si passa a griglia (T40).
"""
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
T = CFG["tile"]
CLI = cm.expand(T["chrome_bridge_cli"])
GARCON = CFG["terminal"]["garcon"]
DISPATCH = str(HERE / "supervisor")
URL_TERMINALE = T["terminal_url"]
SEGNAPOSTO = cm.expand(T["placeholder_file"])
REGISTRO_MONITOR = cm.expand(T["monitor_registry"])
SOGLIA = int(T["min_column_px"])
# Due misure dello stesso monitor differiscono di 13-34 px (finestra massimizzata vs registro,
# 10/09: -1462 vs -1475; 09/09: 749 vs 783): sotto TOLL sono lo stesso monitor
TOLL = int(T.get("monitor_tolerance_px", 40))
NOMI = T["monitor_names"]          # interno → nome mostrato (nativo, sinistra, ...)
NOMI_INV = {v: k for k, v in NOMI.items()}
DIREZIONI = {"su": "above", "alto": "above", "sopra": "above", "up": "above",
             "giu": "below", "giù": "below", "basso": "below", "sotto": "below", "down": "below",
             "sinistra": "left", "left": "left", "destra": "right", "right": "right", "nativo": "native", "native": "native"}
PREFIXES = sorted((a.get("tmux_prefix") or "" for a in CFG["accounts"].values()), key=len, reverse=True)


def bridge(comando, **params):
    """Chiama un tool di chrome-bridge dalla CLI. Torna il JSON, o esce."""
    runner = [sys.executable, CLI] if CLI.endswith(".py") else ["node", CLI]
    cmd = runner + [comando, "--format", "json"]
    if params:
        cmd += ["--json", json.dumps(params)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        sys.exit(f"chrome-bridge {comando}: {p.stderr.strip() or p.stdout.strip()}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        sys.exit(f"chrome-bridge {comando}: {M('tile.not_json')}\n{p.stdout[:300]}")


def tmux(*args):
    return subprocess.run(sessions.TMUX + list(args), capture_output=True, text=True)


def sessioni_vive():
    return [r for r in tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines() if r]


def etichetta(nome):
    """Quello che il titolo della scheda mostra: senza il prefisso d'account."""
    if CFG["tabs"]["strip_prefix"]:
        for p in PREFIXES:
            if p and nome.startswith(p):
                return nome[len(p):]
    return nome


def scheda_di(nome, schede):
    """La scheda terminale di una sessione: prima per URL (`args[]=<nome>`, esatto), poi
    per titolo — che e' quello che la sessione aveva ALL'AVVIO: una rinominata dopo non
    si riconosce, ed e' giusto cosi'. Meglio non trovarla che spostare la scheda sbagliata."""
    atteso = f"args[]={nome}"
    for s in schede:
        if s["url"].startswith(URL_TERMINALE) and atteso in s["url"]:
            return s
    coda = " " + etichetta(nome)
    for s in schede:
        if s["url"].startswith(URL_TERMINALE) and s["title"].endswith(coda):
            return s
    return None


def rinfresca_titoli():
    """Riscrive il titolo della scheda di ogni sessione attaccata (T29): dopo un riavvio
    di Chrome il Terminale ripristina le schede col titolo che avevano prima. La sequenza
    OSC va sul tty del client tmux, che e' quel che la scheda sta mostrando."""
    p = tmux("list-clients", "-F", "#{client_tty} #{client_session}")
    for riga in p.stdout.splitlines():
        if not riga.strip():
            continue
        tty, _, nome = riga.partition(" ")
        icona = subprocess.run([str(HERE / "cm-color.sh"), nome], capture_output=True, text=True).stdout.strip()
        if not icona:
            continue
        try:
            with open(tty, "w") as f:
                f.write(f"\033]0;{icona}\007")
        except OSError:
            pass
    time.sleep(1)


def pulisci_home():
    """Chiude le schede `#home` (pagina iniziale del Terminale) delle finestre che
    contengono SOLO quella: residui puri. Chiudere la `#home` di una finestra con dentro
    sessioni vive resta appeso 30 s e non chiude niente (T28). Sempre a fine fase."""
    schede = bridge("get_tabs")
    quante = {}
    for t in schede:
        quante[t["windowId"]] = quante.get(t["windowId"], 0) + 1
    chiuse = 0
    for t in schede:
        if not (t["url"].startswith(URL_TERMINALE) and "#home" in t["url"]):
            continue
        if quante.get(t["windowId"], 0) != 1:
            continue
        try:
            bridge("tab_action", action="close", tab_id=t["id"])
            chiuse += 1
        except SystemExit:
            pass
    return chiuse


def apri_finestra(nome):
    subprocess.run([str(HERE / "cm-terminal.sh"), "open", nome, "ephemeral"], check=False)


def apri_finestra_semplice():
    """Finestra del Terminale con una shell normale, senza comando: la SORGENTE da duplicare.
    Le `#home` non sono shell; le schede aperte da `attach` portano il nome della sessione
    nell'URL e il duplicato si riattacca a QUELLA (T28)."""
    # SOLO col backend chromeos vero: col backend `fake` dei test il garcon di questa
    # macchina apriva finestre vere sul desktop (successo il 09/09/2026, tre terminali)
    backend = subprocess.run([str(HERE / "cm-terminal.sh"), "detect"], capture_output=True, text=True).stdout.strip()
    if backend == "chromeos" and os.path.exists(GARCON):
        subprocess.Popen([GARCON, "--client", "--terminal"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sorgente_duplicabile(schede, finestra):
    for t in schede:
        if (t["windowId"] == finestra and t["url"].startswith(URL_TERMINALE)
                and "#home" not in t["url"] and "attacca" not in t["url"] and "supervisor" not in t["url"]
                and "args[]=attach" not in t["url"]):
            return t
    return None


def client(nome=None):
    """Le coppie (tty, sessione) dei client tmux, o i tty di una sessione sola."""
    p = tmux("list-clients", "-F", "#{client_tty} #{client_session}")
    coppie = [r.split(None, 1) for r in p.stdout.splitlines() if r.strip()]
    if nome is None:
        return {(t, s) for t, s in coppie}
    return {t for t, s in coppie if s == nome}


def attendi_nuovo_client(nome, prima, secondi=None):
    """Aspetta un client NUOVO sulla sessione, non uno qualsiasi (T27): se una sessione ha
    gia' una scheda, «risulta attaccata» e' vero dal primo istante; il codice concludeva che
    la scheda nuova era arrivata, chiudeva la vecchia, e con destroy-unattached la sessione
    moriva. Ha ucciso googlebook-it."""
    for _ in range(secondi or int(T["new_client_wait_s"])):
        time.sleep(1)
        if client(nome) - prima:
            return True
    return False


def stacca_in_finestra_app(n, schede):
    """La sessione in una finestra app del Terminale TUTTA SUA, via garcon con `attach NOME`
    (l'utente 11/09/2026 13:26: «sempre schede anche quando affiancate» — mai piu' popup: la finestra
    app ha la barra delle schede e si accorpa a mano; porta con se' la sua #home, prezzo noto T78).
    Client NUOVO atteso (T27), poi la scheda vecchia si chiude. Torna l'id della finestra nuova."""
    vecchia = scheda_di(n, schede)
    prima = client(n)
    subprocess.run([str(HERE / "cm-terminal.sh"), "open", n, "ephemeral"], check=False,
                   env={**os.environ, "CM_TERMINAL_FORCE_WINDOW": "1"})
    if not attendi_nuovo_client(n, prima, int(T["window_open_wait_s"]) + int(T["new_client_wait_s"])):
        sys.exit(M("tile.no_client", name=n, s=int(T["window_open_wait_s"]) + int(T["new_client_wait_s"])))
    time.sleep(1)
    nuova = None
    for _ in range(int(T["window_open_wait_s"])):
        schede2 = bridge("get_tabs")
        nuova = next((t for t in schede2 if t["url"].startswith(URL_TERMINALE) and f"args[]={n}" in t["url"]
                      and (not vecchia or t["id"] != vecchia["id"])), None)
        if nuova:
            break
        time.sleep(1)
    if not nuova:
        sys.exit(M("tile.move_no_window", name=n, resp="-"))
    if vecchia:
        bridge("tab_action", action="close", tab_id=vecchia["id"])
    sfratta_home(nuova["windowId"])
    return nuova["windowId"]


# ---------------------------------------------------------------- merge
def unisci(nomi):
    """Tutte a schede di un'unica finestra del Terminale. La finestra di raccolta deve
    contenere una scheda duplicabile E non essere un popup (T60): un duplicato eredita il
    tipo della finestra, e duplicare in un popup produce un altro popup."""
    rinfresca_titoli()
    tutte = bridge("get_tabs", include_windows=True)
    schede = tutte.get("tabs", tutte) if isinstance(tutte, dict) else tutte
    riquadri = tutte.get("windows", []) if isinstance(tutte, dict) else []
    tipo_di = {f["id"]: f.get("type") for f in riquadri}
    # T78 (l'utente, 11/09/2026): la scheda iniziale (#home) della SWA del Terminale NON si chiude
    # finche' nella finestra ci sono altre schede (la x sparisce; via bridge va in timeout): una
    # finestra app senza #home nasce solo trascinando fuori a mano una scheda di sessione. Si
    # preferisce QUELLA come raccolta; con la sola finestra con #home si raccoglie li' e si dice
    con_home = {t["windowId"] for t in schede if t["url"].startswith(URL_TERMINALE) and "#home" in t["url"]}
    casa = finestra_di_raccolta(schede, riquadri)
    if casa is not None and casa in con_home:
        print(M("tile.home_evicted", n=sfratta_home(casa), win=casa))
    if casa is None:
        prima = nomi[0]
        precedenti = {t["id"] for t in schede}
        prima_dei_client = client(prima)
        with open(SEGNAPOSTO, "w") as f:
            f.write(prima + "\n")
        apri_finestra_semplice()
        if not attendi_nuovo_client(prima, prima_dei_client):
            sys.exit(M("tile.no_home_window", name=prima))
        time.sleep(2)
        schede = bridge("get_tabs")
        nuove = [t for t in schede if t["id"] not in precedenti and t["url"].startswith(URL_TERMINALE)]
        if not nuove:
            sys.exit(M("tile.no_new_tab"))
        casa = nuove[0]["windowId"]
        print(M("tile.home_opened", win=casa, name=prima))
    else:
        print(M("tile.collecting", win=casa))
    for n in nomi:
        schede = bridge("get_tabs")
        coda = " " + etichetta(n)
        dentro = [t for t in schede if t["windowId"] == casa and t["title"].endswith(coda)]
        fuori = [t for t in schede if t["windowId"] != casa and t["url"].startswith(URL_TERMINALE) and t["title"].endswith(coda)]
        if not dentro:
            sorgente = sorgente_duplicabile(schede, casa)
            if not sorgente:
                sys.exit(M("tile.no_source", win=casa))
            prima_dei_client = client(n)
            with open(SEGNAPOSTO, "w") as f:
                f.write(n + "\n")
            r = bridge("tab_action", action="duplicate", tab_id=sorgente["id"])
            # T73 (09/09/2026): il Terminale di ChromeOS avvia la shell di una scheda duplicata
            # solo quando la MOSTRA; in una finestra coperta il duplicato resta vuoto, nessuna
            # shell legge il segnaposto e il merge scade dopo new_client_wait_s
            if r.get("duplicated"):
                bridge("tab_action", action="activate", tab_id=r["duplicated"])
            if not attendi_nuovo_client(n, prima_dei_client):
                if os.path.exists(SEGNAPOSTO):
                    os.remove(SEGNAPOSTO)
                sys.exit(M("tile.no_client", name=n, s=T["new_client_wait_s"]))
            print(f"  {n:24} " + M("tile.tab_attached", tab=r.get("duplicated")))
        else:
            print(f"  {n:24} " + M("tile.already_in"))
        for t in fuori:
            bridge("tab_action", action="close", tab_id=t["id"])
    sfratta_home(casa)   # la finestra nata da garcon (apri_finestra_semplice) porta la sua #home
    chiuse = pulisci_home()
    print(M("tile.merged", n=len(nomi), win=casa) + (" " + M("tile.home_closed", n=chiuse) if chiuse else ""))


# ---------------------------------------------------------------- sfratto della #home (T80)
def sfratta_home(wid):
    """La scheda iniziale «Terminale» (#home) NON si chiude finche' ha compagnia (T78), ma si puo'
    portare via DA SOLA: `move_tab` in un popup nuovo, poi `close` li' (chrome-bridge ed24b83,
    misurato dal vivo l'11/09/2026 su 8 sessioni). Solo per URL con #home, mai per posizione; solo
    se la finestra ha ALTRE schede (una finestra con la sola home che sta per ricevere la prima
    sessione si lascia stare). Torna quante ne ha sfrattate."""
    schede = bridge("get_tabs")
    mie = [t for t in schede if t["windowId"] == wid]
    homes = [t for t in mie if t["url"].startswith(URL_TERMINALE) and "#home" in t["url"]]
    if not homes or len(mie) <= len(homes):
        return 0
    n = 0
    for h in homes:
        try:
            r = bridge("move_tab", tab_id=h["id"], new_window=True, window_type="popup")
            if not (r.get("to_window") or r.get("window_id") or r.get("windowId")):
                continue
            bridge("tab_action", action="close", tab_id=h["id"])
            n += 1
        except SystemExit:
            continue
    return n


# ---------------------------------------------------------------- scheda (terminal.open_as_tab)
def finestra_di_raccolta(schede, riquadri):
    """La finestra app del Terminale con una shell duplicabile, preferendo quella SENZA #home (T78)."""
    tipo_di = {f["id"]: f.get("type") for f in riquadri}
    con_home = {t["windowId"] for t in schede if t["url"].startswith(URL_TERMINALE) and "#home" in t["url"]}
    candidate = []
    for t in schede:
        if tipo_di.get(t["windowId"]) == "popup" or t["windowId"] in candidate:
            continue
        if sorgente_duplicabile(schede, t["windowId"]):
            candidate.append(t["windowId"])
    return next((w for w in candidate if w not in con_home), candidate[0] if candidate else None)


def apri_scheda(nome, dry=False):
    """«Sempre tutte schede» (l'utente, 11/09/2026): la sessione nasce come scheda di una finestra del
    Terminale gia' aperta — stessa meccanica di merge (duplicate + segnaposto + client NUOVO, T27/T73).
    Senza nessuna finestra con una shell duplicabile si apre UNA finestra semplice via garcon con il
    segnaposto: la sua shell si attacca e resta la sorgente per le sessioni successive (una finestra
    sola anche per un restore di otto sessioni). Exit 0 = attaccata; 1 = fallita (niente garcon)."""
    schede, riquadri = tabs_and_windows()
    casa = finestra_di_raccolta(schede, riquadri)
    if dry:
        print(M("tile.tab_plan", name=nome, win=casa) if casa is not None else M("tile.tab_plan_new", name=nome))
        return 0
    prima = client(nome)
    with open(SEGNAPOSTO, "w") as f:
        f.write(nome + "\n")
    if casa is None:
        apri_finestra_semplice()
        if not attendi_nuovo_client(nome, prima, int(T["window_open_wait_s"]) + int(T["new_client_wait_s"])):
            if os.path.exists(SEGNAPOSTO):
                os.remove(SEGNAPOSTO)
            print(M("tile.no_home_window", name=nome), file=sys.stderr)
            return 1
        time.sleep(1)
        mia = scheda_di(nome, bridge("get_tabs"))
        if mia:
            sfratta_home(mia["windowId"])
        print(M("tile.tab_window_opened", name=nome))
        return 0
    sorgente = sorgente_duplicabile(schede, casa)
    r = bridge("tab_action", action="duplicate", tab_id=sorgente["id"])
    if r.get("duplicated"):
        bridge("tab_action", action="activate", tab_id=r["duplicated"])   # T73
    if not attendi_nuovo_client(nome, prima):
        if os.path.exists(SEGNAPOSTO):
            os.remove(SEGNAPOSTO)
        print(M("tile.no_client", name=nome, s=T["new_client_wait_s"]), file=sys.stderr)
        return 1
    sfratta_home(casa)
    print(M("tile.tab_opened", name=nome, win=casa, tab=r.get("duplicated")))
    return 0


# ---------------------------------------------------------------- monitor
def registro_carica():
    try:
        return json.load(open(REGISTRO_MONITOR))
    except (OSError, ValueError):
        return []


def registro_salva(aree):
    """Ogni misura riuscita arricchisce il registro; i doppioni si fondono. Il registro
    non comanda mai da solo: le voci vive vincono su quelle ricordate (T32)."""
    vecchie = registro_carica()
    for a in aree:
        if not (a and a.get("width")):
            continue
        for b in vecchie:
            if all(abs(a[k] - b[k]) <= TOLL for k in ("left", "top", "width", "height")):
                break
        else:
            vecchie.append({k: a[k] for k in ("left", "top", "width", "height")})
    try:
        os.makedirs(os.path.dirname(REGISTRO_MONITOR), exist_ok=True)
        json.dump(vecchie, open(REGISTRO_MONITOR, "w"))
    except OSError:
        pass


def leggi_avail(tab_id):
    r = bridge("execute_js", tab_id=tab_id,
               code="JSON.stringify({left:screen.availLeft|0,top:screen.availTop|0,width:screen.availWidth,height:screen.availHeight})")
    v = r.get("result") if isinstance(r, dict) else r
    return json.loads(v) if isinstance(v, str) else v


def misura_monitor(wid, riquadri_per_id):
    """Area utile del monitor su cui sta la finestra `wid`: si PRESTA una pagina scriptabile
    a quel monitor, si legge `screen.avail*`, la si rimette (T30). Strategie morte (T31):
    spingere a -6000, camminare a passi, bounds giganti — regola del 50% di Chrome.
    Mai una massimizzata come sonda (il tile su quello stato viene ignorato e la misura
    riporta il monitor SUO); una minimizzata va bene ma resta de-minimizzata."""
    f = riquadri_per_id.get(wid)
    if not f:
        return None
    d = bridge("get_tabs", include_windows=True)
    schede = d.get("tabs", d) if isinstance(d, dict) else d
    riquadri = {x["id"]: x for x in (d.get("windows", []) if isinstance(d, dict) else [])}

    def candidato(stati):
        for s in schede:
            w = riquadri.get(s["windowId"])
            if w and s["url"].startswith(("http://", "https://")) and w.get("state") in stati:
                return (s, w)
        return None
    sonda = candidato(("normal",)) or candidato(("minimized",))
    apri_e_chiudi = None
    if not sonda:
        try:
            nuovo = bridge("create_tab", url="https://example.com/", active=False)
            apri_e_chiudi = nuovo.get("tabId") or nuovo.get("id")
            time.sleep(2)
            d2 = bridge("get_tabs", include_windows=True)
            schede2 = d2.get("tabs", d2) if isinstance(d2, dict) else d2
            riq2 = {x["id"]: x for x in (d2.get("windows", []) if isinstance(d2, dict) else [])}
            s2 = next((s for s in schede2 if s.get("id") == apri_e_chiudi), None)
            if s2 and s2["windowId"] in riq2:
                sonda = (s2, riq2[s2["windowId"]])
        except SystemExit:
            pass
    if not sonda:
        return None
    scheda, casa = sonda
    originale = {k: casa[k] for k in ("left", "top", "width", "height")}
    prestito = {"left": f["left"] + 8, "top": f["top"] + 8, "width": 500, "height": 400}
    area = None
    try:
        bridge("tile_windows", window_ids=[casa["id"]], area=prestito, include_types=["normal", "popup", "app"])
        # dopo un cambio di display `screen.*` si aggiorna con ritardo: si rilegge finche' il
        # rettangolo non contiene il punto del prestito; un execute_js fallito si RIPROVA
        for _ in range(10):
            time.sleep(0.5)
            try:
                a = leggi_avail(scheda["id"])
            except (SystemExit, TypeError, ValueError):
                continue
            if (a and a.get("width") and a["left"] <= prestito["left"] <= a["left"] + a["width"]
                    and a["top"] <= prestito["top"] <= a["top"] + a["height"]):
                area = a
                break
            area = a or area
    except (SystemExit, ValueError, TypeError):
        area = None
    finally:
        try:
            bridge("tile_windows", window_ids=[casa["id"]], area=originale, include_types=["normal", "popup", "app"])
        except SystemExit:
            pass
        if apri_e_chiudi:
            try:
                bridge("tab_action", tab_id=apri_e_chiudi, action="close")
            except SystemExit:
                pass
    if area and area.get("width"):
        registro_salva([area])
        return area
    return None


def monitor_noti(riquadri, schede):
    """I monitor dedotti da cio' che e' gia' aperto, senza spostare niente: una finestra
    massimizzata combacia col suo monitor; `screen.avail*` da una scheda http(s) descrive il
    monitor di quella finestra. Un monitor vuoto lo trova solo `sonda_direzione`."""
    aree = []

    def aggiungi(a):
        for b in aree:
            if all(abs(a[k] - b[k]) <= TOLL for k in ("left", "top", "width", "height")):
                return
        aree.append(a)
    for f in riquadri:
        if f.get("state") == "maximized":
            aggiungi({k: f[k] for k in ("left", "top", "width", "height")})
    viste = set()
    for s in schede:
        if not s["url"].startswith(("http://", "https://")) or s["windowId"] in viste:
            continue
        viste.add(s["windowId"])
        try:
            a = leggi_avail(s["id"])
            if a:
                aggiungi(a)
        except (SystemExit, TypeError, ValueError):
            continue
    registro_salva(aree)
    for a in registro_carica():
        aggiungi(a)
    return aree


def battezza(aree):
    """Da rettangoli a nomi: e' l'ORIGINE (0,0) a dire qual e' il nativo, non la dimensione;
    gli altri si distinguono per posizione orizzontale."""
    posti, resto = {}, []
    for a in aree:
        if abs(a["left"]) <= 8 and abs(a["top"]) <= 8:
            posti["native"] = a
        else:
            resto.append(a)
    resto.sort(key=lambda a: a["left"])
    if len(resto) == 1:
        posti["above" if resto[0]["top"] < 0 else "external"] = resto[0]
    elif len(resto) == 2:
        posti["left"], posti["right"] = resto
    else:
        for i, a in enumerate(resto):
            posti["left" if i == 0 else "right" if i == len(resto) - 1 else f"center{i}"] = a
    return posti


def nome_posto(chiave):
    return NOMI.get(chiave, chiave)


def risolvi_verso(nome, posti, box):
    """Da un nome o una direzione a un posto vero (T38): `sinistra` trova il monitor alla
    sinistra delle finestre comunque si chiami; `sopra`/`sotto` = quello con la maggiore
    sovrapposizione orizzontale."""
    chiave = NOMI_INV.get(nome, nome)
    if chiave in posti:
        return posti[chiave]
    verso = DIREZIONI.get(nome, chiave if chiave in ("above", "below", "left", "right") else None)
    if not verso or not box:
        return None
    cx = (box["left"] + box["right"]) / 2
    cy = (box["top"] + box["bottom"]) / 2
    # il monitor che CONTIENE gia' le finestre non e' «a destra» di se stesso: si esclude
    # (visto nei test del 09/09: con un solo esterno, `move destra` sceglieva il nativo)
    posti = {k: x for k, x in posti.items()
             if not (x["left"] <= cx <= x["left"] + x["width"] and x["top"] <= cy <= x["top"] + x["height"])}
    if verso == "above":
        cand = [x for x in posti.values() if x["top"] + x["height"] / 2 < cy]
    elif verso == "below":
        cand = [x for x in posti.values() if x["top"] + x["height"] / 2 > cy]
    elif verso == "left":
        cand = [x for x in posti.values() if x["left"] + x["width"] / 2 < cx]
    elif verso == "right":
        cand = [x for x in posti.values() if x["left"] + x["width"] / 2 > cx]
    else:
        return None
    if not cand:
        return None

    def affaccio(x):
        if verso in ("above", "below"):
            return min(x["left"] + x["width"], box["right"]) - max(x["left"], box["left"])
        return min(x["top"] + x["height"], box["bottom"]) - max(x["top"], box["top"])
    return max(cand, key=affaccio)


def sonda_direzione(wid, verso, per_id):
    """Cerca un monitor in una direzione spingendo una finestra a passi di mezza finestra
    (ogni tappa e' legale su QUALCHE schermo); si misura dove si e' ARRIVATI anche se non ci
    si e' mossi; la finestra torna dov'era."""
    def leggi():
        d = bridge("get_tabs", include_windows=True)
        for f in (d.get("windows", []) if isinstance(d, dict) else []):
            if f["id"] == wid:
                return f
        return None
    f = leggi()
    if not f:
        return None
    originale = {k: f[k] for k in ("left", "top", "width", "height")}
    v = DIREZIONI.get(verso, NOMI_INV.get(verso, verso))
    dx, dy = {"left": (-1, 0), "right": (1, 0), "above": (0, -1), "below": (0, 1), "native": (0, 1)}.get(v, (0, 0))
    if not (dx or dy):
        return None
    passo_x = max(60, originale["width"] // 2 - 8) * dx
    passo_y = max(60, originale["height"] // 2 - 8) * dy
    x, y = originale["left"], originale["top"]
    for _ in range(80):
        try:
            bridge("tile_windows", window_ids=[wid], area={"left": x + passo_x, "top": y + passo_y,
                   "width": originale["width"], "height": originale["height"]}, include_types=["normal", "popup", "app"])
        except SystemExit:
            break
        b = leggi()
        if not b or (b["left"], b["top"]) == (x, y):
            break
        x, y = b["left"], b["top"]
    area = misura_monitor(wid, {wid: {"left": x, "top": y, "width": originale["width"], "height": originale["height"]}})
    bridge("tile_windows", window_ids=[wid], area=originale, include_types=["normal", "popup", "app"])
    return area


# ---------------------------------------------------------------- tile
def tabs_and_windows():
    tutte = bridge("get_tabs", include_windows=True)
    riquadri = tutte.get("windows", []) if isinstance(tutte, dict) else []
    schede = tutte.get("tabs", tutte) if isinstance(tutte, dict) else tutte
    return schede, riquadri


def affianca(a):
    vive = sessioni_vive()
    if not vive:
        sys.exit(M("tile.no_sessions"))
    if a.sessioni:
        ignote = [n for n in a.sessioni if n not in vive]
        if ignote:
            sys.exit(M("tile.unknown_sessions", names=", ".join(ignote), live=", ".join(vive)))
        nomi = a.sessioni
    else:
        nomi = vive
    rinfresca_titoli()
    schede = bridge("get_tabs")
    mancanti = [n for n in nomi if not scheda_di(n, schede)]

    if a.dry_run:
        verso = M("tile.mode_merge") if a.merge else M("tile.mode_columns")
        if a.on:
            schede2, riq = tabs_and_windows()
            per_id_p = {f["id"]: f for f in riq}
            b = [per_id_p[s["windowId"]] for n in nomi if (s := scheda_di(n, schede2)) and s["windowId"] in per_id_p]
            box_p = {"left": min(x["left"] for x in b), "top": min(x["top"] for x in b),
                     "right": max(x["left"] + x["width"] for x in b), "bottom": max(x["top"] + x["height"] for x in b)} if b else None
            posti = battezza(monitor_noti(riq, schede2))
            x = risolvi_verso(a.on, posti, box_p)
            if x:
                print(M("tile.plan_monitor", name=a.on, w=x["width"], h=x["height"], l=x["left"], t=x["top"], col=x["width"] // max(1, len(nomi))))
            else:
                print(M("tile.plan_no_monitor", name=a.on, known=", ".join(nome_posto(k) for k in sorted(posti)) or "-"))
        print(f"{len(nomi)} {verso}: {', '.join(nomi)}")
        for n in nomi:
            s = scheda_di(n, schede)
            print(f"  {n:24} " + (M("tile.would_open") if not s else M("tile.tab_in_window", tab=s["id"], win=s["windowId"])))
        return
    if a.merge:
        unisci(nomi)
        return

    # 1. le sessioni senza scheda ne ricevono una
    if mancanti:
        for n in mancanti:
            apri_finestra(n)
        for _ in range(int(T["window_open_wait_s"])):
            time.sleep(1)
            schede = bridge("get_tabs")
            if all(scheda_di(n, schede) for n in mancanti):
                break
        else:
            persi = [n for n in mancanti if not scheda_di(n, schede)]
            sys.exit(M("tile.window_missing", names=", ".join(persi)))

    # 2. una scheda per finestra: chi convive riparte in una finestra app propria via garcon (la prima
    #    resta: staccarle tutte lascerebbe la finestra con la sola #home, che pulisci_home chiude)
    d0 = bridge("get_tabs", include_windows=True)
    tipo0 = {f["id"]: f.get("type") for f in (d0.get("windows", []) if isinstance(d0, dict) else [])}
    per_finestra = {}
    for n in nomi:
        per_finestra.setdefault(scheda_di(n, schede)["windowId"], []).append(n)
    finestre = []
    for wid, gruppo in per_finestra.items():
        # finestre POPUP (staccate dalle versioni precedenti): tutte le loro sessioni in finestre app
        resta = [] if tipo0.get(wid) == "popup" else gruppo[:1]
        if resta:
            finestre.append((resta[0], wid))
        for n in gruppo[len(resta):]:
            finestre.append((n, stacca_in_finestra_app(n, schede)))
            schede = bridge("get_tabs")
    # 2b. le finestre MASSIMIZZATE vanno rifatte anche da sole (T34): Chrome ignora i bounds
    #     in quello stato; in una finestra nuova lo stato e' normal. Vale anche per le MINIMIZZATE
    #     (T72, 09/09/2026: 4/6 «NON applicata» sul nativo, tutte finestre ridotte a icona)
    d = bridge("get_tabs", include_windows=True)
    stato = {f["id"]: f.get("state") for f in (d.get("windows", []) if isinstance(d, dict) else [])}
    for i, (n, wid) in enumerate(finestre):
        if stato.get(wid) not in ("maximized", "minimized", "fullscreen"):
            continue
        nuova = stacca_in_finestra_app(n, schede)
        schede = bridge("get_tabs")
        print(M("tile.maximized_detached", name=n, state=stato.get(wid)))
        finestre[i] = (n, nuova)
    ordinate = [wid for n in nomi for (m_, wid) in finestre if m_ == n]   # ordine richiesto (T35)
    layout = "rows" if a.rows else "grid" if a.grid else "columns"

    schede, riquadri = tabs_and_windows()
    per_id = {f["id"]: f for f in riquadri}

    def riquadro(ids):
        b = [per_id[i] for i in ids if i in per_id]
        if not b:
            return None
        return {"left": min(x["left"] for x in b), "top": min(x["top"] for x in b),
                "right": max(x["left"] + x["width"] for x in b), "bottom": max(x["top"] + x["height"] for x in b)}

    def dentro(f, r):
        """Il CENTRO della finestra sta nel riquadro (T33): la sovrapposizione non basta."""
        cx = f["left"] + f["width"] / 2
        cy = f["top"] + f["height"] / 2
        return r["left"] <= cx <= r["right"] and r["top"] <= cy <= r["bottom"]
    box = riquadro(ordinate)

    # sessioni sparse su piu' monitor (T39): si radunano dove sta la maggioranza; a parita',
    # dove sta la prima dell'ordine chiesto
    if not a.on and len(ordinate) > 1:
        posti_ora = battezza(monitor_noti(riquadri, schede))
        conta = {}
        for wid in ordinate:
            f = per_id.get(wid)
            if not f:
                continue
            cx, cy = f["left"] + f["width"] / 2, f["top"] + f["height"] / 2
            for nome, x in posti_ora.items():
                if x["left"] <= cx <= x["left"] + x["width"] and x["top"] <= cy <= x["top"] + x["height"]:
                    conta.setdefault(nome, []).append(wid)
                    break
        if len(conta) > 1:
            casa = max(conta, key=lambda n: (len(conta[n]), ordinate[0] in conta[n]))
            print(M("tile.scattered", n=len(conta), where=nome_posto(casa)))
            a.on = nome_posto(casa)

    riferimento_scheda = None
    if box:
        for s in schede:
            if not s["url"].startswith(("http://", "https://")):
                continue
            f = per_id.get(s["windowId"])
            if f and dentro(f, box):
                riferimento_scheda = s["id"]
                break

    scelto = None
    if a.on:
        posti = battezza(monitor_noti(riquadri, schede))
        scelto = risolvi_verso(a.on, posti, box)
        if not scelto:
            scelto = sonda_direzione(ordinate[0], a.on, per_id)
            if scelto:
                for x in posti.values():
                    if all(abs(scelto[k] - x[k]) <= TOLL for k in ("left", "top", "width", "height")):
                        scelto = None
                        break
        if not scelto:
            sys.exit(M("tile.no_monitor_there", name=a.on, known=", ".join(nome_posto(k) for k in sorted(posti)) or "-"))
    if scelto:
        # T32: una voce del registro puo' essere STANTIA (monitor scollegato o rimappato): un
        # tile di prova la smaschera; «visible screen space» la elimina dal registro
        r = bridge("tile_windows", window_ids=[ordinate[0]],
                   area={"left": scelto["left"] + 8, "top": scelto["top"] + 8,
                         "width": min(600, scelto["width"] - 16), "height": min(500, scelto["height"] - 16)},
                   include_types=["normal", "popup", "app"])
        esiti = r.get("results", [])
        if esiti and "visible screen space" in str(esiti[0].get("error") or ""):
            vive_reg = [b for b in registro_carica() if not all(abs(scelto[k] - b[k]) <= TOLL for k in ("left", "top", "width", "height"))]
            try:
                json.dump(vive_reg, open(REGISTRO_MONITOR, "w"))
            except OSError:
                pass
            sys.exit(M("tile.stale_monitor", name=a.on, w=scelto["width"], h=scelto["height"], l=scelto["left"], t=scelto["top"]))
        area = scelto
        print(M("tile.monitor_line", name=nome_posto(a.on), w=area["width"], h=area["height"], l=area["left"], t=area["top"]))
    elif a.area:
        l, top, w, h = (int(x) for x in a.area.split(","))
        area = {"left": l, "top": top, "width": w, "height": h}
    elif riferimento_scheda:
        area = leggi_avail(riferimento_scheda)
    elif box:
        area = misura_monitor(ordinate[0], per_id)
        if area:
            print(M("tile.measured", w=area["width"], h=area["height"], l=area["left"], t=area["top"]))
        else:
            area = {"left": box["left"], "top": box["top"], "width": box["right"] - box["left"], "height": box["bottom"] - box["top"]}
            print(M("tile.unmeasured"))
    else:
        sys.exit(M("tile.no_reference"))

    n = len(ordinate)
    # DISPARI con la master (l'utente, 11/09/2026 15:50; `tile.odd_layout`: master-primary | uniform):
    # la sessione della radice diventa la finestra grande a sinistra (60%), le altre impilate a
    # destra in n-1 righe. Solo col layout di default (colonne), con n >= 3 dispari e la master
    # fra le affiancate; altrimenti il ripiego uniforme di sempre. La colonna impilata puo' scendere
    # sotto min_column_px: consentito, segnalato, mai rifiutato.
    master_nome = CFG["workspace"]["root_session_name"]
    if (layout == "columns" and n >= 3 and n % 2 == 1 and master_nome in nomi
            and str(T.get("odd_layout", "master-primary")) == "master-primary"):
        layout = "main-vertical"
    # colonne sotto tile.min_column_px non sono piu' un terminale (80 colonne di testo non ci
    # stanno): si passa da soli a griglia, come la doc prometteva e il codice legacy non faceva
    if layout == "columns" and n > 1 and (area["width"] - a.padding * 2) // n < SOGLIA:
        print(M("tile.narrow_grid", px=(area["width"] - a.padding * 2) // n, min=SOGLIA))
        layout = "grid"
    if layout == "columns":
        colonne, righe = n, 1
    elif layout == "rows":
        colonne, righe = 1, n
    else:
        colonne = max(1, min(n, area["width"] // SOGLIA))
        righe = math.ceil(n / colonne)
        colonne = math.ceil(n / righe)
    esiti = []
    if layout == "main-vertical":
        wid_master = next(w for n_, w in finestre if n_ == master_nome)
        pila = [w for w in ordinate if w != wid_master]
        larga = int((area["width"] - a.padding * 2) * 0.6)
        stretta = area["width"] - a.padding * 2 - larga
        riga = (area["height"] - a.padding * 2) // len(pila)
        if stretta < SOGLIA:
            print(M("tile.narrow_stack", px=stretta, min=SOGLIA))
        slots = [(wid_master, {"left": area["left"] + a.padding, "top": area["top"] + a.padding, "width": larga, "height": area["height"] - a.padding * 2})]
        slots += [(w, {"left": area["left"] + a.padding + larga, "top": area["top"] + a.padding + i * riga, "width": stretta, "height": riga}) for i, w in enumerate(pila)]
        colonne, righe = 2, len(pila)
    else:
        passo_x = (area["width"] - a.padding * 2) // colonne
        passo_y = (area["height"] - a.padding * 2) // righe
        slots = [(wid, {"left": area["left"] + a.padding + (i % colonne) * passo_x, "top": area["top"] + a.padding + (i // colonne) * passo_y,
                        "width": passo_x, "height": passo_y}) for i, wid in enumerate(ordinate)]
    # UNA PER VOLTA con l'area del proprio slot (T35): passandole tutte insieme tile_windows
    # sceglie da se' quale va dove e l'ordine chiesto si perde
    for wid, slot in slots:
        r = bridge("tile_windows", window_ids=[wid], area=slot, include_types=["normal", "popup", "app"])
        esiti += r.get("results", [])
    chiuse = pulisci_home()
    piazzate = sum(1 for r in esiti if r.get("applied"))
    if chiuse:
        print(M("tile.home_closed", n=chiuse))
    print(M("tile.done", n=piazzate, tot=len(ordinate), layout=layout, w=area["width"], h=area["height"]))
    for n_, wid in ((n_, w) for n_ in nomi for (m_, w) in finestre if m_ == n_):
        r = next((x for x in esiti if x.get("window_id") == wid), None)
        stato_ = "ok" if r and r.get("applied") else M("tile.not_applied")
        # `actual` c'e' solo quando la finestra e' stata davvero spostata (T37): leggerla
        # faceva morire lo script DOPO aver disposto tutto
        dove = (r or {}).get("actual") or (r or {}).get("bounds") or {}
        pos = f"{dove['left']},{dove['top']}" if dove.get("left") is not None else "—"
        print(f"  {n_:24} {stato_:12} {pos}")


def layout_cmd(argv):
    azione = argv[0] if argv else "list"
    nome = argv[1] if len(argv) > 1 else ""
    if azione in ("save", "salva"):
        if not nome:
            sys.exit(M("tile.layout_usage"))
        r = bridge("window_layout", action="save", name=nome)
        vive = sessioni_vive()
        p = Path(cm.expand(CFG["state_dir"])) / "layouts"
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{nome}.json").write_text(json.dumps({"sessions": vive, "saved": time.strftime("%Y-%m-%dT%H:%M:%S")}))
        print(M("tile.layout_saved", name=nome, n=len(vive)))
    elif azione in ("restore", "ripristina"):
        if not nome:
            sys.exit(M("tile.layout_usage"))
        p = Path(cm.expand(CFG["state_dir"])) / "layouts" / f"{nome}.json"
        try:
            attese = json.loads(p.read_text()).get("sessions", [])
        except (OSError, ValueError):
            attese = []
        vive = set(sessioni_vive())
        mancano = [s for s in attese if s not in vive]
        if mancano:
            print(M("tile.layout_missing", names=", ".join(mancano)))
        r = bridge("window_layout", action="restore", name=nome)
        if isinstance(r, dict) and "matched" in r:
            print(M("tile.layout_restored", name=nome, n=r.get("matched", 0), missing=r.get("unmatched_saved", 0), extra=len(r.get("unmatched_current") or [])))
            for x in r.get("results", []):
                w = x.get("window") or {}
                print(f"  {x.get('window_id')}  " + (M("tile.not_applied") + f" ({x['error']})" if x.get("error") else f"ok  {w.get('left')},{w.get('top')} {w.get('width')}x{w.get('height')}"))
        else:
            print(json.dumps(r, ensure_ascii=False)[:600])
    else:
        r = bridge("window_layout", action="list")
        rows = (r or {}).get("layouts", []) if isinstance(r, dict) else []
        for x in rows:
            if not isinstance(x, dict):
                x = {"name": str(x)}
            print(f"  {x.get('name'):<24} {x.get('windows', '?')} finestre  {str(x.get('savedAt', ''))[:16]}")
        if not rows:
            print(M("tile.layout_none"))


def chiudi_scheda(nome, dry=False):
    """07/10/2026 (`close` di una sessione attaccata e idle, ok del maintainer): chiude la scheda del Terminale che
    mostra quella sessione; con destroy-unattached la sessione finisce da sola. La scheda si prende per URL
    (`args[]=<nome>`, esatto) o, in mancanza, per titolo SOLO se una sola scheda corrisponde: meglio non trovarla che
    chiudere quella di un'altra sessione. 0 chiusa (o da chiudere, a secco), 3 nessuna scheda certa."""
    import urllib.parse
    schede = bridge("get_tabs")
    # l'argomento esatto: «args[]=alfa» e' anche il prefisso di «args[]=alfa-2»
    per_url = [s for s in schede if s["url"].startswith(URL_TERMINALE)
               and nome in urllib.parse.parse_qs(urllib.parse.urlsplit(s["url"]).query).get("args[]", [])]
    coda = " " + etichetta(nome)
    per_titolo = [s for s in schede if s["url"].startswith(URL_TERMINALE) and s["title"].endswith(coda)]
    scelta = per_url if len(per_url) == 1 else (per_titolo if not per_url and len(per_titolo) == 1 else [])
    if not scelta:
        print(M("tile.close_tab_none", name=nome, n=len(per_url) or len(per_titolo)), file=sys.stderr)
        return 3
    t = scelta[0]
    if not dry:
        bridge("tab_action", action="close", tab_id=t["id"])
    print(t.get("title") or t["id"])
    return 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["close-tab"]:
        # da cm-close.sh: senza bridge o fuori da ChromeOS 5, e close rifiuta senza ripieghi distruttivi
        if not CLI or not os.path.exists(CLI):
            sys.exit(5)
        backend = subprocess.run([str(HERE / "cm-terminal.sh"), "detect"], capture_output=True, text=True).stdout.strip()
        if backend not in ("chromeos", "fake"):
            sys.exit(5)
        resto = [x for x in argv[1:] if x not in ("--dry-run", "--prova")]
        if not resto:
            sys.exit(2)
        sys.exit(chiudi_scheda(resto[0], dry=any(x in argv for x in ("--dry-run", "--prova"))))
    if argv[:1] == ["open-tab"]:
        # da cm-terminal.sh: senza bridge o fuori da ChromeOS si torna 5 e il chiamante usa garcon
        if not CLI or not os.path.exists(CLI):
            sys.exit(5)
        backend = subprocess.run([str(HERE / "cm-terminal.sh"), "detect"], capture_output=True, text=True).stdout.strip()
        if backend not in ("chromeos", "fake"):
            sys.exit(5)
        resto = [x for x in argv[1:] if x not in ("--dry-run", "--prova", "ephemeral")]
        if not resto:
            sys.exit(M("tile.tab_usage"))
        sys.exit(apri_scheda(resto[0], dry=any(x in argv for x in ("--dry-run", "--prova"))))
    if not CLI or not os.path.exists(CLI):
        sys.exit(M("tile.needs_bridge", path=T["chrome_bridge_cli"] or "-"))
    backend = subprocess.run([str(HERE / "cm-terminal.sh"), "detect"], capture_output=True, text=True).stdout.strip()
    if backend not in ("chromeos", "fake"):
        sys.exit(M("tile.needs_chromeos", backend=backend))
    sub = argv[0] if argv and argv[0] in ("tile", "merge", "move", "where", "layout") else "tile"
    if argv and argv[0] == sub:
        argv = argv[1:]
    if sub == "layout":
        layout_cmd(argv)
        return
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("sessioni", nargs="*")
    ap.add_argument("--rows", "--righe", dest="rows", action="store_true")
    ap.add_argument("--grid", "--griglia", dest="grid", action="store_true")
    ap.add_argument("--merge", "--unisci", "--raccogli", dest="merge", action="store_true")
    ap.add_argument("--padding", type=int, default=int(T["padding"]))
    ap.add_argument("--area")
    ap.add_argument("--on", "--su", dest="on", metavar="POSTO")
    ap.add_argument("--where", "--dove", dest="where", action="store_true")
    ap.add_argument("--dry-run", "--prova", dest="dry_run", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    if sub == "move":
        if not argv or argv[0].startswith("-"):
            sys.exit(M("tile.move_usage"))
        argv = ["--on", argv[0]] + argv[1:]
    a = ap.parse_args(argv)
    if a.help:
        print(__doc__)
        return
    if sub == "merge":
        a.merge = True
    if sub == "where" or a.where:
        schede, riq = tabs_and_windows()
        posti = battezza(monitor_noti(riq, schede))
        if not posti:
            sys.exit(M("tile.no_monitors"))
        for k, x in sorted(posti.items(), key=lambda kv: (kv[1]["top"], kv[1]["left"])):
            print(f"  {nome_posto(k):10} {x['width']}x{x['height']:<6} ({x['left']},{x['top']})")
        print("\n" + M("tile.where_note"))
        return
    affianca(a)


if __name__ == "__main__":
    main()
