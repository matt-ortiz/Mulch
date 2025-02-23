"""add pickup field

Revision ID: add_pickup_field
Revises: add_year_field
Create Date: 2024-03-21
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Add is_pickup column
    op.add_column('order', sa.Column('is_pickup', sa.Boolean, server_default='false'))
    
    # Update existing orders based on notes
    op.execute("""
        UPDATE "order" 
        SET is_pickup = true 
        WHERE LOWER(notes) LIKE '%pick%up%' 
        OR LOWER(notes) LIKE '%pickup%'
    """)

def downgrade():
    op.drop_column('order', 'is_pickup') 