"""Coordinamento e negoziazione tra agenti di team diversi (NANDA Town: layer 8 e 9).

1. contract_net: protocollo "Contract Net" (FIPA, citato nel whitepaper per gli appalti):
   - CFP (call for proposals): la stessa richiesta strutturata {skill, args} va in parallelo
     a tutti gli agenti trovati nella discovery che offrono quella skill (nostri o di altri team);
   - proposte: le risposte (Busta, o qualunque risultato A2A) vengono valutate da una
     funzione di punteggio scelta da chi chiede (None = proposta non accettabile);
   - aggiudicazione: vince il punteggio più alto; agli altri non serve rispondere.
   Funziona con qualunque agente A2A: non serve che gli altri team conoscano il protocollo.

2. alternating_offers: trattativa a offerte alternate (stile Rubinstein, citato nel
   whitepaper per prezzi e pagamenti) tra due strategie con un valore di riserva ciascuna.
   È pura logica: la si usa dentro un agente che tratta, ad esempio, prezzo o orario.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from a2a_lite import A2AClient, A2AError, TaskState
from a2a_lite._http import httpx
from registry.client import DiscoveryClient

from .mach import MachContext


@dataclass
class Proposal:
    agent_id: str
    team: str
    card_url: str
    state: str
    result: dict[str, Any] | None
    text: str
    score: float | None = None
    error: str | None = None


@dataclass
class ContractNetResult:
    winner: Proposal | None
    proposals: list[Proposal] = field(default_factory=list)

    @property
    def ranking(self) -> list[Proposal]:
        valid = [p for p in self.proposals if p.score is not None]
        return sorted(valid, key=lambda p: p.score or 0, reverse=True)


async def contract_net(discovery: DiscoveryClient, *, skill: str, args: dict[str, Any], ctx: MachContext,
                       score: Callable[[Proposal], float | None], http: "httpx.AsyncClient",
                       caller: str = "mach:orchestrator", max_agents: int = 5, timeout_s: float = 15.0,
                       origin: str | None = None, text: str | None = None,
                       on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> ContractNetResult:
    candidates = (await discovery.search(skill, skill=skill, origin=origin))[:max_agents]
    md = ctx.next_hop(caller).to_metadata()

    async def ask(c: dict[str, Any]) -> Proposal:
        p = Proposal(c["id"], c.get("origin", "?"), c["card_url"], "error", None, "")
        try:
            client = A2AClient(c["card_url"], http=http, caller=caller, on_event=on_event)
            task = await asyncio.wait_for(client.send(text, {"skill": skill, "args": args}, metadata=md),
                                          timeout=timeout_s)
            p.state, p.result, p.text = task.status.state.value, task.result_data(), task.result_text()
            if task.status.state == TaskState.COMPLETED:
                p.score = score(p)
        except (asyncio.TimeoutError, A2AError, httpx.HTTPError, ValueError) as exc:
            p.error = f"{exc.__class__.__name__}: {exc}"
        return p

    proposals = list(await asyncio.gather(*(ask(c) for c in candidates)))
    valid = [p for p in proposals if p.score is not None]
    winner = max(valid, key=lambda p: p.score or 0) if valid else None
    if on_event:
        await on_event({"type": "contract_net", "agent": caller, "skill": skill,
                        "text": f"{len(proposals)} proposte, vince {winner.agent_id if winner else 'nessuno'}",
                        "data": {"ranking": [(p.agent_id, p.score) for p in proposals]}})
    return ContractNetResult(winner, proposals)


@dataclass
class Negotiation:
    agreed: bool
    value: float | None
    rounds: int
    history: list[tuple[str, float]]


def alternating_offers(buyer_start: float, buyer_max: float, seller_start: float, seller_min: float, *,
                       concession: float = 0.25, max_rounds: int = 12) -> Negotiation:
    """Compratore e venditore si alternano: ognuno concede una frazione della distanza dalla
    propria riserva (pazienza decrescente, come lo sconto di Rubinstein). Accordo quando
    un'offerta rientra nella riserva dell'altro; nessun accordo se le riserve non si incontrano."""
    buyer, seller = buyer_start, seller_start
    history: list[tuple[str, float]] = []
    for r in range(1, max_rounds + 1):
        history.append(("compratore", round(buyer, 2)))
        if buyer >= seller_min:      # il venditore accetta un'offerta dentro la sua riserva
            return Negotiation(True, round(buyer, 2), r, history)
        history.append(("venditore", round(seller, 2)))
        if seller <= buyer_max:      # il compratore accetta una richiesta dentro la sua riserva
            return Negotiation(True, round(seller, 2), r, history)
        buyer = min(buyer + concession * (buyer_max - buyer), buyer_max)
        seller = max(seller - concession * (seller - seller_min), seller_min)
    return Negotiation(False, None, max_rounds, history)
