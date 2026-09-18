"""The RPC client's ≥400 handling must surface the daemon's whole error
body — route, status, the ``error`` headline, and every remaining field —
because a 4xx body is the diagnosis (re-auth needed vs cloud config vs an
operator denial), not noise to discard."""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from puffo_agent.mcp._host_mcp import PuffoRpcClient


async def _serving_client(body: Any, status: int) -> tuple[PuffoRpcClient, TestClient]:
    """A real loopback server that answers every RPC with one canned body."""
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_post("/v1/rpc/{agent_id}/{route}", handler)
    http = TestClient(TestServer(app))
    await http.start_server()
    rpc = PuffoRpcClient(str(http.make_url("")).rstrip("/"), "agent_a")
    return rpc, http


@pytest.mark.asyncio
async def test_rich_error_body_survives_into_the_exception():
    """Status, route, headline, and the non-error fields all arrive."""
    rpc, http = await _serving_client(
        {
            "error": "permission denied by operator",
            "code": "operator_denied",
            "hint": "re-run /permissions and approve host-mcp",
            "request_id": "req-123",
        },
        403,
    )
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await rpc.sync_mcp(template_id="tpl-1")
    finally:
        await rpc.close()
        await http.close()
    message = str(excinfo.value)
    assert "rpc sync-mcp failed with status 403" in message
    assert "permission denied by operator" in message
    assert "operator_denied" in message
    assert "re-run /permissions and approve host-mcp" in message
    assert "req-123" in message


@pytest.mark.asyncio
async def test_body_without_error_headline_still_reaches_the_exception():
    """An empty ``error`` must not reduce the failure to a bare status."""
    rpc, http = await _serving_client(
        {"error": "", "reason": "token expired", "reauthorize": True},
        401,
    )
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await rpc.read_inbox()
    finally:
        await rpc.close()
        await http.close()
    message = str(excinfo.value)
    assert "rpc read-inbox failed with status 401" in message
    assert "token expired" in message
    assert "reauthorize" in message


@pytest.mark.asyncio
async def test_oversized_detail_is_bounded():
    """A pathological body may not turn the exception into a log bomb."""
    rpc, http = await _serving_client(
        {"error": "internal", "trace": "x" * 2000},
        500,
    )
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await rpc.send_message(channel="ch_a", text="hello")
    finally:
        await rpc.close()
        await http.close()
    message = str(excinfo.value)
    assert "rpc send-message failed with status 500: internal" in message
    assert "xxx" in message
    assert len(message) < 600


@pytest.mark.asyncio
async def test_error_only_body_gets_no_detail_tail():
    """Today's ``{"error": str}`` daemons keep a clean one-line message."""
    rpc, http = await _serving_client({"error": "bad request"}, 400)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await rpc.sync_mcp(template_id="tpl-1")
    finally:
        await rpc.close()
        await http.close()
    assert str(excinfo.value) == "rpc sync-mcp failed with status 400: bad request"
