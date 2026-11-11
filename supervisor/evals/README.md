# Eval di innesco delle skill (`claude plugin eval`)

Cinque casi, uno per superficie: la skill `sessions` (elenco, lancio, segnalazione), il comando `quota`
e la skill `screen-layout`. Ogni caso è un prompt in italiano nel registro di chi usa il plugin e un solo
grader `tool_used` su `Skill` con `input_match` sul nome atteso, marcato `arm: with-only`: con
`--ablation with-without` (il default quando il plugin si risolve) diventa l'indicatore «la skill è
scattata», non un punteggio. Solo `Skill` fra i tool: si misura l'innesco, non l'esecuzione (il corpo
delle skill vuole `Bash`, che qui manca apposta: nessun comando vero parte durante l'eval).

    cd supervisor && claude plugin eval . --runs 2 -j 1 --max-cost-usd 5

Ogni corsa è un processo `claude` figlio sul credenziale di chi lancia: sulla stessa quota. Non lanciarlo
mentre la macchina è satura (25/09/2026: load 78, RAM senza swap).
