# Prompt Assembly

Hermes separates **cached system prompt state** from **ephemeral API-call-time additions**. This affects token usage, prompt caching effectiveness, session continuity, and memory correctness.

**Primary files:** `run_agent.py`, `agent/prompt_builder.py`, `tools/memory_tool.py`

## Cached system prompt layers (in order)

1. Agent identity — `SOUL.md` from `HERMES_HOME`, or `DEFAULT_AGENT_IDENTITY`
2. Tool-aware behavior guidance
3. Honcho static block (when active)
4. Optional system message
5. Frozen MEMORY snapshot
6. Frozen USER profile snapshot
7. Skills index
8. Context files (`AGENTS.md`, `.cursorrules`, `.cursor/rules/*.mdc`) — SOUL.md not re-included
9. Timestamp / optional session ID
10. Platform hint

When `skip_context_files` is set (e.g., subagent delegation), SOUL.md not loaded → hardcoded `DEFAULT_AGENT_IDENTITY`.

## How SOUL.md appears

`load_soul_md()` reads `~/.hermes/SOUL.md`, security scans, truncates at 20k chars. Replaces `DEFAULT_AGENT_IDENTITY`. `build_context_files_prompt(skip_soul=True)` prevents duplication.

## Context file injection — priority system (first match wins)

| Priority | Files | Search scope | Notes |
|---|---|---|---|
| 1 | `.hermes.md`, `HERMES.md` | CWD up to git root | Hermes-native project config |
| 2 | `AGENTS.md` | CWD only | Common agent instruction file |
| 3 | `CLAUDE.md` | CWD only | Claude Code compatibility |
| 4 | `.cursorrules`, `.cursor/rules/*.mdc` | CWD only | Cursor compatibility |

All files: security scanned, truncated at 20k chars (70/20 head/tail), YAML frontmatter stripped.

## API-call-time-only layers (NOT persisted)

- `ephemeral_system_prompt`
- Prefill messages
- Gateway-derived session context overlays
- Later-turn Honcho recall injected into current-turn user message

## Memory snapshots

Frozen at session start. Mid-session writes update disk but don't mutate built system prompt until new session or forced rebuild.

## Skills index

Compact skills index contributed when skills tooling is available.

## Why split this way

- Preserve provider-side prompt caching
- Avoid mutating history unnecessarily
- Keep memory semantics understandable
- Let gateway/ACP/CLI add context without poisoning persistent prompt state
