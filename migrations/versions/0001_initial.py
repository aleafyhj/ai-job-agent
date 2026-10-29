"""Initial PostgreSQL schema, frozen independently of live model definitions."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE artifacts (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	kind TEXT NOT NULL, 
	storage_key TEXT NOT NULL, 
	mime_type TEXT NOT NULL, 
	sha256 CHAR(64) NOT NULL, 
	size BIGINT NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_artifacts PRIMARY KEY (id), 
	CONSTRAINT uq_artifacts_storage_key UNIQUE (storage_key), 
	CONSTRAINT ck_artifacts_size CHECK (size >= 0)
)
""")
    op.execute("""
CREATE TABLE profiles (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	current_version_id UUID, 
	default_resume_id UUID, 
	CONSTRAINT pk_profiles PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE platform_accounts (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	platform_key VARCHAR(64) NOT NULL, 
	label TEXT NOT NULL, 
	session_secret_ref TEXT, 
	session_status VARCHAR(32) DEFAULT 'unknown' NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	CONSTRAINT pk_platform_accounts PRIMARY KEY (id), 
	CONSTRAINT ck_platform_accounts_session_status CHECK (session_status IN ('unknown','valid','expired','needs_input'))
)
""")
    op.execute("""
CREATE TABLE policies (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	name TEXT NOT NULL, 
	current_version_id UUID, 
	CONSTRAINT pk_policies PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE jobs (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	platform_key VARCHAR(64) NOT NULL, 
	external_job_id TEXT, 
	canonical_url TEXT NOT NULL, 
	identity_key TEXT NOT NULL, 
	company_name TEXT NOT NULL, 
	title TEXT NOT NULL, 
	status VARCHAR(32) DEFAULT 'active' NOT NULL, 
	latest_snapshot_id UUID, 
	first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_jobs PRIMARY KEY (id), 
	CONSTRAINT ck_jobs_status CHECK (status IN ('active','closed','unavailable')), 
	CONSTRAINT uq_jobs_platform_key_identity_key UNIQUE (platform_key, identity_key)
)
""")
    op.execute("""
CREATE INDEX ix_jobs_platform_status_seen ON jobs (platform_key, status, last_seen_at)
""")
    op.execute("""
CREATE TABLE executor_leases (
	resource_key VARCHAR(255) NOT NULL, 
	lease_owner TEXT NOT NULL, 
	lease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	fencing_token BIGINT NOT NULL, 
	CONSTRAINT pk_executor_leases PRIMARY KEY (resource_key), 
	CONSTRAINT ck_executor_leases_fencing_token CHECK (fencing_token > 0)
)
""")
    op.execute("""
CREATE TABLE idempotency_keys (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	scope TEXT NOT NULL, 
	key VARCHAR(128) NOT NULL, 
	request_hash CHAR(64) NOT NULL, 
	response_status INTEGER NOT NULL, 
	response_body JSONB DEFAULT '{}'::jsonb NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_idempotency_keys PRIMARY KEY (id), 
	CONSTRAINT uq_idempotency_keys_scope_key UNIQUE (scope, key), 
	CONSTRAINT ck_idempotency_keys_response_status CHECK (response_status BETWEEN 100 AND 599)
)
""")
    op.execute("""
CREATE TABLE profile_versions (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	profile_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	skills JSONB DEFAULT '[]'::jsonb NOT NULL, 
	experience_years NUMERIC(5, 2), 
	education JSONB DEFAULT '{}'::jsonb NOT NULL, 
	preferences JSONB DEFAULT '{}'::jsonb NOT NULL, 
	facts JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_profile_versions PRIMARY KEY (id), 
	CONSTRAINT uq_profile_versions_profile_id_version UNIQUE (profile_id, version), 
	CONSTRAINT uq_profile_versions_profile_id_id UNIQUE (profile_id, id), 
	CONSTRAINT ck_profile_versions_version CHECK (version > 0), 
	CONSTRAINT ck_profile_versions_experience_years CHECK (experience_years >= 0), 
	CONSTRAINT fk_profile_versions_profile_id_profiles FOREIGN KEY(profile_id) REFERENCES profiles (id)
)
""")
    op.execute("""
CREATE INDEX ix_profile_versions_profile_id ON profile_versions (profile_id)
""")
    op.execute("""
CREATE TABLE resumes (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	profile_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	artifact_id UUID NOT NULL, 
	sha256 CHAR(64) NOT NULL, 
	original_filename TEXT NOT NULL, 
	mime_type TEXT NOT NULL, 
	size BIGINT NOT NULL, 
	CONSTRAINT pk_resumes PRIMARY KEY (id), 
	CONSTRAINT uq_resumes_profile_id_version UNIQUE (profile_id, version), 
	CONSTRAINT uq_resumes_profile_id_id UNIQUE (profile_id, id), 
	CONSTRAINT ck_resumes_version CHECK (version > 0), 
	CONSTRAINT ck_resumes_size CHECK (size > 0), 
	CONSTRAINT fk_resumes_profile_id_profiles FOREIGN KEY(profile_id) REFERENCES profiles (id), 
	CONSTRAINT fk_resumes_artifact_id_artifacts FOREIGN KEY(artifact_id) REFERENCES artifacts (id)
)
""")
    op.execute("""
CREATE INDEX ix_resumes_profile_id ON resumes (profile_id)
""")
    op.execute("""
CREATE INDEX ix_resumes_artifact_id ON resumes (artifact_id)
""")
    op.execute("""
CREATE TABLE policy_versions (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	policy_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	threshold NUMERIC(9, 6) DEFAULT '70' NOT NULL, 
	comparison VARCHAR(32) DEFAULT 'gte' NOT NULL, 
	daily_limit INTEGER DEFAULT '20' NOT NULL, 
	timezone TEXT DEFAULT 'Asia/Shanghai' NOT NULL, 
	auto_apply BOOLEAN DEFAULT false NOT NULL, 
	blocked_companies JSONB DEFAULT '[]'::jsonb NOT NULL, 
	allowed_platforms JSONB DEFAULT '[]'::jsonb NOT NULL, 
	scoring_rule_version TEXT DEFAULT 'v1' NOT NULL, 
	equivalence_map JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_policy_versions PRIMARY KEY (id), 
	CONSTRAINT ck_policy_versions_comparison CHECK (comparison IN ('gte','gt')), 
	CONSTRAINT uq_policy_versions_policy_id_version UNIQUE (policy_id, version), 
	CONSTRAINT uq_policy_versions_policy_id_id UNIQUE (policy_id, id), 
	CONSTRAINT ck_policy_versions_version CHECK (version > 0), 
	CONSTRAINT ck_policy_versions_threshold CHECK (threshold BETWEEN 0 AND 100), 
	CONSTRAINT ck_policy_versions_daily_limit CHECK (daily_limit > 0), 
	CONSTRAINT fk_policy_versions_policy_id_policies FOREIGN KEY(policy_id) REFERENCES policies (id)
)
""")
    op.execute("""
CREATE INDEX ix_policy_versions_policy_id ON policy_versions (policy_id)
""")
    op.execute("""
CREATE TABLE search_plans (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	name TEXT NOT NULL, 
	profile_id UUID NOT NULL, 
	policy_id UUID NOT NULL, 
	config JSONB DEFAULT '{}'::jsonb NOT NULL, 
	version INTEGER DEFAULT '1' NOT NULL, 
	schedule_cron TEXT, 
	timezone TEXT DEFAULT 'Asia/Shanghai' NOT NULL, 
	enabled BOOLEAN DEFAULT false NOT NULL, 
	next_run_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_search_plans PRIMARY KEY (id), 
	CONSTRAINT ck_search_plans_version CHECK (version > 0), 
	CONSTRAINT fk_search_plans_profile_id_profiles FOREIGN KEY(profile_id) REFERENCES profiles (id), 
	CONSTRAINT fk_search_plans_policy_id_policies FOREIGN KEY(policy_id) REFERENCES policies (id)
)
""")
    op.execute("""
CREATE INDEX ix_search_plans_profile_id ON search_plans (profile_id)
""")
    op.execute("""
CREATE INDEX ix_search_plans_policy_id ON search_plans (policy_id)
""")
    op.execute("""
CREATE TABLE job_snapshots (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	job_id UUID NOT NULL, 
	content_hash CHAR(64) NOT NULL, 
	raw_artifact_id UUID NOT NULL, 
	normalized JSONB DEFAULT '{}'::jsonb NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	extractor_version TEXT NOT NULL, 
	CONSTRAINT pk_job_snapshots PRIMARY KEY (id), 
	CONSTRAINT uq_job_snapshots_job_id_content_hash UNIQUE (job_id, content_hash), 
	CONSTRAINT uq_job_snapshots_job_id_id UNIQUE (job_id, id), 
	CONSTRAINT fk_job_snapshots_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), 
	CONSTRAINT fk_job_snapshots_raw_artifact_id_artifacts FOREIGN KEY(raw_artifact_id) REFERENCES artifacts (id)
)
""")
    op.execute("""
CREATE INDEX ix_job_snapshots_job_id ON job_snapshots (job_id)
""")
    op.execute("""
CREATE INDEX ix_job_snapshots_raw_artifact_id ON job_snapshots (raw_artifact_id)
""")
    op.execute("""
CREATE TABLE daily_quotas (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	profile_id UUID NOT NULL, 
	platform_account_id UUID NOT NULL, 
	local_date DATE NOT NULL, 
	policy_timezone TEXT NOT NULL, 
	reserved INTEGER DEFAULT '0' NOT NULL, 
	submitted INTEGER DEFAULT '0' NOT NULL, 
	CONSTRAINT pk_daily_quotas PRIMARY KEY (id), 
	CONSTRAINT uq_quota_day UNIQUE (profile_id, platform_account_id, local_date, policy_timezone), 
	CONSTRAINT ck_daily_quotas_counts CHECK (reserved >= 0 AND submitted >= 0), 
	CONSTRAINT fk_daily_quotas_profile_id_profiles FOREIGN KEY(profile_id) REFERENCES profiles (id), 
	CONSTRAINT fk_daily_quotas_platform_account_id_platform_accounts FOREIGN KEY(platform_account_id) REFERENCES platform_accounts (id)
)
""")
    op.execute("""
CREATE INDEX ix_daily_quotas_platform_account_id ON daily_quotas (platform_account_id)
""")
    op.execute("""
CREATE INDEX ix_daily_quotas_profile_id ON daily_quotas (profile_id)
""")
    op.execute("""
CREATE TABLE search_runs (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	plan_id UUID NOT NULL, 
	plan_version INTEGER NOT NULL, 
	profile_version_id UUID NOT NULL, 
	policy_version_id UUID NOT NULL, 
	config_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	mode VARCHAR(32) DEFAULT 'collect_only' NOT NULL, 
	status VARCHAR(32) DEFAULT 'queued' NOT NULL, 
	scheduled_for TIMESTAMP WITH TIME ZONE, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	error JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_search_runs PRIMARY KEY (id), 
	CONSTRAINT ck_search_runs_mode CHECK (mode IN ('collect_only','analyze_only','auto_apply')), 
	CONSTRAINT ck_search_runs_status CHECK (status IN ('queued','running','pausing','paused','cancelling','cancelled','completed','partial_failed','failed')), 
	CONSTRAINT uq_search_runs_plan_id_scheduled_for UNIQUE (plan_id, scheduled_for), 
	CONSTRAINT ck_search_runs_plan_version CHECK (plan_version > 0), 
	CONSTRAINT fk_search_runs_plan_id_search_plans FOREIGN KEY(plan_id) REFERENCES search_plans (id), 
	CONSTRAINT fk_search_runs_profile_version_id_profile_versions FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id), 
	CONSTRAINT fk_search_runs_policy_version_id_policy_versions FOREIGN KEY(policy_version_id) REFERENCES policy_versions (id)
)
""")
    op.execute("""
CREATE INDEX ix_search_runs_profile_version_id ON search_runs (profile_version_id)
""")
    op.execute("""
CREATE INDEX ix_search_runs_plan_id ON search_runs (plan_id)
""")
    op.execute("""
CREATE INDEX ix_search_runs_policy_version_id ON search_runs (policy_version_id)
""")
    op.execute("""
CREATE TABLE analyses (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	job_snapshot_id UUID NOT NULL, 
	profile_version_id UUID NOT NULL, 
	policy_version_id UUID NOT NULL, 
	status VARCHAR(32) DEFAULT 'queued' NOT NULL, 
	score NUMERIC(9, 6), 
	hard_status VARCHAR(32) DEFAULT 'unknown' NOT NULL, 
	eligible BOOLEAN DEFAULT false NOT NULL, 
	requirements JSONB DEFAULT '[]'::jsonb NOT NULL, 
	match_evidence JSONB DEFAULT '{}'::jsonb NOT NULL, 
	uncertainties JSONB DEFAULT '[]'::jsonb NOT NULL, 
	prompt_version TEXT NOT NULL, 
	model TEXT NOT NULL, 
	input_hash CHAR(64) NOT NULL, 
	error JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_analyses PRIMARY KEY (id), 
	CONSTRAINT ck_analyses_status CHECK (status IN ('queued','running','ready','needs_review','failed')), 
	CONSTRAINT ck_analyses_hard_status CHECK (hard_status IN ('pass','fail','unknown')), 
	CONSTRAINT uq_analysis_versions UNIQUE (job_snapshot_id, profile_version_id, policy_version_id, prompt_version, model), 
	CONSTRAINT ck_analyses_score CHECK (score BETWEEN 0 AND 100), 
	CONSTRAINT ck_analyses_eligible CHECK (NOT eligible OR (status = 'ready' AND score IS NOT NULL AND hard_status = 'pass')), 
	CONSTRAINT fk_analyses_job_snapshot_id_job_snapshots FOREIGN KEY(job_snapshot_id) REFERENCES job_snapshots (id), 
	CONSTRAINT fk_analyses_profile_version_id_profile_versions FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id), 
	CONSTRAINT fk_analyses_policy_version_id_policy_versions FOREIGN KEY(policy_version_id) REFERENCES policy_versions (id)
)
""")
    op.execute("""
CREATE INDEX ix_analyses_profile_version_id ON analyses (profile_version_id)
""")
    op.execute("""
CREATE INDEX ix_analyses_job_snapshot_id ON analyses (job_snapshot_id)
""")
    op.execute("""
CREATE INDEX ix_analyses_policy_version_id ON analyses (policy_version_id)
""")
    op.execute("""
CREATE INDEX ix_analyses_profile_score ON analyses (profile_version_id, status, score DESC)
""")
    op.execute("""
CREATE TABLE run_jobs (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	run_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	snapshot_id UUID NOT NULL, 
	CONSTRAINT pk_run_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_run_jobs_run_id_job_id UNIQUE (run_id, job_id), 
	CONSTRAINT fk_run_snapshot_owner FOREIGN KEY(job_id, snapshot_id) REFERENCES job_snapshots (job_id, id), 
	CONSTRAINT fk_run_jobs_run_id_search_runs FOREIGN KEY(run_id) REFERENCES search_runs (id), 
	CONSTRAINT fk_run_jobs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), 
	CONSTRAINT fk_run_jobs_snapshot_id_job_snapshots FOREIGN KEY(snapshot_id) REFERENCES job_snapshots (id)
)
""")
    op.execute("""
CREATE INDEX ix_run_jobs_run_id ON run_jobs (run_id)
""")
    op.execute("""
CREATE INDEX ix_run_jobs_job_id ON run_jobs (job_id)
""")
    op.execute("""
CREATE INDEX ix_run_jobs_snapshot_id ON run_jobs (snapshot_id)
""")
    op.execute("""
CREATE TABLE applications (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	profile_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	platform_account_id UUID NOT NULL, 
	analysis_id UUID NOT NULL, 
	resume_id UUID NOT NULL, 
	run_id UUID, 
	status VARCHAR(32) DEFAULT 'queued' NOT NULL, 
	blocked_reason TEXT, 
	required_inputs JSONB DEFAULT '[]'::jsonb NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE, 
	confirmation_ref TEXT, 
	CONSTRAINT pk_applications PRIMARY KEY (id), 
	CONSTRAINT ck_applications_status CHECK (status IN ('queued','preparing','submitting','submitted','unknown','failed','needs_input','blocked','cancelled')), 
	CONSTRAINT uq_applications_profile_id_job_id UNIQUE (profile_id, job_id), 
	CONSTRAINT fk_application_resume_owner FOREIGN KEY(profile_id, resume_id) REFERENCES resumes (profile_id, id), 
	CONSTRAINT ck_applications_submission_evidence CHECK (status <> 'submitted' OR (submitted_at IS NOT NULL AND confirmation_ref IS NOT NULL)), 
	CONSTRAINT fk_applications_profile_id_profiles FOREIGN KEY(profile_id) REFERENCES profiles (id), 
	CONSTRAINT fk_applications_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), 
	CONSTRAINT fk_applications_platform_account_id_platform_accounts FOREIGN KEY(platform_account_id) REFERENCES platform_accounts (id), 
	CONSTRAINT fk_applications_analysis_id_analyses FOREIGN KEY(analysis_id) REFERENCES analyses (id), 
	CONSTRAINT fk_applications_resume_id_resumes FOREIGN KEY(resume_id) REFERENCES resumes (id), 
	CONSTRAINT fk_applications_run_id_search_runs FOREIGN KEY(run_id) REFERENCES search_runs (id)
)
""")
    op.execute("""
CREATE INDEX ix_applications_status_created ON applications (status, created_at)
""")
    op.execute("""
CREATE INDEX ix_applications_analysis_id ON applications (analysis_id)
""")
    op.execute("""
CREATE INDEX ix_applications_profile_id ON applications (profile_id)
""")
    op.execute("""
CREATE INDEX ix_applications_platform_account_id ON applications (platform_account_id)
""")
    op.execute("""
CREATE INDEX ix_applications_run_id ON applications (run_id)
""")
    op.execute("""
CREATE INDEX ix_applications_account_submitted ON applications (platform_account_id, submitted_at)
""")
    op.execute("""
CREATE INDEX ix_applications_resume_id ON applications (resume_id)
""")
    op.execute("""
CREATE INDEX ix_applications_job_id ON applications (job_id)
""")
    op.execute("""
CREATE TABLE llm_calls (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	analysis_id UUID NOT NULL, 
	provider TEXT NOT NULL, 
	model TEXT NOT NULL, 
	prompt_version TEXT NOT NULL, 
	input_hash CHAR(64) NOT NULL, 
	output_artifact_id UUID, 
	tokens_in INTEGER, 
	tokens_out INTEGER, 
	latency_ms INTEGER NOT NULL, 
	status VARCHAR(32) DEFAULT 'succeeded' NOT NULL, 
	error JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_llm_calls PRIMARY KEY (id), 
	CONSTRAINT ck_llm_calls_status CHECK (status IN ('succeeded','failed')), 
	CONSTRAINT ck_llm_calls_usage CHECK (tokens_in >= 0 AND tokens_out >= 0 AND latency_ms >= 0), 
	CONSTRAINT fk_llm_calls_analysis_id_analyses FOREIGN KEY(analysis_id) REFERENCES analyses (id), 
	CONSTRAINT fk_llm_calls_output_artifact_id_artifacts FOREIGN KEY(output_artifact_id) REFERENCES artifacts (id)
)
""")
    op.execute("""
CREATE INDEX ix_llm_calls_analysis_id ON llm_calls (analysis_id)
""")
    op.execute("""
CREATE INDEX ix_llm_calls_output_artifact_id ON llm_calls (output_artifact_id)
""")
    op.execute("""
CREATE TABLE application_attempts (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	application_id UUID NOT NULL, 
	attempt_no INTEGER NOT NULL, 
	status VARCHAR(32) DEFAULT 'prepared' NOT NULL, 
	form_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	answers JSONB DEFAULT '{}'::jsonb NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	result JSONB DEFAULT '{}'::jsonb NOT NULL, 
	artifact_ids JSONB DEFAULT '[]'::jsonb NOT NULL, 
	CONSTRAINT pk_application_attempts PRIMARY KEY (id), 
	CONSTRAINT ck_application_attempts_status CHECK (status IN ('prepared','submitting','confirmed','not_submitted','unknown')), 
	CONSTRAINT uq_application_attempts_application_id_attempt_no UNIQUE (application_id, attempt_no), 
	CONSTRAINT ck_application_attempts_attempt_no CHECK (attempt_no > 0), 
	CONSTRAINT fk_application_attempts_application_id_applications FOREIGN KEY(application_id) REFERENCES applications (id)
)
""")
    op.execute("""
CREATE INDEX ix_application_attempts_application_id ON application_attempts (application_id)
""")
    op.execute("""
CREATE TABLE tasks (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	type VARCHAR(64) NOT NULL, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	run_id UUID, 
	application_id UUID, 
	status VARCHAR(32) DEFAULT 'queued' NOT NULL, 
	priority INTEGER DEFAULT '0' NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	lease_owner TEXT, 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	heartbeat_at TIMESTAMP WITH TIME ZONE, 
	attempt_count INTEGER DEFAULT '0' NOT NULL, 
	max_attempts INTEGER DEFAULT '3' NOT NULL, 
	progress JSONB DEFAULT '{}'::jsonb NOT NULL, 
	error JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_tasks PRIMARY KEY (id), 
	CONSTRAINT ck_tasks_status CHECK (status IN ('queued','running','succeeded','failed','waiting_input','cancelled','retry_wait')), 
	CONSTRAINT ck_tasks_attempts CHECK (attempt_count >= 0 AND max_attempts > 0), 
	CONSTRAINT ck_tasks_running_lease CHECK (status <> 'running' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)), 
	CONSTRAINT fk_tasks_run_id_search_runs FOREIGN KEY(run_id) REFERENCES search_runs (id), 
	CONSTRAINT fk_tasks_application_id_applications FOREIGN KEY(application_id) REFERENCES applications (id)
)
""")
    op.execute("""
CREATE INDEX ix_tasks_claim ON tasks (priority DESC, available_at, created_at) WHERE status IN ('queued', 'retry_wait')
""")
    op.execute("""
CREATE INDEX ix_tasks_run_id ON tasks (run_id)
""")
    op.execute("""
CREATE INDEX ix_tasks_lease_expiry ON tasks (lease_expires_at) WHERE status = 'running'
""")
    op.execute("""
CREATE INDEX ix_tasks_application_id ON tasks (application_id)
""")
    op.execute("""
CREATE TABLE quota_reservations (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	application_id UUID NOT NULL, 
	quota_id UUID NOT NULL, 
	state VARCHAR(32) DEFAULT 'reserved' NOT NULL, 
	CONSTRAINT pk_quota_reservations PRIMARY KEY (id), 
	CONSTRAINT ck_quota_reservations_state CHECK (state IN ('reserved','consumed','released')), 
	CONSTRAINT uq_quota_reservations_application_id UNIQUE (application_id), 
	CONSTRAINT fk_quota_reservations_application_id_applications FOREIGN KEY(application_id) REFERENCES applications (id), 
	CONSTRAINT fk_quota_reservations_quota_id_daily_quotas FOREIGN KEY(quota_id) REFERENCES daily_quotas (id)
)
""")
    op.execute("""
CREATE INDEX ix_quota_reservations_quota_id ON quota_reservations (quota_id)
""")
    op.execute("""
CREATE INDEX ix_quota_reservations_application_id ON quota_reservations (application_id)
""")
    op.execute("""
CREATE TABLE audit_events (
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	run_id UUID, 
	application_id UUID, 
	task_id UUID, 
	event_type TEXT NOT NULL, 
	actor TEXT NOT NULL, 
	details JSONB DEFAULT '{}'::jsonb NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_audit_events PRIMARY KEY (id), 
	CONSTRAINT fk_audit_events_run_id_search_runs FOREIGN KEY(run_id) REFERENCES search_runs (id), 
	CONSTRAINT fk_audit_events_application_id_applications FOREIGN KEY(application_id) REFERENCES applications (id), 
	CONSTRAINT fk_audit_events_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id)
)
""")
    op.execute("""
CREATE INDEX ix_audit_events_run_id ON audit_events (run_id)
""")
    op.execute("""
CREATE INDEX ix_audit_events_task_id ON audit_events (task_id)
""")
    op.execute("""
CREATE INDEX ix_audit_application_time ON audit_events (application_id, occurred_at)
""")
    op.execute("""
CREATE INDEX ix_audit_events_application_id ON audit_events (application_id)
""")
    op.execute("""
ALTER TABLE profiles ADD CONSTRAINT fk_profiles_current_version_id_owner FOREIGN KEY(id, current_version_id) REFERENCES profile_versions (profile_id, id)
""")
    op.execute("""
ALTER TABLE profiles ADD CONSTRAINT fk_profiles_default_resume_id_owner FOREIGN KEY(id, default_resume_id) REFERENCES resumes (profile_id, id)
""")
    op.execute("""
ALTER TABLE policies ADD CONSTRAINT fk_policies_current_version_id_owner FOREIGN KEY(id, current_version_id) REFERENCES policy_versions (policy_id, id)
""")
    op.execute("""
ALTER TABLE jobs ADD CONSTRAINT fk_jobs_latest_snapshot_id_owner FOREIGN KEY(id, latest_snapshot_id) REFERENCES job_snapshots (job_id, id)
""")
    op.execute("""
CREATE FUNCTION reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable record: insert a new version instead' USING ERRCODE = '23514';
END;
$$
""")
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON profile_versions FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON policy_versions FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON resumes FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON job_snapshots FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")
    op.execute("""
CREATE FUNCTION check_application_links() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM analyses a
        JOIN profile_versions pv ON pv.id = a.profile_version_id
        JOIN job_snapshots js ON js.id = a.job_snapshot_id
        JOIN jobs j ON j.id = js.job_id
        JOIN platform_accounts pa ON pa.id = NEW.platform_account_id
        WHERE a.id = NEW.analysis_id AND pv.profile_id = NEW.profile_id
          AND js.job_id = NEW.job_id AND pa.platform_key = j.platform_key
    ) THEN
        RAISE EXCEPTION 'application links do not match' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
""")
    op.execute("""
CREATE TRIGGER application_links BEFORE INSERT OR UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION check_application_links()
""")


