from flask import Blueprint, jsonify, request, render_template, redirect, url_for, current_app, flash, Response, send_file
from flask_login import login_required, current_user
from app.models import Order, Delivery, User, Settings
from app import db
from datetime import datetime
import csv
from io import TextIOWrapper, StringIO
from opencage.geocoder import OpenCageGeocode
from math import radians, sin, cos, sqrt, atan2
from sqlalchemy.orm import joinedload
import googlemaps
import json
import time
import pdfkit
import io
from itertools import combinations

admin_routes = Blueprint('admin', __name__)

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance between two points on Earth."""
    R = 6371  # Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

@admin_routes.route('/assign-order', methods=['POST'])
@login_required
def assign_order():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    order_id = data.get('order_id')
    driver_id = data.get('driver_id')
    
    if not order_id or not driver_id:
        return jsonify({'error': 'Missing required data'}), 400
        
    try:
        # Handle temp_ prefixed IDs (new assignments)
        if isinstance(order_id, str) and order_id.startswith('temp_'):
            real_order_id = int(order_id.replace('temp_', ''))
            # Create new delivery
            delivery = Delivery(
                order_id=real_order_id,
                driver_id=driver_id,
                status='assigned',
                assigned_at=datetime.utcnow()
            )
            db.session.add(delivery)
        else:
            # Update existing delivery
            delivery = Delivery.query.get(int(order_id))
            if not delivery:
                return jsonify({'error': 'Delivery not found'}), 404
            delivery.driver_id = driver_id
            delivery.status = 'assigned'
            delivery.assigned_at = datetime.utcnow()
            
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error assigning order: {str(e)}")
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/auto-assign', methods=['POST'])
@login_required
def auto_assign():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    # Get unassigned orders
    unassigned_orders = Order.query.filter(~Order.delivery.has()).all()
    available_drivers = User.query.filter_by(is_admin=False).all()

    # Group orders by mulch type and location
    orders_by_type = {}
    for order in unassigned_orders:
        if order.mulch_type not in orders_by_type:
            orders_by_type[order.mulch_type] = []
        orders_by_type[order.mulch_type].append(order)

    # Assign orders to drivers
    for mulch_type, orders in orders_by_type.items():
        # Sort orders by location proximity
        # This is a simplified version - you might want to use a more sophisticated algorithm
        for driver in available_drivers:
            remaining_capacity = driver.vehicle_capacity
            assigned_orders = []

            for order in orders:
                if order.bags_ordered <= remaining_capacity:
                    delivery = Delivery(
                        order_id=order.id,
                        driver_id=driver.id,
                        status='assigned',
                        assigned_at=datetime.utcnow()
                    )
                    db.session.add(delivery)
                    remaining_capacity -= order.bags_ordered
                    assigned_orders.append(order)

            # Remove assigned orders from the list
            for order in assigned_orders:
                orders.remove(order)

    db.session.commit()
    return jsonify({'success': True})

@admin_routes.route('/import-orders', methods=['POST'])
@login_required
def import_orders():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    if 'file' not in request.files:
        flash('No file uploaded', 'error')
        return redirect(url_for('admin.settings'))

    # Get the year from the form
    year = request.form.get('year', datetime.now().year)
    try:
        year = int(year)
    except ValueError:
        flash('Invalid year specified', 'error')
        return redirect(url_for('admin.settings'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin.settings'))

    if file and file.filename.endswith('.csv'):
        csv_file = TextIOWrapper(file, encoding='utf-8')
        csv_reader = csv.DictReader(csv_file)
        
        # CSV field mappings
        FIELD_MAPPINGS = {
            'customer_name': 'First Name, Last Name',
            'email': 'Email Address',
            'phone': 'Cell Phone',
            'address': 'Delivery Address',
            'bags_ordered': 'Total Amount of Bags of Mulch',
            'mulch_type': 'Color of Mulch',
            'notes': 'Pickup or delivery instructions (If picking up, please type in Pick-up). If delivery, type Delivery, and any extra information, please add that too)',
            'preferred_contact': 'Please choose your preferred type of communication on delivery day'
        }
        
        # Initialize counters and logs
        stats = {
            'total_rows': 0,
            'skipped_empty': 0,
            'invalid_bags': 0,
            'successful': 0,
            'errors': 0
        }
        error_logs = []
        success_logs = []
        
        # First, delete existing orders for this year
        try:
            deleted_count = Order.query.filter_by(year=year).delete()
            db.session.commit()
            success_logs.append(f"Cleared {deleted_count} existing orders for year {year}")
        except Exception as e:
            error_logs.append(f"Error clearing orders: {str(e)}")
            db.session.rollback()
            flash('\n'.join(error_logs), 'error')
            return redirect(url_for('admin.settings'))
        
        for row in csv_reader:
            stats['total_rows'] += 1
            try:
                # Skip empty rows
                if not row.get(FIELD_MAPPINGS['customer_name']):
                    stats['skipped_empty'] += 1
                    continue

                # Clean and convert bags to integer
                try:
                    bags_str = row[FIELD_MAPPINGS['bags_ordered']]
                    bags_num = ''.join(filter(lambda x: x.isdigit() or x == '.', bags_str.split()[0]))
                    bags = int(float(bags_num))
                    
                    if bags <= 0:
                        raise ValueError("Bag count must be positive")
                        
                except (ValueError, TypeError, IndexError) as e:
                    stats['invalid_bags'] += 1
                    error_logs.append(f"Invalid bag count '{bags_str}' for {row[FIELD_MAPPINGS['customer_name']]}: {str(e)}")
                    continue

                # Process delivery/pickup status and address
                notes = row.get(FIELD_MAPPINGS['notes'], '').strip()
                address = row.get(FIELD_MAPPINGS['address'], '').strip()
                
                is_pickup = any(keyword in notes.lower() for keyword in ['pick-up', 'pickup', 'pick up'])
                
                if is_pickup:
                    # For pickup orders, use school address
                    address = current_app.config['SCHOOL_ADDRESS']
                elif address:
                    # Clean and complete address if needed
                    has_zip = any(zip_code in address for zip_code in ['22315', '22309', '22079', '22041', '22305'])
                    has_state = 'VA' in address.upper() or 'VIRGINIA' in address.upper()
                    
                    if ',' not in address and not has_state and not has_zip:
                        # No comma, no state, no zip - assume local address
                        address = f"{address}, Alexandria, VA 22315"
                    elif has_state and not has_zip:
                        # Has state but no zip
                        address = f"{address} 22315"
                else:
                    # No address provided
                    if is_pickup:
                        address = current_app.config['SCHOOL_ADDRESS']
                    else:
                        stats['errors'] += 1
                        error_logs.append(f"No address provided for {row[FIELD_MAPPINGS['customer_name']]}")
                        continue

                # Get and standardize the contact preference
                contact_pref = row[FIELD_MAPPINGS['preferred_contact']].lower()
                if 'text' in contact_pref:
                    preferred_contact = 'text'
                elif 'call' in contact_pref:
                    preferred_contact = 'call'
                elif 'email' in contact_pref:
                    preferred_contact = 'email'
                else:
                    preferred_contact = 'text'  # default to text
                
                # Create new order - remove is_pickup from initial creation
                order = Order(
                    customer_name=row[FIELD_MAPPINGS['customer_name']].strip(),
                    email=row.get(FIELD_MAPPINGS['email'], '').strip(),
                    address=address,  # Use processed address
                    phone=row.get(FIELD_MAPPINGS['phone'], '').strip(),
                    bags_ordered=bags,
                    mulch_type=row[FIELD_MAPPINGS['mulch_type']].strip(),
                    notes=notes,
                    preferred_contact=preferred_contact,
                    latitude=None,
                    longitude=None,
                    year=year
                )
                
                # Set is_pickup separately to handle case where column might not exist yet
                try:
                    order.is_pickup = is_pickup
                except:
                    # Column doesn't exist yet, skip setting this field
                    pass
                
                db.session.add(order)
                stats['successful'] += 1
                success_logs.append(f"Added {'pickup' if is_pickup else 'delivery'} order: {order.customer_name} - {bags} bags")

            except Exception as e:
                stats['errors'] += 1
                error_logs.append(f"Error processing {row.get(FIELD_MAPPINGS['customer_name'], 'Unknown')}: {str(e)}")
                continue

        try:
            db.session.commit()
            
            # Prepare summary message
            summary = [
                f"Import Summary for {year}:",
                f"Total rows processed: {stats['total_rows']}",
                f"Successfully imported: {stats['successful']}",
                f"Empty rows skipped: {stats['skipped_empty']}",
                f"Invalid bag counts: {stats['invalid_bags']}",
                f"Errors encountered: {stats['errors']}"
            ]
            
            if error_logs:
                summary.append("\nErrors:")
                summary.extend(error_logs)
            
            if success_logs:
                summary.append("\nSuccessful imports:")
                summary.extend(success_logs)
            
            flash('\n'.join(summary), 'info')
            return redirect(url_for('admin.settings'))
            
        except Exception as e:
            db.session.rollback()
            error_logs.append(f"Database error: {str(e)}")
            flash('\n'.join(error_logs), 'error')
            return redirect(url_for('admin.settings'))

    flash('Invalid file type', 'error')
    return redirect(url_for('admin.settings'))

@admin_routes.route('/dashboard')
@login_required
def dashboard():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get basic stats
    total_orders = Order.query.count()
    completed_deliveries = Delivery.query.filter_by(status='delivered').count()
    
    # Calculate bags
    total_bags = db.session.query(db.func.sum(Order.bags_ordered)).scalar() or 0
    
    # Get completed bags
    completed_bags = (db.session.query(db.func.sum(Order.bags_ordered))
                     .join(Delivery)
                     .filter(Delivery.status == 'delivered')
                     .scalar()) or 0
    
    # Calculate remaining bags
    remaining_bags = total_bags - completed_bags
    
    # Calculate progress
    progress = (completed_deliveries / total_orders * 100) if total_orders > 0 else 0
    
    # Get recent deliveries
    recent_deliveries = (Delivery.query
                        .filter_by(status='delivered')
                        .order_by(Delivery.delivered_at.desc())
                        .limit(10)
                        .all())
    
    # Get delivery counts by status
    status_counts = {
        'assigned': Delivery.query.filter_by(status='assigned').count(),
        'delivered': completed_deliveries
    }
    
    # Get remaining deliveries for map - show unassigned orders AND assigned but not delivered
    remaining_deliveries = (
        Delivery.query.filter(
            (Delivery.status != 'delivered')  # Not delivered
        ).all()
    )
    
    # Also get orders that don't have any delivery record yet
    unassigned_orders = Order.query.filter(
        ~Order.delivery.has()  # No delivery record
    ).all()
    
    # Create pseudo-delivery objects for unassigned orders
    for order in unassigned_orders:
        delivery = Delivery(
            order=order,
            status='pending'
        )
        remaining_deliveries.append(delivery)
    
    # Calculate map center (use first order's coordinates or fallback)
    first_order = Order.query.filter(
        Order.latitude.isnot(None), 
        Order.longitude.isnot(None)
    ).first()
    
    map_center = {
        'lat': first_order.latitude if first_order else 38.7849,
        'lng': first_order.longitude if first_order else -77.1527
    }
    
    # Get all drivers and their stats
    drivers = User.query.filter_by(is_admin=False).all()
    
    return render_template('admin/dashboard.html',
                         total_orders=total_orders,
                         total_bags=total_bags,
                         completed_bags=completed_bags,
                         remaining_bags=remaining_bags,
                         progress=progress,
                         recent_deliveries=recent_deliveries,
                         status_counts=status_counts,
                         remaining_deliveries=remaining_deliveries,
                         map_center=map_center,
                         drivers=drivers)

@admin_routes.route('/assign-orders')
@login_required
def assign_orders_page():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get unassigned orders and available drivers
    unassigned_orders = Order.query.filter(~Order.delivery.has()).all()
    drivers = User.query.filter_by(is_admin=False).all()
    
    return render_template('admin/assign_orders.html',
                         unassigned_orders=unassigned_orders,
                         drivers=drivers)

@admin_routes.route('/manage-drivers')
@login_required
def manage_drivers():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    drivers = User.query.filter_by(is_admin=False).all()
    return render_template('admin/manage_drivers.html', drivers=drivers)

@admin_routes.route('/view-map')
@admin_routes.route('/view-map/<int:driver_id>')
@admin_routes.route('/view-map/preview/<int:driver_id>/<path:order_ids>')
@login_required
def view_map(driver_id=None, order_ids=None):
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get all available drivers
    drivers = User.query.filter_by(is_admin=False).all()
    selected_driver = None
    
    # Create a list to hold all deliveries
    deliveries = []
    
    if driver_id and not order_ids:  # Viewing a specific driver's deliveries
        selected_driver = User.query.get_or_404(driver_id)
        deliveries = (Delivery.query
            .filter_by(driver_id=driver_id)
            .filter(Delivery.status != 'delivered')  # Exclude delivered orders
            .options(joinedload(Delivery.order))
            .options(joinedload(Delivery.driver))
            .all())
    elif order_ids:  # Preview mode
        # Convert string of order IDs to list
        order_id_list = [int(id) for id in order_ids.split(',')]
        orders = Order.query.filter(Order.id.in_(order_id_list)).all()
        selected_driver = User.query.get_or_404(driver_id)
        
        # Create temporary delivery objects
        for order in orders:
            temp_delivery = type('TempDelivery', (), {
                'id': f'temp_{order.id}',
                'order': order,
                'status': 'proposed',
                'driver': selected_driver,
                'driver_id': driver_id,
                'order_id': order.id,
                'assigned_at': None,
                'delivered_at': None
            })
            deliveries.append(temp_delivery)
    else:  # Main view - show ALL orders with their delivery status
        # Get all orders
        orders = Order.query.all()
        
        # For each order, create a delivery object (real or temporary)
        for order in orders:
            delivery = (Delivery.query
                       .filter_by(order_id=order.id)
                       .options(joinedload(Delivery.driver))
                       .first())
            
            if delivery:
                # Real delivery exists
                deliveries.append(delivery)
            else:
                # Create temporary delivery for unassigned order
                temp_delivery = type('TempDelivery', (), {
                    'id': f'temp_{order.id}',
                    'order': order,
                    'status': 'pending',
                    'driver': None,
                    'driver_id': None,
                    'order_id': order.id,
                    'assigned_at': None,
                    'delivered_at': None
                })
                deliveries.append(temp_delivery)
        
        # Debug print
        print("\nMain view deliveries:")
        for d in deliveries:
            print(f"ID: {d.id}, Status: {d.status}, Order: {d.order_id}, "
                  f"Driver: {d.driver.first_name if d.driver else 'None'}, "
                  f"Mulch: {d.order.mulch_type}")
    
    # Get map center point
    if deliveries and deliveries[0].order.latitude and deliveries[0].order.longitude:
        map_center = {
            'lat': deliveries[0].order.latitude,
            'lng': deliveries[0].order.longitude
        }
    else:
        map_center = current_app.config.get('DEFAULT_MAP_CENTER', {
            'lat': 38.7392, 
            'lng': -77.1026
        })

    # Helper functions for the template
    def getMulchColor(mulch_type):
        colors = {
            'Black Shredded Hardwood': '#000000',
            'Red Shredded Hardwood': '#8B4513',
            'Shredded Hardwood (Natural brown)': '#DEB887'
        }
        return colors.get(mulch_type, '#808080')

    def getStatusBorder(status):
        colors = {
            'pending': '#6c757d',
            'assigned': '#3b82f6',
            'in_progress': '#f59e0b',
            'delivered': '#10b981',
            'proposed': '#9333ea'  # Added for preview mode
        }
        return colors.get(status, '#6c757d')
    
    return render_template('admin/view_map.html',
                         deliveries=deliveries,
                         drivers=drivers,
                         selected_driver=selected_driver,
                         map_center=map_center,
                         getMulchColor=getMulchColor,
                         getStatusBorder=getStatusBorder)

@admin_routes.route('/geocode-addresses', methods=['POST'])
@login_required
def geocode_addresses():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    orders = Order.query.filter(
        (Order.latitude.is_(None)) | (Order.longitude.is_(None))
    ).all()
    
    geocoder = OpenCageGeocode(current_app.config['OPENCAGE_API_KEY'])
    
    updated = 0
    errors = []
    
    for order in orders:
        try:
            location = geocoder.geocode(order.address)
            if location:
                order.latitude = location.latitude
                order.longitude = location.longitude
                updated += 1
        except Exception as e:
            errors.append(f"Error geocoding {order.address}: {str(e)}")
            continue
            
    db.session.commit()
    
    return jsonify({
        'success': True,
        'updated': updated,
        'errors': errors
    })

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance between two points."""
    R = 6371  # Earth's radius in kilometers
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c * 0.621371  # Convert to miles

