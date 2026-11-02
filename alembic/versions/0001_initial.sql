CREATE TABLE events(id TEXT PRIMARY KEY, source TEXT NOT NULL, type TEXT NOT NULL, category TEXT NOT NULL,
  actor TEXT, principal TEXT, subject TEXT, dedup_key TEXT NOT NULL, trust TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('accepted','rejected','observed','duplicate')),
  payload_json TEXT CHECK(json_valid(payload_json)), received_at TEXT NOT NULL,
  summary TEXT, link TEXT, addressed INTEGER NOT NULL DEFAULT 0, UNIQUE(source, dedup_key));
CREATE INDEX ix_events_received ON events(received_at);

CREATE TABLE rules(id TEXT PRIMARY KEY, rank INTEGER NOT NULL, enabled INTEGER NOT NULL, match_json TEXT CHECK(json_valid(match_json)),
  skill_id TEXT, priority TEXT, dedup_window_s INTEGER, merge_by TEXT, "continue" INTEGER DEFAULT 0,
  accept_mode TEXT CHECK(accept_mode IN('off','observe','auto')), assess INTEGER DEFAULT 0, engine_role TEXT,
  skill_section TEXT, target_dir TEXT, concurrency_key TEXT, max_parallel INTEGER,
  notify TEXT CHECK(notify IN('decision_only','result','never')));
CREATE INDEX ix_rules_rank ON rules(rank);

CREATE TABLE skills(id TEXT PRIMARY KEY, version INTEGER, rank INTEGER, enabled INTEGER, config_json TEXT CHECK(json_valid(config_json)),
  config_hash TEXT, updated_at TEXT);
CREATE INDEX ix_skills_rank ON skills(rank);

CREATE TABLE tasks(id TEXT PRIMARY KEY, event_id TEXT REFERENCES events(id), skill_id TEXT, status TEXT NOT NULL,
  priority TEXT NOT NULL, pinned INTEGER DEFAULT 0, manual_rank INTEGER, skill_rank INTEGER, retry_count INTEGER DEFAULT 0,
  fix_round INTEGER DEFAULT 0, repo TEXT, engine_hint TEXT, lease_owner TEXT, lease_until TEXT, version INTEGER NOT NULL DEFAULT 0,
  enqueued_at TEXT, closed_at TEXT, close_reason TEXT, mandate_json TEXT CHECK(mandate_json IS NULL OR json_valid(mandate_json)),
  policy_version TEXT, concurrency_key TEXT, reserved_slot INTEGER DEFAULT 0, stopped_by_owner INTEGER DEFAULT 0);
CREATE INDEX ix_tasks_queue ON tasks(status, pinned DESC, priority, manual_rank, skill_rank, enqueued_at);

CREATE TABLE task_events(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), from_status TEXT, to_status TEXT,
  reason_json TEXT CHECK(json_valid(reason_json)), actor TEXT, at TEXT);
CREATE TABLE runs(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), attempt INTEGER, engine_id TEXT, tier TEXT,
  started_at TEXT, ended_at TEXT, outcome TEXT, error_class TEXT, receipt_json TEXT, session_log_path TEXT, recovered INTEGER DEFAULT 0);
CREATE TABLE pull_requests(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), scm TEXT, repo TEXT, number INTEGER, url TEXT,
  head TEXT, base TEXT, ci_stage TEXT, fix_round INTEGER DEFAULT 0, risk_hard TEXT, risk_ai TEXT, risk_final TEXT,
  merge_action TEXT, merged_sha TEXT, live_sha TEXT, verified_at TEXT, UNIQUE(scm, repo, number));
CREATE TABLE reviews(id TEXT PRIMARY KEY, pr_id TEXT REFERENCES pull_requests(id), round INTEGER, engine_id TEXT, model TEXT,
  same_model_fallback INTEGER DEFAULT 0, verdict TEXT, issues_json TEXT, token_ratio REAL, at TEXT);
CREATE TABLE ci_runs(id TEXT PRIMARY KEY, pr_id TEXT REFERENCES pull_requests(id), round INTEGER, provider TEXT, run_ref TEXT,
  conclusion TEXT, summary TEXT, log_path TEXT);
