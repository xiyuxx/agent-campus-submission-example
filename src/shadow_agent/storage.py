"""The sole SQL boundary: short transactions, trusted evidence and stable logical keys."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from .auditing import Auditor, LocalMetadataAuditor
from .common import BusinessError, digest, dumps, new_id, now, utc
from .contracts import (
    FACT_FIELDS,
    Catalog,
    Confirmation,
    Material,
    MaterialSnapshot,
    Request,
    RequestItem,
    Rule,
    RuleCheck,
    ScheduleOptions,
    SlotObservation,
)
from .policy import check_rules, confirmation_definitions, semantic_hash


def load_catalog(directory: Path) -> Catalog:
    try:
        return Catalog.model_validate(
            {
                name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8-sig"))
                for name in ("materials", "supplements", "rules", "schedule")
            }
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise BusinessError("INVALID_CATALOG", "四个资料文件必须完整、可读且为合法 JSON") from exc


class Repository:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], str] = now,
        fault: str | None = None,
        busy_ms: int = 2000,
        auditor: Auditor | None = None,
    ):
        self.path = Path(path)
        self.clock = clock
        self.fault = fault
        self.busy_ms = min(max(busy_ms, 0), 2000)
        self.auditor = auditor or LocalMetadataAuditor()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, autocommit=True, timeout=self.busy_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_ms}")
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            connection.close()
            raise BusinessError("UNSUPPORTED_SQLITE", "SQLite 外键不可用")
        return connection

    @contextmanager
    def transaction(self, *, write: bool = False):
        connection = self._connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != 1:
                raise BusinessError("SCHEMA_VERSION", "请先 seed/migrate 初始化；不支持此结构版本")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            try:
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                if write and not connection.in_transaction:
                    raise BusinessError("COMMIT_UNKNOWN", "提交回执异常，需独立核对") from exc
                raise
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                return
            if version != 0:
                raise BusinessError("SCHEMA_VERSION", "数据库版本高于本程序支持范围")
            if connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchone():
                raise BusinessError("SCHEMA_VERSION", "拒绝覆盖来源未知的非空数据库")
            if sqlite3.sqlite_version_info < (3, 38, 0):
                raise BusinessError("UNSUPPORTED_SQLITE", "需要支持 STRICT 和 JSON 的 SQLite")
            connection.execute("SELECT json_valid('{}')")
            schema = (
                files("shadow_agent")
                .joinpath("migrations/001_initial.sql")
                .read_text(encoding="utf-8")
            )
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + schema + "\nPRAGMA user_version=1;COMMIT;"
            )
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BusinessError("SCHEMA_INVALID", "外键结构检查失败")
        finally:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            connection.close()

    @staticmethod
    def _meta(connection) -> dict:
        row = connection.execute("SELECT * FROM catalog_meta WHERE singleton_id=1").fetchone()
        if not row:
            raise BusinessError("CATALOG_UNINITIALIZED", "请先导入资料目录")
        return dict(row)

    def _event(
        self,
        connection,
        draft: dict,
        kind: str,
        target: str,
        reason: str,
        *,
        operation_id=None,
        import_id=None,
        run_id=None,
        details=None,
    ) -> None:
        connection.execute(
            "INSERT INTO draft_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("EV"),
                draft["draft_id"],
                draft["revision"],
                kind,
                draft.get("validity"),
                target,
                reason,
                dumps(details or {}),
                operation_id,
                import_id,
                run_id,
                self.clock(),
            ),
        )

    def apply_catalog_snapshot(self, catalog: Catalog, *, update: bool = False) -> dict:
        self.initialize()
        canonical = catalog.canonical()
        source_hash = digest(canonical)
        with self.transaction(write=True) as connection:
            row = connection.execute("SELECT * FROM catalog_meta WHERE singleton_id=1").fetchone()
            if row and not update:
                raise BusinessError("CATALOG_EXISTS", "已有资料；更新需显式使用 --update")
            previous = row["revision"] if row else 0
            changed = not row or row["source_sha256"] != source_hash
            revision = previous + int(changed)
            import_id = new_id("IMP")
            changes = {}
            for table, key in (
                ("materials", "material_id"),
                ("supplements", "material_id"),
                ("rules", "rule_id"),
            ):
                old = {
                    x[key]: x["content_sha256"]
                    for x in connection.execute(
                        f"SELECT {key}, content_sha256 FROM {table} WHERE is_current=1"
                    )
                }
                new = {x[key]: digest(x) for x in canonical[table]}
                changes[table] = {
                    "added": sorted(new.keys() - old.keys()),
                    "removed": sorted(old.keys() - new.keys()),
                    "modified": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
                }
            for table, entries, key in (
                ("schedule", canonical["schedule"]["entries"], lambda x: x["entry_id"]),
                (
                    "allowed_slots",
                    canonical["schedule"]["allowed_slots"],
                    lambda x: dumps([x["platform"], x["account_id"], x["at"]]),
                ),
            ):
                old = {}
                for existing in connection.execute(f"SELECT * FROM {table}"):
                    value = {k: v for k, v in dict(existing).items() if k != "import_id"}
                    value["at"] = value.pop("at_utc")
                    old[key(value)] = digest(value)
                new = {key(x): digest(x) for x in entries}
                changes[table] = {
                    "added": sorted(new.keys() - old.keys()),
                    "removed": sorted(old.keys() - new.keys()),
                    "modified": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
                }
            active = (
                [
                    dict(x)
                    for x in connection.execute("SELECT * FROM drafts WHERE validity='active'")
                ]
                if changed
                else []
            )
            changes["staled_drafts"] = [x["draft_id"] for x in active]
            manifest = {
                name: {"sha256": digest(value), "count": len(value)}
                for name, value in canonical.items()
                if name != "schedule"
            }
            manifest["schedule"] = {
                "sha256": digest(canonical["schedule"]),
                "count": len(catalog.schedule.entries),
                "allowed_slots_count": len(catalog.schedule.allowed_slots),
            }
            connection.execute(
                "INSERT INTO catalog_imports VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    previous,
                    revision,
                    "applied" if changed else "noop",
                    "synthetic-fixtures",
                    source_hash,
                    catalog.schedule.schedule_version,
                    dumps(manifest),
                    dumps(changes),
                    self.clock(),
                ),
            )
            if changed:
                for table in ("materials", "supplements", "rules"):
                    connection.execute(
                        f"UPDATE {table} SET is_current=0, last_import_id=?", (import_id,)
                    )
                for value in canonical["materials"]:
                    columns = list(value) + ["content_sha256", "is_current", "last_import_id"]
                    self._upsert(
                        connection,
                        "materials",
                        "material_id",
                        columns,
                        [*value.values(), digest(value), 1, import_id],
                    )
                for value in canonical["supplements"]:
                    data = {
                        "material_id": value["material_id"],
                        "supplement_version": value["supplement_version"],
                        "source_id": value["source_id"],
                        "fields_json": dumps(value["fields"]),
                        "content_sha256": digest(value),
                        "is_current": 1,
                        "last_import_id": import_id,
                    }
                    self._upsert(
                        connection, "supplements", "material_id", list(data), list(data.values())
                    )
                for value in canonical["rules"]:
                    data = {
                        **value,
                        "value_json": dumps(value["value"]),
                        "content_sha256": digest(value),
                        "is_current": 1,
                        "last_import_id": import_id,
                    }
                    del data["value"]
                    self._upsert(connection, "rules", "rule_id", list(data), list(data.values()))
                connection.execute("DELETE FROM schedule")
                connection.execute("DELETE FROM allowed_slots")
                for entry in canonical["schedule"]["entries"]:
                    connection.execute(
                        "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
                        (
                            entry["entry_id"],
                            entry["platform"],
                            entry["account_id"],
                            entry["at"],
                            entry["material_id"],
                            import_id,
                        ),
                    )
                for slot in canonical["schedule"]["allowed_slots"]:
                    connection.execute(
                        "INSERT INTO allowed_slots VALUES (?,?,?,?)",
                        (
                            slot["platform"],
                            slot["account_id"],
                            slot["at"],
                            import_id,
                        ),
                    )
                for draft in active:
                    self._stale(connection, draft, "CATALOG_CHANGED", import_id=import_id)
                connection.execute(
                    "INSERT INTO catalog_meta VALUES (1,?,?,?,?,?) ON CONFLICT(singleton_id) "
                    "DO UPDATE SET revision=excluded.revision, "
                    "current_import_id=excluded.current_import_id, "
                    "source_sha256=excluded.source_sha256, "
                    "schedule_version=excluded.schedule_version, "
                    "updated_at=excluded.updated_at",
                    (
                        revision,
                        import_id,
                        source_hash,
                        catalog.schedule.schedule_version,
                        self.clock(),
                    ),
                )
        return {
            "import_id": import_id,
            "catalog_revision": revision,
            "outcome": "applied" if changed else "noop",
            "changes": changes,
        }

    def preview_catalog_snapshot(self, catalog: Catalog) -> dict:
        canonical = catalog.canonical()
        previous = 0
        changed = True
        existing = {
            name: {} for name in ("materials", "supplements", "rules", "schedule", "allowed_slots")
        }
        active = []
        if self.path.exists():
            connection = self._connect()
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise BusinessError("SCHEMA_VERSION", "不支持该数据库版本")
                if version == 1:
                    connection.execute("BEGIN")
                    meta = connection.execute(
                        "SELECT * FROM catalog_meta WHERE singleton_id=1"
                    ).fetchone()
                    previous = meta["revision"] if meta else 0
                    changed = not meta or meta["source_sha256"] != digest(canonical)
                    for table, key in (
                        ("materials", "material_id"),
                        ("supplements", "material_id"),
                        ("rules", "rule_id"),
                    ):
                        existing[table] = {
                            x[key]: x["content_sha256"]
                            for x in connection.execute(
                                f"SELECT {key},content_sha256 FROM {table} WHERE is_current=1"
                            )
                        }
                    for table in ("schedule", "allowed_slots"):
                        for row in connection.execute(f"SELECT * FROM {table}"):
                            value = {k: v for k, v in dict(row).items() if k != "import_id"}
                            value["at"] = value.pop("at_utc")
                            key = (
                                value["entry_id"]
                                if table == "schedule"
                                else dumps([value["platform"], value["account_id"], value["at"]])
                            )
                            existing[table][key] = digest(value)
                    active = (
                        [
                            x[0]
                            for x in connection.execute(
                                "SELECT draft_id FROM drafts WHERE validity='active'"
                            )
                        ]
                        if changed
                        else []
                    )
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
        incoming = {
            name: {x[key]: digest(x) for x in canonical[name]}
            for name, key in (
                ("materials", "material_id"),
                ("supplements", "material_id"),
                ("rules", "rule_id"),
            )
        }
        incoming["schedule"] = {x["entry_id"]: digest(x) for x in canonical["schedule"]["entries"]}
        incoming["allowed_slots"] = {
            dumps([x["platform"], x["account_id"], x["at"]]): digest(x)
            for x in canonical["schedule"]["allowed_slots"]
        }
        changes = {}
        for table, new in incoming.items():
            old = existing[table]
            changes[table] = {
                "added": sorted(new.keys() - old.keys()),
                "removed": sorted(old.keys() - new.keys()),
                "modified": sorted(k for k in new.keys() & old.keys() if new[k] != old[k]),
            }
        changes["staled_drafts"] = active
        return {
            "dry_run": True,
            "previous_revision": previous,
            "catalog_revision": previous + int(changed),
            "changed": bool(changed),
            "changes": changes,
        }

    @staticmethod
    def _upsert(connection, table: str, key: str, columns: list[str], values: list) -> None:
        # All identifiers originate in validated contracts and this module's constants.
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            f"ON CONFLICT({key}) DO UPDATE SET "
            + ",".join(f"{x}=excluded.{x}" for x in columns if x != key),
            values,
        )

    def register_request(self, request: Request) -> Request:
        canonical = request.canonical()
        serialized = dumps(canonical)
        with self.transaction(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM requests WHERE request_id=?", (request.request_id,)
            ).fetchone()
            if old:
                if (
                    old["input_sha256"] != digest(canonical)
                    or old["canonical_input_json"] != serialized
                ):
                    raise BusinessError(
                        "INPUT_CONFLICT", "相同 request_id 的原始要求不同，请使用新 ID"
                    )
                return Request.model_validate_json(old["submitted_input_json"])
            connection.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?)",
                (
                    request.request_id,
                    request.schema_version,
                    request.model_dump_json(),
                    serialized,
                    digest(canonical),
                    self.clock(),
                ),
            )
            for item in request.items:
                value = item.publish_requirement.canonical()
                connection.execute(
                    "INSERT INTO request_items VALUES (?,?,?,?,?,?)",
                    (
                        request.request_id,
                        item.material_id,
                        value["requested_at"],
                        value["timezone"],
                        value["constraint"],
                        None
                        if value["candidate_times"] is None
                        else dumps(value["candidate_times"]),
                    ),
                )
        return request

    def _basic(self, connection, material_id: str) -> MaterialSnapshot:
        meta = self._meta(connection)
        row = connection.execute(
            "SELECT * FROM materials WHERE material_id=? AND is_current=1", (material_id,)
        ).fetchone()
        if not row:
            raise BusinessError("MATERIAL_NOT_FOUND", "当前目录未找到该素材，请补充资料并显式导入")
        material = Material.model_validate({k: row[k] for k in Material.model_fields})
        return MaterialSnapshot(
            material=material,
            catalog_revision=meta["revision"],
            import_id=meta["current_import_id"],
            sources={
                x: f"materials:{material_id}@{material.material_version}" for x in FACT_FIELDS
            },
        )

    def read_material_basic(self, material_id: str) -> MaterialSnapshot:
        if self.fault == "read_failure":
            raise BusinessError("READ_FAILED", "显式注入的素材读取失败")
        with self.transaction() as connection:
            return self._basic(connection, material_id)

    def _supplement(self, connection, snapshot: MaterialSnapshot) -> MaterialSnapshot:
        if self._meta(connection)["revision"] != snapshot.catalog_revision:
            raise BusinessError("STALE_EVIDENCE", "资料已更新，请重新读取 basic")
        row = connection.execute(
            "SELECT * FROM supplements WHERE material_id=? AND is_current=1",
            (snapshot.material.material_id,),
        ).fetchone()
        result = snapshot.model_copy(deep=True)
        result.supplemented = True
        if not row:
            return result
        facts = json.loads(row["fields_json"])
        data = snapshot.material.model_dump()
        conflicts = [
            k for k, v in facts.items() if data[k] is not None and v is not None and data[k] != v
        ]
        if conflicts:
            raise BusinessError("DATA_CONFLICT", "补充资料冲突，整次未合并：" + ",".join(conflicts))
        for key, value in facts.items():
            if data[key] is None and value is not None:
                data[key] = value
                result.sources[key] = (
                    f"supplements:{row['material_id']}@{row['supplement_version']}:{row['source_id']}"
                )
        result.material = Material.model_validate(data)
        result.supplement_version = row["supplement_version"]
        return result

    def read_supplement(self, snapshot: MaterialSnapshot) -> MaterialSnapshot:
        with self.transaction() as connection:
            return self._supplement(connection, snapshot)

    @staticmethod
    def _rules(connection) -> list[Rule]:
        result = []
        for row in connection.execute("SELECT * FROM rules WHERE is_current=1 ORDER BY rule_id"):
            value = {key: row[key] for key in Rule.model_fields if key != "value"}
            value["value"] = json.loads(row["value_json"])
            result.append(Rule.model_validate(value))
        return result

    def read_rule_snapshot(self, snapshot: MaterialSnapshot) -> RuleCheck:
        with self.transaction() as connection:
            if self._meta(connection)["revision"] != snapshot.catalog_revision:
                raise BusinessError("STALE_EVIDENCE", "资料已变，请刷新素材与规则")
            rules = self._rules(connection)
        # No database transaction is held while the audit implementation runs.
        report = self.auditor.evaluate(
            snapshot.model_copy(deep=True), [rule.model_copy(deep=True) for rule in rules]
        )
        if (
            not isinstance(report, RuleCheck)
            or report.material_sha256 != snapshot.facts_hash()
            or report.catalog_revision != snapshot.catalog_revision
            or report.rules != rules
        ):
            raise BusinessError("INVALID_AUDIT", "审核报告与本次素材或规则不匹配")
        return report

    def supplement_available(self, material_id: str) -> bool:
        with self.transaction() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM supplements WHERE material_id=? AND is_current=1",
                    (material_id,),
                ).fetchone()
                is not None
            )

    @staticmethod
    def _draft(connection, request_id: str, material_id: str):
        row = connection.execute(
            "SELECT * FROM drafts WHERE request_id=? AND material_id=?", (request_id, material_id)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _occupancy(connection, platform, account_id, at_utc, self_id):
        return [
            dict(row)
            for row in connection.execute(
                "SELECT 'schedule' AS source, entry_id AS id FROM schedule "
                "WHERE platform=? AND account_id=? AND at_utc=? UNION ALL "
                "SELECT 'draft' AS source, draft_id AS id FROM drafts "
                "WHERE platform=? AND account_id=? AND at_utc=? AND validity='active' "
                "AND (? IS NULL OR draft_id<>?)",
                (platform, account_id, at_utc, platform, account_id, at_utc, self_id, self_id),
            )
        ]

    @staticmethod
    def _candidate_times(connection, item: RequestItem, snapshot: MaterialSnapshot):
        explicit = item.publish_requirement.candidate_times
        if explicit is not None:
            times = sorted({utc(t) for t in explicit})
            return times, "explicit" if times else "none"
        times = [
            row[0]
            for row in connection.execute(
                "SELECT at_utc FROM allowed_slots WHERE platform=? AND account_id=? "
                "ORDER BY at_utc",
                (snapshot.material.platform, snapshot.material.account_id),
            )
        ]
        return times, "default"

    def runtime_state(self, request_id: str, material_id: str) -> dict:
        """Read a consistent catalog/plan snapshot for local binding, not model input."""
        with self.transaction() as connection:
            draft = self._draft(connection, request_id, material_id)
            return {
                "catalog_revision": self._meta(connection)["revision"],
                "draft": self._inspect(connection, draft) if draft else None,
            }

    def query_occupancy(
        self, request_id: str, item: RequestItem, snapshot: MaterialSnapshot, query: str
    ) -> SlotObservation:
        with self.transaction() as connection:
            return self._query_occupancy(connection, request_id, item, snapshot, query)

    def query_schedule_options(
        self, request_id: str, item: RequestItem, snapshot: MaterialSnapshot
    ) -> ScheduleOptions:
        """Observe the original and necessary alternatives in one consistent read transaction."""
        with self.transaction() as connection:
            requested = self._query_occupancy(connection, request_id, item, snapshot, "requested")
            alternatives = (
                self._query_occupancy(connection, request_id, item, snapshot, "alternatives")
                if not requested.slots[0]["available"]
                else None
            )
            return ScheduleOptions(requested=requested, alternatives=alternatives)

    def _query_occupancy(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        item: RequestItem,
        snapshot: MaterialSnapshot,
        query: str,
    ) -> SlotObservation:
        if self.fault == "query_failure":
            raise BusinessError("QUERY_FAILED", "显式注入的时段查询失败")
        requirement = item.publish_requirement
        if requirement.missing():
            raise BusinessError("PRECONDITION_FAILED", "缺少原时间、时区或 hard/soft，不能查询")
        meta = self._meta(connection)
        if meta["revision"] != snapshot.catalog_revision:
            raise BusinessError("STALE_EVIDENCE", "资料已变，请刷新素材")
        draft = self._draft(connection, request_id, item.material_id)
        self_id = draft["draft_id"] if draft else None
        if query == "requested":
            times, source = [utc(requirement.requested_at)], "requested"
        else:
            if query != "alternatives":
                raise BusinessError("INVALID_QUERY", "不支持的本地时段查询")
            times, source = self._candidate_times(connection, item, snapshot)

        def inspect(at):
            conflicts = self._occupancy(
                connection,
                snapshot.material.platform,
                snapshot.material.account_id,
                at,
                self_id,
            )
            return {"at": at, "available": not conflicts, "conflicts": conflicts}

        checked, slots = [], []
        if query == "requested":
            slots = [inspect(times[0])]
            checked = list(slots)
        else:
            original = utc(requirement.requested_at)
            for direction, candidates in (
                ("before", reversed([at for at in times if at < original])),
                ("after", [at for at in times if at > original]),
            ):
                for at in candidates:
                    slot = {**inspect(at), "direction": direction}
                    checked.append(slot)
                    if slot["available"]:
                        slots.append(slot)
                        break
        return SlotObservation(
            catalog_revision=meta["revision"],
            query=query,
            source=source,
            slots=slots,
            checked_slots=checked,
        )

    def _stale(self, connection, draft, reason, *, import_id=None, run_id=None) -> None:
        if draft["validity"] != "active":
            return
        connection.execute(
            "UPDATE drafts SET validity='stale', updated_at=? WHERE draft_id=?",
            (self.clock(), draft["draft_id"]),
        )
        self._event(
            connection, draft, "staled", "stale", reason, import_id=import_id, run_id=run_id
        )

    def _invalid_reason(self, connection, draft) -> str | None:
        if draft["catalog_revision"] != self._meta(connection)["revision"]:
            return "CATALOG_CHANGED"
        historical = connection.execute(
            "SELECT payload_json FROM draft_history WHERE draft_id=? AND revision=?",
            (draft["draft_id"], draft["revision"]),
        ).fetchone()
        payload = json.loads(historical[0])
        actual = self._basic(connection, draft["material_id"])
        if payload["snapshot"]["supplemented"]:
            actual = self._supplement(connection, actual)
        review = check_rules(actual, self._rules(connection))
        if (
            actual.facts_hash() != review.material_sha256
            or actual.facts_hash()
            != MaterialSnapshot.model_validate(payload["snapshot"]).facts_hash()
            or review.model_dump(mode="json") != payload["review"]
        ):
            return "EVIDENCE_CHANGED"
        if self._occupancy(
            connection, draft["platform"], draft["account_id"], draft["at_utc"], draft["draft_id"]
        ):
            return "SLOT_CONFLICT"
        return None

    def revalidate_current_draft(
        self, request_id: str, material_id: str, *, run_id: str | None = None
    ) -> None:
        with self.transaction(write=True) as connection:
            draft = self._draft(connection, request_id, material_id)
            if draft and draft["validity"] == "active":
                reason = self._invalid_reason(connection, draft)
                if reason:
                    self._stale(connection, draft, reason, run_id=run_id)

    def _inspect(self, connection, draft: dict) -> dict:
        history = connection.execute(
            "SELECT * FROM draft_history WHERE draft_id=? AND revision=?",
            (draft["draft_id"], draft["revision"]),
        ).fetchone()
        payload = json.loads(history["payload_json"])
        definitions = payload["confirmation_items"]
        accepted = set()
        for row in connection.execute(
            "SELECT decisions_json FROM confirmations WHERE draft_id=? AND revision=?",
            (draft["draft_id"], draft["revision"]),
        ):
            accepted.update(x["item_id"] for x in json.loads(row[0]) if x["decision"] == "accept")
        if draft["validity"] != "active":
            accepted.clear()
        pending = [x for x in definitions if x["id"] not in accepted]
        status = (
            "rejected"
            if draft["validity"] == "rejected"
            else "not_required"
            if not definitions
            else "pending"
            if pending
            else "confirmed"
        )
        return {
            **draft,
            "payload": payload,
            "confirmation_status": status,
            "pending_items": pending,
            "accepted_items": sorted(accepted),
        }

    def current_draft(self, request_id: str, material_id: str) -> dict | None:
        with self.transaction() as connection:
            draft = self._draft(connection, request_id, material_id)
            return self._inspect(connection, draft) if draft else None

    def inspect_draft(self, draft_id: str) -> dict:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM drafts WHERE draft_id=?", (draft_id,)
            ).fetchone()
            if not row:
                raise BusinessError("DRAFT_NOT_FOUND", "草稿不存在")
            result = self._inspect(connection, dict(row))
            result["history"] = [
                {**dict(x), "payload_json": json.loads(x["payload_json"])}
                for x in connection.execute(
                    "SELECT * FROM draft_history WHERE draft_id=? ORDER BY revision", (draft_id,)
                )
            ]
            result["events"] = [
                dict(x)
                for x in connection.execute(
                    "SELECT * FROM draft_events WHERE draft_id=? "
                    "ORDER BY revision,occurred_at,event_id",
                    (draft_id,),
                )
            ]
            return result

    def list_drafts(self) -> list[dict]:
        with self.transaction() as connection:
            return [
                self._inspect(connection, dict(row))
                for row in connection.execute("SELECT * FROM drafts ORDER BY created_at,draft_id")
            ]

    def save_draft(
        self,
        request: Request,
        item: RequestItem,
        snapshot: MaterialSnapshot,
        review: RuleCheck,
        slot: SlotObservation,
        suggested_at: str,
        expected_revision: int,
        *,
        evidence_ids: list[str],
        run_id: str,
        slot_evidence: list[SlotObservation] | None = None,
    ) -> dict:
        write_id = new_id("WRITE")
        status = "saved"
        committed = False
        try:
            with self.transaction(write=True) as connection:
                meta = self._meta(connection)
                existing = self._draft(connection, request.request_id, item.material_id)
                if existing and existing["validity"] == "rejected":
                    raise BusinessError(
                        "DRAFT_REJECTED", "方案已拒绝，请使用新 request_id 独立发起"
                    )
                if (existing["revision"] if existing else 0) != expected_revision:
                    raise BusinessError("REVISION_CONFLICT", "当前草稿修订已改变，请重新观察")
                registered = connection.execute(
                    "SELECT canonical_input_json FROM requests WHERE request_id=?",
                    (request.request_id,),
                ).fetchone()
                if not registered or registered[0] != dumps(request.canonical()):
                    raise BusinessError("INPUT_CONFLICT", "保存必须使用已登记的不可变请求")
                if item not in request.items:
                    raise BusinessError("PRECONDITION_FAILED", "素材不属于当前请求")
                if (
                    meta["revision"] != snapshot.catalog_revision
                    or review.catalog_revision != snapshot.catalog_revision
                    or slot.catalog_revision != snapshot.catalog_revision
                ):
                    raise BusinessError("STALE_EVIDENCE", "观察已过期，必须重新读取和检查")
                actual = self._basic(connection, item.material_id)
                if snapshot.supplemented:
                    actual = self._supplement(connection, actual)
                fresh_review = check_rules(actual, self._rules(connection))
                if (
                    actual.facts_hash() != snapshot.facts_hash()
                    or fresh_review.model_dump() != review.model_dump()
                ):
                    raise BusinessError("STALE_EVIDENCE", "素材或规则依据不一致")
                requirement = item.publish_requirement
                if requirement.missing() or review.review_status not in ("passed", "needs_review"):
                    raise BusinessError("PRECONDITION_FAILED", "缺少必要要求或审核不允许保存")
                observed = {x["at"] for x in slot.slots if x["available"]}
                if suggested_at not in observed:
                    raise BusinessError("PRECONDITION_FAILED", "建议时段未被本轮观察为可用")
                if suggested_at != utc(requirement.requested_at):
                    if slot.query != "alternatives":
                        raise BusinessError("PRECONDITION_FAILED", "改期必须观察合法替代候选")
                    permitted, _ = self._candidate_times(connection, item, actual)
                    if suggested_at not in permitted:
                        raise BusinessError("PRECONDITION_FAILED", "模型不能扩大候选范围")
                if self._occupancy(
                    connection,
                    actual.material.platform,
                    actual.material.account_id,
                    suggested_at,
                    existing["draft_id"] if existing else None,
                ):
                    raise BusinessError("SLOT_CONFLICT", "保存时该时段已被占用，请刷新查询")
                definitions = confirmation_definitions(item, review, suggested_at)
                semantic = semantic_hash(snapshot, review, item, suggested_at, definitions)
                if (
                    existing
                    and existing["validity"] == "active"
                    and existing["catalog_revision"] == meta["revision"]
                    and existing["semantic_sha256"] == semantic
                ):
                    return {"save_status": "reused", "draft": self._inspect(connection, existing)}
                draft_id = existing["draft_id"] if existing else new_id("D")
                revision = expected_revision + 1
                stamp = self.clock()
                payload = {
                    "request_id": request.request_id,
                    "original_requirement": requirement.canonical(),
                    "input_sha256": digest(request.canonical()),
                    "snapshot": snapshot.model_dump(mode="json"),
                    "review": review.model_dump(mode="json"),
                    "slot_observation": slot.model_dump(),
                    "slot_observations": [x.model_dump() for x in (slot_evidence or [slot])],
                    "save_conflicts": [],
                    "suggested_at": suggested_at,
                    "confirmation_items": definitions,
                    "run_id": run_id,
                    "evidence_ids": evidence_ids,
                }
                if existing:
                    updated = connection.execute(
                        "UPDATE drafts SET revision=?,validity='active',platform=?,account_id=?,"
                        "at_utc=?,catalog_revision=?,semantic_sha256=?,updated_at=? "
                        "WHERE draft_id=? AND revision=? AND validity<>'rejected'",
                        (
                            revision,
                            actual.material.platform,
                            actual.material.account_id,
                            suggested_at,
                            meta["revision"],
                            semantic,
                            stamp,
                            draft_id,
                            expected_revision,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise BusinessError("REVISION_CONFLICT", "并发更新被拒绝")
                    status = "updated"
                else:
                    connection.execute(
                        "INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            draft_id,
                            request.request_id,
                            item.material_id,
                            revision,
                            "active",
                            actual.material.platform,
                            actual.material.account_id,
                            suggested_at,
                            meta["revision"],
                            semantic,
                            stamp,
                            stamp,
                        ),
                    )
                connection.execute(
                    "INSERT INTO draft_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        draft_id,
                        revision,
                        write_id,
                        meta["revision"],
                        meta["current_import_id"],
                        actual.material.material_version,
                        actual.facts_hash(),
                        semantic,
                        review.review_status,
                        "1.0",
                        dumps(payload),
                        stamp,
                    ),
                )
                event_draft = {
                    "draft_id": draft_id,
                    "revision": revision,
                    "validity": existing["validity"] if existing else None,
                }
                self._event(
                    connection,
                    event_draft,
                    "revised" if existing else "created",
                    "active",
                    "PLAN_SAVED",
                    run_id=run_id,
                    details={"previous_revision": expected_revision},
                )
                if self.fault in ("save_before", "save_once"):
                    once = self.fault == "save_once"
                    if once:
                        self.fault = None
                    raise BusinessError(
                        "SAVE_FAILED", "显式注入的提交前失败，事务已回滚", retryable=once
                    )
            committed = True
            if self.fault in ("receipt_lost", "receipt_unknown"):
                resolved = self.resolve_save_outcome(
                    write_id,
                    request.request_id,
                    item.material_id,
                    expected_revision=revision,
                    semantic_sha256=semantic,
                )
                return {"save_status": status, "draft": resolved, "receipt_recovered": True}
            return {
                "save_status": status,
                "draft": self.current_draft(request.request_id, item.material_id),
            }
        except BusinessError as exc:
            if exc.code != "COMMIT_UNKNOWN":
                raise
            resolved = self.resolve_save_outcome(
                write_id,
                request.request_id,
                item.material_id,
                expected_revision=revision,
                semantic_sha256=semantic,
            )
            return {"save_status": status, "draft": resolved, "receipt_recovered": True}
        except sqlite3.Error as exc:
            if committed:
                resolved = self.resolve_save_outcome(
                    write_id,
                    request.request_id,
                    item.material_id,
                    expected_revision=revision,
                    semantic_sha256=semantic,
                )
                return {"save_status": status, "draft": resolved, "receipt_recovered": True}
            raise self.database_error(exc) from exc

    def resolve_save_outcome(
        self,
        write_id: str,
        request_id: str,
        material_id: str,
        *,
        expected_revision: int,
        semantic_sha256: str,
    ) -> dict:
        if self.fault == "receipt_unknown":
            raise BusinessError("SAVE_UNKNOWN", "回执丢失且核对失败，提交结果未知")
        try:
            with self.transaction() as connection:
                proof = connection.execute(
                    "SELECT * FROM draft_history WHERE storage_write_id=?", (write_id,)
                ).fetchone()
                current = self._draft(connection, request_id, material_id)
                if (
                    proof
                    and current
                    and proof["draft_id"] == current["draft_id"]
                    and proof["revision"] == expected_revision
                    and proof["semantic_sha256"] == semantic_sha256
                ):
                    return self._inspect(connection, current)
        except sqlite3.Error as exc:
            raise BusinessError("SAVE_UNKNOWN", "无法读取提交凭据，保存结果未知") from exc
        raise BusinessError("SAVE_UNKNOWN", "没有充分凭据核实本次保存")

    @staticmethod
    def database_error(exc: sqlite3.Error) -> BusinessError:
        busy = getattr(exc, "sqlite_errorcode", None) in (
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        )
        return BusinessError(
            "DATABASE_BUSY" if busy else "DATABASE_ERROR",
            "数据库忙，请稍后重试" if busy else "数据库操作失败，未报告保存成功",
            retryable=busy,
        )

    def confirm_draft(self, decision: Confirmation, *, actor_source: str = "cli") -> dict:
        canonical = decision.canonical()
        error = None
        result = None
        with self.transaction(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM confirmations WHERE operation_id=?", (decision.operation_id,)
            ).fetchone()
            if old:
                if old["canonical_input_json"] != dumps(canonical):
                    raise BusinessError("OPERATION_CONFLICT", "相同 operation_id 的确认内容不同")
                row = connection.execute(
                    "SELECT * FROM drafts WHERE draft_id=?", (decision.draft_id,)
                ).fetchone()
                return {
                    "replayed": True,
                    "stored_operation_result": json.loads(old["result_json"]),
                    "current_draft": self._inspect(connection, dict(row)),
                }
            row = connection.execute(
                "SELECT * FROM drafts WHERE draft_id=?", (decision.draft_id,)
            ).fetchone()
            if not row:
                raise BusinessError("DRAFT_NOT_FOUND", "没有该草稿")
            draft = dict(row)
            if draft["revision"] != decision.expected_revision:
                raise BusinessError("REVISION_CONFLICT", "请查看当前修订后再确认")
            state = self._inspect(connection, draft)
            pending = {x["id"] for x in state["pending_items"]}
            if draft["validity"] != "active":
                raise BusinessError("DRAFT_INACTIVE", "只能决定当前有效草稿的未决确认项")
            if any(x.item_id not in pending for x in decision.decisions):
                raise BusinessError("INVALID_DECISION", "只能决定尚未解决的 confirm 项")
            actual = [x.model_dump() for x in decision.decisions if x.decision != "skip"]
            if not actual:
                return {"replayed": False, "skipped": True, "current_draft": state}
            reason = self._invalid_reason(connection, draft)
            if reason:
                self._stale(connection, draft, reason)
                error = BusinessError(
                    "STALE_EVIDENCE", "草稿依据或占用已变，请重评估；没有记录批准"
                )
            else:
                rejected = any(x["decision"] == "reject" for x in actual)
                accepted = set(state["accepted_items"]) | {
                    x["item_id"] for x in actual if x["decision"] == "accept"
                }
                status = (
                    "rejected" if rejected else "confirmed" if pending <= accepted else "pending"
                )
                result = {
                    "operation_id": decision.operation_id,
                    "draft_id": decision.draft_id,
                    "revision": draft["revision"],
                    "confirmation_status": status,
                    "decisions": actual,
                }
                connection.execute(
                    "INSERT INTO confirmations VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        decision.operation_id,
                        draft["draft_id"],
                        draft["revision"],
                        dumps(canonical),
                        digest(canonical),
                        dumps(actual),
                        dumps(result),
                        actor_source,
                        self.clock(),
                    ),
                )
                if rejected:
                    connection.execute(
                        "UPDATE drafts SET validity='rejected',updated_at=? WHERE draft_id=?",
                        (self.clock(), draft["draft_id"]),
                    )
                self._event(
                    connection,
                    draft,
                    "rejected" if rejected else "accepted",
                    "rejected" if rejected else "active",
                    "USER_DECISION",
                    operation_id=decision.operation_id,
                    details={"decisions": actual},
                )
        if error:
            raise error
        return {
            "replayed": False,
            "stored_operation_result": result,
            "current_draft": self.inspect_draft(decision.draft_id),
        }
