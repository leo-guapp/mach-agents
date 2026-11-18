"""Strumenti per testare più agenti nello stesso processo, senza aprire porte.

    transport = RoutedASGITransport({"health.local": health_app, "registry.local": reg_app})
    http = httpx.AsyncClient(transport=transport)
    client = A2AClient("http://health.local", http=http)
"""
from __future__ import annotations

from ._http import httpx


class RoutedASGITransport(httpx.AsyncBaseTransport):
    def __init__(self, apps: dict) -> None:
        self._transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    def add(self, host: str, app) -> None:
        self._transports[host] = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request):
        host = request.url.host
        if host not in self._transports:
            raise httpx.ConnectError(f"Nessuna app registrata per l'host {host}", request=request)
        return await self._transports[host].handle_async_request(request)
