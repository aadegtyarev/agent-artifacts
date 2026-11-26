"""Explicit compaction dispatch used by Store and the optional owner API.

The caller must independently establish closure, authorization, mapped document
identity, sharing policy, durable operation inputs and exclusive serialization
against append/compaction. `before_write` must persist a durable dispatch claim.
After any possible dispatch, never call this function again for the same operation;
use the separate read-only verifier with the same persisted inputs instead.
Folder checks are not an atomic containment or ACL lock. Real Docs defaults,
table behavior and permissions have not been validated against Google.
"""

from __future__ import annotations

from collections.abc import Callable

from .docs_report import DocsReport
from .drive_storage import DriveError, _segment
from .report_compaction import CompactionPlan, CompactionRow, VerifiedReportSnapshot, plan_compaction
from .report_compaction_readback import verify_compacted_report
from .report_source import ExpectedReportRound, ReportPresentation, read_report_source


class CompactionUnresolved(Exception):
    """A batch may have been dispatched, but exact current results are not confirmed."""


def compact_report(report: DocsReport, document_id: str, artifact_id: str, *,
                   expected_rounds: tuple[ExpectedReportRound, ...], operation_key: str,
                   summary: str, rows: tuple[CompactionRow, ...],
                   before_write: Callable[[str], None],
                   presentation: ReportPresentation | None = None) -> CompactionPlan:
    """Verify source, plan, claim, dispatch once, then verify exact current result.

    Success means the complete expected table was read back. Any provider error
    or mismatch after dispatch is unresolved; this function does not auto-replay.
    The hook receives the verified tab ID, runs after preflight, and may abort by
    raising. The caller must persist that tab with the possible-dispatch claim.
    """
    if not callable(before_write):
        raise ValueError("caller-managed before_write claim required")
    snapshot = read_report_source(report, document_id, artifact_id, expected_rounds=expected_rounds,
                                  presentation=presentation)
    return dispatch_verified(report, snapshot, operation_key=operation_key, summary=summary, rows=rows,
                             before_write=before_write, presentation=presentation)


def dispatch_verified(report: DocsReport, snapshot: VerifiedReportSnapshot, *, operation_key: str,
                      summary: str, rows: tuple[CompactionRow, ...], before_write: Callable[[str], None],
                      presentation: ReportPresentation | None) -> CompactionPlan:
    """Write only against a caller-verified book, tab, revision and version set."""
    if not callable(before_write):
        raise ValueError("caller-managed before_write claim required")
    plan = plan_compaction(snapshot, public_url=report._origin, operation_key=operation_key,
                           summary=summary, rows=rows)
    before_write(snapshot.tab_id)
    try:
        report._json_request("POST", f"https://docs.googleapis.com/v1/documents/"
                             f"{_segment(snapshot.document_id)}:batchUpdate", json=plan.batch)
        if not verify_compacted_report(report, snapshot.document_id, snapshot.artifact_id, tab_id=snapshot.tab_id,
                                       operation_key=operation_key, summary=summary, rows=rows,
                                       presentation=presentation):
            raise CompactionUnresolved("compaction outcome is not confirmed")
    except (DriveError, ValueError):
        raise CompactionUnresolved("compaction outcome is not confirmed") from None
    return plan
