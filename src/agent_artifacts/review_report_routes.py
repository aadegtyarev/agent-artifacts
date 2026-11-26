"""Explicit review report commands for the artifact-owning agent."""

from __future__ import annotations

import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .api import ApiError, error, json_body, require_agent
from .credentials import hash_key
from .docs_report import DocsReport, ReportConflict, ReportLimitError
from .drive_storage import DriveAccessError, DriveError, _segment
from .report_compaction_dispatch import CompactionUnresolved
from .review_reports import ReportNotFound, ReportPending, ReportResult, ReviewReports
from .review_submissions import close_source, read_evidence
from .store import ID_RE, Store
from .workspace_drive import DriveGrantRequired, WorkspaceDrive

_ROUND = frozenset({"key", "version_id", "goal", "decision", "reasons", "open_items"})
_CLOSE = frozenset({"key", "closed", "outcome", "rows"})
_FINAL = frozenset({"closure_id", "outcome", "rows"})
_RESULTS = {"round": ("appended", "already_present"), "close": ("compacted", "already_compacted"),
            "final": ("finalized", "already_finalized")}
_NO_STORE = {"Cache-Control": "no-store"}


def report_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{_segment(document_id)}/edit"


def report_routes(store: Store, public_url: str, drive: WorkspaceDrive, *, read_only: bool = False,
                  include_legacy_probe_routes: bool = False) -> list[Route]:
    journal = ReviewReports(store, public_url)

    def perform(artifact: str, agent: str, credential_hash: str, kind: str, fields: dict) -> ReportResult:
        subject = store.accounts.google_subject_for_agent(agent, credential_hash)
        if subject is None:
            raise ApiError(403, "drive_account_required", "Link this agent to your Workspace account.",
                           account_url=public_url + "/account")
        prepare = {"round": journal.prepare_round, "close": journal.prepare_close, "final": journal.prepare_final}
        ticket = prepare[kind](artifact, agent, fields)
        if ticket.confirmed_doc is not None:
            return ReportResult(ticket.confirmed_doc, False)
        with drive.open(subject) as storage:
            return journal.execute(ticket, DocsReport(storage, public_url=public_url, artifact_file_id=ticket.file_id))

    def problem(status: int, code: str, message: str, **extra) -> JSONResponse:
        response = error(status, code, message, **extra)
        response.headers.update(_NO_STORE)
        return response

    def read_source(artifact_id: str, agent: str, credential_hash: str,
                    closure: str | None, thread: int | None) -> dict | None:
        with store._conn() as db:
            # Check the active credential in the same read as the ownership
            # query; never serve another owner's closing publication.
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM agents WHERE name=? AND key_hash=?",
                              (agent, credential_hash)).fetchone():
                return None
            if closure is not None and thread is not None:
                return read_evidence(db, artifact_id, agent, closure, thread)
            return close_source(db, artifact_id, agent, public_url)

    async def source(request: Request) -> Response:
        try:
            agent = require_agent(store, request)
        except ApiError as exc:
            return problem(exc.status, exc.code, exc.message, **exc.extra)
        closure, thread = None, None
        if "n" in request.path_params:
            value = request.path_params["n"]
            closure = request.query_params.get("closure_id", "")
            if not re.fullmatch(r"[1-9][0-9]{0,8}", value) or not re.fullmatch(ID_RE, closure):
                return problem(400, "bad_request", "Expected a closure ID and published thread number.")
            thread = int(value)
        supplied = request.headers["authorization"].partition(" ")[2].strip()
        found = await run_in_threadpool(read_source, request.path_params["id"], agent, hash_key(supplied),
                                       closure, thread)
        if found is None:
            return problem(404, "not_found", "No captured evidence or owned closure found.")
        return JSONResponse(found, headers=_NO_STORE)

    def command(kind: str):
        async def handle(request: Request) -> Response:
            try:
                agent = require_agent(store, request)
                if read_only:
                    return problem(503, "drive_read_only", "Google Drive writes are paused.")
                supplied = request.headers["authorization"].partition(" ")[2].strip()
                required = {"round": _ROUND, "close": _CLOSE, "final": _FINAL}[kind]
                allowed = required | {"threads"} if kind == "round" else required
                fields = await json_body(request, allowed, required, reject_ambiguous=True)
                result = await run_in_threadpool(perform, request.path_params["id"], agent,
                                                 hash_key(supplied), kind, fields)
            except ApiError as exc:
                return problem(exc.status, exc.code, exc.message, **exc.extra)
            except ReportNotFound:
                return problem(404, "not_found", "No eligible owned report or published version.")
            except ReportLimitError:
                return problem(413, "report_limit", "Report exceeds the safe size limit.")
            except ReportPending:
                return problem(409, "report_pending", "The report is closed or an operation is unresolved. "
                               "For an unresolved operation, retry only the same request.")
            except ReportConflict:
                return problem(409, "report_conflict", "The report, its source, or the operation key conflicts.")
            except DriveGrantRequired:
                return problem(403, "drive_authorization_required", "Authorize Google Drive for the linked account.",
                               authorization_url=public_url + "/auth/drive-access?return=/account")
            except DriveAccessError:
                return problem(403, "drive_access_denied", "Ask your administrator to check Google Drive access.")
            except (DriveError, CompactionUnresolved):
                return problem(503, "report_unavailable", "The report could not be verified. Retry only the same request.")
            except (ValueError, TypeError):
                return problem(400, "bad_request", "Invalid report or unpublished evidence.")
            return JSONResponse({"status": _RESULTS[kind][not result.newly_confirmed],
                                 "report_url": report_url(result.document_id)},
                                headers=_NO_STORE)
        return handle

    base = "/api/v1/artifacts/{id:artifact_id}/review-report"
    routes = [Route(base + "/source", source),
              Route(base + "/evidence/{n}", source),
              Route(base + "/finalize", command("final"), methods=["POST"])]
    # Local regression exercises the earlier staged protocol. The deployed
    # app mounts only closure-bound finalization, with no config to opt in.
    if include_legacy_probe_routes:
        routes.extend([Route(base + "/rounds", command("round"), methods=["POST"]),
                       Route(base + "/compact", command("close"), methods=["POST"])])
    return routes
