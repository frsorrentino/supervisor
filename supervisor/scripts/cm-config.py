#!/usr/bin/env python3
"""Configurazione di supervisor: default, file, rilevamento della macchina.

Un solo file per tutta la macchina (~/.config/cc-supervisor/config.json): la
configurazione descrive la postazione, non il login, quindi vale per tutti gli
account. Nessuno script legge il JSON da solo: gli script bash fanno
`eval "$(cm-config.py --sh)"`, quelli python importano questo modulo.

Sottocomandi:
  --sh                 export CM_* con i default applicati (per gli script bash)
  --messages           export MSG_* del catalogo nella lingua configurata
  --msg KEY k=v ...    un messaggio formattato (per gli script bash)
  --account-for DIR    l'account dedotto da folder_map per DIR (vuoto se nessuno)
  --get KEY            un valore (chiave puntata); ~ espansa; liste/dict in JSON
  --dump-defaults      i default in JSON (genera config.example.json)
  --path               percorso del file di configurazione
  init [--dry-run|--yes] [--force] [--json] [--shim] [--shell] [--cron] [--tmux]
                       legge la macchina e propone/scrive la configurazione;
                       --shell/--cron/--tmux stampano (con --yes installano) i blocchi
                       per rc di shell, crontab e ~/.tmux.conf
  doctor               PASS/WARN/FAIL con rimedio; exit 1 se un FAIL

Variabili d'ambiente: CC_SUPERVISOR_CONFIG (file), CM_HOME (home finta, test),
CM_FAKE_MACHINE (descrizione JSON della macchina al posto di /proc, PATH e
ambiente — solo test).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent

# ----------------------------------------------------------------- default
# Colonna «default» del piano §4. I valori che dipendono dalla lingua stanno in
# BY_LANG e si applicano sopra questi quando `language` lo dice.
DEFAULTS = {
    "language": "en",
    "plugin_root": "",
    "state_dir": "~/.local/state/cc-supervisor",
    "workspace": {
        "root": "~",
        "root_session_name": "master",
        "excluded_dirs": [".git", "node_modules"],
        "project_dirs": ["."],
    },
    "accounts": {
        "default": {
            "config_dir": "~/.claude",
            "tmux_prefix": "",
            "shape": "circle",
            "shell_command": "claude",
            "label": "default",
            "kind": "",   # 1.8: personal | work per il relay; vuoto = default_account personal, gli altri work
        }
    },
    "default_account": "default",
    "accounts_warn_on_mismatch": True,
    "folder_map": [],
    "session": {
        "claude_args": [],
        "remote_control": True,
        "name_sanitize_chars": ".:",
        "startup_timeout_s": 90,
        # 06/10/2026 (revisione della directory dei plugin: un plugin non approva da solo i dialoghi di sicurezza):
        # vuoto di default, `launch` si ferma sul dialogo e chiede di accettarlo a mano; chi vuole che il lancio
        # risponda «Yes» da solo li scrive qui, per esempio "trust (this|the) folder", "Bypass Permissions mode"
        "dialog_patterns": [],
        "env": {},
        "window_by_default": True,
        "bg_remote_control": False,
        "death_check_s": 3,
        "link_wait_s": 20,
    },
    "terminal": {
        "backend": "auto",
        "garcon": "/opt/google/cros-containers/bin/garcon",
        "open_as_tab": True,
        "attach_wait_s": 25,
        "attach_retry_wait_s": 20,
        "ephemeral": True,
        "headless_skip": True,
    },
    "tabs": {
        "title": True,
        "strip_prefix": True,
        "colors": {
            "circle": ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚪"],
            "square": ["🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "⬜"],
        },
        "color_registry": "",
    },
    "tile": {
        "chrome_bridge_cli": "",
        "terminal_url": "chrome-untrusted://terminal/",
        "min_column_px": 340,
        "odd_layout": "master-primary",
        "padding": 0,
        "monitor_names": {
            "native": "native", "left": "left", "right": "right", "above": "above",
            "below": "below", "external": "external", "center": "center",
        },
        "monitor_registry": "",
        "placeholder_file": "",
        "placeholder_ttl_s": 120,
        "new_client_wait_s": 60,
        "window_open_wait_s": 20,
        "log": "",
    },
    "profiles": {},
    "registry": {"file": "", "good_file": "", "cron_minutes": 5},
    "restore": {"uptime_max_min": 15, "confirm_timeout_s": 15, "snapshot_window_min": 60, "last": ""},
    "restart": {"flag_file": "", "log": "", "exit_wait_s": 20, "term_wait_s": 10, "kill_wait_s": 15, "dead_wait_s": 120},
    "talk": {"quiet_s": 6, "max_wait_s": 240, "warn_native_channel": True,
             "from_name": "supervisor", "from_mode": "bypass"},
    "report": {"subdir": "docs/reports", "image_exts": ["png", "jpg", "jpeg", "webp", "gif"]},
    "sessions": {"stall_min": 20, "recent_min": 5, "max_sessions": 5, "idle_hours": 2},
    "quota": {"source": "~/.claude/fable-director", "warn_pct": 85},
    # spento di default (l'utente, 10/09/2026): la master resta l'unico ingresso dal telefono; lo
    # sportello apre sessioni senza progetto, con nome casuale e cartella fissa, e costa un
    # processo sempre acceso
    "desk": {"enabled": False, "name": "sportello", "capacity": 4, "permission_mode": "default"},
    # S09 (14/09/2026): prova di un secondo agente, spenta di default — `launch --agent codex` e le sessioni Codex
    # in `sessions` (stato dal rollout di ~/.codex); vedi la nota di prova in docs/
    "experimental": {"codex": False},
    # Telegram a SENSO UNICO dal 16/09/2026 (ritiro del bot interattivo): solo spedizione — diario, rapporto
    # della notte, avvisi della guardia, scorta del polso. Token e chat autorizzate restano quelli del plugin
    # `telegram` di Claude Code (scelta dell'utente, 10/09/2026); niente poller, niente stato per chat
    "bot": {"enabled": False, "token_file": "~/.claude/channels/telegram/.env",
            "access_file": "~/.claude/channels/telegram/access.json",
            "api_base": "https://api.telegram.org", "log": "",
            "http_timeout_s": 20, "command_timeout_s": 120,
            "quiet_when_watch": True, "watch_fresh_s": 180},
    # recap serale dal ledger (N7): ora del cron, citazioni, riassunto col modello, riga del giorno nei progetti
    # last: none | short (solo le ferme su domanda, 60 caratteri) | full; hide_zero_turns: le chiuse
    # senza turni non compaiono
    "recap": {"cron_time": "20:00", "max_last_chars": 160, "last": "short", "hide_zero_turns": True, "closed_chars": 90,
              "summary": "model", "summary_model": "haiku", "summary_timeout_s": 120, "project_log": "docs/recap.md", "startup_lines": 5,
              "min_turns": 3},
    # guardia della quota: avviso sopra warn_pct (una volta per finestra), ripresa delle sessioni
    # fallite e della coda notturna quando la finestra si resetta
    "guard": {"warn_pct": 95, "cron_minutes": 5, "night_after_reset": True, "night_timeout_s": 3600,
              "resume_prompt": "La quota è tornata disponibile: riprendi da dove eri rimasto.",
              # 1.67 (14/10): il reset settimanale — avviso se mancano week_unused_hours al reset e la settimana e' sotto
              # week_unused_pct (c'e' quota da usare, per esempio di notte); avviso se al ritmo di adesso finirebbe piu' di
              # week_pace_margin_h ore prima del reset (solo da week_pace_min_pct in su)
              "week_unused_hours": 12, "week_unused_pct": 60, "week_pace_margin_h": 12, "week_pace_min_pct": 30},
    # 14/10 (dall'analisi dei concorrenti): cambio automatico di account al limite, solo per gli account elencati. Sopra
    # at_pct (5 ore o settimana, il piu' alto) le sessioni NUOVE di quell'account partono sull'altro; si torna sotto
    # back_pct e dopo almeno min_s (isteresi e attesa). Di notte (night_hours) la strada «altro account» della 1.58
    # parte da sola. Ogni sessione cosi' manda il suo lavoro all'altra organizzazione: per questo e' spento finche'
    # l'utente non elenca gli account
    "auto_switch": {"accounts": [], "at_pct": 90, "back_pct": 75, "min_s": 1800, "night_hours": [0, 7]},
    # turno di notte (N5): coda di lavori `claude -p` non presidiati, con guardie su RAM e quota
    "night": {"cron_time": "02:00", "min_free_mb": 1500, "max_quota_pct": 80, "item_timeout_s": 3600,
              # 06/10/2026: "default" (nessun permesso allargato); per i lavori che scrivono file serve "acceptEdits",
              # scelto dall'utente nella sua config
              "max_turns": 40, "permission_mode": "default", "tool_memory_limit": "2g", "retry_watchdog_max_wait_ms": 900000,
              # 16/10/2026 (Claude Code 2.1.301): al limite d'uso il watchdog aspetta il reset solo fino a qui, poi
              # chiude la richiesta (un limite settimanale puo' azzerarsi fra giorni); 0 = non impostata
              "retry_watchdog_max_usage_limit_wait_ms": 900000,
              # 16/10/2026 (Claude Code 2.1.292): `claude -p` aspetta i comandi in background e le sveglie dopo il
              # risultato; la voce si chiude dopo after_result_s senza attivita' dal risultato (0 = fino a item_timeout_s)
              "after_result_s": 300,
              "out_subdir": "docs/notte", "max_items_per_run": 3, "max_queued": 8, "queue_file": "", "done_file": "", "log": ""},
    # relay per l'app Wear OS (0.4.0): Firebase RTDB + FCM dietro cm-relay.py; service account e chiave in relay.dir
    "relay": {"enabled": False, "firebase_url": "", "service_account": "~/.cc-supervisor/relay/service-account.json",
              "fcm_topic": "watch", "tier_high": ["rm -rf", "git push", "deploy", "DROP", "ssh", "sudo", "--force", "git reset --hard"],
              "state_max_kb": 8, "events_days": 7, "dir": "~/.cc-supervisor/relay", "heartbeat_s": 60, "debounce_s": 2,
              "token_url": "https://oauth2.googleapis.com/token", "fcm_url": "https://fcm.googleapis.com", "host": "",
              "log": "", "command_timeout_s": 120, "pair_ttl_s": 300, "pair_attempts": 5,
              "web_app_url": "https://supervisor.francescosorrentino.com", "serve_timeout_s": 50,
              "colors": {}, "awaiting_max_s": 1800, "agenda_file": "", "agenda_owner": "owner", "duplicate_prompt_s": 60,
              # scorta su Telegram quando il polso non riceve da tanto (0 = mai): 10 minuti
              "telegram_fallback_after_s": 600,
              # contesto di una sessione (1.13.1): dove una statusline salva la finestra dichiarata da Claude Code, e i
              # modelli il cui id non dice se la finestra e' da 1M o standard (sotto i 200k token il contesto resta null)
              "window_hint_dir": "~/.claude/fable-director/sessions", "window_unmarked": ["claude-fable-5-1"],
              # modelli da 1M che non portano [1m] nell'id (16/10/2026: Haiku 5.5, 1M di contesto)
              "window_1m": ["claude-haiku-5-5"],
              # 1.15 (24/09/2026): i dati dell'app Firebase nel QR di `relay pair` — o qui, o letti da un
              # google-services.json (project_info + il client con il pacchetto dell'app). Senza: niente QR, il codice basta.
              "firebase_app": {"api_key": "", "project_id": "", "app_id": ""}, "google_services": "",
              # il client Android da prendere in quel file quando ne ha piu' d'uno (vuoto: l'unico che c'e')
              "app_package": "",
              # 1.32: pair --add apre l'invito nell'app dell'Android di questa macchina (ARC); "" = /usr/bin/adb o il PATH
              "adb": "", "adb_serial": "emulator-5554",
              # dove `relay setup` crea l'istanza del Realtime Database
              "setup_location": "europe-west1",
              # 1.25 (02/10/2026): i comandi slash che il telefono puo' dare a una sessione (senza «/»), in state.slash
              "slash_commands": ["compact", "clear", "exit", "context", "cost"],
              # 1.35 (05/10/2026): la web app e l'API locale su 127.0.0.1 dentro `relay serve`; dir = la cartella
              # compilata della web app (web/dist del repo dell'app), servita su /
              "web": {"enabled": False, "port": 8765, "dir": ""},
              # 1.37 (05/10/2026): dove la statusline di fable-director scrive lo snapshot di ogni sessione
              # (<session_id>.json, chiave "advice": il consiglio di modello ed effort), e fin quando vale
              "advice_dir": "~/.claude/fable-director/sessions", "advice_max_age_s": 21600},
    # cambio di modello ed effort di una sessione dal suo selettore, SOLO per quella sessione (16/09/2026, contratto
    # 1.12): `pick` e' l'etichetta della voce nel selettore di /model, `id` quello che la sessione riporta in model.id.
    # Dalla 2.1.280 (catturato il 22/09/2026) «Opus (1M context)» e' Opus 5.5 e Opus 5 non ha piu' una voce: tenere
    # claude-opus-5[1m] su quell'etichetta avrebbe scelto Opus 5.5 senza errore. Dal 16/10/2026 Sonnet 5.5 e Haiku 5.5
    # (Claude Code 2.1.293: l'alias «haiku» gira su claude-haiku-5-5), id e nomi come li scrivono le trascrizioni.
    # compact_timeout_s (2.1.301): verso una finestra piu' piccola la conversazione si riassume prima del cambio; finche'
    # lo schermo mostra il riassunto non si preme Esc e si aspetta la conferma fino a qui
    "tune": {"timeout_s": 8, "compact_timeout_s": 300, "file": "", "efforts": ["low", "medium", "high", "xhigh", "max"],
             # `pick`: le etichette nel selettore di /model, una lista perche' cambiano con Claude Code (le seconde fino
             # alla 2.1.280, le prime dalla 2.1.283, dove la voce «(1M context)» non c'e' piu')
             "models": [{"id": "claude-opus-5-5[1m]", "label": "Opus 5.5", "pick": ["Opus 5.5", "Opus (1M context)"]},
                        {"id": "claude-fable-5-1", "label": "Fable 5.1", "pick": ["Fable 5.1", "Fable"]},
                        {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "pick": ["Sonnet 5.5", "Sonnet"]},
                        {"id": "claude-haiku-5-5", "label": "Haiku 5.5", "pick": ["Haiku 5.5", "Haiku"]}]},
    # casella persistente (23/09/2026, docs/plans/2026-09-23-approvazioni-casella-registro-design.md): scadenza dei
    # messaggi in attesa. La coda degli ok e' stata tolta la sera stessa (decisione dell'utente, 22:39)
    "inbox": {"expires_h": 48},
    # 03/10: registro dei compiti. guard = la guardia della corsia chiusa (push, release, publish) nega quei comandi
    # fuori da un compito approvato: spenta di default, perche' vale per ogni sessione della macchina
    "tasks": {"guard": False, "notify": "master"},
    "hooks": {
        "local_time": {"enabled": True, "format": "%A %Y-%m-%d %H:%M", "prefix": "[local time]"},
        "restart_stop": {"enabled": True},
        "session_kernel": {"enabled": True},
        "ask_notify": {"enabled": True, "delay_s": 1.5, "synth_model": "haiku", "synth_max_chars": 88, "synth_timeout_s": 20},
    },
    "shell": {
        "wrappers": {"claude": "default"},
        "aliases": {},
        "passthrough_args": ["--version", "-v", "--help", "-h", "-p", "--print", "mcp", "update",
                             "doctor", "install", "agents", "attach", "logs", "stop", "plugin"],
        "restore_prompt": True,
        "disable_terminal_title": True,
    },
    "tmux": {"keybindings": {"tile": "a", "merge": "u", "move_arrows": True}, "socket": ""},
    # piu' macchine (piano docs/plans/2026-09-27-multi-pc.md, v1): gli host dichiarati dall'utente, ognuno con le tre
    # parti — dichiarata (kind, transport), misurata (measured, da `host doctor`), confermata (roles, limits, trust,
    # remote_root, confirmed_at). `local` non si dichiara: esiste sempre ed e' la regia.
    "hosts": {},
    # scheduler dei lavori pesanti e soglie dell'onboarding (1.3, 1.4, 3.3). Le formule dei limiti sono queste: si
    # mostrano all'onboarding e si ritarano con scheduler.log, non a intuito
    "scheduler": {
        "fresh_s": 180,              # una lettura dell'host piu' vecchia non vale per lo scheduler
        "poll_idle_s": 600,          # host senza niente di vivo: si rilegge ogni 10 minuti
        "poll_busy_s": 30,           # con lavori o sessioni remote vive: ogni 30 s
        "load_gate_pct": 80,         # cancello di carico: niente lavoro pesante nuovo sopra questa quota dei thread
        "roles": {"compute_cpu_multi": 1.0, "compute_ram_gb": 8},
        "limits": {"threads_per_heavy": 4, "reserve_gb": 2, "session_reserve_gb": 0.35, "heavy_footprint_gb": 3,
                   "session_peak_gb": 0.65, "sessions_cap": 6},
        "setup_s": 10,               # preparazione fissa di un invio (connessione, estrazione)
        "drift_pct": 30,             # scarto fra rel previsto e osservato che, per tre lavori di fila, chiede un doctor
        "doctor_max_age_d": 30,
        "bench_max_load_pct": 20,    # sopra questo carico il micro-benchmark si rimanda: misurerebbe il rumore
        "remote_root": {"posix": "~/claude-work", "windows": "%USERPROFILE%\\claude-work"},
        "client_paths": [],          # il perimetro clienti: mai su un host con trust.clients false
        "secret_globs": [".env*", "*.pem", "*.key", "id_*", ".npmrc", "credentials*"],
        "ssh_connect_timeout_s": 4,
        "min_free_gb": 2,
        # launch propone (e --host auto sceglie) un host con il ruolo sessions quando la regia e' carica: carico a
        # 1 minuto oltre load_per_core per core, o RAM disponibile sotto free_ram_gb (proposta win, ok del maintainer 01/10)
        "session_offload": {"load_per_core": 1.0, "free_ram_gb": 2},
    },
}

# Default che cambiano con la lingua (piano §4: nomi dei monitor, hook ora,
# cartella delle segnalazioni, alias di shell).
BY_LANG = {
    "it": {
        "hooks": {"local_time": {"format": "%A %d/%m/%Y %H:%M", "prefix": "[ora locale]"}},
        "report": {"subdir": "docs/segnalazioni"},
        "tile": {"monitor_names": {"native": "nativo", "left": "sinistra", "right": "destra",
                                   "above": "sopra", "below": "sotto", "external": "esterno",
                                   "center": "centro"}},
        "shell": {"aliases": {"lancia": "launch", "chiudi": "close", "riavvia": "restart",
                              "sessioni": "sessions", "parla-con": "talk", "segnala": "report",
                              "registro": "registry", "ripristina": "restore", "affianca": "tile",
                              "riaffianca": "tile", "unisci": "merge", "sposta": "move",
                              "attacca": "attach", "quote": "quota"}},
    },
    "en": {},
}

# Nomi dei file di stato sotto state_dir quando la chiave e' vuota.
STATE_FILES = {
    "tabs.color_registry": "colors",
    "tile.monitor_registry": "monitors.json",
    "tile.placeholder_file": "next-session",
    "tile.log": "tile.log",
    "registry.file": "sessions.json",
    "registry.good_file": "sessions-good.json",
    "restart.flag_file": "restart.json",
    "restart.log": "restart.log",
    "tune.file": "tuned.json",
    "bot.log": "bot.log",
    "night.queue_file": "night-queue.jsonl",
    "night.done_file": "night-done.jsonl",
    "night.log": "night.log",
}

# Percorsi legacy di questa postazione (D4): stessi formati, si condividono
# durante la prova in parallelo.
# Il registro delle sessioni (registry.file) NON si condivide: il ripristino legacy
# legge l'etichetta «aziendale», il plugin scrive i nomi degli account della
# configurazione. Ognuno tiene il suo finche' il cut-over non spegne il legacy.
LEGACY_STATE = {
    "tabs.color_registry": "~/.claude/colori-sessione",
    "tile.monitor_registry": "~/.claude/monitor-noti.json",
    "tile.placeholder_file": "~/.claude/prossima-sessione",
    "tile.log": "~/.local/state/affianca.log",
    "restart.flag_file": "~/.claude/riavvio-armato.json",
    "restart.log": "~/.claude/ultimo-riavvio.log",
}
LEGACY_SKILL = "~/.claude/skills/nuova-sessione"
LEGACY_REGISTRY = "~/.claude/sessioni-vive.json"   # letto da init per dedurre prefissi e mappa cartelle
LEGACY_LAUNCH = "~/.claude/skills/nuova-sessione/lancia.sh"   # letto da init per i flag di avvio (T74)

ACCOUNT_FIELDS = ("config_dir", "tmux_prefix", "shape", "shell_command", "label", "remote_control")
SHAPES = ("circle", "square")
FIRST_ACCOUNT_NAME = {"it": "personale", "en": "personal"}
SECOND_ACCOUNT_NAME = {"it": "professionale", "en": "work"}
DEFAULT_SECOND_PREFIX = "pro-"


# ------------------------------------------------------------------ utilita'
def home():
    return Path(os.environ.get("CM_HOME") or Path.home())


def detect_language(env_get=None):
    """(lingua, fonte) quando la config non la dice: `language` in ~/.claude/settings.json (italiano se comincia
    con «ital»), poi LANG (italiano se comincia con «it»), altrimenti inglese. La usa init per proporre il valore
    e, dal 14/09/2026 (S03), ogni comando quando il file non ha `language`: prima valeva sempre l'inglese."""
    env_get = env_get or os.environ.get
    if str(read_json("~/.claude/settings.json").get("language", "")).lower().startswith("ital"):
        return "it", "~/.claude/settings.json language"
    if str(env_get("LANG", "") or "").lower().startswith("it"):
        return "it", "LANG"
    return "en", "default"


