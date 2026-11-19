"""Compatibilità NANDA (nodo di Milano), Agente Ambiente, negoziazione, MCP e ponte testo.

Ecosistema simulato: nostro registry + 4 agenti + gateway (AI Catalog, firme);
finto NANDA Index v2; Team B (agente A2A registrato sull'Index come card diretta);
Team C (un concorrente che offre anche lui la skill "percorso").
"""
import json

import pytest
import yaml

from a2a_lite import A2AClient, A2AServer, AgentCard, AgentInterface, AgentSkill, Reply, TaskContext, TaskState
from a2a_lite._http import httpx
from a2a_lite.testing import RoutedASGITransport
from agents.accessibility.agent import AccessibilityAgent
from agents.base import build_app
from agents.environment.agent import EnvironmentAgent
from agents.health.agent import HealthAgent
from agents.mcp_export import to_mcp
from agents.mobility.agent import MobilityAgent
from gateway import Gateway, TeamConfig, UpstreamAgent
from interop.foreign import ForeignAgentAdapter
from interop.mach import MachContext
from interop.nanda_bridge import text_handler
from interop.negotiation import alternating_offers, contract_net
from nanda.catalog import A2A_CARD_MEDIA_TYPE, AI_CATALOG_MEDIA_TYPE
from nanda.index_client import NandaIndexClient, NandaIndexSource
from nanda.local_index import LocalNandaIndex
from nanda.signing import (SigningKey, did_web, public_key_from_multibase, verify_agentaddr,
                           verify_agentfacts_vc)
from nanda.town import town_record, write_records
from registry import DiscoveryClient, Registry, RegistryClient

DOMAIN = "team-mach.example"


def simple_agent(name: str, skill_id: str, tags: list[str], host: str, reply) -> A2AServer:
    card = AgentCard(name=name, description=name, supported_interfaces=[AgentInterface(url=f"http://{host}/")],
                     skills=[AgentSkill(id=skill_id, name=skill_id, description=name, tags=tags)])

    async def handler(ctx: TaskContext) -> Reply:
        return reply(ctx)
    return A2AServer(card, handler)


@pytest.fixture
async def eco():
    transport = RoutedASGITransport({})
    http = httpx.AsyncClient(transport=transport)
    reg = Registry(http=http)
    transport.add("registry.local", reg.app())
    rc = RegistryClient("http://registry.local", http=http)
    agents = {"health": HealthAgent(llm=None, http=http), "mobility": MobilityAgent(llm=None, http=http),
              "access": AccessibilityAgent(llm=None, http=http), "env": EnvironmentAgent(llm=None, http=http)}
    for host, agent in agents.items():
        base = f"http://{host}.local"
        transport.add(f"{host}.local", build_app(agent, base, "http://registry.local"))
        await rc.register(agent.card(base), base + "/.well-known/agent-card.json", agent_id=agent.agent_id)
    key = SigningKey.load_or_create(None, did_web("gw.local"))
    gw = Gateway("http://gw.local", [
        UpstreamAgent("accessibility", "http://access.local", "mach:accessibility"),
        UpstreamAgent("mobility", "http://mobility.local", "mach:mobility"),
        UpstreamAgent("healthcare", "http://health.local", "mach:healthcare"),
        UpstreamAgent("environment", "http://env.local", "mach:environment"),
    ], [TeamConfig("team-b", "k-b", ["mach:accessibility:*", "mach:environment:*"])],
        http=http, publisher_domain=DOMAIN, publisher_name="Team MACH", signing_key=key)
    transport.add("gw.local", gw.app())
    idx = LocalNandaIndex()
    transport.add("index.local", idx.app())
    transport.add("teamb.local", simple_agent(
        "Eventi Team B", "eventi_accessibili", ["eventi", "cultura"], "teamb.local",
        lambda ctx: Reply(text="Concerto accessibile sabato", data={"eventi": ["e1"]})).app())
    transport.add("teamc.local", simple_agent(
        "Percorsi Team C", "percorso", ["mobilita", "percorso"], "teamc.local",
        lambda ctx: Reply(text="In bus 25 minuti", data={"data": {"minuti_stimati": 25, "senza_barriere": True}})).app())
    yield {"http": http, "rc": rc, "reg": reg, "gw": gw, "key": key, "idx": idx, **agents}


# ============================================================================ AI Catalog sul gateway

