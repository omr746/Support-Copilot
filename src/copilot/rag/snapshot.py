"""Immutable snapshot store, deduplication, incremental versioning, and private backup store.

Note: Canonical implementation is now in `copilot.rag.crawler.snapshot`.
This module re-exports symbols for backward compatibility.
"""

from copilot.rag.crawler.snapshot import (
    SnapshotStore,
    generate_snapshot_id,
)

__all__ = [
    "SnapshotStore",
    "generate_snapshot_id",
]