def claude_bin():
    """Il binario di claude per chi lo lancia da python: CM_CLAUDE_BIN (prove), poi il PATH, poi ~/.local/bin/claude.
    Stessa regola di cm-launch.sh (T81): dal cron il PATH e' /usr/bin:/bin e il link di claude sta in ~/.local/bin.
    14/09/2026: senza, il recap delle 20:00 chiamava «claude» a vuoto e scriveva i riassunti del giorno vuoti (dal
    12/09: recap sempre vuoto sul polso); la coda notturna e la sintesi delle risposte avevano lo stesso difetto."""
    b = os.environ.get("CM_CLAUDE_BIN") or shutil.which("claude")
    if b:
        return b
    p = home() / ".local" / "bin" / "claude"
    return str(p) if os.access(p, os.X_OK) else "claude"


def expand(s):
    """~ → home (CM_HOME nei test). Solo stringhe che iniziano con ~."""
    if isinstance(s, str) and (s == "~" or s.startswith("~/")):
        return str(home()) + s[1:]
    return s


def contract(s):
    """Percorso assoluto sotto home → forma con ~ (per scrivere la config)."""
    h = str(home())
    if isinstance(s, str) and (s == h or s.startswith(h + "/")):
        return "~" + s[len(h):]
    return s


def config_path():
    p = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    return Path(p) if p else home() / ".config" / "cc-supervisor" / "config.json"


