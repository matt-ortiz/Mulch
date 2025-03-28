from flask import Blueprint, jsonify, request, render_template, redirect, url_for, current_app, flash, Response, send_file
from flask_login import login_required, current_user
from app.models import Order, Delivery, User, Settings, Route, RouteStop
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
import requests

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
    unassigned_orders = Order.query.filter(~Order.deliveries.any()).all()
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

def sanitize_name(name):
    """
    Clean up name data while preserving valid special characters.
    Handles: O'Name, spaces, case, commas
    """
    if not name:
        return ""
    
    # Handle "Last, First" format
    if "," in name:
        parts = [p.strip() for p in name.split(",")]
        name = " ".join(reversed(parts))
    
    # Remove multiple spaces
    name = " ".join(name.split())
    
    # Remove any non-printable characters while preserving valid special chars
    valid_special_chars = "'`-."
    name = "".join(c for c in name if c.isalnum() or c.isspace() or c in valid_special_chars)
    
    return name.strip()

def sanitize_phone(phone):
    """
    Standardize phone numbers to pure digits.
    Input examples: 
    - (703) 971-7192
    - 703.971.7192
    - 703 971 7192
    - 703-971-7192
    Returns: 7039717192
    """
    if not phone:
        return ""
    
    # Remove common separators and get only digits
    digits = "".join(c for c in phone if c.isdigit())
    
    # Validate length (10 digits for US numbers)
    if len(digits) == 10:
        return digits
    elif len(digits) == 11 and digits.startswith('1'):
        return digits[1:]  # Remove leading 1
    elif len(digits) == 7:
        return "703" + digits  # Add default area code for local numbers
    else:
        return digits  # Return what we have, let validation handle it

def sanitize_address(address):
    """
    Clean up and standardize address format.
    Handles:
    - Line breaks
    - Extra spaces
    - Periods in street types
    - Missing city/state/zip
    """
    if not address:
        return ""
    
    # Replace line breaks with spaces
    address = address.replace('\n', ' ').replace('\r', ' ')
    
    # Standardize common abbreviations
    replacements = {
        'Apt.': 'Apt',
        'St.': 'Street',
        'Rd.': 'Road',
        'Dr.': 'Drive',
        'Ln.': 'Lane',
        'Ave.': 'Avenue',
        'Ct.': 'Court',
        'Cir.': 'Circle',
        'Blvd.': 'Boulevard',
        'Pkwy.': 'Parkway',
        'Sq.': 'Square',
        'Pl.': 'Place',
        'Ter.': 'Terrace'
    }
    
    # Clean up the address
    address = ' '.join(address.split())  # Remove multiple spaces
    
    # Replace abbreviations
    for old, new in replacements.items():
        address = address.replace(old, new)
        # Also try without the period
        address = address.replace(old[:-1], new)
    
    # Add VA and zip if missing (common in the dataset)
    if 'Alexandria' in address and 'VA' not in address.upper():
        address = address.replace('Alexandria', 'Alexandria, VA')
    if 'Lorton' in address and 'VA' not in address.upper():
        address = address.replace('Lorton', 'Lorton, VA')
    
    # Add zip code if missing for common areas
    if 'Alexandria, VA' in address and not any(zip_code in address for zip_code in ['22315', '22309', '22306']):
        address += ' 22315'  # Default zip for Hayfield area
    if 'Lorton, VA' in address and '22079' not in address:
        address += ' 22079'
    
    return address.strip()

def parse_bags_ordered(bags_str):
    """
    Parse and validate the number of bags ordered.
    Handles:
    - Numbers with text (e.g., "30 bags")
    - Decimal points
    - Text numbers
    """
    if not bags_str:
        return 0
        
    # Remove non-numeric characters except decimal point
    nums = ''.join(c for c in bags_str if c.isdigit() or c == '.')
    
    try:
        # Convert to float first to handle decimal points
        bags = float(nums)
        # Convert to int, rounding up
        return int(bags + 0.5)
    except (ValueError, TypeError):
        return 0

