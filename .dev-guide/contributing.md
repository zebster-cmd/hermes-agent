# Contributing

## Contribution Priorities

1. **Bug fixes** — crashes, incorrect behavior, data loss
2. **Cross-platform compatibility** — macOS, different Linux distros, WSL2
3. **Security hardening** — shell injection, prompt injection, path traversal
4. **Performance and robustness** — retry logic, error handling, graceful degradation
5. **New skills** — broadly useful ones
6. **New tools** — rarely needed; most capabilities should be skills
7. **Documentation** — fixes, clarifications, new examples

## Common contribution paths

- Building a new tool? → Adding Tools
- Building a new skill? → Creating Skills
- Building a new inference provider? → Adding Providers

## Development Setup

### Prerequisites

| Requirement | Notes |
|---|---|
| **Git** | With `--recurse-submodules` support |
| **Python 3.10+** | uv will install it if missing |
| **uv** | Fast Python package manager |
| **Node.js 18+** | Optional — needed for browser tools and WhatsApp bridge |

### Clone and Install

```bash
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
uv venv venv --python 3.11
export VIRTUAL_ENV="$(pwd)/venv"
uv pip install -e ".[all,dev]"
uv pip install -e "./tinker-atropos"
npm install  # Optional: browser tools
```

### Configure for Development

```bash
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills}
cp cli-config.yaml.example ~/.hermes/config.yaml
touch ~/.hermes/.env
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key' >> ~/.hermes/.env
```

### Run

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/venv/bin/hermes" ~/.local/bin/hermes
hermes doctor
hermes chat -q "Hello"
```

### Run Tests

```bash
pytest tests/ -v
```

## Code Style

- **PEP 8** with practical exceptions (no strict line length enforcement)
- **Comments**: Only when explaining non-obvious intent, trade-offs, or API quirks
- **Error handling**: Catch specific exceptions. Use `logger.warning()`/`logger.error()` with `exc_info=True`
- **Cross-platform**: Never assume Unix
- **Profile-safe paths**: Never hardcode `~/.hermes` — use `get_hermes_home()` from `hermes_constants`

## Cross-Platform Compatibility

Hermes supports Linux, macOS, and WSL2. Native Windows is **not supported**.

1. **`termios` and `fcntl` are Unix-only** — Always catch both `ImportError` and `NotImplementedError`
2. **File encoding** — Handle non-UTF-8 `.env` files with `encoding="latin-1"` fallback
3. **Process management** — `os.setsid()`, `os.killpg()` differ across platforms
4. **Path separators** — Use `pathlib.Path` instead of string concatenation with `/`

## Security Considerations

| Layer | Implementation |
|---|---|
| **Sudo password piping** | `shlex.quote()` to prevent shell injection |
| **Dangerous command detection** | Regex patterns in `tools/approval.py` with user approval flow |
| **Cron prompt injection** | Scanner blocks instruction-override patterns |
| **Write deny list** | Protected paths via `os.path.realpath()` to prevent symlink bypass |
| **Skills guard** | Security scanner for hub-installed skills |
| **Code execution sandbox** | Child process runs with API keys stripped |
| **Container hardening** | Docker: all capabilities dropped, no privilege escalation, PID limits |

## Pull Request Process

### Branch Naming

```
fix/description        # Bug fixes
feat/description       # New features
docs/description       # Documentation
test/description       # Tests
refactor/description   # Code restructuring
```

### Before Submitting

1. Run tests: `pytest tests/ -v`
2. Test manually: Run `hermes` and exercise the code path you changed
3. Check cross-platform impact
4. Keep PRs focused: One logical change per PR

### Commit Messages — Conventional Commits

```
<type>(<scope>): <description>
```

Types: `fix`, `feat`, `docs`, `test`, `refactor`, `chore`
Scopes: `cli`, `gateway`, `tools`, `skills`, `agent`, `install`, `whatsapp`, `security`