def deep_merge(base, over, path="", unknown=None):
    """Copia `over` sopra `base`. Chiavi ignote → lista `unknown` (avviso, non errore)."""
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        here = f"{path}.{k}" if path else k
        if k not in out:
            if unknown is not None and path not in ("accounts", "shell.wrappers", "shell.aliases", "tile.monitor_names",
                                                    "profiles", "session.env", "bot.links", "relay.colors", "hosts") \
                    and not path.startswith(("profiles.", "hosts.")):
                unknown.append(here)   # ignota: avvisata e scartata, mai propagata
                continue
            out[k] = v
            continue
        if isinstance(out[k], dict) and isinstance(v, dict) and k not in ("wrappers", "aliases", "profiles") \
                and here not in ("session.env", "hosts"):
            out[k] = deep_merge(out[k], v, here, unknown)
        else:
            out[k] = v
    return out


def defaults_for(lang):
    return deep_merge(DEFAULTS, BY_LANG.get(lang, {}))


def normalize_accounts(cfg):
    """Ogni account riceve i campi mancanti; forma alternata circle/square."""
    accs = cfg.get("accounts") or {}
    out = {}
    for i, (name, a) in enumerate(accs.items()):
        a = dict(a or {})
        a.setdefault("config_dir", "~/.claude" if i == 0 else f"~/.claude-{name}")
        a.setdefault("tmux_prefix", "" if i == 0 else f"{name[:3]}-")
        a.setdefault("shape", SHAPES[i % len(SHAPES)])
        a.setdefault("shell_command", "claude" if i == 0 else f"claude-{name}")
        a.setdefault("label", name)
        out[name] = a
    cfg["accounts"] = out
    if cfg.get("default_account") not in out and out:
        cfg["default_account"] = next(iter(out))
    return cfg


def apply_state_defaults(cfg):
    for key, fname in STATE_FILES.items():
        if not get_raw(cfg, key):
            set_key(cfg, key, cfg["state_dir"].rstrip("/") + "/" + fname)
    if not cfg["restore"].get("last"):
        cfg["restore"]["last"] = cfg["workspace"]["root_session_name"]
    return cfg


def get_raw(cfg, dotted):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def set_key(cfg, dotted, value):
    parts = dotted.split(".")
    cur = cfg
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def load(path=None, warn=True):
    cfg = _load_raw(path, warn)
    if "diary" in cfg and "recap" not in cfg:   # nome vecchio (fino alla 0.3.3)
        cfg["recap"] = cfg.pop("diary")
    return cfg


def _load_raw(path=None, warn=True):
    """Default ← file. Il file puo' essere assente (valgono i default)."""
    p = Path(path) if path else config_path()
    data = {}
    if p.is_file():
        data = json.loads(p.read_text())
    # S03 (14/09/2026): senza `language` nel file vale la lingua rilevata (come in init), non sempre l'inglese
    lang = data.get("language") or detect_language()[0]
    unknown = []
    cfg = deep_merge(defaults_for(lang), data, unknown=unknown)
    cfg["language"] = lang
    if "accounts" in data:
        # gli account del file sostituiscono quello di default, non si sommano
        cfg["accounts"] = data["accounts"]
        if data.get("default_account") is None:
            cfg["default_account"] = next(iter(data["accounts"]), "default")
    cfg = normalize_accounts(cfg)
    cfg = apply_state_defaults(cfg)
    if warn and unknown:
        print(f"cm-config: unknown keys ignored in {p}: {', '.join(unknown)}", file=sys.stderr)
    return cfg


def expand_all(v):
    if isinstance(v, dict):
        return {k: expand_all(x) for k, x in v.items()}
    if isinstance(v, list):
        return [expand_all(x) for x in v]
    return expand(v)


def account_for(cfg, path):
    """(account, dedotto) per una cartella: il prefisso piu' lungo di folder_map, altrimenti (default_account, False).
    Unica copia della regola (piano multi-PC 4.1): prima stava in cm-launch.sh, cm-cloud.sh, cm-night.py e cm-relay.py,
    con confronti diversi (testo nudo, realpath). Vale il confronto piu' largo: testo espanso o percorso reale."""
    raw = os.path.normpath(expand(str(path)))
    real = os.path.realpath(raw)
    best, best_len = "", -1
    for e in cfg.get("folder_map") or []:
        p = (e.get("path") or "").rstrip("/")
        if not p:
            continue
        p = os.path.normpath(expand(p))
        for mine, theirs in ((raw, p), (real, os.path.realpath(p))):
            if (mine == theirs or mine.startswith(theirs.rstrip("/") + "/")) and len(p) > best_len:
                best, best_len = e.get("account") or "", len(p)
                break
    if best:
        return best, True
    return cfg.get("default_account") or "", False