@admin_routes.route('/manage-addresses')
@login_required
def manage_addresses():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get settings for far threshold
    settings = Settings.query.first()
    far_threshold_km = settings.far_threshold if settings else 10.0  # Default to 10km if no settings
    far_threshold_mi = far_threshold_km * 0.621371  # Convert to miles for display
    
    # Get statistics
    total_orders = Order.query.count()
    geocoded_orders = Order.query.filter(
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).count()
    
    # Get all orders with coordinates
    orders_with_coords = Order.query.filter(
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).all()
    
    far_deliveries_count = 0
    
    if orders_with_coords:
        # Calculate distances from school
        school_lat = current_app.config['SCHOOL_LATITUDE']
        school_lng = current_app.config['SCHOOL_LONGITUDE']
        
        for order in orders_with_coords:
            distance = calculate_distance(school_lat, school_lng, 
                                       order.latitude, order.longitude)
            order.distance_from_school = round(distance, 1)
            order.is_far = distance > far_threshold_mi  # Use threshold from settings
            if order.is_far:
                far_deliveries_count += 1
    
    # Get all orders, ordered by distance (far ones first)
    orders = Order.query.order_by(
        Order.latitude.is_(None).desc(),  # Invalid addresses first
        Order.customer_name
    ).all()
    
    return render_template('admin/manage_addresses.html',
                         total_orders=total_orders,
                         geocoded_orders=geocoded_orders,
                         orders_without_coords=orders,
                         orders_count=total_orders - geocoded_orders,
                         far_deliveries_count=far_deliveries_count,
                         far_threshold=far_threshold_mi,  # Pass threshold to template
                         school_location={
                             'lat': current_app.config['SCHOOL_LATITUDE'],
                             'lng': current_app.config['SCHOOL_LONGITUDE'],
                             'address': current_app.config['SCHOOL_ADDRESS']
                         })

