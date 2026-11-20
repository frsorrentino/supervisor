#!/usr/bin/env python3
"""Recap GitHub: campi giusti (20/11/2026, supervisor-recap-github-campi-20-11). Le uscite vere della 0.8.191 sul Chromebook, coi
blocchi di tests/fixtures/recap-github-blocchi-20-11.txt: il prefisso preso dal titolo o dal link nel testo invece che dal repo
del blocco, «#N» nel prefisso, l'autore perso ([CITAZIONE], [ISSUE DA UTENTE]), il doppio spazio, il titolo perso quando il
modello non rispondeva in 60 s (38-61 s a riga), un blocco di tipo sconosciuto sparito con exit 0.

GC1 [STATO] su un'issue della persona: «chrome-bridge: wondermuse ha commentato un'issue su Chrome Bridge (…)»
GC2 [CITAZIONE] su un repo d'altri: «claude-code: rbartoli ha commentato un'issue su Claude Code (…)», niente «#N» nel prefisso,
    niente doppio spazio
GC3 [ISSUE DA UTENTE] da un bot: il prefisso e' il repo del blocco (claude-master-watch), l'autore c'e'
GC4 [ISSUE DA UTENTE] di una persona: «chrome-bridge: wondermuse ha aperto un'issue su Chrome Bridge (…)»
GC5 un blocco di tipo sconosciuto in mezzo agli altri esce com'e'; i conosciuti restano una riga ciascuno
GC6 il modello parte leggero (--setting-sources project, --strict-mcp-config, niente strumenti, haiku); se non risponde, ogni
    blocco esce entro 30 s col titolo fra «» (o l'inizio del testo, se il blocco non ha titolo)
GC7 la [MENZIONE] del 19/11 resta «chrome-bridge: wondermuse ha aperto una PR su HOL Guard (…)»
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

BLOCKS = (Path(__file__).resolve().parent / "fixtures" / "recap-github-blocchi-20-11.txt").read_text(encoding="utf-8")
parts = [b.strip() for b in BLOCKS.split("\n\n") if b.strip()]
STATO, CIT, ISSUE, ISSUE2, COMMENTO = parts
tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
state.mkdir()
argslog = tmp / "args.log"
fake = tmp / "fake-claude"
fake.write_text(f'#!/bin/sh\nprintf "%s" "$*" | tr "\\n" " " >> {argslog}\necho >> {argslog}\n'   # una riga per chiamata (il prompt ha un a capo)
                f'if [ -n "$FAKE_SLOW" ]; then sleep 60; fi\necho "profilo di sicurezza per chrome-bridge"\n')
fake.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "default_account": "personale",
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "recap": {"github_owner": "frsorrentino", "summary_model": "haiku"}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_CLAUDE_BIN": str(fake)})
CLI = str(T.SCRIPTS / "supervisor")


def gh(text, slow=False):
    t0 = time.time()
    r = subprocess.run([CLI, "recap", "github"], input=text, capture_output=True, text=True, env=dict(ENV, **({"FAKE_SLOW": "1"} if slow else {})),
                       timeout=300)
    return r, time.time() - t0


def one(block):
    r, _ = gh(block)
    return r.stdout.strip()


s = one(STATO)
T.check("GC1 [STATO] on the person's issue: «chrome-bridge: wondermuse ha commentato un'issue su Chrome Bridge (…)»",
        s.startswith("chrome-bridge: wondermuse ha commentato un'issue su Chrome Bridge (") and s.endswith("#issuecomment-7009262128"), s)
c = one(CIT)
T.check("GC2 [CITAZIONE] on someone else's repo: «claude-code: rbartoli ha commentato un'issue su Claude Code (…)», no «#N» in the prefix, no double space",
        c.startswith("claude-code: rbartoli ha commentato un'issue su Claude Code (") and "  " not in c and "#91870:" not in c, c)
i = one(ISSUE)
T.check("GC3 [ISSUE DA UTENTE] from a bot: the prefix is the block's repo (claude-master-watch), the author is there",
        i.startswith("claude-master-watch: claude-observe-reports[bot] ha aperto un'issue su ") and "  " not in i, i)
i2 = one(ISSUE2)
T.check("GC4 [ISSUE DA UTENTE] from a person: «chrome-bridge: wondermuse ha aperto un'issue su Chrome Bridge (…)»",
        i2.startswith("chrome-bridge: wondermuse ha aperto un'issue su Chrome Bridge (") and i2.endswith("/issues/5"), i2)

OTHER = "[ALTRO TIPO] frsorrentino/supervisor qualcosa di nuovo 11-20T03:00\n  una riga di dettaglio"
r, _ = gh(COMMENTO + "\n\n" + OTHER + "\n\n" + ISSUE2)
out = r.stdout.strip().splitlines()
T.check("GC5 a block of an unknown kind among the others comes out as it is; the known ones stay one line each",
        r.returncode == 0 and len(out) == 4 and out[0].startswith("chrome-bridge: wondermuse ha commentato l'issue #5")
        and out[1] == "[ALTRO TIPO] frsorrentino/supervisor qualcosa di nuovo 11-20T03:00" and out[2] == "  una riga di dettaglio"
        and out[3].startswith("chrome-bridge: wondermuse ha aperto"), r.stdout)

argslog.write_text("")
times, lines = [], []
for b in (STATO, CIT, ISSUE2):
    rr, dt = gh(b, slow=True)
    times.append(round(dt, 1))
    lines.append(rr.stdout.strip())
args = argslog.read_text().splitlines()
T.check("GC6 the model starts light (setting sources project, strict MCP, no tools, haiku); when it does not answer each block is out within 30 s "
        "with the title in «» (or the start of the text when the block has no title)",
        args and all("--setting-sources project" in a and "--strict-mcp-config" in a and "--model haiku" in a for a in args)
        and all(t < 30 for t in times) and lines[0].endswith("(«HOL Guard profile for chrome-bridge?») — https://github.com/frsorrentino/chrome-bridge/issues/5#issuecomment-7009262128")
        and "(Three gaps from building a mod" in lines[1] and "«HOL Guard profile for chrome-bridge?»" in lines[2],
        f"{times}\n" + "\n".join(lines) + "\n" + "\n".join(args[:2]))

MENTION = ("[MENZIONE] hashgraph-online/hol-guard PullRequest «Add Chrome Bridge MCP server profile» 11-19T12:45\n"
           "  wondermuse https://github.com/hashgraph-online/hol-guard/pull/8079 Adds a Guard profile for [Chrome Bridge](https://github.com/frsorrentino/chrome-bridge), "
           "which exposes the user's real, logged-in Chrome as an MCP server.")
m = one(MENTION)
T.check("GC7 the [MENZIONE] of 19/11 stays «chrome-bridge: wondermuse ha aperto una PR su HOL Guard (…)»",
        m == "chrome-bridge: wondermuse ha aperto una PR su HOL Guard (profilo di sicurezza per chrome-bridge) — https://github.com/hashgraph-online/hol-guard/pull/8079", m)
T.finish()
