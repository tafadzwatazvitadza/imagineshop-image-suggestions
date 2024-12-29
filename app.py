from flask import Flask, render_template
from flask_cors import CORS
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from sqlalchemy import func
from datetime import datetime, date

from app_config import Config
from models import db, User, ProductProgress

migrate = Migrate()

def create_app():
    app = Flask(__name__)
    CORS(app)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    def get_monthly_earnings(user):
        from models import db, ProductProgress

        start_of_month = datetime(date.today().year, date.today().month, 1)

        if start_of_month.month == 12:
            end_of_month = datetime(start_of_month.year + 1, 1, 1)
        else:
            end_of_month = datetime(start_of_month.year, start_of_month.month + 1, 1)

        monthly_count = db.session.query(func.count(ProductProgress.id)).filter(
            ProductProgress.user_id == user.id,
            ProductProgress.status == 'done',
            ProductProgress.completed_at >= start_of_month,
            ProductProgress.completed_at < end_of_month
        ).scalar()

        return monthly_count  # just return the count

    @app.context_processor
    def inject_monthly_earnings():
        from flask_login import current_user
        if current_user.is_authenticated:
            earnings = get_monthly_earnings(current_user)
            # e.g., "Dec"
            month_abbrev = date.today().strftime("%b")
            # Or if you prefer full name, use "%B" (e.g., "December")
            month_earnings_display = f"{month_abbrev} R{earnings}"
            return {'month_earnings_display': month_earnings_display}
        return {'month_earnings_display': ''}  # Return empty if not logged in

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

# from flask import Flask, render_template
# from flask_cors import CORS
# from flask_login import LoginManager
# from flask_migrate import Migrate
#
# from app_config import Config
# from models import db, User
#
# # Initialize extensions
# migrate = Migrate()
#
# def create_app():
#     app = Flask(__name__)
#     CORS(app)
#     app.config.from_object(Config)
#
#     # Initialize database
#     db.init_app(app)
#
#     # Initialize Flask-Migrate
#     migrate.init_app(app, db)
#
#     # Initialize Flask-Login
#     login_manager = LoginManager()
#     login_manager.init_app(app)
#     login_manager.login_view = 'auth.login'
#     login_manager.login_message_category = 'info'
#
#     @login_manager.user_loader
#     def load_user(user_id):
#         return User.query.get(int(user_id))
#
#     # Register Blueprints
#     from routes.auth import auth_bp
#     from routes.product import product_bp
#     from routes.image import image_bp
#     from routes.banners import banners_bp
#     from routes.brands import brands_bp
#
#     app.register_blueprint(auth_bp)
#     app.register_blueprint(product_bp)
#     app.register_blueprint(image_bp)
#     app.register_blueprint(banners_bp)
#     app.register_blueprint(brands_bp)
#
#     # Error Handlers
#     @app.errorhandler(404)
#     def page_not_found(e):
#         return render_template('404.html'), 404
#
#     @app.errorhandler(403)
#     def forbidden(e):
#         return render_template('403.html'), 403
#
#     @app.errorhandler(500)
#     def internal_error(e):
#         return render_template('500.html'), 500
#
#     return app
#
# app = create_app()
#
# if __name__ == '__main__':
#     app.run(debug=True, port=Config.PORT)
