# Script di prova (piano B)

Servono a provare il sistema **senza** il nodo NANDA vero e **senza** altri team reali. Restano pronti come piano B per la gara.

Si lanciano sempre dalla cartella principale della repo, con il sistema già avviato (`python scripts/run_local.py`).

| Script | Cosa fa |
|---|---|
| `fake_milan_node.py` | finto NANDA Index v2 su :8300, al posto del "nodo di Milano" |
| `fake_team_b.py` | un altro team finto: agente A2A :8101, API REST :8102, indice :8103 |
| `demo_milano.py` | prova generale: registrazione, risoluzione firmata, discovery, delega, Ambiente→Mobilità (usa i due script sopra; `--index <URL>` per il nodo vero) |
| `demo_interop.py` | prova con il team finto: indici federati, chiamate via gateway, delega A2A → API → escalation |

Esempio, in tre terminali:

```bash
python scripts/run_local.py
python scripts/prove/fake_milan_node.py
python scripts/prove/fake_team_b.py
python scripts/prove/demo_milano.py      # in un quarto terminale
```
