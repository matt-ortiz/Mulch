import sys
import os

# Add your project directory to the sys.path
path = '/home/mattmulch/Mulch'
if path not in sys.path:
    sys.path.append(path)

# Import your app from the flask app factory
from app import create_app
application = create_app()

# Initialize the database (if needed)
from app import db
with application.app_context():
    db.create_all() 