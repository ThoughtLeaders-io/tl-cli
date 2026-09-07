"""Tests for how billing refusals and withheld fields reach the user.

The server composes every refusal's story into `detail` (and names the code
in `code`); the CLI's job is to deliver it verbatim, add the CLI-native way
to act on it, and never bolt on a diagnosis of its own. These tests pin the
three places that used to do otherwise: the 402 branch (fixed copy that
overwrote the server's prepaid/subscription distinction), the 403 branch
(a blanket "your plan may not include this" on every 403, superuser gates
included), and successful envelopes whose premium fields were silently
withheld (`_upgrade_required`).
"""

import pytest

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.output.formatter import output


def _render(error: ApiError, capsys, code: int) -> str:
    with pytest.raises(SystemExit) as exc:
        handle_api_error(error)
    assert exc.value.code == code
    return " ".join(capsys.readouterr().err.split())  # undo console wrapping


class TestInsufficientCredits402:
    def test_server_detail_reaches_the_user_verbatim(self, capsys):
        # The subscription org's alternative — wait for the next top-up —
        # exists only in the server's sentence. Fixed client copy erased it.
        detail = (
            "Your organization is out of CLI credits for this top-up period. "
            "Buy credits at https://app.thoughtleaders.io/billing or wait for "
            "the next top-up."
        )
        out = _render(
            ApiError(402, detail, raw={"detail": detail, "code": "insufficient_credits"}),
            capsys,
            code=4,
        )
        assert "wait for the next top-up" in out
        # The CLI-native affordance stays alongside the server's sentence.
        assert "tl credits buy" in out

    def test_empty_detail_falls_back_to_the_generic_copy(self, capsys):
        out = _render(ApiError(402, "", raw=None), capsys, code=4)
        assert "Insufficient credits." in out
        assert "tl credits buy" in out


class TestAccessDenied403:
    def test_plan_gate_detail_is_verbatim_without_a_second_diagnosis(self, capsys):
        detail = "Snapshots require a paid plan"
        out = _render(
            ApiError(403, detail, raw={"detail": detail, "code": "plan_required"}),
            capsys,
            code=5,
        )
        assert "Access denied: Snapshots require a paid plan" in out
        # The detail already names the plan; a bolted-on suggestion would be
        # redundant here and wrong on every non-billing 403.
        assert "Your plan may not include" not in out

    def test_superuser_gate_gets_no_plan_upsell(self, capsys):
        out = _render(ApiError(403, "Superuser only", raw={"detail": "Superuser only"}), capsys, code=5)
        assert "Access denied: Superuser only" in out
        assert "plan" not in out.lower()


class TestHintsOnBillingStatuses:
    # A hint on a refusal is just as actionable as one on a 400 — the old
    # handler only rendered hints on the generic branch.
    def test_hint_renders_on_403(self, capsys):
        error = ApiError(403, "Query rejected: premium column(s)", raw={"hint": "Name the columns you want."})
        out = _render(error, capsys, code=5)
        assert "Hint: Name the columns you want." in out

    def test_hint_renders_on_429(self, capsys):
        error = ApiError(429, "Premium data quota reached", raw={"hint": "Assign another seat."})
        out = _render(error, capsys, code=3)
        assert "Hint: Assign another seat." in out


class TestUpgradeNoticeOnSuccess:
    def test_withheld_fields_are_announced_on_stderr(self, capsys):
        # ES attaches `_upgrade_required` to a 200 whose premium fields were
        # stripped; silently missing fields read as "no data exists".
        output(
            {
                "results": [{"id": 1, "title": "video"}],
                "_upgrade_required": {
                    "fields": ["transcript", "sponsored_brand_mentions"],
                    "message": (
                        "Audience demographics, channel outreach emails, video "
                        "transcripts and brand mentions are available on paid plans."
                    ),
                },
            },
            fmt="json",
        )
        captured = capsys.readouterr()
        err_out = " ".join(captured.err.split())
        assert "available on paid plans" in err_out
        assert "transcript, sponsored_brand_mentions" in err_out
        # The notice is a banner, not data: stdout stays valid JSON.
        assert "_upgrade_required" in captured.out

    def test_absent_key_prints_nothing(self, capsys):
        output({"results": [{"id": 1}]}, fmt="json")
        assert "paid plans" not in capsys.readouterr().err
