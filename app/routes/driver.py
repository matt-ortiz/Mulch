from flask import Blueprint, render_template, jsonify, request, Response, stream_with_context
from flask_login import login_required, current_user
from app.models import Order, Delivery
from app import db
from datetime import datetime
import json
import time

driver_bp = Blueprint('driver', __name__)

@driver_bp.route('/dashboard')
@login_required
def dashboard():
    # Get assigned deliveries for the current driver
    deliveries = Delivery.query.filter_by(driver_id=current_user.id).all()
    return render_template('driver/dashboard.html', deliveries=deliveries)

@driver_bp.route('/delivery/<int:delivery_id>/complete', methods=['POST'])
@login_required
def complete_delivery(delivery_id):
    delivery = Delivery.query.get_or_404(delivery_id)
    if delivery.driver_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    delivery.status = 'delivered'
    delivery.delivered_at = datetime.utcnow()
    delivery.delivery_notes = request.form.get('notes', '')
    db.session.commit()
    
    return jsonify({'success': True})

@driver_bp.route('/route')
@login_required
def view_route():
    # Get active deliveries for map view
    deliveries = Delivery.query.filter_by(
        driver_id=current_user.id,
        status='assigned'
    ).all()
    return render_template('driver/route.html', deliveries=deliveries)

@driver_bp.route('/updates')
@login_required
def get_updates():
    def generate():
        while True:
            # Get fresh data for this driver
            deliveries = Delivery.query.filter_by(
                driver_id=current_user.id
            ).all()
            
            # Create update data
            data = {
                'deliveries': [
                    {
                        'id': d.id,
                        'status': d.status,
                        'customer_name': d.order.customer_name,
                        'address': d.order.address,
                        'bags_ordered': d.order.bags_ordered,
                        'mulch_type': d.order.mulch_type
                    } for d in deliveries
                ],
                'stats': {
                    'total': len(deliveries),
                    'completed': sum(1 for d in deliveries if d.status == 'delivered'),
                    'total_bags': sum(d.order.bags_ordered for d in deliveries)
                }
            }
            
            # Send the update
            yield f"data: {json.dumps(data)}\n\n"
            
            # Wait before next update
            time.sleep(10)  # Check for updates every 10 seconds
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream'
    )
