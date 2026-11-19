"""Avvia registry + tre agenti + gateway inbound in locale, ognuno nel suo processo.

    python scripts/run_local.py
    -> registry       http://localhost:8000   (pagina con agenti e messaggi live)
    -> sanità         http://localhost:8001
    -> mobilità       http://localhost:8002
    -> accessibilità  http://localhost:8003
    -> ambiente       http://localhost:8004
    -> gateway        http://localhost:9000   (porta per gli altri team: /agents/<nome>/)

Gateway e agenti condividono MACH_GATEWAY_SECRET (generato qui se non è nel .env):
così gli agenti verificano che i permessi dei team arrivino davvero dal gateway.
Ctrl+C per fermare tutto.
"""
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = os.getenv("REGISTRY_URL", "http://localhost:8000")
PROCS = [
    [sys.executable, "-m", "uvicorn", "registry.server:app", "--port", "8000", "--log-level", "warning"],
    [sys.executable, "-m", "agents.health.agent", "--port", "8001", "--registry", REGISTRY],
    [sys.executable, "-m", "agents.mobility.agent", "--port", "8002", "--registry", REGISTRY],
    [sys.executable, "-m", "agents.accessibility.agent", "--port", "8003", "--registry", REGISTRY],
    [sys.executable, "-m", "agents.environment.agent", "--port", "8004", "--registry", REGISTRY],
    [sys.executable, "-m", "uvicorn", "gateway.server:create_app", "--factory", "--port", "9000",
     "--log-level", "warning"],
]


def main() -> None:
    env = {**os.environ, "REGISTRY_URL": REGISTRY}
    env.setdefault("MACH_GATEWAY_SECRET", secrets.token_hex(16))
    procs = []
    for i, cmd in enumerate(PROCS):
        procs.append(subprocess.Popen(cmd, cwd=ROOT, env=env))
        if i == 0:
            time.sleep(1.0)  # il registry deve essere su prima che gli agenti si registrino
    print("Avviati. Registry: http://localhost:8000  ·  Gateway per gli altri team: http://localhost:9000\n"
          "Ctrl+C per fermare.")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.send_signal(signal.SIGINT)
        for p in procs:
            p.wait(timeout=5)


if __name__ == "__main__":
    main()
