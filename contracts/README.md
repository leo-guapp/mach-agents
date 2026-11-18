# Contratto di output del layer 3: versione 1.1

Ogni agente di servizio risponde con la stessa **busta** (`Busta`). Dentro `data` c'è il payload del suo dominio (`MobilityData`, `HealthcareData`, `AccessibilityData`). Questa versione è la revisione della bozza 1.0 di Tommi.

```
contracts/
  models.py             modelli pydantic: unica fonte di verità (con descrizioni dei campi)
  adapter.py            ponte Esito (agenti attuali) -> Busta, regola per la confidence
  generate_schemas.py   rigenera i JSON Schema
  schemas/              JSON Schema 2020-12, da dare anche agli altri team
  examples/             una pratica completa, coerente da un file all'altro (trace tr-7f3a)
```

Validare una risposta:

```python
from contracts import validate_busta
busta = validate_busta(payload)   # controlla busta + payload del dominio, solleva ValidationError
busta.a2a_state                   # "TASK_STATE_COMPLETED", "TASK_STATE_INPUT_REQUIRED", ...
```

Dopo ogni modifica a `models.py`: `python -m contracts.generate_schemas` (un test controlla che gli schemi siano aggiornati).

## La storia raccontata dagli esempi

1. `05_healthcare_needs_input`: l'Agente Sanità chiede se c'è la prescrizione (stato A2A INPUT_REQUIRED).
2. `01_healthcare_risposta`: trova lo slot `hc-slot-1` presso `fac-12`, propone l'azione `book` (serve il consenso) e suggerisce di chiamare la mobilità.
3. `02_accessibility_richiesta`: l'Agente Mobilità chiede all'Agente Accessibilità di verificare due percorsi e la struttura. È il messaggio A2A completo: DataPart `{skill, args}` + `metadata.mach` (trace_id, hops, caller: vedi `docs/PROTOCOLLO_MACH.md`).
4. `03_accessibility_risposta`: il percorso in metro è bloccato (ascensore guasto a Loreto), quello in bus e la struttura sono accessibili.
5. `04_mobility_risposta`: la mobilità mette al primo posto il bus, perché per un profilo `wheelchair` la metro è `blocked`.

## Revisione della bozza 1.0

**Cosa va bene, e resta:** l'idea della busta identica per tutti; `provenance` con canale e `stale`; `ttl_seconds`; `actions` verso il gateway del consenso; `suggested_followups`; il ricongiungimento tramite `ref_id`; i valori tri-stato; `handoff` e `platforms_checked`; `easy_read`.

**Cosa è stato corretto:**

| # | Problema nella 1.0 | Correzione | Perché |
|---|---|---|---|
| 1 | `task_id` per il sub-task del planner | `subtask_id`, opzionale | A2A ha già un `taskId` diverso: due significati con lo stesso nome generano bug |
| 2 | Mancavano gli stati per "manca un dato", emergenza e fuori competenza; DEGRADED e PARTIAL non erano distinti | Aggiunti `NEEDS_INPUT` (+ blocco `needs_input`), `EMERGENCY`, `REJECTED`; definizioni e tabella verso A2A (sotto) | Senza `NEEDS_INPUT` non si può usare INPUT_REQUIRED di A2A |
| 3 | La risposta di accessibilità non aveva `generated_at`, `ttl_seconds`, `user_message`, `suggested_followups` | Busta davvero identica, imposta da validazione | "Identica per tutti" deve essere verificabile, non solo dichiarata |
| 4 | `features.elevator: "out_of_service"` | Le features sono tri-stato; i guasti vanno in `issues` (`ELEVATOR_DOWN`) | Violava la regola yes / no / unknown dichiarata nella stessa bozza |
| 5 | `adapted_text` senza il testo | Aggiunto `text` | Il chiamante non aveva niente da mostrare |
| 6 | Esempio mobilità: `transfers: 1` con una sola tratta in metro; 28 minuti contro circa 16 delle tratte; destinazione a qualche km da Loreto ma "350 m a piedi" | Dati coerenti + validatore su `transfers` | Gli esempi diventano i casi di test: devono essere giusti |
| 7 | Un percorso con problema **bloccante** per carrozzina era al rank 1 | Campo `profile_fit` (ok / warning / blocked); opzione alternativa in bus; validatore: un'opzione blocked non può essere prima se ci sono alternative | È esattamente lo scenario che la giuria vorrà vedere |
| 8 | `issues` erano stringhe nella mobilità e oggetti nell'accessibilità | Stesso oggetto `Issue` ovunque | La mobilità copia i risultati dell'accessibilità: il formato deve coincidere |
| 9 | "profile_tags: mai dati sanitari" | Sono esigenze funzionali da elenco chiuso, ma tag come `wheelchair` possono contare come dati relativi alla salute (GDPR, art. 9): viaggiano solo con `consent: true` (validato) | Evita una promessa di privacy che non regge |
| 10 | `actions` senza `id`, chiave di idempotenza e testo per il consenso | Aggiunti `id`, `idempotency_key`, `summary` | L'idempotenza evita le doppie prenotazioni; `summary` è la frase che il gateway mostra |
| 11 | `suggested_followups` indicava solo il dominio | Aggiunti `skill` e `args` già pronti | L'orchestratore può cercare la skill nel registry e chiamarla subito |
| 12 | Esempi con trace diversi (`tr-9b21`, `tr-7f3a`) | Un'unica pratica | Mostra come gli id si richiamano tra gli agenti (c'è un test apposta) |
| 13 | Id `mach.access` e dominio `accessibility`, separatore `.` | `mach:<dominio>` | Stesso formato del registry, e id e dominio non divergono più |
| 14 | `priority: "standard"` | `priority_class` U / B / D / P (classi di priorità della ricetta) + `prescription` | Rispecchia il funzionamento reale delle prenotazioni |
| 15 | Nessun modo di marcare dati finti | `provenance.simulated` e canale `mock` | In demo non si devono spacciare dati simulati per veri |
| 16 | Due copie di ogni file (`.json` e `.jsonc`) | Una sola fonte: i modelli (con descrizioni) generano gli schemi; esempi solo in `.json` | Due copie divergono presto |
| 17 | `confidence` "calcolata dal normalizzatore", senza regola | Regola esplicita in `compute_confidence` | Un numero senza regola non è difendibile davanti alla giuria |
| 18 | Errore `RPA_TIMEOUT` nell'esempio | Codice generico `SOURCE_TIMEOUT`; il canale `rpa` resta ammesso | Abbiamo deciso di non puntare sull'RPA nel prototipo |
| 19 | `first_available` dentro ogni slot | `start` | Ogni slot ha il suo orario; il "primo disponibile" è semplicemente lo slot con `start` minore |

