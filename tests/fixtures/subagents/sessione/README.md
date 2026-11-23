# Fixture dei sottoagenti (23/11/2026)

- `sessione/subagents/agent-*`: quattro sottoagenti veri di Claude Code 2.1.329 su win (un Explore, due
  general-purpose in background, un fork di ottobre senza trascrizione), ridotti con `riduci.py`: le righe e le
  chiavi lette da supervisor restano, i testi no.
- `sessione/subagents/workflows/wf_fixture-1/`: un Workflow. Il meta.json e il journal hanno la forma di un Workflow
  vero di Claude Code 2.1.322 sul Chromebook (agentType workflow-subagent, workflowPhase, niente toolUseId ne'
  finished; journal con launched, started e result per agentId); le trascrizioni sono quelle ridotte di win. Il
  terzo agente ha started senza result: e' in corso.
