#!/usr/bin/env python3
"""The one home for the skill's two on-disk stores: the passage corpus and the
creator ledger. Imported by the sibling scripts, never run.

**Corpus** (``corpus.jsonl.gz``, also read as plain ``.jsonl``): one JSON
object per video, ``{"id", "title", "publication_date", "views", "duration",
"content_type", "cues": [[start_seconds, text], ...]}``; ``cues: []`` means
no transcript stored. Written gzipped (captions compress ~8x), read either
way, so an older plain corpus still opens. ``open_corpus`` /
``open_corpus_write`` are that convention's single home; ``cues`` parses the
timed-text caption XML into ``[(start, text)]``.

**Ledger** (``<channel_id>-facts.jsonl``): one JSONL file whose FIRST line
may be the meta record (an object whose ``schema`` starts with
``tl-creator-meta/``; see ``references/profile-spec.md``) and whose every
following line is one fact. Nothing iterates a ledger raw: ``read_ledger`` /
``iter_facts`` keep the header from being mistaken for a fact, and
``write_ledger`` writes header-first and atomically. A working
``facts.jsonl`` under ``.corpus/<id>/`` has no header; the reader returns
``meta=None`` for it.
"""
from __future__ import annotations

import contextlib
import gzip
import html
import io
import json
import os
import pathlib
import re
from typing import Iterator

# --------------------------------------------------------------------------- #
# the passage corpus
# --------------------------------------------------------------------------- #
CUE = re.compile(r'<text start="([\d.]+)"[^>]*>(.*?)</text>', re.S)
TAG = re.compile(r"<[^>]+>")

GZIP_MAGIC = b"\x1f\x8b"
# Compression is single-threaded and these files run to hundreds of megabytes,
# so the level is a real time/space trade. COMPRESS_LEVEL is the fast end for
# bulk window records; CORPUS_LEVEL is the slower, smaller end used for the
# durable corpus, whose write sits behind a network sweep that dwarfs the
# difference.
COMPRESS_LEVEL = 1
CORPUS_LEVEL = 6


def resolve_corpus(path: str | pathlib.Path) -> pathlib.Path:
    """The file a reader should actually open for ``path``.

    A caller may name either form. ``corpus.jsonl`` resolves to the gzipped
    ``corpus.jsonl.gz`` when that exists (what the fetch writes today) and
    otherwise stays as given, so a corpus fetched before the store was
    compressed keeps working untouched.
    """
    p = pathlib.Path(path)
    if p.suffix == ".gz":
        return p
    gz = p.with_name(p.name + ".gz")
    return gz if gz.exists() else p


def open_corpus(path: str | pathlib.Path):
    """Open a corpus (or windows) file for reading text, gzipped or not.

    The compression is sniffed from the file's magic bytes rather than its
    name, so a misnamed file reads correctly either way. Concatenated gzip
    members are transparent to the reader: it sees one continuous line stream.
    """
    p = resolve_corpus(path)
    with open(p, "rb") as probe:
        gzipped = probe.read(2) == GZIP_MAGIC
    if gzipped:
        return gzip.open(p, "rt", encoding="utf-8")
    return open(p, encoding="utf-8")


@contextlib.contextmanager
def open_corpus_write(path: str | pathlib.Path, *, append: bool = False,
                      level: int = COMPRESS_LEVEL):
    """Write text into a gzip file, deterministically.

    ``mtime=0``, an empty stored filename and a fixed compression level keep
    the bytes a pure function of the content — the same corpus compresses to
    the same file on every run. ``append=True`` starts a NEW gzip member at
    the end of an existing file; concatenated members are a valid gzip stream.
    """
    raw = open(path, "ab" if append else "wb")
    try:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                           compresslevel=level)
        with io.TextIOWrapper(gz, encoding="utf-8") as text:
            yield text
    finally:
        raw.close()


def _unescape(text: str) -> str:
    """Unescape to a fixed point: caption text is sometimes double-escaped
    (``&amp;#39;``), so a single pass leaves ``&#39;`` behind."""
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return text


def cues(raw: str | None) -> list[tuple[float, str]]:
    """Caption XML -> [(start_seconds, text)]."""
    out = []
    for start, body in CUE.findall(raw or ""):
        text = _unescape(TAG.sub(" ", body)).replace("\n", " ").strip()
        if text:
            out.append((float(start), re.sub(r"\s+", " ", text)))
    return out


# --------------------------------------------------------------------------- #
# the creator ledger
# --------------------------------------------------------------------------- #
META_SCHEMA_PREFIX = "tl-creator-meta/"


def is_meta(obj: object) -> bool:
    return isinstance(obj, dict) and str(obj.get("schema") or "").startswith(META_SCHEMA_PREFIX)


def _lines(path: pathlib.Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: not JSON ({exc})") from exc
            if not isinstance(obj, dict):
                raise SystemExit(f"{path}:{n}: expected an object, got {type(obj).__name__}")
            yield obj


def read_ledger(path: str | os.PathLike) -> tuple[dict | None, list[dict]]:
    """``(meta, facts)`` — ``meta`` is None when the file carries no header.
    A meta record anywhere but the first line is an error, not a fact."""
    path = pathlib.Path(path)
    meta: dict | None = None
    facts: list[dict] = []
    for i, obj in enumerate(_lines(path)):
        if is_meta(obj):
            if i != 0:
                raise SystemExit(f"{path}: meta record on line {i + 1}; it must be the first line")
            meta = obj
            continue
        facts.append(obj)
    return meta, facts


def iter_facts(path: str | os.PathLike) -> Iterator[dict]:
    for obj in _lines(pathlib.Path(path)):
        if not is_meta(obj):
            yield obj


def count_facts(path: str | os.PathLike) -> int:
    return sum(1 for _ in iter_facts(path))


def write_ledger(path: str | os.PathLike, meta: dict | None, facts: list[dict]) -> pathlib.Path:
    """Header first (when given), one fact per line, written to a sibling
    temp file and renamed into place so a reader never sees a half file."""
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".partial")
    with open(tmp, "w", encoding="utf-8") as fh:
        if meta is not None:
            if not is_meta(meta):
                raise ValueError("meta record must carry a tl-creator-meta/* schema")
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for fact in facts:
            if is_meta(fact):
                raise ValueError("a fact cannot carry a meta schema")
            fh.write(json.dumps(fact, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path