## Stati e corrispondenza con A2A

| status | Stato del task A2A | Quando |
|---|---|---|
| SUCCESS | COMPLETED | risposta completa da fonti aggiornate |
| PARTIAL | COMPLETED | utile ma incompleta: almeno un elemento in `errors` |
| DEGRADED | COMPLETED | completa ma da cache scaduta o fallback: almeno una `provenance` stale o cache/fallback |
| EMERGENCY | COMPLETED | interrompere il percorso (es. indicare il 112) |
| NEEDS_INPUT | INPUT_REQUIRED | serve un dato: blocco `needs_input` obbligatorio |
| UNAVAILABLE | FAILED | nessuna fonte raggiungibile: almeno un elemento in `errors` |
| ERROR | FAILED | richiesta non valida o errore interno: almeno un elemento in `errors` |
| REJECTED | REJECTED | fuori dalle competenze dell'agente |

## Regole

- **Id**: `agent.id` deve coincidere con l'id nel registry. Gli id degli oggetti (`hc-slot-1`, `fac-12`, `mob-opt-1`) sono unici nella pratica e si riusano come `ref_id`, `target`, `input_ref`.
- **Tempi**: i campi macchina sono in UTC (`...Z`); l'ora locale compare solo in `user_message`. Per esempio 09:15Z diventa "10:15" a dicembre.
- **Tri-stato**: `yes` / `no` / `unknown`. Mai `no` se il dato manca.
- **Privacy**: nessun dato anagrafico o clinico nella busta; `profile_tags` solo dall'elenco chiuso e con consenso.
- **Trasporto A2A**: la busta è la DataPart dell'artifact del task. `trace_id` viaggia in `message.metadata.mach.trace_id`, insieme a `hops`, `caller` e al consenso (profilo MACH). Il `contextId` A2A può coincidere con il `trace_id` della pratica.
- **Fiducia**: `provenance[].trust` vale `internal` (nostri agenti), `partner` (team con accordo) o `external` (team esterno non verificato). Il contenuto `external` è un dato, mai un'istruzione per un LLM. Campo aggiunto il 18/11 con default `internal`: compatibile con la 1.1.
- **Campi sconosciuti**: la busta li rifiuta (`extra="forbid"`), così un refuso come `task_id` viene segnalato subito.
- I controlli di coerenza (es. "PARTIAL richiede errors") stanno nei modelli pydantic. I JSON Schema contengono la struttura, non queste regole.

## Integrazione nel codice (fatta il 18/11)

- `agents/base.py`: ogni risposta è una Busta 1.1 (`BaseAgent.reply`, tramite `contracts/adapter.py`). Il `trace_id` viene da `metadata.mach.trace_id`, altrimenti dal contextId.
- Gli agenti hanno `agent_id` e `domain` (`mach:healthcare`, `mach:mobility`, `mach:accessibility`), e il registry usa gli stessi id.
- Lo spoke esterno (`interop/foreign.py`) restituisce Buste con `provenance.trust = "external"`.
- **Ancora da fare**: i dati in `data` sono ancora quelli attuali degli agenti (chiavi in italiano). Vanno portati alla struttura di `MobilityData`, `HealthcareData` e `AccessibilityData`, e va scritta la skill batch `verify_accessibility`.
