"""Additive schema migrations — columns added after initial table creation.

Extracted from main.py (bootstrap layer — docs/TARGET_ARCHITECTURE.md Layer 2). Idempotent:
each entry ADDs one column, and a duplicate ADD just rolls back, so this is safe to run on
every startup. Poor-man's migration until Alembic. main.py calls migrate_schema() at startup.
"""
from sqlalchemy import text

from db import engine


def migrate_schema() -> None:
    """Add columns that were added after initial table creation."""
    additions = [
        # domain_registry extended fields (v1)
        ("domain_registry", "page_states", "JSON NOT NULL DEFAULT '[]'"),
        ("domain_registry", "capture_defaults", "JSON NOT NULL DEFAULT '{}'"),
        ("domain_registry", "validation_expectations", "JSON NOT NULL DEFAULT '[]'"),
        ("domain_registry", "config_version", "VARCHAR(50) NOT NULL DEFAULT 'v1'"),
        # scenario_registry vision training fields (v2)
        ("scenario_registry", "element_query", "VARCHAR(500)"),
        ("scenario_registry", "expected_outcome_state", "VARCHAR(100)"),
        ("scenario_registry", "difficulty", "VARCHAR(50)"),
        ("scenario_registry", "is_eval_only", "BOOLEAN NOT NULL DEFAULT false"),
        # training_captures vision training fields (v2)
        ("training_captures", "scenario_id", "VARCHAR(120)"),
        ("training_captures", "element_query", "VARCHAR(500)"),
        ("training_captures", "observed_page_state", "VARCHAR(100)"),
        ("training_captures", "post_action_state", "VARCHAR(100)"),
        # training_captures annotator-created candidates (v3)
        ("training_captures", "manual_candidates", "JSON NOT NULL DEFAULT '[]'"),
        # training_captures interaction-layer payload (v4)
        ("training_captures", "action_text", "VARCHAR(1000)"),
        # goal_registry training config fields (v2)
        ("goal_registry", "description", "TEXT"),
        ("goal_registry", "typical_element_types", "JSON NOT NULL DEFAULT '[]'"),
        ("goal_registry", "success_criteria", "VARCHAR(500)"),
        # goal_registry agent-stage (v5)
        ("goal_registry", "stage", "VARCHAR(30) NOT NULL DEFAULT 'neutral'"),
        # page_state_registry objective(goal) scope (v5)
        ("page_state_registry", "goal_id", "VARCHAR(100)"),
        # page_state_registry agent-stage (v6)
        ("page_state_registry", "stage", "VARCHAR(30)"),
        # task_registry training config fields (v2)
        ("task_registry", "description", "TEXT"),
        ("task_registry", "estimated_steps", "VARCHAR(50)"),
        ("task_registry", "is_repeatable", "BOOLEAN NOT NULL DEFAULT true"),
        # model_eval_run resumability + live progress (v7)
        ("model_eval_run", "progress", "JSON"),
        ("model_eval_run", "cancel_requested", "BOOLEAN NOT NULL DEFAULT false"),
        ("model_eval_run", "resumed_from", "VARCHAR(64)"),
        # training_sessions catchall-training vs workhorse split (v8)
        ("training_sessions", "purpose", "VARCHAR(30) NOT NULL DEFAULT 'data_collection'"),
        # training_captures distillation provenance (v9)
        ("training_captures", "label_source", "VARCHAR(20)"),
        ("training_captures", "label_confidence", "DOUBLE PRECISION"),
        ("training_captures", "verified_at", "TIMESTAMPTZ"),
        # training_captures page-state label provenance (v10)
        ("training_captures", "state_label_source", "VARCHAR(20)"),
        ("training_captures", "state_label_confidence", "DOUBLE PRECISION"),
        # training_captures multi-tenant + cross-platform axes (v11)
        ("training_captures", "tenant_id", "VARCHAR(120)"),
        ("training_captures", "predecessor_capture_id", "INTEGER REFERENCES training_captures(id) ON DELETE SET NULL"),
        # observed_jobs richer signal (v12)
        ("observed_jobs", "salary", "VARCHAR(200)"),
        ("observed_jobs", "description", "TEXT"),
        ("observed_jobs", "apply_type", "VARCHAR(30)"),
        # observed_jobs cross-site apply routing (v13)
        ("observed_jobs", "application_platform", "VARCHAR(40)"),
        # training_sessions persistent (pre-authed) profile attach (v14)
        ("training_sessions", "persistent_profile", "VARCHAR(120)"),
        # training_sessions multi-account + protect guard (v15)
        ("training_sessions", "account_id", "VARCHAR(120)"),
        ("training_sessions", "protected", "BOOLEAN NOT NULL DEFAULT false"),
        # training_captures AX faucet yield (v16)
        ("training_captures", "ax_candidate_count", "INTEGER NOT NULL DEFAULT 0"),
        # observed_jobs → canonical Job link (v17). The sighting keeps its own identity and gains
        # a pointer; nullable because scraping must never block on resolving what it just saw.
        ("observed_jobs", "canonical_job_key", "VARCHAR(64)"),
        # jobs: observations split from distinct sightings (v18) — see the Job model docstring.
        ("jobs", "seen_count", "INTEGER NOT NULL DEFAULT 0"),
        # applications → the Search that produced them (v19) — provenance to query+date, so
        # "which search led to this application" is a join, not a guess (operator, 2026-08-10).
        ("applications", "search_id", "INTEGER"),
        # job_decisions → the same Search join (v19): picks and passes tie to the query+date that
        # put the cards on the table, not just its display string.
        ("job_decisions", "search_id", "INTEGER"),
        # searches: which PROCESS a row is (v20) — `query` (someone typed one) or `feed` (the
        # engine's own front page). A feed run is a unit of work inside a living session, exactly
        # what this table already modelled, so it got a discriminator rather than a twin table.
        ("searches", "kind", "VARCHAR(20) NOT NULL DEFAULT 'query'"),
        ("searches", "surface", "VARCHAR(60) NOT NULL DEFAULT ''"),
    ]
    with engine.connect() as conn:
        for table, col, definition in additions:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {definition}"))
                conn.commit()
            except Exception:
                conn.rollback()
