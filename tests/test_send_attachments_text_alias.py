"""``send_message_with_attachments`` must not silently lose its body.

``send_message`` names the message body ``text`` while the attachments
tool names it ``caption``; models habitually carry ``text=`` across, and
pydantic's default ignore-unknown-fields policy made that body vanish
with a successful send result (observed in production 2026-10-05: four
messages delivered as bare file cards, ``"text":""`` in the envelopes).
``text`` is therefore a first-class alias for ``caption``, and giving
both with different content is a loud error, never a silent pick.
"""

from __future__ import annotations

import pytest

from puffo_agent.mcp import core_message_tools


def _build_mcp(monkeypatch, captured):
    from mcp.server.fastmcp import FastMCP

    async def _capture(cfg, request, tool_name="send_message"):
        captured.append(request)
        return {"state": "sent", "attempted": True, "seq": 1}

    monkeypatch.setattr(
        core_message_tools, "_dispatch_semantic_send", _capture
    )
    mcp = FastMCP("test")
    core_message_tools.register_message_tools(mcp, cfg=object())
    return mcp


async def _call(mcp, args):
    result = await mcp.call_tool("send_message_with_attachments", args)
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        return "".join(getattr(item, "text", str(item)) for item in result)
    return str(result)


@pytest.mark.asyncio
async def test_text_reaches_the_caption(monkeypatch):
    """The production trap: ``text=`` through the real validation layer
    must land in the outgoing caption, not evaporate."""
    captured = []
    mcp = _build_mcp(monkeypatch, captured)
    await _call(
        mcp,
        {"paths": ["a.md"], "channel": "ch_test", "text": "the lost body"},
    )
    assert len(captured) == 1
    assert captured[0].caption == "the lost body"


@pytest.mark.asyncio
async def test_caption_alone_is_unchanged(monkeypatch):
    captured = []
    mcp = _build_mcp(monkeypatch, captured)
    await _call(
        mcp,
        {"paths": ["a.md"], "channel": "ch_test", "caption": "as before"},
    )
    assert captured[0].caption == "as before"


@pytest.mark.asyncio
async def test_identical_caption_and_text_are_accepted(monkeypatch):
    captured = []
    mcp = _build_mcp(monkeypatch, captured)
    await _call(
        mcp,
        {
            "paths": ["a.md"],
            "channel": "ch_test",
            "caption": "same body",
            "text": "same body",
        },
    )
    assert captured[0].caption == "same body"


@pytest.mark.asyncio
async def test_conflicting_caption_and_text_error_loudly(monkeypatch):
    """Two different bodies must never silently pick one."""
    captured = []
    mcp = _build_mcp(monkeypatch, captured)
    with pytest.raises(Exception) as excinfo:
        result = await mcp.call_tool(
            "send_message_with_attachments",
            {
                "paths": ["a.md"],
                "channel": "ch_test",
                "caption": "one body",
                "text": "another body",
            },
        )
        # FastMCP versions differ on raise-vs-isError; normalize to raise.
        text = str(result)
        if "aliases" in text:
            raise RuntimeError(text)
    assert "alias" in str(excinfo.value)
    assert captured == []  # nothing was dispatched


@pytest.mark.asyncio
async def test_empty_both_sends_empty_caption(monkeypatch):
    """Files-only sends (no body at all) keep working."""
    captured = []
    mcp = _build_mcp(monkeypatch, captured)
    await _call(mcp, {"paths": ["a.md"], "channel": "ch_test"})
    assert captured[0].caption == ""
