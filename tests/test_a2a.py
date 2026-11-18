"""Test del livello A2A: card, dialogo a più turni, errori, compatibilità 0.3."""
import pytest

from a2a_lite import (A2AClient, A2AServer, AgentCard, AgentInterface, AgentSkill, Reply, TaskContext,
                      TaskState)
from a2a_lite._http import httpx
from a2a_lite.errors import TaskNotFound
from a2a_lite.testing import RoutedASGITransport


def make_echo_server() -> A2AServer:
    card = AgentCard(
        name="echo", description="Ripete, ma prima chiede il nome",
        supported_interfaces=[AgentInterface(url="http://echo.local/")],
        skills=[AgentSkill(id="echo", name="Echo", description="Ripete il testo", tags=["test"])],
    )

    async def handler(ctx: TaskContext) -> Reply:
        if not ctx.is_continuation and "nome" not in ctx.data:
            return Reply(state=TaskState.INPUT_REQUIRED, text="Come ti chiami?")
        nome = ctx.data.get("nome") or ctx.text
        return Reply(text=f"Ciao {nome}", data={"saluto": f"Ciao {nome}"})

    return A2AServer(card, handler)


@pytest.fixture
def http():
    server = make_echo_server()
    transport = RoutedASGITransport({"echo.local": server.app()})
    return httpx.AsyncClient(transport=transport)


async def test_card_has_v1_and_legacy_fields(http):
    r = await http.get("http://echo.local/.well-known/agent-card.json")
    card = r.json()
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["url"] == "http://echo.local/"  # per client 0.3


async def test_multi_turn_input_required(http):
    client = A2AClient("http://echo.local", http=http)
    t1 = await client.send("Salutami")
    assert t1.status.state == TaskState.INPUT_REQUIRED
    assert t1.status.message.text() == "Come ti chiami?"
    t2 = await client.send("Leonardo", task_id=t1.id, context_id=t1.context_id)
    assert t2.id == t1.id and t2.status.state == TaskState.COMPLETED
    assert t2.result_data() == {"saluto": "Ciao Leonardo"}
    t3 = await client.get_task(t1.id)
    assert len(t3.history) == 4  # utente, agente, utente, agente


async def test_structured_request_completes_in_one_turn(http):
    client = A2AClient("http://echo.local", http=http)
    t = await client.send(data={"nome": "Giovanni"})
    assert t.status.state == TaskState.COMPLETED and t.result_text() == "Ciao Giovanni"


async def test_task_not_found(http):
    client = A2AClient("http://echo.local", http=http)
    with pytest.raises(TaskNotFound):
        await client.get_task("non-esiste")


async def test_raw_v1_wire_format(http):
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {
        "role": "ROLE_USER", "messageId": "m1", "parts": [{"data": {"nome": "Ada"}}]}}}
    r = (await http.post("http://echo.local/", json=body, headers={"A2A-Version": "1.0"})).json()
    task = r["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "kind" not in task


async def test_raw_legacy_03_wire_format(http):
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {
        "kind": "message", "role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"nome": "Ada"}}]}}}
    r = (await http.post("http://echo.local/", json=body)).json()
    task = r["result"]
    assert task["kind"] == "task" and task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"][0] == {"kind": "text", "text": "Ciao Ada"}


async def test_unknown_version_rejected(http):
    body = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}}
    r = (await http.post("http://echo.local/", json=body, headers={"A2A-Version": "9.0"})).json()
    assert r["error"]["code"] == -32009


async def test_streaming_unsupported(http):
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage", "params": {}}
    r = (await http.post("http://echo.local/", json=body)).json()
    assert r["error"]["code"] == -32004
