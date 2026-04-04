"""Honcho memory plugin — MemoryProvider for Honcho AI-native memory.

Provides cross-session user modeling with dialectic Q&A, semantic search,
peer cards, and persistent conclusions via the Honcho SDK. Honcho provides AI-native cross-session user
modeling with dialectic Q&A, semantic search, peer cards, and conclusions.

The 4 tools (profile, search, context, conclude) are exposed through
the MemoryProvider interface.

Config: Uses the existing Honcho config chain:
  1. $HERMES_HOME/honcho.json (profile-scoped)
  2. ~/.honcho/config.json (legacy global)
  3. Environment variables
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (moved from tools/honcho_tools.py)
# ---------------------------------------------------------------------------

PROFILE_SCHEMA = {
    "name": "honcho_profile",
    "description": (
        "Get or set a peer's profile card in Honcho — a curated list of stable biographical facts "
        "(identity, occupation, relationships, preferences, traits). Max 40 facts per card.\n"
        "Actions: 'get' (default) reads the card, 'set' overwrites it, 'add' appends facts.\n"
        "Use 'peer' for the card owner and 'target' for directional cards (what peer knows about target).\n"
        "Cards auto-populate during dreaming — use set/add for bootstrapping or corrections."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "add"],
                "description": "What to do: 'get' (read card), 'set' (overwrite card), 'add' (append facts). Default: get.",
            },
            "facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of factual statements for set/add actions. Each string is one stable fact (not moods or transient info).",
            },
            "peer": {
                "type": "string",
                "description": "Card owner peer ID (default: user peer). Use honcho_peers to list available peers.",
            },
            "target": {
                "type": "string",
                "description": "For directional cards: get/set what 'peer' knows about 'target'. Omit for the peer's own card.",
            },
        },
        "required": [],
    },
}

SEARCH_SCHEMA = {
    "name": "honcho_search",
    "description": (
        "Semantic search over Honcho's stored context about the user. "
        "Returns raw excerpts ranked by relevance — no LLM synthesis. "
        "Cheaper and faster than honcho_context. "
        "Good when you want to find specific past facts and reason over them yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in Honcho's memory.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Token budget for returned context (default 800, max 2000).",
            },
        },
        "required": ["query"],
    },
}

CONTEXT_SCHEMA = {
    "name": "honcho_context",
    "description": (
        "Ask Honcho a natural language question and get a synthesized answer. "
        "Uses Honcho's LLM (dialectic reasoning) — higher cost than honcho_profile or honcho_search. "
        "Can query about any peer by ID (default: user peer)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A natural language question.",
            },
            "peer": {
                "type": "string",
                "description": "Peer ID to query about. Default: user peer. Use 'ai' for the assistant, or any peer ID.",
            },
        },
        "required": ["query"],
    },
}

CONCLUDE_SCHEMA = {
    "name": "honcho_conclude",
    "description": (
        "Write a conclusion about the user back to Honcho's memory. "
        "Conclusions are persistent facts that build the user's profile. "
        "Use when the user states a preference, corrects you, or shares "
        "something to remember across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {
                "type": "string",
                "description": "A factual statement about the user to persist.",
            }
        },
        "required": ["conclusion"],
    },
}

PEERS_SCHEMA = {
    "name": "honcho_peers",
    "description": (
        "List or create peers in the Honcho workspace. "
        "Peers are identities (users, agents, personas) that Honcho tracks.\n"
        "Actions: 'list' (default) shows all peers, 'create' creates a new peer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create"],
                "description": "What to do: 'list' (show all peers) or 'create' (make a new peer). Default: list.",
            },
            "peer_id": {
                "type": "string",
                "description": "ID for the new peer (required for create). Must be alphanumeric with hyphens/underscores.",
            },
        },
        "required": [],
    },
}

WORKSPACE_SCHEMA = {
    "name": "honcho_workspace",
    "description": (
        "Manage Honcho workspaces — isolated memory spaces for different contexts.\n"
        "Actions: 'get' (default) shows current workspace, 'list' shows all, 'create' makes a new one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "list", "create"],
                "description": "What to do. Default: get.",
            },
            "workspace_id": {
                "type": "string",
                "description": "ID for new workspace (required for create). Alphanumeric with hyphens/underscores.",
            },
        },
        "required": [],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HonchoMemoryProvider(MemoryProvider):
    """Honcho AI-native memory with dialectic Q&A and persistent user modeling."""

    def __init__(self):
        self._manager = None   # HonchoSessionManager
        self._config = None    # HonchoClientConfig
        self._session_key = ""
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "honcho"

    def is_available(self) -> bool:
        """Check if Honcho is configured. No network calls."""
        try:
            from plugins.memory.honcho.client import HonchoClientConfig
            cfg = HonchoClientConfig.from_global_config()
            return cfg.enabled and bool(cfg.api_key or cfg.base_url)
        except Exception:
            return False

    def save_config(self, values, hermes_home):
        """Write config to $HERMES_HOME/honcho.json (Honcho SDK native format)."""
        import json
        from pathlib import Path
        config_path = Path(hermes_home) / "honcho.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    def get_config_schema(self):
        return [
            {"key": "api_key", "description": "Honcho API key", "secret": True, "env_var": "HONCHO_API_KEY", "url": "https://app.honcho.dev"},
            {"key": "baseUrl", "description": "Honcho base URL (for self-hosted)"},
            {"key": "observation", "description": "Observation preset: directional (default), unified, off, or JSON object with per-peer booleans"},
            {"key": "messageMaxChars", "description": "Max chars per Honcho message (default 25000). Longer messages are chunked."},
            {"key": "dialecticDynamic", "description": "Auto-bump reasoning level for dialectic queries (default true)"},
            {"key": "dialecticMaxInputChars", "description": "Max chars for dialectic input queries (default 10000)"},
        ]

    def post_setup(self, hermes_home: str, config: dict) -> None:
        """Run the full Honcho setup wizard after provider selection."""
        try:
            import types
            from plugins.memory.honcho.cli import cmd_setup
            cmd_setup(types.SimpleNamespace())
        except ImportError:
            # cli.py not present in this checkout — fall through to generic setup
            pass

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize Honcho session manager."""
        try:
            from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client
            from plugins.memory.honcho.session import HonchoSessionManager

            cfg = HonchoClientConfig.from_global_config()
            if not cfg.enabled or not (cfg.api_key or cfg.base_url):
                logger.debug("Honcho not configured — plugin inactive")
                return

            self._config = cfg
            client = get_honcho_client(cfg)
            self._manager = HonchoSessionManager(
                honcho=client,
                config=cfg,
                context_tokens=cfg.context_tokens,
            )

            # Build session key from kwargs or session_id
            platform = kwargs.get("platform", "cli")
            user_id = kwargs.get("user_id", "")
            if user_id:
                self._session_key = f"{platform}:{user_id}"
            else:
                self._session_key = session_id

        except ImportError:
            logger.debug("honcho-ai package not installed — plugin inactive")
        except Exception as e:
            logger.warning("Honcho init failed: %s", e)
            self._manager = None

    def system_prompt_block(self) -> str:
        if not self._manager or not self._session_key:
            return ""
        return (
            "# Honcho Memory\n"
            "Active. AI-native cross-session user modeling.\n"
            "Use honcho_profile for a quick factual snapshot, "
            "honcho_search for raw excerpts, honcho_context for synthesized answers, "
            "honcho_conclude to save facts about the user."
        )

    def prefetch(self, query: str) -> str:
        """Return prefetched dialectic context from background thread."""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## Honcho Context\n{result}"

    def queue_prefetch(self, query: str) -> None:
        """Fire a background dialectic query for the upcoming turn."""
        if not self._manager or not self._session_key or not query:
            return

        def _run():
            try:
                result = self._manager.dialectic_query(
                    self._session_key, query, peer="user"
                )
                if result and result.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = result
            except Exception as e:
                logger.debug("Honcho prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="honcho-prefetch"
        )
        self._prefetch_thread.start()

    @staticmethod
    def _chunk_message(content: str, limit: int) -> list[str]:
        """Split content into chunks that fit within the Honcho message limit.

        Splits at paragraph boundaries when possible, falling back to
        sentence boundaries, then word boundaries. Each continuation
        chunk is prefixed with "[continued] " so Honcho's representation
        engine can reconstruct the full message.
        """
        if len(content) <= limit:
            return [content]

        prefix = "[continued] "
        prefix_len = len(prefix)
        chunks = []
        remaining = content
        first = True
        while remaining:
            budget = limit if first else limit - prefix_len
            if len(remaining) <= budget:
                chunks.append(remaining if first else prefix + remaining)
                break

            effective_limit = limit if first else limit - prefix_len
            segment = remaining[:effective_limit]

            # Try paragraph break, then sentence, then word
            cut = segment.rfind("\n\n")
            if cut < effective_limit * 0.3:
                cut = segment.rfind(". ")
                if cut >= 0:
                    cut += 2  # include the period and space
            if cut < effective_limit * 0.3:
                cut = segment.rfind(" ")
            if cut < effective_limit * 0.3:
                cut = effective_limit  # hard cut

            chunk = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip()
            if not first:
                chunk = prefix + chunk
            chunks.append(chunk)
            first = False

        return chunks

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Record the conversation turn in Honcho (non-blocking).

        Messages exceeding the Honcho API limit (default 25k chars) are
        split into multiple messages with continuation markers.
        """
        if not self._manager or not self._session_key:
            return

        msg_limit = self._config.message_max_chars if self._config else 25000

        def _sync():
            try:
                session = self._manager.get_or_create_session(self._session_key)
                for chunk in self._chunk_message(user_content, msg_limit):
                    session.add_message("user", chunk)
                for chunk in self._chunk_message(assistant_content, msg_limit):
                    session.add_message("assistant", chunk)
                self._manager._flush_session(session)
            except Exception as e:
                logger.debug("Honcho sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="honcho-sync"
        )
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in user profile writes as Honcho conclusions."""
        if action != "add" or target != "user" or not content:
            return
        if not self._manager or not self._session_key:
            return

        def _write():
            try:
                self._manager.create_conclusion(self._session_key, content)
            except Exception as e:
                logger.debug("Honcho memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="honcho-memwrite")
        t.start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Flush all pending messages to Honcho on session end."""
        if not self._manager:
            return
        # Wait for pending sync
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)
        try:
            self._manager.flush_all()
        except Exception as e:
            logger.debug("Honcho session-end flush failed: %s", e)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [PROFILE_SCHEMA, SEARCH_SCHEMA, CONTEXT_SCHEMA, CONCLUDE_SCHEMA, PEERS_SCHEMA, WORKSPACE_SCHEMA]

    def _resolve_peer(self, peer_arg: str | None):
        """Resolve a peer argument to a Honcho Peer object.

        'user' or None -> user peer, 'ai' -> assistant peer,
        anything else -> get-or-create by ID.
        """
        if self._session_key not in self._manager._cache:
            self._manager.get_or_create(self._session_key)
        session = self._manager._cache[self._session_key]

        if not peer_arg or peer_arg == "user":
            return self._manager._get_or_create_peer(session.user_peer_id)
        elif peer_arg == "ai":
            return self._manager._get_or_create_peer(session.assistant_peer_id)
        else:
            sanitized = self._manager._sanitize_id(peer_arg)
            return self._manager._get_or_create_peer(sanitized)

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if not self._manager or not self._session_key:
            return json.dumps({"error": "Honcho is not active for this session."})

        try:
            if tool_name == "honcho_profile":
                action = args.get("action", "get")
                peer = self._resolve_peer(args.get("peer"))
                target = args.get("target") or None

                if action == "get":
                    card = peer.get_card(target=target)
                    if not card:
                        label = f"{peer.id} about {target}" if target else peer.id
                        return json.dumps({"result": "No profile facts available yet.", "peer": label})
                    return json.dumps({"result": card, "peer": peer.id, "target": target})

                elif action == "set":
                    facts = args.get("facts", [])
                    if not facts:
                        return json.dumps({"error": "Missing 'facts' list for set action."})
                    if len(facts) > 40:
                        return json.dumps({"error": f"Peer cards have a 40-fact limit. Got {len(facts)}."})
                    result = peer.set_card(facts, target=target)
                    return json.dumps({"result": result or facts, "peer": peer.id, "target": target, "action": "set"})

                elif action == "add":
                    facts = args.get("facts", [])
                    if not facts:
                        return json.dumps({"error": "Missing 'facts' list for add action."})
                    existing = peer.get_card(target=target) or []
                    merged = existing + facts
                    if len(merged) > 40:
                        return json.dumps({"error": f"Would exceed 40-fact limit ({len(existing)} existing + {len(facts)} new = {len(merged)}). Use 'set' to replace."})
                    result = peer.set_card(merged, target=target)
                    return json.dumps({"result": result or merged, "peer": peer.id, "target": target, "action": "add", "added": len(facts)})

                return json.dumps({"error": f"Unknown action: {action}"})

            elif tool_name == "honcho_search":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                max_tokens = min(int(args.get("max_tokens", 800)), 2000)
                result = self._manager.search_context(
                    self._session_key, query, max_tokens=max_tokens
                )
                if not result:
                    return json.dumps({"result": "No relevant context found."})
                return json.dumps({"result": result})

            elif tool_name == "honcho_context":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                # Dialectic input guard — truncate long queries
                max_input = self._config.dialectic_max_input_chars if self._config else 10000
                if len(query) > max_input:
                    query = query[:max_input]
                    logger.debug("Truncated dialectic query to %d chars", max_input)
                peer_arg = args.get("peer", "user")
                peer = self._resolve_peer(peer_arg)
                result = peer.chat(query) or ""
                return json.dumps({"result": result or "No result from Honcho.", "peer": peer.id})

            elif tool_name == "honcho_conclude":
                conclusion = args.get("conclusion", "")
                if not conclusion:
                    return json.dumps({"error": "Missing required parameter: conclusion"})
                ok = self._manager.create_conclusion(self._session_key, conclusion)
                if ok:
                    return json.dumps({"result": f"Conclusion saved: {conclusion}"})
                return json.dumps({"error": "Failed to save conclusion."})

            elif tool_name == "honcho_peers":
                action = args.get("action", "list")

                if action == "list":
                    page = self._manager.honcho.peers()
                    peers = [{"id": p.id, "created_at": str(p.created_at)} for p in page.items]
                    return json.dumps({"peers": peers, "total": page.total})

                elif action == "create":
                    peer_id = args.get("peer_id", "")
                    if not peer_id:
                        return json.dumps({"error": "Missing 'peer_id' for create action."})
                    sanitized = self._manager._sanitize_id(peer_id)
                    peer = self._manager.honcho.peer(sanitized)
                    return json.dumps({"result": f"Peer '{peer.id}' created.", "peer": peer.id})

                return json.dumps({"error": f"Unknown action: {action}"})

            elif tool_name == "honcho_workspace":
                action = args.get("action", "get")

                if action == "get":
                    return json.dumps({
                        "workspace": self._config.workspace_id,
                        "host": self._config.host,
                        "peer_name": self._config.peer_name,
                        "ai_peer": self._config.ai_peer,
                    })

                elif action == "list":
                    try:
                        page = self._manager.honcho.workspaces()
                        workspaces = [{"id": w.id, "created_at": str(w.created_at)} for w in page.items]
                        return json.dumps({"workspaces": workspaces, "total": page.total})
                    except AttributeError:
                        import httpx
                        base = self._config.base_url or "http://localhost:8000"
                        resp = httpx.post(f"{base}/v3/workspaces/list", json={}, timeout=10)
                        resp.raise_for_status()
                        data = resp.json()
                        workspaces = [{"id": w["id"], "created_at": w.get("created_at", "")} for w in data.get("items", [])]
                        return json.dumps({"workspaces": workspaces, "total": data.get("total", len(workspaces))})

                elif action == "create":
                    workspace_id = args.get("workspace_id", "")
                    if not workspace_id:
                        return json.dumps({"error": "Missing 'workspace_id' for create action."})
                    try:
                        ws = self._manager.honcho.workspace(workspace_id)
                        return json.dumps({"result": f"Workspace '{ws.id}' ready.", "workspace": ws.id})
                    except (AttributeError, Exception):
                        import httpx
                        base = self._config.base_url or "http://localhost:8000"
                        resp = httpx.post(f"{base}/v3/workspaces", json={"id": workspace_id}, timeout=10)
                        resp.raise_for_status()
                        data = resp.json()
                        return json.dumps({"result": f"Workspace '{data.get('id', workspace_id)}' ready.", "workspace": data.get("id", workspace_id)})

                return json.dumps({"error": f"Unknown action: {action}"})

            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        except Exception as e:
            logger.error("Honcho tool %s failed: %s", tool_name, e)
            return json.dumps({"error": f"Honcho {tool_name} failed: {e}"})

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        # Flush any remaining messages
        if self._manager:
            try:
                self._manager.flush_all()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register Honcho as a memory provider plugin."""
    ctx.register_memory_provider(HonchoMemoryProvider())