# ------------------------------------------------------------------ messaggi
_MSG = {}


def messages(lang):
    if lang not in _MSG:
        f = PLUGIN / "messages" / f"{lang}.json"
        if not f.is_file():
            f = PLUGIN / "messages" / "en.json"
        _MSG[lang] = json.loads(f.read_text(encoding="utf-8"))
    return _MSG[lang]


def msg(cfg, _key, **kw):
    text = messages(cfg.get("language", "en")).get(_key) or messages("en").get(_key) or _key
    try:
        return text.format(**kw)
    except (KeyError, IndexError):
        return text


# ------------------------------------------------------------------ --sh
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sh_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def sh_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        if all(isinstance(x, (str, int, float)) for x in v):
            return " ".join(str(expand(x)) for x in v)
        return "\n".join("\t".join(str(expand(x.get(k))) for k in sorted(x)) for x in v)
    return str(expand(v))


def flatten_sh(cfg, prefix="CM"):
    lines = []

    def walk(name, v):
        if isinstance(v, dict):
            lines.append(f"export {name}_KEYS={sh_quote(' '.join(v.keys()))}")
            if all(IDENT.match(k) for k in v):
                for k, x in v.items():
                    walk(f"{name}_{k.upper()}", x)
            else:
                lines.append(f"export {name}={sh_quote(chr(10).join(f'{k}{chr(9)}{sh_value(x)}' for k, x in v.items()))}")
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            # folder_map: path<TAB>account per riga, con ~ espansa
            rows = []
            for x in v:
                rows.append("\t".join(str(expand(x.get(k, ""))) for k in ("path", "account")))
            lines.append(f"export {name}={sh_quote(chr(10).join(rows))}")
        else:
            lines.append(f"export {name}={sh_quote(sh_value(v))}")

    for k, v in cfg.items():
        if k == "hosts":   # misure annidate: gli script bash non le leggono, le legge cm-hosts.py
            continue
        walk(f"{prefix}_{k.upper()}", v)
    # i pattern dei dialoghi contengono spazi: come lista sarebbero illeggibili, si esporta la regex unita
    lines.append(f"export CM_SESSION_DIALOG_REGEX={sh_quote('|'.join(cfg['session'].get('dialog_patterns', [])))}")
    lines.append(f"export CM_PLUGIN_DIR={sh_quote(str(PLUGIN))}")
    lines.append(f"export CM_CONFIG_FILE={sh_quote(str(config_path()))}")
    return "\n".join(lines) + "\n"


def messages_sh(cfg):
    out = []
    for k, v in messages(cfg.get("language", "en")).items():
        out.append(f"export MSG_{k.upper().replace('.', '_')}={sh_quote(v)}")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ macchina
class Machine:
    """Cio' che init e doctor guardano. La versione finta legge un JSON."""

    def __init__(self):
        fake = os.environ.get("CM_FAKE_MACHINE")
        self.fake = json.loads(Path(fake).read_text()) if fake else None

    def which(self, cmd):
        if self.fake is not None:
            return self.fake.get("paths", {}).get(cmd, f"/fake/bin/{cmd}") if cmd in self.fake.get("which", []) else None
        return shutil.which(cmd)

    def exists(self, path):
        path = expand(path)
        if self.fake is not None:
            return path in self.fake.get("exists", [])
        return os.path.exists(path)

    def env(self, name, default=""):
        if self.fake is not None:
            return self.fake.get("env", {}).get(name, default)
        return os.environ.get(name, default)

    def platform(self):
        if self.fake is not None:
            return self.fake.get("platform", "linux")
        return sys.platform

    def has_module(self, name):
        """Un modulo Python importabile (nome puntato, es. cryptography.hazmat.primitives.ciphers.aead).
        La macchina finta lo dichiara in "modules"."""
        if self.fake is not None:
            return name in self.fake.get("modules", [])
        try:
            __import__(name)
            return True
        except ImportError:
            return False

    def procs(self):
        """Processi claude vivi: pid, cmdline, environ, cwd, nome tmux (o "")."""
        if self.fake is not None:
            return self.fake.get("procs", [])
        out = []
        panes = {}
        try:
            r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{session_name} #{pane_pid}"],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                s, _, p = line.partition(" ")
                panes[p.strip()] = s
        except (OSError, subprocess.SubprocessError):
            pass
        for d in glob.glob("/proc/[0-9]*"):
            try:
                cmd = Path(d, "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
                if not re.search(r"(^|/)claude( |$)", cmd) or "shell-snapshots" in cmd:
                    continue
                env = {}
                for kv in Path(d, "environ").read_bytes().split(b"\0"):
                    k, _, v = kv.partition(b"=")
                    if k in (b"CLAUDE_CONFIG_DIR",):
                        env[k.decode()] = v.decode(errors="replace")
                cwd = os.readlink(Path(d, "cwd"))
                pid = int(Path(d).name)
                ppid = ""
                for line in Path(d, "status").read_text().splitlines():
                    if line.startswith("PPid:"):
                        ppid = line.split()[1]
                tm = panes.get(str(pid)) or panes.get(ppid) or ""
                out.append({"pid": pid, "cmdline": cmd, "environ": env, "cwd": cwd, "tmux": tm})
            except (OSError, ValueError):
                continue
        return out

    def version(self, cmd):
        if self.fake is not None:
            return self.fake.get("versions", {}).get(cmd, "")
        try:
            # tmux risponde a -V; con --version stampa l'usage
            r = subprocess.run([cmd, "-V" if cmd == "tmux" else "--version"], capture_output=True, text=True, timeout=10)
            return (r.stdout or r.stderr).strip().splitlines()[0][:40]
        except (OSError, subprocess.SubprocessError, IndexError):
            return ""


RELAY_CRYPTO_MODULE = "cryptography.hazmat.primitives.ciphers.aead"


def relay_deps_missing(m=None, crontab_cmd="crontab"):
    """Le due dipendenze del relay dell'app Wear OS (0.4.20), fino a oggi scoperte solo come errore:
    il modulo Python `cryptography` (AES-GCM, X25519) e `crontab` nel PATH (relay install). Torna
    le mancanti tra "crypto" e "crontab"; doctor le mostra, relay pair/install si fermano prima
    di chiedere o scrivere qualcosa. Nessuna installazione automatica."""
    m = m or Machine()
    missing = []
    if not m.has_module(RELAY_CRYPTO_MODULE):
        missing.append("crypto")
    if not m.which(crontab_cmd):
        missing.append("crontab")
    return missing


# ------------------------------------------------------------------ prerequisiti (13/10)
# init e doctor dicono cosa manca e come installarlo sul sistema in uso. Nato dalla prova d'installazione da zero
# del 13/10: un utente nuovo scopriva le mancanze solo come errore, e in WSL il `claude` di Windows (dal PATH di
# Windows) passava per buono. Nessuna installazione automatica: si stampa il comando, lo lancia la persona.
MIN_PYTHON = (3, 8)
MIN_CLAUDE = (2, 1, 263)
INSTALL = {   # prerequisito → gestore di pacchetti → comando
    "tmux": {"apt": "sudo apt install tmux", "dnf": "sudo dnf install tmux", "pacman": "sudo pacman -S tmux",
             "zypper": "sudo zypper install tmux", "brew": "brew install tmux"},
    "python3": {"apt": "sudo apt install python3", "dnf": "sudo dnf install python3", "pacman": "sudo pacman -S python",
                "zypper": "sudo zypper install python3", "brew": "brew install python"},
    "bash": {"apt": "sudo apt install bash", "dnf": "sudo dnf install bash", "pacman": "sudo pacman -S bash",
             "zypper": "sudo zypper install bash", "brew": "brew install bash"},
    "cryptography": {"apt": "sudo apt install python3-cryptography", "dnf": "sudo dnf install python3-cryptography",
                     "pacman": "sudo pacman -S python-cryptography", "zypper": "sudo zypper install python3-cryptography",
                     "brew": "python3 -m pip install --user cryptography"},
    "crontab": {"apt": "sudo apt install cron", "dnf": "sudo dnf install cronie", "pacman": "sudo pacman -S cronie",
                "zypper": "sudo zypper install cronie", "brew": "crontab ships with macOS"},
}
CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"
FIREBASE_INSTALL = "npm install -g firebase-tools && firebase login"
PKG_MANAGERS = (("apt", "apt-get"), ("dnf", "dnf"), ("pacman", "pacman"), ("zypper", "zypper"))


def system_kind(m):
    """(sistema, gestore di pacchetti): sistema fra macos, chromeos, wsl, linux; gestore fra apt, dnf, pacman,
    zypper, brew, "" se nessuno. ChromeOS = il contenitore Linux (Crostini), Debian: apt; WSL2 di solito Ubuntu."""
    if m.platform() == "darwin":
        return "macos", "brew"
    pm = next((k for k, b in PKG_MANAGERS if m.which(b)), "")
    if m.exists("/dev/.cros_milestone") or m.exists("/opt/google/cros-containers/bin/garcon"):
        return "chromeos", pm
    if m.env("WSL_DISTRO_NAME") or m.env("WSL_INTEROP"):
        return "wsl", pm
    return "linux", pm


def version_tuple(text):
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(x or 0) for x in found.groups()) if found else None


# in WSL2 l'installer lancia `npm uninstall -g @anthropic-ai/claude-code` e `npm` e' quello di Windows: senza le
# cartelle di Windows nel PATH non tocca il Claude Code di Windows (provato il 13/10 con un npm finto)
CLAUDE_INSTALL_WSL = "curl -fsSL https://claude.ai/install.sh | PATH=/usr/local/bin:/usr/bin:/bin bash"


