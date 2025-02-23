"""add year field

Revision ID: add_year_field
Revises: add_preferred_contact
Create Date: 2024-03-20

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

def upgrade():
    # Add year column
    op.add_column('order', sa.Column('year', sa.Integer))
    
    # Set default value for existing records
    op.execute(f"UPDATE \"order\" SET year = {datetime.now().year}")
    
    # Make it non-nullable after setting default
    op.alter_column('order', 'year', nullable=False)

def downgrade():
    op.drop_column('order', 'year') 