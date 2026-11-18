# Stato del progetto

> File condiviso e breve: lo aggiornano persone e AI alla fine di ogni compito (vedi AGENTS.md).
> Tenete ogni sezione entro una schermata e cancellate le cose superate. La storia completa resta in git.

## Chi fa cosa

| Area | Responsabile | Cartelle |
|---|---|---|
| Comunicazione tra agenti (A2A, registry) | Leonardo | `a2a_lite/`, `registry/` |
| Agenti di servizio (layer 3) | Leonardo + ? | `agents/` |
| Orchestratore (layer 2) | ? | ? |
| Interfaccia / demo | ? | ? |
| Architettura e schema | Giovanni | `docs/` |

## In corso (per non pestarsi i piedi)

- nome · cosa · ramo · da quando

## Decisioni prese

- 2026-11-17 · A2A 1.0 con compatibilità 0.3; DataPart `{skill, args}` tra i nostri agenti; risposta standard `Esito`.

## Problemi aperti

-

## Registro modifiche (più recenti in alto)

- 2026-11-18 · Leonardo · repo · creata la repository GitHub con le regole per gli assistenti AI e `scripts/sync.py` · IMPATTO: tutti clonano la repo e lanciano `python scripts/sync.py` a inizio sessione