def install_hint(cfg, what, pm, kind=""):
    if what == "claude":
        return CLAUDE_INSTALL_WSL if kind == "wsl" else CLAUDE_INSTALL
    if what == "firebase":
        return FIREBASE_INSTALL
    return INSTALL[what].get(pm) or msg(cfg, "prereq.no_pkg_manager", what=what)


def prereq_rows(m, cfg):
    """Le righe dei prerequisiti, nel formato di doctor: (livello, chiave, kwargs, rimedio gia' scritto o None).
    Il relay (cryptography, crontab, firebase): FAIL/WARN con relay.enabled, altrimenti INFO solo se manca."""
    kind, pm = system_kind(m)
    rows = [("INFO", "prereq.system", {"system": msg(cfg, f"prereq.sys_{kind}"), "pm": pm or "-"}, None)]

    def add(tool, ok, key, kw, what):
        if ok:
            rows.append(("PASS", key, kw, None))
        else:
            rows.append(("FAIL", key, kw, install_hint(cfg, what, pm)))

    for tool in ("tmux", "bash"):
        p = m.which(tool)
        add(tool, p, "doctor.tool_ok" if p else "doctor.tool_missing", {"tool": tool, "version": m.version(tool) if p else ""}, tool)
    p = m.which("python3")
    if not p:
        add("python3", False, "doctor.tool_missing", {"tool": "python3"}, "python3")
    else:
        v = m.version("python3")
        vt = version_tuple(v)
        if vt and vt[:2] < MIN_PYTHON:
            rows.append(("FAIL", "prereq.too_old", {"tool": "python3", "version": v, "min": "3.8"}, install_hint(cfg, "python3", pm)))
        else:
            rows.append(("PASS", "doctor.tool_ok", {"tool": "python3", "version": v}, None))
    p = m.which("claude")
    if not p:
        rows.append(("FAIL", "doctor.tool_missing", {"tool": "claude"}, install_hint(cfg, "claude", pm, kind)))
    elif kind == "wsl" and p.startswith("/mnt/"):
        # il `claude` di Windows, arrivato col PATH di Windows: le sessioni girerebbero fuori da WSL
        rows.append(("FAIL", "prereq.claude_windows", {"path": p}, CLAUDE_INSTALL_WSL))
    else:
        v = m.version("claude")
        vt = version_tuple(v)
        if vt and vt < MIN_CLAUDE:
            rows.append(("FAIL", "prereq.too_old", {"tool": "claude", "version": v, "min": "2.1.263"}, "claude update"))
        else:
            rows.append(("PASS", "doctor.tool_ok", {"tool": "claude", "version": v}, None))

    be = cfg["terminal"]["backend"]
    if be == "chromeos":
        g = cfg["terminal"]["garcon"]
        rows.append(("PASS", "doctor.backend_ok", {"backend": be, "bin": g}, None) if m.exists(g)
                    else ("FAIL", "doctor.backend_bin_missing", {"backend": be, "bin": g}, None))
    elif be in ("none", "auto"):
        rows.append(("INFO", "doctor.backend_none", {}, msg(cfg, f"prereq.terminal_{kind}")))
    else:
        b = BACKEND_BIN.get(be, be)
        rows.append(("PASS", "doctor.backend_ok", {"backend": be, "bin": b}, None) if m.which(b)
                    else ("FAIL", "doctor.backend_bin_missing", {"backend": be, "bin": b}, msg(cfg, f"prereq.terminal_{kind}")))

    # 13/10: col relay acceso ma senza relay.firebase_url c'e' solo la web app locale (`relay web`): niente cifratura,
    # niente crontab, niente Firebase — le dipendenze del bus restano INFO
    on = cfg["relay"]["enabled"] and bool(str(cfg["relay"].get("firebase_url") or "").strip())
    if cfg["relay"]["enabled"] and not on:
        rows.append(("INFO", "doctor.relay_local", {}, None))
    missing = relay_deps_missing(m)
    mod = RELAY_CRYPTO_MODULE.split(".")[0]
    if "crypto" in missing:
        rows.append(("FAIL" if on else "INFO", "doctor.crypto_missing" if on else "prereq.relay_crypto_missing", {"module": mod}, install_hint(cfg, "cryptography", pm)))
    elif on:
        rows.append(("PASS", "doctor.crypto_ok", {"module": mod}, None))
    if "crontab" in missing:
        rows.append(("FAIL" if on else "INFO", "doctor.crontab_missing" if on else "prereq.relay_crontab_missing", {}, install_hint(cfg, "crontab", pm)))
    elif on:
        rows.append(("PASS", "doctor.crontab_ok", {"path": m.which("crontab")}, None))
    if not m.which("firebase"):
        # serve a `relay setup` (il progetto Firebase); un relay gia' configurato ne fa a meno: WARN, non FAIL
        rows.append(("WARN" if on else "INFO", "prereq.firebase_missing", {}, install_hint(cfg, "firebase", pm)))
    return rows


def print_rows(cfg, rows):
    """Stampa le righe nel formato di doctor; torna (fail, warn)."""
    fails = warns = 0
    for level, key, kw, fix in rows:
        detail = msg(cfg, key, **kw) if key.startswith(("doctor.", "prereq.")) else f"{key} {kw.get('path', '')}"
        print(msg(cfg, f"doctor.{level.lower()}", name=key.split(".")[-1], detail=detail))
        if fix:
            print(msg(cfg, "doctor.fix", fix=msg(cfg, fix) if fix.startswith("doctor.") else fix))
        fails += level == "FAIL"
        warns += level == "WARN"
    return fails, warns


def read_json(path):
    try:
        return json.loads(Path(expand(path)).read_text())
    except (OSError, ValueError):
        return {}


def relay_firebase_app(cfg):
    """I dati dell'app Firebase per il QR di `relay pair` (contratto 1.15): (dati, origine) oppure (None, motivo).
    Prima `relay.firebase_app` {api_key, project_id, app_id} se completo; altrimenti `relay.google_services`, il
    google-services.json scaricato dalla console: project_info.project_id e, nel client Android dell'app
    (`relay.app_package` se il file ne ha piu' d'uno, altrimenti l'unico), client_info.mobilesdk_app_id e
    api_key[0].current_key. Il motivo e' la chiave del messaggio: relay.app_unset (nessuna delle due),
    relay.app_file_missing, relay.app_file_many (piu' client e nessun relay.app_package), relay.app_file_bad."""
    r = cfg.get("relay") or {}
    app = r.get("firebase_app") if isinstance(r.get("firebase_app"), dict) else {}
    got = {k: str(app.get(k) or "").strip() for k in ("api_key", "project_id", "app_id")}
    if all(got.values()):
        return got, "config"
    gs = str(r.get("google_services") or "").strip()
    if not gs:
        return None, "relay.app_unset"
    path = Path(expand(gs))
    if not path.is_file():
        return None, "relay.app_file_missing"
    want = str(r.get("app_package") or "").strip()
    try:
        doc = json.loads(path.read_text())
        clients = [(c, str(((c.get("client_info") or {}).get("android_client_info") or {}).get("package_name") or ""))
                   for c in doc.get("client") or [] if isinstance(c, dict)]
        clients = [(c, pkg) for c, pkg in clients if pkg and (not want or pkg == want)]
        if len(clients) > 1:
            return None, "relay.app_file_many"
        client = clients[0][0]
        got = {"api_key": str(client["api_key"][0]["current_key"]), "project_id": str(doc["project_info"]["project_id"]),
               "app_id": str(client["client_info"]["mobilesdk_app_id"])}
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None, "relay.app_file_bad"
    return (got, str(path)) if all(got.values()) else (None, "relay.app_file_bad")


def has_local_time_hook(settings):
    for entry in (settings.get("hooks") or {}).get("UserPromptSubmit") or []:
        for h in entry.get("hooks") or []:
            c = h.get("command") or ""
            if "ora locale" in c or "local time" in c or (c.startswith("date ") and "sed" in c):
                return True
    return False