CREATE TABLE decisions(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), kind TEXT, question TEXT, recommend TEXT,
  options_json TEXT, nonce TEXT UNIQUE, card_msg_id TEXT,
  status TEXT CHECK(status IN('pending','answered','expired','precedent')), answer TEXT, answered_by TEXT,
  save_as_precedent TEXT, precedent_id TEXT, reason_json TEXT, expires_at TEXT);
CREATE TABLE effects(id TEXT PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL, target TEXT, idem_key TEXT NOT NULL UNIQUE,
  payload_json TEXT CHECK(json_valid(payload_json)),
  status TEXT NOT NULL CHECK(status IN('pending','running','done','failed','dropped')),
  attempts INTEGER DEFAULT 0, next_at TEXT, last_error TEXT, order_key TEXT, dropped_reason TEXT, created_at TEXT);
CREATE INDEX ix_effects_due ON effects(status, next_at);
CREATE INDEX ix_effects_order ON effects(order_key, created_at);
CREATE TABLE subscriptions(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), scm_or_source TEXT, thread_ref TEXT,
  own INTEGER, context_only INTEGER, created_at TEXT, closed_at TEXT, expires_at TEXT);
CREATE INDEX ix_sub_thread ON subscriptions(thread_ref);
CREATE TABLE source_cursors(source_id TEXT PRIMARY KEY, cursor TEXT, updated_at TEXT, consecutive_failures INTEGER DEFAULT 0, last_error TEXT);
CREATE TABLE observations(id TEXT PRIMARY KEY, event_id TEXT, rule_id TEXT, would_accept INTEGER, assessment_json TEXT, policy_version TEXT, at TEXT);
CREATE TABLE config_versions(revision INTEGER PRIMARY KEY, file TEXT, content_hash TEXT, base_revision INTEGER,
  origin TEXT CHECK(origin IN('console','cli','agent','upgrade')), actor TEXT, at TEXT);
CREATE TABLE cost_ledger(id TEXT PRIMARY KEY, task_id TEXT, run_id TEXT, engine_id TEXT, skill_id TEXT,
  mode TEXT CHECK(mode IN('token','per_call','duration')), input_tokens INTEGER, output_tokens INTEGER, calls INTEGER,
  duration_s REAL, cost_cny REAL, est INTEGER, at TEXT);
CREATE INDEX ix_cost_at ON cost_ledger(at);
CREATE INDEX ix_cost_skill ON cost_ledger(skill_id, at);
CREATE TABLE budgets(id TEXT PRIMARY KEY, scope TEXT CHECK(scope IN('global','skill','engine')), scope_id TEXT,
  period TEXT CHECK(period IN('day','month')), limit_cny REAL, soft_pct REAL, hard_action TEXT);
CREATE TABLE memories(id TEXT PRIMARY KEY,
  kind TEXT CHECK(kind IN('fact','lesson','context','precedent_decided','precedent_rejected')),
  scope TEXT, title TEXT, content TEXT, quote TEXT, decided_at TEXT, decision_id TEXT, tags TEXT, source_task_id TEXT, expires_at TEXT);
-- memories_vec: CREATE VIRTUAL TABLE memories_vec USING vec0(embedding float[<dim>]);  维度 <dim> 由所选 embedding 模型决定，部署时填写
CREATE VIRTUAL TABLE memories_fts USING fts5(title, content, content='memories', content_rowid='rowid');
CREATE TABLE issues_lookback(id TEXT PRIMARY KEY, task_id TEXT, pattern TEXT, root_cause TEXT, fix TEXT, created_at TEXT);
CREATE TABLE engine_health(engine_id TEXT PRIMARY KEY, status TEXT, last_ok_at TEXT, last_err TEXT, breaker_state TEXT, breaker_until TEXT);
CREATE TABLE secrets_meta(ref TEXT PRIMARY KEY, provider TEXT, created_at TEXT, rotated_at TEXT, rotate_every_days INTEGER, last_reminded_at TEXT);
CREATE TABLE audit_log(id TEXT PRIMARY KEY, actor TEXT CHECK(actor IN('owner','agent-cli','system')), action TEXT, target TEXT, diff_json TEXT, at TEXT);
CREATE TABLE principals(id TEXT PRIMARY KEY, name TEXT, role TEXT, identities_json TEXT);
CREATE TABLE system_state(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