def sanitize_notes(notes):
    """
    Clean up delivery notes while preserving important information.
    Handles:
    - Line breaks
    - Extra spaces
    - Common text patterns
    """
    if not notes:
        return ""
    
    # Replace line breaks with spaces
    notes = notes.replace('\n', ' ').replace('\r', ' ')
    
    # Remove multiple spaces
    notes = ' '.join(notes.split())
    
    # Standardize common phrases
    notes = notes.lower()
    if any(word in notes for word in ['pick-up', 'pickup', 'pick up']):
        notes = 'Pick-up: ' + notes
    elif 'delivery' not in notes.lower():
        notes = 'Delivery: ' + notes
    
    return notes.strip()

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
        try:
            # Try to detect the CSV encoding
            raw_data = file.read()
            file.seek(0)  # Reset file pointer
            
            try:
                # Try UTF-8 first
                decoded = raw_data.decode('utf-8')
                encoding = 'utf-8'
            except UnicodeDecodeError:
                try:
                    # Try UTF-8-SIG (with BOM)
                    decoded = raw_data.decode('utf-8-sig')
                    encoding = 'utf-8-sig'
                except UnicodeDecodeError:
                    # Fall back to Windows-1252
                    decoded = raw_data.decode('cp1252')
                    encoding = 'cp1252'
            
            csv_file = TextIOWrapper(file, encoding=encoding)
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
                'errors': 0,
                'warnings': 0
            }
            error_logs = []
            warning_logs = []
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

                    # Clean and validate data
                    customer_name = sanitize_name(row[FIELD_MAPPINGS['customer_name']])
                    if not customer_name:
                        stats['errors'] += 1
                        error_logs.append(f"Invalid customer name in row {stats['total_rows']}")
                        continue

                    # Parse and validate bags ordered
                    bags_str = row[FIELD_MAPPINGS['bags_ordered']]
                    bags = parse_bags_ordered(bags_str)
                    if bags <= 0:
                        stats['invalid_bags'] += 1
                        error_logs.append(f"Invalid bag count '{bags_str}' for {customer_name}")
                        continue

                    # Clean other fields
                    email = row.get(FIELD_MAPPINGS['email'], '').strip().lower()
                    phone = sanitize_phone(row.get(FIELD_MAPPINGS['phone'], ''))
                    address = sanitize_address(row.get(FIELD_MAPPINGS['address'], ''))
                    notes = sanitize_notes(row.get(FIELD_MAPPINGS['notes'], ''))
                    mulch_type = row[FIELD_MAPPINGS['mulch_type']].strip()
                    
                    # Determine if pickup or delivery
                    is_pickup = any(keyword in notes.lower() for keyword in ['pick-up', 'pickup', 'pick up'])
                    
                    # Handle address for pickup orders
                    if is_pickup:
                        address = current_app.config['SCHOOL_ADDRESS']
                    elif not address:
                        stats['warnings'] += 1
                        warning_logs.append(f"No address provided for {customer_name}")
                        if not is_pickup:
                            stats['errors'] += 1
                            error_logs.append(f"Delivery order for {customer_name} has no address")
                            continue

                    # Get and standardize contact preference
                    contact_pref = row[FIELD_MAPPINGS['preferred_contact']].lower()
                    if 'text' in contact_pref:
                        preferred_contact = 'text'
                    elif 'call' in contact_pref:
                        preferred_contact = 'call'
                    elif 'email' in contact_pref:
                        preferred_contact = 'email'
                    else:
                        preferred_contact = 'text'  # default to text
                    
                    # Create new order
                    order = Order(
                        customer_name=customer_name,
                        email=email,
                        address=address,
                        phone=phone,
                        bags_ordered=bags,
                        mulch_type=mulch_type,
                        notes=notes,
                        preferred_contact=preferred_contact,
                        latitude=None,
                        longitude=None,
                        year=year,
                        is_pickup=is_pickup
                    )
                    
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
                    f"Warnings: {stats['warnings']}",
                    f"Errors: {stats['errors']}"
                ]
                
                if warning_logs:
                    summary.append("\nWarnings:")
                    summary.extend(warning_logs)
                
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
                
        except Exception as e:
            flash(f'Error processing CSV file: {str(e)}', 'error')
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
        ~Order.deliveries.any()  # Use deliveries instead of delivery
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
    unassigned_orders = Order.query.filter(~Order.deliveries.any()).all()
    drivers = User.query.filter_by(is_admin=False).all()
    
    return render_template('admin/assign_orders.html',
                         unassigned_orders=unassigned_orders,
                         drivers=drivers)

