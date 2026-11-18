# Guida: comunicazione tra agenti e agenti di servizio (layer 3)

Questa guida spiega i concetti e collega ognuno al codice del repository. Leggila in ordine la prima volta; dopo, usala come riferimento.

---

## 1. Il quadro in tre domande

Quando un agente deve farsi aiutare da un altro, deve risolvere tre problemi diversi:

| Domanda | Soluzione | Nel repo |
|---|---|---|
| **Chi** può aiutarmi? | Discovery: un *registry* dove gli agenti pubblicano cosa sanno fare (NANDA Index, AgentFacts) | `registry/` |
| **Come** gli parlo? | Un protocollo comune per messaggi e compiti: **A2A** | `a2a_lite/` |
| Come usa **i suoi strumenti**? | Un protocollo per collegare un agente a tool e dati: **MCP** | `agents/tools.py` |

Raskar usa questa metafora: A2A sono le strade, il NANDA Index è l'elenco degli indirizzi. MCP invece è la "cassetta degli attrezzi" che ogni agente tiene in casa sua.

```
 Orchestratore (layer 2)
      │ 1. "chi sa calcolare percorsi?"           ┌────────────┐
      ├──────────────────────────────────────────►│  Registry  │
      │ ◄── card_url dell'Agente Mobilità ─────── └────────────┘
      │ 2. A2A SendMessage                       ▲ registrazione + heartbeat
      ▼                                           │
 ┌─────────────────┐  A2A (agente→agente)  ┌──────┴──────────┐
 │ Agente Sanità   │──────────────────────►│ Agente Access.  │
 └───────┬─────────┘                       └─────────────────┘
         │ MCP / funzioni
         ▼
   dati, API, mock
```

---

## 2. A2A spiegato bene

A2A (Agent2Agent) è uno standard aperto, oggi gestito dalla Linux Foundation. La **versione 1.0** è uscita il 12 marzo 2026. La specifica è su https://a2a-protocol.org/latest/specification/.

### 2.1 La Agent Card: il biglietto da visita

Ogni agente pubblica un file JSON a un indirizzo fisso:

```
GET http://<agente>/.well-known/agent-card.json
```

Contiene chi è l'agente, dove riceve i messaggi, con quale protocollo e cosa sa fare (le *skill*). Ecco un estratto della card del nostro Agente Mobilità:

```json
{
  "name": "Agente Mobilità",
  "description": "Calcola percorsi con la metropolitana di Milano ...",
  "version": "0.1.0",
  "supportedInterfaces": [
    {"url": "http://localhost:8002/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
  ],
  "capabilities": {"streaming": false, "pushNotifications": false, "extendedAgentCard": false},
  "defaultInputModes": ["text/plain", "application/json"],
  "skills": [{
    "id": "percorso",
    "name": "Percorso con i mezzi",
    "description": "... Argomenti: origine (string, obbligatorio), destinazione (string, obbligatorio), senza_barriere (boolean) ...",
    "tags": ["mobilita", "trasporti", "percorso", "atm", "accessibilita"],
    "examples": ["Come arrivo da Loreto a Niguarda?"]
  }]
}
```

La card la genera il `BaseAgent` partendo dai metodi decorati con `@skill` (`agents/base.py`, metodo `card`). Gli argomenti finiscono nella descrizione così anche l'LLM di un altro team capisce come chiamarci.

### 2.2 Il trasporto: JSON-RPC su HTTP

Tutte le chiamate sono `POST` allo stesso URL, con un corpo JSON-RPC 2.0:

```json
{"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": { ... }}
```

Il client manda anche l'header `A2A-Version: 1.0`. I metodi principali sono:

| Metodo | A cosa serve |
|---|---|
| `SendMessage` | invia un messaggio; crea un task o ne continua uno |
| `GetTask` | legge lo stato di un task (polling) |
| `CancelTask` | annulla un task non ancora concluso |
| `ListTasks` | elenca i task (es. per contextId) |
| `SendStreamingMessage`, `SubscribeToTask` | aggiornamenti in streaming (SSE): **non** li implementiamo |

### 2.3 Message e Part

