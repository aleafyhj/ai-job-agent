"""Persist safe BOSS immediate-communication attempts."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE communications (
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    profile_id UUID NOT NULL,
    job_id UUID NOT NULL,
    analysis_id UUID NOT NULL,
    status VARCHAR(32) DEFAULT 'pending' NOT NULL,
    local_date DATE NOT NULL,
    policy_timezone TEXT NOT NULL,
    before_artifact_id UUID NOT NULL,
    after_artifact_id UUID,
    button_coordinate JSONB DEFAULT '{}'::jsonb NOT NULL,
    result JSONB DEFAULT '{}'::jsonb NOT NULL,
    attempted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    confirmed_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_communications PRIMARY KEY (id),
    CONSTRAINT ck_communications_status
        CHECK (status IN ('pending','confirmed','unknown')),
    CONSTRAINT ck_communications_confirmation_evidence
        CHECK (status <> 'confirmed' OR confirmed_at IS NOT NULL),
    CONSTRAINT fk_communications_profile_id_profiles
        FOREIGN KEY(profile_id) REFERENCES profiles (id),
    CONSTRAINT fk_communications_job_id_jobs
        FOREIGN KEY(job_id) REFERENCES jobs (id),
    CONSTRAINT fk_communications_analysis_id_analyses
        FOREIGN KEY(analysis_id) REFERENCES analyses (id),
    CONSTRAINT fk_communications_before_artifact_id_artifacts
        FOREIGN KEY(before_artifact_id) REFERENCES artifacts (id),
    CONSTRAINT fk_communications_after_artifact_id_artifacts
        FOREIGN KEY(after_artifact_id) REFERENCES artifacts (id),
    CONSTRAINT uq_communications_analysis_id UNIQUE (analysis_id),
    CONSTRAINT uq_communications_profile_id_job_id UNIQUE (profile_id, job_id)
)
""")
    op.execute("CREATE INDEX ix_communications_profile_id ON communications (profile_id)")
    op.execute("CREATE INDEX ix_communications_job_id ON communications (job_id)")
    op.execute("CREATE INDEX ix_communications_analysis_id ON communications (analysis_id)")
    op.execute(
        "CREATE INDEX ix_communications_before_artifact_id ON communications (before_artifact_id)"
    )
    op.execute(
        "CREATE INDEX ix_communications_after_artifact_id ON communications (after_artifact_id)"
    )
    op.execute(
        "CREATE INDEX ix_communications_profile_day ON communications (profile_id, local_date)"
    )


def downgrade():
    op.execute("DROP TABLE communications")
