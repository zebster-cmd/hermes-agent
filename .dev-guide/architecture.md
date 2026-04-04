# Architecture

## High-level structure

```
hermes-agent/
├── run_agent.py              # AIAgent core loop
├── cli.py                    # interactive terminal UI
├── model_tools.py            # tool discovery/orchestration
├── toolsets.py               # tool groupings and presets
├── hermes_state.py           # SQLite session/state database
├── batch_runner.py           # batch trajectory generation
├── agent/                    # prompt building, compression, caching, metadata, trajectories
├── hermes_cli/               # command entrypoints, auth, setup, models, config, doctor
├── tools/                    # tool implementations and terminal environments
├── gateway/                  # messaging gateway, session routing, delivery, pairing, hooks
├── cron/                     # scheduled job storage and scheduler
├── plugins/memory/           # Memory provider plugins (honcho, openviking, mem0, etc.)
├── acp_adapter/              # ACP editor integration server
├── acp_registry/             # ACP registry manifest + icon
├── environments/             # Hermes RL / benchmark environment framework
├── skills/                   # bundled skills
├── optional-skills/          # official optional skills
└── tests/                    # test suite
```

## Recommended reading order

1. Architecture (this page)
2. Agent Loop Internals
3. Prompt Assembly
4. Provider Runtime Resolution
5. Adding Providers
6. Tools Runtime
7. Session Storage
8. Gateway Internals
9. Context Compression & Prompt Caching
10. ACP Internals
11. Environments, Benchmarks & Data Generation

## Major subsystems

### Agent loop
Core synchronous orchestration engine: `AIAgent` in `run_agent.py`. Responsible for provider/API-mode selection, prompt construction, tool execution, retries/fallback, callbacks, compression, persistence.

### Prompt system
Split between `run_agent.py`, `agent/prompt_builder.py`, `agent/prompt_caching.py`, `agent/context_compressor.py`.

### Provider/runtime resolution
Shared runtime provider resolver used by CLI, gateway, cron, ACP, and auxiliary calls.

### Tooling runtime
Tool registry, toolsets, terminal backends, process manager, dispatch rules.

### Session persistence
SQLite-based, with lineage preserved across compression splits.

### Messaging gateway
Long-running orchestration layer for platform adapters, session routing, pairing, delivery, cron ticking.

### ACP integration
Exposes Hermes as editor-native agent over stdio/JSON-RPC.

### Cron
Cron jobs as first-class agent tasks, not just shell tasks.

### RL / environments / trajectories
Full environment framework for evaluation, RL integration, SFT data generation.

## Design themes

- Prompt stability matters
- Tool execution must be observable and interruptible
- Session persistence must survive long-running use
- Platform frontends should share one agent core
- Optional subsystems should remain loosely coupled