@admin_routes.route('/manage-drivers')
@login_required
def manage_drivers():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    drivers = User.query.filter_by(is_admin=False).order_by(User.first_name.asc()).all()
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
    
    # Get settings for school location and far threshold
    settings = Settings.query.first()
    if not settings or not settings.school_latitude or not settings.school_longitude:
        flash('Please configure school location in settings first', 'error')
        return redirect(url_for('admin.settings'))
    
    far_threshold_km = settings.far_threshold if settings else 10.0
    far_threshold_mi = far_threshold_km * 0.621371
    
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
        # Use settings for school location
        school_lat = float(settings.school_latitude)
        school_lng = float(settings.school_longitude)
        
        for order in orders_with_coords:
            distance = calculate_distance(school_lat, school_lng, 
                                       order.latitude, order.longitude)
            order.distance_from_school = round(distance, 1)
            order.is_far = distance > far_threshold_mi
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
                             'lat': float(settings.school_latitude),
                             'lng': float(settings.school_longitude),
                             'address': settings.school_address or 'School Location'
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
        
    # Get driver with deliveries and orders joined
    driver = User.query\
        .options(joinedload(User.deliveries).joinedload(Delivery.order))\
        .get(driver_id)
        
    if not driver:
        return jsonify({'error': 'Driver not found'}), 404
        
    # Get recent deliveries
    recent_deliveries = []
    for delivery in driver.deliveries:
        if delivery.order:
            recent_deliveries.append({
                'customer_name': delivery.order.customer_name,
                'bags': delivery.order.bags_ordered,
                'date': delivery.assigned_at.isoformat() if delivery.assigned_at else None
            })
    
    return jsonify({
        'id': driver.id,
        'first_name': driver.first_name,
        'email': driver.email,
        'vehicle_capacity': driver.vehicle_capacity,
        'is_admin': driver.is_admin,
        'map_preference': driver.map_preference,
        'recent_deliveries': recent_deliveries
    })

