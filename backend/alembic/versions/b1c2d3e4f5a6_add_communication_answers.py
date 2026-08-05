"""add communication_answers table

Revision ID: b1c2d3e4f5a6
Revises: 34716348e109
Create Date: 2026-05-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = '34716348e109'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table in insp.get_table_names()


def upgrade() -> None:
    # Idempotent: the table may already exist from a create_all() bootstrap on a
    # dev DB whose alembic version row predates this migration.
    if _has_table('communication_answers'):
        return
    op.create_table(
        'communication_answers',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('interview_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.String(length=36), nullable=False),
        sa.Column('candidate_id', sa.String(length=36), nullable=True),
        sa.Column('audio_path', sa.String(length=500), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('words_per_minute', sa.Float(), nullable=True),
        sa.Column('filler_count', sa.Integer(), nullable=True),
        sa.Column('filler_ratio', sa.Float(), nullable=True),
        sa.Column('pause_count', sa.Integer(), nullable=True),
        sa.Column('speaking_ratio', sa.Float(), nullable=True),
        sa.Column('acoustic_confidence', sa.Float(), nullable=True),
        sa.Column('communication_score', sa.Float(), nullable=True),
        sa.Column('breakdown_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id']),
        sa.ForeignKeyConstraint(['question_id'], ['interview_questions.id']),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_communication_answers_interview_id', 'communication_answers', ['interview_id'])
    op.create_index('ix_communication_answers_question_id', 'communication_answers', ['question_id'])
    op.create_index('ix_communication_answers_candidate_id', 'communication_answers', ['candidate_id'])


def downgrade() -> None:
    op.drop_index('ix_communication_answers_candidate_id', table_name='communication_answers')
    op.drop_index('ix_communication_answers_question_id', table_name='communication_answers')
    op.drop_index('ix_communication_answers_interview_id', table_name='communication_answers')
    op.drop_table('communication_answers')
