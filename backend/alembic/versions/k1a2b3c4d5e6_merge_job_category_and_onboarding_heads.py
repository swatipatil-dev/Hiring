"""Merge the two open heads (job_category + onboarding branches)

The migration history had branched off ``g7b8c9d0e1f2`` into two heads that were
never reconciled:

* ``abf4d61600ac`` — add_job_category_and_jd_templates
* ``i9d0e1f2g3h4`` — extend_company_onboarding (via h8c9d0e1f2g3)

The local DB physically contains the schema from *both* branches (verified:
``jd_templates``, ``job_postings.job_category``, ``users.uq_users_company_email``,
``companies.legal_name``, ``subscription_plans.currency`` all present), but its
``alembic_version`` was left at a phantom revision with no script file. This is an
empty merge revision that unifies the two heads so the history is single-headed
again and downstream migrations (the coding-judge tables) have one clear parent.

Revision ID: k1a2b3c4d5e6
Revises: abf4d61600ac, i9d0e1f2g3h4
Create Date: 2026-05-27 00:00:00.000000
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'k1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = ('abf4d61600ac', 'i9d0e1f2g3h4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pure merge revision — no schema changes.
    pass


def downgrade() -> None:
    # Pure merge revision — no schema changes.
    pass
