-- 初始数据库结构；由 Repository.initialize 与后续迁移共同执行。
-- 连接先开启 foreign_keys，使用显式事务；执行前检查 STRICT / JSON 支持。

CREATE TABLE catalog_imports (
    import_id TEXT PRIMARY KEY NOT NULL,
    previous_revision INTEGER NOT NULL CHECK (previous_revision >= 0),
    catalog_revision INTEGER NOT NULL CHECK (catalog_revision >= 1),
    outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'noop')),
    source_label TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    schedule_version INTEGER NOT NULL CHECK (schedule_version >= 1),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    changes_json TEXT NOT NULL CHECK (json_valid(changes_json)),
    imported_at TEXT NOT NULL,
    UNIQUE (import_id, catalog_revision),
    CHECK ((outcome = 'applied' AND catalog_revision = previous_revision + 1)
        OR (outcome = 'noop' AND catalog_revision = previous_revision))
) STRICT;

CREATE TABLE catalog_meta (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    current_import_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    schedule_version INTEGER NOT NULL CHECK (schedule_version >= 1),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (current_import_id, revision)
        REFERENCES catalog_imports(import_id, catalog_revision) ON DELETE RESTRICT
) STRICT;

CREATE TABLE materials (
    material_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(material_id)) > 0),
    material_version INTEGER NOT NULL CHECK (material_version >= 1),
    platform TEXT NOT NULL CHECK (length(trim(platform)) > 0),
    account_id TEXT NOT NULL CHECK (length(trim(account_id)) > 0),
    source_asset_id TEXT,
    title TEXT,
    type TEXT CHECK (type IN ('video', 'image', 'text')),
    media_ref TEXT,
    caption TEXT,
    cover_ref TEXT,
    copyright_status TEXT CHECK (copyright_status IN ('authorized', 'unauthorized')),
    license_ref TEXT,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    last_import_id TEXT NOT NULL REFERENCES catalog_imports(import_id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE supplements (
    material_id TEXT PRIMARY KEY NOT NULL
        REFERENCES materials(material_id) ON DELETE RESTRICT,
    supplement_version INTEGER NOT NULL CHECK (supplement_version >= 1),
    source_id TEXT NOT NULL CHECK (length(trim(source_id)) > 0),
    fields_json TEXT NOT NULL CHECK (json_valid(fields_json)),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    last_import_id TEXT NOT NULL REFERENCES catalog_imports(import_id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE rules (
    rule_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(rule_id)) > 0),
    version INTEGER NOT NULL CHECK (version >= 1),
    field TEXT NOT NULL CHECK (field IN (
        'source_asset_id', 'title', 'type', 'media_ref', 'caption',
        'cover_ref', 'copyright_status', 'license_ref'
    )),
    operator TEXT NOT NULL CHECK (operator IN ('missing', 'equals', 'contains')),
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    severity TEXT NOT NULL CHECK (severity IN ('needs_info', 'needs_review', 'blocked')),
    message TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    last_import_id TEXT NOT NULL REFERENCES catalog_imports(import_id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE schedule (
    entry_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(entry_id)) > 0),
    platform TEXT NOT NULL CHECK (length(trim(platform)) > 0),
    account_id TEXT NOT NULL CHECK (length(trim(account_id)) > 0),
    at_utc TEXT NOT NULL,
    material_id TEXT NOT NULL CHECK (length(trim(material_id)) > 0),
    import_id TEXT NOT NULL REFERENCES catalog_imports(import_id) ON DELETE RESTRICT,
    UNIQUE (platform, account_id, at_utc)
) STRICT;

CREATE TABLE allowed_slots (
    platform TEXT NOT NULL CHECK (length(trim(platform)) > 0),
    account_id TEXT NOT NULL CHECK (length(trim(account_id)) > 0),
    at_utc TEXT NOT NULL,
    import_id TEXT NOT NULL REFERENCES catalog_imports(import_id) ON DELETE RESTRICT,
    PRIMARY KEY (platform, account_id, at_utc)
) STRICT;

CREATE TABLE requests (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(request_id)) > 0),
    schema_version TEXT NOT NULL CHECK (schema_version = '1.0'),
    submitted_input_json TEXT NOT NULL CHECK (json_valid(submitted_input_json)),
    canonical_input_json TEXT NOT NULL CHECK (json_valid(canonical_input_json)),
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE request_items (
    request_id TEXT NOT NULL REFERENCES requests(request_id) ON DELETE RESTRICT,
    material_id TEXT NOT NULL CHECK (length(trim(material_id)) > 0),
    requested_at_utc TEXT,
    timezone TEXT,
    constraint_kind TEXT CHECK (constraint_kind IN ('hard', 'soft')),
    candidate_times_json TEXT CHECK (
        candidate_times_json IS NULL OR
        (json_valid(candidate_times_json) AND json_type(candidate_times_json) = 'array')
    ),
    PRIMARY KEY (request_id, material_id)
) STRICT;

