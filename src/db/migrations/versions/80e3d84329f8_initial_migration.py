"""initial migration

Revision ID: 80e3d84329f8
Revises: 
Create Date: 2025-03-28 09:24:41.869066

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80e3d84329f8'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('cars',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vin', sa.String(), nullable=False),
    sa.Column('vehicle', sa.String(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('mileage', sa.Integer(), nullable=True),
    sa.Column('auction', sa.String(), nullable=True),
    sa.Column('auction_name', sa.String(), nullable=True),
    sa.Column('date', sa.DateTime(), nullable=True),
    sa.Column('lot', sa.Integer(), nullable=True),
    sa.Column('seller', sa.String(), nullable=True),
    sa.Column('owners', sa.Integer(), nullable=True),
    sa.Column('location', sa.String(), nullable=True),
    sa.Column('accident_count', sa.Integer(), nullable=True),
    sa.Column('has_correct_vin', sa.Boolean(), nullable=False),
    sa.Column('has_correct_owners', sa.Boolean(), nullable=False),
    sa.Column('has_correct_accidents', sa.Boolean(), nullable=False),
    sa.Column('has_correct_mileage', sa.Boolean(), nullable=False),
    sa.Column('bid', sa.Float(), nullable=True),
    sa.Column('actual_bid', sa.Float(), nullable=True),
    sa.Column('price_sold', sa.Float(), nullable=True),
    sa.Column('suggested_bid', sa.Float(), nullable=True),
    sa.Column('total_investment', sa.Float(), nullable=True),
    sa.Column('net_profit', sa.Float(), nullable=True),
    sa.Column('profit_margin', sa.Float(), nullable=True),
    sa.Column('roi', sa.Float(), nullable=True),
    sa.Column('parts_cost', sa.Float(), nullable=True),
    sa.Column('maintenance', sa.Float(), nullable=True),
    sa.Column('auction_fee', sa.Float(), nullable=True),
    sa.Column('transportation', sa.Float(), nullable=True),
    sa.Column('labor', sa.Float(), nullable=True),
    sa.Column('is_salvage', sa.Boolean(), nullable=True),
    sa.Column('parts_needed', sa.String(), nullable=True),
    sa.Column('recommendation_status', sa.Enum('RECOMMENDED', 'NOT_RECOMMENDED', name='recommendationstatus'), nullable=False),
    sa.Column('car_status', sa.Enum('AT_AUCTION', 'PURCHASED', 'IN_REPAIR', 'READY_FOR_SALE', 'SOLD', name='carstatus'), nullable=False),
    sa.Column('engine', sa.Float(), nullable=True),
    sa.Column('has_keys', sa.Boolean(), nullable=True),
    sa.Column('predicted_roi', sa.Float(), nullable=True),
    sa.Column('predicted_profit_margin', sa.Float(), nullable=True),
    sa.Column('engine_cylinder', sa.Integer(), nullable=True),
    sa.Column('drive_type', sa.String(), nullable=True),
    sa.Column('interior_color', sa.String(), nullable=True),
    sa.Column('exterior_color', sa.String(), nullable=True),
    sa.Column('body_style', sa.String(), nullable=True),
    sa.Column('style_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('vin')
    )
    op.create_index(op.f('ix_cars_id'), 'cars', ['id'], unique=False)
    op.create_index(op.f('ix_cars_mileage'), 'cars', ['mileage'], unique=False)
    op.create_table('user_roles',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.Enum('USER', 'VEHICLE_MANAGER', 'PART_MANAGER', 'ADMIN', name='userroleenum'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('car_sale_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=True),
    sa.Column('date', sa.DateTime(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('lot_number', sa.Integer(), nullable=False),
    sa.Column('final_bid', sa.Float(), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_car_sale_history_id'), 'car_sale_history', ['id'], unique=False)
    op.create_table('condition_assessments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=True),
    sa.Column('part_name', sa.String(), nullable=False),
    sa.Column('issue_description', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_condition_assessments_id'), 'condition_assessments', ['id'], unique=False)
    op.create_table('parts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('car_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parts_id'), 'parts', ['id'], unique=False)
    op.create_table('photos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_photos_id'), 'photos', ['id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('first_name', sa.String(), nullable=True),
    sa.Column('last_name', sa.String(), nullable=True),
    sa.Column('phone_number', sa.String(), nullable=True),
    sa.Column('date_of_birth', sa.Date(), nullable=True),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('temp_email', sa.String(), nullable=True),
    sa.Column('_hashed_password', sa.String(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['user_roles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('users')
    op.drop_index(op.f('ix_photos_id'), table_name='photos')
    op.drop_table('photos')
    op.drop_index(op.f('ix_parts_id'), table_name='parts')
    op.drop_table('parts')
    op.drop_index(op.f('ix_condition_assessments_id'), table_name='condition_assessments')
    op.drop_table('condition_assessments')
    op.drop_index(op.f('ix_car_sale_history_id'), table_name='car_sale_history')
    op.drop_table('car_sale_history')
    op.drop_table('user_roles')
    op.drop_index(op.f('ix_cars_mileage'), table_name='cars')
    op.drop_index(op.f('ix_cars_id'), table_name='cars')
    op.drop_table('cars')
    # ### end Alembic commands ###
