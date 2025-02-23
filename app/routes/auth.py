from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User, Order, Delivery, Settings
from app import db

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # First try to find user with exact username/email
        user = User.query.filter_by(email=username).first()
        
        # If not found, try appending @driver
        if not user and not username.endswith('@driver'):
            driver_username = f"{username}@driver"
            user = User.query.filter_by(email=driver_username).first()
        
        if user:
            if user.check_password(password):  # This now handles both cases
                login_user(user)
                next_page = request.args.get('next')
                if next_page:
                    return redirect(next_page)
                if user.is_admin:
                    return redirect(url_for('admin.dashboard'))
                return redirect(url_for('driver.dashboard'))
        flash('Invalid username or password')
    return render_template('auth/login.html')

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@auth.route('/register', methods=['GET', 'POST'])
def register():
    settings = Settings.query.first()
    if not settings:
        settings = Settings()
        db.session.add(settings)
        db.session.commit()
    
    if request.method == 'POST':
        if not settings.driver_registration_open:
            flash('Driver registration is currently closed')
            return redirect(url_for('auth.register'))
            
        first_name = request.form.get('first_name')
        vehicle_capacity = request.form.get('vehicle_capacity')
        driver_code = request.form.get('driver_code')
        
        if driver_code != settings.driver_code:
            flash('Invalid driver code')
            return redirect(url_for('auth.register'))
        
        # Generate a unique username based on first name
        base_username = first_name.lower().replace(' ', '')
        username = base_username
        counter = 1
        while User.query.filter_by(email=f"{username}@driver").first():
            username = f"{base_username}{counter}"
            counter += 1
            
        user = User(
            first_name=first_name,
            email=f"{username}@driver",
            vehicle_capacity=vehicle_capacity,
            is_admin=False  # Ensure drivers are not admin
        )
        # Don't store password for drivers
        
        db.session.add(user)
        db.session.commit()
        
        return redirect(url_for('auth.login'))
        
    return render_template('auth/register.html', registration_open=settings.driver_registration_open)