async def test_gateway_serves_ai_catalog(eco):
    doc = (await eco["http"].get("http://gw.local/.well-known/ai-catalog.json")).json()
    assert doc["specVersion"] == "1.0" and doc["publisher"]["identifier"] == DOMAIN
    ids = {e["identifier"] for e in doc["entries"]}
    assert f"urn:air:{DOMAIN}:agent:accessibility" in ids and len(ids) == 4
    e = next(e for e in doc["entries"] if e["identifier"].endswith(":mobility"))
    assert e["mediaType"] == A2A_CARD_MEDIA_TYPE and e["url"].endswith("/agents/mobility/.well-known/agent-card.json")
    assert "segnala_evento" not in e["tags"]                                  # skill private escluse
    assert e["extensions"]["org.projectnanda"]["status"] == "active"
    one = (await eco["http"].get(f"http://gw.local/agents/urn:air:{DOMAIN}:agent:mobility")).json()
    assert one["identifier"] == e["identifier"]                               # lookup con URN completo
    found = (await eco["http"].get("http://gw.local/agents/search", params={"q": "co2 emissioni"})).json()
    assert found["entries"][0]["identifier"].endswith(":environment")


# ============================================================================ firme (whitepaper §3.1)

async def test_signed_agentfacts_and_agentaddr(eco):
    did = (await eco["http"].get("http://gw.local/.well-known/did.json")).json()
    pub = public_key_from_multibase(did["verificationMethod"][0]["publicKeyMultibase"])
    vc = (await eco["http"].get("http://gw.local/agents/accessibility/agentfacts.json")).json()
    assert vc["type"] == ["VerifiableCredential", "AgentFacts"] and vc["proof"]["cryptosuite"] == "eddsa-jcs-2022"
    facts = verify_agentfacts_vc(vc, pub)
    assert facts["id"] == "mach:accessibility" and "trust" not in facts
    vc["credentialSubject"]["endpoints"]["static"] = ["http://attaccante.local/"]   # manomissione
    with pytest.raises(ValueError, match="non valida"):
        verify_agentfacts_vc(vc, pub)
    addr = (await eco["http"].get("http://gw.local/agents/accessibility/agentaddr.json")).json()
    rec = verify_agentaddr(addr, pub)
    assert rec["f"] == "http://gw.local/f/accessibility" and addr["size_bytes"] <= 120  # whitepaper: < 120 B
    vc2 = (await eco["http"].get(rec["f"])).json()                                       # il puntatore funziona
    assert verify_agentfacts_vc(vc2, pub)["id"] == "mach:accessibility"
    with pytest.raises(ValueError, match="scaduto"):
        verify_agentaddr(addr, pub, now=rec["iat"] + rec["t"] + 1)


# ============================================================================ NANDA Index v2

async def register(idx_client, org_id, registry_url, media_type, domain=None, tags=()):
    await idx_client.register_account(f"{org_id}@test.local", "pw")
    return await idx_client.register_org({"org_id": org_id, "display_name": org_id, "domain": domain,
                                          "registry_url": registry_url, "media_type": media_type,
                                          "description": f"agenti di {org_id}", "tags": list(tags)})


async def test_register_and_resolve_through_index_catalog_card(eco):
    idx = NandaIndexClient("http://index.local", http=eco["http"])
    rec = await register(idx, "team-mach", "http://gw.local", AI_CATALOG_MEDIA_TYPE, DOMAIN, ["accessibilita"])
    assert rec["status"] == "active" and rec["identifier"] == f"urn:ai:domain:{DOMAIN}"
    res = await idx.resolve_agent(f"urn:ai:domain:{DOMAIN}:agent:accessibility")
    assert res.catalog_entry["identifier"] == f"urn:air:{DOMAIN}:agent:accessibility"
    assert res.runtime_url == "http://gw.local/agents/accessibility/"
    # e l'agente risolto risponde davvero, tramite il gateway
    t = await A2AClient(res.card_url, http=eco["http"], auth_token="k-b").send(
        data={"skill": "accessibilita_sede", "args": {"struttura_id": "fbf"}})
    assert t.status.state == TaskState.COMPLETED


async def test_discovery_on_milan_node_finds_other_teams_not_us(eco):
    idx = NandaIndexClient("http://index.local", http=eco["http"])
    await register(idx, "team-mach", "http://gw.local", AI_CATALOG_MEDIA_TYPE, DOMAIN, ["eventi"])
    await register(NandaIndexClient("http://index.local", http=eco["http"]), "team-b",
                   "http://teamb.local/.well-known/agent-card.json", A2A_CARD_MEDIA_TYPE, tags=["eventi", "cultura"])
    source = NandaIndexSource(NandaIndexClient("http://index.local", http=eco["http"]),
                              exclude_registry_urls={"http://gw.local"})
    disc = DiscoveryClient([eco["rc"], source])
    found = await disc.search("eventi", origin="external")
    assert found and found[0]["origin"] == "team-b" and found[0]["source"] == "nanda-index"
    assert all("gw.local" not in f["card_url"] for f in found)                # mai noi stessi
    res = await ForeignAgentAdapter(disc, http=eco["http"], poll_s=0.01).delegate(
        ctx=MachContext(trace_id="tr-milano"), q="eventi", data={"zona": "loreto"})
    assert res.channel == "a2a" and res.busta.provenance[0].trust == "external"


