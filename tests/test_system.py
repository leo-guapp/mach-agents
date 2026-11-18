"""Test end-to-end: registry + tre agenti nello stesso processo, comunicazione via A2A."""
import pytest

from a2a_lite import A2AClient, TaskState
from a2a_lite._http import httpx
from a2a_lite.testing import RoutedASGITransport
from agents.accessibility.agent import AccessibilityAgent
from agents.base import build_app
from agents.health.agent import HealthAgent
from agents.llm import ScriptedLLM, run_tool_loop
from agents.mobility.agent import MobilityAgent
from agents.tools import ToolRegistry
from contracts import validate_busta
from interop.mach import Consent, MachContext
from registry import Registry, RegistryClient


@pytest.fixture
async def city():
    reg = Registry()
    transport = RoutedASGITransport({"registry.local": reg.app()})
    http = httpx.AsyncClient(transport=transport)
    rc = RegistryClient("http://registry.local", http=http)
    agents = {"health": HealthAgent(llm=None, http=http), "mobility": MobilityAgent(llm=None, http=http),
              "access": AccessibilityAgent(llm=None, http=http)}
    for host, agent in agents.items():
        base = f"http://{host}.local"
        transport.add(f"{host}.local", build_app(agent, base, "http://registry.local"))
        await rc.register(agent.card(base), base + "/.well-known/agent-card.json",
                          agent_id=agent.agent_id)  # (in produzione lo fa la lifespan)
    yield {"http": http, "rc": rc, "reg": reg, **agents}


def client(city, host):
    return A2AClient(f"http://{host}.local", http=city["http"], caller="test")


# ------------------------------------------------------------------ discovery

async def test_discovery_by_capability(city):
    found = await city["rc"].search("prenotare una visita ortopedica")
    assert found[0]["label"] == "Agente Sanità"
    found = await city["rc"].search(tag="trasporti")
    assert [f["label"] for f in found] == ["Agente Mobilità"]
    found = await city["rc"].search(skill="accessibilita_sede")
    assert found[0]["id"] == "mach:accessibility"  # stesso id della Busta (contratto 1.1)
    facts = await city["rc"].resolve("mach:healthcare")
    assert facts["facts"]["agent_name"] == "urn:agent:mach:healthcare"
    assert facts["facts"]["origin"] == "local" and facts["facts"]["mach_profile"] is True
    # le skill private (simulazione eventi) non compaiono nella discovery
    assert not await city["rc"].search(skill="segnala_evento")


# ------------------------------------------------------------------ sanità

async def test_health_natural_language_orientation(city):
    t = await client(city, "health").send("Ho mal di schiena da giorni, a chi mi devo rivolgere?")
    assert t.status.state == TaskState.COMPLETED
    d = t.result_data()
    assert d["status"] == "SUCCESS" and d["data"]["percorso"] == "sintomo_senza_prescrizione"
    assert d["provenance"] and d["agent"]["id"] == "mach:healthcare"
    validate_busta(d, check_domain=False)  # ogni risposta è una Busta 1.1 valida


async def test_health_emergency_short_circuit(city):
    t = await client(city, "health").send("Mio padre ha un forte dolore al petto")
    assert t.result_data()["status"] == "EMERGENCY" and "112" in t.result_text()


async def test_health_multi_turn_then_agent_to_agent(city):
    c = client(city, "health")
    t1 = await c.send(data={"skill": "cerca_disponibilita",
                            "args": {"prestazione": "visita ortopedica", "verifica_accessibilita": True}})
    assert t1.status.state == TaskState.INPUT_REQUIRED
    assert t1.result_data()["needs_input"]["fields"] == ["ha_prescrizione"]
    t2 = await c.send("sì", task_id=t1.id, context_id=t1.context_id)  # risposta in testo libero
    assert t2.status.state == TaskState.COMPLETED
    opzioni = t2.result_data()["data"]["opzioni"]
    assert len(opzioni) == 3
    # l'Agente Sanità ha chiesto all'Agente Accessibilità, trovato nel registry
    assert opzioni[0]["accessibilita"]["struttura_id"] == opzioni[0]["struttura_id"]
    tipi = [e["type"] for e in city["reg"].events]
    assert "call" in tipi and "result" in tipi
    # la chiamata agente->agente porta lo stesso trace_id con un passaggio in più
    assert t2.result_data()["trace_id"] == t1.result_data()["trace_id"]


