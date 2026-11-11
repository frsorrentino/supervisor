#!/usr/bin/env bash
# supervisor attach — si attacca a una sessione tmux per nome, in una finestra di terminale.
#
#   supervisor attach <nome>            si attacca e basta
#   supervisor attach <nome> ephemeral  attaccandosi accende destroy-unattached:
#                                          chiudere la scheda chiudera' la sessione
#   (`effimera` accettato come sinonimo)
#
# Esiste per un motivo preciso (T6): il parser di riga di comando di garcon
# (Chromium CommandLine) SCARTA ogni token che inizia con "-". Passare
# "tmux attach -t nome" attraverso `garcon --client --terminal` fa sparire
# il -t: tmux ripiega su "attach all'ultima sessione usata" e ci si attacca
# a quella SBAGLIATA — sembrava funzionare quando per coincidenza l'ultima
# era quella giusta. Qui gli argomenti non hanno trattini e passano integri;
# i trattini che servono stanno DENTRO lo script, dove garcon non guarda.
#
# destroy-unattached si accende DOPO l'attacco, nello stesso comando (T7):
# acceso prima ucciderebbe la sessione appena creata, che nasce staccata. Un
# hook client-detached NON funziona (al distacco session_attached vale ancora 1).
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
NOME="${1:?uso: supervisor attach <nome-sessione> [ephemeral]}"
MODE="${2:-}"

cm_tmux has-session -t "=$NOME" 2>/dev/null || {
  cm_msg attach.missing "name=$NOME" >&2
  echo "  $(cm_tmux list-sessions -F '#{session_name}' 2>/dev/null | tr '\n' ' ')" >&2
  exit 3
}

if [ "$CM_TABS_TITLE" = true ]; then
  # Titolo della scheda. La scelta di forma e colore sta in `color`, che e'
  # l'unico punto dove si decide: usarlo anche dai wrapper di shell evita che
  # due vie diverse assegnino lo stesso colore a sessioni diverse.
  TITOLO="$("$CM_SCRIPTS/cm-color.sh" "$NOME")"
  printf '\033]0;%s\007' "$TITOLO"
  # Il printf da solo si perde (T26, visto il 2026-08-27: scheda rimasta col
  # titolo di un'altra sessione). tmux con set-titles riafferma il titolo a
  # ogni ridisegno.
  # T64: `set-option -t` NON accetta il prefisso `=` (come capture-pane e
  # send-keys, T1): risponde «no such session» — l'attacca legacy lo usava e
  # il set-titles falliva in silenzio dal 2026-08-27. Nome nudo, dopo che
  # has-session -t = ha garantito che il nome esatto esiste.
  cm_tmux set-option -t "$NOME" set-titles on 2>/dev/null
  cm_tmux set-option -t "$NOME" set-titles-string "$TITOLO" 2>/dev/null
fi

case "$MODE" in
  ephemeral|effimera)
    exec "${CM_TMUX_BIN:-tmux}" ${CM_TMUX_ARGS:-} ${CM_TMUX_SOCKET:+-L "$CM_TMUX_SOCKET"} \
      attach -t "=$NOME" \; set-option -t "$NOME" destroy-unattached on ;;
  "")
    exec "${CM_TMUX_BIN:-tmux}" ${CM_TMUX_ARGS:-} ${CM_TMUX_SOCKET:+-L "$CM_TMUX_SOCKET"} attach -t "=$NOME" ;;
  *)
    echo "supervisor attach: modo sconosciuto '$MODE' (ephemeral)" >&2; exit 2 ;;
esac
