"""Tests for agent/rich_output.py — syntax highlighting, diff rendering, code block detection."""

import pytest
from unittest.mock import patch

from agent.rich_output import (
    DiffRenderer,
    FilePathFormatter,
    LanguageDetector,
    StreamingCodeBlockHighlighter,
    SyntaxHighlighter,
    _intra_diff,
    _parse_diff_filename,
    clean_command_output,
    format_response,
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
# StreamingCodeBlockHighlighter
# ---------------------------------------------------------------------------

class TestStreamingCodeBlockHighlighter:
    def setup_method(self):
        self.hl = StreamingCodeBlockHighlighter()

    def test_plain_lines_pass_through(self):
        assert self.hl.process_line("Hello world") == "Hello world"
        assert self.hl.process_line("Another line") == "Another line"

    def test_opening_fence_suppressed(self):
        assert self.hl.process_line("```python") is None

    def test_code_lines_buffered(self):
        self.hl.process_line("```python")
        assert self.hl.process_line("x = 1") is None
        assert self.hl.process_line("y = 2") is None

    def test_closing_fence_flushes_highlighted(self):
        self.hl.process_line("```python")
        self.hl.process_line("x = 1")
        result = self.hl.process_line("```")
        assert result is not None
        assert "x" in result

    def test_full_code_block_sequence(self):
        lines = ["Here is code:", "```python", "def foo(): pass", "```", "Done."]
        outputs = []
        for line in lines:
            out = self.hl.process_line(line)
            if out is not None:
                outputs.append(out)
        tail = self.hl.flush()
        if tail:
            outputs.append(tail)

        combined = "\n".join(outputs)
        assert "Here is code:" in combined
        assert "foo" in combined
        assert "Done." in combined

    def test_flush_returns_none_when_no_open_block(self):
        assert self.hl.flush() is None

    def test_flush_returns_content_for_unclosed_block(self):
        self.hl.process_line("```python")
        self.hl.process_line("x = 1")
        result = self.hl.flush()
        assert result is not None
        assert "x" in result

    def test_reset_clears_state(self):
        self.hl.process_line("```python")
        self.hl.process_line("x = 1")
        self.hl.reset()
        assert self.hl.flush() is None
        # Should behave as fresh after reset
        assert self.hl.process_line("normal line") == "normal line"

    def test_multiple_blocks_in_sequence(self):
        lines = [
            "Block one:", "```python", "a = 1", "```",
            "Block two:", "```javascript", "var b = 2;", "```",
        ]
        outputs = [self.hl.process_line(l) for l in lines]
        non_none = [o for o in outputs if o is not None]
        assert len(non_none) == 4  # "Block one:", highlighted, "Block two:", highlighted

    def test_no_language_hint_still_works(self):
        self.hl.process_line("```")
        self.hl.process_line("SELECT * FROM users;")
        result = self.hl.process_line("```")
        assert result is not None
        assert "SELECT" in result

    def test_lang_hint_passed_to_highlighter(self):
        """Opening fence ```python should call to_ansi with language='python'."""
        with patch.object(self.hl._hl, "to_ansi", return_value="highlighted") as mock_ansi:
            self.hl.process_line("```python")
            self.hl.process_line("x = 1")
            self.hl.process_line("```")
        mock_ansi.assert_called_once()
        _, kwargs = mock_ansi.call_args
        assert kwargs.get("language") == "python"

    def test_no_lang_hint_calls_content_detection(self):
        """Opening fence with no hint should fall back to detect_from_content."""
        with patch.object(self.hl._det, "detect_from_content", return_value=None) as mock_det:
            self.hl.process_line("```")
            self.hl.process_line("x = 1")
            self.hl.process_line("```")
        mock_det.assert_called_once()

    def test_four_backtick_fence_opened_and_closed(self):
        """4-backtick opening fence is handled; 4-backtick closing fence closes it."""
        assert self.hl.process_line("````python") is None
        assert self.hl.process_line("x = 1") is None
        result = self.hl.process_line("````")
        assert result is not None
        assert "x" in result

    def test_four_backtick_fence_three_backtick_close_ignored(self):
        """3-backtick closing fence inside a 4-backtick block is buffered, not a close."""
        assert self.hl.process_line("````python") is None
        assert self.hl.process_line("x = 1") is None
        assert self.hl.process_line("```") is None  # still buffering
        result = self.hl.flush()
        assert result is not None
        assert "x" in result

    def test_prose_after_four_backtick_block_rendered(self):
        """Lines after a properly-closed 4-backtick block pass through as prose."""
        self.hl.process_line("````python")
        self.hl.process_line("x = 1")
        self.hl.process_line("````")
        out = self.hl.process_line("plain text")
        assert out == "plain text"


# ---------------------------------------------------------------------------
# format_response
# ---------------------------------------------------------------------------

class TestFormatResponse:
    def test_plain_text_unchanged(self):
        text = "No code here, just text."
        result = format_response(text)
        assert "No code here, just text." in result

    def test_code_block_highlighted(self):
        text = "Here:\n```python\ndef foo(): pass\n```\nDone."
        result = format_response(text)
        assert "foo" in result
        assert "Here:" in result
        assert "Done." in result

    def test_multiple_code_blocks(self):
        text = "First:\n```python\nx = 1\n```\nSecond:\n```javascript\nvar y = 2;\n```"
        result = format_response(text)
        assert "x" in result
        assert "y" in result

    def test_no_code_blocks_returns_original(self):
        text = "Just a response with no fences."
        assert format_response(text) == text

    def test_empty_string(self):
        assert format_response("") == ""

    def test_code_block_without_language(self):
        text = "```\nSELECT * FROM t;\n```"
        result = format_response(text)
        assert "SELECT" in result

    def test_no_lang_hint_calls_content_detection(self):
        """Fence with no lang tag should call LanguageDetector.detect_from_content."""
        from unittest.mock import patch, MagicMock
        with patch("agent.rich_output.LanguageDetector") as MockLD:
            mock_instance = MagicMock()
            mock_instance.detect_from_content.return_value = None
            MockLD.return_value = mock_instance
            format_response("```\nSELECT * FROM t;\n```")
        mock_instance.detect_from_content.assert_called_once()

    def test_fence_delimiters_not_in_output(self):
        """format_response must not include raw ``` in the highlighted output."""
        text = "```python\ndef foo(): pass\n```"
        result = format_response(text)
        import re as _re
        plain = _re.sub(r"\x1b\[[0-9;]*m", "", result)
        for line in plain.splitlines():
            assert not line.strip().startswith("```"), f"fence leaked: {line!r}"

    def test_four_backtick_fence_consumed(self):
        """format_response handles 4-backtick fences via backreference."""
        text = "Intro.\n````python\nx = 1\n````\nDone."
        result = format_response(text)
        assert "Intro." in result
        assert "Done." in result
        assert "x" in result
        import re as _re
        plain = _re.sub(r"\x1b\[[0-9;]*m", "", result)
        for line in plain.splitlines():
            assert not line.strip().startswith("````"), f"4-backtick fence leaked: {line!r}"


# ---------------------------------------------------------------------------
# clean_command_output
# ---------------------------------------------------------------------------

class TestCleanCommandOutput:
    def test_strips_venv_paths(self):
        noisy = "/home/user/venv/lib/python3.11/site-packages/foo.py\nActual output"
        result = clean_command_output(noisy)
        assert "site-packages" not in result
        assert "Actual output" in result

    def test_keeps_meaningful_lines(self):
        output = "Build succeeded\n3 tests passed\nDone."
        result = clean_command_output(output)
        assert "Build succeeded" in result
        assert "3 tests passed" in result

    def test_empty_string(self):
        assert clean_command_output("") == ""

    def test_removes_excessive_blank_lines(self):
        output = "line1\n\n\n\n\nline2"
        result = clean_command_output(output)
        assert result.count("\n") < 3


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
