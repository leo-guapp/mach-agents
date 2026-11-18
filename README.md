# mach-agents

Livello di comunicazione (A2A + indice federato stile NANDA), gateway per gli altri team e agenti di servizio (layer 3) per il Milano Agentic City Hackathon.

Da leggere, in quest'ordine:
1. **[GUIDA.md](GUIDA.md)**: i concetti (A2A, discovery, MCP, contratto degli agenti).
2. **[docs/ARCHITETTURA_INTEROP.md](docs/ARCHITETTURA_INTEROP.md)**: come parliamo con gli altri team e perché.
3. **[docs/PROTOCOLLO_MACH.md](docs/PROTOCOLLO_MACH.md)**: le regole del profilo MACH, da passare agli altri team.
4. **[contracts/README.md](contracts/README.md)**: il formato delle risposte (Busta 1.1).

## Avvio rapido

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # httpx, starlette, uvicorn, pydantic, pytest
python -m pytest -q                # 68 test, senza rete né LLM
python scripts/run_local.py        # registry :8000 + agenti :8001-8003 + gateway :9000
python scripts/demo_scenario.py    # in un altro terminale: la pratica completa
```

Prova di interoperabilità con un team finto (agente A2A, API REST e indice in processi separati):

```bash
python scripts/fake_team_b.py      # Team B: agente :8101, API :8102, indice :8103
python scripts/demo_interop.py     # indici federati, chiamate via gateway, delega A2A → API → escalation
```

Apri http://localhost:8000 per vedere gli agenti registrati e i messaggi che si scambiano in tempo reale.

Per usare Gemma 4: copia `.env.example` in `.env` e imposta `LLM_BASE_URL` e `LLM_MODEL`. Senza LLM gli agenti funzionano comunque, con il routing per parole chiave.

## Struttura

```
a2a_lite/        protocollo A2A 1.0 (+ compatibilità 0.3): tipi, server, client
registry/        indice federato stile NANDA: AgentFacts, discovery, peer di altri team, /index.json, eventi
gateway/         A2A Gateway inbound: API key per team, scope, quote, hop limit, card pubbliche, audit
interop/
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
scripts/         run_local.py, demo_scenario.py, fake_team_b.py, demo_interop.py
docs/            architettura di interoperabilità, profilo MACH
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
