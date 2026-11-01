"""Check the proposed DDL in disposable databases; not application acceptance tests."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "15-database-schema.sql"
STAMP = "2026-11-03T10:00:00.000000Z"
DIGEST = "a" * 64


def connect(path: str = ":memory:") -> sqlite3.Connection:
    connection = sqlite3.connect(path, autocommit=True, timeout=0.01)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA.read_text(encoding="utf-8"))
    return connection


class DesignSchemaChecks(unittest.TestCase):
    """Minimal SQL fixtures intentionally omit application-level JSON validation."""

    def setUp(self) -> None:
        self.db = connect()
        self.db.execute(
            "INSERT INTO catalog_imports VALUES (?, 0, 1, 'applied', ?, ?, 1, '{}', '{}', ?)",
            ("I1", "synthetic-design-check", DIGEST, STAMP),
        )
        self.db.execute("INSERT INTO catalog_meta VALUES (1, 1, 'I1', ?, 1, ?)", (DIGEST, STAMP))
        self.db.execute(
            "INSERT INTO materials (material_id, material_version, platform, account_id, "
            "content_sha256, is_current, last_import_id) VALUES ('M1', 1, 'p', 'a', ?, 1, 'I1')",
            (DIGEST,),
        )
        for request_id in ("R1", "R2", "R3"):
            self.db.execute(
                "INSERT INTO requests VALUES (?, '1.0', '{}', '{}', ?, ?)",
                (request_id, DIGEST, STAMP),
            )
            self.db.execute(
                "INSERT INTO request_items VALUES (?, 'M1', ?, 'Asia/Shanghai', 'hard', NULL)",
                (request_id, STAMP),
            )

    def tearDown(self) -> None:
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")
        self.db.close()

    def insert_draft(
        self,
        draft_id: str = "D1",
        request_id: str = "R1",
        validity: str = "active",
        account_id: str = "a",
        material_id: str = "M1",
    ) -> None:
        self.db.execute(
            "INSERT INTO drafts VALUES (?, ?, ?, 1, ?, 'p', ?, ?, 1, ?, ?, ?)",
            (draft_id, request_id, material_id, validity, account_id, STAMP, DIGEST, STAMP, STAMP),
        )

    def insert_history(self, draft_id: str = "D1", revision: int = 1) -> None:
        self.db.execute(
            "INSERT INTO draft_history VALUES "
            "(?, ?, ?, 1, 'I1', 1, ?, ?, 'passed', '1.0', '{}', ?)",
            (draft_id, revision, f"WRITE-{draft_id}-{revision}", DIGEST, DIGEST, STAMP),
        )

    def save_fixture(self, **kwargs: str) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.insert_draft(**kwargs)
        self.insert_history(kwargs.get("draft_id", "D1"))
        self.db.execute("COMMIT")

    def insert_confirmation(self, draft_id: str = "D1", revision: int = 1) -> None:
        self.db.execute(
            "INSERT INTO confirmations VALUES ('C1', ?, ?, '{}', ?, '[]', '{}', 'cli', ?)",
            (draft_id, revision, DIGEST, STAMP),
        )

    def test_thirteen_tables_and_integrity(self) -> None:
        tables = self.db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall()
        self.assertEqual(len(tables), 13)
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_business_unknown_is_nullable_but_enum_is_checked(self) -> None:
        self.assertIsNone(self.db.execute("SELECT copyright_status FROM materials").fetchone()[0])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE materials SET copyright_status='unknown'")

    def test_strict_type_rejects_noninteger_version(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE materials SET material_version='invalid'")

    def test_current_pointer_requires_history_at_commit(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.insert_draft()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("COMMIT")
        self.db.execute("ROLLBACK")
        self.assertEqual(self.db.execute("SELECT count(*) FROM drafts").fetchone()[0], 0)

    def test_deferred_pointer_allows_atomic_create(self) -> None:
        self.save_fixture()
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_logical_key_remains_unique_for_inactive_drafts(self) -> None:
        self.save_fixture(validity="stale")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_draft(draft_id="D2", validity="rejected")

    def test_active_capacity_and_stale_release(self) -> None:
        self.save_fixture()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_draft(draft_id="D2", request_id="R2")
        self.db.execute("UPDATE drafts SET validity='stale' WHERE draft_id='D1'")
        self.save_fixture(draft_id="D2", request_id="R2")

    def test_rejected_draft_does_not_occupy_slot(self) -> None:
        self.save_fixture(validity="rejected")
        self.save_fixture(draft_id="D2", request_id="R2")

    def test_distinct_account_can_share_time(self) -> None:
        self.save_fixture()
        self.save_fixture(draft_id="D2", request_id="R2", account_id="another")

    def test_schedule_can_reference_external_material(self) -> None:
        self.db.execute(
            "INSERT INTO schedule VALUES ('S1', 'p', 'a', ?, 'EXTERNAL-M0', 'I1')", (STAMP,)
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO schedule VALUES ('S2', 'p', 'a', ?, 'EXTERNAL-M2', 'I1')", (STAMP,)
            )

    def test_cross_table_capacity_requires_repository(self) -> None:
        self.save_fixture()
        self.db.execute(
            "INSERT INTO schedule VALUES ('S1', 'p', 'a', ?, 'EXTERNAL-M0', 'I1')", (STAMP,)
        )
        # This explicitly demonstrates the limit of the two separate unique indexes.
        self.assertEqual(self.db.execute("SELECT count(*) FROM schedule").fetchone()[0], 1)

    def test_unknown_material_request_allowed_but_draft_rejected(self) -> None:
        self.db.execute(
            "INSERT INTO request_items VALUES ('R1', 'MISSING', NULL, NULL, NULL, NULL)"
        )
        self.db.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_draft(material_id="MISSING")

    def test_draft_must_belong_to_request_item(self) -> None:
        self.db.execute("DELETE FROM request_items WHERE request_id='R1'")
        self.db.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_draft()

    def test_confirmation_references_exact_history(self) -> None:
        self.save_fixture()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_confirmation(revision=2)
        self.insert_confirmation()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_confirmation()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM draft_history")

    def test_event_cannot_use_another_draft_confirmation(self) -> None:
        self.save_fixture()
        self.save_fixture(draft_id="D2", request_id="R2", account_id="another")
        self.insert_confirmation()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO draft_events VALUES ('E1', 'D2', 1, 'accepted', 'active', "
                "'active', 'USER_ACCEPT', '{}', 'C1', NULL, NULL, ?)",
                (STAMP,),
            )

    def test_default_candidates_and_empty_candidates_are_distinct(self) -> None:
        self.db.execute("UPDATE request_items SET candidate_times_json='[]' WHERE request_id='R2'")
        values = self.db.execute(
            "SELECT candidate_times_json FROM request_items ORDER BY request_id"
        ).fetchall()
        self.assertEqual(values[:2], [(None,), ("[]",)])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE request_items SET candidate_times_json='{}'")

    def test_revision_keeps_old_confirmation_and_snapshot(self) -> None:
        self.save_fixture()
        self.insert_confirmation()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE drafts SET revision=2 WHERE draft_id='D1'")
        self.insert_history(revision=2)
        self.db.execute("COMMIT")
        self.assertEqual(self.db.execute("SELECT count(*) FROM draft_history").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT revision FROM confirmations").fetchone()[0], 1)

    def test_write_receipt_is_unique_and_rollback_is_atomic(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.insert_draft()
        self.insert_history()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO draft_history SELECT draft_id, 2, storage_write_id, "
                "catalog_revision, import_id, material_version, effective_material_sha256, "
                "semantic_sha256, review_status, payload_schema_version, payload_json, created_at "
                "FROM draft_history"
            )
        self.db.execute("ROLLBACK")
        self.assertEqual(self.db.execute("SELECT count(*) FROM drafts").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM draft_history").fetchone()[0], 0)

    def test_import_revision_pair_is_checked(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE catalog_meta SET revision=2")

    def test_active_conflict_query_uses_partial_index(self) -> None:
        plan = self.db.execute(
            "EXPLAIN QUERY PLAN SELECT draft_id FROM drafts WHERE platform=? AND account_id=? "
            "AND at_utc=? AND validity='active'",
            ("p", "a", STAMP),
        ).fetchall()
        self.assertIn("ux_drafts_active_slot", str(plan))

    def test_immediate_transaction_excludes_second_writer(self) -> None:
        workspace = SCHEMA.parent.parent.resolve()
        temporary_root = workspace / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="shadow-design-", dir=temporary_root) as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(temporary_root.resolve()))
            path = str(Path(directory) / "schema.sqlite")
            first = connect(path)
            second = sqlite3.connect(path, autocommit=True, timeout=0.01)
            try:
                first.execute("BEGIN IMMEDIATE")
                with self.assertRaises(sqlite3.OperationalError) as error:
                    second.execute("BEGIN IMMEDIATE")
                self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                first.execute("ROLLBACK")
                second.execute("BEGIN IMMEDIATE")
                second.execute("ROLLBACK")
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    print(f"SQLite {sqlite3.sqlite_version}; DDL design checks only", flush=True)
    unittest.main(verbosity=2)