CREATE TABLE drafts (
    draft_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(draft_id)) > 0),
    request_id TEXT NOT NULL,
    material_id TEXT NOT NULL REFERENCES materials(material_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    validity TEXT NOT NULL CHECK (validity IN ('active', 'stale', 'rejected')),
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    catalog_revision INTEGER NOT NULL CHECK (catalog_revision >= 1),
    semantic_sha256 TEXT NOT NULL CHECK (length(semantic_sha256) = 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (request_id, material_id),
    FOREIGN KEY (request_id, material_id)
        REFERENCES request_items(request_id, material_id) ON DELETE RESTRICT,
    FOREIGN KEY (draft_id, revision) REFERENCES draft_history(draft_id, revision)
        DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE draft_history (
    draft_id TEXT NOT NULL REFERENCES drafts(draft_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    storage_write_id TEXT NOT NULL UNIQUE,
    catalog_revision INTEGER NOT NULL CHECK (catalog_revision >= 1),
    import_id TEXT NOT NULL,
    material_version INTEGER NOT NULL CHECK (material_version >= 1),
    effective_material_sha256 TEXT NOT NULL CHECK (length(effective_material_sha256) = 64),
    semantic_sha256 TEXT NOT NULL CHECK (length(semantic_sha256) = 64),
    review_status TEXT NOT NULL CHECK (review_status IN ('passed', 'needs_review')),
    payload_schema_version TEXT NOT NULL CHECK (payload_schema_version = '1.0'),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (draft_id, revision),
    FOREIGN KEY (import_id, catalog_revision)
        REFERENCES catalog_imports(import_id, catalog_revision) ON DELETE RESTRICT
) STRICT;

CREATE TABLE confirmations (
    operation_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(operation_id)) > 0),
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    canonical_input_json TEXT NOT NULL CHECK (json_valid(canonical_input_json)),
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    decisions_json TEXT NOT NULL CHECK (json_valid(decisions_json)),
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    actor_source TEXT NOT NULL CHECK (actor_source IN ('cli', 'review')),
    decided_at TEXT NOT NULL,
    UNIQUE (operation_id, draft_id, revision),
    FOREIGN KEY (draft_id, revision)
        REFERENCES draft_history(draft_id, revision) ON DELETE RESTRICT
) STRICT;

CREATE TABLE draft_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('created', 'revised', 'staled', 'accepted', 'rejected')
    ),
    from_validity TEXT CHECK (from_validity IN ('active', 'stale', 'rejected')),
    to_validity TEXT NOT NULL CHECK (to_validity IN ('active', 'stale', 'rejected')),
    reason_code TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    operation_id TEXT,
    import_id TEXT REFERENCES catalog_imports(import_id) ON DELETE RESTRICT,
    run_id TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (draft_id, revision)
        REFERENCES draft_history(draft_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (operation_id, draft_id, revision)
        REFERENCES confirmations(operation_id, draft_id, revision) ON DELETE RESTRICT
) STRICT;

CREATE UNIQUE INDEX ux_drafts_active_slot
    ON drafts(platform, account_id, at_utc) WHERE validity = 'active';
CREATE INDEX ix_drafts_material ON drafts(material_id);
CREATE INDEX ix_confirmations_draft_revision ON confirmations(draft_id, revision);
CREATE INDEX ix_draft_events_revision ON draft_events(draft_id, revision, occurred_at, event_id);
CREATE INDEX ix_imports_revision ON catalog_imports(catalog_revision, imported_at);
