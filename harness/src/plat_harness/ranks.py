"""Permission ranks. A Rank 0 session cannot execute writes."""

from __future__ import annotations

from enum import IntEnum


class PermissionRank(IntEnum):
    EXPLAIN = 0
    RECOMMEND = 1
    DRAFT = 2
    EXECUTE = 3


# Tool-table labels in HARNESS_V2.md §4.4 → minimum rank required to call.
TOOL_RANK_LABEL = {
    "READ": PermissionRank.EXPLAIN,
    "RECOMMEND": PermissionRank.RECOMMEND,
    "DRAFT": PermissionRank.DRAFT,
    "WRITE": PermissionRank.EXECUTE,
    "READ/DRAFT": PermissionRank.DRAFT,
}
