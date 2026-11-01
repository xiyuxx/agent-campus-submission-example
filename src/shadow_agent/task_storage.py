"""Stable publication tasks; methods run within Repository's SQL boundary."""

import json

from .common import BusinessError, digest, dumps, new_id
from .contracts import BusinessFacts, Catalog, Material, PublishRequirement, Request, RequestItem


class TaskStorage:
    def _task_event(self, db, task, kind, details=None):
        db.execute(
            "INSERT INTO task_events VALUES (?,?,?,?,?,?)",
            (
                new_id("TE"),
                task["task_id"],
                task["revision"],
                kind,
                dumps(details or {}),
                self.clock(),
            ),
        )

    def _backfill_tasks(self, db):
        groups = {}
        for row in db.execute("SELECT * FROM drafts ORDER BY created_at,draft_id"):
            groups.setdefault((row["material_id"], row["platform"], row["account_id"]), []).append(
                dict(row)
            )
        for (material, platform, account), drafts in groups.items():
            preferred = next((d for d in drafts if d["validity"] == "active"), drafts[-1])
            ordered = [d for d in drafts if d != preferred] + [preferred]
            task = self._new_task(db, material, platform, account)
            for version, draft in enumerate(ordered, 1):
                db.execute(
                    "INSERT INTO task_requests VALUES (?,?,?,?,?,?,NULL,NULL)",
                    (
                        draft["request_id"],
                        material,
                        task["task_id"],
                        version,
                        draft["draft_id"],
                        draft["revision"],
                    ),
                )
            db.execute(
                "UPDATE publication_tasks SET revision=?,current_request_id=?,current_draft_id=? "
                "WHERE task_id=?",
                (len(ordered), preferred["request_id"], preferred["draft_id"], task["task_id"]),
            )
            self._task_event(db, {**task, "revision": len(ordered)}, "migrated")

    def _new_task(self, db, material, platform, account):
        stamp = self.clock()
        task = dict(
            task_id=new_id("TASK"),
            material_id=material,
            platform=platform,
            account_id=account,
            status="open",
            revision=0,
            current_request_id=None,
            current_draft_id=None,
            created_at=stamp,
            updated_at=stamp,
        )
        db.execute(
            "INSERT INTO publication_tasks VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(task.values())
        )
        return task

    @staticmethod
    def _task_for(db, material, platform, account):
        row = db.execute(
            "SELECT * FROM publication_tasks WHERE material_id=? AND platform=? AND account_id=?",
            (material, platform, account),
        ).fetchone()
        return dict(row) if row else None

    def _task_access(self, db, request_id, item, snapshot):
        task = self._task_for(
            db, item.material_id, snapshot.material.platform, snapshot.material.account_id
        )
        if not task:
            return None
        link = db.execute(
            "SELECT * FROM task_requests WHERE request_id=? AND material_id=?",
            (request_id, item.material_id),
        ).fetchone()
        if not link or link["task_id"] != task["task_id"]:
            related = []
            if task["current_draft_id"]:
                row = db.execute(
                    "SELECT * FROM drafts WHERE draft_id=?", (task["current_draft_id"],)
                ).fetchone()
                related.append(
                    {k: row[k] for k in ("draft_id", "request_id", "revision", "at_utc")}
                )
            raise BusinessError(
                "DUPLICATE_PUBLICATION",
                "已有发布任务，请在 work 中继续或修改该任务",
                details={"task_id": task["task_id"], "related_drafts": related},
            )
        if task["status"] == "cancelled":
            raise BusinessError("TASK_CANCELLED", "任务已取消；需用户显式恢复")
        if task["current_request_id"] != request_id or task["revision"] != link["task_revision"]:
            raise BusinessError("TASK_SUPERSEDED", "要求已有新版本，请继续当前发布任务")
        return task

    def _publication_self(self, db, request_id, item, snapshot):
        task = self._task_access(db, request_id, item, snapshot)
        if task and task["current_draft_id"]:
            return task["current_draft_id"]
        draft = self._draft(db, request_id, item.material_id)
        return draft["draft_id"] if draft else None

    def _replace_task_plan(self, db, request, item, snapshot, draft_id):
        task = self._task_access(db, request.request_id, item, snapshot)
        if not task:
            task = self._new_task(
                db, item.material_id, snapshot.material.platform, snapshot.material.account_id
            )
            task.update(revision=1, current_request_id=request.request_id)
            db.execute(
                "INSERT INTO task_requests VALUES (?,?,?,?,?,?,NULL,NULL)",
                (request.request_id, item.material_id, task["task_id"], 1, None, 0),
            )
        previous = task["current_draft_id"]
        if previous and previous != draft_id:
            link = db.execute(
                "SELECT * FROM task_requests WHERE request_id=? AND material_id=?",
                (request.request_id, item.material_id),
            ).fetchone()
            old = dict(db.execute("SELECT * FROM drafts WHERE draft_id=?", (previous,)).fetchone())
            if (
                link["expected_draft_id"] != previous
                or link["expected_draft_revision"] != old["revision"]
            ):
                raise BusinessError("REVISION_CONFLICT", "原方案已修改，请刷新任务要求")
            self._stale(db, old, "TASK_REPLACED")
        db.execute(
            "UPDATE publication_tasks SET revision=?,current_request_id=?,current_draft_id=?,"
            "updated_at=? WHERE task_id=?",
            (task["revision"], request.request_id, draft_id, self.clock(), task["task_id"]),
        )
        self._task_event(db, task, "plan_saved", {"draft_id": draft_id, "previous": previous})

    def prepare_task(
        self,
        material_id: str,
        requirement: PublishRequirement,
        *,
        expected_revision: int | None = None,
        reopen: bool = False,
    ) -> Request:
        """Only a user operation may create or change an immutable task requirement."""
        with self.transaction(write=True) as db:
            material = self._basic(db, material_id).material
            task = self._task_for(db, material_id, material.platform, material.account_id)
            if task:
                if expected_revision is not None and task["revision"] != expected_revision:
                    raise BusinessError("REVISION_CONFLICT", "任务已有更新，请刷新工作清单")
                if task["status"] == "cancelled" and not reopen:
                    raise BusinessError("TASK_CANCELLED", "任务已取消，恢复需明确选择")
                old = self._task_request(db, task)
                if (
                    task["status"] == "open"
                    and old
                    and len(old.items) == 1
                    and not reopen
                    and old.items[0].publish_requirement.canonical() == requirement.canonical()
                ):
                    return old
            else:
                task = self._new_task(db, material_id, material.platform, material.account_id)
            request = Request(
                request_id=new_id("TASKREQ"),
                items=[RequestItem(material_id=material_id, publish_requirement=requirement)],
            )
            self._register_request(db, request)
            version = task["revision"] + 1
            previous = db.execute(
                "SELECT * FROM drafts WHERE draft_id=?", (task["current_draft_id"],)
            ).fetchone()
            db.execute(
                "INSERT INTO task_requests VALUES (?,?,?,?,?,?,NULL,NULL)",
                (
                    request.request_id,
                    material_id,
                    task["task_id"],
                    version,
                    task["current_draft_id"],
                    previous["revision"] if previous else 0,
                ),
            )
            db.execute(
                "UPDATE publication_tasks SET revision=?,current_request_id=?,status='open',"
                "updated_at=? WHERE task_id=?",
                (version, request.request_id, self.clock(), task["task_id"]),
            )
            self._task_event(
                db,
                {**task, "revision": version},
                "reopened" if task["status"] == "cancelled" else "requirements_changed",
                {"request_id": request.request_id},
            )
            return request

    @staticmethod
    def _task_request(db, task):
        row = db.execute(
            "SELECT submitted_input_json FROM requests WHERE request_id=?",
            (task["current_request_id"],),
        ).fetchone()
        if not row:
            return None
        return Request.model_validate_json(row[0])

    def task_request(self, task_id):
        with self.transaction() as db:
            task = db.execute(
                "SELECT * FROM publication_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not task:
                raise BusinessError("TASK_NOT_FOUND", "发布任务不存在")
            if task["status"] == "cancelled":
                raise BusinessError("TASK_CANCELLED", "任务已取消")
            # Preserve the original canonical multi-item request for legacy registration.
            row = db.execute(
                "SELECT submitted_input_json FROM requests WHERE request_id=?",
                (task["current_request_id"],),
            ).fetchone()
            return Request.model_validate_json(row[0])

    def record_task_result(self, request, result, output):
        with self.transaction(write=True) as db:
            for item in result.items:
                db.execute(
                    "UPDATE task_requests SET result_json=?,run_directory=? "
                    "WHERE request_id=? AND material_id=?",
                    (item.model_dump_json(), str(output), request.request_id, item.material_id),
                )

    def cancel_task(self, task_id, *, expected_revision):
        with self.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM publication_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row:
                raise BusinessError("TASK_NOT_FOUND", "发布任务不存在")
            task = dict(row)
            if task["revision"] != expected_revision:
                raise BusinessError("REVISION_CONFLICT", "任务已变化，请刷新后再取消")
            if task["status"] == "cancelled":
                return {"cancelled": True, "reused": True}
            if task["current_draft_id"]:
                draft = dict(
                    db.execute(
                        "SELECT * FROM drafts WHERE draft_id=?", (task["current_draft_id"],)
                    ).fetchone()
                )
                self._stale(db, draft, "TASK_CANCELLED")
            db.execute(
                "UPDATE publication_tasks SET status='cancelled',updated_at=? WHERE task_id=?",
                (self.clock(), task_id),
            )
            self._task_event(db, task, "cancelled")
            return {
                "cancelled": True,
                "reused": False,
                "message": "已取消本地任务；外部发布需另行协调",
            }

    def task_worklist(self):
        with self.transaction(write=True) as db:
            results = []
            for row in db.execute(
                "SELECT * FROM materials WHERE is_current=1 ORDER BY material_id"
            ):
                task = self._task_for(db, row["material_id"], row["platform"], row["account_id"])
                result = {
                    "material_id": row["material_id"],
                    "title": row["title"],
                    "platform": row["platform"],
                    "account_id": row["account_id"],
                    "task_id": None,
                    "state": "待处理",
                    "revision": 0,
                    "requirement": None,
                    "draft": None,
                    "last_result": None,
                }
                if task:
                    request = self._task_request(db, task)
                    link = db.execute(
                        "SELECT * FROM task_requests WHERE request_id=? AND material_id=?",
                        (task["current_request_id"], row["material_id"]),
                    ).fetchone()
                    draft_row = db.execute(
                        "SELECT * FROM drafts WHERE draft_id=?", (task["current_draft_id"],)
                    ).fetchone()
                    draft = (
                        self._inspect(db, self._fresh(db, dict(draft_row))) if draft_row else None
                    )
                    last = json.loads(link["result_json"]) if link and link["result_json"] else None
                    state = "待检查"
                    if task["status"] == "cancelled":
                        state = "已取消"
                    elif draft and draft["request_id"] != task["current_request_id"]:
                        state = "修改未完成"
                    elif draft and draft["validity"] == "active":
                        state = "等你确认" if draft["pending_items"] else "可交接"
                    elif draft and draft["validity"] == "rejected":
                        state = "方案已拒绝"
                    elif draft:
                        state = "需重新检查"
                    if state not in ("已取消", "修改未完成") and last:
                        if last["draft"]["save_status"] in ("failed", "unknown"):
                            state = "保存未完成"
                        elif last["task_status"] in ("failed", "incomplete"):
                            state = "执行未完成"
                        elif last["review_status"] == "blocked":
                            state = "暂不能安排"
                        elif last["review_status"] == "needs_info":
                            state = "待补资料"
                    result.update(
                        task_id=task["task_id"],
                        revision=task["revision"],
                        state=state,
                        requirement=next(
                            x for x in request.items if x.material_id == row["material_id"]
                        ).publish_requirement.model_dump(mode="json"),
                        draft=draft,
                        last_result=last,
                        run_directory=link["run_directory"] if link else None,
                    )
                results.append(result)
            return results

    def handoff(self):
        rows = self.task_worklist()
        ready = []
        for row in rows:
            if row["state"] != "可交接":
                continue
            draft = row["draft"]
            ready.append(
                {
                    "task_id": row["task_id"],
                    "material_id": row["material_id"],
                    "title": row["title"],
                    "platform": row["platform"],
                    "account_id": row["account_id"],
                    "suggested_at": draft["at_utc"],
                    "timezone": row["requirement"]["timezone"],
                    "original_requirement": row["requirement"],
                    "review_status": draft["payload"]["review"]["review_status"],
                    "risk_reasons": draft["payload"]["review"]["matches"],
                    "confirmation_status": draft["confirmation_status"],
                    "pending_items": [],
                    "save_status": "saved",
                    "draft_id": draft["draft_id"],
                    "revision": draft["revision"],
                }
            )
        return {
            "export_version": "H-" + digest(ready)[:16],
            "generated_at": self.clock(),
            "business_time": self.business_now(),
            "notice": "本文件是静态草稿快照；改期或取消后请重新导出并核对版本。"
            "未执行真实发布或平台预约。",
            "items": ready,
            "excluded_count": len(rows) - len(ready),
        }

    def supplement_material(self, material_id, fields, *, expected_material_version):
        validated = BusinessFacts.model_validate(fields)
        changes = validated.model_dump(exclude_unset=True)
        if not changes:
            raise BusinessError("INVALID_INPUT", "请提供需要补充的业务字段")
        with self.transaction() as db:
            material = self._basic(db, material_id).material
            if material.material_version != expected_material_version:
                raise BusinessError("REVISION_CONFLICT", "素材已更新，请刷新后再补充")
            # Reconstruct the entire current catalog through its existing validated import API.
            materials = [dict(r) for r in db.execute("SELECT * FROM materials WHERE is_current=1")]
            supplements = [
                dict(r) for r in db.execute("SELECT * FROM supplements WHERE is_current=1")
            ]
            rules = self._rules(db)
            schedule = {
                "schedule_version": self._meta(db)["schedule_version"],
                "entries": [
                    {k: r[k] for k in ("entry_id", "platform", "account_id", "material_id")}
                    | {"at": r["at_utc"]}
                    for r in db.execute("SELECT * FROM schedule")
                ],
                "allowed_slots": [
                    {"platform": r["platform"], "account_id": r["account_id"], "at": r["at_utc"]}
                    for r in db.execute("SELECT * FROM allowed_slots")
                ],
            }
            values = [{k: r[k] for k in Material.model_fields} for r in materials]
            for value in values:
                if value["material_id"] == material_id:
                    value.update(changes)
                    value["material_version"] += 1
            supplement_values = [
                {
                    "material_id": r["material_id"],
                    "supplement_version": r["supplement_version"],
                    "source_id": r["source_id"],
                    "fields": json.loads(r["fields_json"]),
                }
                for r in supplements
            ]
            # The user's explicit update supersedes stale supplement fields of the same name.
            for value in supplement_values:
                if value["material_id"] == material_id:
                    value["fields"] = {k: v for k, v in value["fields"].items() if k not in changes}
                    value["supplement_version"] += 1
            catalog = Catalog.model_validate(
                dict(
                    materials=values, supplements=supplement_values, rules=rules, schedule=schedule
                )
            )
            expected_catalog = self._meta(db)["revision"]
        return self.apply_catalog_snapshot(catalog, update=True, expected_revision=expected_catalog)