def downgrade():
    op.execute("""
ALTER TABLE profiles DROP CONSTRAINT fk_profiles_current_version_id_owner
""")
    op.execute("""
ALTER TABLE profiles DROP CONSTRAINT fk_profiles_default_resume_id_owner
""")
    op.execute("""
ALTER TABLE policies DROP CONSTRAINT fk_policies_current_version_id_owner
""")
    op.execute("""
ALTER TABLE jobs DROP CONSTRAINT fk_jobs_latest_snapshot_id_owner
""")
    op.execute("""
DROP TABLE audit_events
""")
    op.execute("""
DROP TABLE quota_reservations
""")
    op.execute("""
DROP TABLE tasks
""")
    op.execute("""
DROP TABLE application_attempts
""")
    op.execute("""
DROP TABLE llm_calls
""")
    op.execute("""
DROP TABLE applications
""")
    op.execute("""
DROP TABLE run_jobs
""")
    op.execute("""
DROP TABLE analyses
""")
    op.execute("""
DROP TABLE search_runs
""")
    op.execute("""
DROP TABLE daily_quotas
""")
    op.execute("""
DROP TABLE job_snapshots
""")
    op.execute("""
DROP TABLE search_plans
""")
    op.execute("""
DROP TABLE policy_versions
""")
    op.execute("""
DROP TABLE resumes
""")
    op.execute("""
DROP TABLE profile_versions
""")
    op.execute("""
DROP TABLE idempotency_keys
""")
    op.execute("""
DROP TABLE executor_leases
""")
    op.execute("""
DROP TABLE jobs
""")
    op.execute("""
DROP TABLE policies
""")
    op.execute("""
DROP TABLE platform_accounts
""")
    op.execute("""
DROP TABLE profiles
""")
    op.execute("""
DROP TABLE artifacts
""")
    op.execute("""
DROP FUNCTION check_application_links()
""")
    op.execute("""
DROP FUNCTION reject_immutable_change()
""")
