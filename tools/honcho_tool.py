"""Honcho tool — AI-native cross-session memory via Honcho SDK.

Registers six LLM-callable tools:
- ``honcho_profile``   -- get/set a peer's profile card (stable biographical facts)
- ``honcho_search``    -- semantic search over stored context (raw excerpts)
- ``honcho_context``   -- natural language Q&A with synthesized answers (LLM dialectic)
- ``honcho_conclude``  -- write persistent conclusions about the user
- ``honcho_peers``     -- list or create peers (identities Honcho tracks)
- ``honcho_workspace`` -- manage isolated memory spaces

These tools work as a standalone toolset, available whenever Honcho is
configured (via honcho.json or HONCHO_API_KEY env var).  They can coexist
with the memory provider plugin (which adds lifecycle hooks like session
sync, prefetch, and automatic memory mirroring).  When both are active,
the memory provider's registry entries take precedence due to
``run_agent.py``'s re-registration on init — the plugin's handlers have
session context that the standalone tools lack.

Config resolution:
  1. $HERMES_HOME/honcho.json (profile-scoped)
  2. ~/.hermes/honcho.json (default profile)
  3. ~/.honcho/config.json (global)
  4. HONCHO_API_KEY env var
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy initialization — avoids import errors when honcho-ai not installed
# ---------------------------------------------------------------------------

_manager = None
_config = None
_initialized = False


def _ensure_init():
    """Lazily initialize the Honcho client and session manager.

    Called on first tool dispatch.  Caches the manager for reuse.
    Returns (manager, config) or (None, None) if unavailable.
    """
    global _manager, _config, _initialized
    if _initialized:
        return _manager, _config
    _initialized = True

    try:
        from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client
        from plugins.memory.honcho.session import HonchoSessionManager

        cfg = HonchoClientConfig.from_global_config()
        if not cfg.enabled or not (cfg.api_key or cfg.base_url):
            logger.debug("Honcho not configured — toolset inactive")
            return None, None

        client = get_honcho_client(cfg)
        mgr = HonchoSessionManager(
            honcho=client,
            config=cfg,
            context_tokens=cfg.context_tokens,
        )
        _manager = mgr
        _config = cfg
        return _manager, _config

    except ImportError:
        logger.debug("honcho-ai package not installed — toolset inactive")
        return None, None
    except Exception as e:
        logger.warning("Honcho tool init failed: %s", e)
        return None, None


def _get_session_key() -> str:
    """Derive a session key for standalone tool use."""
    return os.getenv("HERMES_SESSION_ID", "cli:default")


def _resolve_peer(mgr, session_key: str, peer_arg: str | None):
    """Resolve a peer argument to a Honcho Peer object."""
    if session_key not in mgr._cache:
        mgr.get_or_create(session_key)
    session = mgr._cache[session_key]

    if not peer_arg or peer_arg == "user":
        return mgr._get_or_create_peer(session.user_peer_id)
    elif peer_arg == "ai":
        return mgr._get_or_create_peer(session.assistant_peer_id)
    else:
        sanitized = mgr._sanitize_id(peer_arg)
        return mgr._get_or_create_peer(sanitized)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def _check_honcho_available() -> bool:
    """Tool is available when Honcho is configured."""
    # Quick check without initializing the full client
    if os.getenv("HONCHO_API_KEY"):
        return True
    try:
        from plugins.memory.honcho.client import HonchoClientConfig
        cfg = HonchoClientConfig.from_global_config()
        return cfg.enabled and bool(cfg.api_key or cfg.base_url)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_profile(args: dict, **kw) -> str:
    """Handler for honcho_profile tool."""
    mgr, cfg = _ensure_init()
    if not mgr:
        return json.dumps({"error": "Honcho is not available."})

    action = args.get("action", "get")
    session_key = _get_session_key()

    try:
        peer = _resolve_peer(mgr, session_key, args.get("peer"))
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

    except Exception as e:
        logger.error("honcho_profile error: %s", e)
        return json.dumps({"error": f"Profile failed: {e}"})


def _handle_search(args: dict, **kw) -> str:
    """Handler for honcho_search tool."""
    mgr, cfg = _ensure_init()
    if not mgr:
        return json.dumps({"error": "Honcho is not available."})

    query = args.get("query", "")
    if not query:
        return json.dumps({"error": "Missing required parameter: query"})

    max_tokens = min(int(args.get("max_tokens", 800)), 2000)
    session_key = _get_session_key()

    try:
        result = mgr.search_context(session_key, query, max_tokens=max_tokens)
        if not result:
            return json.dumps({"result": "No relevant context found."})
        return json.dumps({"result": result})
    except Exception as e:
        logger.error("honcho_search error: %s", e)
        return json.dumps({"error": f"Search failed: {e}"})


def _handle_context(args: dict, **kw) -> str:
    """Handler for honcho_context tool."""
    mgr, cfg = _ensure_init()
    if not mgr:
        return json.dumps({"error": "Honcho is not available."})

    query = args.get("query", "")
    if not query:
        return json.dumps({"error": "Missing required parameter: query"})

    session_key = _get_session_key()
    peer_arg = args.get("peer", "user")

    try:
        peer = _resolve_peer(mgr, session_key, peer_arg)
        result = peer.chat(query) or ""
        return json.dumps({"result": result or "No result from Honcho.", "peer": peer.id})
    except Exception as e:
        logger.error("honcho_context error: %s", e)
        return json.dumps({"error": f"Context failed: {e}"})


def _handle_conclude(args: dict, **kw) -> str:
    """Handler for honcho_conclude tool."""
    mgr, cfg = _ensure_init()
    if not mgr:
        return json.dumps({"error": "Honcho is not available."})

    conclusion = args.get("conclusion", "")
    if not conclusion:
        return json.dumps({"error": "Missing required parameter: conclusion"})

    session_key = _get_session_key()

    try:
        ok = mgr.create_conclusion(session_key, conclusion)
        if ok:
            return json.dumps({"result": f"Conclusion saved: {conclusion}"})
        return json.dumps({"error": "Failed to save conclusion."})
    except Exception as e:
        logger.error("honcho_conclude error: %s", e)
        return json.dumps({"error": f"Conclude failed: {e}"})


def _handle_peers(args: dict, **kw) -> str:
    """Handler for honcho_peers tool."""
    mgr, cfg = _ensure_init()
    if not mgr:
        return json.dumps({"error": "Honcho is not available."})

    action = args.get("action", "list")

    try:
        if action == "list":
            page = mgr.honcho.peers()
            peers = [{"id": p.id, "created_at": str(p.created_at)} for p in page.items]
            return json.dumps({"peers": peers, "total": page.total})

        elif action == "create":
            peer_id = args.get("peer_id", "")
            if not peer_id:
                return json.dumps({"error": "Missing 'peer_id' for create action."})
            sanitized = mgr._sanitize_id(peer_id)
            peer = mgr.honcho.peer(sanitized)
            return json.dumps({"result": f"Peer '{peer.id}' created.", "peer": peer.id})

        return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error("honcho_peers error: %s", e)
        return json.dumps({"error": f"Peers failed: {e}"})


def _handle_workspace(args: dict, **kw) -> str:
    """Handler for honcho_workspace tool."""
    mgr, cfg = _ensure_init()
    if not mgr:
        return json.dumps({"error": "Honcho is not available."})

    action = args.get("action", "get")

    try:
        if action == "get":
            return json.dumps({
                "workspace": cfg.workspace_id,
                "host": cfg.host,
                "peer_name": cfg.peer_name,
                "ai_peer": cfg.ai_peer,
            })

        elif action == "list":
            try:
                page = mgr.honcho.workspaces()
                workspaces = [{"id": w.id, "created_at": str(w.created_at)} for w in page.items]
                return json.dumps({"workspaces": workspaces, "total": page.total})
            except AttributeError:
                import httpx
                base = cfg.base_url or "http://localhost:8000"
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
                ws = mgr.honcho.workspace(workspace_id)
                return json.dumps({"result": f"Workspace '{ws.id}' ready.", "workspace": ws.id})
            except (AttributeError, Exception):
                import httpx
                base = cfg.base_url or "http://localhost:8000"
                resp = httpx.post(f"{base}/v3/workspaces", json={"id": workspace_id}, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                return json.dumps({"result": f"Workspace '{data.get('id', workspace_id)}' ready.", "workspace": data.get("id", workspace_id)})

        return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error("honcho_workspace error: %s", e)
        return json.dumps({"error": f"Workspace failed: {e}"})


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

HONCHO_PROFILE_SCHEMA = {
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

HONCHO_SEARCH_SCHEMA = {
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

HONCHO_CONTEXT_SCHEMA = {
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

HONCHO_CONCLUDE_SCHEMA = {
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

HONCHO_PEERS_SCHEMA = {
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

HONCHO_WORKSPACE_SCHEMA = {
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
# Registration
# ---------------------------------------------------------------------------

from tools.registry import registry

registry.register(
    name="honcho_profile",
    toolset="honcho",
    schema=HONCHO_PROFILE_SCHEMA,
    handler=_handle_profile,
    check_fn=_check_honcho_available,
    requires_env=["HONCHO_API_KEY"],
    emoji="H",
)

registry.register(
    name="honcho_search",
    toolset="honcho",
    schema=HONCHO_SEARCH_SCHEMA,
    handler=_handle_search,
    check_fn=_check_honcho_available,
    requires_env=["HONCHO_API_KEY"],
    emoji="H",
)

registry.register(
    name="honcho_context",
    toolset="honcho",
    schema=HONCHO_CONTEXT_SCHEMA,
    handler=_handle_context,
    check_fn=_check_honcho_available,
    requires_env=["HONCHO_API_KEY"],
    emoji="H",
)

registry.register(
    name="honcho_conclude",
    toolset="honcho",
    schema=HONCHO_CONCLUDE_SCHEMA,
    handler=_handle_conclude,
    check_fn=_check_honcho_available,
    requires_env=["HONCHO_API_KEY"],
    emoji="H",
)

registry.register(
    name="honcho_peers",
    toolset="honcho",
    schema=HONCHO_PEERS_SCHEMA,
    handler=_handle_peers,
    check_fn=_check_honcho_available,
    requires_env=["HONCHO_API_KEY"],
    emoji="H",
)

registry.register(
    name="honcho_workspace",
    toolset="honcho",
    schema=HONCHO_WORKSPACE_SCHEMA,
    handler=_handle_workspace,
    check_fn=_check_honcho_available,
    requires_env=["HONCHO_API_KEY"],
    emoji="H",
)