@admin_routes.route('/update-address/<int:order_id>', methods=['POST'])
@login_required
def update_address(order_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_address = data.get('address')
    
    order = Order.query.get_or_404(order_id)
    order.address = new_address
    order.latitude = None
    order.longitude = None
    
    db.session.commit()
    
    return jsonify({'success': True})

@admin_routes.route('/retry-geocoding/<int:order_id>', methods=['POST'])
@login_required
def retry_geocoding(order_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    order = Order.query.get_or_404(order_id)
    settings = Settings.query.first()
    far_threshold = settings.far_threshold if settings else 10.0  # Default to 10km if no settings
    
    try:
        # Add VA to improve accuracy if not present
        address = order.address
        if 'VA' not in address.upper() and 'VIRGINIA' not in address.upper():
            address += ', VA'
        if 'USA' not in address.upper():
            address += ', USA'
        
        gmaps = googlemaps.Client(key=current_app.config['GOOGLE_MAPS_API_KEY'])
        result = gmaps.geocode(address, region='us')
        
        if result and len(result):
            location = result[0]['geometry']['location']
            order.latitude = location['lat']
            order.longitude = location['lng']
            
            # Calculate distance from school
            distance = calculate_distance(
                float(current_app.config['SCHOOL_LATITUDE']),
                float(current_app.config['SCHOOL_LONGITUDE']),
                order.latitude,
                order.longitude
            )
            
            # Mark as far if beyond threshold
            order.is_far = distance > far_threshold
            
            db.session.commit()
            return jsonify({'success': True})
            
    except Exception as e:
        print(f"Error geocoding {order.address}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/update-coordinates/<int:order_id>', methods=['POST'])
@login_required
def update_coordinates(order_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    order = Order.query.get_or_404(order_id)
    
    order.latitude = data.get('lat')
    order.longitude = data.get('lng')
    
    db.session.commit()
    
    return jsonify({'success': True})

@admin_routes.route('/retry-all-geocoding', methods=['POST'])
@login_required
def retry_all_geocoding():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    orders = Order.query.filter(
        (Order.latitude.is_(None)) | (Order.longitude.is_(None))
    ).all()
    
    gmaps = googlemaps.Client(key=current_app.config['GOOGLE_MAPS_API_KEY'])
    success_count = 0
    error_count = 0
    
    for order in orders:
        try:
            # Format address
            address = order.address
            if 'VA' not in address.upper() and 'VIRGINIA' not in address.upper():
                address += ', VA'
            if 'USA' not in address.upper():
                address += ', USA'
            
            print(f"Geocoding: {address}")  # Debug log
            
            result = gmaps.geocode(
                address,
                region='us'
            )
            
            if result and len(result):
                location = result[0]['geometry']['location']
                order.latitude = location['lat']
                order.longitude = location['lng']
                success_count += 1
                print(f"Success: {location}")  # Debug log
            else:
                error_count += 1
                print(f"No results found for: {address}")  # Debug log
                
        except Exception as e:
            print(f"Error geocoding {order.address}: {str(e)}")  # Debug log
            error_count += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Successfully geocoded {success_count} addresses. {error_count} failed.'
    })

@admin_routes.route('/driver/<int:driver_id>')
@login_required
def get_driver_details(driver_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    driver = User.query.get_or_404(driver_id)
    completed_deliveries = Delivery.query.filter_by(
        driver_id=driver_id,
        status='delivered'
    ).all()
    
    total_bags = sum(d.order.bags_ordered for d in completed_deliveries)
    
    recent_deliveries = [{
        'customer_name': d.order.customer_name,
        'bags': d.order.bags_ordered,
        'date': d.delivered_at.isoformat() if d.delivered_at else None
    } for d in completed_deliveries[-5:]]  # Get last 5 deliveries
    
    return jsonify({
        'id': driver.id,
        'first_name': driver.first_name,
        'email': driver.email,
        'vehicle_capacity': driver.vehicle_capacity,
        'is_admin': driver.is_admin,
        'total_deliveries': len(completed_deliveries),
        'total_bags': total_bags,
        'recent_deliveries': recent_deliveries
    })

@admin_routes.route('/update-driver/<int:driver_id>', methods=['POST'])
@login_required
def update_driver(driver_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    driver = User.query.get_or_404(driver_id)
    data = request.get_json()
    
    driver.first_name = data['first_name']
    driver.email = data['email'] if data['email'] else f"{data['first_name'].lower()}@driver"
    driver.vehicle_capacity = int(data['vehicle_capacity'])
    driver.is_admin = data['is_admin']
    
    try:
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/delete-driver/<int:driver_id>', methods=['POST'])
@login_required
def delete_driver(driver_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    driver = User.query.get_or_404(driver_id)
    
    # Check if driver has any active deliveries
    active_deliveries = Delivery.query.filter_by(
        driver_id=driver_id
    ).filter(Delivery.status != 'delivered').count()
    
    if active_deliveries > 0:
        return jsonify({
            'success': False,
            'error': 'Cannot delete driver with active deliveries'
        }), 400
    
    db.session.delete(driver)
    db.session.commit()
    return jsonify({'success': True})

@admin_routes.route('/unassign-order', methods=['POST'])
@login_required
def unassign_order():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    delivery_id = data.get('order_id')  # We're still getting it as order_id from the frontend
    
    if not delivery_id:
        return jsonify({'error': 'No delivery ID provided'}), 400

    try:
        # Convert to integer and handle temp_ prefix if present
        if isinstance(delivery_id, str):
            delivery_id = int(delivery_id.replace('temp_', ''))
        else:
            delivery_id = int(delivery_id)
            
        # Find the delivery by its ID (not order_id)
        delivery = Delivery.query.get(delivery_id)
        
        if delivery:
            print(f"Found delivery to delete: {delivery.id} for order {delivery.order_id}")  # Debug log
            try:
                db.session.delete(delivery)
                db.session.commit()
                return jsonify({'success': True})
            except Exception as e:
                db.session.rollback()
                print(f"Database error: {str(e)}")  # Debug log
                return jsonify({'error': f'Database error: {str(e)}'}), 500
        else:
            print(f"No delivery found for id: {delivery_id}")  # Debug log
            return jsonify({'error': 'Delivery not found'}), 404
            
    except ValueError as e:
        print(f"Value error: {str(e)}")  # Debug log
        return jsonify({'error': 'Invalid delivery ID format'}), 400
    except Exception as e:
        print(f"Unexpected error: {str(e)}")  # Debug log
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@admin_routes.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    settings = Settings.query.first()
    if not settings:
        settings = Settings()
        db.session.add(settings)
        db.session.commit()
    
    if request.method == 'POST':
        settings.driver_registration_open = 'driver_registration_open' in request.form
        settings.driver_code = request.form['driver_code']
        settings.far_threshold = float(request.form['far_threshold']) / 0.621371  # Convert miles to km
        
        try:
            db.session.commit()
            flash('Settings updated successfully', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating settings: {str(e)}', 'error')
        
        return redirect(url_for('admin.settings'))
    
    # Add current_year to the template context
    current_year = datetime.now().year
    
    return render_template('admin/settings.html', 
                         settings=settings,
                         current_year=current_year)

@admin_routes.route('/create-driver', methods=['POST'])
@login_required
def create_driver():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.get_json()
    
    # Generate email if not provided
    email = data['email'] if data['email'] else f"{data['first_name'].lower()}@driver"
    
    # Create new driver
    driver = User(
        first_name=data['first_name'],
        email=email,
        vehicle_capacity=int(data['vehicle_capacity']),
        is_admin=data['is_admin']
    )
    
    try:
        db.session.add(driver)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/ai-routes')
@login_required
def ai_routes():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    def get_mulch_color(mulch_type):
        colors = {
            'Black Shredded Hardwood': '#000000',
            'Red Shredded Hardwood': '#8B4513',
            'Shredded Hardwood (Natural brown)': '#DEB887'
        }
        return colors.get(mulch_type, '#808080')
    
    drivers = User.query.filter_by(is_admin=False).all()
    unassigned_orders = Order.query.filter(
        ~Order.delivery.has(),  # No delivery record
        Order.latitude.isnot(None),  # Has valid coordinates
        Order.longitude.isnot(None)
    ).all()
    
    # Group orders by mulch type
    orders_by_type = {}
    for order in unassigned_orders:
        if order.mulch_type not in orders_by_type:
            orders_by_type[order.mulch_type] = []
        orders_by_type[order.mulch_type].append(order)
    
    # Generate suggested loads for each driver
    suggested_loads = {}
    for driver in drivers:
        # Skip drivers who are at capacity
        current_load = sum(d.order.bags_ordered for d in driver.deliveries 
                         if d.status != 'delivered')
        if current_load >= driver.vehicle_capacity:
            continue
            
        # Calculate remaining capacity
        remaining_capacity = driver.vehicle_capacity - current_load
        
        # Generate loads for this driver
        driver_loads = generate_driver_loads(driver, orders_by_type.copy(), remaining_capacity)
        if driver_loads:  # Only include drivers who can take more orders
            suggested_loads[driver.id] = driver_loads
    
    return render_template('admin/ai_routes.html', 
                         drivers=drivers,
                         suggested_loads=suggested_loads,
                         getMulchColor=get_mulch_color)

def calculate_distance_between_orders(order1, order2):
    """Calculate distance between two orders in kilometers"""
    R = 6371  # Earth's radius in kilometers
    
    lat1, lon1 = map(radians, [order1.latitude, order1.longitude])
    lat2, lon2 = map(radians, [order2.latitude, order2.longitude])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c * 0.621371  # Convert to miles

def cluster_by_location(orders, max_distance=2.0):
    """Group orders into clusters based on proximity."""
    clusters = []
    unassigned = orders.copy()
    
    while unassigned:
        current_cluster = [unassigned.pop(0)]
        i = 0
        while i < len(unassigned):
            # Check distance from all points in current cluster
            distances = [
                calculate_distance(
                    order.latitude, order.longitude,
                    unassigned[i].latitude, unassigned[i].longitude
                )
                for order in current_cluster
            ]
            if min(distances) <= max_distance:
                current_cluster.append(unassigned.pop(i))
            else:
                i += 1
        clusters.append(current_cluster)
    
    return clusters

def generate_driver_loads(driver, orders_by_type, remaining_capacity):
    """
    Generate optimized loads for a driver based on:
    1. Mulch type grouping
    2. Geographic clustering
    3. Load size optimization (minimum 75% capacity for larger trucks)
    4. Distance minimization
    """
    loads = []
    min_capacity_threshold = 0.75  # 75% minimum capacity for larger trucks
    is_large_truck = driver.vehicle_capacity >= 80  # Consider trucks with 100+ bag capacity as "large"
    
    for mulch_type, orders in orders_by_type.items():
        if not orders:
            continue
            
        # Create geographic clusters
        clusters = cluster_by_location(orders)
        
        for cluster in clusters:
            current_load = {
                'orders': [],
                'total_bags': 0,
                'stops': 0,
                'mulch_type': mulch_type,
                'efficiency': 0,
                'total_distance': 0
            }
            
            # Sort orders by size and proximity
            cluster.sort(key=lambda x: (
                -x.bags_ordered,  # Negative to sort largest first
                sum(calculate_distance_between_orders(x, o) for o in cluster)  # Total distance to other orders
            ))
            
            for order in cluster:
                # Check if adding this order exceeds capacity
                if current_load['total_bags'] + order.bags_ordered > remaining_capacity:
                    # Before saving the current load, check if it meets minimum capacity for large trucks
                    capacity_utilization = current_load['total_bags'] / remaining_capacity
                    
                    if current_load['orders'] and (not is_large_truck or capacity_utilization >= min_capacity_threshold):
                        current_load['efficiency'] = current_load['total_bags'] / current_load['stops']
                        loads.append(current_load.copy())
                    
                    # Start a new load
                    current_load = {
                        'orders': [],
                        'total_bags': 0,
                        'stops': 0,
                        'mulch_type': mulch_type,
                        'efficiency': 0,
                        'total_distance': 0
                    }
                    
                    # Try to add the current order to the new load
                    if order.bags_ordered <= remaining_capacity:
                        current_load['orders'].append(order)
                        current_load['total_bags'] += order.bags_ordered
                        current_load['stops'] += 1
                    
                    continue
                
                # Calculate total distance to existing orders in load
                if current_load['orders']:
                    distance_to_load = min(
                        calculate_distance_between_orders(order, existing_order)
                        for existing_order in current_load['orders']
                    )
                    if distance_to_load > 2:  # If order is too far from existing orders, start new load
                        # Check capacity utilization before saving
                        capacity_utilization = current_load['total_bags'] / remaining_capacity
                        
                        if current_load['orders'] and (not is_large_truck or capacity_utilization >= min_capacity_threshold):
                            current_load['efficiency'] = current_load['total_bags'] / current_load['stops']
                            loads.append(current_load.copy())
                        
                        current_load = {
                            'orders': [],
                            'total_bags': 0,
                            'stops': 0,
                            'mulch_type': mulch_type,
                            'efficiency': 0,
                            'total_distance': 0
                        }
                
                # Add order to current load
                current_load['orders'].append(order)
                current_load['total_bags'] += order.bags_ordered
                current_load['stops'] += 1
            
            # Save the last load if it meets capacity requirements
            if current_load['orders']:
                capacity_utilization = current_load['total_bags'] / remaining_capacity
                if not is_large_truck or capacity_utilization >= min_capacity_threshold:
                    current_load['efficiency'] = current_load['total_bags'] / current_load['stops']
                    loads.append(current_load.copy())
    
    # Sort loads by efficiency and capacity utilization
    loads.sort(key=lambda x: (
        -x['total_bags'] / remaining_capacity,  # Prioritize higher capacity utilization
        -x['efficiency']  # Then by bags per stop
    ))
    
    return loads

@admin_routes.route('/assign-load', methods=['POST'])
@login_required
def assign_load():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    driver_id = data.get('driver_id')
    order_ids = data.get('order_ids', [])

    if not driver_id or not order_ids:
        return jsonify({'error': 'Missing required data'}), 400

    try:
        # Get driver but don't check capacity
        driver = User.query.get(driver_id)
        if not driver:
            return jsonify({'error': 'Driver not found'}), 404

        # Calculate total bags for logging purposes
        total_bags = db.session.query(db.func.sum(Order.bags_ordered))\
            .filter(Order.id.in_(order_ids))\
            .scalar() or 0

        # Create deliveries for all orders
        for order_id in order_ids:
            delivery = Delivery(
                order_id=order_id,
                driver_id=driver_id,
                status='assigned',
                assigned_at=datetime.utcnow()
            )
            db.session.add(delivery)

        db.session.commit()
        
        # Add warning to response if capacity not set
        response = {'success': True}
        if not driver.vehicle_capacity or driver.vehicle_capacity == 0:
            response['warning'] = f'Assigned {total_bags} bags to driver with unknown capacity'
            
        return jsonify(response)
    except Exception as e:
        db.session.rollback()
        print(f"Error assigning load: {str(e)}")
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/load-generator')
@login_required
def load_generator():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
        
    # Get available drivers
    drivers = User.query.filter_by(is_admin=False).all()
    
    # Get unassigned orders
    unassigned_orders = Order.query.filter(
        ~Order.delivery.has()  # No delivery assigned
    ).all()
    
    # Calculate total unassigned bags by mulch type
    mulch_totals = {}
    for order in unassigned_orders:
        mulch_type = order.mulch_type
        if mulch_type in mulch_totals:
            mulch_totals[mulch_type] += order.bags_ordered
        else:
            mulch_totals[mulch_type] = order.bags_ordered
    
    return render_template('admin/load_generator.html', 
                         drivers=drivers,
                         unassigned_orders=unassigned_orders,
                         mulch_totals=mulch_totals)

@admin_routes.route('/suggest-load/<int:driver_id>')
@login_required
def suggest_load(driver_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    skip_previous = request.args.get('skip_previous', '').lower() == 'true'
    last_suggestion = request.args.get('last_suggestion', '0')
    current_index = int(last_suggestion)
    
    driver = User.query.get_or_404(driver_id)

    # Add this check
    if not driver.vehicle_capacity or driver.vehicle_capacity == 0:
        return jsonify({
            'error': 'Driver vehicle capacity not set',
            'needs_capacity': True
        }), 400

    # Get current assigned orders for this driver
    current_deliveries = Delivery.query.filter_by(
        driver_id=driver_id,
        status='assigned'
    ).all()
    
    current_load = {
        'orders': [],
        'stats': {
            'total_bags': 0,
            'capacity_used': 0
        }
    }
    
    if current_deliveries:
        current_load['orders'] = [{
            'id': d.order.id,
            'customer_name': d.order.customer_name,
            'address': d.order.address,
            'bags_ordered': d.order.bags_ordered,
            'mulch_type': d.order.mulch_type,
            'latitude': d.order.latitude,
            'longitude': d.order.longitude,
            'delivery_id': d.id,  # Include delivery ID for unassigning
            'is_assigned': True  # Flag to indicate this is an assigned order
        } for d in current_deliveries]
        
        total_bags = sum(d.order.bags_ordered for d in current_deliveries)
        current_load['stats'] = {
            'total_bags': total_bags,
            'capacity_used': total_bags / driver.vehicle_capacity
        }
        
        return jsonify(current_load)

    # Get current load for driver
    current_load = sum(d.order.bags_ordered for d in driver.deliveries if d.status != 'delivered')
    remaining_capacity = driver.vehicle_capacity - current_load
    if remaining_capacity <= 0:
        return jsonify({'error': 'No available capacity for this driver'}), 400

    # Get unassigned orders efficiently, excluding those that are already assigned
    unassigned_orders = Order.query.options(joinedload(Order.delivery)).filter(
        Order.delivery == None,  # Ensures order is unassigned
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).all()

    if not unassigned_orders:
        return jsonify({'orders': [], 'stats': {'total_bags': 0, 'capacity_used': 0}})

    # Group orders by mulch type and sort by distance
    orders_by_type = {}
    school_lat = current_app.config['SCHOOL_LATITUDE']
    school_lon = current_app.config['SCHOOL_LONGITUDE']
    
    for order in unassigned_orders:
        distance = calculate_distance(school_lat, school_lon, order.latitude, order.longitude)
        orders_by_type.setdefault(order.mulch_type, []).append((order, distance))

    # Sort each group by distance
    for mulch_type in orders_by_type:
        orders_by_type[mulch_type].sort(key=lambda x: x[1])  # Sort by distance
        orders_by_type[mulch_type] = [o[0] for o in orders_by_type[mulch_type]]  # Keep just orders

    # Build loads for each mulch type
    suggested_loads = []
    for mulch_type, orders in orders_by_type.items():
        current_bags = 0
        load_orders = []

        for order in orders:
            if current_bags + order.bags_ordered <= remaining_capacity:
                load_orders.append(order)
                current_bags += order.bags_ordered
            if current_bags >= remaining_capacity * 0.9:  # Try to fill to at least 90%
                break

        if load_orders:
            suggested_loads.append({
                'orders': load_orders,
                'total_bags': current_bags,
                'efficiency': current_bags / len(load_orders),  # Bags per stop
                'mulch_type': mulch_type
            })

    if not suggested_loads:
        return jsonify({'orders': [], 'stats': {'total_bags': 0, 'capacity_used': 0}})

    # Sort loads by efficiency and capacity utilization
    suggested_loads.sort(key=lambda x: (
        x['total_bags'] / remaining_capacity,  # Prioritize fuller loads
        x['efficiency']  # Then by bags per stop
    ), reverse=True)

    # Cycle through suggestions
    if skip_previous:
        current_index = (current_index + 1) % len(suggested_loads)
    best_load = suggested_loads[current_index]

    return jsonify({
        'orders': [{
            'id': order.id,
            'customer_name': order.customer_name,
            'address': order.address,
            'bags_ordered': order.bags_ordered,
            'mulch_type': order.mulch_type,
            'latitude': order.latitude,
            'longitude': order.longitude
        } for order in best_load['orders']],
        'stats': {
            'total_bags': best_load['total_bags'],
            'capacity_used': best_load['total_bags'] / driver.vehicle_capacity,
            'suggestion_index': current_index  # Return the current index
        }
    })

@admin_routes.route('/assign-suggested-load', methods=['POST'])
@login_required
def assign_suggested_load():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    driver_id = data.get('driver_id')
    order_ids = data.get('order_ids', [])  # Get the order IDs from the request
    
    if not order_ids:
        return jsonify({'error': 'No orders provided'}), 400
        
    driver = User.query.get_or_404(driver_id)
    
    try:
        # Create deliveries for the specific orders that were suggested
        for order_id in order_ids:
            delivery = Delivery(
                order_id=order_id,
                driver_id=driver_id,
                status='assigned',
                assigned_at=datetime.utcnow()
            )
            db.session.add(delivery)
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/geocode-all', methods=['POST'])
@login_required
def geocode_all_addresses():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    orders = Order.query.all()  # Get all orders regardless of status
    
    gmaps = googlemaps.Client(key=current_app.config['GOOGLE_MAPS_API_KEY'])
    success_count = 0
    error_count = 0
    
    for order in orders:
        try:
            # Format address
            address = order.address
            if 'VA' not in address.upper() and 'VIRGINIA' not in address.upper():
                address += ', VA'
            if 'USA' not in address.upper():
                address += ', USA'
            
            print(f"Geocoding: {address}")  # Debug log
            
            result = gmaps.geocode(
                address,
                region='us'
            )
            
            if result and len(result):
                location = result[0]['geometry']['location']
                order.latitude = location['lat']
                order.longitude = location['lng']
                success_count += 1
                print(f"Success: {location}")  # Debug log
            else:
                error_count += 1
                print(f"No results found for: {address}")  # Debug log
                
        except Exception as e:
            print(f"Error geocoding {order.address}: {str(e)}")  # Debug log
            error_count += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Successfully geocoded {success_count} addresses. {error_count} failed.'
    })

@admin_routes.route('/import-issues', methods=['GET', 'POST'])
@login_required
def import_issues():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        order_id = request.form.get('order_id')
        field = request.form.get('field')
        value = request.form.get('value')
        
        order = Order.query.get(order_id)
        if order:
            try:
                if field == 'bags_ordered':
                    order.bags_ordered = int(value)
                elif field == 'customer_name':
                    order.customer_name = value
                elif field == 'address':
                    order.address = value
                elif field == 'phone':
                    order.phone = value
                elif field == 'email':
                    order.email = value
                elif field == 'notes':
                    order.notes = value
                
                db.session.commit()
                flash('Order updated successfully', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating order: {str(e)}', 'error')
        
        return redirect(url_for('admin.import_issues'))
    
    # Get orders with issues
    orders_with_issues = Order.query.filter(
        (Order.bags_ordered.is_(None)) |
        (Order.bags_ordered == 0) |
        (Order.customer_name == '') |
        (Order.address == '')
    ).all()
    
    return render_template('admin/import_issues.html', orders=orders_with_issues)

@admin_routes.route('/build-load')
@login_required
def build_load():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get all unassigned orders and orders in progress
    unassigned_orders = (Order.query
        .outerjoin(Delivery)
        .filter(
            (Delivery.id.is_(None)) |  # No delivery record
            (Delivery.status.in_(['pending', 'assigned']))  # Or not delivered
        )
        .all())
    
    # Get all drivers (don't filter by capacity)
    drivers = User.query.filter_by(is_admin=False).all()
    
    # Calculate total bags for each mulch type
    mulch_totals = {}
    for order in unassigned_orders:
        mulch_type = order.mulch_type
        if mulch_type in mulch_totals:
            mulch_totals[mulch_type] += order.bags_ordered
        else:
            mulch_totals[mulch_type] = order.bags_ordered
    
    return render_template('admin/build_load.html',
                         orders=unassigned_orders,
                         drivers=drivers,  # Pass all drivers
                         mulch_totals=mulch_totals)

@admin_routes.route('/driver-updates')
@login_required
def driver_updates():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    # Query current driver status
    drivers = User.query.filter_by(is_admin=False).all()
    data = []
    for driver in drivers:
        pending_deliveries = (Delivery.query
            .filter_by(driver_id=driver.id)
            .filter(Delivery.status.in_(['pending', 'assigned']))
            .count())
        data.append({
            'id': driver.id,
            'is_busy': pending_deliveries > 0
        })
    
    return jsonify(data)

@admin_routes.route('/dashboard-updates')
@login_required
def dashboard_updates():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    # Get basic stats
    total_orders = Order.query.count()
    completed_deliveries = Delivery.query.filter_by(status='delivered').count()
    
    # Calculate bags
    total_bags = db.session.query(db.func.sum(Order.bags_ordered)).scalar() or 0
    completed_bags = (db.session.query(db.func.sum(Order.bags_ordered))
                     .join(Delivery)
                     .filter(Delivery.status == 'delivered')
                     .scalar()) or 0
    remaining_bags = total_bags - completed_bags
    
    # Calculate progress
    progress = (completed_deliveries / total_orders * 100) if total_orders > 0 else 0
    
    # Get delivery counts by status
    status_counts = {
        'assigned': Delivery.query.filter_by(status='assigned').count(),
        'delivered': completed_deliveries
    }
    
    # Get recent deliveries (limit to 6)
    recent_deliveries = []
    for delivery in (Delivery.query
                    .filter_by(status='delivered')
                    .order_by(Delivery.delivered_at.desc())
                    .limit(6)):
        recent_deliveries.append({
            'customer_name': delivery.order.customer_name,
            'driver_name': delivery.driver.first_name,
            'delivered_at': delivery.delivered_at.strftime('%m/%d %I:%M %p')
        })

    # Get driver stats
    driver_stats = []
    for driver in User.query.filter_by(is_admin=False):
        delivered_bags = sum(d.order.bags_ordered for d in driver.deliveries 
                           if d.status == 'delivered')
        total_driver_bags = sum(d.order.bags_ordered for d in driver.deliveries)
        completed_count = sum(1 for d in driver.deliveries if d.status == 'delivered')
        assigned_count = sum(1 for d in driver.deliveries if d.status == 'assigned')
        
        driver_stats.append({
            'id': driver.id,
            'name': driver.first_name,
            'delivered_bags': delivered_bags,
            'total_bags': total_driver_bags,
            'completed_count': completed_count,
            'assigned_count': assigned_count,
            'progress': (delivered_bags / total_driver_bags * 100) if total_driver_bags > 0 else 0
        })

    return jsonify({
        'stats': {
            'total_orders': total_orders,
            'total_bags': total_bags,
            'completed_bags': completed_bags,
            'remaining_bags': remaining_bags,
            'progress': progress,
            'status_counts': status_counts
        },
        'recent_deliveries': recent_deliveries,
        'driver_stats': driver_stats
    })

@admin_routes.route('/print-cards')
@login_required
def print_cards():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    school_lat = float(current_app.config['SCHOOL_LATITUDE'])
    school_lng = float(current_app.config['SCHOOL_LONGITUDE'])
    
    # Get all orders with valid coordinates
    orders = Order.query.filter(
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).all()
    
    # Calculate distance from school for each order
    for order in orders:
        order.distance_from_school = calculate_distance(
            school_lat, school_lng,
            float(order.latitude), float(order.longitude)
        )
    
    # Orders without coordinates
    orders_no_coords = Order.query.filter(
        (Order.latitude.is_(None)) | 
        (Order.longitude.is_(None))
    ).all()
    
    # Group orders by mulch type
    orders_by_mulch = {}
    for order in orders:
        if order.mulch_type not in orders_by_mulch:
            orders_by_mulch[order.mulch_type] = []
        orders_by_mulch[order.mulch_type].append(order)
    
    # For each mulch type, create one optimized route
    clustered_orders = {}
    for mulch_type, mulch_orders in orders_by_mulch.items():
        # Sort by distance from school
        mulch_orders.sort(key=lambda x: x.distance_from_school)
        
        # Store as a single route/cluster
        clustered_orders[mulch_type] = [mulch_orders]  # Single cluster per mulch type
    
    # Calculate stats
    stats = {
        'total_orders': len(orders) + len(orders_no_coords),
        'mulch_counts': {},
        'unmatched_addresses': len(orders_no_coords)
    }
    
    # Process orders with coordinates
    for mulch_type, clusters in clustered_orders.items():
        total_orders = len(clusters[0])  # Only one cluster per type now
        total_bags = sum(order.bags_ordered for order in clusters[0])
        stats['mulch_counts'][mulch_type] = {
            'count': total_orders,
            'bags': total_bags,
            'clusters': 1  # Always one cluster now
        }
    
    # Process orders without coordinates
    no_coords_by_mulch = {}
    if orders_no_coords:
        for order in orders_no_coords:
            if order.mulch_type not in no_coords_by_mulch:
                no_coords_by_mulch[order.mulch_type] = []
            no_coords_by_mulch[order.mulch_type].append(order)
            
            # Update stats
            if order.mulch_type not in stats['mulch_counts']:
                stats['mulch_counts'][order.mulch_type] = {'count': 0, 'bags': 0, 'clusters': 0}
            stats['mulch_counts'][order.mulch_type]['count'] += 1
            stats['mulch_counts'][order.mulch_type]['bags'] += order.bags_ordered
    
    return render_template(
        'admin/print_cards.html',
        clustered_orders=clustered_orders,
        orders_no_coords=no_coords_by_mulch,
        stats=stats
    )

def get_driving_distance_matrix(locations, gmaps):
    """Get actual driving distances between multiple points using Google Maps."""
    try:
        # Get distance matrix from Google Maps
        matrix = gmaps.distance_matrix(
            locations,
            locations,
            mode="driving",
            units="imperial"
        )
        
        # Create distance matrix
        distances = []
        for row in matrix['rows']:
            distance_row = []
            for element in row['elements']:
                if element['status'] == 'OK':
                    # Convert to miles and round to 1 decimal
                    miles = round(element['distance']['value'] * 0.000621371, 1)
                    distance_row.append(miles)
                else:
                    distance_row.append(float('inf'))
            distances.append(distance_row)
        
        return distances
    except Exception as e:
        print(f"Error getting distance matrix: {str(e)}")
        return None

def optimize_route(locations, distances):
    """Implement a simple nearest neighbor algorithm for route optimization."""
    if not locations:
        return []
        
    unvisited = list(range(1, len(locations)))  # Skip school (index 0)
    current = 0  # Start at school
    route = [current]
    
    while unvisited:
        # Find the nearest unvisited location
        nearest = min(unvisited, key=lambda x: distances[current][x])
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest
        
    return route

@admin_routes.route('/export-delivery-data')
@login_required
def export_delivery_data():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    school_lat = float(current_app.config['SCHOOL_LATITUDE'])
    school_lng = float(current_app.config['SCHOOL_LONGITUDE'])
    
    # Create CSV in memory
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Mulch Type',
        'Route Group',
        'Customer Name',
        'Address',
        'Phone',
        'Contact Preference',
        'Bags Ordered',
        'Instructions',
        'Miles from School',
        'Distance to Next Stop',
        'Latitude',
        'Longitude'
    ])
    
    # Get orders and organize them like in print_cards
    orders = Order.query.filter(
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).all()
    
    # Calculate and add distance from school
    for order in orders:
        order.distance_from_school = calculate_distance(
            school_lat, school_lng,
            order.latitude, order.longitude
        )
    
    # Group by mulch type
    orders_by_mulch = {}
    for order in orders:
        if order.mulch_type not in orders_by_mulch:
            orders_by_mulch[order.mulch_type] = []
        orders_by_mulch[order.mulch_type].append(order)
    
    # Process each mulch type and its clusters
    for mulch_type, mulch_orders in orders_by_mulch.items():
        mulch_orders.sort(key=lambda x: x.distance_from_school)
        
        # Create clusters
        clusters = []
        unassigned = mulch_orders.copy()
        max_driving_distance = 3.0
        
        while unassigned:
            current_cluster = []
            seed_order = unassigned.pop(0)
            current_cluster.append(seed_order)
            
            i = 0
            while i < len(unassigned):
                order = unassigned[i]
                min_distance = float('inf')
                
                for cluster_order in current_cluster:
                    distance = calculate_distance(
                        cluster_order.latitude, cluster_order.longitude,
                        order.latitude, order.longitude
                    )
                    min_distance = min(min_distance, distance)
                
                if min_distance <= max_driving_distance:
                    current_cluster.append(unassigned.pop(i))
                else:
                    i += 1
            
            if current_cluster:
                if len(current_cluster) > 1:
                    first_order = current_cluster[0]
                    current_cluster[1:] = sorted(
                        current_cluster[1:],
                        key=lambda x: calculate_distance(
                            first_order.latitude, first_order.longitude,
                            x.latitude, x.longitude
                        )
                    )
                clusters.append(current_cluster)
        
        # Write clusters to CSV
        for cluster_idx, cluster in enumerate(clusters, 1):
            for i, order in enumerate(cluster):
                # Calculate distance to next stop
                distance_to_next = ''
                if i < len(cluster) - 1:
                    distance_to_next = calculate_distance(
                        order.latitude, order.longitude,
                        cluster[i + 1].latitude, cluster[i + 1].longitude
                    )
                    distance_to_next = f"{distance_to_next:.1f}"
                
                writer.writerow([
                    mulch_type,
                    f"Route {cluster_idx}",
                    order.customer_name,
                    order.address,
                    f"{order.phone[:3]}-{order.phone[3:6]}-{order.phone[6:]}",
                    order.preferred_contact,
                    order.bags_ordered,
                    order.notes,
                    f"{order.distance_from_school:.1f}",
                    distance_to_next,
                    order.latitude,
                    order.longitude
                ])
            
            # Add blank row between clusters
            writer.writerow([])
    
    # Add unmatched addresses
    unmatched = Order.query.filter(
        (Order.latitude.is_(None)) | 
        (Order.longitude.is_(None))
    ).all()
    
    if unmatched:
        writer.writerow(['UNMATCHED ADDRESSES'])
        writer.writerow([])  # blank row
        
        for order in unmatched:
            writer.writerow([
                order.mulch_type,
                'NEEDS VERIFICATION',
                order.customer_name,
                order.address,
                f"{order.phone[:3]}-{order.phone[3:6]}-{order.phone[6:]}",
                order.preferred_contact,
                order.bags_ordered,
                order.notes,
                '',  # No distance from school
                '',  # No distance to next
                '',  # No latitude
                ''   # No longitude
            ])
    
    # Create the response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=delivery_routes.csv'
        }
    )

@admin_routes.route('/view-routes')
@login_required
def view_routes():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get school location from settings
    settings = Settings.query.first()
    if not settings:
        flash('Please configure school location in settings first', 'error')
        return redirect(url_for('admin.settings'))
    
    school_lat = float(settings.school_latitude)
    school_lng = float(settings.school_longitude)
    school_address = settings.school_address
    
    # Rest of the function remains the same, but use settings.school_address instead of config
    clustered_data = {
        'school': {
            'lat': school_lat,
            'lng': school_lng,
            'address': school_address  # Updated to use settings
        },
        'routes': {}
    }
    
    # Get all orders with valid coordinates
    orders = Order.query.filter(
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).all()
    
    # Group orders by mulch type
    orders_by_mulch = {}
    for order in orders:
        if order.mulch_type not in orders_by_mulch:
            orders_by_mulch[order.mulch_type] = []
        orders_by_mulch[order.mulch_type].append(order)
    
    # For each mulch type, create one optimized route
    for mulch_type, mulch_orders in orders_by_mulch.items():
        # Start with orders closest to school
        current_point = (school_lat, school_lng)
        route_orders = []
        remaining = mulch_orders.copy()
        
        # Build route one point at a time
        while remaining:
            # Find closest order to current point
            next_order = min(remaining, key=lambda x: calculate_distance(
                current_point[0], current_point[1],
                x.latitude, x.longitude
            ))
            
            route_orders.append(next_order)
            current_point = (next_order.latitude, next_order.longitude)
            remaining.remove(next_order)
        
        # Convert to serializable format with school as start/end point
        serialized_route = []
        
        # Add school as starting point
        serialized_route.append({
            'id': 'school_start',
            'customer_name': 'Hayfield Secondary',
            'address': school_address,
            'latitude': float(school_lat),
            'longitude': float(school_lng),
            'stop_number': 0,
            'is_school': True,
            'distance_from_prev': 0,
            'running_bag_total': 0
        })
        
        # Add all delivery stops
        for i, order in enumerate(route_orders, 1):
            # Calculate distance from previous point
            prev_lat = school_lat if i == 1 else route_orders[i-2].latitude
            prev_lng = school_lng if i == 1 else route_orders[i-2].longitude
            
            distance_from_prev = calculate_distance(
                prev_lat, prev_lng,
                order.latitude, order.longitude
            )
            
            serialized_route.append({
                'id': order.id,
                'customer_name': order.customer_name,
                'address': order.address,
                'latitude': float(order.latitude),
                'longitude': float(order.longitude),
                'bags_ordered': order.bags_ordered,
                'mulch_type': order.mulch_type,
                'phone': order.phone,
                'preferred_contact': order.preferred_contact,
                'notes': order.notes,
                'stop_number': i,
                'is_school': False,
                'distance_from_prev': round(distance_from_prev, 1),
                'running_bag_total': sum(o.bags_ordered for o in route_orders[:i])
            })
        
        # Add school as ending point
        last_lat = route_orders[-1].latitude if route_orders else school_lat
        last_lng = route_orders[-1].longitude if route_orders else school_lng
        distance_to_school = calculate_distance(last_lat, last_lng, school_lat, school_lng)
        
        serialized_route.append({
            'id': 'school_end',
            'customer_name': 'Hayfield Secondary',
            'address': school_address,
            'latitude': float(school_lat),
            'longitude': float(school_lng),
            'stop_number': len(route_orders) + 1,
            'is_school': True,
            'distance_from_prev': round(distance_to_school, 1),
            'running_bag_total': sum(o.bags_ordered for o in route_orders)
        })
        
        # Store as a single route
        clustered_data['routes'][mulch_type] = [serialized_route]
        
        print(f"Created route for {mulch_type}: {len(serialized_route)} stops (including school)")
    
    # Add stats to the clustered_data
    clustered_data['stats'] = {
        'total_orders': len(orders),
        'mulch_types': {}
    }
    
    for mulch_type, routes in clustered_data['routes'].items():
        route = routes[0]  # We only have one route per mulch type now
        # Exclude school points from stats
        delivery_stops = [stop for stop in route if not stop.get('is_school')]
        clustered_data['stats']['mulch_types'][mulch_type] = {
            'total_stops': len(delivery_stops),
            'total_bags': sum(stop['bags_ordered'] for stop in delivery_stops)
        }
    
    return render_template('admin/view_routes.html', clustered_data=clustered_data)

@admin_routes.route('/update-school-location', methods=['POST'])
@login_required
def update_school_location():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.get_json()
    address = data.get('address')
    lat = data.get('latitude')
    lng = data.get('longitude')
    
    if not all([address, lat, lng]):
        return jsonify({'error': 'Missing required fields'}), 400
    
    try:
        settings = Settings.query.first()
        if not settings:
            settings = Settings()
            db.session.add(settings)
        
        settings.school_address = address
        settings.school_latitude = float(lat)
        settings.school_longitude = float(lng)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'School location updated successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

