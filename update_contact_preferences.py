from app import create_app, db
from app.models import Order
import csv

def update_contact_preferences():
    app = create_app()
    with app.app_context():
        # First, delete all existing orders
        try:
            Order.query.delete()
            db.session.commit()
            print("Cleared existing orders")
        except Exception as e:
            print(f"Error clearing orders: {str(e)}")
            db.session.rollback()
            return

        # Read the CSV file
        with open('app/Hayfield Mulch Orders.csv', 'r', encoding='utf-8') as file:
            csv_reader = csv.DictReader(file)
            
            # Counter for tracking changes
            added = 0
            errors = 0
            
            for row in csv_reader:
                try:
                    # Skip empty rows
                    if not row.get('First Name, Last Name'):
                        continue
                    
                    # Skip already delivered orders
                    if row.get('Delivered') == '1.0':
                        continue

                    # Convert bags to integer
                    try:
                        bags = int(float(row['Total Amount of Bags of Mulch']))
                    except (ValueError, TypeError):
                        print(f"Invalid bag count for {row['First Name, Last Name']}")
                        errors += 1
                        continue

                    # Get and standardize the contact preference
                    contact_pref = row['Please choose your preferred type of communication on delivery day'].lower()
                    if 'text' in contact_pref:
                        preferred_contact = 'text'
                    elif 'call' in contact_pref:
                        preferred_contact = 'call'
                    else:
                        preferred_contact = 'text'  # default to text
                    
                    # Create new order without geocoding
                    order = Order(
                        customer_name=row['First Name, Last Name'],
                        email=row.get('Email Address', ''),
                        address=row['Delivery Address'],
                        phone=row.get('Cell Phone', ''),
                        bags_ordered=bags,
                        mulch_type=row['Color of Mulch'],
                        notes=row.get('Pickup or delivery instructions (If picking up, please type in Pick-up). If delivery, type Delivery, and any extra information, please add that too)', ''),
                        preferred_contact=preferred_contact,
                        latitude=None,  # Let geocoding be handled by manage_addresses
                        longitude=None
                    )
                    
                    db.session.add(order)
                    added += 1
                        
                except Exception as e:
                    print(f"Error processing row: {str(e)}")
                    errors += 1
                    continue
            
            # Commit all changes
            try:
                db.session.commit()
                print(f"Successfully added {added} orders")
                if errors > 0:
                    print(f"Encountered {errors} errors")
                print("\nNow you can use the Address Management page to geocode all addresses.")
            except Exception as e:
                db.session.rollback()
                print(f"Error saving changes: {str(e)}")

if __name__ == '__main__':
    update_contact_preferences() 