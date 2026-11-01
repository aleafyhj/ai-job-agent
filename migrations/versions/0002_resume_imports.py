"""Persist evidence-backed resume imports and generated profile versions."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE resume_imports (
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    profile_id UUID NOT NULL,
    resume_id UUID NOT NULL,
    source_profile_version_id UUID NOT NULL,
    target_profile_version_id UUID NOT NULL,
    status VARCHAR(32) DEFAULT 'needs_review' NOT NULL,
    mode VARCHAR(32) DEFAULT 'fill_missing' NOT NULL,
    source_hash CHAR(64) NOT NULL,
    parser_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    extraction JSONB DEFAULT '{}'::jsonb NOT NULL,
    warnings JSONB DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT pk_resume_imports PRIMARY KEY (id),
    CONSTRAINT ck_resume_imports_status CHECK (status IN ('ready','needs_review')),
    CONSTRAINT ck_resume_imports_mode
        CHECK (mode IN ('fill_missing','replace_resume_fields')),
    CONSTRAINT uq_resume_imports_resume_id_prompt_version_model
        UNIQUE (resume_id, prompt_version, model),
    CONSTRAINT fk_resume_imports_profile_id_profiles
        FOREIGN KEY(profile_id) REFERENCES profiles (id),
    CONSTRAINT fk_resume_imports_resume_id_resumes
        FOREIGN KEY(resume_id) REFERENCES resumes (id),
    CONSTRAINT fk_resume_imports_source_profile_version_id_profile_versions
        FOREIGN KEY(source_profile_version_id) REFERENCES profile_versions (id),
    CONSTRAINT fk_resume_imports_target_profile_version_id_profile_versions
        FOREIGN KEY(target_profile_version_id) REFERENCES profile_versions (id),
    CONSTRAINT fk_resume_import_resume_owner
        FOREIGN KEY(profile_id, resume_id) REFERENCES resumes (profile_id, id),
    CONSTRAINT fk_resume_import_source_owner
        FOREIGN KEY(profile_id, source_profile_version_id)
        REFERENCES profile_versions (profile_id, id),
    CONSTRAINT fk_resume_import_target_owner
        FOREIGN KEY(profile_id, target_profile_version_id)
        REFERENCES profile_versions (profile_id, id)
)
""")
    op.execute("CREATE INDEX ix_resume_imports_profile_id ON resume_imports (profile_id)")
    op.execute("CREATE INDEX ix_resume_imports_resume_id ON resume_imports (resume_id)")
    op.execute(
        "CREATE INDEX ix_resume_imports_source_profile_version_id "
        "ON resume_imports (source_profile_version_id)"
    )
    op.execute(
        "CREATE INDEX ix_resume_imports_target_profile_version_id "
        "ON resume_imports (target_profile_version_id)"
    )
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON resume_imports
FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")


def downgrade():
    op.execute("DROP TABLE resume_imports")
