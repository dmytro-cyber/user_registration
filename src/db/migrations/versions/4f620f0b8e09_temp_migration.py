"""temp_migration

Revision ID: 4f620f0b8e09
Revises: 3ca4b7ba1290
Create Date: 2025-03-20 09:39:03.693452

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4f620f0b8e09"
down_revision: Union[str, None] = "3ca4b7ba1290"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "users", "date_of_birth", existing_type=postgresql.TIMESTAMP(), type_=sa.Date(), existing_nullable=True
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "users", "date_of_birth", existing_type=sa.Date(), type_=postgresql.TIMESTAMP(), existing_nullable=True
    )
    # ### end Alembic commands ###
