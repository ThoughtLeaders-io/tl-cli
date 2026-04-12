#!/usr/bin/env python3
"""Shared PostgreSQL connection and name-resolution utilities for TL skills.

Used by: tl-data/pg_query.py, create-report/create_report.py,
         create-report/resolve_cross_refs.py

Env: TL_DATABASE_URI or DATABASE_URL
"""
import os
import sys

try:
    import psycopg
except ImportError:
    print("ERROR: psycopg not installed. Run: pip install psycopg[binary]", file=sys.stderr)
    sys.exit(1)


def get_db_uri():
    uri = os.environ.get("TL_DATABASE_URI", "") or os.environ.get("DATABASE_URL", "")
    if not uri:
        raise EnvironmentError("TL_DATABASE_URI or DATABASE_URL not set")
    return uri


def get_connection(readonly=False):
    """Return a psycopg connection.

    Args:
        readonly: If True, sets autocommit + readonly session (for SELECT-only scripts).
                  If False, returns a standard read-write connection.
    """
    conn = psycopg.connect(get_db_uri())
    if readonly:
        conn.autocommit = True
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


def resolve_brand_names(cur, names):
    """Resolve brand names to IDs via case-insensitive exact match.

    Returns (ids, unresolved) where unresolved is a list of names that had no match.
    """
    ids = []
    unresolved = []
    for name in names:
        cur.execute(
            "SELECT id FROM thoughtleaders_brand WHERE LOWER(name) = LOWER(%s) LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
        if row:
            ids.append(row[0])
        else:
            unresolved.append(name)
            print(f"WARNING: Brand '{name}' not found", file=sys.stderr)
    return ids, unresolved


def resolve_channel_names(cur, names):
    """Resolve channel names to IDs via case-insensitive exact match.

    Returns (ids, unresolved) where unresolved is a list of names that had no match.
    """
    ids = []
    unresolved = []
    for name in names:
        cur.execute(
            "SELECT id FROM thoughtleaders_channel WHERE LOWER(channel_name) = LOWER(%s) AND is_active = true LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
        if row:
            ids.append(row[0])
        else:
            unresolved.append(name)
            print(f"WARNING: Channel '{name}' not found", file=sys.stderr)
    return ids, unresolved
