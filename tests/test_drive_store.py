"""Committed Drive references with a local fake and real SQLite."""

import io
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from agent_artifacts.drive_storage import DriveAccessError, DriveRevision, DriveTransientError
from agent_artifacts.store import (
    DriveArtifactError,
    DriveConflict,
    NoSuchProject,
    QuotaExceeded,
    SectionWithoutProject,
    StorageFull,
    Store,
    TooMany,
)


class FakeDrive:
    project_layout = False

    def __init__(self):
        self.files = {}
        self.failure = None
        self.entered = None
        self.resume = None
        self.lock = threading.Lock()
        self.after_send = None
        self.markers = {}
        self.generated = 0
        self.lose_response = False

    def generate_file_id(self):
        self.generated += 1
        return f"file{self.generated}"

    def inspect_operation(self, file_id, operation_id):
        if self.failure:
            raise self.failure
        if file_id not in self.files:
            raise DriveAccessError("unavailable")
        return self.markers.get(file_id) == operation_id, str(len(self.files[file_id]))

    def create(self, name, src, *, file_id, operation_id, before_write=None):
        data = src.read()
        if before_write:
            before_write()
        if self.failure:
            raise self.failure
        if self.after_send:
            self.after_send()
        with self.lock:
            self.files[file_id] = [data]
            self.markers[file_id] = operation_id
        if self.lose_response:
            raise DriveTransientError("lost response")
        return DriveRevision(file_id, "1")

    def replace(self, file_id, src, *, operation_id, before_write=None):
        data = src.read()
        if before_write:
            before_write()
        if self.failure:
            raise self.failure
        if self.after_send:
            self.after_send()
        if data == b"blocked":
            self.entered.set()
            assert self.resume.wait(5)
        with self.lock:
            self.files[file_id].append(data)
            self.markers[file_id] = operation_id
            revision = DriveRevision(file_id, str(len(self.files[file_id])))
        if self.lose_response:
            raise DriveTransientError("lost response")
        return revision

    @contextmanager
    def open_revision(self, ref, *, control=None):
        if control is not None:
            control.check()
        if self.failure:
            raise self.failure
        yield iter([self.files[ref.file_id][int(ref.revision_id) - 1]])


def put(store, drive, content, artifact_id=None, **kwargs):
    return store.put_drive(drive, io.BytesIO(content), owner="alice", name="a.txt", kind="txt",
                           size=len(content), artifact_id=artifact_id, **kwargs)


def read(store, drive, artifact_id):
    artifact, ref = store.get_drive(artifact_id, owner="alice")
    with drive.open_revision(ref) as chunks:
        return artifact, b"".join(chunks)


@pytest.fixture
def setup(tmp_path):
    clock = [100.0]
    store = Store(tmp_path, max_total_bytes=1000, user_quota_bytes=100, ttl_seconds=10,
                  clock=lambda: clock[0])
    store.add_agent("alice")
    return store, FakeDrive(), clock


