---
name: cerca
description: Use when the user asks what was done on a topic and when, in past sessions or other projects ("cosa abbiamo fatto su X", "quando abbiamo sistemato Y", "dove avevamo deciso Z", "l'altra volta come avevamo risolto", "cerca nelle conversazioni", "what did we do about X", "when did we fix Y", "find the conversation where"). Searches every project's past conversations (prompts, final replies, outcome lines, commits) in three steps with `supervisor cerca`.
---

# Cercare nel lavoro passato (supervisor cerca)

Un indice di tutte le conversazioni dei progetti, aggiornato da solo: i prompt della persona, la risposta finale di ogni
turno, la riga «Esito:» e i commit. Mai l'uscita degli strumenti, mai il testo fra `<private>` e `</private>`. Maiuscole e
accenti non contano. Si usa in tre passi, dal piu' corto al piu' lungo: leggere tutto subito costa contesto e confonde.

## 1. L'indice

```bash
supervisor cerca listino dicembre
```

Una riga per risultato, dal piu' recente: `#ID data ora · sessione · tipo · estratto`. Tutte le parole devono esserci;
se non trova niente, prova meno parole o un sinonimo. `--limit N` per piu' righe, `--json` per leggerle da un programma.

## 2. Le voci intorno

```bash
supervisor cerca --timeline 1234
```

Le voci della stessa conversazione prima e dopo quella scelta (`→` la segna), in ordine di tempo: si vede cosa e' stato
chiesto, cosa e' stato risposto e com'e' finita. Per un commit, i commit vicini dello stesso progetto. `--n 10` allarga.

## 3. Il testo intero

```bash
supervisor cerca --dettagli 1234
```

Il testo intero della voce, con sessione, cartella, data, conversazione e voce della chat. Solo per le voci che servono
davvero alla risposta.

## Come rispondere

Riporta alla persona la data e la sessione di cio' che hai trovato, con le parole che contano; se le voci si contraddicono,
la piu' recente vale, e dillo. Non citare mai un testo che l'indice non ha mostrato.
