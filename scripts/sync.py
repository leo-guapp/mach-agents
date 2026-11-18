#!/usr/bin/env python3
"""Scarica le modifiche dei compagni e riassume cosa è cambiato.

Uso (dalla cartella della repo):
    python scripts/sync.py            # git pull + riepilogo delle novità
    python scripts/sync.py --no-pull  # solo riepilogo, senza scaricare
    python scripts/sync.py --righe 800  # mostra più righe di diff (default 300)

Il riepilogo viene stampato e salvato in .ai/AGGIORNAMENTI.md (file locale, non
condiviso). Lo script si ricorda l'ultimo commit visto SU QUESTO COMPUTER
(in .git/ultimo-sync), quindi ogni membro vede le novità dalla SUA ultima
sincronizzazione. Gli assistenti AI leggono il riepilogo invece di
riscansionare tutto il codice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys


def git(*args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.rstrip("\n")


def esiste_commit(sha: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True
    ).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-pull", action="store_true", help="non eseguire git pull")
    ap.add_argument("--righe", type=int, default=300, help="righe massime di diff del codice")
    a = ap.parse_args()
    try:  # evita errori con accenti/simboli sui terminali Windows
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    try:
        root = pathlib.Path(git("rev-parse", "--show-toplevel"))
    except RuntimeError:
        print("Errore: lancia lo script dentro la cartella della repo.")
        return 1
    marker = pathlib.Path(git("rev-parse", "--git-path", "ultimo-sync")).resolve()
    out_dir = root / ".ai"
    out_file = out_dir / "AGGIORNAMENTI.md"

    vecchio = marker.read_text().strip() if marker.exists() else ""
    if vecchio and not esiste_commit(vecchio):
        vecchio = ""

    if not a.no_pull:
        ramo = git("branch", "--show-current")
        print(f"Scarico le novità (ramo: {ramo or 'detached'})...")
        try:
            git("pull", "--rebase", "--autostash")
        except RuntimeError as e:
            print("\n!! git pull non è riuscito. Probabile conflitto o rete assente.")
            print(e)
            print("\nCosa fare: `git status` per vedere i file in conflitto; risolvili,"
                  " poi `git add <file>` e `git rebase --continue`."
                  " Se non sai come procedere: `git rebase --abort` e chiedi al team.")
            return 1

    nuovo = git("rev-parse", "HEAD")
    if vecchio == nuovo:
        print("Nessuna novità dall'ultima sincronizzazione.")
        if out_file.exists():
            print(f"L'ultimo riepilogo resta in {out_file.relative_to(root)}")
        return 0

    io = git("config", "user.email", check=False).lower()
    fmt = "%h|%ae|%an|%ar|%s"
    if vecchio:
        intervallo = f"{vecchio}..{nuovo}"
        log = git("log", "--no-merges", f"--format={fmt}", intervallo)
        titolo = f"Novità da {vecchio[:7]} a {nuovo[:7]}"
    else:
        log = git("log", "--no-merges", "-n", "15", f"--format={fmt}")
        titolo = "Prima sincronizzazione su questo computer: ultimi 15 commit"

    righe_log = []
    for riga in filter(None, log.splitlines()):
        sha, email, nome, quando, msg = riga.split("|", 4)
        tu = " (tu)" if io and email.lower() == io else ""
        righe_log.append(f"- `{sha}` {nome}{tu}, {quando}: {msg}")
        corpo = git("log", "-1", "--format=%b", sha).strip()
        for r in corpo.splitlines():
            if r.strip().upper().startswith(("IMPATTO", "PERCHÉ", "PERCHE")):
                righe_log.append(f"    {r.strip()}")

    parti = [f"# {titolo}", f"_Generato il {dt.datetime.now():%Y-%m-%d %H:%M}_", ""]
    parti += ["## Commit", *(righe_log or ["- (nessun commit nuovo)"]), ""]

    if vecchio:
        stat = git("diff", "--stat=100", vecchio, nuovo)
        parti += ["## File modificati", "```", stat or "(nessuno)", "```", ""]

        stato = git("diff", "-U0", vecchio, nuovo, "--", "STATO.md")
        aggiunte = [l[1:] for l in stato.splitlines()
                    if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
        if aggiunte:
            parti += ["## Righe nuove in STATO.md", *aggiunte, ""]

        diff = git("diff", vecchio, nuovo, "--", ".", ":(exclude)STATO.md")
        righe = diff.splitlines()
        parti += [f"## Diff del codice (prime {a.righe} righe su {len(righe)})", "```diff",
                  *righe[: a.righe], "```"]
        if len(righe) > a.righe:
            parti.append(f"Diff troncato. Per un file: `git diff {vecchio[:7]} HEAD -- <file>`")

    testo = "\n".join(parti) + "\n"
    out_dir.mkdir(exist_ok=True)
    out_file.write_text(testo, encoding="utf-8")
    marker.write_text(nuovo + "\n")
    print(testo)
    print(f"(Riepilogo salvato in {out_file.relative_to(root)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
