"""Store valuation source snapshots and repair missing Won inventory records."""
from alembic import op
import sqlalchemy as sa

revision = "b81c4e2a6d90"
down_revision = "9d6e7f8a1b2c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cars", sa.Column("market_price_sources", sa.JSON(), nullable=True))
    op.execute("""
        INSERT INTO car_inventory
            (purchase_date, vehicle, vin, stock, vehicle_cost, car_status, car_id)
        SELECT CURRENT_TIMESTAMP, c.vehicle, c.vin, RIGHT(c.vin, 6),
               c.actual_bid, 'AWAITING_DELIVERY', c.id
        FROM cars c
        WHERE c.car_status = 'WON'
          AND NOT EXISTS (SELECT 1 FROM car_inventory i WHERE i.car_id = c.id)
    """)
    op.execute("UPDATE cars SET relevance = 'ACTIVE' WHERE car_status = 'WON'")


def downgrade():
    # Recovered business records must survive a schema rollback.
    op.drop_column("cars", "market_price_sources")