def consent_md(skill: str, key: str | None = None) -> dict:
    """Metadata MACH con il consenso dato dall'utente (in produzione: dal gateway del Layer 4)."""
    return MachContext(trace_id="tr-test", consent=[Consent(skill=skill, granted_by="user", reference="dec-1",
                                                            idempotency_key=key)]).to_metadata()


async def test_booking_requires_consent(city):
    c = client(city, "health")
    args = {"slot_id": "S1", "chiave_idempotenza": "pratica-7"}
    t1 = await c.send(data={"skill": "prenota", "args": args})
    assert t1.status.state == TaskState.INPUT_REQUIRED
    d = t1.result_data()
    assert d["needs_input"]["fields"] == ["consent"]
    assert "visita ortopedica" in d["data"]["azione_proposta"]["summary"]
    # un "sì" in testo libero NON basta per una scrittura: serve la prova strutturata
    t2 = await c.send("sì", task_id=t1.id, context_id=t1.context_id)
    assert t2.status.state == TaskState.INPUT_REQUIRED
    t3 = await c.send(task_id=t1.id, context_id=t1.context_id, data={"ok": True},
                      metadata=consent_md("prenota", "pratica-7"))
    assert t3.status.state == TaskState.COMPLETED and t3.result_data()["data"]["codice"].startswith("PRN-")


async def test_consent_for_other_action_is_rejected(city):
    c = client(city, "health")
    t = await c.send(data={"skill": "prenota", "args": {"slot_id": "S4", "chiave_idempotenza": "k-A"}},
                     metadata=consent_md("prenota", "k-B"))  # consenso dato per un'altra operazione
    assert t.status.state == TaskState.INPUT_REQUIRED


async def test_health_booking_is_idempotent(city):
    c = client(city, "health")
    args = {"slot_id": "S2", "chiave_idempotenza": "pratica-42"}
    md = consent_md("prenota", "pratica-42")
    t1 = await c.send(data={"skill": "prenota", "args": args}, metadata=md)
    t2 = await c.send(data={"skill": "prenota", "args": args}, metadata=md)  # es. la risposta era andata persa
    assert t1.result_data()["data"]["codice"] == t2.result_data()["data"]["codice"]
    assert t2.result_data()["data"]["duplicato_evitato"] is True
    t3 = await c.send(data={"skill": "prenota", "args": {"slot_id": "S2", "chiave_idempotenza": "altra"}},
                      metadata=consent_md("prenota", "altra"))
    assert t3.status.state == TaskState.FAILED


async def test_missing_required_arg_asks_question(city):
    c = client(city, "health")
    md = consent_md("prenota", "k1")
    t1 = await c.send(data={"skill": "prenota", "args": {"chiave_idempotenza": "k1"}}, metadata=md)
    assert t1.status.state == TaskState.INPUT_REQUIRED and "slot" in t1.result_text().lower()
    t2 = await c.send("S3", task_id=t1.id, context_id=t1.context_id, metadata=md)
    assert t2.status.state == TaskState.COMPLETED


async def test_hop_limit_stops_loops(city):
    md = MachContext(trace_id="tr-loop", hops=9).to_metadata()
    t = await client(city, "access").send(data={"skill": "accessibilita_sede", "args": {"struttura_id": "fbf"}},
                                          metadata=md)
    assert t.status.state == TaskState.REJECTED and "passaggi" in t.result_text()


# ------------------------------------------------------------------ mobilità + eventi

async def test_mobility_replans_after_elevator_failure(city):
    c = client(city, "mobility")
    req = {"skill": "percorso", "args": {"origine": "Loreto", "destinazione": "niguarda", "senza_barriere": True}}
    t1 = await c.send(data=req)
    r1 = t1.result_data()["data"]
    assert r1["arrivo_metro"] == "Ca' Granda" and t1.result_data()["status"] == "SUCCESS"
    await c.send(data={"skill": "segnala_evento", "args": {"tipo": "ascensore_guasto", "stazione": "Ca' Granda"}})
    t2 = await c.send(data=req)
    r2 = t2.result_data()
    assert r2["status"] == "PARTIAL" and r2["data"]["arrivo_metro"] != "Ca' Granda"
    assert r2["data"]["ultimo_tratto"]


