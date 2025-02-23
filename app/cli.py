import click
from flask.cli import with_appcontext
from app.models import User
from app import db

@click.command('create-admin')
@click.argument('email')
@click.argument('password')
@click.argument('first_name')
@with_appcontext
def create_admin(email, password, first_name):
    """Create an admin user"""
    if User.query.filter_by(email=email).first():
        click.echo('Email already registered')
        return
    
    admin = User(
        email=email,
        first_name=first_name,
        is_admin=True,
        vehicle_capacity=0  # Admins don't need vehicle capacity
    )
    admin.set_password(password)
    
    db.session.add(admin)
    db.session.commit()
    
    click.echo(f'Admin user {email} created successfully')

@click.command('reset-admin-password')
@click.argument('email')
@click.argument('new_password')
@with_appcontext
def reset_admin_password(email, new_password):
    """Reset an admin user's password"""
    user = User.query.filter_by(email=email, is_admin=True).first()
    
    if not user:
        click.echo('Admin user not found')
        return
    
    user.set_password(new_password)
    db.session.commit()
    
    click.echo(f'Password reset successfully for admin {email}')

@click.command('check-admin')
@click.argument('email')
@with_appcontext
def check_admin(email):
    """Check if a user exists and is admin"""
    user = User.query.filter_by(email=email).first()
    if not user:
        click.echo('User not found')
        return
    click.echo(f'User found: {user.email}')
    click.echo(f'Is admin: {user.is_admin}') 