# ============================================================================ Agente Ambiente

async def test_environment_asks_mobility_and_compares(eco):
    t = await A2AClient("http://env.local", http=eco["http"]).send(
        data={"skill": "confronta_mezzi", "args": {"origine": "Loreto", "destinazione": "Turati"}},
        metadata=MachContext(trace_id="tr-env").to_metadata())
    d = t.result_data()
    assert d["status"] == "SUCCESS" and d["trace_id"] == "tr-env"
    mezzi = {o["mezzo"] for o in d["data"]["opzioni"]}
    assert {"metro", "auto"} <= mezzi and d["data"]["consigliato"] != "auto"
    assert d["data"]["risparmio_vs_auto_g"] > 0
    calls = [e for e in eco["reg"].events if e["type"] == "call" and e.get("from") == "mach:environment"]
    assert calls  # l'Agente Ambiente ha chiamato l'Agente Mobilità via A2A


async def test_environment_free_text(eco):
    r = await eco["env"].invoke(text="Quanta CO2 emetto facendo 10 km in auto?")
    assert r.busta["data"]["co2_g"] == 1700
    r = await eco["env"].invoke(text="Budget di 20 kg di CO2, già emessi 15 kg")
    assert r.busta["data"]["stato"] == "attenzione"


# ============================================================================ negoziazione

async def test_contract_net_picks_best_route_across_teams(eco):
    await eco["rc"].import_card("http://teamc.local/.well-known/agent-card.json", "team-c")

    def score(p):
        d = (p.result or {}).get("data") or {}
        return None if d.get("minuti_stimati") is None else 100 - d["minuti_stimati"]

    res = await contract_net(DiscoveryClient([eco["rc"]]), skill="percorso",
                             args={"origine": "Loreto", "destinazione": "Turati"},
                             ctx=MachContext(trace_id="tr-cnp"), score=score, http=eco["http"])
    teams = {p.team for p in res.proposals}
    assert {"local", "teamc"} <= teams or {"local", "team-c"} <= teams
    assert res.winner is not None and res.winner.score == max(p.score for p in res.ranking)


def test_alternating_offers():
    ok = alternating_offers(buyer_start=20, buyer_max=35, seller_start=50, seller_min=30)
    assert ok.agreed and 30 <= ok.value <= 35
    ko = alternating_offers(buyer_start=10, buyer_max=20, seller_start=50, seller_min=30)
    assert not ko.agreed


# ============================================================================ portabilità: MCP e ponte testo

async def test_mcp_export_only_read_skills():
    pytest.importorskip("mcp.server.mcpserver")
    server = to_mcp(HealthAgent(llm=None))
    names = {t.name for t in await server.list_tools()}
    assert "orienta" in names and "prenota" not in names   # niente scritture senza canale per il consenso
    out = await server.call_tool("orienta", {"bisogno": "ho mal di schiena"})
    assert json.loads(out.content[0].text)["status"] == "SUCCESS"


async def test_text_bridge_handles_follow_up_questions():
    handle = text_handler(HealthAgent(llm=None))
    first = await handle("Cerca una visita ortopedica")
    assert "prescrizione" in first
    second = await handle("sì")                              # continua lo stesso task
    assert "Disponibilità" in second
    busta = json.loads(await handle(json.dumps({"skill": "orienta", "args": {"bisogno": "ricetta da prenotare"}})))
    assert busta["agent"]["id"] == "mach:healthcare"


def test_nandatown_records(tmp_path):
    rec = town_record("MACH Agente Accessibilità", "Barriere e accessibilità delle sedi",
                      "https://gw.example/agents/accessibility/.well-known/agent-card.json", "leo-guapp",
                      ["accessibilita", "mach:writes"])
    [p] = write_records([rec], tmp_path)
    data = yaml.safe_load(p.read_text())
    assert data["kind"] == "agent" and data["consent"]["probes"] is True
    assert data["entry"]["a2a_card_url"].startswith("https://") and "mach:writes" not in data["tags"]
    assert p.name == "mach-agente-accessibilit.yaml" or p.name.startswith("mach-agente-accessibilit")