# ------------------------------------------------------------------ init
def detect(m):
    """Legge la macchina e produce (config, sources). Ogni valore ha una fonte."""
    src = {}
    h = home()

    # lingua: settings dell'account principale, poi LANG
    settings_main = read_json("~/.claude/settings.json")
    lang, src["language"] = detect_language(m.env)
    cfg = defaults_for(lang)
    cfg["language"] = lang

    # backend terminale
    t = cfg["terminal"]
    if m.exists(t["garcon"]):
        t["backend"], src["terminal.backend"] = "chromeos", f"{t['garcon']} present"
    elif m.env("TERM_PROGRAM") == "iTerm.app":
        t["backend"], src["terminal.backend"] = "iterm2", "TERM_PROGRAM=iTerm.app"
    elif m.env("TERM_PROGRAM") == "Apple_Terminal":
        t["backend"], src["terminal.backend"] = "macos-terminal", "TERM_PROGRAM=Apple_Terminal"
    elif m.env("KITTY_WINDOW_ID") or m.which("kitty"):
        t["backend"], src["terminal.backend"] = "kitty", "kitty present"
    elif m.env("WT_SESSION") or m.which("wt.exe"):
        t["backend"], src["terminal.backend"] = "wt", "Windows Terminal present"
    elif m.which("gnome-terminal"):
        t["backend"], src["terminal.backend"] = "gnome", "gnome-terminal in PATH"
    else:
        t["backend"], src["terminal.backend"] = "none", "no known terminal found"

    # account: ~/.claude + ~/.claude-* con credenziali o settings
    first = FIRST_ACCOUNT_NAME[lang]
    accounts = {first: {"config_dir": "~/.claude", "tmux_prefix": "", "shape": "circle",
                        "shell_command": "claude", "label": first}}
    src[f"accounts.{first}.config_dir"] = "~/.claude"
    extras = sorted(d for d in glob.glob(str(h / ".claude-*"))
                    if os.path.isdir(d) and (os.path.exists(f"{d}/.credentials.json") or os.path.exists(f"{d}/settings.json")))
    rc_text = ""
    for rc in (".bashrc", ".zshrc", ".bash_profile"):
        try:
            rc_text += (h / rc).read_text() + "\n"
        except OSError:
            pass
    for i, d in enumerate(extras):
        name = SECOND_ACCOUNT_NAME[lang] if i == 0 else f"account{i + 2}"
        suffix = os.path.basename(d)[len(".claude-"):]
        cmd = f"claude-{suffix}"
        fn = re.search(r"^\s*([A-Za-z0-9_-]+)\s*\(\)\s*\{[^}]*CLAUDE_CONFIG_DIR=\"?\$HOME/\.claude-" + re.escape(suffix), rc_text, re.M | re.S)
        if fn:
            cmd, src[f"accounts.{name}.shell_command"] = fn.group(1), "function in shell rc"
        else:
            src[f"accounts.{name}.shell_command"] = "claude-<suffix> of the config dir"
        accounts[name] = {"config_dir": contract(d), "tmux_prefix": DEFAULT_SECOND_PREFIX,
                          "shape": SHAPES[(i + 1) % len(SHAPES)], "shell_command": cmd, "label": name}
        src[f"accounts.{name}.config_dir"] = f"{contract(d)} has credentials/settings"
        src[f"accounts.{name}.tmux_prefix"] = "default"
    cfg["accounts"] = accounts
    cfg["default_account"] = first
    src["default_account"] = "first account"

    # processi e registro legacy: prefissi tmux, radice, mappa cartelle
    by_dir = {expand(a["config_dir"]): n for n, a in accounts.items()}
    procs = m.procs()
    obs = []   # (account, cwd, tmux)
    for p in procs:
        conf = p.get("environ", {}).get("CLAUDE_CONFIG_DIR") or str(h / ".claude")
        acc = by_dir.get(conf)
        if acc:
            obs.append((acc, p.get("cwd", ""), p.get("tmux", "")))
    legacy = read_json(LEGACY_REGISTRY)
    legacy_names = {"personale": first, "aziendale": SECOND_ACCOUNT_NAME[lang]}
    for s in legacy.get("sessioni", []):
        acc = legacy_names.get(s.get("account"), s.get("account"))
        if acc in accounts:
            obs.append((acc, s.get("cartella", ""), s.get("nome", "")))

    for name in accounts:
        names = [tm for acc, _, tm in obs if acc == name and tm]
        others = [tm for acc, _, tm in obs if acc != name and tm]
        if names:
            prefixes = {tm.split("-", 1)[0] + "-" if "-" in tm else "" for tm in names}
            if len(prefixes) == 1:
                pfx = prefixes.pop()
                if pfx and all(not o.startswith(pfx) for o in others):
                    accounts[name]["tmux_prefix"] = pfx
                    src[f"accounts.{name}.tmux_prefix"] = "shared by every live/registered session of the account"
                elif not pfx:
                    accounts[name]["tmux_prefix"] = ""
                    src[f"accounts.{name}.tmux_prefix"] = "sessions of the account carry no prefix"
    if first not in {n for n, _ in [(k, 0) for k in src] } and f"accounts.{first}.tmux_prefix" not in src:
        src[f"accounts.{first}.tmux_prefix"] = "default"

    # flag di avvio (T74, 09/09/2026): le sessioni vive dicono come le si lancia qui. Se la
    # MAGGIORANZA dei processi claude porta --dangerously-skip-permissions, senza quel flag
    # una sessione nuova nasce in modalita' dontAsk e ogni Bash le viene negato. Maggioranza
    # e non totalita': una sessione lanciata senza flag (per prova) non deve nascondere le altre.
    # 06/10/2026 (revisione della directory dei plugin): il flag NON si copia piu' da solo nella config — un plugin
    # non accende il bypass dei permessi. Lo si segnala soltanto; l'utente lo aggiunge a session.claude_args se vuole.
    flag = "--dangerously-skip-permissions"
    live = [p.get("cmdline", "") for p in procs if p.get("cmdline", "").split()[:1] and "claude" in p["cmdline"].split()[0]]
    if live and sum(flag in c for c in live) * 2 > len(live):
        src["session.claude_args"] = (f"default — {sum(flag in c for c in live)} of {len(live)} live claude sessions carry {flag}: "
                                      f"add it to session.claude_args yourself to launch the same way")
    elif not live and Path(expand(LEGACY_LAUNCH)).is_file() and flag in Path(expand(LEGACY_LAUNCH)).read_text():
        src["session.claude_args"] = f"default — the legacy launch script uses {flag}: add it to session.claude_args yourself"
    else:
        src["session.claude_args"] = "default"

    # radice
    cwds = [c for _, c, _ in obs if c]
    root = None
    if cwds:
        common = os.path.commonpath(cwds) if len(cwds) > 1 else os.path.dirname(cwds[0])
        if common and common != "/" and common != str(h):
            root, src["workspace.root"] = common, "common ancestor of session folders"
    if root is None:
        for cand in ("~/Desktop/workspaces", "~/workspaces", "~/Workspace", "~/Projects", "~/projects",
                     "~/code", "~/dev", "~/src"):
            if os.path.isdir(expand(cand)):
                root, src["workspace.root"] = expand(cand), f"{cand} exists"
                break
    if root is None:
        root, src["workspace.root"] = str(h), "default (~)"
    cfg["workspace"]["root"] = contract(root)

    # mappa cartelle: per account, il primo componente sotto la radice
    comp = {}
    for acc, c, _ in obs:
        if c and c.startswith(root + "/"):
            comp.setdefault(acc, set()).add(c[len(root) + 1:].split("/")[0])
    fmap = []
    for acc, comps in comp.items():
        for cpt in sorted(comps):
            if all(cpt not in comp.get(o, set()) for o in comp if o != acc) and cpt:
                fmap.append({"path": contract(f"{root}/{cpt}"), "account": acc})
    cfg["folder_map"] = fmap
    src["folder_map"] = "folders of live/registered sessions, one account each" if fmap else "none found"

    # cartelle progetto: genitori delle cartelle di sessione, relativi alla radice
    pdirs = set()
    for _, c, _ in obs:
        if c and c.startswith(root + "/"):
            rel = os.path.dirname(c[len(root) + 1:])
            if rel:
                pdirs.add(rel)
    cfg["workspace"]["project_dirs"] = sorted(pdirs) or ["."]
    src["workspace.project_dirs"] = "parents of session folders" if pdirs else "default"

    # escluse: default + voci della radice che iniciano con _ o .
    ex = list(cfg["workspace"]["excluded_dirs"])
    try:
        for e in sorted(os.listdir(root)):
            if (e.startswith("_") or e.startswith(".")) and os.path.isdir(os.path.join(root, e)) and e not in ex:
                ex.append(e)
    except OSError:
        pass
    cfg["workspace"]["excluded_dirs"] = ex
    src["workspace.excluded_dirs"] = "defaults + root entries starting with _ or ."

    # stato: legacy condiviso (D4) se la skill legacy c'e'
    if os.path.isdir(expand(LEGACY_SKILL)):
        cfg["state_dir"] = "~/.claude"
        for k, v in LEGACY_STATE.items():
            set_key(cfg, k, v)
            src[k] = "legacy file (shared during the parallel trial)"
        src["state_dir"] = f"{LEGACY_SKILL} present"
    else:
        src["state_dir"] = "default"

    # chrome-bridge
    cb = ""
    for j in (read_json("~/.claude.json"), settings_main):
        args = ((j.get("mcpServers") or {}).get("chrome-bridge") or {}).get("args") or []
        for a in args:
            if a.endswith("index.js"):
                cand = a[: -len("index.js")] + "cli.js"
                if os.path.exists(cand):
                    cb = contract(cand)
                    break
        if cb:
            break
    cfg["tile"]["chrome_bridge_cli"] = cb
    src["tile.chrome_bridge_cli"] = "mcpServers.chrome-bridge in ~/.claude.json / settings.json" if cb else "not found"

    # shell
    cfg["shell"]["wrappers"] = {a["shell_command"]: n for n, a in accounts.items()}
    src["shell.wrappers"] = "one per account"
    src["shell.aliases"] = f"language {lang}"

    # tmux keybindings dal .tmux.conf legacy
    try:
        tc = (h / ".tmux.conf").read_text()
        kb = cfg["tmux"]["keybindings"]
        ma = re.search(r"^bind\s+(\S+)\s+run-shell.*affianca", tc, re.M)
        mu = re.search(r"^bind\s+(\S+)\s+run-shell.*unisci", tc, re.M)
        if ma:
            kb["tile"] = ma.group(1)
        if mu:
            kb["merge"] = mu.group(1)
        kb["move_arrows"] = "sposta" in tc
        src["tmux.keybindings"] = "~/.tmux.conf"
    except OSError:
        src["tmux.keybindings"] = "default"

    cfg = normalize_accounts(cfg)
    cfg = apply_state_defaults(cfg)
    return cfg, src