Un **Message** è un turno di conversazione: ha un `role` (`ROLE_USER` per chi chiede, `ROLE_AGENT` per chi risponde) e una lista di **Part**. Ogni Part contiene *una sola* di queste cose:

- `text`: testo libero;
- `data`: JSON strutturato;
- `url` oppure `raw` (base64): file.

```json
{
  "role": "ROLE_USER",
  "messageId": "4f1c…",
  "parts": [
    {"text": "Percorso senza barriere verso Niguarda"},
    {"data": {"skill": "percorso", "args": {"origine": "Loreto", "destinazione": "niguarda", "senza_barriere": true}}}
  ]
}
```

**Questa è la nostra convenzione chiave**: tra i nostri agenti usiamo la DataPart `{"skill", "args"}`, che è veloce, deterministica e non consuma token. Gli agenti degli altri team possono mandarci solo testo, e noi lo gestiamo comunque (routing con LLM o per parole chiave). Vedi §5.

### 2.4 Il Task e il suo ciclo di vita

Quando un agente riceve un messaggio, crea un **Task**: l'unità di lavoro con un id e uno stato.

```
SUBMITTED ─► WORKING ─┬─► COMPLETED   (risultato negli artifacts)
                      ├─► INPUT_REQUIRED ──(il chiamante risponde con lo stesso taskId)──► WORKING ...
                      ├─► FAILED       (errore, fonte offline)
                      ├─► REJECTED     (non è compito mio)
                      └─► CANCELED
```

In JSON gli stati si scrivono `TASK_STATE_COMPLETED`, `TASK_STATE_INPUT_REQUIRED`, ecc. Tre identificativi da non confondere:

| Id | Cosa identifica | Chi lo crea |
|---|---|---|
| `messageId` | un singolo messaggio | chi lo invia |
| `taskId` | un compito (può durare più turni) | il server |
| `contextId` | una conversazione o pratica intera, anche con più task | il server, poi il client lo riusa |

**Consiglio per l'orchestratore**: usate un `contextId` per pratica del cittadino e passatelo a tutti gli agenti. Così si ricostruisce tutto il percorso dai log.

### 2.5 Il dialogo a più turni (INPUT_REQUIRED)

È il meccanismo con cui un agente dice "mi manca un dato". Lo implementa `A2AServer`, il `BaseAgent` lo usa in automatico. Ecco i due scambi reali tra orchestratore e Agente Sanità:

```json
// 1) richiesta
{"method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "m1",
  "parts": [{"data": {"skill": "cerca_disponibilita", "args": {"prestazione": "visita ortopedica"}}}]}}}

// risposta: il task resta aperto
{"result": {"task": {"id": "T1", "contextId": "C1",
  "status": {"state": "TASK_STATE_INPUT_REQUIRED",
             "message": {"role": "ROLE_AGENT", "parts": [
               {"text": "Hai già una prescrizione (ricetta) del medico per questa visita?"},
               {"data": {"status": "NEEDS_INPUT", "mancanti": ["ha_prescrizione"], "...": "..."}}]}}}}}

// 2) il chiamante risponde citando taskId e contextId
{"method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "m2",
  "taskId": "T1", "contextId": "C1", "parts": [{"text": "sì"}]}}}

// risposta: stesso task, ora COMPLETED con il risultato negli artifacts
```

Il "sì" in testo libero viene convertito nel tipo giusto (`bool`) guardando lo schema dell'argomento mancante (`BaseAgent._coerce`).

### 2.6 Artifact e status.message

- **Artifact**: il prodotto finale del task (il risultato). Lo leggi con `task.result_data()` / `task.result_text()`.
- **status.message**: un messaggio legato allo stato corrente. Per esempio la domanda in INPUT_REQUIRED o il motivo di un FAILED.

### 2.7 Cosa non usiamo, e perché

- **Streaming (SSE)** serve quando un task dura minuti e vuoi aggiornamenti progressivi. I nostri agenti rispondono in pochi secondi: il blocco sincrono basta.
- **Push notification** (webhook) servono quando il task dura ore. Non è il nostro caso.

Se all'hackathon servissero, si aggiungono al server senza toccare gli agenti.

