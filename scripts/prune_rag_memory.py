"""Prune old entries from Ciel's long-term RAG memory (ChromaDB), by age.

Criterion 1 of 2 discussed: pure time-decay — delete anything older than N days,
no content judgment involved (deliberately NOT an LLM call: an AI judging "is this
memory still correct/useful" risks deleting something valid just as easily as
something stale, and 1000+ records is too many to hand-review). Criterion 2
(filtering out test-run pollution specifically) is a separate, later pass.

SAFE BY DEFAULT:
  - Dry-run unless --apply is passed: shows exactly what WOULD be deleted (count,
    date range, a few sample previews) without touching the database.
  - When --apply IS passed, the entire ciel_data/vector_memory/ directory is backed
    up first (timestamped copy) unless --no-backup is explicitly given — deleting
    long-term memory is hard to reverse, a stale-but-still-cited fact could matter
    later, and the backup costs almost nothing next to that risk.

Usage:
    python -m scripts.prune_rag_memory --older-than-days 45              # dry run
    python -m scripts.prune_rag_memory --older-than-days 45 --apply      # real delete (backs up first)
    python -m scripts.prune_rag_memory --older-than-days 45 --apply --no-backup
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Memory previews are arbitrary archived Ciel/user text and may contain emoji (the
# persona uses them, e.g. "🧠 [COGNITION]") — Windows consoles default to cp1252,
# which can't encode them and would crash the print. Same fix as test_integration.py.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import rag_manager as rm


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune RAG memories older than N days.")
    ap.add_argument("--older-than-days", type=int, required=True,
                    help="Delete memories with no activity older than this many days.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this flag, only reports what would happen.")
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip backing up ciel_data/vector_memory/ before a real delete. Not recommended.")
    args = ap.parse_args()

    if not rm._ensure_initialized():
        print("[prune_rag_memory] RAG is unavailable (missing deps or init failed) — nothing to prune.")
        return 1

    total_before = rm.get_memory_count()
    stale = rm.list_by_age(args.older_than_days)

    print(f"Total memories in vault:     {total_before}")
    print(f"Older than {args.older_than_days} days:{' ' * max(0, 10 - len(str(args.older_than_days)))} {len(stale)}")

    if not stale:
        print("Nothing to prune at this cutoff.")
        return 0

    by_date = Counter(m["date"] for m in stale)
    print("\nBy date:")
    for d in sorted(by_date):
        print(f"  {d}: {by_date[d]}")

    print("\nSample previews (first 5):")
    for m in stale[:5]:
        print(f"  [{m['date']}] {m['preview']}...")

    if not args.apply:
        print(f"\nDRY RUN — nothing deleted. Re-run with --apply to actually delete these {len(stale)} memories.")
        return 0

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = rm.VECTOR_DB_DIR.parent / f"vector_memory_backup_{stamp}"
        print(f"\nBacking up {rm.VECTOR_DB_DIR} -> {backup_dir} ...")
        shutil.copytree(rm.VECTOR_DB_DIR, backup_dir)
        print("Backup complete.")
    else:
        print("\n--no-backup given — skipping backup (not recommended).")

    ids = [m["id"] for m in stale]
    deleted = rm.delete_by_ids(ids)
    total_after = rm.get_memory_count()
    print(f"\nRequested deletion of {deleted} memories.")
    print(f"Vault count: {total_before} -> {total_after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