def print_detected(cfg, src):
    print(msg(cfg, "init.header"))
    flat = {}

    def walk(prefix, v):
        if isinstance(v, dict) and prefix not in ("shell.wrappers", "shell.aliases", "tile.monitor_names", "tabs.colors"):
            for k, x in v.items():
                walk(f"{prefix}.{k}" if prefix else k, x)
        else:
            flat[prefix] = v
    walk("", cfg)
    for k, v in flat.items():
        s = src.get(k)
        if s is None:
            # fonte ereditata dal ramo (es. accounts.x.*) o default
            s = next((src[p] for p in src if k.startswith(p + ".")), "default")
        val = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        print(msg(cfg, "init.detected", key=k, value=val[:40], source=s))


SHIM = """#!/usr/bin/env bash
# supervisor shim — installed by `supervisor init --shim`.
# The plugin cache moves to a new directory at every version (and a live
# session keeps running from the old one), so this file never hardcodes the
# root: it resolves it at every call, in this order:
#   1. CC_SUPERVISOR_ROOT            (explicit, also used by the tests)
#   2. plugin_root in the config     (a development checkout)
#   3. installed_plugins.json        (CLAUDE_CONFIG_DIR first, then ~/.claude, then ~/.claude-*)
root="${CC_SUPERVISOR_ROOT:-${TEAM_SUPERVISOR_ROOT:-${CLAUDE_MASTER_ROOT:-}}}"
if [ -z "$root" ]; then
  root=$(python3 - "$HOME" "${CLAUDE_CONFIG_DIR:-}" "${CC_SUPERVISOR_CONFIG:-${TEAM_SUPERVISOR_CONFIG:-${CLAUDE_MASTER_CONFIG:-}}}" <<'PY' 2>/dev/null
import glob, json, os, sys
home, confdir, cfgfile = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = cfgfile or os.path.join(home, ".config", "cc-supervisor", "config.json")
try:
    pr = json.load(open(cfg)).get("plugin_root", "")
    if pr:
        pr = os.path.expanduser(pr)
        if os.path.isdir(pr):
            print(pr); sys.exit(0)
except Exception:
    pass
dirs = ([confdir] if confdir else []) + [os.path.join(home, ".claude")] + sorted(glob.glob(os.path.join(home, ".claude-*")))
for d in dirs:
    try:
        plugins = json.load(open(os.path.join(d, "plugins", "installed_plugins.json"))).get("plugins", {})
    except Exception:
        continue
    for key, entries in plugins.items():
        if key.startswith("supervisor@"):
            for e in (entries if isinstance(entries, list) else [entries]):
                p = e.get("installPath", "")
                if p and os.path.isdir(p):
                    print(p); sys.exit(0)
sys.exit(1)
PY
  )
fi
if [ -z "$root" ] || [ ! -x "$root/scripts/supervisor" ]; then
  echo "supervisor: plugin root not found. Set CC_SUPERVISOR_ROOT, or plugin_root in the configuration, or install the plugin (claude plugin install supervisor@...)" >&2
  exit 127
fi
exec "$root/scripts/supervisor" "$@"
"""


def write_shim(cfg):
    p = home() / ".local" / "bin" / "supervisor"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(SHIM)
    p.chmod(0o755)
    print(msg(cfg, "init.shim_written", path=p))


SHELL_STUB = """# supervisor — generato da `supervisor init --shell`. Non modificare: si rigenera.
# Carica l'integrazione shell del plugin (wrapper, alias, segnaposto, ripristino) dalla
# radice corrente del plugin, che cambia a ogni versione: la risolve lo shim.
_cm_shim="$HOME/.local/bin/supervisor"
if [ -x "$_cm_shim" ]; then
  _cm_r="$("$_cm_shim" root 2>/dev/null)"
  [ -n "$_cm_r" ] && [ -f "$_cm_r/shell/supervisor.sh" ] && . "$_cm_r/shell/supervisor.sh"
  unset _cm_r
fi
unset _cm_shim
"""


def write_shell(cfg):
    p = home() / ".config" / "cc-supervisor" / "shell.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(SHELL_STUB)
    print(msg(cfg, "init.shell_written", path=p))


def cron_setup(cfg, install):
    """`init --cron`: la riga di riconciliazione del registro (registry.cron_minutes)."""
    minutes = int(cfg["registry"]["cron_minutes"] or 0)
    if minutes <= 0:
        print(msg(cfg, "init.cron_off"))
        return 0
    shim = home() / ".local" / "bin" / "supervisor"
    line = f"*/{minutes} * * * * {shim} registry >/dev/null 2>&1"
    if not install:
        print(msg(cfg, "init.cron_line", min=minutes, line=line))
        return 0
    crontab = os.environ.get("CM_CRONTAB_CMD", "crontab")
    current = subprocess.run([crontab, "-l"], capture_output=True, text=True).stdout
    if "supervisor registry" in current or f"{shim} registry" in current:
        print(msg(cfg, "init.cron_present"))
        return 0
    new = current.rstrip("\n") + ("\n" if current.strip() else "") + "# supervisor: riconciliazione del registro delle sessioni\n" + line + "\n"
    subprocess.run([crontab, "-"], input=new, text=True, check=True)
    print(msg(cfg, "init.cron_installed", line=line))
    return 0


TMUX_MARK = "# supervisor: disposizione delle sessioni (generato da `supervisor init --tmux`)"


def tmux_block(cfg):
    """Il blocco per ~/.tmux.conf da tmux.keybindings: tile/merge su due tasti, frecce = move.

    Le frecce sono legate solo quando la finestra ha un pannello solo (una sessione, un
    pannello): con piu' pannelli tornano a select-pane senza disfare niente.
    run-shell -b non blocca il server tmux mentre il giro e' in corso.
    """
    kb = cfg["tmux"]["keybindings"]
    shim = "$HOME/.local/bin/supervisor"
    log = str(cfg["tile"]["log"] or "").replace("~", "$HOME", 1) or "/dev/null"
    lines = [TMUX_MARK]
    if kb.get("tile"):
        lines.append(f'bind {kb["tile"]} run-shell -b "{shim} tile  >> {log} 2>&1"')
    if kb.get("merge"):
        lines.append(f'bind {kb["merge"]} run-shell -b "{shim} merge >> {log} 2>&1"')
    if kb.get("move_arrows"):
        for key, direction, pane in (("Left", "left", "L"), ("Right", "right", "R"), ("Up", "up", "U"), ("Down", "down", "D")):
            lines.append(f"bind -r {key:<5} if -F '#{{==:#{{window_panes}},1}}' "
                         f"\"run-shell -b '{shim} move {direction} >> {log} 2>&1'\" 'select-pane -{pane}'")
    lines.append("# supervisor: fine")
    return "\n".join(lines) + "\n"


def tmux_setup(cfg, install):
    """`init --tmux`: stampa il blocco; con --yes lo accoda a ~/.tmux.conf (una volta sola)."""
    block = tmux_block(cfg)
    conf = home() / ".tmux.conf"
    if not install:
        print(msg(cfg, "init.tmux_block", path=conf, block=block.rstrip("\n")))
        return 0
    current = conf.read_text() if conf.is_file() else ""
    if TMUX_MARK in current:
        print(msg(cfg, "init.tmux_present", path=conf))
        return 0
    conf.write_text(current.rstrip("\n") + ("\n\n" if current.strip() else "") + block)
    print(msg(cfg, "init.tmux_installed", path=conf))
    return 0


def cmd_init(args):
    m = Machine()
    cfg, src = detect(m)
    dry = "--dry-run" in args
    yes = "--yes" in args
    force = "--force" in args
    target = config_path()
    # --shim e' un'azione a se', idempotente: da solo installa lo shim e basta;
    # con --yes installa lo shim E scrive la config.
    if "--shim" in args:
        write_shim(cfg)
    if "--shell" in args:
        write_shell(cfg)
    live = load(config_path(), warn=False) if config_path().is_file() else cfg
    if "--cron" in args:
        cron_setup(live, yes)
    if "--tmux" in args:
        tmux_setup(live, yes)
    if ("--cron" in args or "--tmux" in args) and not dry:
        return 0
    if ("--shim" in args or "--shell" in args) and not yes and not dry:
        return 0
    if "--json" in args:
        print(json.dumps({"config": cfg, "sources": src, "path": str(target)}, ensure_ascii=False, indent=2))
        if not (yes and not dry):
            return 0
    else:
        print_detected(cfg, src)
        print(msg(cfg, "prereq.header"))
        fails, warns = print_rows(cfg, prereq_rows(m, cfg))
        print(msg(cfg, "doctor.summary", fails=fails, warns=warns))
    if dry or not yes:
        if not yes:
            print(msg(cfg, "init.dry_run", path=target))
        return 0
    if target.exists() and not force:
        print(msg(cfg, "init.exists", path=target))
        return 2
    if target.exists():
        shutil.copy(target, str(target) + ".bak")
    target.parent.mkdir(parents=True, exist_ok=True)
    # si scrive senza i default espansi dello stato: le chiavi vuote restano
    # vuote cosi' un cambio di state_dir le segue
    to_write = json.loads(json.dumps(cfg))
    target.write_text(json.dumps(to_write, ensure_ascii=False, indent=2) + "\n")
    print(msg(cfg, "init.written", path=target))
    return 0