### 2.8 Versione 1.0 contro 0.3

Molti esempi online e alcuni SDK usano ancora la 0.3. Le differenze sul filo sono queste:

| | 0.3 | 1.0 |
|---|---|---|
| Metodi | `message/send`, `tasks/get` | `SendMessage`, `GetTask` |
| Part | `{"kind": "text", "text": …}` | `{"text": …}` (niente `kind`) |
| Ruoli | `"user"`, `"agent"` | `"ROLE_USER"`, `"ROLE_AGENT"` |
| Stati | `"input-required"` | `"TASK_STATE_INPUT_REQUIRED"` |
| Card | campo `url` | `supportedInterfaces[]` |
| Risultato di SendMessage | il Task direttamente | `{"task": …}` oppure `{"message": …}` |

**Il nostro server accetta entrambe**: se arriva `message/send`, risponde in 0.3. Il nostro client parla 1.0 e, se il server non lo capisce, riprova in 0.3. È un vantaggio concreto: possiamo parlare con tutti gli altri team.

### 2.9 Errori

Gli errori sono errori JSON-RPC con codici specifici: `-32001` task non trovato, `-32002` task non annullabile, `-32004` operazione non supportata, `-32009` versione non supportata, più quelli standard (`-32601` metodo sconosciuto, `-32602` parametri non validi). Vedi `a2a_lite/errors.py`.

### 2.10 Perché un'implementazione nostra (`a2a_lite`) invece dell'SDK ufficiale?

L'SDK ufficiale (`pip install a2a-sdk`, versione 1.x, con modalità di compatibilità 0.3) va benissimo. Però tra 0.3 e 1.x le sue API Python sono cambiate molto, e molti esempi online non funzionano più. L'interoperabilità dipende dal **formato sul filo**, non dalla libreria, e `a2a_lite` è piccolo, leggibile e testato. **Da fare mercoledì**: verificare che il client ufficiale parli con i nostri agenti e viceversa (§8). Se qualcosa non torna, si corregge in un punto solo (`a2a_lite/types.py`).

---

## 3. Discovery: registry, AgentFacts, NANDA

### 3.1 Due modi di trovare un agente

1. **Conosci già l'indirizzo** e leggi `/.well-known/agent-card.json`. È semplice, ma qualcuno deve dirti l'indirizzo.
2. **Chiedi a un registry**: "chi sa calcolare percorsi?". Il registry restituisce gli agenti adatti con il loro indirizzo. È la parte che rende il sistema *aperto*: l'orchestratore non ha gli agenti scritti nel codice.

### 3.2 AgentFacts e NANDA Index

- **AgentFacts** è il formato NANDA per descrivere un agente: id, nome, provider, endpoint, capability, skill, e in più valutazioni, telemetria e certificazioni (firmabili crittograficamente). I campi base corrispondono a quelli della Agent Card. Il registry li genera dalla card: `registry/agentfacts.py`.
- **NANDA Index** è un indice minimale e federato: non contiene tutto, *punta* a dove stanno gli AgentFacts di ogni agente. È pensato per miliardi di agenti, con nomi tipo `@registry:namespace/agent-id`.
- **NANDA Adapter** (`pip install nanda-adapter`) è la loro libreria per rendere un agente raggiungibile e registrato. Si definisce una funzione messaggio → stringa e i messaggi tra agenti usano la sintassi `@agent_id testo`.

### 3.3 Cosa abbiamo noi

`registry/server.py` è un registry locale "in stile NANDA":

| Endpoint | Funzione |
|---|---|
| `POST /agents` | registrazione con `{card, card_url}`: genera gli AgentFacts |
| `GET /search?q=…&tag=…&skill=…` | discovery per capacità, ordinata per punteggio |
| `GET /agents/{id}` | risoluzione: AgentFacts + card |
| `POST /agents/{id}/heartbeat` | "sono vivo". Dopo 120 s senza heartbeat l'agente risulta non attivo |
| `POST/GET /events` | traccia dei messaggi tra agenti |
| `GET /` | pagina che mostra agenti e messaggi in tempo reale (ottima per la demo) |

