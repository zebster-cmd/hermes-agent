# Creating Skills

Skills are the preferred way to add new capabilities. No code changes to the agent required.

## Skill vs Tool

- **Skill**: instructions + shell commands + existing tools (arXiv, git workflows, Docker, PDF)
- **Tool**: end-to-end integration with API keys, custom processing, binary data, streaming

## Directory Structure

```
skills/
├── research/
│   └── arxiv/
│       ├── SKILL.md              # Required: main instructions
│       └── scripts/              # Optional: helper scripts
```

## SKILL.md Format

```yaml
---
name: my-skill
description: Brief description
version: 1.0.0
author: Your Name
license: MIT
platforms: [macos, linux]          # Optional — omit for all platforms
metadata:
  hermes:
    tags: [Category, Keywords]
    requires_toolsets: [web]       # Only show when these toolsets active
    requires_tools: [web_search]   # Only show when these tools available
    fallback_for_toolsets: [browser]  # Hide when these toolsets active
    fallback_for_tools: [browser_navigate]
required_environment_variables:
  - name: MY_API_KEY
    prompt: "Enter your API key"
    help: "Get one at https://example.com"
    required_for: "API access"
---
# Skill Title
## When to Use
## Quick Reference
## Procedure
## Pitfalls
## Verification
```

### Conditional Activation

| Field | Behavior |
|---|---|
| `requires_toolsets` | Hidden when ANY listed toolset is **not** available |
| `requires_tools` | Hidden when ANY listed tool is **not** available |
| `fallback_for_toolsets` | Hidden when ANY listed toolset **is** available |
| `fallback_for_tools` | Hidden when ANY listed tool **is** available |

### Environment Variables

Declared vars auto-registered for passthrough into sandboxed environments (terminal, execute_code).

### Credential Files (OAuth tokens, etc.)

```yaml
required_credential_files:
  - path: google_token.json
    description: Google OAuth2 token
```

Auto-mounted into Docker (read-only bind) and Modal (synced before each command).

## Guidelines

- **No external dependencies** — prefer stdlib Python, curl, existing tools
- **Progressive disclosure** — common workflow first, edge cases at bottom
- **Include helper scripts** in `scripts/`
- **Test**: `hermes chat --toolsets skills -q "Use the X skill to do Y"`

## Where Should It Live?

- `skills/` — bundled, broadly useful
- `optional-skills/` — official but not universally needed
- **Skills Hub** — specialized, community, niche

## Security Scanning

Trust levels: `builtin`, `official`, `trusted`, `community`