# ------------------------------------------------------------------ doctor
BACKEND_BIN = {"chromeos": None, "gnome": "gnome-terminal", "kitty": "kitty", "iterm2": "osascript",
               "macos-terminal": "osascript", "wt": "wt.exe", "none": None, "auto": None}


def plugin_row(name, a, checkout_ver):
    """Una riga di doctor per account: il plugin supervisor risulta installato? che versione ha la
    cache rispetto al checkout? errorDetails/noteDetails (Claude Code >= 2.1.268)?"""
    fake = os.environ.get("CM_FAKE_PLUGIN_LIST")
    try:
        if fake:
            data = json.loads(Path(fake).read_text())
        else:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
            conf = expand(a.get("config_dir", ""))
            if os.path.realpath(conf) != os.path.realpath(expand("~/.claude")):   # T68
                env["CLAUDE_CONFIG_DIR"] = conf
            p = subprocess.run(["claude", "plugin", "list", "--json"], capture_output=True, text=True, timeout=30, env=env)
            data = json.loads(p.stdout) if p.stdout.strip().startswith("[") else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        data = None
    if not isinstance(data, list):
        return ("INFO", "doctor.plugin_unknown", {"account": name}, None)
    row = next((r for r in data if str(r.get("id", "")).startswith("supervisor@")), None)
    if row is None:
        return ("WARN", "doctor.plugin_missing", {"account": name}, "doctor.fix_plugin_missing")
    ver = str(row.get("version", ""))
    if row.get("errorDetails"):
        return ("WARN", "doctor.plugin_error", {"account": name, "error": str(row["errorDetails"])[:200]}, None)
    if not row.get("enabled", True):
        return ("WARN", "doctor.plugin_disabled", {"account": name}, "doctor.fix_plugin_disabled")
    if checkout_ver and ver != checkout_ver:
        return ("WARN", "doctor.plugin_stale", {"account": name, "cache": ver, "checkout": checkout_ver}, "doctor.fix_plugin_stale")
    note = str(row.get("noteDetails") or "")[:120]
    return ("PASS", "doctor.plugin_ok", {"account": name, "version": ver, "note": f" · {note}" if note else ""}, None)


def cmd_doctor():
    m = Machine()
    p = config_path()
    rows = []   # (level, key, kwargs, fixkey)
    try:
        cfg = load(p, warn=False)
        if p.is_file():
            rows.append(("PASS", "config", {"path": p}, None))
        else:
            rows.append(("WARN", "doctor.no_config", {"path": p}, "doctor.fix_no_config"))
    except (ValueError, OSError) as e:
        cfg = load(None, warn=False) if False else normalize_accounts(apply_state_defaults(defaults_for("en")))
        rows.append(("FAIL", "doctor.config_bad", {"error": e}, None))

    # prerequisiti: strumenti, terminale, dipendenze del relay, con il comando per installarli (13/10)
    rows += prereq_rows(m, cfg)

    if cfg["relay"]["enabled"] and str(cfg["relay"].get("firebase_url") or "").strip():
        # 1.15: i dati dell'app Firebase nel QR di `relay pair` — senza, il QR non compare e resta il codice a 6 cifre
        app, why = relay_firebase_app(cfg)
        rows.append(("PASS", "doctor.firebase_app_ok", {"project": app["project_id"], "source": why}, None) if app
                    else ("WARN", "doctor.firebase_app_missing", {"why": msg(cfg, why, path=expand(cfg["relay"].get("google_services") or ""), package=cfg["relay"].get("app_package") or "")}, "doctor.fix_firebase_app"))

    # 09/10: un host confermato senza il sondatore nel crontab → le sue sessioni non compaiono in sessions ne' nell'app
    if any(n != "local" and h.get("confirmed_at") for n, h in (cfg.get("hosts") or {}).items()):
        try:
            cron = subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout
        except OSError:
            cron = ""
        rows.append(("PASS", "doctor.hosts_poller_ok", {}, None) if "supervisor hosts poll" in cron
                    else ("WARN", "doctor.hosts_poller_missing", {}, "doctor.fix_hosts_poller"))

    for name, a in cfg["accounts"].items():
        d = expand(a["config_dir"])
        if os.path.isdir(d):
            rows.append(("PASS", "doctor.account_ok", {"account": name, "path": a["config_dir"]}, None))
            st = read_json(os.path.join(d, "settings.json"))
            if st.get("crossSessionInbound") != "accept":
                rows.append(("WARN", "doctor.inbound_held", {"account": name}, "doctor.fix_inbound"))
            if cfg["hooks"]["local_time"]["enabled"] and has_local_time_hook(st):
                rows.append(("WARN", "doctor.hook_dup", {"account": name, "path": a["config_dir"] + "/settings.json"}, "doctor.fix_hook_dup"))
        else:
            rows.append(("FAIL", "doctor.account_dir_missing", {"account": name, "path": a["config_dir"]}, None))

    cb = cfg["tile"]["chrome_bridge_cli"]
    if not cb:
        rows.append(("INFO", "doctor.cb_unset", {}, None))
    elif os.path.isfile(expand(cb)):
        rows.append(("PASS", "doctor.cb_ok", {"path": cb}, None))
    else:
        rows.append(("WARN", "doctor.cb_missing", {"path": cb}, "doctor.fix_cb"))

    # plugin nella cache di ogni account: `claude plugin list --json` (Claude Code 2.1.268: righe con
    # errorDetails/noteDetails). Nei test CM_FAKE_PLUGIN_LIST (file JSON); con la macchina finta si salta.
    try:
        checkout_ver = json.loads((HERE.parent / ".claude-plugin" / "plugin.json").read_text()).get("version", "")
    except (OSError, ValueError):
        checkout_ver = ""
    if m.fake is None or os.environ.get("CM_FAKE_PLUGIN_LIST"):
        for name, a in cfg["accounts"].items():
            rows.append(plugin_row(name, a, checkout_ver))
    shim = home() / ".local" / "bin" / "supervisor"
    if shim.is_file():
        rows.append(("PASS", "doctor.shim_ok", {"path": contract(str(shim))}, None))
        # S01 (14/09/2026): lo shim c'e' ma la sua cartella non e' nel PATH di chi lancia doctor (il PATH arriva
        # intatto: ne' lo shim ne' il dispatcher lo toccano), e `supervisor` per nome non si trova
        in_path = {os.path.realpath(os.path.expanduser(x)) for x in m.env("PATH").split(os.pathsep) if x}
        if os.path.realpath(str(shim.parent)) not in in_path:
            rows.append(("WARN", "doctor.shim_not_in_path", {"dir": contract(str(shim.parent))}, "doctor.fix_shim_path"))
        else:
            # 08/10 (rinomino in «supervisor»): il pacchetto npm `supervisor` (node-supervisor) installa un comando con
            # lo stesso nome; se viene prima nel PATH, `supervisor` per nome non e' il nostro
            first = m.which("supervisor")
            if first and os.path.realpath(first) != os.path.realpath(str(shim)):
                rows.append(("WARN", "doctor.shim_shadowed", {"other": contract(first), "path": contract(str(shim))}, "doctor.fix_shim_shadowed"))
    else:
        rows.append(("WARN", "doctor.shim_missing", {"path": contract(str(shim))}, "doctor.fix_shim"))

    sd = expand(cfg["state_dir"])
    try:
        os.makedirs(sd, exist_ok=True)
        ok = os.access(sd, os.W_OK)
    except OSError:
        ok = False
    rows.append(("PASS" if ok else "FAIL", "doctor.state_ok" if ok else "doctor.state_bad", {"path": cfg["state_dir"]}, None))

    if os.path.isdir(expand(LEGACY_SKILL)):
        rows.append(("INFO", "doctor.legacy", {"path": LEGACY_SKILL}, None))

    print(msg(cfg, "doctor.header"))
    fails, warns = print_rows(cfg, rows)
    print(msg(cfg, "doctor.summary", fails=fails, warns=warns))
    return 1 if fails else 0


# ------------------------------------------------------------------ main
def main(argv):
    if not argv:
        print(__doc__)
        return 2
    a = argv[0]
    if a == "--dump-defaults":
        cfg = normalize_accounts(apply_state_defaults(json.loads(json.dumps(DEFAULTS))))
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0
    if a == "--path":
        print(config_path())
        return 0
    if a == "init":
        return cmd_init(argv[1:])
    if a == "doctor":
        return cmd_doctor()
    cfg = load()
    if a == "--sh":
        sys.stdout.write(flatten_sh(cfg))
        if "--messages" in argv:
            sys.stdout.write(messages_sh(cfg))
        return 0
    if a == "--messages":
        sys.stdout.write(messages_sh(cfg))
        return 0
    if a == "--msg":
        kw = dict(x.split("=", 1) for x in argv[2:] if "=" in x)
        print(msg(cfg, argv[1], **kw))
        return 0
    if a == "--account-for":
        # l'account DEDOTTO dalla cartella, vuoto se nessuna voce di folder_map la copre (chi chiama sceglie il default)
        acc, deduced = account_for(cfg, argv[1] if len(argv) > 1 else os.getcwd())
        print(acc if deduced else "")
        return 0
    if a == "--get":
        try:
            v = expand_all(get_raw(cfg, argv[1]))
        except (KeyError, IndexError):
            print(f"cm-config: unknown key {argv[1:2]}", file=sys.stderr)
            return 1
        if isinstance(v, (dict, list)):
            print(json.dumps(v, ensure_ascii=False))
        elif isinstance(v, bool):
            print("true" if v else "false")
        else:
            print(v)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
