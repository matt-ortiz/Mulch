from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from config import Config
import logging
from sqlalchemy.exc import OperationalError
from functools import wraps
import time

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def retry_on_db_error(max_retries=3, delay=0.1):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return f(*args, **kwargs)
                except OperationalError as e:
                    retries += 1
                    if retries == max_retries:
                        logger.error(f"Database error after {max_retries} retries: {str(e)}")
                        raise
                    logger.warning(f"Database error (attempt {retries}): {str(e)}")
                    time.sleep(delay * (2 ** (retries - 1)))  # Exponential backoff
            return f(*args, **kwargs)
        return wrapper
    return decorator

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Force template reloading
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from app.routes.auth import auth
    from app.routes.driver import driver_bp
    from app.routes.main import main
    from app.routes.admin import admin_routes
    from app.routes.loading import loading
    from app.cli import create_admin, reset_admin_password, check_admin

    app.register_blueprint(auth)
    app.register_blueprint(driver_bp)
    app.register_blueprint(main)
    app.register_blueprint(admin_routes, url_prefix='/admin')
    app.register_blueprint(loading)
    
    app.cli.add_command(create_admin)
    app.cli.add_command(reset_admin_password)
    app.cli.add_command(check_admin)

    @login_manager.user_loader
    @retry_on_db_error()
    def load_user(id):
        from app.models import User
        return User.query.get(int(id))

    # Add health check endpoint
    @app.route('/health')
    def health_check():
        try:
            # Test database connection
            db.session.execute('SELECT 1')
            return {'status': 'healthy'}, 200
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {'status': 'unhealthy', 'error': str(e)}, 500

    return app
