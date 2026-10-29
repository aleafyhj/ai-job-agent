"""PostgreSQL schema using SQLAlchemy Core; no database access at import time."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def col(name, kind=sa.Text, *, nullable=False, default=None):
    return sa.Column(name, kind, nullable=nullable, server_default=default)


def ref(name, target, *, nullable=False):
    return sa.Column(name, UUID, sa.ForeignKey(target), nullable=nullable, index=True)


def doc(name, default="{}"):
    return col(name, JSONB, default=sa.text(f"'{default}'::jsonb"))


def timestamp(name, *, nullable=True):
    return col(name, sa.DateTime(timezone=True), nullable=nullable)


def check(expression, name):
    return sa.CheckConstraint(expression, name=name)


def state(values, default, name="status"):
    return (
        col(name, sa.String(32), default=sa.text(f"'{default}'")),
        check(f"{name} IN ({','.join(repr(value) for value in values.split())})", name),
    )


def table(name, *columns):
    return sa.Table(
        name,
        metadata,
        sa.Column("id", UUID, primary_key=True, default=uuid4),
        col("created_at", sa.DateTime(timezone=True), default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        *columns,
    )


artifacts = table(
    "artifacts",
    col("kind"),
    col("storage_key"),
    col("mime_type"),
    col("sha256", sa.CHAR(64)),
    col("size", sa.BigInteger),
    timestamp("expires_at"),
    doc("metadata"),
    sa.UniqueConstraint("storage_key"),
    check("size >= 0", "size"),
)
profiles = table(
    "profiles",
    col("name", sa.String(100)),
    col("current_version_id", UUID, nullable=True),
    col("default_resume_id", UUID, nullable=True),
)
profile_versions = table(
    "profile_versions",
    ref("profile_id", "profiles.id"),
    col("version", sa.Integer),
    doc("skills", "[]"),
    col("experience_years", sa.Numeric(5, 2), nullable=True),
    doc("education"),
    doc("preferences"),
    doc("facts"),
    sa.UniqueConstraint("profile_id", "version"),
    sa.UniqueConstraint("profile_id", "id"),
    check("version > 0", "version"),
    check("experience_years >= 0", "experience_years"),
)
resumes = table(
    "resumes",
    ref("profile_id", "profiles.id"),
    col("version", sa.Integer),
    ref("artifact_id", "artifacts.id"),
    col("sha256", sa.CHAR(64)),
    col("original_filename"),
    col("mime_type"),
    col("size", sa.BigInteger),
    sa.UniqueConstraint("profile_id", "version"),
    sa.UniqueConstraint("profile_id", "id"),
    check("version > 0", "version"),
    check("size > 0", "size"),
)
platform_accounts = table(
    "platform_accounts",
    col("platform_key", sa.String(64)),
    col("label"),
    col("session_secret_ref", nullable=True),
    *state("unknown valid expired needs_input", "unknown", "session_status"),
    timestamp("verified_at"),
    col("enabled", sa.Boolean, default=sa.true()),
)
policies = table("policies", col("name"), col("current_version_id", UUID, nullable=True))
policy_versions = table(
    "policy_versions",
    ref("policy_id", "policies.id"),
    col("version", sa.Integer),
    col("threshold", sa.Numeric(9, 6), default="70"),
    *state("gte gt", "gte", "comparison"),
    col("daily_limit", sa.Integer, default="20"),
    col("timezone", default="Asia/Shanghai"),
    col("auto_apply", sa.Boolean, default=sa.false()),
    doc("blocked_companies", "[]"),
    doc("allowed_platforms", "[]"),
    col("scoring_rule_version", default="v1"),
    doc("equivalence_map"),
    sa.UniqueConstraint("policy_id", "version"),
    sa.UniqueConstraint("policy_id", "id"),
    check("version > 0", "version"),
    check("threshold BETWEEN 0 AND 100", "threshold"),
    check("daily_limit > 0", "daily_limit"),
)
search_plans = table(
    "search_plans",
    col("name"),
    ref("profile_id", "profiles.id"),
    ref("policy_id", "policies.id"),
    doc("config"),
    col("version", sa.Integer, default="1"),
    col("schedule_cron", nullable=True),
    col("timezone", default="Asia/Shanghai"),
    col("enabled", sa.Boolean, default=sa.false()),
    timestamp("next_run_at"),
    check("version > 0", "version"),
)
search_runs = table(
    "search_runs",
    ref("plan_id", "search_plans.id"),
    col("plan_version", sa.Integer),
    ref("profile_version_id", "profile_versions.id"),
    ref("policy_version_id", "policy_versions.id"),
    doc("config_snapshot"),
    *state("collect_only analyze_only auto_apply", "collect_only", "mode"),
    *state(
        "queued running pausing paused cancelling cancelled completed partial_failed failed",
        "queued",
    ),
    timestamp("scheduled_for"),
    timestamp("started_at"),
    timestamp("finished_at"),
    doc("error"),
    sa.UniqueConstraint("plan_id", "scheduled_for"),
    check("plan_version > 0", "plan_version"),
)
jobs = table(
    "jobs",
    col("platform_key", sa.String(64)),
    col("external_job_id", nullable=True),
    col("canonical_url"),
    col("identity_key"),
    col("company_name"),
    col("title"),
    *state("active closed unavailable", "active"),
    col("latest_snapshot_id", UUID, nullable=True),
    col("first_seen_at", sa.DateTime(timezone=True), default=sa.func.now()),
    col("last_seen_at", sa.DateTime(timezone=True), default=sa.func.now()),
    sa.UniqueConstraint("platform_key", "identity_key"),
)
job_snapshots = table(
    "job_snapshots",
    ref("job_id", "jobs.id"),
    col("content_hash", sa.CHAR(64)),
    ref("raw_artifact_id", "artifacts.id"),
    doc("normalized"),
    col("captured_at", sa.DateTime(timezone=True), default=sa.func.now()),
    col("extractor_version"),
    sa.UniqueConstraint("job_id", "content_hash"),
    sa.UniqueConstraint("job_id", "id"),
)
run_jobs = table(
    "run_jobs",
    ref("run_id", "search_runs.id"),
    ref("job_id", "jobs.id"),
    ref("snapshot_id", "job_snapshots.id"),
    sa.UniqueConstraint("run_id", "job_id"),
    sa.ForeignKeyConstraint(
        ["job_id", "snapshot_id"],
        ["job_snapshots.job_id", "job_snapshots.id"],
        name="fk_run_snapshot_owner",
    ),
)
analyses = table(
    "analyses",
    ref("job_snapshot_id", "job_snapshots.id"),
    ref("profile_version_id", "profile_versions.id"),
    ref("policy_version_id", "policy_versions.id"),
    *state("queued running ready needs_review failed", "queued"),
    col("score", sa.Numeric(9, 6), nullable=True),
    *state("pass fail unknown", "unknown", "hard_status"),
    col("eligible", sa.Boolean, default=sa.false()),
    doc("requirements", "[]"),
    doc("match_evidence"),
    doc("uncertainties", "[]"),
    col("prompt_version"),
    col("model"),
    col("input_hash", sa.CHAR(64)),
    doc("error"),
    sa.UniqueConstraint(
        "job_snapshot_id",
        "profile_version_id",
        "policy_version_id",
        "prompt_version",
        "model",
        name="uq_analysis_versions",
    ),
    check("score BETWEEN 0 AND 100", "score"),
    check(
        "NOT eligible OR (status = 'ready' AND score IS NOT NULL AND hard_status = 'pass')",
        "eligible",
    ),
)
applications = table(
    "applications",
    ref("profile_id", "profiles.id"),
    ref("job_id", "jobs.id"),
    ref("platform_account_id", "platform_accounts.id"),
    ref("analysis_id", "analyses.id"),
    ref("resume_id", "resumes.id"),
    ref("run_id", "search_runs.id", nullable=True),
    *state(
        "queued preparing submitting submitted unknown failed needs_input blocked cancelled",
        "queued",
    ),
    col("blocked_reason", nullable=True),
    doc("required_inputs", "[]"),
    timestamp("submitted_at"),
    col("confirmation_ref", nullable=True),
    sa.UniqueConstraint("profile_id", "job_id"),
    sa.ForeignKeyConstraint(
        ["profile_id", "resume_id"],
        ["resumes.profile_id", "resumes.id"],
        name="fk_application_resume_owner",
    ),
    check(
        "status <> 'submitted' OR (submitted_at IS NOT NULL AND confirmation_ref IS NOT NULL)",
        "submission_evidence",
    ),
)
application_attempts = table(
    "application_attempts",
    ref("application_id", "applications.id"),
    col("attempt_no", sa.Integer),
    *state("prepared submitting confirmed not_submitted unknown", "prepared"),
    doc("form_snapshot"),
    doc("answers"),
    col("started_at", sa.DateTime(timezone=True), default=sa.func.now()),
    timestamp("finished_at"),
    doc("result"),
    doc("artifact_ids", "[]"),
    sa.UniqueConstraint("application_id", "attempt_no"),
    check("attempt_no > 0", "attempt_no"),
)
tasks = table(
    "tasks",
    col("type", sa.String(64)),
    doc("payload"),
    ref("run_id", "search_runs.id", nullable=True),
    ref("application_id", "applications.id", nullable=True),
    *state("queued running succeeded failed waiting_input cancelled retry_wait", "queued"),
    col("priority", sa.Integer, default="0"),
    col("available_at", sa.DateTime(timezone=True), default=sa.func.now()),
    col("lease_owner", nullable=True),
    timestamp("lease_expires_at"),
    timestamp("heartbeat_at"),
    col("attempt_count", sa.Integer, default="0"),
    col("max_attempts", sa.Integer, default="3"),
    doc("progress"),
    doc("error"),
    check("attempt_count >= 0 AND max_attempts > 0", "attempts"),
    check(
        "status <> 'running' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
        "running_lease",
    ),
)
daily_quotas = table(
    "daily_quotas",
    ref("profile_id", "profiles.id"),
    ref("platform_account_id", "platform_accounts.id"),
    col("local_date", sa.Date),
    col("policy_timezone"),
    col("reserved", sa.Integer, default="0"),
    col("submitted", sa.Integer, default="0"),
    sa.UniqueConstraint(
        "profile_id", "platform_account_id", "local_date", "policy_timezone", name="uq_quota_day"
    ),
    check("reserved >= 0 AND submitted >= 0", "counts"),
)
quota_reservations = table(
    "quota_reservations",
    ref("application_id", "applications.id"),
    ref("quota_id", "daily_quotas.id"),
    *state("reserved consumed released", "reserved", "state"),
    sa.UniqueConstraint("application_id"),
)
executor_leases = sa.Table(
    "executor_leases",
    metadata,
    sa.Column("resource_key", sa.String(255), primary_key=True),
    col("lease_owner"),
    col("lease_expires_at", sa.DateTime(timezone=True)),
    col("fencing_token", sa.BigInteger),
    check("fencing_token > 0", "fencing_token"),
)
llm_calls = table(
    "llm_calls",
    ref("analysis_id", "analyses.id"),
    col("provider"),
    col("model"),
    col("prompt_version"),
    col("input_hash", sa.CHAR(64)),
    ref("output_artifact_id", "artifacts.id", nullable=True),
    col("tokens_in", sa.Integer, nullable=True),
    col("tokens_out", sa.Integer, nullable=True),
    col("latency_ms", sa.Integer),
    *state("succeeded failed", "succeeded"),
    doc("error"),
    check("tokens_in >= 0 AND tokens_out >= 0 AND latency_ms >= 0", "usage"),
)
audit_events = table(
    "audit_events",
    ref("run_id", "search_runs.id", nullable=True),
    ref("application_id", "applications.id", nullable=True),
    ref("task_id", "tasks.id", nullable=True),
    col("event_type"),
    col("actor"),
    doc("details"),
    col("occurred_at", sa.DateTime(timezone=True), default=sa.func.now()),
)
idempotency_keys = table(
    "idempotency_keys",
    col("scope"),
    col("key", sa.String(128)),
    col("request_hash", sa.CHAR(64)),
    col("response_status", sa.Integer),
    doc("response_body"),
    col("expires_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("scope", "key"),
    check("response_status BETWEEN 100 AND 599", "response_status"),
)

# Named ALTER constraints break circular creation order and enforce ownership.
for root, pointer, child, owner in (
    (profiles, "current_version_id", profile_versions, "profile_id"),
    (profiles, "default_resume_id", resumes, "profile_id"),
    (policies, "current_version_id", policy_versions, "policy_id"),
    (jobs, "latest_snapshot_id", job_snapshots, "job_id"),
):
    root.append_constraint(
        sa.ForeignKeyConstraint(
            ["id", pointer],
            [child.c[owner], child.c.id],
            name=f"fk_{root.name}_{pointer}_owner",
            use_alter=True,
        )
    )

sa.Index("ix_jobs_platform_status_seen", jobs.c.platform_key, jobs.c.status, jobs.c.last_seen_at)
sa.Index(
    "ix_analyses_profile_score",
    analyses.c.profile_version_id,
    analyses.c.status,
    analyses.c.score.desc(),
)
sa.Index("ix_applications_status_created", applications.c.status, applications.c.created_at)
sa.Index(
    "ix_applications_account_submitted",
    applications.c.platform_account_id,
    applications.c.submitted_at,
)
sa.Index(
    "ix_tasks_claim",
    tasks.c.priority.desc(),
    tasks.c.available_at,
    tasks.c.created_at,
    postgresql_where=tasks.c.status.in_(["queued", "retry_wait"]),
)
sa.Index(
    "ix_tasks_lease_expiry", tasks.c.lease_expires_at, postgresql_where=tasks.c.status == "running"
)
sa.Index("ix_audit_application_time", audit_events.c.application_id, audit_events.c.occurred_at)

# Applied by the initial migration; future schema changes require new migrations.
IMMUTABLE_TABLES = ("profile_versions", "policy_versions", "resumes", "job_snapshots")
