#!/usr/bin/env bash
# supervisor color — sceglie l'icona del titolo di scheda per una sessione tmux.
#
#   supervisor color <nome-tmux>      stampa "<icona> <etichetta>"
#
# Forma = account (dal prefisso del nome: `tabs`/`accounts.<n>.shape` in config,
# oggi tondo personale e quadrato professionale). Colore = quale sessione, e
# deve essere DIVERSO da quelli in uso.
#
# Il colore non si puo' scegliere guardando solo se stessi: un contatore globale
# non sa cosa e' ancora aperto e rida' lo stesso colore appena il giro si chiude,
# un hash del nome e' stabile ma non evita le collisioni. Serve un registro, e
# la lista delle sessioni vive come verita' su cosa e' occupato davvero (T25).
#
# Le voci di sessioni morte vengono potate a ogni giro: il registro non cresce e
# un colore liberato torna disponibile subito. Registro: `nome<TAB>indice`,
# stesso formato di ~/.claude/colori-sessione (condiviso nella prova, D4).
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"

NOME="${1:?uso: supervisor color <nome-tmux>}"
REG="$CM_TABS_COLOR_REGISTRY"
LOCK="$REG.lock"
mkdir -p "$(dirname "$REG")"

ACC="$(cm_account_of_name "$NOME")"
SHAPE="$(cm_get "$ACC" SHAPE)"
PFX="$(cm_get "$ACC" TMUX_PREFIX)"
palette_var="CM_TABS_COLORS_$(cm_upper "$SHAPE")"
FORME="${!palette_var:-}"
[ -n "$FORME" ] || FORME="$CM_TABS_COLORS_CIRCLE"
# conteggio con un array bash, non con `wc -w`: in locale C le emoji non
# contano come parole (N=0 → indice "nan" nel registro, visto il 09/09/2026)
read -ra ICONE <<<"$FORME"
N=${#ICONE[@]}
ETICHETTA="$NOME"
[ "$CM_TABS_STRIP_PREFIX" = true ] && [ -n "$PFX" ] && ETICHETTA="${NOME#"$PFX"}"

# Le forme di TUTTI i nomi del registro servono per la coppia forma+colore
# (oltre N vive il colore si ripete per forza): passo ad awk "prefisso=forma"
# per ogni account, dal prefisso piu' lungo al piu' corto.
MAPPA=""
for acc in $CM_ACCOUNTS_KEYS; do MAPPA="$MAPPA$(cm_get "$acc" TMUX_PREFIX)=$(cm_get "$acc" SHAPE)"$'\n'; done
MAPPA=$(printf '%s' "$MAPPA" | awk -F= '{print length($1) "\t" $0}' | sort -rn | cut -f2-)

# Lock con mkdir (T56): due schede aperte nello stesso istante sceglierebbero lo
# stesso colore leggendo il registro prima che l'altra lo scriva.
# Il lock non si salta mai (15/09/2026: dopo 5 s si andava avanti senza, e la trap
# cancellava il lock dell'altro giro): si aspetta. Solo un lock piu' vecchio di
# CM_COLOR_LOCK_STALE_S secondi e' di un giro morto (la sezione critica dura
# millisecondi) e si riprende. La trap toglie il lock solo se e' nostro.
STALE="${CM_COLOR_LOCK_STALE_S:-10}"
MIO=0
trap '[ "$MIO" = 1 ] && rmdir "$LOCK" 2>/dev/null' EXIT
trap 'exit 1' INT TERM
until mkdir "$LOCK" 2>/dev/null; do
  m=$(stat -c %Y "$LOCK" 2>/dev/null || stat -f %m "$LOCK" 2>/dev/null) \
    && [ $(( $(date +%s) - m )) -ge "$STALE" ] && rmdir "$LOCK" 2>/dev/null
  sleep 0.1
done
MIO=1

# nome:creazione di ogni sessione viva (`:` perche' tmux non lo ammette nei nomi; una
# TAB nel formato esce `_` quando manca LANG, come da cron e dal relay).
# Nessun server («no server running» / «error connecting») = nessuna sessione viva: si
# pota tutto. Qualunque altro errore = non si sa chi e' viva: TMUX_OK=0 e non si pota
# niente (15/09/2026: una potatura su una lista incompleta toglie la voce a una
# sessione aperta, e il suo indice va a un'altra).
OUT=$(cm_tmux list-sessions -F '#{session_name}:#{session_created}' 2>&1); RC=$?
TMUX_OK=1; VIVE="$OUT"
if [ "$RC" -ne 0 ]; then
  VIVE=""
  case "$OUT" in *"no server running"*|*"error connecting"*) ;; *) TMUX_OK=0 ;; esac
