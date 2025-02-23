from datetime import datetime
from app import db

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    vehicle_capacity = db.Column(db.Integer)  # Number of bags that fit in their vehicle
    is_admin = db.Column(db.Boolean, default=False)
    deliveries = db.relationship('Delivery', backref='driver', lazy=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(64), nullable=False)
    address = db.Column(db.String(256), nullable=False)
    phone = db.Column(db.String(20))
    bags_ordered = db.Column(db.Integer, nullable=False)
    mulch_type = db.Column(db.String(64))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    notes = db.Column(db.Text)
    delivery = db.relationship('Delivery', backref='order', lazy=True, uselist=False)

class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    status = db.Column(db.String(20), default='pending')  # pending, assigned, in_progress, delivered
    assigned_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    delivery_notes = db.Column(db.Text) 