import os
from dotenv import load_dotenv
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'
    
    # Database configuration with connection pooling and SSL settings
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'postgresql://postgres:zaq12wsx@10.5.0.50/mulch'
    
    # Add SSL mode to database URL if not already present
    # if 'sslmode=' not in SQLALCHEMY_DATABASE_URI:
    #     SQLALCHEMY_DATABASE_URI += '?sslmode=require'
    
    # SQLAlchemy connection pool settings
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,  # Maximum number of connections to keep persistently
        'pool_timeout': 30,  # Seconds to wait before giving up on getting a connection
        'pool_recycle': 1800,  # Recycle connections after 30 minutes
        'max_overflow': 2,  # Allow up to 2 connections beyond pool_size
        'pool_pre_ping': True,  # Enable connection health checks
    }
    
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

    # Add to existing Config class
    GRAPHHOPPER_URL = 'http://10.7.10.20:8989'