fi
[ -f "$REG" ] || : > "$REG"

# 11/11 (supervisor-macos-11-11): le liste su piu' righe passano dall'ambiente, non da -v: l'awk di macOS rifiuta un a capo
# in un -v («newline in string») e il registro dei colori non si scriveva
export CM_AWK_VIVE="$VIVE" CM_AWK_MAPPA="$MAPPA"
IDX=$(
  awk -v nome="$NOME" -v shape="$SHAPE" -v n="$N" -v tmux_ok="$TMUX_OK" '
    function shape_of(s,   k, p, i) {
      for (i = 1; i <= nm; i++) {
        p = mp[i]
        if (p == "" || substr(s, 1, length(p)) == p) return ms[i]
      }
      return "circle"
    }
    BEGIN {
      nm = split(ENVIRON["CM_AWK_MAPPA"], righe, "\n")
      for (i = 1; i <= nm; i++) { split(righe[i], kv, "="); mp[i] = kv[1]; ms[i] = kv[2] }
      nv = split(ENVIRON["CM_AWK_VIVE"], v, "\n")
      for (k = 1; k <= nv; k++) if (v[k] != "") { split(v[k], f, ":"); viva[f[1]] = 1; nata[f[1]] = f[2] + 0 }
      viva[nome] = 1              # la propria sessione potrebbe non esistere ancora
      if (!(nome in nata)) nata[nome] = 9999999999   # non ancora nata: la piu nuova
    }
    # registro: nome \t indice
    { if (tmux_ok != 1 || ($1 in viva)) { reg[$1] = $2; if ($1 != nome) {
        occupato[$2] = 1
        coppia[shape_of($1) "-" $2] = 1
        # una sessione piu vecchia, della stessa forma, sullo stesso indice: l indice e suo
        # (a pari secondo decide il nome, cosi le due non se lo scambiano a ogni giro)
        if (tmux_ok == 1 && (nata[$1] < nata[nome] || (nata[$1] == nata[nome] && $1 < nome))) prima[shape_of($1) "-" $2] = 1
      } } }
    END {
      # un indice resta della sessione per tutta la sua vita (15/09/2026: la scheda lo
      # fissa all attacco, il relay lo rilegge a ogni push); lo cede solo a una piu vecchia
      if (nome in reg && (tmux_ok != 1 || !((shape "-" reg[nome]) in prima))) { print reg[nome]; exit }
      for (i = 0; i < n; i++) if (!(i in occupato))            { print i; exit }
      # oltre N sessioni vive il colore si ripete per forza: si riusa quello con la
      # coppia forma+colore ancora libera, cosi restano distinguibili
      for (i = 0; i < n; i++) if (!((shape "-" i) in coppia)) { print i; exit }
      if (nome in reg) { print reg[nome]; exit }   # nessun posto migliore: tiene il suo
      print length(reg) % n
    }
  ' "$REG"
)

# riscrive il registro: le vive tranne se stessa, piu' la propria voce (tutte, se tmux non risponde)
{
  awk -v nome="$NOME" -v tmux_ok="$TMUX_OK" '
    BEGIN { nv = split(ENVIRON["CM_AWK_VIVE"], v, "\n"); for (k = 1; k <= nv; k++) if (v[k] != "") { split(v[k], f, ":"); viva[f[1]] = 1 } }
    $1 != nome && (tmux_ok != 1 || ($1 in viva)) { print $1 "\t" $2 }
  ' "$REG"
  printf '%s\t%s\n' "$NOME" "$IDX"
} > "$REG.tmp.$$" && mv "$REG.tmp.$$" "$REG"

printf '%s %s\n' "${ICONE[$IDX]}" "$ETICHETTA"