Gli agenti si registrano da soli all'avvio e mandano l'heartbeat (`agents/base.py`, `build_app`).

### 3.4 All'hackathon: tre scenari

1. **Gli organizzatori forniscono un registry (NANDA o altro).** Riscrivete solo `registry/client.py` mantenendo gli stessi metodi (`register`, `search`, `resolve`). Gli agenti non cambiano.
2. **Forniscono una piattaforma con il NANDA Adapter.** La firma richiesta (messaggio → risposta) è già quella del nostro `handle`: si scrive un piccolo wrapper.
3. **Non forniscono niente.** Offrite il vostro registry agli altri team ("registratevi qui"). Diventate il punto di incontro dell'ecosistema: è un'ottima posizione per la giuria.

---

### 3.5 Aggiornamento del 18/11: indice federato e gateway
Il registry ora importa gli indici degli altri team (`POST /peers`, `POST /import`) e marca ogni agente con `origin` e `trust`. Pubblica anche `/index.json`, che gli altri possono importare. Gli altri team raggiungono i nostri sub-agenti direttamente, tramite il gateway: `<gateway>/agents/<nome>/`. Dettagli e motivazioni in `docs/ARCHITETTURA_INTEROP.md`.

## 4. MCP contro A2A: quando un tool, quando un agente

| | Tool (MCP) | Agente (A2A) |
|---|---|---|
| Chi decide | chi lo chiama | lui stesso (ha logica o LLM) |
| Interazione | una chiamata, una risposta | può fare domande, durare più turni |
| Esempio | "elenca le fermate della M5" | "trovami un percorso accessibile e avvisami se cambia qualcosa" |
| Confine | dentro l'agente | tra organizzazioni |

Regola pratica: **un ente diverso = un agente diverso; una funzione dello stesso ente = un tool.** Ecco perché per la Sanità consiglio un agente con più tool (uno per fonte), e sub-agenti solo dove c'è un vero confine di responsabilità.

I tool si scrivono una volta con `ToolRegistry` (`agents/tools.py`). Ne ricava lo schema per il function calling dell'LLM e, con `to_mcp()`, li espone come server MCP (testato con `mcp` 2.x, dove `FastMCP` si chiama `MCPServer`).

---

## 5. Il contratto dei nostri agenti

### 5.1 Doppia modalità d'ingresso

1. **Strutturata**: DataPart `{"skill": "<id>", "args": {...}}`. Nessun LLM, risultato prevedibile. L'orchestratore la usa sempre.
2. **Linguaggio naturale**: solo testo. Se c'è l'LLM, sceglie la skill ed estrae gli argomenti; altrimenti si fa routing per parole chiave più `extract_args`. Gli argomenti obbligatori mancanti si chiedono con INPUT_REQUIRED.

### 5.2 L'Esito: la risposta standard

> Aggiornamento del 18/11: le skill restituiscono ancora un `Esito`, ma il `BaseAgent` lo converte nella **Busta 1.1** (`contracts/`) prima di rispondere. I nomi cambiano così: `fonti` → `provenance`, `mancanti` → `needs_input.fields` / `errors`, `message` → `user_message.text`.

Ogni skill restituisce un `Esito` (`agents/envelope.py`). È il "JSON Formatter" dello schema di Giovanni:

```json
{"status": "SUCCESS", "message": "…", "data": {…},
 "fonti": [{"nome": "…", "url": "…", "simulato": true, "recuperato": "2026-11-17T…"}],
 "mancanti": [], "skill": "percorso"}
```

| Esito | Stato A2A | Significato |
|---|---|---|
| SUCCESS | COMPLETED | tutto ok |
| PARTIAL | COMPLETED | utile ma incompleto: vedi `mancanti` |
| EMERGENCY | COMPLETED | interrompere il percorso (es. 112) |
| NEEDS_INPUT | INPUT_REQUIRED | serve un dato |
| OFFLINE / ERROR | FAILED | fonte giù / richiesta sbagliata |
| OUT_OF_SCOPE | REJECTED | non è compito mio |

`fonti` serve per mostrare al cittadino *da dove viene* ogni indicazione, come chiedi nelle tue risposte. `simulato: true` evita di spacciare dati finti per veri.

