"""Add append-only human reviews for model analyses."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE analysis_reviews (
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    analysis_id UUID NOT NULL,
    decision VARCHAR(32) DEFAULT 'needs_changes' NOT NULL,
    reviewer VARCHAR(100) DEFAULT 'local_user' NOT NULL,
    notes TEXT,
    requirement_reviews JSONB DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT pk_analysis_reviews PRIMARY KEY (id),
    CONSTRAINT ck_analysis_reviews_decision
        CHECK (decision IN ('approved','rejected','needs_changes')),
    CONSTRAINT fk_analysis_reviews_analysis_id_analyses
        FOREIGN KEY(analysis_id) REFERENCES analyses (id)
)
""")
    op.execute("CREATE INDEX ix_analysis_reviews_analysis_id ON analysis_reviews (analysis_id)")
    op.execute("""
CREATE INDEX ix_analysis_reviews_latest
ON analysis_reviews (analysis_id, created_at DESC, id DESC)
""")
    op.execute("""
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON analysis_reviews
FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()
""")


def downgrade():
    op.execute("DROP TABLE analysis_reviews")
