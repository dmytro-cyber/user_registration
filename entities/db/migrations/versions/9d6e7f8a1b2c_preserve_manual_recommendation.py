"""preserve manual recommendation decisions

Revision ID: 9d6e7f8a1b2c
Revises: 3103b8be0e0d
Create Date: 2026-09-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9d6e7f8a1b2c"
down_revision: Union[str, None] = "3103b8be0e0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cars",
        sa.Column(
            "recommendation_manually_set",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("cars", "recommendation_manually_set")
