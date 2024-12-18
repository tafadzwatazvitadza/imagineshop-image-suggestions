# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default_secret_key')  # Ensure to set a strong secret key in production
    SQLALCHEMY_DATABASE_URI = os.getenv('DB_URI')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MEDUSA_API_URL = os.getenv('MEDUSA_API_URL')
    PUBLISHABLE_KEY = os.getenv('NEXT_PUBLIC_MEDUSA_PUBLISHABLE_KEY')
    MEDUSA_ADMIN_URL = os.getenv('MEDUSA_ADMIN_URL')
    S3_FILE_URL = os.getenv('S3_FILE_URL')
    S3_BUCKET = os.getenv('S3_BUCKET')
    S3_REGION = os.getenv('S3_REGION')
    S3_ACCESS_KEY_ID = os.getenv('S3_ACCESS_KEY_ID')
    S3_SECRET_ACCESS_KEY = os.getenv('S3_SECRET_ACCESS_KEY')
    S3_ENDPOINT = os.getenv('S3_ENDPOINT')
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
    PORT = int(os.getenv('PORT', 5000))
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a_very_secure_and_random_secret_key'
