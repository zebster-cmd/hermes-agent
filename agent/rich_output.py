"""Rich-based syntax highlighting, diff rendering, and output utilities.

Drop into hermes-agent's ``agent/`` directory.

No project-specific imports — only ``rich`` (always present in Hermes) and
``pygments`` (bundled as a rich dependency).

Public API
----------
LanguageDetector        detect language from filename / content
FilePathFormatter       per-type icons + compact relative-path display
SyntaxHighlighter       Pygments → Rich markup → ANSI string
DiffRenderer            unified diff → Rich Text with line numbers → ANSI lines
apply_inline_markdown   convert **bold** / *italic* / `code` / ~~strike~~ to ANSI
apply_block_line        convert block-level markdown (headings, hr, blockquotes,
                        lists) to ANSI on a single line
clean_command_output    strip venv/stacktrace noise from command output
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from difflib import SequenceMatcher
from io import StringIO
from pathlib import Path
from typing import Optional

from rich.console import Console, Group
from rich.style import Style
from rich.text import Text

logger = logging.getLogger(__name__)

# Diff background colours — kept in sync with agent.display._ANSI_PLUS / _ANSI_MINUS
# _ANSI_PLUS  = "\033[38;2;255;255;255;48;2;20;90;20m"   → rgb(20,90,20)
# _ANSI_MINUS = "\033[38;2;255;255;255;48;2;120;20;20m"  → rgb(120,20,20)
_DIFF_BG_ADD = "#145a14"   # rgb(20, 90, 20)
_DIFF_BG_DEL = "#781414"   # rgb(120, 20, 20)

# Minimum SequenceMatcher ratio to apply intra-line highlighting.
# Below this the lines are too dissimilar and highlighting would be noise.
_INTRA_DIFF_MIN_RATIO: float = 0.5

# ---------------------------------------------------------------------------
# Pygments availability (bundled transitively via rich, but guard anyway)
# ---------------------------------------------------------------------------

try:
    from pygments.lexers import (
        TextLexer,
        get_lexer_by_name,
        get_lexer_for_filename,
        guess_lexer,
    )
    from pygments.token import (
        Comment,
        Error,
        Generic,
        Keyword,
        Name,
        Number,
        Operator,
        String,
    )
    from pygments.util import ClassNotFound

    _PYGMENTS = True
except ImportError:
    _PYGMENTS = False


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class LanguageDetector:
    """Detect programming language from filename extension or code content."""

    EXTENSION_MAP: dict[str, str] = {
        # Python
        ".py": "python", ".pyx": "python", ".pyi": "python", ".pyw": "python",
        # JavaScript / TypeScript
        ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "tsx",
        # JVM
        ".java": "java", ".scala": "scala", ".kt": "kotlin", ".groovy": "groovy",
        # C family
        ".c": "c", ".h": "c",
        ".cpp": "cpp", ".cxx": "cpp", ".cc": "cpp", ".hpp": "cpp",
        ".cs": "csharp", ".fs": "fsharp",
        # Systems
        ".rs": "rust", ".go": "go", ".swift": "swift",
        # Scripting
        ".rb": "ruby", ".php": "php", ".pl": "perl", ".lua": "lua",
        ".r": "r", ".R": "r",
        # Web
        ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
        ".sass": "sass", ".vue": "vue", ".svelte": "svelte",
        # Shell
        ".sh": "bash", ".bash": "bash", ".zsh": "zsh", ".fish": "fish",
        ".ps1": "powershell", ".bat": "batch", ".cmd": "batch",
        # Data / config
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".xml": "xml",
        # Docs
        ".md": "markdown", ".rst": "rst", ".tex": "latex",
        # DB
        ".sql": "sql",
        # Containers
        ".dockerfile": "dockerfile",
        # Other
        ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
        ".erl": "erlang", ".hs": "haskell", ".ml": "ocaml",
        ".elm": "elm", ".zig": "zig", ".vim": "vim",
    }

    CONTENT_PATTERNS: dict[str, list[str]] = {
        "python": [
            r"^\s*def\s+\w+\s*\(", r"^\s*class\s+\w+\s*[\(:]",
            r"^\s*import\s+\w+", r"^\s*from\s+\w+\s+import",
            r'if\s+__name__\s*==\s*[\'"]__main__[\'"]',
        ],
        "javascript": [
            r"^\s*function\s+\w+\s*\(", r"^\s*const\s+\w+\s*=",
            r"console\.log\s*\(", r'require\s*\([\'"]', r"module\.exports",
        ],
        "typescript": [
            r"^\s*interface\s+\w+", r"^\s*type\s+\w+\s*=",
            r":\s*string\s*[;,}]", r":\s*number\s*[;,}]",
        ],
        "java": [r"^\s*public\s+class\s+\w+", r"System\.out\.print"],
        "cpp": [r"#include\s*<\w+>", r"std::\w+", r"cout\s*<<"],
        "go": [r"^\s*package\s+\w+", r"^\s*func\s+\w+\s*\(", r"fmt\.Print"],
        "rust": [r"^\s*fn\s+\w+\s*\(", r"^\s*use\s+\w+", r"println!\s*\("],
        "bash": [r"#!/bin/bash", r"#!/bin/sh", r"^\s*if\s*\[", r"\$\{\w+\}"],
        "sql": [r"^\s*SELECT\s+", r"^\s*INSERT\s+INTO", r"^\s*CREATE\s+TABLE"],
    }

    def detect_from_filename(self, filename: str) -> Optional[str]:
        if not filename:
            return None
        ext = Path(filename).suffix.lower()
        if ext in self.EXTENSION_MAP:
            return self.EXTENSION_MAP[ext]
        name = Path(filename).name.lower()
        if name in {"dockerfile", "makefile", "rakefile", "gemfile", "vagrantfile"}:
            return name
        return None

    def detect_from_content(self, content: str, max_lines: int = 50) -> Optional[str]:
        if not content.strip():
            return None
        sample = "\n".join(content.split("\n")[:max_lines])
        scores: dict[str, int] = {}
        for lang, patterns in self.CONTENT_PATTERNS.items():
            score = sum(1 for p in patterns if re.search(p, sample, re.MULTILINE))
            if score:
                scores[lang] = score
        return max(scores, key=lambda k: scores[k]) if scores else None

    def detect(self, content: str, filename: Optional[str] = None) -> Optional[str]:
        return self.detect_from_filename(filename) or self.detect_from_content(content)


# ---------------------------------------------------------------------------
# File path formatting
# ---------------------------------------------------------------------------

class FilePathFormatter:
    """Per-filetype icons and compact relative-path display."""

    _ICONS: dict[str, str] = {
        ".py": "🐍", ".js": "📜", ".ts": "📘", ".tsx": "⚛️", ".jsx": "⚛️",
        ".html": "🌐", ".css": "🎨", ".scss": "🎨", ".md": "📝",
        ".json": "📋", ".yaml": "⚙️", ".yml": "⚙️", ".toml": "⚙️",
        ".txt": "📄", ".log": "📊", ".conf": "⚙️", ".cfg": "⚙️",
        ".xml": "📋", ".sql": "🗃️", ".sh": "💻", ".bash": "💻",
        ".go": "🐹", ".rs": "🦀", ".java": "☕", ".cpp": "⚙️",
        ".c": "⚙️", ".h": "📋",
    }

    @staticmethod
    def get_file_icon(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        return FilePathFormatter._ICONS.get(ext, "📄")

    @staticmethod
    def format_path(
        file_path: str,
        compact: bool = True,
        cwd: Optional[str] = None,
    ) -> str:
        if not compact:
            return file_path
        try:
            return os.path.relpath(file_path, cwd or os.getcwd())
        except (ValueError, OSError):
            return file_path

    @staticmethod
    def titled(
        file_path: str,
        compact: bool = True,
        cwd: Optional[str] = None,
    ) -> str:
        """Return ``{icon} {path}`` string."""
        icon = FilePathFormatter.get_file_icon(file_path)
        path = FilePathFormatter.format_path(file_path, compact, cwd)
        return f"{icon} {path}"


# ---------------------------------------------------------------------------
# Pygments → Rich markup formatter (internal)
# ---------------------------------------------------------------------------

class _PygmentsToRich:
    """Convert a Pygments token stream to a Rich markup string."""

    # Built lazily so the class-level dict isn't populated when Pygments is absent
    _STYLES: dict = {}

    @classmethod
    def _ensure_styles(cls) -> None:
        if cls._STYLES or not _PYGMENTS:
            return
        cls._STYLES = {
            Keyword: "bold blue",
            Keyword.Type: "bold cyan",
            Name: "white",
            Name.Builtin: "cyan",
            Name.Class: "bold yellow",
            Name.Constant: "bold yellow",
            Name.Decorator: "bright_cyan",
            Name.Exception: "bold red",
            Name.Function: "bold yellow",
            Name.Function.Magic: "cyan",
            Name.Tag: "bold blue",
            Name.Variable.Magic: "cyan",
            Comment: "dim green",
            Comment.Preproc: "bold green",
            String: "green",
            String.Doc: "dim green",
            String.Escape: "bold green",
            String.Interpol: "bold green",
            String.Regex: "magenta",
            Number: "magenta",
            Operator: "white",
            Operator.Word: "bold blue",
            Generic.Deleted: "red",
            Generic.Inserted: "green",
            Generic.Error: "bold red",
            Error: "bold red",
        }

    def format(self, tokens) -> str:
        self._ensure_styles()
        parts: list[str] = []
        for ttype, value in tokens:
            style = self._resolve(ttype)
            if style and value.strip():
                esc = value.replace("[", r"\[").replace("]", r"\]")
                parts.append(f"[{style}]{esc}[/{style}]")
            else:
                parts.append(value)
        return "".join(parts)

    def _resolve(self, ttype) -> Optional[str]:
        t = ttype
        while t is not None:
            if t in self._STYLES:
                return self._STYLES[t]
            t = t.parent  # type: ignore[assignment]
        return None


# ---------------------------------------------------------------------------
# Public: syntax highlighter
# ---------------------------------------------------------------------------

class SyntaxHighlighter:
    """Highlight source code using Pygments, output as Rich markup or ANSI.

    Falls back to plain green when Pygments is unavailable.
    """

    def __init__(self) -> None:
        self._fmt = _PygmentsToRich()
        self._detector = LanguageDetector()

    # -- Rich markup (for embedding in Rich Text / Panel) --------------------

    def to_markup(
        self,
        code: str,
        language: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> str:
        """Return a Rich markup string with syntax colours applied."""
        if not _PYGMENTS:
            escaped = code.replace("[", r"\[").replace("]", r"\]")
            return f"[green]{escaped}[/green]"
        try:
            lexer = self._lexer(code, language, filename)
            return self._fmt.format(list(lexer.get_tokens(code)))
        except Exception as exc:
            logger.debug("Pygments highlight failed: %s", exc)
            escaped = code.replace("[", r"\[").replace("]", r"\]")
            return f"[green]{escaped}[/green]"

    # -- ANSI string (for plain print / print_fn) ----------------------------

    def to_ansi(
        self,
        code: str,
        language: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> str:
        """Return an ANSI-escaped string suitable for plain ``print()``."""
        markup = self.to_markup(code, language, filename)
        buf = StringIO()
        Console(file=buf, highlight=False, force_terminal=True, width=220).print(markup)
        return buf.getvalue()

    # -- Helpers -------------------------------------------------------------

    def _lexer(self, code: str, language: Optional[str], filename: Optional[str]):
        try:
            if language:
                return get_lexer_by_name(language, stripnl=False)
            if filename:
                return get_lexer_for_filename(filename, stripnl=False)
            return guess_lexer(code, stripnl=False)
        except ClassNotFound:
            return TextLexer(stripnl=False)


# ---------------------------------------------------------------------------
# Diff renderer helpers (module-level for testability)
# ---------------------------------------------------------------------------

def _parse_diff_filename(path: str, fallback: Optional[str] = None) -> str:
    """Return the basename from a unified-diff path string.

    Strips ``b/`` / ``a/`` prefixes produced by ``git diff``.  If the result
    is ``/dev/null`` (deleted-file diff), recurses on *fallback* (the ``---``
    path) instead.
    """
    for prefix in ("b/", "a/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    if path == "/dev/null":
        if fallback:
            return _parse_diff_filename(fallback)
        return "?"
    name = Path(path).name
    return name if name else path


def _count_pass(
    lines: list[str],
    explicit_filename: Optional[str] = None,
) -> list[tuple[Optional[str], int, int]]:
    """First pass over diff lines: build ``(filename, n_adds, n_dels)`` per file boundary.

    When *explicit_filename* is provided (from ``from_content()``), the
    filename is fixed and only one entry is produced.  Otherwise filenames are
    parsed from ``+++ `` lines.
    """
    entries: list[tuple[Optional[str], int, int]] = []
    current_file: Optional[str] = explicit_filename
    n_adds = n_dels = 0
    from_path: Optional[str] = None
    started = explicit_filename is not None

    for line in lines:
        if line.startswith("--- "):
            from_path = line[4:].strip()
        elif line.startswith("+++ "):
            if started:
                entries.append((current_file, n_adds, n_dels))
            to_path = line[4:].strip()
            if explicit_filename is None:
                current_file = _parse_diff_filename(to_path, from_path)
            n_adds = n_dels = 0
            started = True
        elif line.startswith("+"):
            n_adds += 1
        elif line.startswith("-"):
            n_dels += 1

    if started:
        entries.append((current_file, n_adds, n_dels))

    return entries


def _make_header(filename: Optional[str], n_adds: int, n_dels: int) -> tuple[Text, Text]:
    """Return ``(header_Text, separator_Text)`` for the diff summary line."""
    def _pl(n: int) -> str:
        return f"{n} line" if n == 1 else f"{n} lines"

    parts: list[Text] = [
        Text("● ", style="bright_white"),
        Text(filename or "?", style=Style(color="bright_white", bold=True)),
        Text("   "),
    ]
    if n_adds > 0 and n_dels == 0:
        parts.append(Text(f"Added {_pl(n_adds)}", style="green"))
    elif n_dels > 0 and n_adds == 0:
        parts.append(Text(f"Removed {_pl(n_dels)}", style="red"))
    elif n_adds > 0 and n_dels > 0:
        parts.append(Text(f"Added {_pl(n_adds)}", style="green"))
        parts.append(Text(f", removed {_pl(n_dels)}", style="red"))

    header = Text.assemble(*parts)
    separator = Text("─" * len(header.plain), style="dim")
    return header, separator


def _flat_del(ln: int, content: str) -> Text:
    """Render a deletion line with flat (no intra-line) highlighting."""
    return Text.assemble(
        Text(f"{ln:>4} ", style="dim"),
        Text("- ", style=Style(color="red", bold=True)),
        Text(content, style=Style(bgcolor=_DIFF_BG_DEL, color="white")),
    )


def _flat_add(ln: int, content: str) -> Text:
    """Render an addition line with flat (no intra-line) highlighting."""
    return Text.assemble(
        Text(f"{ln:>4} ", style="dim"),
        Text("+ ", style=Style(color="green", bold=True)),
        Text(content, style=Style(bgcolor=_DIFF_BG_ADD, color="white")),
    )


def _intra_diff(old: str, new: str) -> tuple[list[Text], list[Text]]:
    """Character-level diff between two line content strings.

    Returns ``(del_segments, add_segments)`` — lists of ``Text`` objects
    covering the full content of each line with no gaps.  Changed characters
    are rendered bright-red / bright-green bold; unchanged characters use the
    base diff background with white foreground.

    Callers: ``Text.assemble(*del_segments)`` / ``Text.assemble(*add_segments)``.

    Note: segment lists may have different total character counts when
    ``delete`` or ``insert`` opcodes are present — this is correct because the
    two lines have different lengths.
    """
    del_segs: list[Text] = []
    add_segs: list[Text] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            del_segs.append(Text(old[i1:i2], style=Style(bgcolor=_DIFF_BG_DEL, color="white")))
            add_segs.append(Text(new[j1:j2], style=Style(bgcolor=_DIFF_BG_ADD, color="white")))
        elif tag == "replace":
            del_segs.append(Text(old[i1:i2], style=Style(bgcolor=_DIFF_BG_DEL, color="bright_red", bold=True)))
            add_segs.append(Text(new[j1:j2], style=Style(bgcolor=_DIFF_BG_ADD, color="bright_green", bold=True)))
        elif tag == "delete":
            del_segs.append(Text(old[i1:i2], style=Style(bgcolor=_DIFF_BG_DEL, color="bright_red", bold=True)))
        elif tag == "insert":
            add_segs.append(Text(new[j1:j2], style=Style(bgcolor=_DIFF_BG_ADD, color="bright_green", bold=True)))
    return del_segs, add_segs


# ---------------------------------------------------------------------------
# Public: diff renderer
# ---------------------------------------------------------------------------

class DiffRenderer:
    """Render a unified diff as Rich Text objects with line numbers.

    Produces coloured ``+`` / ``-`` lines with green / red backgrounds and
    dim context lines — significantly richer than raw ANSI strings.
    """

    # -- From old/new strings ------------------------------------------------

    def from_content(
        self,
        old: str,
        new: str,
        file_path: str = "file",
        context_lines: int = 3,
    ) -> Group:
        """Generate and render a diff between *old* and *new*."""
        import difflib

        lines = list(difflib.unified_diff(
            old.splitlines(keepends=False),
            new.splitlines(keepends=False),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=context_lines,
            lineterm="",
        ))
        return self._style(lines, file_path=file_path)

    # -- From unified diff text ----------------------------------------------

    def from_unified(self, diff_text: str) -> Group:
        """Render an already-generated unified diff string."""
        return self._style(diff_text.splitlines())

    # -- ANSI lines (drop-in for _render_inline_unified_diff) ----------------

    def to_lines(self, diff_text: str, width: int = 220) -> list[str]:
        """Render *diff_text* and return a list of ANSI-escaped strings.

        Compatible with Hermes's ``print_fn`` pattern: each element maps to
        one ``print_fn(line)`` call.
        """
        buf = StringIO()
        Console(file=buf, highlight=False, force_terminal=True, width=width).print(
            self.from_unified(diff_text)
        )
        # Drop the trailing empty line that Console adds
        return buf.getvalue().rstrip("\n").splitlines()

    # -- Internal rendering --------------------------------------------------

    def _style(self, lines: list[str], file_path: Optional[str] = None) -> Group:
        """Render *lines* (from a unified diff) as a ``Group`` of Rich ``Text``.

        *file_path* — when supplied (from ``from_content()``), its basename is
        used for the summary header instead of parsing the ``+++ `` line.
        """
        styled: list[Text] = []

        # Pass 1 — count adds/dels per file boundary for the summary header.
        explicit_filename = Path(file_path).name if file_path else None
        file_entries = iter(_count_pass(lines, explicit_filename))

        # Pass 2 — render with run-based pairing and intra-line highlighting.
        ln_old = ln_new = 0
        from_path: Optional[str] = None
        del_run: list[tuple[int, str]] = []  # (line_number, content)
        add_run: list[tuple[int, str]] = []

        def flush_runs() -> None:
            """Pair del/add runs and emit highlighted (or flat) Text objects."""
            if not del_run and not add_run:
                return
            n_pairs = min(len(del_run), len(add_run))

            # Precompute intra-diff segments for each pair.
            pair_segs: list[tuple[Optional[list[Text]], Optional[list[Text]]]] = []
            for i in range(n_pairs):
                old_content = del_run[i][1]
                new_content = add_run[i][1]
                r = SequenceMatcher(None, old_content, new_content).ratio()
                if r >= _INTRA_DIFF_MIN_RATIO:
                    d, a = _intra_diff(old_content, new_content)
                    pair_segs.append((d, a))
                else:
                    pair_segs.append((None, None))

            for i, (ln, content) in enumerate(del_run):
                if i < n_pairs and pair_segs[i][0] is not None:
                    styled.append(Text.assemble(
                        Text(f"{ln:>4} ", style="dim"),
                        Text("- ", style=Style(color="red", bold=True)),
                        *pair_segs[i][0],
                    ))
                else:
                    styled.append(_flat_del(ln, content))

            for i, (ln, content) in enumerate(add_run):
                if i < n_pairs and pair_segs[i][1] is not None:
                    styled.append(Text.assemble(
                        Text(f"{ln:>4} ", style="dim"),
                        Text("+ ", style=Style(color="green", bold=True)),
                        *pair_segs[i][1],
                    ))
                else:
                    styled.append(_flat_add(ln, content))

            del_run.clear()
            add_run.clear()

        for line in lines:
            if line.startswith("--- "):
                flush_runs()
                from_path = line[4:].strip()
                continue

            if line.startswith("+++ "):
                flush_runs()
                entry = next(file_entries, None)
                if entry:
                    fname, n_adds, n_dels = entry
                    header, sep = _make_header(fname, n_adds, n_dels)
                    styled.append(header)
                    styled.append(sep)
                continue

            if line.startswith("@@"):
                flush_runs()
                m = re.search(r"@@ -(\d+),?\d* \+(\d+),?\d* @@", line)
                if m:
                    ln_old, ln_new = int(m.group(1)), int(m.group(2))
                styled.append(Text(line, style=Style(color="cyan", bold=True)))
                continue

            if line.startswith("-"):
                if add_run:
                    # -→+→- transition: flush current run and start fresh
                    flush_runs()
                del_run.append((ln_old, line[1:]))
                ln_old += 1
                continue

            if line.startswith("+"):
                add_run.append((ln_new, line[1:]))
                ln_new += 1
                continue

            # Context line — show new-file line number (matches GitHub/delta convention
            # and avoids duplicate numbers when old/new offsets diverge)
            flush_runs()
            content = line[1:] if line.startswith(" ") else line
            styled.append(Text.assemble(
                Text(f"{ln_new:>4} ", style="dim"),
                Text("  ", style="dim"),
                Text(content, style="dim"),
            ))
            ln_old += 1
            ln_new += 1

        flush_runs()  # end of input
        styled.append(Text(""))  # trailing blank line
        return Group(*styled)


# ---------------------------------------------------------------------------
# Public: inline markdown → ANSI rendering
# ---------------------------------------------------------------------------

_MD_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_BOLD_STAR_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_BOLD_UNDER_RE = re.compile(r"(?<![_\w])__(.+?)__(?![_\w])")
_MD_ITALIC_STAR_RE = re.compile(r"\*([^*\n]+?)\*")
_MD_ITALIC_UNDER_RE = re.compile(r"(?<![_\w])_([^_\n]+)_(?![_\w])")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
# Images must be matched before links (![  prefix overlaps with [)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_EM_RE = re.compile(r"<em>(.*?)</em>", re.IGNORECASE)
_MD_STRONG_RE = re.compile(r"<strong>(.*?)</strong>", re.IGNORECASE)

_MD_BOLD_ANSI = "\033[1m"
_MD_ITALIC_ANSI = "\033[3m"
_MD_STRIKE_ANSI = "\033[9m"
_MD_CODE_ANSI = "\033[97m"
_MD_RST_ANSI = "\033[0m"


def apply_inline_markdown(line: str, reset_suffix: str = "") -> str:
    """Apply ANSI styling to inline markdown spans in a single text line.

    Handles ``**bold**``, ``__bold__``, ``*italic*``, ``_italic_``,
    ``~~strikethrough~~``, and `` `code` ``.  Backtick spans are processed
    first and their content is protected from bold/italic passes via
    placeholder tokens.

    ``reset_suffix`` is appended after each closing reset; pass the active
    response-text ANSI colour here so it is restored between adjacent spans
    during streaming.

    Returns *line* unchanged if it already contains ANSI escape codes.
    """
    if "\x1b" in line:
        return line

    rst = _MD_RST_ANSI + reset_suffix

    # Step 1: protect backtick code spans with index placeholders so later
    # passes cannot match * or _ inside them.
    protected: list[str] = []

    def _protect_code(m: re.Match) -> str:  # type: ignore[type-arg]
        protected.append(f"{_MD_CODE_ANSI}{m.group(1)}{rst}")
        return f"\x00{len(protected) - 1}\x00"

    line = _MD_CODE_RE.sub(_protect_code, line)

    # Step 2: bold
    line = _MD_BOLD_STAR_RE.sub(lambda m: f"{_MD_BOLD_ANSI}{m.group(1)}{rst}", line)
    line = _MD_BOLD_UNDER_RE.sub(lambda m: f"{_MD_BOLD_ANSI}{m.group(1)}{rst}", line)

    # Step 3: italic (runs after bold so ** is already consumed)
    line = _MD_ITALIC_STAR_RE.sub(lambda m: f"{_MD_ITALIC_ANSI}{m.group(1)}{rst}", line)
    line = _MD_ITALIC_UNDER_RE.sub(lambda m: f"{_MD_ITALIC_ANSI}{m.group(1)}{rst}", line)

    # Step 4: strikethrough
    line = _MD_STRIKE_RE.sub(lambda m: f"{_MD_STRIKE_ANSI}{m.group(1)}{rst}", line)

    # Step 5a: images (before links — ![  prefix overlaps)
    line = _MD_IMAGE_RE.sub(lambda m: f"\033[2m[img: {m.group(1)}]\033[0m{reset_suffix}", line)

    # Step 5b: links — underline text, discard URL
    line = _MD_LINK_RE.sub(lambda m: f"\033[4m{m.group(1)}\033[0m{reset_suffix}", line)

    # Step 5c: HTML inline tags
    line = _MD_EM_RE.sub(lambda m: f"{_MD_ITALIC_ANSI}{m.group(1)}\033[0m{reset_suffix}", line)
    line = _MD_STRONG_RE.sub(lambda m: f"{_MD_BOLD_ANSI}{m.group(1)}\033[0m{reset_suffix}", line)

    # Step 6: restore protected code spans
    for idx, span in enumerate(protected):
        line = line.replace(f"\x00{idx}\x00", span)

    # Step 7: strip CommonMark backslash escapes (\] → ], \* → *, etc.)
    line = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|~])', r'\1', line)

    return line


# ---------------------------------------------------------------------------
# Public: block-level markdown → ANSI rendering
# ---------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_MD_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_MD_BLOCKQUOTE_RE = re.compile(r"^>+\s?(.*)")
_MD_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.+)")
_MD_REF_LINK_RE = re.compile(r"^\[[^\]]+\]:\s+\S+")

_HEADING_STYLES = {
    1: "\033[1;97m",
    2: "\033[1;37m",
    3: "\033[1m",
    4: "\033[1;2m",
    5: "\033[1;2m",
    6: "\033[1;2m",
}
_BLOCKQUOTE_ANSI = "\033[2m"
_BULLETS = ["•", "◦", "▸", "·"]


def apply_block_line(line: str) -> str:
    """Apply ANSI styling to block-level markdown structures in a single line.

    Handles headings (h1–h6), horizontal rules, blockquotes, unordered lists,
    and reference link suppression.  Ordered lists are passed through unchanged.

    Two early-exit guards:
    - Lines containing ``\\x1b`` are already ANSI-rendered — returned as-is.
    - Lines containing ``\\n`` are multi-line blocks from ``StreamingBlockBuffer``
      (table or setext) — returned as-is.

    Returns *line* unchanged if no block pattern matches.
    """
    if "\x1b" in line:
        return line
    if "\n" in line:
        return line

    # Reference link definition — suppress entirely
    if _MD_REF_LINK_RE.match(line):
        return ""

    # Headings
    m = _MD_HEADING_RE.match(line)
    if m:
        level = len(m.group(1))
        text = m.group(2)
        style = _HEADING_STYLES.get(level, "\033[1;2m")
        rendered_text = apply_inline_markdown(text, reset_suffix=style)
        return f"{style}{rendered_text}{_MD_RST_ANSI}"

    # Horizontal rule
    stripped = line.rstrip()
    if _MD_HR_RE.match(stripped):
        cols = shutil.get_terminal_size((80, 24)).columns
        return f"\033[2m{'─' * cols}\033[0m"

    # Blockquote — collapse any level of nesting to single gutter
    m = _MD_BLOCKQUOTE_RE.match(line)
    if m:
        content = m.group(1)
        content_rendered = apply_inline_markdown(content, reset_suffix=_BLOCKQUOTE_ANSI)
        return f"{_BLOCKQUOTE_ANSI}▌ {content_rendered}\033[0m"

    # Unordered list — bullet symbol by indent depth
    m = _MD_UL_RE.match(line)
    if m:
        indent, _marker, content = m.group(1), m.group(2), m.group(3)
        level = len(indent) // 2
        bullet = _BULLETS[min(level, len(_BULLETS) - 1)]
        return f"{indent}{bullet} {content}"

    return line


# ---------------------------------------------------------------------------
# Public: fenced code block highlighting for LLM responses
# ---------------------------------------------------------------------------

def format_response(text: str) -> str:
    """Apply syntax highlighting and markdown rendering to a complete response string.

    Pass 1: replaces each `` ```lang\\ncode\\n``` `` block with an
    ANSI-highlighted version.  Pass 2: applies block-level then inline markdown
    (headings, hr, blockquotes, lists, bold, italic, code spans, etc.) to every
    non-code line.  Suitable for the non-streaming Rich Panel display path.
    """
    _hl = SyntaxHighlighter()
    _det = LanguageDetector()

    _RST = "\033[0m"  # reset — transparent no-op; marks lines as code for pass 2

    def _highlight(m: "re.Match") -> str:
        lang = m.group(2).strip() or None
        code = m.group(3)
        if not lang:
            lang = _det.detect_from_content(code)
        highlighted = _hl.to_ansi(code, language=lang).rstrip("\n")
        # Some lexers (e.g. plain-text) emit lines with no ANSI codes.
        # Pass 2 uses `"\x1b" in l` to detect already-highlighted lines and
        # skip markdown rendering.  Guarantee every code-block line has at
        # least one escape by prepending a no-op reset to bare lines.
        lines_out = []
        for line in highlighted.splitlines():
            lines_out.append(line if "\x1b" in line else _RST + line)
        return "\n".join(lines_out)

    # Match fenced code blocks of any depth (3+ backticks); \1 backreference
    # ensures the closing fence uses the same backtick sequence as the opener.
    text = re.sub(r"(`{3,})(\w*)\n(.*?)\1", _highlight, text, flags=re.DOTALL)
    # Block + inline markdown pass — lines with \x1b are already highlighted code.
    # Use splitlines() (no keepends) so apply_block_line never receives a trailing
    # \n that its capture groups would silently drop.  Rejoin manually and restore
    # the final newline if the original text ended with one.
    lines = text.splitlines()
    result = "\n".join(
        l if "\x1b" in l else apply_inline_markdown(apply_block_line(l))
        for l in lines
    )
    if text.endswith("\n"):
        result += "\n"
    return result


class StreamingCodeBlockHighlighter:
    """State machine that syntax-highlights fenced code blocks during streaming.

    Feed lines one at a time with ``process_line()``.  Regular lines are
    returned immediately; lines inside a code block are buffered and the
    entire highlighted block is returned when the closing fence arrives.

    Example usage in a line-emission loop::

        hl = StreamingCodeBlockHighlighter()
        for line in stream_lines:
            out = hl.process_line(line)
            if out is not None:
                emit(out)
        # End of stream — flush any unclosed block
        tail = hl.flush()
        if tail is not None:
            emit(tail)
    """

    # Matches an opening fence: 3+ backticks, optional language hint (word chars)
    _FENCE_OPEN_RE = re.compile(r"^(`{3,})\s*(\w*)$")
    # Matches a closing fence: 3+ backticks, optional trailing whitespace only
    _FENCE_CLOSE_RE = re.compile(r"^(`+)\s*$")

    def __init__(self) -> None:
        self._in_block: bool = False
        self._lang: Optional[str] = None
        self._fence_depth: int = 3  # backtick count of the opening fence
        self._buf: list[str] = []
        self._hl = SyntaxHighlighter()
        self._det = LanguageDetector()

    def process_line(self, line: str) -> Optional[str]:
        """Process one line.

        Returns the string to emit (may be multi-line for a highlighted block),
        or ``None`` to suppress the line (still accumulating a code block).
        """
        stripped = line.strip()

        if not self._in_block:
            m = self._FENCE_OPEN_RE.match(stripped)
            if m:
                self._in_block = True
                self._fence_depth = len(m.group(1))
                self._lang = m.group(2) or None
                self._buf = []
                return None  # suppress opening fence — will re-emit with block
            return line  # plain text, pass through

        # Inside a code block — closing fence: >= fence_depth backticks, nothing else
        m = self._FENCE_CLOSE_RE.match(stripped)
        if m and len(m.group(1)) >= self._fence_depth:
            return self._flush_block()
        self._buf.append(line)
        return None  # still accumulating

    def flush(self) -> Optional[str]:
        """Flush any open (unclosed) code block at end of stream."""
        if self._in_block and self._buf:
            return self._flush_block()
        return None

    def reset(self) -> None:
        """Reset state for a new response turn."""
        self._in_block = False
        self._lang = None
        self._fence_depth = 3
        self._buf = []

    def _flush_block(self) -> str:
        code = "\n".join(self._buf)
        lang = self._lang or self._det.detect_from_content(code)
        highlighted = self._hl.to_ansi(code, language=lang).rstrip("\n")
        self._in_block = False
        self._lang = None
        self._buf = []
        return highlighted


# ---------------------------------------------------------------------------
# Public: output noise cleaning
# ---------------------------------------------------------------------------

_NOISE_SUBSTRINGS = frozenset({
    "/venv/lib/python", "/site-packages/", "langsmith/", "langchain/",
    "__pycache__", "venv/lib/", "site-packages",
    "Traceback (most recent call last)", '  File "/',
})


def clean_command_output(content: str) -> str:
    """Strip venv paths, stacktrace boilerplate, and excessive blank lines.

    Useful for cleaning up ``terminal`` tool results before display.
    """
    out: list[str] = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if any(s in line for s in _NOISE_SUBSTRINGS):
            continue
        if len(line) > 80 and line.count("/") > 5:
            continue
        if line.startswith("from ") and "import" in line and len(line) > 60:
            continue
        line = re.sub(r"\\n\./([^/]+/)*", "", line)
        line = re.sub(r"\\n/[^/]+/[^/]+/([^/]+)", r" \1", line)
        line = line.replace("\\n", "\n").replace("\n\n\n", "\n\n")
        if line and len(line) > 3:
            out.append(line)

    result = "\n".join(out)
    return re.sub(r"\n\s*\n\s*\n", "\n\n", result).strip()


# ---------------------------------------------------------------------------
# Module-level convenience singletons
# ---------------------------------------------------------------------------

lang_detector = LanguageDetector()
syntax_highlighter = SyntaxHighlighter()
diff_renderer = DiffRenderer()
