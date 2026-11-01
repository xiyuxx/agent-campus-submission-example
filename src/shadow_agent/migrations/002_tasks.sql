CREATE TABLE publication_tasks (
    task_id TEXT PRIMARY KEY NOT NULL,
    material_id TEXT NOT NULL REFERENCES materials(material_id),
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'cancelled')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    current_request_id TEXT REFERENCES requests(request_id),
    current_draft_id TEXT REFERENCES drafts(draft_id) DEFERRABLE INITIALLY DEFERRED,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(material_id, platform, account_id)
) STRICT;

CREATE TABLE task_requests (
    request_id TEXT NOT NULL,
    material_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES publication_tasks(task_id),
    task_revision INTEGER NOT NULL CHECK (task_revision >= 1),
    expected_draft_id TEXT REFERENCES drafts(draft_id),
    expected_draft_revision INTEGER NOT NULL CHECK (expected_draft_revision >= 0),
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    run_directory TEXT,
    PRIMARY KEY(request_id, material_id),
    UNIQUE(task_id, task_revision),
    FOREIGN KEY(request_id, material_id) REFERENCES request_items(request_id, material_id)
) STRICT;

CREATE TABLE task_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL REFERENCES publication_tasks(task_id),
    task_revision INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK(json_valid(details_json)),
    occurred_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX ux_drafts_active_material
    ON drafts(material_id, platform, account_id) WHERE validity='active';
