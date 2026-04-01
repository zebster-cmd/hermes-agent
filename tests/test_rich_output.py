"""Tests for agent/rich_output.py — syntax highlighting, diff rendering, code block detection."""

import pytest

from agent.rich_output import (
    DiffRenderer,
    FilePathFormatter,
    LanguageDetector,
    SyntaxHighlighter,
    _intra_diff,
    _parse_diff_filename,
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _seg_color(seg) -> str:
    """Return the colour name of a Text segment as a plain string."""
    return seg.style.color.name  # rich.color.Color.name is the canonical name string


def _renderables(diff: str) -> list:
    """Return the list of Text children from DiffRenderer._style().

    Uses ``group.renderables`` which is an internal Rich attribute.  If a
    future Rich upgrade removes it, switch to rendering the Group to a Console
    buffer and inspecting the output instead.
    """
    return list(DiffRenderer()._style(diff.splitlines()).renderables)


# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------

class TestLanguageDetector:
    def setup_method(self):
        self.det = LanguageDetector()

    def test_detect_python_from_extension(self):
        assert self.det.detect_from_filename("foo.py") == "python"

    def test_detect_typescript_from_extension(self):
        assert self.det.detect_from_filename("app.ts") == "typescript"

    def test_detect_unknown_extension_returns_none(self):
        assert self.det.detect_from_filename("file.xyz") is None

    def test_detect_dockerfile(self):
        assert self.det.detect_from_filename("Dockerfile") == "dockerfile"

    def test_detect_makefile(self):
        assert self.det.detect_from_filename("Makefile") == "makefile"

    def test_detect_python_from_content(self):
        code = "def hello():\n    return 42\n"
        assert self.det.detect_from_content(code) == "python"

    def test_detect_javascript_from_content(self):
        code = "const x = require('fs');\nmodule.exports = x;\n"
        assert self.det.detect_from_content(code) == "javascript"

    def test_detect_bash_from_content(self):
        code = "#!/bin/bash\nif [ -f foo ]; then echo hi; fi\n"
        assert self.det.detect_from_content(code) == "bash"

    def test_detect_empty_content_returns_none(self):
        assert self.det.detect_from_content("") is None
        assert self.det.detect_from_content("   \n  ") is None

    def test_detect_prefers_filename_over_content(self):
        # .js extension should win even if content looks like Python
        assert self.det.detect("def foo(): pass", filename="script.js") == "javascript"


# ---------------------------------------------------------------------------
# FilePathFormatter
# ---------------------------------------------------------------------------

class TestFilePathFormatter:
    def test_python_icon(self):
        assert FilePathFormatter.get_file_icon("main.py") == "🐍"

    def test_rust_icon(self):
        assert FilePathFormatter.get_file_icon("lib.rs") == "🦀"

    def test_unknown_extension_fallback(self):
        assert FilePathFormatter.get_file_icon("weird.xyz") == "📄"

    def test_titled_includes_icon_and_path(self):
        result = FilePathFormatter.titled("main.py", compact=False)
        assert "🐍" in result
        assert "main.py" in result

    def test_format_path_compact_returns_relative(self, tmp_path):
        file_path = str(tmp_path / "sub" / "foo.py")
        result = FilePathFormatter.format_path(file_path, compact=True, cwd=str(tmp_path))
        assert result == "sub/foo.py"

    def test_format_path_verbose_returns_full(self, tmp_path):
        file_path = str(tmp_path / "foo.py")
        result = FilePathFormatter.format_path(file_path, compact=False)
        assert result == file_path


# ---------------------------------------------------------------------------
# SyntaxHighlighter
# ---------------------------------------------------------------------------

class TestSyntaxHighlighter:
    def setup_method(self):
        self.hl = SyntaxHighlighter()

    def test_to_ansi_returns_string(self):
        result = self.hl.to_ansi("x = 1", language="python")
        assert isinstance(result, str)
        assert "x" in result

    def test_to_ansi_contains_ansi_codes(self):
        result = self.hl.to_ansi("def foo(): pass", language="python")
        # Should contain at least one ANSI escape sequence
        assert "\033[" in result

    def test_to_markup_returns_string(self):
        result = self.hl.to_markup("x = 1", language="python")
        assert isinstance(result, str)

    def test_to_ansi_empty_string(self):
        result = self.hl.to_ansi("")
        assert isinstance(result, str)

    def test_to_ansi_fallback_on_unknown_language(self):
        # Unknown language should not crash
        result = self.hl.to_ansi("some text", language="nonexistentlang123")
        assert isinstance(result, str)
        assert "some text" in result


# ---------------------------------------------------------------------------
# DiffRenderer
# ---------------------------------------------------------------------------

class TestDiffRenderer:
    def setup_method(self):
        self.dr = DiffRenderer()

    def test_to_lines_returns_list(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        lines = self.dr.to_lines(diff)
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_to_lines_contains_content(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        lines = self.dr.to_lines(diff)
        combined = "\n".join(lines)
        assert "old" in combined
        assert "new" in combined

    def test_from_content_produces_renderable(self):
        from rich.console import Group
        result = self.dr.from_content("old line\n", "new line\n", file_path="test.py")
        assert isinstance(result, Group)

    def test_from_unified_empty_diff(self):
        # Empty diff should not crash
        result = self.dr.to_lines("")
        assert isinstance(result, list)

    def test_file_header_formatted(self):
        diff = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-x\n+y\n"
        lines = self.dr.to_lines(diff)
        combined = "\n".join(lines)
        assert "main.py" in combined

    def test_to_lines_does_not_crash_on_malformed_diff(self):
        result = self.dr.to_lines("not a real diff at all\njust some text\n")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _intra_diff unit tests
# ---------------------------------------------------------------------------

class TestIntraDiff:
    def test_equal_spans_use_base_colour(self):
        del_segs, add_segs = _intra_diff("abc", "abc")
        for seg in del_segs + add_segs:
            assert not seg.style.bold
            assert _seg_color(seg) == "white"

    def test_changed_span_highlighted(self):
        del_segs, add_segs = _intra_diff("foo bar", "foo baz")
        # There must be at least one bright_red segment in del and bright_green in add
        del_highlighted = [s for s in del_segs if _seg_color(s) == "bright_red"]
        add_highlighted = [s for s in add_segs if _seg_color(s) == "bright_green"]
        assert del_highlighted, "expected at least one bright_red segment in del_segs"
        assert add_highlighted, "expected at least one bright_green segment in add_segs"
        # All highlighted segments must be bold
        assert all(s.style.bold for s in del_highlighted)
        assert all(s.style.bold for s in add_highlighted)
        # Equal spans must be white and not bold
        del_equal = [s for s in del_segs if _seg_color(s) == "white"]
        assert del_equal, "expected equal (white) segments in del_segs"
        assert all(not s.style.bold for s in del_equal)

    def test_delete_opcode_no_add_seg(self):
        del_segs, add_segs = _intra_diff("abcXYZ", "abc")
        del_plain = "".join(s.plain for s in del_segs)
        add_plain = "".join(s.plain for s in add_segs)
        assert "XYZ" in del_plain
        assert len(add_plain) == 3  # only "abc"

    def test_insert_opcode_no_del_seg(self):
        del_segs, add_segs = _intra_diff("abc", "abcXYZ")
        del_plain = "".join(s.plain for s in del_segs)
        add_plain = "".join(s.plain for s in add_segs)
        assert "XYZ" in add_plain
        assert len(del_plain) == 3  # only "abc"


# ---------------------------------------------------------------------------
# _parse_diff_filename unit tests
# ---------------------------------------------------------------------------

class TestParseDiffFilename:
    def test_strips_b_prefix(self):
        assert _parse_diff_filename("b/src/foo.py") == "foo.py"

    def test_strips_a_prefix(self):
        assert _parse_diff_filename("a/src/foo.py") == "foo.py"

    def test_bare_path(self):
        assert _parse_diff_filename("path/bar.py") == "bar.py"

    def test_devnull_falls_back_to_from(self):
        assert _parse_diff_filename("/dev/null", "a/old.py") == "old.py"

    def test_devnull_no_fallback_returns_question(self):
        assert _parse_diff_filename("/dev/null") == "?"


# ---------------------------------------------------------------------------
# DiffRenderer rendering tests
# ---------------------------------------------------------------------------

_SIMPLE_DIFF = (
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-foo bar\n"
    "+foo baz\n"
    " context\n"
)

_LOW_RATIO_DIFF = (
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1 +1 @@\n"
    "-aaaa\n"
    "+zzzz\n"
)


class TestDiffRendererV2:
    def test_intra_diff_skipped_below_ratio(self):
        import re
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        Console(file=buf, force_terminal=True, highlight=False, width=220).print(
            DiffRenderer()._style(_LOW_RATIO_DIFF.splitlines())
        )
        lines = buf.getvalue().splitlines()
        del_line = next(l for l in lines if "aaaa" in re.sub(r"\x1b\[[0-9;]*m", "", l))
        # bright_red bold is encoded as \x1b[1;91; — must not appear on a flat-colour line
        assert "\x1b[1;91;" not in del_line

    def test_pairing_per_run_not_per_hunk(self):
        # Use pairs with ratio > 0.5 so intra-diff triggers.
        # "return foo_value" vs "return bar_value": share "return " + "_value" = 13 chars,
        # total = 32, ratio = 26/32 ≈ 0.81.
        diff = (
            "--- a/f.py\n+++ b/f.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-return foo_value\n"
            "+return bar_value\n"
            " context\n"
            "-return foo_result\n"
            "+return bar_result\n"
        )
        import re
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        Console(file=buf, force_terminal=True, highlight=False, width=220).print(
            DiffRenderer()._style(diff.splitlines())
        )
        output = buf.getvalue()
        # Both pairs should produce intra-highlighted changed chars
        assert output.count("\x1b[1;91;") >= 2  # bright_red bold in both del lines
        assert output.count("\x1b[1;92;") >= 2  # bright_green bold in both add lines

    def test_alternating_run_flush(self):
        # -A +B -C +D with no context between — should pair (-A,+B) and (-C,+D)
        diff = (
            "--- a/f.py\n+++ b/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-alpha\n"
            "+ALPHA\n"
            "-beta\n"
            "+BETA\n"
        )
        renderables = _renderables(diff)
        all_plain = " ".join(r.plain for r in renderables)
        assert "alpha" in all_plain
        assert "ALPHA" in all_plain
        assert "beta" in all_plain
        assert "BETA" in all_plain

    def test_unpaired_lines_flat_colour(self):
        diff = (
            "--- a/f.py\n+++ b/f.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-first del\n"
            "-second del\n"
            "+one add\n"
        )
        import re
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        Console(file=buf, force_terminal=True, highlight=False, width=220).print(
            DiffRenderer()._style(diff.splitlines())
        )
        output = buf.getvalue()
        lines = output.splitlines()
        second_del = next(
            l for l in lines
            if "second del" in re.sub(r"\x1b\[[0-9;]*m", "", l)
        )
        assert "\x1b[91m" not in second_del  # no bright_red on unpaired line

    def test_summary_header_add_only(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n+new\n"
        renderables = _renderables(diff)
        plain = renderables[0].plain
        assert "Added" in plain
        assert "removed" not in plain

    def test_summary_header_remove_only(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n"
        plain = _renderables(diff)[0].plain
        assert "Removed" in plain
        assert "Added" not in plain

    def test_summary_header_mixed(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n"
        plain = _renderables(diff)[0].plain
        assert "Added" in plain
        assert "removed" in plain

    def test_summary_header_plural(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +2 @@\n+line1\n+line2\n"
        plain = _renderables(diff)[0].plain
        assert "Added 2 lines" in plain

    def test_summary_header_singular(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n+line1\n"
        plain = _renderables(diff)[0].plain
        assert "Added 1 line" in plain
        assert "lines" not in plain

    def test_summary_header_contains_filename(self):
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n+x\n"
        plain = _renderables(diff)[0].plain
        assert "foo.py" in plain

    def test_summary_header_strips_b_prefix(self):
        diff = "--- a/path/bar.py\n+++ b/path/bar.py\n@@ -1 +1 @@\n+x\n"
        plain = _renderables(diff)[0].plain
        assert "bar.py" in plain
        assert "b/bar.py" not in plain

    def test_summary_header_bare_path(self):
        diff = "--- path/bar.py\n+++ path/bar.py\n@@ -1 +1 @@\n+x\n"
        plain = _renderables(diff)[0].plain
        assert "bar.py" in plain

    def test_summary_header_devnull_fallback(self):
        diff = "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        plain = _renderables(diff)[0].plain
        assert "old.py" in plain

    def test_multi_file_diff_two_headers(self):
        diff = (
            "--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n+x\n"
            "--- a/two.py\n+++ b/two.py\n@@ -1 +1 @@\n+y\n"
        )
        renderables = _renderables(diff)
        header_plains = [r.plain for r in renderables if "●" in r.plain]
        assert len(header_plains) == 2
        assert any("one.py" in p for p in header_plains)
        assert any("two.py" in p for p in header_plains)

    def test_separator_width_matches_header(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n+x\n"
        renderables = _renderables(diff)
        header = renderables[0]
        separator = renderables[1]
        assert len(separator.plain) == len(header.plain)
