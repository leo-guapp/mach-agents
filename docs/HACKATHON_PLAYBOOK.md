# Playbook MACH (20–21 novembre 2026)

Cosa ci chiederanno, secondo il whitepaper (sezione WG4) e il riassunto del team:

1. costruire **un agente funzionante (PoC)** per un servizio cittadino in una delle tracce: Accessibilità, Mobilità, Ambiente;
2. **registrarlo sul nodo di Milano** (server di test con il NANDA Index) con una **AgentFacts card** (capability + endpoint);
3. farlo **trovare, negoziare e passare compiti** agli agenti degli altri team (A2A o MCP);
4. **demo dal vivo** agent-to-agent alla giuria a fine giorno 2;
5. **rilascio open source** nella Agentic City Platform.

Il kit ufficiale (WG4) e i token LLM li danno gli organizzatori il giorno stesso. Non sappiamo ancora com'è fatto: questo repository è pensato per adattarsi a qualunque forma prenda.

## Cosa serve davvero (e cosa no)

| Serve | Ce l'abbiamo | Dove |
|---|---|---|
| Agenti con skill chiare in almeno una traccia | Accessibilità, Mobilità, Ambiente (+ Sanità) | `agents/` |
| Endpoint A2A raggiungibile + Agent Card | sì, dietro il gateway | `gateway/`, `a2a_lite/` |
| Registrazione sul NANDA Index | sì, Index v2 + AI Catalog | `scripts/register_nanda.py`, `nanda/` |
| AgentFacts firmati (VC) e AgentAddr | sì, Ed25519 + did:web | `nanda/signing.py`, `/agents/<n>/agentfacts.json` |
| Discovery degli altri team | sì, registry federato + Index | `nanda/index_client.py`, `registry/` |
| Negoziazione | contract-net + offerte alternate | `interop/negotiation.py` |
| Hand-off verso altri team | spoke esterno A2A → API → escalation | `interop/foreign.py` |
| Sicurezza inbound | API key, scope, quote, hop limit, audit | `gateway/` |
| MCP (se il kit lo vuole) | export delle skill come tool MCP | `agents/mcp_export.py` |
| Adattatore al kit ufficiale | `invoke()` + bridge testo→testo | `agents/base.py`, `interop/nanda_bridge.py` |

**Probabilmente non serve** costruire l'intera architettura a 5 layer (saga, orchestratore centrale complesso, memoria del cittadino persistente, SPID/IT-Wallet reali). Al massimo va *raccontata* nella presentazione come visione, in linea con il whitepaper. Il tempo va su: un agente che funziona bene + interoperabilità dimostrabile.

## Prima ora: capire che nodo è

```bash
curl -s $NODO/health
curl -s $NODO/api/v1/index | head        # nanda-index-v2 → usare register_nanda.py così com'è
curl -s $NODO/.well-known/ai-catalog.json # nanda-registry-server → pubblicare noi come voci del catalogo
curl -s $NODO/agents                      # registry "vecchio stile" → adattare RegistryClient
```

- **nanda-index-v2** (il più probabile): org con `registry_url` = il nostro gateway e `media_type` AI Catalog. Script pronto.
- **nanda-town-2 / registro via PR**: `python scripts/nandatown_records.py --gateway $URL_PUBBLICO --github <utente>` genera i file YAML da proporre.
- **Kit con agente “a template”** (tipo `nanda-agent`, funzione testo→testo): usare `interop.nanda_bridge.sync_text_handler(agent)` come improvement function, oppure esportare le skill come MCP (`python -m agents.mcp_export accessibility --port 8201`).
- **Qualcosa di diverso**: il nucleo resta `agent.invoke(skill, args)`; si scrive solo l'adattatore.

## Checklist del giorno 1

1. `.env`: `LLM_BASE_URL`, `LLM_MODEL` (i token dati dagli organizzatori), `NANDA_INDEX_URL`, `MACH_GATEWAY_PUBLIC_URL` (URL **raggiungibile dagli altri**: IP della macchina in rete locale o tunnel), `MACH_PUBLISHER_DOMAIN`.
2. `python scripts/run_local.py` → controllare `$URL_PUBBLICO/.well-known/ai-catalog.json` da un altro PC.
3. `python scripts/register_nanda.py --check`, poi senza `--check` (crea account, org, verifica la risoluzione).
4. Scrivere sul canale dell'hackathon il nostro URN (`urn:ai:domain:<dominio>`) e il link a `docs/PROTOCOLLO_MACH.md`.
5. Creare chiavi per i team che ci vogliono chiamare (`gateway/config.json` → `teams`), scope minimi.
6. Cercare gli altri team: `NandaIndexSource` nella `DiscoveryClient`; provare una delega reale.
7. Scegliere la traccia su cui puntare per la demo e rendere quell'agente ottimo (dati reali di Milano se li danno).

## Demo per la giuria (5 minuti)

Prova generale completa, in locale:

```bash
python scripts/run_local.py
python scripts/fake_milan_node.py      # al posto del nodo vero
python scripts/fake_team_b.py          # al posto di un altro team
python scripts/demo_milano.py          # registrazione, risoluzione firmata, discovery, delega, Ambiente→Mobilità
```

Racconto suggerito: (1) il nostro agente è nell'Index e chiunque può verificarne l'identità (AgentAddr + VC firmati, did:web); (2) un cittadino chiede “come vado da Loreto a Turati inquinando meno, senza barriere?”; (3) l'Agente Ambiente chiede il percorso all'Agente Mobilità, (4) trova sul nodo un agente di un altro team e gli delega una parte, (5) se più agenti offrono lo stesso servizio, contract-net sceglie il migliore; (6) la dashboard su `:8000` mostra i messaggi in tempo reale.

## Rischi e piani B

- **Il nodo non è raggiungibile / cambia API** → il registry federato (`/index.json`, peer) funziona senza Index.
- **Gli altri team non parlano il profilo MACH** → l'A2A puro funziona; il profilo è opzionale (`mach_profile: false`).
- **Niente LLM** → gli agenti usano routing per parole chiave ed estrazione con regex.
- **Rete dell'evento chiusa** → tunnel o hotspot; tenere pronta la demo locale.
- **Chiave di firma**: `gateway_signing_key.pem` si crea da sola, non va mai committata (è in `.gitignore`).
