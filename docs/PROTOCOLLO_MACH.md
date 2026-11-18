# Profilo MACH per A2A, versione 0.1

> **In short (EN):** "MACH protocol" is not a new protocol. It is **A2A 1.0** (JSON-RPC over HTTP, Agent Cards, tasks) plus a few conventions: structured requests as a DataPart `{"skill", "args"}`, a `metadata.mach` block (trace_id, hops, caller, consent) to trace requests and stop loops across teams, and responses wrapped in a common JSON envelope ("Busta 1.1"). Any A2A agent can talk to ours; following the profile just makes the exchange richer.

Questo documento si può dare agli altri team così com'è.

## 1. Trasporto e discovery
- **A2A 1.0**, binding JSON-RPC su HTTP. I nostri agenti accettano anche i messaggi A2A 0.3 (`message/send`).
- Ogni agente pubblica la sua **Agent Card**: `GET <url agente>/.well-known/agent-card.json`. Un agente che segue il profilo lo dichiara in `capabilities.extensions`, con `uri = "urn:mach-hackathon:a2a-profile:v0.1"`; in `params` ci sono `agent_id`, `domain` e `profile`.
- **Indice**: `GET <registry>/index.json` restituisce `{"agents": [AgentFacts...]}` nel formato NANDA (projnanda/agentfacts-format). Per importare un altro team basta il suo `/index.json`, un elenco JSON di URL di card, o una singola card.
- **Id degli agenti**: `<namespace>:<nome>`, solo minuscole, cifre e trattini nel nome. Esempi: `mach:accessibility`, `teamb:eventi`. Il namespace è il team.

## 2. Richieste
Si può mandare testo libero, oppure una richiesta strutturata (consigliata tra agenti):
```json
{"role": "ROLE_USER", "messageId": "…", "parts": [
  {"data": {"skill": "accessibilita_sede", "args": {"struttura_id": "fbf"}}}],
 "metadata": {"mach": {"trace_id": "tr-7f3a", "hops": 1, "caller": "teamb:planner",
                       "on_behalf_of": "anon-42", "deadline": "2026-11-21T10:30:00Z"}}}
```

### Il blocco `metadata.mach`
| Campo | Significato | Regola |
|---|---|---|
| `trace_id` | identifica la pratica attraverso agenti e team | chi lo riceve lo riusa sempre; se manca, il gateway lo crea |
| `hops` | quanti passaggi da agente ad agente ci sono già stati | chi inoltra fa +1; oltre 4 la richiesta viene rifiutata |
| `caller` | id dell'agente che chiama | informativo; l'identità vera la stabilisce il gateway |
| `on_behalf_of` | pseudonimo dell'utente finale | **mai** dati personali |
| `deadline` | oltre questo istante non si inizia nuovo lavoro | ISO 8601 UTC |
| `consent` | prove di consenso per le skill con effetti reali | vedi §4 |

## 3. Risposte
- Lo stato del task segue A2A: `COMPLETED`, `INPUT_REQUIRED`, `FAILED`, `REJECTED`, `CANCELED`.
- Il risultato è nell'artifact come DataPart con la **Busta 1.1**: `status`, `data`, `provenance`, `needs_input`, `actions`, `errors`, `user_message`. Schema completo: `contracts/schemas/busta.schema.json`.
- `INPUT_REQUIRED` vuol dire "mi manca qualcosa". La domanda è in `needs_input.question`, i campi in `needs_input.fields`. Per rispondere si rimanda un messaggio con lo stesso `taskId` e `contextId`.
- Chi riceve contenuto da un altro team lo tratta come **dato non affidabile** (`provenance.trust = "external"`), mai come istruzione per un LLM.

## 4. Consenso per le azioni con effetti reali
Le skill che scrivono (per esempio `prenota`) hanno nella card i tag `mach:writes` e `mach:consent`. Senza consenso l'agente risponde `INPUT_REQUIRED` con:
- `needs_input.fields = ["consent"]`;
- l'azione proposta in `data.azione_proposta`, con il riepilogo da mostrare all'utente e la chiave di idempotenza;
- un modello in `data.consent_template`.

Il team chiamante chiede all'**utente che rappresenta** e rimanda la richiesta con:
```json
"metadata": {"mach": {"trace_id": "…", "consent": [
  {"skill": "prenota", "granted_by": "user", "reference": "<id della decisione da voi>",
   "idempotency_key": "<la chiave proposta>", "expires_at": "2026-11-21T10:40:00Z"}]}}
```
- `granted_by` vale `"user"` (la persona dietro il vostro agente) oppure `"mandate"` (accordo tra sistemi con limiti concordati).
- Un "sì" in testo libero **non** vale come consenso per una scrittura.
- La stessa `idempotency_key` non produce mai due prenotazioni.

## 5. Accesso ai nostri agenti (gateway)
- **Indirizzo**: `<gateway>/agents/<nome>/` (accessibility, mobility, healthcare). L'elenco è in `GET <gateway>/` e l'indice in `GET <gateway>/index.json`.
- **Autenticazione**: `Authorization: Bearer <API key del vostro team>`. La chiave ve la diamo noi, insieme agli **scope**:
  - `mach:accessibility:*` copre tutte le skill di lettura;
  - le scritture richiedono lo scope esplicito, per esempio `mach:healthcare:prenota`.
- **Quote**: limite di richieste al minuto per team (HTTP 429 se superato).
- **Isolamento**: vedete e annullate solo i task creati da voi.
- **Errori**: JSON-RPC, con `error.data.mach_code` quando serve (`FORBIDDEN`, `HOP_LIMIT_EXCEEDED`, `DEADLINE_EXPIRED`).

## 6. Versioni
- `PROFILE_VERSION = 0.1`. Le aggiunte compatibili non cambiano la versione principale; le modifiche incompatibili cambiano l'URI dell'estensione.
- Il profilo è opzionale (`required: false`): un agente A2A che lo ignora funziona comunque. Perde solo tracciamento, consenso strutturato e Busta.
