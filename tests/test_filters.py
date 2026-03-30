"""Tests for the key:value filter parser."""

import pytest

from tl_cli.filters import parse_filters, split_id_and_filters


class TestParseFilters:
    def test_simple_filter(self):
        assert parse_filters(["status:sold"]) == {"status": "sold"}

    def test_multiple_filters(self):
        result = parse_filters(["status:sold", "brand:Nike", "since:2026-01"])
        assert result == {"status": "sold", "brand": "Nike", "since": "2026-01"}

    def test_quoted_value_double(self):
        assert parse_filters(['brand:"Hello World"']) == {"brand": "Hello World"}

    def test_quoted_value_single(self):
        assert parse_filters(["brand:'Hello World'"]) == {"brand": "Hello World"}

    def test_empty_list(self):
        assert parse_filters([]) == {}

    def test_hyphenated_key(self):
        assert parse_filters(["send-date:2026-01"]) == {"send-date": "2026-01"}

    def test_invalid_filter_exits(self):
        with pytest.raises(SystemExit):
            parse_filters(["not_a_filter"])

    def test_value_with_colon(self):
        # "url:https://example.com" should work — first colon splits
        result = parse_filters(["url:https://example.com"])
        assert result == {"url": "https://example.com"}


class TestSplitIdAndFilters:
    def test_id_only(self):
        id_val, filters = split_id_and_filters(["12345"])
        assert id_val == "12345"
        assert filters == {}

    def test_filters_only(self):
        id_val, filters = split_id_and_filters(["status:sold", "brand:Nike"])
        assert id_val is None
        assert filters == {"status": "sold", "brand": "Nike"}

    def test_id_and_filters(self):
        id_val, filters = split_id_and_filters(["12345", "status:sold"])
        assert id_val == "12345"
        assert filters == {"status": "sold"}

    def test_empty(self):
        id_val, filters = split_id_and_filters([])
        assert id_val is None
        assert filters == {}
