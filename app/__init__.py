from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from config import Config

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'

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
    def load_user(id):
        from app.models import User
        return User.query.get(int(id))

    return app