### 5.3 Proprietà già implementate e testate

- **Idempotenza**: `prenota` con la stessa `chiave_idempotenza` non crea mai due prenotazioni (risposta persa → si riprova senza rischi).
- **Emergenza**: l'Agente Sanità controlla le parole d'allarme prima di qualsiasi skill.
- **Consenso**: `profilo_bisogni` non salva nulla senza `consenso=true`, e altrimenti lo chiede.
- **Ripianificazione**: `segnala_evento` (ascensore guasto, sciopero) cambia i percorsi successivi.
- **Agente → agente**: `cerca_disponibilita` con `verifica_accessibilita=true` trova l'Agente Accessibilità nel registry e lo interroga in parallelo.

---

## 6. LLM: Gemma 4

Gemma 4 esiste in cinque taglie: E2B, E4B, 12B, 26B A4B (MoE) e 31B. Ha function calling nativo, ruolo di sistema nativo e contesto da 128K (modelli piccoli) a 256K (medi).

- Il nostro client (`agents/llm.py`) usa l'API **compatibile OpenAI** (`/v1/chat/completions`), che vLLM, Ollama, LM Studio e SGLang espongono. Configurazione nel file `.env` (vedi `.env.example`).
- Con **vLLM** il function calling richiede di attivare il parser dei tool all'avvio del server: controllate la documentazione della versione che usate.
- Google consiglia `temperature=1.0, top_p=0.95, top_k=64` per uso generale. Per il **routing in JSON** noi usiamo `temperature=0`: provate entrambe e misurate.
- **Regola d'oro**: l'LLM interpreta e scrive, le regole decidono. Prenotazioni, consenso ed emergenze non dipendono mai solo dall'LLM. E ogni agente funziona anche a LLM spento (fallback per parole chiave): la demo non muore se l'endpoint rallenta.

---

## 7. Aggiungere un agente o una skill (ricetta)

1. Crea `agents/<nome>/agent.py` con una classe che estende `BaseAgent` (nome, descrizione, `domande`).
2. Scrivi le skill come metodi `async def ...(self, ctx, arg1: str, arg2: bool = False) -> Esito` decorati con `@skill(id, nome, descrizione, tags=[...], examples=[...])`. Gli argomenti senza default sono obbligatori: se mancano, l'agente li chiede da solo.
3. Metti i dati in `data.json` (marcati come simulati finché non sono veri).
4. Opzionale: `extract_args` per estrarre argomenti dal testo senza LLM.
5. In fondo al file: `if __name__ == "__main__": run(MiaClasse, <porta>)`, poi aggiungi una riga in `scripts/run_local.py` e un test in `tests/`.

---

## 8. Interoperabilità con gli altri team: checklist

- [ ] **URL raggiungibile**: sulla stessa rete Wi-Fi usate l'IP della macchina (`--public-url http://192.168.x.y:8001`); altrimenti un tunnel (cloudflared, ngrok) o una VM. L'URL nella card deve essere quello pubblico.
- [ ] `curl <url>/.well-known/agent-card.json` funziona da un'altra macchina.
- [ ] Test con l'**SDK ufficiale**: `pip install a2a-sdk`, usate il suo client contro un nostro agente, e il nostro `A2AClient` contro un agente d'esempio dell'SDK.
- [ ] Concordate con gli altri team: lingua dei messaggi, uso delle DataPart, riuso del `contextId`.
- [ ] Token condiviso, se serve: `A2AServer(auth_token=...)` e `A2AClient(auth_token=...)`.

---

## 9. Limiti noti (da sapere prima della demo)

- Task e profili sono **in memoria**: si perdono al riavvio.
- Niente streaming né push notification (§2.7).
- La ricerca del registry è per parole chiave; se serve, si possono aggiungere gli embedding.
- Tutti i dati di dominio sono **simulati** (strutture, slot, ascensori, accessibilità delle sedi).
- La card include anche i campi della 0.3 (`url`, `protocolVersion`) per i client vecchi. Se un client 1.0 molto rigido li rifiuta, si spengono con `card.wire(legacy_fields=False)`.
