#!/usr/bin/env python3
"""tl-channel-authenticity orchestrator — two-phase (subagent in the middle).

Phase 1 (collect):
    analyze_channel.py "<ref>"
  Runs Group A, Group B, and Group C rule-based checks + comment scrape.
  Writes a state file and a separate llm_batch file. Prints a JSON envelope
  telling the skill orchestrator where the batch is and what to do next.

  --> The skill (Claude) then sends the llm_batch to the
      `youtube-comment-classifier` Haiku subagent and saves its JSON reply.

Phase 2 (finalize):
    analyze_channel.py --finalize <state.json> <llm_classifications.json>
  Folds the LLM verdict into Group C, computes the composite score, writes
  the final JSON + markdown report, prints the report.

`<ref>` = handle/@handle/URL/channel name/numeric id, or `adlink:<id>`.

Data access is entirely via the shared tl_data wrapper (the `tl` CLI). No database credentials
are used.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import _io_utf8  # noqa: F401  (side effect: forces UTF-8 stdout/stderr on Windows)
import anomaly_detector
import comment_analyzer
import engagement_ratios
import resolve_channel
import score as score_mod
import video_integrity

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2] / "_shared"))
import tl_data

import channel_lookup

OUT_DIR = Path("/tmp")


def collect(ref: str) -> dict:
    tl_data.preflight()
    data = resolve_channel.resolve(ref)
    ch = data["channel"]
    cid = int(ch["id"])

    ga = engagement_ratios.analyze(ch, data["longform"], data["shorts"])
    gb = anomaly_detector.analyze(ch, data["longform"], data.get("focus_video"))
    # video integrity (deleted/unlisted, intent-aware) folds into Group B
    vi = video_integrity.analyze(ch)
    gb["flags"].extend(vi["flags"])
    gb["subscore"] = max(gb["subscore"] - vi["penalty"], 0)
    gb["hard_fail"] = bool(gb.get("hard_fail")) or vi["hard_fail"]
    gb["metrics"]["video_integrity"] = vi["metrics"]
    scraped = comment_analyzer.collect(
        ch, data["longform"], data["shorts"], data.get("focus_video")
    )
    gc = comment_analyzer.analyze(ch, scraped)

    ts = time.strftime("%Y%m%d-%H%M%S")
    state = {
        "ref": ref,
        "channel": ch,
        "focus_video": data.get("focus_video"),
        "focus_adlink": data.get("focus_adlink"),
        "group_a": ga,
        "group_b": gb,
        "group_c": gc,
        "_scraped_index": {
            vid: {"views": b["video"].get("views"), "n": len(b["comments"])}
            for vid, b in scraped.items()
        },
    }
    state_path = OUT_DIR / f"channel_authenticity_{cid}_{ts}.state.json"
    batch_path = OUT_DIR / f"channel_authenticity_{cid}_{ts}.llmbatch.json"
    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    batch_path.write_text(json.dumps(gc.get("llm_batch", []), default=str), encoding="utf-8")

    return {
        "phase": "collect_done",
        "channel": {"id": cid, "name": ch.get("channel_name")},
        "state_path": str(state_path),
        "llm_batch_path": str(batch_path),
        "llm_batch_size": len(gc.get("llm_batch", [])),
        "channel_language": ch.get("language"),
        "next": (
            "Send the JSON array in llm_batch_path to the "
            "`youtube-comment-classifier` subagent (prepend a line: "
            f"'channel niche: cat {ch.get('content_category')}, language "
            f"{ch.get('language')}'). Save its JSON reply to a file, then run: "
            "analyze_channel.py --finalize <state_path> <llm_reply.json>"
        ),
    }


def finalize(state_path: str, llm_paths: list[str]) -> dict:
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    passes: list[list[dict]] = []
    for lp in llm_paths:
        try:
            c = json.loads(Path(lp).read_text(encoding="utf-8"))
            if isinstance(c, dict):
                c = c.get("classifications", [])
            if c:
                passes.append(c)
        except (json.JSONDecodeError, FileNotFoundError):
            continue

    comment_analyzer.apply_llm(state["group_c"], passes)
    state["score"] = score_mod.composite(
        state["group_a"], state["group_b"], state["group_c"]
    )

    cid = state["channel"]["id"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    final_json = OUT_DIR / f"channel_authenticity_{cid}_{ts}.final.json"
    report_md = OUT_DIR / f"channel_authenticity_{cid}_{ts}.report.md"
    final_json.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    import report as report_mod

    md = report_mod.render(state)
    report_md.write_text(md, encoding="utf-8")
    return {"final_json": str(final_json), "report_md": str(report_md), "markdown": md}


def main() -> None:
    ap = argparse.ArgumentParser(description="tl-channel-authenticity orchestrator")
    ap.add_argument("ref", nargs="?", help="channel handle/url/id or adlink:<id>")
    ap.add_argument(
        "--finalize",
        nargs="+",
        metavar="STATE LLM [LLM ...]",
        help="state.json followed by one or more classifier-output files "
        "(multiple = majority-voted for a stable organic share)",
    )
    args = ap.parse_args()

    try:
        if args.finalize:
            if len(args.finalize) < 2:
                ap.error("--finalize needs STATE and at least one LLM file")
            res = finalize(args.finalize[0], args.finalize[1:])
            print(res["markdown"])
            print(
                f"\n<!-- final JSON: {res['final_json']} · "
                f"report: {res['report_md']} -->"
            )
        elif args.ref:
            res = collect(args.ref)
            print(json.dumps(res, indent=2))
        else:
            ap.print_help()
            sys.exit(2)
    except channel_lookup.AmbiguousChannel as exc:
        print(
            json.dumps(
                {
                    "error": "ambiguous_channel",
                    "ref": exc.ref,
                    "candidates": [
                        {
                            "id": c.get("id"),
                            "name": c.get("channel_name"),
                            "subscribers": c.get("subscribers"),
                        }
                        for c in exc.candidates
                    ],
                    "message": (
                        "Multiple channels match. Present these candidates to "
                        "the user (highest subscribers first = most likely "
                        "intended) and re-run with the chosen id."
                    ),
                },
                indent=2,
                default=str,
            )
        )
        sys.exit(4)
    except tl_data.CliUnavailable as exc:
        print(
            json.dumps(
                {
                    "error": "cli_unavailable",
                    "message": str(exc),
                    "hint": "Authenticate the tl CLI: run `tl auth login` "
                    "(or set TL_API_KEY).",
                },
                indent=2,
            )
        )
        sys.exit(3)


if __name__ == "__main__":
    main()
