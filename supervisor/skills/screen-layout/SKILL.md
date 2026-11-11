---
name: screen-layout
description: Use when the user asks to arrange, tile, split, merge, move or lay out the Claude session windows on the screen or monitors (ChromeOS Terminal via chrome-bridge) — "affianca le sessioni", "mettile in colonne", "unisci in schede", "sposta a destra/sinistra/sul monitor", "layout mattina", "quali monitor vedi". English: "tile the sessions", "merge the tabs", "move to the right monitor", "save this layout".
---

# Disporre le sessioni sullo schermo (supervisor tile / merge / move / layout)

Vale solo con il Terminale di ChromeOS e chrome-bridge in ascolto (una sessione con l'MCP
attivo basta). Altrove i comandi escono dicendo cosa manca: non fingere che abbiano
funzionato.

    supervisor tile [nomi] [--rows|--grid] [--on POSTO] [--dry-run]   una finestra per sessione, affiancate
    supervisor merge [nomi]                                            tutte a schede di UNA finestra del Terminale
    supervisor move POSTO [nomi]                                       su un altro monitor, e riaffianca lì
    supervisor tile --where                                            i monitor riconosciuti
    supervisor layout save|restore|list NOME                           layout con nome

Alias: `affianca`, `riaffianca`, `unisci`, `sposta`; tasti tmux `Ctrl+b a` / `Ctrl+b u` / frecce
(config `tmux.keybindings`, blocco generato da `init --tmux`).

## Regole

- **Prima `--dry-run`** quando l'utente non è davanti allo schermo: dice cosa farebbe (quali
  finestre, quale monitor, larghezza delle colonne) senza toccare niente. Ogni giro di
  `merge` costa ~20 secondi a sessione.
- **I posti si chiamano per come stanno**: `nativo` è lo schermo incorporato (origine 0,0),
  gli altri `sinistra`/`destra` per posizione, `sopra` se l'esterno è uno solo (nomi in config
  `tile.monitor_names`). `sposta sinistra` trova comunque il monitor alla sinistra delle
  finestre, comunque si chiami. Un monitor senza nessuna finestra sopra non lo vede nessuno:
  `move` ci va a sbattere per trovarlo; `--where` non lo elenca.
- **Sessioni sparse su più monitor** vengono radunate dove sta la maggioranza (a parità,
  dove sta la prima chiesta): `tile` lo dice.
- **Meno di 340 px per colonna** (`tile.min_column_px`): griglia automatica.
- **Numero DISPARI di finestre con la master fra loro** (`tile.odd_layout` = `master-primary`): la master è la finestra grande a sinistra (60%), le altre impilate a destra
  in N-1 righe; `tile` lo dice («main-vertical»). Con un numero pari, con `--rows`/`--grid`, o
  se la master non è fra le affiancate: colonne/griglia uniformi come sempre. La colonna impilata
  può scendere sotto `min_column_px`: consentito e segnalato, non rifiutato. `odd_layout: uniform`:
  colonne uniformi anche con numero dispari.
- **`merge` non è il contrario di `tile`**: tornare a schede passa da una scheda duplicata
  e da un segnaposto consumato dalla shell; se «nessuna scheda nuova attaccata entro N s»,
  le vecchie NON sono state chiuse e la sessione è salva. Non riprovare a raffica.
- **Nessuna finestra del Terminale mostra la scheda iniziale «Terminale» (#home) accanto a una
  sessione**: la #home non si chiude finché ha compagnia ma si può portare via DA SOLA — `move_tab`
  in un popup nuovo, poi `close` lì. `launch`/`restart`/`restore` (scheda in una finestra aperta, o
  UNA finestra nuova via garcon per la prima), `tile` (una finestra app per sessione via garcon, mai
  popup) e `merge` (raccolta) la sfrattano da soli, sempre e solo per URL `#home`, mai per posizione,
  e solo se la finestra ha altre schede. Le finestre `normal` sono rifiutate (barra degli
  indirizzi).
- **Layout con nome**: `layout save NOME` fotografa tutte le finestre e le sessioni presenti;
  `layout restore NOME` riposiziona solo quelle che riconosce e dice quali mancano: allora
  si rifà con `tile` e si risalva.
- Dopo un riavvio di Chrome i titoli delle schede sono stantii: `tile` e `merge` li
  riscrivono da soli prima di cercare le schede.

## Cosa riferire

Il riepilogo che il comando stampa (`N/M finestre disposte`, monitor e posizione per
sessione, «NON applicata» se Chrome ha rifiutato). Se una finestra risulta non applicata,
di' quale e proponi `--dry-run` o `move` verso un monitor visibile, non un secondo `tile`
alla cieca.
