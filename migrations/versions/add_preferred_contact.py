"""add preferred contact

Revision ID: add_preferred_contact
Revises: previous_revision_id
Create Date: 2024-03-20

"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('order', sa.Column('preferred_contact', sa.String(20)))

def downgrade():
    op.drop_column('order', 'preferred_contact')


upgrade()