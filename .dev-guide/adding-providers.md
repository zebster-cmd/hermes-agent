# Adding Providers

Only add a built-in provider for first-class UX: provider-specific auth, curated model catalog, setup menu entries, provider aliases, non-OpenAI API adapter. Otherwise, a named custom provider may suffice.

## The mental model

1. `hermes_cli/auth.py` → credentials
2. `hermes_cli/runtime_provider.py` → runtime data (provider, api_mode, base_url, api_key, source)
3. `run_agent.py` → uses `api_mode` for request building
4. `hermes_cli/models.py` + `hermes_cli/main.py` → CLI visibility (setup.py inherits automatically)
5. `agent/auxiliary_client.py` + `agent/model_metadata.py` → side tasks + token budgeting

API modes: `chat_completions`, `codex_responses`, `anthropic_messages`

## Implementation paths

### Path A — OpenAI-compatible
Auth metadata + model catalog + runtime resolution + CLI wiring + aux defaults + tests + docs.

### Path B — Native provider
Everything from Path A + `agent/<provider>_adapter.py` + `run_agent.py` branches.

## File checklist

### Required for every built-in provider
1. `hermes_cli/auth.py`
2. `hermes_cli/models.py`
3. `hermes_cli/runtime_provider.py`
4. `hermes_cli/main.py`
5. `agent/auxiliary_client.py`
6. `agent/model_metadata.py`
7. tests
8. user-facing docs

### Additional for native providers
1. `agent/<provider>_adapter.py`
2. `run_agent.py`
3. `pyproject.toml` (if SDK required)

## Steps

1. **Pick canonical provider id** — use everywhere consistently
2. **Auth metadata** (`hermes_cli/auth.py`) — `ProviderConfig` in `PROVIDER_REGISTRY` + aliases
3. **Model catalog** (`hermes_cli/models.py`) — `_PROVIDER_MODELS`, `_PROVIDER_LABELS`, `_PROVIDER_ALIASES`
4. **Runtime resolution** (`hermes_cli/runtime_provider.py`) — branch returning provider/api_mode/base_url/api_key/source
5. **CLI wiring** (`hermes_cli/main.py`) — `provider_labels`, `providers` list, dispatch, `--provider` choices
6. **Auxiliary calls** — aux model in `_API_KEY_PROVIDER_AUX_MODELS`, context lengths in `model_metadata.py`
7. **Native adapter** (if needed) — isolate in `agent/<provider>_adapter.py`, audit `api_mode` switches in `run_agent.py`
8. **Tests** — `test_runtime_provider_resolution.py`, `test_cli_provider_resolution.py`, `test_cli_model_command.py`, `test_setup_model_selection.py`, `test_provider_parity.py`
9. **Live verification** — `hermes chat -q "Say hello" --provider X --model Y`
10. **Docs** — quickstart, configuration, environment variables

## Search targets

`PROVIDER_REGISTRY`, `_PROVIDER_ALIASES`, `_PROVIDER_MODELS`, `resolve_runtime_provider`, `_model_flow_`, `select_provider_and_model`, `api_mode`, `_API_KEY_PROVIDER_AUX_MODELS`, `self.client.`

## Common pitfalls

1. Provider in auth but not model parsing → `/model` fails
2. `config["model"]` can be string or dict — normalize both
3. Not every service needs a built-in provider
4. Aux routing not updated → summarization/memory/vision helpers fail
5. Native branches hiding behind `api_mode` / `self.client.` in `run_agent.py`
6. Sending OpenRouter-only knobs to other providers
