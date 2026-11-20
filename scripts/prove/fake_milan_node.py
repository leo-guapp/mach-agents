"""Avvia un finto "nodo di Milano" (NANDA Index compatibile v2) su http://localhost:8300.

Serve a provare registrazione, ricerca e risoluzione prima della gara, e come piano B.
Vedi nanda/local_index.py per le differenze rispetto al nodo vero.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402

from nanda.local_index import LocalNandaIndex  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8300
    print(f"Finto nodo di Milano (NANDA Index v2 compatibile) su http://localhost:{port}")
    uvicorn.run(LocalNandaIndex().app(), port=port, log_level="warning")
