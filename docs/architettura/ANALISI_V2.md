# Architettura v2 (schema di Giovanni, 17–18/11/2026): analisi e decisioni

Schema: [schema-v2.mmd](schema-v2.mmd) (Mermaid; si apre su GitHub o su mermaid.live).

> **Aggiornamento 19/11 (whitepaper):** all'hackathon conta un agente PoC ben fatto, registrato sul nodo di Milano e capace di interagire con gli altri team. Questo schema resta la visione da raccontare nel pitch; i Layer 4–5 completi probabilmente non vanno costruiti. Vedi [../HACKATHON_PLAYBOOK.md](../HACKATHON_PLAYBOOK.md).

## Cosa cambia rispetto alla v1
- **Layer 1**: nuovo "A2A API Gateway (inbound da altri team)" che entra in Identity & Security, con un'etichetta "MACH Interop Protocol".
- **Layer 3**: nuovo spoke "Ente Esterno (Foreign Team Cartridge)" con Foreign Agent Adapter → Interop Tools → "Richiamo API Team Esterno (se disponibile)" → "RPA su App Team Esterno (API sniffing & DOM parsing)" → Discovery Fallback → JSON Formatter → "JSON Payload Ext".
- **Layer 2**: terzo sub-task "Sub-Task (MACH Protocol)" verso lo spoke esterno.
- **Layer 4 e 5**: invariati.

## Sintesi della Architecture Review
Giudizio: l'idea è giusta, ma i percorsi nuovi sono disegnati solo in andata. 10 rilievi nuovi (6 gravi).

| Id | Rilievo | Correzione proposta | Nel codice |
|---|---|---|---|
| A | Un agente che chiama non riceve risposta (i ritorni vanno tutti alla UI) | Ogni esito torna al canale d'origine; per gli agenti si usano gli stati del task A2A | `gateway/`, `a2a_lite/` |
| B | Nessuno può dare il consenso se il chiamante è un agente | Approvatore = la persona dietro l'agente (via input-required) o un mandato concordato | `metadata.mach.consent`, `INPUT_REQUIRED` |
| C | Un solo controllo d'identità per utenti e agenti | Credenziali per team, scope per skill, quote, deleghe; card pubblica con le sole skill offerte | `gateway/` |
| D | Due nomi per il protocollo ("A2A" e "MACH") | MACH = profilo di A2A | `docs/PROTOCOLLO_MACH.md` |
| E | Nessuna protezione dai loop tra team | trace_id + hop, profondità massima | `interop/mach.py` |
| F | Lo spoke esterno non usa il protocollo di cui porta il nome | Prima task A2A, poi API documentata, poi escalation | `interop/foreign.py` |
| G | Fallback via scraping delle API interne di un partner | Eliminarlo per i team esterni | non implementato apposta |
| H | Dati dell'utente escono prima del consenso | Gate di condivisione dati prima dell'adapter | `interop/policy.py` |
| I | I risultati esterni entrano nell'Aggregatore come quelli interni | Validazione + provenienza + livello di fiducia | `provenance.trust` nella Busta |
| J | Il Layer 5 non sa agire tramite il team esterno | Step "task A2A delegato" nella Saga; compensazione = cancel del task | `ForeignAgentAdapter.cancel()` |

## Decisione: gli altri team passano dall'orchestratore o parlano con i sub-agenti?
**Ibrido: "porta unica, indirizzi multipli".**
- Ogni agente di servizio (Accessibilità, Mobilità, Sanità, Ambiente) è **pubblicato nell'indice** con la sua Agent Card. Gli altri team lo chiamano **direttamente** per le richieste di un solo dominio: è lo spirito NANDA.
- Anche l'orchestratore può essere pubblicato, con una skill per le pratiche multi-ente.
- Tutte le chiamate esterne passano dal **gateway inbound** (reverse proxy `https://<gateway>/agents/<nome>/`): identità del team, scope per skill, quote, limite di hop, audit, card filtrate. Il gateway non decide il percorso.
- Le skill con effetti reali (es. `prenota`) richiedono il consenso nel messaggio; se manca, l'agente risponde INPUT_REQUIRED.

## "Protocollo MACH" = profilo di A2A 1.0
Nessun protocollo nuovo: A2A 1.0 più convenzioni nostre, dichiarate come estensione nella Agent Card.
- Richieste strutturate come DataPart `{skill, args}`.
- `metadata.mach` = {trace_id, hops, caller, deadline, consent}.
- Output nella Busta 1.1 (`contracts/`).
- Discovery: registry federato + NANDA Index (AI Catalog, AgentFacts firmati).