async def test_mobility_same_station(city):
    t = await client(city, "mobility").send(data={"skill": "percorso",
                                                  "args": {"origine": "Loreto", "destinazione": "cdc_loreto"}})
    d = t.result_data()["data"]
    assert d["tratte"] == [] and d["minuti_stimati"] == 0


async def test_mobility_strike_and_free_text(city):
    c = client(city, "mobility")
    await c.send("Sciopero", data={"skill": "segnala_evento", "args": {"tipo": "sciopero", "linea": "M2"}})
    t = await c.send("Come arrivo da Loreto a Turati?")  # prima passava da M2, ora deve evitarla
    assert t.status.state == TaskState.COMPLETED
    assert [tr["linea"] for tr in t.result_data()["data"]["tratte"]] == ["M1", "M3"]
    t = await c.send("Come arrivo da Loreto a Moscova?")  # Moscova è solo sulla M2
    assert t.result_data()["status"] == "PARTIAL" and t.result_data()["data"]["linee_ferme"] == ["M2"]


# ------------------------------------------------------------------ accessibilità

async def test_accessibility_profile_requires_consent(city):
    c = client(city, "access")
    t1 = await c.send(data={"skill": "profilo_bisogni", "args": {"cittadino_id": "anon-7", "mobilita_ridotta": True}})
    assert t1.status.state == TaskState.INPUT_REQUIRED
    t2 = await c.send("sì", task_id=t1.id, context_id=t1.context_id)
    assert t2.result_data()["data"]["profilo"] == {"mobilita_ridotta": True}


async def test_out_of_scope_is_rejected(city):
    t = await client(city, "access").send("Qual è il risultato della partita di ieri?")
    assert t.status.state == TaskState.REJECTED


# ------------------------------------------------------------------ compatibilità e LLM

async def test_legacy_03_client_can_call_our_agent(city):
    body = {"jsonrpc": "2.0", "id": 7, "method": "message/send", "params": {"message": {
        "kind": "message", "role": "user", "messageId": "x1",
        "parts": [{"kind": "data", "data": {"skill": "stato_rete", "args": {}}}]}}}
    r = (await city["http"].post("http://mobility.local/", json=body)).json()
    assert r["result"]["kind"] == "task" and r["result"]["status"]["state"] == "completed"


async def test_llm_routing_with_scripted_llm():
    llm = ScriptedLLM(['```json\n{"skill": "accessibilita_sede", "args": {"struttura_id": "fbf"}}\n```'])
    agent = AccessibilityAgent(llm=llm)
    skill_id, args = await agent.route(type("Ctx", (), {"text": "Il Fatebenefratelli ha barriere?"})())
    assert skill_id == "accessibilita_sede" and args == {"struttura_id": "fbf"}


async def test_tool_loop_with_scripted_llm():
    tools = ToolRegistry()

    @tools.tool
    def somma(a: int, b: int) -> int:
        """Somma due numeri."""
        return a + b

    llm = ScriptedLLM([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "somma", "arguments": '{"a": 2, "b": 3}'}}]},
        "Il risultato è 5.",
    ])
    text, trace = await run_tool_loop(llm, "sei utile", "quanto fa 2+3?", tools)
    assert text == "Il risultato è 5." and trace[0]["result"] == 5
    assert tools.openai_schema()[0]["function"]["parameters"]["required"] == ["a", "b"]


async def test_tools_export_to_mcp():
    pytest.importorskip("mcp.server.mcpserver")
    tools = ToolRegistry()

    @tools.tool
    def fermate_linea(linea: str) -> list[str]:
        """Elenca le fermate di una linea della metropolitana."""
        return {"M5": ["Garibaldi FS", "Isola"]}.get(linea, [])

    server = tools.to_mcp("dati-mobilita")
    listed = await server.list_tools()
    assert listed[0].name == "fermate_linea"
    result = await server.call_tool("fermate_linea", {"linea": "M5"})
    assert "Isola" in " ".join(c.text for c in result.content)  # una lista diventa più blocchi di testo
