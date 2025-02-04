from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()

class ProductProgress(db.Model):
    __tablename__ = 'product_progress'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    handle = db.Column(db.String(255), nullable=True)
    thumbnail = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='pending', index=True)  # 'pending', 'processing', 'done', etc.
    processed_at = db.Column(db.DateTime, nullable=True)

    # New field to mark the time it was completed
    completed_at = db.Column(db.DateTime, nullable=True)

    # Foreign key linking to the User table
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Define the relationship to the User model
    user = relationship('User', back_populates='products')


class User(db.Model, UserMixin):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, unique=True)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(200), nullable=False)

    role = db.Column(db.String(50), default='worker', nullable=False)

    # Define the reverse relationship
    products = relationship('ProductProgress', back_populates='user')

    # Method to hash the password
    @staticmethod
    def generate_hash_password(password):
        return generate_password_hash(password)

    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    # Helper method to check user role
    def is_admin(self):
        return self.role.lower() == 'admin'

    def __repr__(self):
        return f"<User {self.username}>"


# Banner model
class Banner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    image_url = db.Column(db.String(200), nullable=True)
    collection_path = db.Column(db.String(100), nullable=True)
    coupon_code = db.Column(db.String(50), nullable=True)
    expiry_date = db.Column(db.DateTime, nullable=True)