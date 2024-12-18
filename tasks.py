# tasks.py
import os
import shutil
from datetime import datetime, timezone

import requests
from celery import Celery

from app_config import Config
from models import db, ProductProgress
from utils import setup_logger, upload_images_to_s3, update_medusa_product_images, \
    get_jwt_token

logger = setup_logger(__name__)

celery = Celery('tasks', broker=Config.CELERY_BROKER_URL, backend=Config.CELERY_RESULT_BACKEND)


def fetch_medusa_product(product_id):
    headers = {"x-publishable-api-key": Config.PUBLISHABLE_KEY}

    try:
        url = f"{Config.MEDUSA_API_URL}/{product_id}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        product_data = response.json()
        product = product_data.get('product')  # Adjust based on Medusa API response format
        return product
    except requests.RequestException as e:
        logger.error(f"Error fetching product {product_id} from Medusa: {e}")
        return None


@celery.task
def process_product_images(product_id):
    try:
        # Initialize Flask application context for database operations
        from app import create_app
        app = create_app()
        app.app_context().push()

        # Fetch the product entry from the database
        product_entry = ProductProgress.query.filter_by(product_id=product_id).first()
        if not product_entry:
            logger.error(f"Product with ID {product_id} not found in the database.")
            return

        # Update status to 'processing'
        product_entry.status = 'processing'
        db.session.commit()
        logger.debug(f"Updated product {product_id} status to 'processing'.")

        # Prepare the product directory
        product_name = product_entry.title.replace(" ", "_").lower()
        product_dir = os.path.join("static", "product_images", product_name)
        logger.debug(f"Product directory path: {product_dir}")

        # Check if the directory exists and delete it if it does
        if os.path.exists(product_dir):
            shutil.rmtree(product_dir)
            logger.debug(f"Deleted existing directory: {product_dir}")

        # Recreate the directory
        os.makedirs(product_dir, exist_ok=True)
        logger.debug(f"Created directory: {product_dir}")

        # Determine how many additional images to download
        current_images_count = len([
            f for f in os.listdir(product_dir)
            if f.lower().endswith('.webp')
        ])
        images_to_download = max(0, 15 - current_images_count)
        logger.info(f"Current images count: {current_images_count}. Images to download: {images_to_download}")

        if images_to_download > 0:
            # Download additional images (e.g., from Google)
            try:
                from utils import download_images  # Ensure this is correctly imported
                download_images(
                    product_entry.title,
                    product_dir,
                    max_num=images_to_download,
                    product_id=product_entry.product_id
                )
                logger.debug(f"Downloaded {images_to_download} additional images for product {product_entry.product_id}.")
            except Exception as e:
                logger.error(f"Error downloading additional images: {e}")

        # Upload images to S3
        try:
            uploaded_files = upload_images_to_s3(product_dir)
            logger.info(f"Uploaded {len(uploaded_files)} images to S3 for product {product_id}.")
        except Exception as e:
            logger.error(f"Error uploading images to S3: {e}")
            product_entry.status = 'error'
            product_entry.error_message = f"S3 upload failed: {e}"
            db.session.commit()
            return

        # Build the array of image objects for Medusa
        image_urls = [
            {"url": f"{Config.S3_FILE_URL}/{fname}"}
            for fname in uploaded_files
        ]
        logger.debug(f"Image URLs for Medusa: {image_urls}")

        # Get JWT token
        try:
            admin_token = get_jwt_token(Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
            logger.debug("Obtained JWT token for Medusa API.")
        except Exception as e:
            logger.error(f"Error obtaining JWT token: {e}")
            product_entry.status = 'error'
            product_entry.error_message = f"JWT token error: {e}"
            db.session.commit()
            return

        # Update Medusa product images
        try:
            update_medusa_product_images(product_id, image_urls, admin_token)
            logger.info(f"Updated Medusa product images for product ID: {product_id}.")
        except Exception as e:
            logger.error(f"Error updating Medusa product images: {e}")
            product_entry.status = 'error'
            product_entry.error_message = f"Medusa update error: {e}"
            db.session.commit()
            return

        # Mark product as done
        product_entry.status = 'done'
        product_entry.processed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info(f"Marked product {product_id} as 'done'.")

        # Remove the local product_dir
        try:
            shutil.rmtree(product_dir, ignore_errors=True)
            logger.debug(f"Removed local directory {product_dir}.")
        except Exception as e:
            logger.warning(f"Failed to remove local directory {product_dir}: {e}")

    except Exception as e:
        logger.error(f"Unexpected error processing product {product_id}: {e}")
        if product_entry:
            product_entry.status = 'error'
            product_entry.error_message = str(e)
            db.session.commit()


# def process_product_images(product_id):
#     try:
#         # Initialize Flask application context for database operations
#         from app import create_app
#         app = create_app()
#         app.app_context().push()
#
#         product_entry = ProductProgress.query.filter_by(product_id=product_id).first()
#         if not product_entry:
#             logger.error(f"Product with ID {product_id} not found.")
#             return
#
#         # Update status to 'processing'
#         product_entry.status = 'processing'
#         db.session.commit()
#
#         # # Fetch the latest product data
#         # products = fetch_shop_products()
#         # product = next((p for p in products if p["id"] == product_entry.product_id), None)
#         # if not product:
#         #     product_entry.status = 'error'
#         #     product_entry.error_message = "Product not found in store."
#         #     db.session.commit()
#         #     logger.error(f"Product {product_id} not found in Medusa store.")
#         #     return
#
#         product_name = product_entry.title.replace(" ", "_").lower()
#         product_dir = os.path.join("static", "product_images", product_name)
#
#         # Check if the directory exists and delete it if it does
#         if os.path.exists(product_dir):
#             shutil.rmtree(product_dir)
#
#         # Recreate the directory
#         os.makedirs(product_dir, exist_ok=True)
#
#         # Download and process existing images
#         existing_images = product_entry.get("images", [])
#         print(existing_images)
#         existing_idx = 1
#         for image_data in existing_images:
#             image_url = image_data.get("url")
#             if not image_url:
#                 continue
#             try:
#                 response = requests.get(image_url, stream=True)
#                 response.raise_for_status()
#                 temp_path = os.path.join(product_dir, f"temp_existing_{existing_idx}.jpg")
#                 with open(temp_path, "wb") as file:
#                     for chunk in response.iter_content(1024):
#                         file.write(chunk)
#                 webp_path = convert_to_webp(temp_path)
#                 new_name = f"{product_entry.product_id}-existing-{existing_idx}.webp"
#                 new_path = os.path.join(product_dir, new_name)
#                 os.rename(webp_path, new_path)
#                 existing_idx += 1
#             except requests.RequestException as e:
#                 logger.error(f"Error downloading existing image {image_url}: {e}")
#             except Exception as e:
#                 logger.error(f"Error processing image {image_url}: {e}")
#
#         # Determine how many additional images to download
#         current_images_count = len([
#             f for f in os.listdir(product_dir)
#             if f.lower().endswith('webp')
#         ])
#         images_to_download = max(0, 15 - current_images_count)
#         if images_to_download > 0:
#             # Download additional images (google)
#             from utils import download_images  # Import here to avoid circular imports
#             download_images(product_entry.title, product_dir, max_num=images_to_download, product_id=product_entry.product_id)
#             logger.debug(f"Downloaded {images_to_download} additional images for product {product_entry.product_id}.")
#
#         # Upload images to S3
#         uploaded_files = upload_images_to_s3(product_dir)
#
#         # Build the array of image objects for Medusa
#         image_urls = [
#             {"url": f"{Config.S3_FILE_URL}/{fname}"}
#             for fname in uploaded_files
#         ]
#
#         # Get JWT token
#         admin_token = get_jwt_token(Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
#
#         # Update Medusa product images
#         update_medusa_product_images(product_id, image_urls, admin_token)
#
#         # Mark product as done
#         product_entry.status = 'done'
#         product_entry.processed_at = datetime.now(timezone.utc)
#         db.session.commit()
#
#         # Remove the local product_dir
#         shutil.rmtree(product_dir, ignore_errors=True)
#         logger.debug(f"Removed local directory {product_dir}")
#
#     except Exception as e:
#         logger.error(f"Error processing product {product_id}: {e}")
#         product_entry.status = 'error'
#         product_entry.error_message = str(e)
#         db.session.commit()
