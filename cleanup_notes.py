from app import create_app, db
from app.models import Order
import re

def cleanup_notes():
    app = create_app()
    with app.app_context():
        # Get all orders
        orders = Order.query.all()
        
        # Counter for tracking changes
        updated = 0
        
        for order in orders:
            if order.notes:
                # Remove "Neighborhood: xxx\n" pattern
                if 'Neighborhood:' in order.notes:
                    # Split by newline and remove the neighborhood line
                    notes_lines = order.notes.split('\n')
                    cleaned_notes = '\n'.join([line for line in notes_lines 
                                             if not line.startswith('Neighborhood:')])
                    
                    # Update the order if changes were made
                    if cleaned_notes != order.notes:
                        order.notes = cleaned_notes.strip()
                        updated += 1
        
        # Commit changes if any were made
        if updated > 0:
            try:
                db.session.commit()
                print(f"Successfully cleaned up {updated} orders")
            except Exception as e:
                db.session.rollback()
                print(f"Error saving changes: {str(e)}")
        else:
            print("No changes needed")

if __name__ == '__main__':
    cleanup_notes() 