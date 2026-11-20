# Contesto MACH + NANDA (aggiornato il 19/11 dopo il whitepaper)

## MACH – Milano Agentic City Hackathon
- Ven 20/11 dalle 15:00 (House of Action, Via Seprio 2), notte da remoto, sab 21/11 dalle 9:30 (ex spazi Breda, Viale Sarca 336/F, ed. 16): sviluppo, pitch, premiazione.
- Promotori: NextMindLab; partner Fastweb+Vodafone e BIP; Project NANDA (MIT); patrocinio Comune di Milano.
- Compito: agenti AI in un **ecosistema condiviso** che **dialogano con gli agenti degli altri team**.
- Tracce: Accessibilità, Mobilità, Ambiente (la pagina media cita anche Benessere).
- Forniti: calcolo, LLM con token. Output open source nella "Agentic City Platform".

## Cosa dice il whitepaper sull'hackathon (WG4)
- WG4 rilascia uno **starter kit open source** per agenti compatibili NANDA.
- Ogni team costruisce un **agente PoC** per un servizio cittadino.
- L'agente si **registra sul "nodo di Milano"** (NANDA Index di test) con una **AgentFacts card** (capability + endpoint).
- Deve **trovare, negoziare e passare compiti** agli agenti degli altri team (A2A o MCP).
- **Demo dal vivo** agent-to-agent a fine giorno 2; rilascio open source.
- Concetti citati: AgentAddr firmato (<120 byte), AgentFacts come Verifiable Credential, Adaptive Resolver, NANDA Adapter (ponte MCP/A2A), NANDA Town (registry, auth, trust, pagamenti, contract-net, negoziazione a offerte alternate), consenso e privacy, identità (Codice Fiscale/DID, SPID/CIE, IT-Wallet).
- **Conseguenza:** non serve costruire tutta l'architettura a 5 layer; serve un agente buono + registrazione + interoperabilità dimostrabile.

## Stack NANDA reale (repo projnanda)
- **nanda-index-v2**: indice di *organizzazioni* (`urn:ai:domain:<dominio>[:agent:<slug>]`) con `registry_url` e `media_type` (`application/ai-catalog+json`, `application/a2a-agent-card+json`). API: `/api/v1/index`, `/api/v1/resolve?locator=`, `/api/v1/search?q=`, `POST /auth/register|login`, `POST /api/v1/orgs`.
- **nanda-registry-server**: AI Catalog (`/.well-known/ai-catalog.json`, voci `urn:air:<dominio>:agent:<id>`); `/agents`, `/agents/search?q=`, `/agents/:id`.
- Risoluzione: Index → registry_url/agents/<id> → card A2A → endpoint.
- Altri: host39, nanda-town-2 (record YAML via PR), nanda-agent/adapter (agente testo→testo). Lo starter kit ufficiale si scopre all'evento.

## Dove sta nel codice
| Concetto | File |
|---|---|
| Registrazione e risoluzione sull'Index | `nanda/index_client.py`, `scripts/register_nanda.py` |
| AI Catalog del gateway | `nanda/catalog.py`, `gateway/server.py` |
| AgentFacts VC, AgentAddr, did:web | `nanda/signing.py` |
| Nodo finto per le prove | `nanda/local_index.py`, `scripts/fake_milan_node.py` |
| Contract-net, offerte alternate | `interop/negotiation.py` |
| MCP / kit testo→testo | `agents/mcp_export.py`, `interop/nanda_bridge.py` |
