# mach-agents

Livello di comunicazione (A2A + indice federato stile NANDA), gateway per gli altri team e agenti di servizio (layer 3) per il Milano Agentic City Hackathon.

Da leggere, in quest'ordine:
0. **[docs/HACKATHON_PLAYBOOK.md](docs/HACKATHON_PLAYBOOK.md)**: l'evento, cosa ci chiederanno (whitepaper WG4), lo stack NANDA e cosa fare il giorno 1.
1. **[docs/GUIDA.md](docs/GUIDA.md)**: i concetti (A2A, discovery, MCP, contratto degli agenti).
2. **[docs/ARCHITETTURA.md](docs/ARCHITETTURA.md)** e [lo schema v2](docs/ARCHITETTURA_SCHEMA_V2.mmd): l'architettura del team, come parliamo con gli altri team e le decisioni prese.
3. **[docs/PROTOCOLLO_MACH.md](docs/PROTOCOLLO_MACH.md)**: le regole del profilo MACH, da passare agli altri team.
4. **[contracts/README.md](contracts/README.md)**: il formato delle risposte (Busta 1.1).
5. **[docs/GUIDA-GIT.md](docs/GUIDA-GIT.md)**: come usare Git e GitHub nel team. Lo stato del lavoro è in **[STATO.md](STATO.md)**.

## Avvio rapido

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # httpx, starlette, uvicorn, pydantic, pytest
python -m pytest -q                # 79 test, senza rete né LLM
python scripts/run_local.py        # registry :8000 + agenti :8001-8004 + gateway :9000
python scripts/demo_scenario.py    # in un altro terminale: la pratica completa
```

Prova generale del nodo di Milano, con gli script di prova in [scripts/prove/](scripts/prove/README.md) (NANDA Index finto, registrazione, risoluzione firmata, discovery, delega):

```bash
python scripts/prove/fake_milan_node.py  # NANDA Index v2 locale :8300
python scripts/prove/fake_team_b.py
python scripts/prove/demo_milano.py      # oppure --index <URL del nodo vero>
python scripts/register_nanda.py --index <URL> --check   # il giorno dell'hackathon
```

Prova di interoperabilità con un team finto (agente A2A, API REST e indice in processi separati):

```bash
python scripts/prove/fake_team_b.py      # Team B: agente :8101, API :8102, indice :8103
python scripts/prove/demo_interop.py     # indici federati, chiamate via gateway, delega A2A → API → escalation
```

Apri http://localhost:8000 per vedere gli agenti registrati e i messaggi che si scambiano in tempo reale.

Per usare Gemma 4: copia `.env.example` in `.env` e imposta `LLM_BASE_URL` e `LLM_MODEL`. Senza LLM gli agenti funzionano comunque, con il routing per parole chiave.

## Struttura

```
a2a_lite/        protocollo A2A 1.0 (+ compatibilità 0.3): tipi, server, client
registry/        indice federato stile NANDA: AgentFacts, discovery, peer di altri team, /index.json, eventi
gateway/         A2A Gateway inbound: API key per team, scope, quote, hop limit, card pubbliche, audit,
                 AI Catalog (/.well-known/ai-catalog.json), did.json, AgentFacts VC e AgentAddr firmati
nanda/           compatibilità con lo stack NANDA: catalog (AI Catalog, URN), signing (Ed25519, VC, AgentAddr),
                 index_client (NANDA Index v2: registrazione, resolve, search), local_index (nodo finto), town (YAML)
interop/
  negotiation.py contract-net (bando → offerte → aggiudicazione) e offerte alternate sul prezzo
  nanda_bridge.py adattatore testo→testo per kit "a template"
  mach.py        profilo MACH: metadata.mach (trace_id, hops, consenso), claims firmati, scope
  policy.py      gate dati: cosa può uscire verso quale team (minimizzazione, consenso)
  foreign.py     spoke esterno: delega A2A → API documentata → escalation, risultati marcati "external"
  http_api.py    client per API REST documentate di altri team (retry, circuit breaker)
contracts/       Busta 1.1: modelli, JSON Schema, esempi (revisione del lavoro di Tommi)
agents/
  base.py        BaseAgent: skill, Agent Card, routing, INPUT_REQUIRED, consenso, permessi, Busta
  envelope.py    Esito: formato interno delle skill, convertito in Busta 1.1
  tools.py       ToolRegistry: tool → schema per function calling / server MCP
  llm.py         client compatibile OpenAI (vLLM/Ollama/LM Studio per Gemma 4) + LLM finto per i test
  health/        Agente Sanità: orienta, cerca_disponibilita, prenota (idempotente), emergenza 112
  mobility/      Agente Mobilità: percorso (senza barriere), segnala_evento, stato_rete
  accessibility/ Agente Accessibilità: accessibilita_sede, profilo_bisogni (con consenso), semplifica
  environment/   Agente Ambiente: impronta_spostamento, confronta_mezzi (chiede il percorso a Mobilità), budget_carbonio
  mcp_export.py  espone le skill di un agente come server MCP
scripts/         run_local.py (avvia tutto), sync.py (aggiorna la repo), demo_scenario.py (demo),
                 register_nanda.py, nandatown_records.py (registrazione in gara)
  prove/         piano B senza nodo vero né altri team: fake_milan_node, fake_team_b, demo_milano, demo_interop
docs/            playbook + contesto, guida ai concetti, architettura (v2 + interoperabilità), profilo MACH, guida git
tests/           protocollo, contratto, sistema completo, interoperabilità tra team
```

## Chiamare un agente da Python (per chi fa l'orchestratore)

```python
from a2a_lite import A2AClient, TaskState
from registry import RegistryClient

rc = RegistryClient("http://localhost:8000")
[mobilita] = (await rc.search(skill="percorso"))[:1]
client = A2AClient(mobilita["card_url"], caller="orchestratore")

task = await client.send(data={"skill": "percorso",
                               "args": {"origine": "Loreto", "destinazione": "niguarda", "senza_barriere": True}},
                         context_id=pratica_id)
if task.status.state == TaskState.INPUT_REQUIRED:
    task = await client.send(risposta_cittadino, task_id=task.id, context_id=task.context_id)
busta = task.result_data()   # Busta 1.1: {"status", "data", "provenance", "needs_input", "errors", ...}
```