@admin_routes.route('/update-driver/<int:driver_id>', methods=['POST'])
@login_required
def update_driver(driver_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.get_json()
    driver = User.query.get(driver_id)
    
    if not driver:
        return jsonify({'error': 'Driver not found'}), 404
        
    try:
        driver.first_name = data.get('first_name', driver.first_name)
        # Handle email - use default if not provided
        email = data.get('email')
        if not email:
            email = f"{driver.first_name.lower()}@driver"
        driver.email = email
        
        driver.vehicle_capacity = int(data.get('vehicle_capacity', 0))
        driver.is_admin = data.get('is_admin', False)
        driver.map_preference = data.get('map_preference', 'google_maps')
        
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
    
    try:
        # Generate email if not provided
        email = data.get('email')
        if not email:
            email = f"{data.get('first_name', '').lower()}@driver"
        
        # Create new driver
        driver = User(
            first_name=data.get('first_name'),
            email=email,
            vehicle_capacity=int(data.get('vehicle_capacity', 0)),
            is_admin=data.get('is_admin', False),
            map_preference=data.get('map_preference', 'google_maps')
        )
        
        # Set password based on email
        driver.set_password(email.split('@')[0])
        
        db.session.add(driver)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'driver_id': driver.id
        })
        
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
        ~Order.deliveries.any(),  # No delivery record
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
        ~Order.deliveries.any()  # No delivery assigned
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
    settings = Settings.query.first()

    if not driver.vehicle_capacity:
        return jsonify({
            'error': 'Driver vehicle capacity not set',
            'needs_capacity': True
        }), 400

    # Get current assigned orders for this driver
    current_deliveries = Delivery.query.filter_by(
        driver_id=driver_id,
        status='assigned'
    ).all()
    
    if current_deliveries:
        # Return currently assigned orders
        return jsonify({
            'orders': [{
                'id': d.order.id,
                'customer_name': d.order.customer_name,
                'address': d.order.address,
                'bags_ordered': d.order.bags_ordered,
                'mulch_type': d.order.mulch_type,
                'latitude': d.order.latitude,
                'longitude': d.order.longitude,
                'delivery_id': d.id,
                'is_assigned': True
            } for d in current_deliveries],
            'stats': {
                'total_bags': sum(d.order.bags_ordered for d in current_deliveries),
                'capacity_used': sum(d.order.bags_ordered for d in current_deliveries) / driver.vehicle_capacity
            }
        })

    # Get unassigned orders
    unassigned_orders = Order.query.filter(
        ~Order.deliveries.any(),
        Order.latitude.isnot(None),
        Order.longitude.isnot(None)
    ).all()

    if not unassigned_orders:
        return jsonify({'orders': [], 'stats': {'total_bags': 0, 'capacity_used': 0}})

    # Group orders by mulch type
    orders_by_type = {}
    for order in unassigned_orders:
        if order.mulch_type not in orders_by_type:
            orders_by_type[order.mulch_type] = []
        orders_by_type[order.mulch_type].append(order)

    # Try to create an optimized load for each mulch type
    suggested_loads = []
    for mulch_type, orders in orders_by_type.items():
        current_bags = 0
        candidate_orders = []
        
        # Find orders that would fit in vehicle
        for order in orders:
            if current_bags + order.bags_ordered <= driver.vehicle_capacity:
                candidate_orders.append(order)
                current_bags += order.bags_ordered

        if candidate_orders:
            # Use GraphHopper to optimize the route for these orders
            optimized_route = optimize_with_graphhopper(candidate_orders, settings)
            if optimized_route:
                # Remove school points and calculate route metrics
                delivery_stops = [stop for stop in optimized_route if not stop.get('is_school')]
                total_distance = sum(stop.get('distance_from_prev', 0) for stop in optimized_route)
                
                suggested_loads.append({
                    'orders': delivery_stops,
                    'total_bags': current_bags,
                    'total_distance': total_distance,
                    'efficiency': current_bags / len(delivery_stops),  # Bags per stop
                    'distance_efficiency': current_bags / total_distance if total_distance > 0 else 0,  # Bags per km
                    'mulch_type': mulch_type
                })

    if not suggested_loads:
        return jsonify({'orders': [], 'stats': {'total_bags': 0, 'capacity_used': 0}})

    # Sort loads by multiple factors
    suggested_loads.sort(key=lambda x: (
        x['distance_efficiency'],  # Prioritize efficient routes (bags/km)
        x['efficiency'],  # Then by bags per stop
        x['total_bags'] / driver.vehicle_capacity  # Then by capacity utilization
    ), reverse=True)

    # Cycle through suggestions if requested
    if skip_previous:
        current_index = (current_index + 1) % len(suggested_loads)

    best_load = suggested_loads[current_index]
    
    return jsonify({
        'orders': best_load['orders'],
        'stats': {
            'total_bags': best_load['total_bags'],
            'capacity_used': best_load['total_bags'] / driver.vehicle_capacity,
            'total_distance': best_load['total_distance'],
            'suggestion_index': current_index
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

    # Get settings for school location
    settings = Settings.query.first()
    if not settings:
        flash('Please configure school location in settings first', 'error')
        return redirect(url_for('admin.settings'))

    # Get all orders that are not delivered
    orders = Order.query\
        .outerjoin(Delivery)\
        .filter(
            (Delivery.status.is_(None)) |  # No delivery record
            (Delivery.status == 'pending') |  # Pending delivery
            (Delivery.status == 'assigned')  # Assigned but not delivered
        )\
        .order_by(Order.id.asc())\
        .all()

    # Get available drivers
    drivers = User.query.filter_by(is_admin=False).all()

    return render_template(
        'admin/build_load.html',
        orders=orders,
        drivers=drivers,
        settings=settings
    )

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
    
    # Get all orders sorted by ID
    orders = Order.query.order_by(Order.id.asc()).all()
    
    # Calculate stats
    stats = {
        'mulch_counts': {}
    }
    
    # Calculate stats for each mulch type
    for order in orders:
        if order.mulch_type not in stats['mulch_counts']:
            stats['mulch_counts'][order.mulch_type] = {
                'count': 0,
                'bags': 0
            }
        stats['mulch_counts'][order.mulch_type]['count'] += 1
        stats['mulch_counts'][order.mulch_type]['bags'] += order.bags_ordered
    
    return render_template(
        'admin/print_cards.html',
        orders=orders,
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
    
    # Create CSV in memory
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header with new column order
    writer.writerow([
        'Order ID',
        'Customer Name',
        'Mulch Type',
        'Bags Ordered',
        'Address',
        'Phone',
        'Contact Preference',
        'Instructions',
        'Latitude',
        'Longitude'
    ])
    
    # Get all orders in sequential order
    orders = Order.query.order_by(Order.id).all()
    
    # Write orders to CSV
    for order in orders:
        writer.writerow([
            order.id,
            order.customer_name,
            order.mulch_type,
            order.bags_ordered,
            order.address,
            f"{order.phone[:3]}-{order.phone[3:6]}-{order.phone[6:]}" if order.phone else '',
            order.preferred_contact,
            order.notes,
            order.latitude,
            order.longitude
        ])
    
    # Create the response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=delivery_orders.csv'
        }
    )

@admin_routes.route('/view-routes')
@login_required
def view_routes():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    settings = Settings.query.first()
    if not settings:
        flash('Please configure school location in settings first', 'error')
        return redirect(url_for('admin.settings'))

    # Check if recalculation was requested
    recalculate = request.args.get('recalculate', '').lower() == 'true'

    clustered_data = {
        'school': {
            'lat': settings.school_latitude,
            'lng': settings.school_longitude,
            'address': settings.school_address
        },
        'routes': {},
        'stats': {
            'total_orders': 0,
            'mulch_types': {}
        },
        'errors': []
    }

    if recalculate:
        # ... existing optimization code ...
        orders = Order.query.filter(
            Order.latitude.isnot(None),
            Order.longitude.isnot(None)
        ).all()
        
        # Group and optimize routes
        # ... rest of existing optimization code ...
    else:
        # Get existing active routes
        active_routes = Route.query.filter_by(is_active=True).all()
        
        for route in active_routes:
            # Use stored route data
            clustered_data['routes'][route.mulch_type] = [route.route_data]
            
            # Get delivery stops (non-school stops)
            delivery_stops = [stop for stop in route.route_data if not stop.get('is_school')]
            
            # Update stats
            clustered_data['stats']['mulch_types'][route.mulch_type] = {
                'total_stops': len(delivery_stops),
                'total_bags': sum(stop.get('bags_ordered', 0) for stop in delivery_stops)
            }
            clustered_data['stats']['total_orders'] += len(delivery_stops)

    return render_template(
        'admin/view_routes.html', 
        clustered_data=clustered_data,
        has_routes=bool(clustered_data['routes'])
    )

def optimize_with_graphhopper(orders, settings):
    """Optimize route using local GraphHopper instance"""
    
    # Create list of points starting with school
    points = [
        [settings.school_longitude, settings.school_latitude]  # GraphHopper expects [lng, lat]
    ]

    # Add delivery points
    for order in orders:
        points.append([
            float(order.longitude),  # GraphHopper expects [lng, lat]
            float(order.latitude)
        ])

    # Add school as end point
    points.append([
        settings.school_longitude,
        settings.school_latitude
    ])

    # Prepare GraphHopper request
    payload = {
        'points': points,
        'profile': 'car',
        'locale': 'en',
        'instructions': True,
        'calc_points': True,
        'points_encoded': False
    }

    try:
        response = requests.post(
            f"{current_app.config['GRAPHHOPPER_URL']}/route",
            headers={'Content-Type': 'application/json'},
            json=payload
        )
        
        if response.status_code != 200:
            print(f"GraphHopper error: {response.text}")
            return None

        route_data = response.json()
        
        # Process and return optimized route
        optimized_route = []
        running_bags = 0
        
        # Add school start
        optimized_route.append({
            'id': 'school_start',
            'customer_name': 'Hayfield Secondary',
            'address': settings.school_address,
            'latitude': float(settings.school_latitude),
            'longitude': float(settings.school_longitude),
            'stop_number': 0,
            'is_school': True,
            'distance_from_prev': 0,
            'running_bag_total': 0
        })
        
        # Process only the actual stops (not the path points)
        # Skip first and last points (school)
        for i, point in enumerate(points[1:-1], 1):
            # Find the order for this stop
            order = min(orders, key=lambda o: haversine_distance(
                o.latitude, o.longitude,
                point[1], point[0]  # point is [lng, lat]
            ))
            running_bags += order.bags_ordered
            
            optimized_route.append({
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
                'distance_from_prev': 0,  # We'll calculate this next
                'running_bag_total': running_bags
            })
        
        # Add school end
        optimized_route.append({
            'id': 'school_end',
            'customer_name': 'Hayfield Secondary',
            'address': settings.school_address,
            'latitude': float(settings.school_latitude),
            'longitude': float(settings.school_longitude),
            'stop_number': len(orders) + 1,
            'is_school': True,
            'distance_from_prev': 0,
            'running_bag_total': running_bags
        })

        # Calculate distances between stops using the path data
        if 'paths' in route_data and len(route_data['paths']) > 0:
            total_distance = route_data['paths'][0]['distance'] / 1000.0  # Convert to km
            # Distribute distance roughly between stops
            if len(optimized_route) > 2:  # If we have stops between start and end
                distance_per_stop = total_distance / (len(optimized_route) - 1)
                for i in range(1, len(optimized_route)):
                    optimized_route[i]['distance_from_prev'] = distance_per_stop

        return optimized_route

    except Exception as e:
        print(f"\nError optimizing route: {str(e)}")
        return None

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

@admin_routes.route('/delivery-status/<int:order_id>')
@login_required
def get_delivery_status(order_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    order = Order.query.get_or_404(order_id)
    delivery = order.delivery  # This uses the property we defined in the Order model
    
    if delivery:
        return jsonify({
            'status': delivery.status,
            'delivered_at': delivery.delivered_at.isoformat() if delivery.delivered_at else None
        })
    else:
        return jsonify({
            'status': 'pending',
            'delivered_at': None
        })

@admin_routes.route('/toggle-delivery/<int:order_id>', methods=['POST'])
@login_required
def toggle_delivery_status(order_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    order = Order.query.get_or_404(order_id)
    delivery = order.delivery
    
    if not delivery:
        # Create a new delivery record if none exists
        delivery = Delivery(
            order=order,
            status='delivered',
            delivered_at=datetime.utcnow()
        )
        db.session.add(delivery)
    else:
        # Toggle the status
        if delivery.status == 'delivered':
            delivery.status = 'pending'
            delivery.delivered_at = None
        else:
            delivery.status = 'delivered'
            delivery.delivered_at = datetime.utcnow()
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'status': delivery.status,
            'delivered_at': delivery.delivered_at.isoformat() if delivery.delivered_at else None
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/view-orders')
@login_required
def view_orders():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    # Get query parameters
    search = request.args.get('search', '').strip()
    status = request.args.get('status', 'all')
    mulch_type = request.args.get('mulch_type', 'all')
    delivery_type = request.args.get('delivery_type', 'all')
    year = request.args.get('year', datetime.now().year)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    # Base query
    query = Order.query
    
    # Apply filters
    if search:
        query = query.filter(
            (Order.id.cast(db.String).ilike(f'%{search}%')) |
            (Order.customer_name.ilike(f'%{search}%')) |
            (Order.address.ilike(f'%{search}%'))
        )
    
    if status != 'all':
        if status == 'pending':
            query = query.filter(~Order.deliveries.any())
        else:
            query = query.join(Delivery).filter(Delivery.status == status)
    
    if mulch_type != 'all':
        query = query.filter(Order.mulch_type == mulch_type)
    
    if delivery_type != 'all':
        query = query.filter(Order.is_pickup == (delivery_type == 'pickup'))
    
    if year:
        query = query.filter(Order.year == year)
    
    # Get unique mulch types for filter dropdown
    mulch_types = db.session.query(Order.mulch_type.distinct()).all()
    mulch_types = [t[0] for t in mulch_types]
    
    # Get total count before pagination
    total_count = query.count()
    
    # Add sorting
    sort_by = request.args.get('sort', 'id')
    sort_dir = request.args.get('dir', 'asc')
    
    if sort_by == 'status':
        if sort_dir == 'asc':
            query = query.outerjoin(Delivery).order_by(
                Delivery.status.is_(None).desc(),
                Delivery.status.asc(),
                Order.id.asc()
            )
        else:
            query = query.outerjoin(Delivery).order_by(
                Delivery.status.is_(None).asc(),
                Delivery.status.desc(),
                Order.id.desc()
            )
    else:
        sort_column = getattr(Order, sort_by, Order.id)
        if sort_dir == 'desc':
            sort_column = sort_column.desc()
        query = query.order_by(sort_column)
    
    # Paginate results
    orders = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template(
        'admin/view_orders.html',
        orders=orders,
        search=search,
        status=status,
        mulch_type=mulch_type,
        delivery_type=delivery_type,
        year=year,
        mulch_types=mulch_types,
        total_count=total_count,
        sort_by=sort_by,
        sort_dir=sort_dir
    )

@admin_routes.route('/unassign-driver/<int:order_id>', methods=['POST'])
@login_required
def unassign_driver(order_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        # Find the delivery for this order
        delivery = Delivery.query.filter_by(order_id=order_id).first()
        
        if not delivery:
            return jsonify({'error': 'No delivery found for this order'}), 404
            
        if delivery.status == 'delivered':
            return jsonify({'error': 'Cannot unassign a delivered order'}), 400
            
        # Delete the delivery record
        db.session.delete(delivery)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error unassigning driver: {str(e)}")
        return jsonify({'error': str(e)}), 500

@admin_routes.route('/add-order', methods=['GET', 'POST'])
@login_required
def add_order():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        try:
            # Create new order
            order = Order(
                customer_name=request.form['customer_name'].strip(),
                email=request.form.get('email', '').strip(),
                address=request.form['address'].strip(),
                phone=request.form.get('phone', '').strip(),
                bags_ordered=int(request.form['bags_ordered']),
                mulch_type=request.form['mulch_type'].strip(),
                notes=request.form.get('notes', '').strip(),
                preferred_contact=request.form['preferred_contact'],
                is_pickup=bool(request.form.get('is_pickup')),
                year=datetime.now().year
            )
            
            db.session.add(order)
            db.session.commit()
            
            flash('Order created successfully!', 'success')
            return redirect(url_for('admin.view_orders'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating order: {str(e)}', 'error')
            return redirect(url_for('admin.add_order'))
    
    return render_template('admin/add-order.html')
