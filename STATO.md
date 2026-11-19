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

- Leonardo · compatibilità NANDA (Index v2, AI Catalog, firme), Agente Ambiente, negoziazione · `leo/whitepaper-nanda` · 2026-11-19

## Decisioni prese

- 2026-11-17 · A2A 1.0 con compatibilità 0.3; DataPart `{skill, args}` tra i nostri agenti; risposta standard `Esito`.
- 2026-11-19 · Dopo il whitepaper: priorità a un agente PoC ben fatto + registrazione sul nodo di Milano + interoperabilità dimostrabile; l'architettura a 5 layer completa probabilmente non serve (vedi `docs/HACKATHON_PLAYBOOK.md`).

## Problemi aperti

- Il kit ufficiale WG4 e il tipo di nodo di Milano si scoprono solo all'evento: adattatori pronti (`register_nanda.py`, `nandatown_records.py`, `mcp_export.py`, `nanda_bridge.py`).
- I fattori di emissione dell'Agente Ambiente sono indicativi: sostituire con dati ISPRA/EEA.

## Registro modifiche (più recenti in alto)

- 2026-11-19 11:30 · Leonardo (con Claude) · nanda, gateway, agents, interop · compatibilità con lo stack NANDA reale (registrazione sull'Index, AI Catalog, AgentFacts/AgentAddr firmati), Agente Ambiente, contract-net, export MCP, prova generale `demo_milano.py`, playbook · IMPATTO: `BaseAgent` ha il nuovo metodo `invoke()` (aggiunta, niente cambia per chi usa le firme esistenti); nuove dipendenze `cryptography`, `pyyaml` → rifare `pip install -e ".[dev]"`
- 2026-11-18 · Leonardo · repo · creata la repository GitHub con le regole per gli assistenti AI e `scripts/sync.py` · IMPATTO: tutti clonano la repo e lanciano `python scripts/sync.py` a inizio sessione
