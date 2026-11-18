# Guida rapida a Git e GitHub per il team

## Le idee in 1 minuto

- **Git** è un programma sul tuo computer. Registra la storia del progetto come una serie di
  "fotografie" (i **commit**). Ogni commit ha un autore, un messaggio e l'elenco esatto delle
  righe cambiate.
- **GitHub** è il sito che ospita la copia centrale (**remote**, chiamato `origin`). Ognuno
  lavora sulla sua copia locale e poi la sincronizza: `push` per mandare, `pull` per ricevere.
- Un **ramo** (branch) è una linea di lavoro parallela. `main` è quella ufficiale.
  Una **Pull Request (PR)** è la richiesta di unire un ramo a `main`, con la possibilità di
  rivedere le modifiche prima.
- Un **conflitto** nasce quando due persone modificano le stesse righe. Git chiede a un umano
  di scegliere quale versione tenere.

```
  il tuo PC                       GitHub                       PC di un compagno
 [file] --add--> [commit] --push--> [origin/main] --pull--> [commit] --> [file]
```

## Setup (una volta per persona, circa 15 minuti)

1. Crea un account su https://github.com e manda il tuo username a chi gestisce la repo.
2. Installa Git: https://git-scm.com/downloads (su Windows tieni le opzioni predefinite).
3. Installa GitHub CLI: https://cli.github.com, poi esegui `gh auth login`
   (scegli GitHub.com → HTTPS → login con il browser).
4. Configura il tuo nome, che comparirà nei commit:
   ```
   git config --global user.name "Nome Cognome"
   git config --global user.email "la-mail-dell-account-github"
   ```
5. Accetta l'invito alla repo: arriva per e-mail, oppure lo trovi su github.com/notifications.
6. Clona la repo **in una cartella normale, non dentro Google Drive, OneDrive o iCloud**:
   ```
   cd ~/progetti
   git clone https://github.com/<OWNER>/mach-agents.git
   cd mach-agents
   python scripts/sync.py
   ```

## Il ciclo di ogni giorno

```
python scripts/sync.py                 # 1. scarica le novità e leggi il riepilogo
git switch -c tuonome/argomento        # 2. (consigliato) crea un ramo per il compito
... lavori, da solo o con l'AI ...
git add -A                             # 3. prepara le modifiche
git commit -m "feat(registry): ricerca per tag"   #    e registrale
git pull --rebase origin main          # 4. prendi le novità arrivate nel frattempo
git push -u origin tuonome/argomento   # 5. invia
gh pr create --fill                    # 6. apri la PR; un compagno la guarda e fa "Merge"
git switch main && python scripts/sync.py   # 7. torna su main aggiornato
```

Durante l'hackathon, per modifiche piccole nella **tua** area, puoi saltare il ramo:
lavori su `main` e fai `git pull --rebase`, poi `git push`.
Regola d'oro: **commit piccoli e frequenti, pull spesso.** Meno tempo passa tra un pull e
l'altro, meno conflitti avrai.

## Comandi utili

| Voglio… | Comando |
|---|---|
| vedere cosa ho cambiato e non ho ancora committato | `git status`, `git diff` |
| vedere la storia | `git log --oneline -15` |
| vedere chi ha cambiato un file e come | `git log -p -3 -- percorso/file.py` |
| annullare le modifiche non salvate a un file | `git restore percorso/file.py` |
| mettere da parte il lavoro per un attimo | `git stash`, poi `git stash pop` |
| cambiare ramo | `git switch nome-ramo` |

## Se c'è un conflitto

1. `git status` mostra i file "both modified".
2. Apri il file: trovi i blocchi `<<<<<<<`, `=======`, `>>>>>>>`. Tieni la versione giusta,
   o un mix delle due, e cancella i marcatori. VS Code ha i pulsanti "Accept…" che lo fanno
   per te.
3. `git add <file>` e poi `git rebase --continue`. Se sei nel panico: `git rebase --abort`
   riporta tutto com'era prima del pull.

## Come lavorano gli strumenti AI su questa repo

Gli agenti da terminale o da editor (Claude Code, Gemini CLI, Codex, Cursor, Copilot) aperti
nella cartella della repo leggono da soli `CLAUDE.md`, `GEMINI.md` o `AGENTS.md`. Lì c'è
scritto di lanciare `scripts/sync.py` all'inizio e di aggiornare `STATO.md` e fare il commit
alla fine. Così nessuno deve riscansionare tutto: ognuno legge solo il riepilogo delle novità.
Le chat web (claude.ai, gemini.google.com) non possono modificare i file della repo da sole.
Per quello servono gli agenti qui sopra.
