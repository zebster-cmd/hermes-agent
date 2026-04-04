# Memory Provider Plugins

Plugins give Hermes persistent, cross-session knowledge beyond built-in MEMORY.md and USER.md.

## Directory Structure

```
plugins/memory/my-provider/
├── __init__.py      # MemoryProvider implementation + register() entry point
├── plugin.yaml      # Metadata (name, description, hooks)
└── README.md        # Setup instructions
```

## The MemoryProvider ABC

From `agent/memory_provider.py`:

```python
from agent.memory_provider import MemoryProvider

class MyMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "my-provider"

    def is_available(self) -> bool:
        """NO network calls."""
        return bool(os.environ.get("MY_API_KEY"))

    def initialize(self, session_id: str, **kwargs) -> None:
        """kwargs always includes hermes_home (str)."""
        self._api_key = os.environ.get("MY_API_KEY", "")
        self._session_id = session_id
```

## Required Methods

### Core Lifecycle

| Method | When Called | Must Implement? |
|---|---|---|
| `name` (property) | Always | **Yes** |
| `is_available()` | Agent init | **Yes** — no network calls |
| `initialize(session_id, **kwargs)` | Agent startup | **Yes** |
| `get_tool_schemas()` | After init | **Yes** |
| `handle_tool_call(name, args)` | Tool use | **Yes** (if tools) |

### Config

| Method | Purpose | Must Implement? |
|---|---|---|
| `get_config_schema()` | Config fields for `hermes memory setup` | **Yes** |
| `save_config(values, hermes_home)` | Write non-secret config | **Yes** (unless env-var-only) |

### Optional Hooks

| Method | When Called | Use Case |
|---|---|---|
| `system_prompt_block()` | System prompt assembly | Static provider info |
| `prefetch(query)` | Before each API call | Return recalled context |
| `queue_prefetch(query)` | After each turn | Pre-warm for next turn |
| `sync_turn(user, assistant)` | After completed turn | Persist conversation |
| `on_session_end(messages)` | Conversation ends | Final extraction/flush |
| `on_pre_compress(messages)` | Before compression | Save insights before discard |
| `on_memory_write(action, target, content)` | Built-in memory writes | Mirror to backend |
| `shutdown()` | Process exit | Clean up connections |

## Config Schema

`get_config_schema()` returns field descriptors for `hermes memory setup`:
- `secret: True` + `env_var` → written to `.env`
- Non-secret fields → passed to `save_config()`

## Plugin Entry Point

```python
def register(ctx) -> None:
    ctx.register_memory_provider(MyMemoryProvider())
```

## plugin.yaml

```yaml
name: my-provider
version: 1.0.0
description: "Short description."
hooks:
  - on_session_end
```

## Threading Contract

**`sync_turn()` MUST be non-blocking.** Use daemon threads for API calls with latency.

## Profile Isolation

Use `hermes_home` kwarg from `initialize()`, not hardcoded `~/.hermes`. Use `get_hermes_home()` from `hermes_constants`.

## Testing

See `tests/agent/test_memory_plugin_e2e.py`. Use `MemoryManager` directly.

## Single Provider Rule (upstream)

Upstream: only one external memory provider at a time. **Our fork** supports multiple concurrent providers via `MemoryManager` with comma-separated config (e.g., `honcho+openviking`).
