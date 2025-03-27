from flask import jsonify, render_template
from app import app
from app.models import Order, Driver
from sqlalchemy import func
from app.utils.status import OrderStatus

@app.route('/api/dashboard-data')
def get_dashboard_data():
    # Get all orders
    orders = Order.query.all()
    total_orders = len(orders)
    total_bags = sum(order.bags_ordered for order in orders)
    
    # Get status counts
    status_counts = {
        'assigned': len([o for o in orders if o.status == OrderStatus.ASSIGNED]),
        'delivered': len([o for o in orders if o.status == OrderStatus.DELIVERED])
    }
    
    # Calculate completed and remaining bags
    completed_bags = sum(order.bags_ordered for order in orders if order.status == OrderStatus.DELIVERED)
    remaining_bags = total_bags - completed_bags
    
    # Calculate progress percentage
    progress = (completed_bags / total_bags * 100) if total_bags > 0 else 0
    
    # Get driver data
    drivers = Driver.query.all()
    drivers_with_orders = []
    driver_orders = {}
    
    for driver in drivers:
        driver_order_list = [o for o in orders if o.driver_id == driver.id]
        if not driver_order_list:
            continue
            
        order_count = len(driver_order_list)
        total_driver_bags = sum(o.bags_ordered for o in driver_order_list)
        
        # Count orders and bags by mulch type
        black_orders = len([o for o in driver_order_list if o.mulch_type == 'Black Shredded Hardwood'])
        red_orders = len([o for o in driver_order_list if o.mulch_type == 'Red Shredded Hardwood'])
        brown_orders = len([o for o in driver_order_list if o.mulch_type == 'Brown Shredded Hardwood'])
        
        black_bags = sum(o.bags_ordered for o in driver_order_list if o.mulch_type == 'Black Shredded Hardwood')
        red_bags = sum(o.bags_ordered for o in driver_order_list if o.mulch_type == 'Red Shredded Hardwood')
        brown_bags = sum(o.bags_ordered for o in driver_order_list if o.mulch_type == 'Brown Shredded Hardwood')
        
        drivers_with_orders.append((
            driver, order_count, total_driver_bags,
            black_orders, red_orders, brown_orders,
            black_bags, red_bags, brown_bags
        ))
        driver_orders[driver.id] = driver_order_list
    
    # Render driver grid HTML
    drivers_html = render_template(
        'loading/_driver_grid.html',
        drivers_with_orders=drivers_with_orders,
        driver_orders=driver_orders
    )
    
    return jsonify({
        'total_orders': total_orders,
        'total_bags': total_bags,
        'status_counts': status_counts,
        'completed_bags': completed_bags,
        'remaining_bags': remaining_bags,
        'progress': progress,
        'drivers_with_orders': [
            {
                'id': d[0].id,
                'name': d[0].first_name,
                'order_count': d[1],
                'total_bags': d[2],
                'black_orders': d[3],
                'red_orders': d[4],
                'brown_orders': d[5],
                'black_bags': d[6],
                'red_bags': d[7],
                'brown_bags': d[8]
            }
            for d in drivers_with_orders
        ],
        'drivers_html': drivers_html
    }) 