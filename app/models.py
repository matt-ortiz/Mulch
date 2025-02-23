# models.py

from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=True)  # Make it nullable
    vehicle_capacity = db.Column(db.Integer)  # Number of bags that fit in their vehicle
    is_admin = db.Column(db.Boolean, default=False)
    deliveries = db.relationship('Delivery', backref='driver', lazy=True)

    def set_password(self, password):
        if self.is_admin:  # Only store passwords for admin users
            self.password_hash = generate_password_hash(password)
        else:
            self.password_hash = None  # Drivers don't store passwords

    def check_password(self, password):
        if self.is_admin:
            return check_password_hash(self.password_hash, password)
        else:
            # For drivers, check against current driver code in settings
            settings = Settings.query.first()
            return password == settings.driver_code

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120))
    address = db.Column(db.String(256), nullable=False)
    phone = db.Column(db.String(20))
    bags_ordered = db.Column(db.Integer, nullable=False)
    mulch_type = db.Column(db.String(64))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    notes = db.Column(db.Text)
    preferred_contact = db.Column(db.String(20))  # 'text' or 'call'
    deliveries = db.relationship('Delivery', backref='order', lazy='dynamic')
    year = db.Column(db.Integer, default=lambda: datetime.now().year)  # Add year field
    is_pickup = db.Column(db.Boolean, default=False)  # Add this field

    @property
    def delivery(self):
        """Get the most recent delivery for this order"""
        return self.deliveries.order_by(Delivery.assigned_at.desc()).first()

class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    status = db.Column(db.String(20), default='pending')  # pending, assigned, delivered
    assigned_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    delivery_notes = db.Column(db.Text)

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    driver_registration_open = db.Column(db.Boolean, default=False)
    driver_code = db.Column(db.String(20), nullable=False, default='DRIVER2024')
    far_threshold = db.Column(db.Float, default=10.0)  # in kilometers
    
    # Add school location fields
    school_address = db.Column(db.String(200), nullable=True)
    school_latitude = db.Column(db.Float, nullable=True)
    school_longitude = db.Column(db.Float, nullable=True)