def test_create_replace_and_read_committed_snapshot(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    comment = store.agent_comment(art.id, "review", None, owner="alice",
                                  validate_anchor=lambda anchor, pin: None)
    before, ref = store.get_drive(art.id)
    changed = put(store, drive, b"new", art.id)
    assert changed.id == art.id
    assert store.get_drive(art.id)[1] == DriveRevision(ref.file_id, "2")
    assert read(store, drive, art.id)[1] == b"new"
    with drive.open_revision(ref) as chunks:
        assert b"".join(chunks) == b"old"
    assert before.size == changed.size == 3
    assert store.comments(art.id, owner="alice")[1] == [comment]
    assert store.drive_attempts() == []
    assert list(store.storage._files.iterdir()) == []


@pytest.mark.parametrize("failure", [DriveAccessError("404"), DriveTransientError("timeout")])
def test_drive_failures_preserve_metadata_and_comments(setup, failure):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    comment = store.agent_comment(art.id, "review", None, owner="alice",
                                  validate_anchor=lambda anchor, pin: None)
    before = store.get_drive(art.id)
    drive.failure = failure
    with pytest.raises(type(failure)):
        put(store, drive, b"new", art.id)
    with pytest.raises(type(failure)):
        read(store, drive, art.id)
    assert store.purge_expired() == 0
    assert store.get_drive(art.id) == before
    assert store.comments(art.id, owner="alice")[1] == [comment]


def test_project_rules_and_owner_rechecked(setup):
    store, drive, clock = setup
    project = store.add_project("alice", "Project")
    art = put(store, drive, b"old", project=project.id, section="review")
    assert art.project == project.id and art.expires_at is None
    clock[0] = 1000
    assert put(store, drive, b"new", art.id).project == project.id
    assert store.get_drive(art.id, owner="not-alice") is None
    assert store.put_drive(drive, io.BytesIO(b"x"), owner="not-alice", name="x", kind="txt",
                           size=1, artifact_id=art.id) is None
    assert read(store, drive, art.id)[1] == b"new"


@pytest.mark.parametrize("ttl", [0, -10])
def test_nonpositive_ttl_rejected_before_drive_write_but_project_allowed(setup, ttl):
    store, drive, _ = setup
    store.set_settings(ttl_seconds=ttl)
    with pytest.raises(DriveArtifactError, match="immediately expire"):
        put(store, drive, b"old")
    assert drive.files == {} and store.drive_attempts() == []
    project = store.add_project("alice", "Project")
    assert put(store, drive, b"new", project=project.id).project == project.id


def test_failed_version_insert_rolls_back_publication_and_keeps_attempt(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    before = store.get_drive(art.id)
    with store._conn(write=True) as db:
        db.execute("CREATE TRIGGER reject_version BEFORE INSERT ON drive_versions "
                   "BEGIN SELECT RAISE(ABORT, 'version failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        put(store, drive, b"new content", art.id)
    assert store.get_drive(art.id) == before
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM drive_versions").fetchone()[0] == 1
    [attempt] = store.drive_attempts()
    assert attempt.returned_revision == "2"
    assert read(store, drive, art.id)[1] == b"old"


def test_version_backfill_migrates_existing_drive_reference_and_is_stable(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    with store._conn(write=True) as db:
        db.execute("DROP TABLE drive_moves")
        db.execute("DROP TABLE review_submissions")
        db.execute("DROP TABLE drive_project_renames")
        db.execute("DROP TABLE drive_renames")
        db.execute("DROP TABLE drive_destination")
        db.execute("DROP TABLE review_operations")
        db.execute("DROP TABLE review_documents")
        db.execute("DROP TABLE drive_names")
        db.execute("DROP TABLE drive_folders")
        db.execute("DROP TABLE drive_versions")
        db.execute("PRAGMA user_version = 10")
    reopened = Store(store._db.parent, max_total_bytes=1000, clock=lambda: 100.0)
    with reopened._conn() as db:
        [version] = db.execute("SELECT version_id FROM drive_versions").fetchone()
    snapshot, ref = reopened.get_drive_version(art.id, version)
    assert (snapshot.name, snapshot.kind, snapshot.size) == ("a.txt", "txt", 3)
    with drive.open_revision(ref) as chunks:
        assert b"".join(chunks) == b"old"
    reopened_again = Store(store._db.parent, max_total_bytes=1000, clock=lambda: 100.0)
    assert reopened_again.get_drive_version(art.id, version) == (snapshot, ref)


def test_initial_quota_failure_creates_no_remote_file(setup):
    store, drive, _ = setup
    with pytest.raises(QuotaExceeded):
        put(store, drive, b"x" * 101)
    assert store.list("alice") == []
    assert drive.files == {}


@pytest.mark.parametrize(("kwargs", "error"), [
    ({"project": "missing"}, NoSuchProject),
    ({"section": "orphan"}, SectionWithoutProject),
])
def test_initial_invalid_placement_has_no_remote_mutation(setup, kwargs, error):
    store, drive, _ = setup
    with pytest.raises(error):
        put(store, drive, b"old", **kwargs)
    assert drive.files == {}


def test_initial_project_file_limit_has_no_remote_mutation(setup, monkeypatch):
    store, drive, _ = setup
    project = store.add_project("alice", "Project")
    put(store, drive, b"old", project=project.id)
    monkeypatch.setattr("agent_artifacts.store.MAX_PROJECT_FILES", 1)
    with pytest.raises(TooMany):
        put(store, drive, b"new", project=project.id)
    assert drive.files == {"file1": [b"old"]}


def test_initial_missing_owner_total_capacity_and_replace_quota(setup):
    store, drive, _ = setup
    with pytest.raises(sqlite3.IntegrityError):
        store.put_drive(drive, io.BytesIO(b"x"), owner="absent", name="x", kind="txt", size=1)
    store.set_settings(max_total_bytes=2)
    with pytest.raises(StorageFull):
        put(store, drive, b"abc")
    assert drive.files == {}
    store.set_settings(max_total_bytes=1000)
    art = put(store, drive, b"old")
    with pytest.raises(QuotaExceeded):
        put(store, drive, b"x" * 101, art.id)
    assert drive.files == {"file1": [b"old"]}


@pytest.mark.parametrize("change", ["expiry", "owner", "reference", "quota"])
def test_changes_during_spooling_cause_no_remote_mutation(setup, change):
    store, drive, clock = setup
    art = put(store, drive, b"old")
    store.add_agent("bob")

    class ChangingSource(io.BytesIO):
        def read(self, size=-1):
            if change == "expiry":
                clock[0] = 111
            elif change == "owner":
                with store._conn(write=True) as db:
                    db.execute("UPDATE artifacts SET owner = 'bob' WHERE id = ?", (art.id,))
            elif change == "reference":
                with store._conn(write=True) as db:
                    db.execute(
                        "UPDATE drive_refs SET revision_id = 'changed' WHERE artifact_id = ?",
                        (art.id,),
                    )
            else:
                store.set_settings(user_quota_bytes=2)
            return super().read(size)

    def call():
        return store.put_drive(drive, ChangingSource(b"new"), owner="alice", name="a.txt",
                               kind="txt", size=3, artifact_id=art.id)
    if change in ("expiry", "owner"):
        assert call() is None
    else:
        with pytest.raises(DriveConflict if change == "reference" else QuotaExceeded):
            call()
    assert drive.files == {"file1": [b"old"]}


@pytest.mark.parametrize("change", ["owner", "quota"])
def test_create_changes_during_spooling_create_no_remote_file(setup, change):
    store, drive, _ = setup

    class ChangingSource(io.BytesIO):
        def read(self, size=-1):
            if change == "owner":
                assert store.remove_agent("alice")
            else:
                store.set_settings(user_quota_bytes=2)
            return super().read(size)

    with pytest.raises(sqlite3.IntegrityError if change == "owner" else QuotaExceeded):
        store.put_drive(drive, ChangingSource(b"new"), owner="alice", name="a", kind="txt", size=3)
    assert drive.files == {}


def test_creation_quota_race_after_send_does_not_publish(setup):
    store, drive, _ = setup
    drive.after_send = lambda: store.set_settings(user_quota_bytes=2)
    with pytest.raises(QuotaExceeded):
        put(store, drive, b"new")
    assert store.list("alice") == []
    assert drive.files == {"file1": [b"new"]}


def test_final_quota_failure_keeps_old_published_revision(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    old = store.get_drive(art.id)
    drive.after_send = lambda: store.set_settings(user_quota_bytes=2)
    with pytest.raises(QuotaExceeded):
        put(store, drive, b"new", art.id)
    assert store.get_drive(art.id) == old
    assert read(store, drive, art.id)[1] == b"old"
    assert drive.files[old[1].file_id][-1] == b"new"


@pytest.mark.parametrize("replacing", [False, True])
def test_post_write_clock_change_cannot_split_metadata_and_reference(setup, monkeypatch, replacing):
    store, drive, clock = setup
    old = put(store, drive, b"old") if replacing else None
    original = store._artifact

    def jump(db, artifact_id, owner=None, *, now=None):
        assert now == 100
        clock[0] = 111
        return original(db, artifact_id, owner, now=now)

    monkeypatch.setattr(store, "_artifact", jump)
    result = put(store, drive, b"new content", old.id if old else None)
    assert result is not None
    clock[0] = 100
    artifact, ref = store.get_drive(result.id)
    assert artifact.size == len(b"new content")
    assert ref.revision_id == ("2" if replacing else "1")
    assert read(store, drive, result.id)[1] == b"new content"


def test_unexpected_post_write_missing_artifact_rolls_back(setup, monkeypatch):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    before = store.get_drive(art.id)
    monkeypatch.setattr(store, "_artifact", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="written artifact missing"):
        put(store, drive, b"different", art.id)
    assert store.get_drive(art.id) == before
    assert drive.files[before[1].file_id] == [b"old", b"different"]


def test_ownership_change_after_send_does_not_publish(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    store.add_agent("bob")

    def change_owner():
        with store._conn(write=True) as db:
            db.execute("UPDATE artifacts SET owner = 'bob' WHERE id = ?", (art.id,))

    drive.after_send = change_owner
    assert put(store, drive, b"new", art.id) is None
    artifact, ref = store.get_drive(art.id, owner="bob")
    assert artifact.size == 3 and ref.revision_id == "1"
    assert drive.files[ref.file_id] == [b"old", b"new"]


@pytest.mark.parametrize("replace", [False, True])
def test_lost_response_recovered_after_restart_without_mutation(setup, replace):
    store, drive, _ = setup
    art = put(store, drive, b"old") if replace else None
    old_ref = store.get_drive(art.id)[1] if art else None
    drive.lose_response = True
    with pytest.raises(DriveTransientError):
        put(store, drive, b"new", art.id if art else None)
    [attempt] = store.drive_attempts()
    assert attempt.returned_revision is None
    assert attempt.previous_revision == (old_ref.revision_id if old_ref else None)
    assert attempt.file_id in drive.files
    drive.lose_response = False
    restarted = Store(store._db.parent, max_total_bytes=1000, clock=lambda: 100.0)
    assert restarted.drive_attempts() == [attempt]
    observation = restarted.inspect_drive_attempt(drive, attempt)
    assert observation.status == "observed"
    assert observation.current_head == ("2" if replace else "1")
    assert restarted.drive_attempts() == [attempt]
    if art:
        assert restarted.get_drive(art.id)[1] == old_ref
        with pytest.raises(DriveConflict):
            put(restarted, drive, b"retry", art.id)
        assert drive.files[attempt.file_id] == [b"old", b"new"]
    else:
        assert restarted.get(attempt.artifact_id) is None
        assert drive.files[attempt.file_id] == [b"new"]


def test_attempt_inspection_missing_marker_and_access_denial(setup):
    store, drive, _ = setup
    drive.lose_response = True
    with pytest.raises(DriveTransientError):
        put(store, drive, b"new")
    [attempt] = store.drive_attempts()
    drive.markers[attempt.file_id] = "different"
    observed = store.inspect_drive_attempt(drive, attempt)
    assert observed.status == "unconfirmed"
    drive.failure = DriveAccessError("denied or absent")
    assert store.inspect_drive_attempt(drive, attempt).status == "unavailable"
    assert store.drive_attempts() == [attempt]


def test_known_unpublished_revision_remains_durable(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    drive.after_send = lambda: store.set_settings(user_quota_bytes=2)
    with pytest.raises(QuotaExceeded):
        put(store, drive, b"new", art.id)
    [attempt] = store.drive_attempts()
    assert (attempt.previous_revision, attempt.returned_revision) == ("1", "2")
    assert store.inspect_drive_attempt(drive, attempt).status == "observed"
    restarted = Store(store._db.parent, max_total_bytes=1000, clock=lambda: 100.0)
    assert restarted.drive_attempts() == [attempt]
    assert read(restarted, drive, art.id)[1] == b"old"


def test_failed_commit_rolls_back_metadata_and_reference(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    old = store.get_drive(art.id)
    with store._conn(write=True) as db:
        db.execute("CREATE TRIGGER reject_ref BEFORE UPDATE ON drive_refs "
                   "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        put(store, drive, b"new content", art.id)
    assert store.get_drive(art.id) == old
    assert read(store, drive, art.id)[1] == b"old"
    assert drive.files[old[1].file_id][-1] == b"new content"


def test_concurrent_replace_detected_without_sqlite_lock_during_remote_call(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    drive.entered, drive.resume = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(put, store, drive, b"blocked", art.id)
        try:
            assert drive.entered.wait(5)
            store.add_agent("bob")
            with pytest.raises(DriveConflict):
                put(store, drive, b"winner", art.id)
            unrelated = put(store, drive, b"other")
            assert read(store, drive, unrelated.id)[1] == b"other"
        finally:
            drive.resume.set()
        assert future.result(timeout=5) is not None
    assert read(store, drive, art.id)[1] == b"blocked"
    assert store.drive_attempts() == []


def test_expiry_during_upload_is_not_republished_or_purged(setup):
    store, drive, clock = setup
    art = put(store, drive, b"old")
    drive.entered, drive.resume = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(put, store, drive, b"blocked", art.id)
        try:
            assert drive.entered.wait(5)
            clock[0] = 111
            assert store.purge_expired() == 0
        finally:
            drive.resume.set()
        assert future.result(timeout=5) is None
    clock[0] = 100
    assert read(store, drive, art.id)[1] == b"old"


def test_disk_cannot_replace_and_no_deletion_policy(setup):
    store, drive, _ = setup
    art = put(store, drive, b"old")
    with pytest.raises(DriveArtifactError):
        store.put(io.BytesIO(b"new"), owner="alice", name="a", kind="txt", size=3,
                  artifact_id=art.id)
    with pytest.raises(sqlite3.IntegrityError):
        store.delete(art.id, owner="alice")
    assert read(store, drive, art.id)[1] == b"old"


def test_drive_cannot_replace_disk_artifact(setup):
    store, drive, _ = setup
    art = store.put(io.BytesIO(b"disk"), owner="alice", name="a", kind="txt", size=4)
    assert put(store, drive, b"new", art.id) is None
    assert not drive.files
    assert (store.storage._files / art.id).read_bytes() == b"disk"
