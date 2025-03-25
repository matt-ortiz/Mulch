from flask import Blueprint, render_template
from app.models import Order, Delivery, User
from app import db
from sqlalchemy import func, case, cast, Integer
from datetime import datetime

loading = Blueprint('loading', __name__)

@loading.route('/loading')
def loading_dashboard():
    # Get basic stats
    total_orders = Order.query.count()
    completed_deliveries = Delivery.query.filter_by(status='delivered').count()
    
    # Calculate total bags
    total_bags = db.session.query(func.sum(Order.bags_ordered)).scalar() or 0
    
    # Get completed bags
    completed_bags = (db.session.query(func.sum(Order.bags_ordered))
                     .join(Delivery)
                     .filter(Delivery.status == 'delivered')
                     .scalar()) or 0
    
    # Calculate remaining bags
    remaining_bags = total_bags - completed_bags
    
    # Calculate progress
    progress = (completed_deliveries / total_orders * 100) if total_orders > 0 else 0
    
    # Get status counts
    status_counts = {
        'assigned': Delivery.query.filter_by(status='assigned').count(),
        'delivered': completed_deliveries
    }
    
    # Get drivers with active orders
    drivers_with_orders = []
    driver_orders = {}
    
    active_drivers = User.query.filter_by(is_admin=False).all()
    
    for driver in active_drivers:
        # Get all orders for this driver
        orders = (Order.query
                 .join(Delivery)
                 .filter(Delivery.driver_id == driver.id)
                 .filter(Delivery.status != 'delivered')
                 .all())
        
        if orders:  # Only include drivers with active orders
            # Count orders and bags by mulch type
            black_orders = sum(1 for o in orders if o.mulch_type == 'Black Shredded Hardwood')
            red_orders = sum(1 for o in orders if o.mulch_type == 'Red Shredded Hardwood')
            brown_orders = sum(1 for o in orders if o.mulch_type == 'Shredded Hardwood (Natural brown)')
            
            black_bags = sum(o.bags_ordered for o in orders if o.mulch_type == 'Black Shredded Hardwood')
            red_bags = sum(o.bags_ordered for o in orders if o.mulch_type == 'Red Shredded Hardwood')
            brown_bags = sum(o.bags_ordered for o in orders if o.mulch_type == 'Shredded Hardwood (Natural brown)')
            
            total_driver_bags = black_bags + red_bags + brown_bags
            total_driver_orders = len(orders)
            
            drivers_with_orders.append((
                driver,
                total_driver_orders,
                total_driver_bags,
                black_orders,
                red_orders,
                brown_orders,
                black_bags,
                red_bags,
                brown_bags
            ))
            
            driver_orders[driver.id] = orders
    
    return render_template('loading/dashboard.html',
                         total_orders=total_orders,
                         total_bags=total_bags,
                         completed_bags=completed_bags,
                         remaining_bags=remaining_bags,
                         progress=progress,
                         status_counts=status_counts,
                         drivers_with_orders=drivers_with_orders,
                         driver_orders=driver_orders) 