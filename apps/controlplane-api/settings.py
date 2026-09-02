import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agentos"
    # Log every SQL statement (SQLAlchemy engine echo). OFF by default — echo=True floods
    # the dev log (was 25MB) and drowns real signal (WARNINGs, escalations). Set SQL_ECHO=true
    # in .env for a debugging session that needs to see queries.
    sql_echo: bool = False
    observer_artifacts_dir: str = "../mcp/output"
    # Local asset store (listing photos). Stub for eventual cloud (S3) storage; empty → repo-root
    # /assets. See assets.py + assets/README.md.
    assets_dir: str = ""
    capture_server_url: str = "http://localhost:8082"
    chrome_cdp_url: str = "http://localhost:9222"
    chrome_binary_path: str = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    training_chrome_profiles_dir: str = "/tmp/agent-platform-training-chrome"
    training_chrome_port_start: int = 9322
    redis_url: str = "redis://localhost:6379/0"
    # Anthropic key for the Haiku SoM picker. Read from .env here so adding it
    # never crashes Settings; extra="ignore" also tolerates other future .env keys.
    anthropic_api_key: str = ""
    # Hard cap on AUTONOMOUS (no-human-in-the-loop) Claude spend per rolling 7 days.
    # When exceeded, the budget guard blocks further LLM calls and the loop must
    # escalate to a human. Keeps testing/runtime cost bounded. Override in .env.
    anthropic_weekly_budget_usd: float = 5.0
    # Teacher-first economics (operator-directed 2026-08-09): on an ATTENDED drive the
    # escalation rung is the session-Claude teacher — already paid for — so the Haiku API rung
    # is demoted to unattended runs. OFF means an attended `/api/controller/run` never wires
    # Haiku regardless of `mode`; flip in .env only to deliberately re-admit the API rung while
    # a teacher is present. Enforced at the model-wiring line (the one place that decides the
    # cascade's membership), because authority is graded AFTER decide() has already spent a call.
    # NAMED exemptions this flag does NOT govern, each an explicit per-call opt-in and all under
    # the weekly cap: `teach_session`'s SHADOW scorer (never acts; it is the only source of the
    # shadow-agreement promotion metric, so gating it would blind graduation), and the direct
    # endpoints /decide_model, /observe?allow_model, /decide_cascade with a posted budget.
    haiku_attended_allowed: bool = False
    # Train-as-we-go, actually as-we-go (2026-08-09): a teacher label lands → the transition
    # table and the perception witnesses refit in the background, so "we are training and
    # labeling along the way" is a property of the label write, not of remembering a button.
    # OFF only for A/B comparisons or when a batch of labels is being written in one sitting.
    train_on_label: bool = True
    # Write-time vector banking (2026-09-02, PLAN_inhouse_reasoner_v1 §4): every journaled
    # decision and transition embeds into vectors.db at its choke point, so each drive feeds
    # the precedent engine as it happens. Same doctrine as train_on_label — the crank is the
    # write. OFF leaves the idempotent backfill CLI as the only path into the store.
    precedent_write_vectors: bool = True
    # The precedent rung in the SHADOW seat (§11 item 2): every shadow comparison consults the
    # $0 retrieval reasoner, so each crank journals what the in-house seat would have decided —
    # the per-scenario agreement data the two-bar gate promotes on. Free by construction.
    precedent_shadow: bool = True
    # The precedent rung ACTING (default OFF — shadow-first doctrine). When on, the seat's
    # proposals enter the live cascade at the student slot; `precedent` is in PROPOSE_RUNGS,
    # so they are reviewed before acting, and authority()'s gates still bind. Flip only when
    # the shadow numbers say a scenario earned it.
    precedent_acting: bool = False
    # Test-account credentials for capturing logged-in states, read from the
    # GITIGNORED .env only. NEVER hardcode a real value here and NEVER log these —
    # see _login_secrets(). Use a throwaway/test account, not a primary one.
    fb_username: str = ""
    fb_password: str = ""
    indeed_username: str = ""
    indeed_password: str = ""
    gmail_username: str = ""
    gmail_password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()

# Export the key into the process env so `anthropic.Anthropic()` (which reads
# ANTHROPIC_API_KEY from os.environ) works server-side without extra plumbing.
if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
