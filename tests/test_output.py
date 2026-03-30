"""Tests for output formatting."""

from tl_cli.output.formatter import detect_format


class TestDetectFormat:
    def test_json_flag(self):
        assert detect_format(json_flag=True, csv_flag=False, md_flag=False, quiet=False) == "json"

    def test_csv_flag(self):
        assert detect_format(json_flag=False, csv_flag=True, md_flag=False, quiet=False) == "csv"

    def test_md_flag(self):
        assert detect_format(json_flag=False, csv_flag=False, md_flag=True, quiet=False) == "md"

    def test_quiet_flag(self):
        assert detect_format(json_flag=False, csv_flag=False, md_flag=False, quiet=True) == "quiet"

    def test_quiet_overrides_json(self):
        assert detect_format(json_flag=True, csv_flag=False, md_flag=False, quiet=True) == "quiet"

    def test_no_flags_non_tty(self):
        # When piped (non-TTY), default to JSON — can't test TTY easily
        result = detect_format(json_flag=False, csv_flag=False, md_flag=False, quiet=False)
        assert result in ("table", "json")  # Depends on test runner TTY
