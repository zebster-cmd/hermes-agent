# Tools Runtime

Tools are self-registering functions grouped into toolsets and executed through a central registry/dispatch system.

**Primary files:** `tools/registry.py`, `model_tools.py`, `toolsets.py`, `tools/terminal_tool.py`, `tools/environments/*`

## Tool registration model

Each tool module calls `registry.register(...)` at import time. `model_tools.py` imports/discovers tool modules and builds the schema list.

### `registry.register()` signature

```python
registry.register(
    name="terminal",               # Unique tool name
    toolset="terminal",            # Toolset this tool belongs to
    schema={...},                  # OpenAI function-calling schema
    handler=handle_terminal,       # Execution function
    check_fn=check_terminal,       # Optional: availability check
    requires_env=["SOME_VAR"],     # Optional: env vars needed
    is_async=False,                # Whether handler is async
    description="Run commands",    # Human-readable description
)
```

### Discovery: `_discover_tools()`

Imports every tool module in order, triggering `registry.register()` calls. Errors in optional tools are caught and logged.

After core discovery: MCP tools via `tools.mcp_tool.discover_mcp_tools()`, Plugin tools via `hermes_cli.plugins.discover_plugins()`.

## Tool availability checking (`check_fn`)

- Returns `True` → available, `False` → excluded
- Results cached per-call
- Exceptions treated as "unavailable" (fail-safe)

## Toolset resolution

Toolsets are named bundles. `get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode)`:

1. `enabled_toolsets` provided → only those toolsets
2. `disabled_toolsets` provided → all minus disabled
3. Neither → all known toolsets
4. Registry applies `check_fn` filtering
5. Dynamic schema patching for `execute_code` and `browser_navigate`

## Dispatch flow

```
Model tool_call → run_agent.py agent loop
  → [Agent-loop tools?] → handled directly (todo, memory, session_search, delegate_task)
  → [Plugin pre-hook] → invoke_hook("pre_tool_call", ...)
  → registry.dispatch(name, args, **kwargs)
  → Look up ToolEntry → [Async?] bridge via _run_async() / [Sync?] call directly
  → Return result string (or JSON error)
  → [Plugin post-hook] → invoke_hook("post_tool_call", ...)
```

### Error wrapping

Two levels: `registry.dispatch()` catches handler exceptions, `handle_function_call()` wraps entire dispatch. Model always receives well-formed JSON.

### Async bridging

- **CLI (no loop)** → persistent event loop for cached async clients
- **Gateway (running loop)** → disposable thread with `asyncio.run()`
- **Worker threads** → per-thread persistent loops in thread-local storage

## DANGEROUS_PATTERNS approval flow

`tools/approval.py` — regex patterns for destructive operations. Before terminal execution:
1. `detect_dangerous_command()` checks patterns
2. CLI → interactive prompt; Gateway → async callback; Smart approval → aux LLM auto-approve
3. Approvals tracked per-session; permanent allowlist in `config.yaml`

## Terminal/runtime environments

Backends: local, docker, ssh, singularity, modal, daytona. Per-task cwd overrides, background process management, PTY mode, approval callbacks.
