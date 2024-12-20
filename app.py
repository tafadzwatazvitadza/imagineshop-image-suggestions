from flask import Flask, render_template
from flask_cors import CORS
from flask_login import LoginManager
from flask_migrate import Migrate

from app_config import Config
from models import db, User

# Initialize extensions
migrate = Migrate()

def create_app():
    app = Flask(__name__)
    CORS(app)
    app.config.from_object(Config)

    # Initialize database
    db.init_app(app)

    # Initialize Flask-Migrate
    migrate.init_app(app, db)

    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register Blueprints
    from routes.auth import auth_bp
    from routes.product import product_bp
    from routes.image import image_bp
    from routes.banners import banners_bp
    from routes.brands import brands_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(product_bp)
    app.register_blueprint(image_bp)
    app.register_blueprint(banners_bp)
    app.register_blueprint(brands_bp)

    # Error Handlers
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('403.html'), 403

    @app.errorhandler(500)
    def internal_error(e):
        return render_template('500.html'), 500

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=Config.PORT)
