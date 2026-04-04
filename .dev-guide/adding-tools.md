# Adding Tools

Before writing a tool, ask: **should this be a skill instead?**

- **Skill**: capability expressed as instructions + shell commands + existing tools
- **Tool**: requires end-to-end integration with API keys, custom processing, binary data, or streaming

## Overview — 3 files to touch

1. **`tools/your_tool.py`** — handler, schema, check function, `registry.register()` call
2. **`toolsets.py`** — add tool name to `_HERMES_CORE_TOOLS` (or a specific toolset)
3. **`model_tools.py`** — add `"tools.your_tool"` to the `_discover_tools()` list

## Step 1: Create the Tool File

```python
# tools/weather_tool.py
import json, os, logging
logger = logging.getLogger(__name__)

def check_weather_requirements() -> bool:
    return bool(os.getenv("WEATHER_API_KEY"))

def weather_tool(location: str, units: str = "metric") -> str:
    api_key = os.getenv("WEATHER_API_KEY")
    if not api_key:
        return json.dumps({"error": "WEATHER_API_KEY not configured"})
    try:
        return json.dumps({"location": location, "temp": 22, "units": units})
    except Exception as e:
        return json.dumps({"error": str(e)})

WEATHER_SCHEMA = {
    "name": "weather",
    "description": "Get current weather for a location.",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name or coordinates"},
            "units": {"type": "string", "enum": ["metric", "imperial"], "default": "metric"}
        },
        "required": ["location"]
    }
}

from tools.registry import registry
registry.register(
    name="weather", toolset="weather", schema=WEATHER_SCHEMA,
    handler=lambda args, **kw: weather_tool(location=args.get("location", ""), units=args.get("units", "metric")),
    check_fn=check_weather_requirements, requires_env=["WEATHER_API_KEY"],
)
```

### Key Rules

- Handlers **MUST** return a JSON string (`json.dumps()`), never raw dicts
- Errors **MUST** be returned as `{"error": "message"}`, never raised
- `check_fn` returns `False` → tool silently excluded
- Handler receives `(args: dict, **kwargs)`

## Step 2: Add to a Toolset (`toolsets.py`)

```python
_HERMES_CORE_TOOLS = [..., "weather"]
# OR create standalone:
"weather": {"description": "Weather lookup tools", "tools": ["weather"], "includes": []},
```

## Step 3: Add Discovery Import (`model_tools.py`)

```python
def _discover_tools():
    _modules = [..., "tools.weather_tool"]
```

## Async Handlers

Mark with `is_async=True` in `registry.register()`. Never call `asyncio.run()` yourself.

## Handlers That Need task_id

Receive via `**kwargs`: `task_id = kw.get("task_id")`

## Agent-Loop Intercepted Tools

`todo`, `memory`, `session_search`, `delegate_task` — intercepted by `run_agent.py` before registry. Schemas still registered for `get_tool_definitions()`.

## Optional: Setup Wizard Integration

Add to `OPTIONAL_ENV_VARS` in `hermes_cli/config.py`.

## Checklist

- [ ] Tool file with handler, schema, check function, registration
- [ ] Added to appropriate toolset in `toolsets.py`
- [ ] Discovery import in `model_tools.py`
- [ ] Handler returns JSON strings, errors as `{"error": "..."}`
- [ ] Optional: API key in `OPTIONAL_ENV_VARS`
- [ ] Optional: Added to `toolset_distributions.py` for batch processing
- [ ] Tested with `hermes chat -q "Use the weather tool for London"`
