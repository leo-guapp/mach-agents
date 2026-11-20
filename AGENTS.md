# Istruzioni per gli assistenti AI (Claude, Gemini, Codex, Cursor, Copilot…)

Queste regole valgono per **tutti** gli strumenti AI che lavorano su questa repo.
`CLAUDE.md` e `GEMINI.md` rimandano qui. Modifica questo file solo se il team è d'accordo.

## Il progetto in breve

Sistema multi-agente per il Milano Agentic City Hackathon (MACH, 20-21 novembre 2026).
Un orchestratore e diversi agenti di servizio (accessibilità, mobilità, sanità) comunicano
via **A2A** (v1.0, compatibile con la 0.3) e si trovano tramite un **registry in stile NANDA**.
Linguaggio: Python. LLM previsto: Gemma 4 tramite API compatibile OpenAI.

| Cartella | Contenuto |
|---|---|
| `a2a_lite/` | implementazione A2A: server, client, tipi, errori |
| `agents/` | `BaseAgent`, tool, client LLM, i 4 agenti di servizio (sanità, mobilità, accessibilità, ambiente), export MCP |
| `registry/` | registry federato in stile NANDA (server, client, AgentFacts) |
| `contracts/` | formato delle risposte (Busta 1.1): modelli, JSON Schema, esempi |
| `gateway/` | porta d'ingresso per gli altri team: API key, scope, quote, AI Catalog, firme |
| `nanda/` | stack NANDA: registrazione sull'Index, AI Catalog, firme Ed25519, nodo finto |
| `interop/` | profilo MACH, gate dati, chiamate verso altri team, negoziazione, bridge testo→testo |
| `scripts/` | `run_local.py` (avvia tutto), `sync.py`, `demo_scenario.py`, registrazione NANDA |
| `scripts/prove/` | piano B: nodo NANDA finto, team finto e relative demo |
| `tests/` | test pytest (79, senza rete né LLM) |
| `docs/` | playbook, guida ai concetti, architettura, profilo MACH, guida git |

Chi è responsabile di cosa e cosa è in corso: vedi `STATO.md`.

## 1. All'inizio di ogni sessione (obbligatorio)

1. Esegui `python scripts/sync.py`. Lo script scarica le modifiche dei compagni e scrive in
   `.ai/AGGIORNAMENTI.md` cosa è cambiato dall'ultima sincronizzazione su questo computer:
   commit, file toccati, righe di impatto e diff.
2. Leggi `.ai/AGGIORNAMENTI.md` e `STATO.md`.
3. **Non rileggere tutta la codebase.** Apri solo i file che servono al compito e, tra quelli
   del riepilogo, quelli che lo riguardano. Per vedere la storia di un file:
   `git log -p -3 -- <file>`.

Se `sync.py` segnala un conflitto, fermati e chiedi all'utente.

## 2. Mentre lavori

- Se sei su `main` e il compito non è banale, crea un ramo `nome/argomento`
  (es. `leo/registry-search`).
- Modifica solo i file necessari. Prima di toccare un'area di cui è responsabile un'altra
  persona (vedi `STATO.md`), avvisa l'utente.
- **Interfacce condivise**: formato `Esito`, DataPart `{"skill", "args"}`, metodi di
  `registry/client.py`, firme di `BaseAgent` e `@skill`. Non cambiarle senza dirlo
  all'utente. Se le cambi, scrivilo nella riga IMPATTO del commit.
- Non committare mai `.env`, chiavi API o token.
- Prima del commit, esegui `pytest -q` se ci sono test per l'area che hai toccato.

## 3. Alla fine di ogni compito (obbligatorio)

1. Aggiungi **una riga** in cima a "Registro modifiche" in `STATO.md`:
   `- AAAA-MM-GG HH:MM · nome · area · cosa è cambiato, in parole semplici · IMPATTO: … (o "nessuno")`.
   Se serve, aggiorna anche "In corso".
2. Fai un commit piccolo, con questo formato:

   ```
   tipo(area): riassunto in massimo 70 caratteri

   Perché: motivo della modifica
   IMPATTO: chi o cosa deve adattarsi (oppure "nessuno")
   ```

   Tipi: `feat` (novità), `fix` (correzione), `refactor`, `docs`, `test`, `chore`.
3. Esegui `git pull --rebase`, poi `git push`. Se lavori su un ramo, fai il push del ramo e
   apri una Pull Request (`gh pr create --fill`), se l'utente è d'accordo.
4. Riassumi all'utente cosa hai fatto, in 3 righe al massimo.
