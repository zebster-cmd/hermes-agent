"""Tests for Honcho plugin cron session write guard (#4052).

Cron sessions must not write messages to Honcho — the cron prompt
contains system instructions ("You are Hermes...") that would be
misattributed to the user peer, corrupting the user representation.

Verifies that:
1. initialize() with cron context sets _cron_skipped
2. sync_turn() is a no-op when _cron_skipped is True
3. on_memory_write() is a no-op when _cron_skipped is True
4. handle_tool_call() returns an error when _cron_skipped is True
5. system_prompt_block() returns empty when _cron_skipped is True
6. Non-cron sessions still operate normally
"""

import json
import pytest
from unittest.mock import MagicMock, patch


def _make_provider(cron: bool = True):
    """Create a HonchoMemoryProvider with cron guard optionally triggered."""
    from plugins.memory.honcho import HonchoMemoryProvider

    provider = HonchoMemoryProvider()
    if cron:
        provider._cron_skipped = True
    return provider


class TestCronGuardSkipsWrites:
    """All write paths must be no-ops when _cron_skipped is True."""

    def test_sync_turn_skipped(self):
        provider = _make_provider(cron=True)
        # Should not raise and should not attempt any Honcho operations
        provider.sync_turn("You are Hermes...", "Sure, here's the result.")

    def test_on_memory_write_skipped(self):
        provider = _make_provider(cron=True)
        # Should not raise
        provider.on_memory_write("add", "user", "User prefers dark mode")

    def test_handle_tool_call_returns_error(self):
        provider = _make_provider(cron=True)
        result = json.loads(provider.handle_tool_call("honcho_profile", {}))
        assert "error" in result
        assert "cron" in result["error"].lower()

    def test_system_prompt_block_empty(self):
        provider = _make_provider(cron=True)
        assert provider.system_prompt_block() == ""

    def test_prefetch_returns_empty(self):
        provider = _make_provider(cron=True)
        assert provider.prefetch("test query") == ""

    def test_queue_prefetch_is_noop(self):
        provider = _make_provider(cron=True)
        # Should not raise
        provider.queue_prefetch("test query")

    def test_get_tool_schemas_empty(self):
        provider = _make_provider(cron=True)
        assert provider.get_tool_schemas() == []

    def test_on_session_end_is_noop(self):
        provider = _make_provider(cron=True)
        # Should not raise
        provider.on_session_end([])


class TestCronGuardInitialize:
    """initialize() must detect cron context and set _cron_skipped."""

    def test_cron_agent_context_triggers_skip(self):
        provider = _make_provider(cron=False)
        provider.initialize("test-session", agent_context="cron")
        assert provider._cron_skipped is True

    def test_flush_agent_context_triggers_skip(self):
        provider = _make_provider(cron=False)
        provider.initialize("test-session", agent_context="flush")
        assert provider._cron_skipped is True

    def test_cron_platform_triggers_skip(self):
        provider = _make_provider(cron=False)
        provider.initialize("test-session", platform="cron")
        assert provider._cron_skipped is True

    def test_normal_context_does_not_skip(self):
        """Non-cron contexts should NOT set _cron_skipped."""
        provider = _make_provider(cron=False)
        # initialize will try to load config which may fail in test env,
        # but _cron_skipped should remain False
        try:
            provider.initialize("test-session", platform="cli")
        except Exception:
            pass  # Config/import errors expected in test
        assert provider._cron_skipped is False
