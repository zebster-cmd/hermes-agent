# Agent Loop Internals

Core orchestration engine: `AIAgent` in `run_agent.py`.

## Responsibilities

- Assembling effective prompt and tool schemas
- Selecting correct provider/API mode
- Making interruptible model calls
- Executing tool calls (sequential or concurrent)
- Maintaining session history
- Handling compression, retries, fallback models

## API modes

| API mode | Used for |
|---|---|
| `chat_completions` | OpenAI-compatible chat endpoints, OpenRouter, custom endpoints |
| `codex_responses` | OpenAI Codex / Responses API path |
| `anthropic_messages` | Native Anthropic Messages API |

## Turn lifecycle

```
run_conversation()
  -> generate effective task_id
  -> append current user message
  -> load or build cached system prompt
  -> maybe preflight-compress
  -> build api_messages
  -> inject ephemeral prompt layers
  -> apply prompt caching if appropriate
  -> make interruptible API call
  -> if tool calls: execute them, append tool results, loop
  -> if final text: persist, cleanup, return response
```

## Interruptible API calls

Wraps API requests for interruption from CLI or gateway. Supports cancellation semantics for long LLM calls.

## Tool execution modes

- Sequential for single or interactive tools
- Concurrent for multiple non-interactive tools
- Preserves message/result ordering

## Callback surfaces

`tool_progress_callback`, `thinking_callback`, `reasoning_callback`, `clarify_callback`, `step_callback`, `stream_delta_callback`, `tool_gen_callback`, `status_callback`

## Budget and fallback

Shared iteration budget across parent/subagents. Budget pressure hints near end of iteration window. Fallback model support for provider/model switching on failure.

## Compression and persistence

- Flush memory before context loss
- Compress middle conversation turns
- Split session lineage into new session ID after compression
- Preserve recent context and structural tool-call/result consistency
