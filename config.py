import os
from dotenv import load_dotenv
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    
    # Add error handling for Google Maps API key
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not GOOGLE_MAPS_API_KEY:
        raise ValueError("No Google Maps API key set in environment variables")
    
    OPENCAGE_API_KEY = '18e4e3f4c0e346c6b79dabf615792fad'
    
    # Hayfield Secondary School coordinates
    SCHOOL_LATITUDE = 38.7468
    SCHOOL_LONGITUDE = -77.1277
    SCHOOL_ADDRESS = "7630 Telegraph Rd, Alexandria, VA 22315"

    # Force template reloading
    TEMPLATES_AUTO_RELOAD = True
    SEND_FILE_MAX_AGE_DEFAULT = 0
