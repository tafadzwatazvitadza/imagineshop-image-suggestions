import logging
import os
import shutil
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
import requests
import tempfile
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from icrawler.builtin import GoogleImageCrawler
from dotenv import load_dotenv
from sqlalchemy import or_

load_dotenv()

MEDUSA_API_URL = os.getenv('MEDUSA_API_URL')
PUBLISHABLE_KEY = os.getenv('NEXT_PUBLIC_MEDUSA_PUBLISHABLE_KEY')
MEDUSA_ADMIN_URL = os.getenv('MEDUSA_ADMIN_URL')

# S3 Configuration
S3_FILE_URL = os.getenv('S3_FILE_URL')
S3_BUCKET = os.getenv('S3_BUCKET')
S3_REGION = os.getenv('S3_REGION')
S3_ACCESS_KEY_ID = os.getenv('S3_ACCESS_KEY_ID')
S3_SECRET_ACCESS_KEY = os.getenv('S3_SECRET_ACCESS_KEY')
S3_ENDPOINT = os.getenv('S3_ENDPOINT')

ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')

def get_jwt_token(email, password):
    url = f"{MEDUSA_ADMIN_URL}/auth/user/emailpass"
    payload = {"email": email, "password": password}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()["token"]

ADMIN_TOKEN = get_jwt_token(ADMIN_EMAIL, ADMIN_PASSWORD)

s3_client = boto3.client(
    's3',
    aws_access_key_id=S3_ACCESS_KEY_ID,
    aws_secret_access_key=S3_SECRET_ACCESS_KEY,
    region_name=S3_REGION,
    endpoint_url=S3_ENDPOINT
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Adjust as needed

console_handler = logging.StreamHandler()
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

app = Flask(__name__)
app.secret_key = 'my_secret_key'

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DB_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class ProductProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.String(255), unique=True, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), default='pending')  # 'pending', 'processing', 'done', 'skipped'
    processed_images_count = db.Column(db.Integer, default=0)
    error_message = db.Column(db.String(1024), nullable=True)
    processed_at = db.Column(db.DateTime, nullable=True)

with app.app_context():
    db.create_all()

OUTPUT_DIR = "./static/product_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_shop_products():
    headers = {"x-publishable-api-key": PUBLISHABLE_KEY}
    products = []
    offset = 0
    limit = 50

    while True:
        url = f"{MEDUSA_API_URL}?offset={offset}&limit={limit}"
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            batch = response.json().get("products", [])
            if not batch:
                break
            products.extend(batch)
            offset += limit
        except requests.RequestException as e:
            logger.error(f"Error fetching products: {e}")
            break

    logger.debug(f"Total products fetched: {len(products)}")
    return products

def convert_to_webp(input_path):
    webp_path = os.path.splitext(input_path)[0] + ".webp"
    with Image.open(input_path) as img:
        img = img.convert("RGB")  # Ensure proper mode for WebP
        img.save(webp_path, format="WEBP", quality=90)
    os.remove(input_path)
    logger.debug(f"Converted {input_path} to {webp_path}")
    return webp_path

def validate_image_dimensions(image_path):
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            is_valid = width >= 800 and height >= 800
            return is_valid, width, height
    except Exception as e:
        logger.error(f"Error validating image dimensions for {image_path}: {e}")
        return False, 0, 0

def resize_and_center_image(input_path, output_path):
    is_url = input_path.startswith("http")
    if is_url:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".webp")
        try:
            response = requests.get(input_path, stream=True)
            response.raise_for_status()
            with open(temp_file.name, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
            input_path = temp_file.name
        except Exception as e:
            logger.error(f"Error downloading image from URL {input_path}: {e}")
            raise

    try:
        with Image.open(input_path) as img:
            img = img.convert("RGBA")
            white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(white_background, img)
            img = img.convert("RGB")

            original_width, original_height = img.size
            if original_width > original_height:
                new_width = 800
                new_height = int(800 * (original_height / original_width))
            else:
                new_height = 800
                new_width = int(800 * (original_width / original_height))

            resized_img = img.resize((new_width, new_height), Image.LANCZOS)
            canvas = Image.new("RGB", (800, 800), (255, 255, 255))
            x_offset = (800 - new_width) // 2
            y_offset = (800 - new_height) // 2
            canvas.paste(resized_img, (x_offset, y_offset))
            canvas.save(output_path, format="WEBP", quality=90)
            logger.debug(f"Resized and centered image saved to {output_path}")
    finally:
        if is_url:
            os.unlink(input_path)

def upload_images_to_s3(product_dir):
    uploaded_files = []
    for root, _, files in os.walk(product_dir):
        for filename in files:
            if filename.lower().endswith('webp'):
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, product_dir)
                s3_key = relative_path.replace("\\", "/")  # Normalize path for S3
                try:
                    s3_client.upload_file(
                        Filename=file_path,
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                        ExtraArgs={
                            'ContentType': 'image/webp',
                            'ACL': 'public-read'
                        }
                    )
                    uploaded_files.append(s3_key)
                    logger.info(f"Uploaded {s3_key} to S3 bucket {S3_BUCKET}")
                except boto3.exceptions.S3UploadFailedError as e:
                    logger.error(f"Upload failed for {s3_key}: {e}")
                except Exception as e:
                    logger.exception(f"Unexpected error during S3 upload for {s3_key}: {e}")
    return uploaded_files

def update_medusa_product_images(product_id, image_urls):
    headers = {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "Content-Type": "application/json"
    }

    thumbnail_url = next((image['url'] for image in image_urls if image['url'].endswith('thumbnail.webp')), None)
    if not thumbnail_url:
        if not image_urls:
            raise ValueError("No images provided in the image URLs list.")
        thumbnail_url = image_urls[0]['url']

    data = {
        "thumbnail": thumbnail_url,
        "images": image_urls
    }
    url = f"{MEDUSA_ADMIN_URL}/admin/products/{product_id}"

    logger.debug(f"Updating product images at {url} with data: {data}")
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    logger.info(f"Updated product {product_id} images in Medusa.")
    return response.json()

def download_images(query, product_dir, max_num=10, domain=None, product_id=None):
    """Download images for a given query and optional domain into product_dir.
       Convert them to WEBP and rename according to product_id and domain."""
    # Before crawling, list existing files to determine new ones after crawl
    existing_files = set(os.listdir(product_dir))

    google_crawler = GoogleImageCrawler(storage={'root_dir': product_dir})
    filtered_query = query
    if domain:
        filtered_query += f" site:{domain}"
    logger.debug(f"Crawling: {filtered_query}, saving to: {product_dir}")
    google_crawler.crawl(keyword=filtered_query, max_num=max_num)

    # After crawl, find new files
    new_files = [f for f in os.listdir(product_dir) if f not in existing_files]

    # Convert all downloaded images to WebP and rename
    final_names = []
    idx = 1
    source_name = domain.replace('.', '_') if domain else "google"
    for f in new_files:
        fpath = os.path.join(product_dir, f)
        if os.path.isfile(fpath) and not fpath.lower().endswith('webp'):
            try:
                webp_path = convert_to_webp(fpath)
                # Rename to product_id-source-idx.webp
                new_name = f"{product_id}-{source_name}-{idx}.webp"
                new_path = os.path.join(product_dir, new_name)
                os.rename(webp_path, new_path)
                final_names.append(new_name)
                idx += 1
            except Exception as e:
                logger.error(f"Error converting {fpath} to webp: {e}")
        elif f.lower().endswith('webp'):
            # If somehow original was webp (rare), rename directly
            new_name = f"{product_id}-{source_name}-{idx}.webp"
            old_path = fpath
            new_path = os.path.join(product_dir, new_name)
            os.rename(old_path, new_path)
            final_names.append(new_name)
            idx += 1

    logger.debug(f"Downloaded {len(final_names)} images for {filtered_query}")
    return final_names

def search_ecommerce_images(query, product_dir, product_id, max_images=10):
    south_african_stores = ['takealot.com', 'incredible.co.za', 'makro.co.za', 'game.co.za', 'hificorp.co.za', 'firstshop.co.za']
    ecommerce_results = {}

    for store in south_african_stores:
        try:
            store_images = download_images(query, product_dir, max_num=max_images, domain=store, product_id=product_id)
            if store_images:
                ecommerce_results[store] = store_images
            else:
                logger.warning(f"No images found for store: {store}.")
        except Exception as e:
            logger.error(f"Failed to crawl images from {store}: {e}")

    return ecommerce_results

def get_product_from_db():
    return (
        ProductProgress.query
        .filter(or_(ProductProgress.status == 'pending', ProductProgress.status == 'processing'))
        .order_by(ProductProgress.id.asc())
        .first()
    )

@app.route("/load_products", methods=["GET", "POST"])
def load_products():
    products = fetch_shop_products()
    new_products_count = 0
    for prod in products:
        product_id = prod.get("id")
        title = prod.get("title", "unknown")
        existing = ProductProgress.query.filter_by(product_id=product_id).first()
        if not existing:
            new_entry = ProductProgress(
                product_id=product_id,
                title=title,
                status='pending'
            )
            db.session.add(new_entry)
            new_products_count += 1
    db.session.commit()

    if new_products_count > 0:
        return "New products loaded into the database. <a href='/'>Go back</a>."
    else:
        return "No new products found. <a href='/'>Go back</a>."

@app.route("/")
def index():
    product_entry = get_product_from_db()
    if not product_entry:
        return render_template("index.html")

    product_entry.status = 'processing'
    db.session.commit()

    all_products = fetch_shop_products()
    product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
    if not product:
        product_entry.status = 'error'
        product_entry.error_message = "Product not found in store."
        db.session.commit()
        return "Product not found in the Medusa store."

    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)
    os.makedirs(product_dir, exist_ok=True)

    existing_images = product.get("images", [])
    logger.debug(f"Existing images for product {product_entry.product_id}: {existing_images}")
    return render_template("current_images.html", product=product, existing_images=existing_images)

@app.route("/change-images", methods=["POST"])
def change_images():
    decision = request.form.get("decision")

    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    all_products = fetch_shop_products()
    product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
    if not product:
        product_entry.status = 'error'
        product_entry.error_message = "Product not found in store during change-images."
        db.session.commit()
        return "Error: Product not found in the Medusa store."

    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)
    os.makedirs(product_dir, exist_ok=True)

    if decision == "no":
        product_entry.status = 'skipped'
        product_entry.processed_at = datetime.now(timezone.utc)
        db.session.commit()
        return redirect(url_for("index"))

    # Download and convert existing images
    existing_images = product.get("images", [])
    existing_idx = 1
    for image_data in existing_images:
        image_url = image_data.get("url")
        if not image_url:
            continue
        try:
            response = requests.get(image_url, stream=True)
            response.raise_for_status()
            temp_path = os.path.join(product_dir, f"temp_existing_{existing_idx}.jpg")
            with open(temp_path, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
            webp_path = convert_to_webp(temp_path)
            new_name = f"{product_entry.product_id}-existing-{existing_idx}.webp"
            new_path = os.path.join(product_dir, new_name)
            os.rename(webp_path, new_path)
            existing_idx += 1
        except requests.RequestException as e:
            logger.error(f"Error downloading existing image {image_url}: {e}")
        except Exception as e:
            logger.error(f"Error processing image {image_url}: {e}")

    # Count current WebP images
    current_images_count = len([
        f for f in os.listdir(product_dir)
        if f.lower().endswith('webp')
    ])
    images_to_download = max(0, 15 - current_images_count)
    if images_to_download > 0:
        # Download additional images (google)
        download_images(product_name, product_dir, max_num=images_to_download, product_id=product_entry.product_id, domain=None)
        logger.debug(f"Downloaded {images_to_download} additional images for product {product_entry.product_id}.")

    return redirect(url_for("validate_images"))

@app.route("/validate-images")
def validate_images():
    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)
    os.makedirs(product_dir, exist_ok=True)

    product_list = fetch_shop_products()
    product_data = next((p for p in product_list if p["id"] == product_entry.product_id), None)
    current_product_images = product_data.get("images", []) if product_data else []

    # Validate current product images from Medusa store
    validated_current_product_images = []
    for img_data in current_product_images:
        image_url = img_data.get("url")
        if image_url:
            try:
                response = requests.get(image_url, stream=True)
                response.raise_for_status()
                with Image.open(response.raw) as img:
                    width, height = img.size
                    is_valid = width >= 800 and height >= 800
                    validated_current_product_images.append({
                        "url": image_url,
                        "width": width,
                        "height": height,
                        "status": "green" if is_valid else "red"
                    })
            except Exception as e:
                logger.error(f"Error validating image dimensions for {image_url}: {e}")
                validated_current_product_images.append({
                    "url": image_url,
                    "width": None,
                    "height": None,
                    "status": "red"
                })

    # Validate images in product_dir (Google + existing + ecommerce)
    all_webp_images = [f for f in os.listdir(product_dir) if f.lower().endswith('webp')]
    validated_local_images = []
    for image in all_webp_images:
        image_path = os.path.join(product_dir, image)
        is_valid, width, height = validate_image_dimensions(image_path)
        validated_local_images.append({
            "name": image,
            "width": width,
            "height": height,
            "status": "green" if is_valid else "red"
        })

    # Attempt to fetch and validate ecommerce images if not enough
    # We'll just do it once. If already have images, you might skip.
    ecommerce_results = search_ecommerce_images(product_entry.title, product_dir, product_entry.product_id)
    validated_ecommerce_images = {}
    for site, images in ecommerce_results.items():
        site_images = []
        for image in images:
            image_path = os.path.join(product_dir, image)
            is_valid, width, height = validate_image_dimensions(image_path)
            site_images.append({
                "name": image,
                "width": width,
                "height": height,
                "status": "green" if is_valid else "red"
            })
        validated_ecommerce_images[site] = site_images

    return render_template(
        "validate_images.html",
        product=product_entry,
        current_product_images=validated_current_product_images,
        google_images=[img for img in validated_local_images if 'google' in img['name']],
        ecommerce_images=validated_ecommerce_images,
        existing_images=[img for img in validated_local_images if 'existing' in img['name']]
    )

@app.route("/validate", methods=["POST"])
def validate():
    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    all_products = fetch_shop_products()
    product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
    if not product:
        product_entry.status = 'error'
        product_entry.error_message = "Product not found in store during validation."
        db.session.commit()
        return "Error: Product not found in the Medusa store."

    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)
    product_id = product_entry.product_id

    selected_images = request.form.getlist("images")

    processed_images = []
    i = 1
    for image in selected_images:
        old_path = os.path.join(product_dir, image)
        new_name = f"{product_id}-image{i}.webp"
        new_path = os.path.join(product_dir, new_name)
        resize_and_center_image(old_path, new_path)
        processed_images.append(new_name)
        i += 1

    # Remove unselected images
    for f in os.listdir(product_dir):
        if f.lower().endswith('webp'):
            if f not in processed_images:
                # This is an unselected image
                file_path = os.path.join(product_dir, f)
                try:
                    os.remove(file_path)
                    logger.debug(f"Removed unselected image {file_path}")
                except Exception as e:
                    logger.error(f"Error removing {file_path}: {e}")

    return render_template(
        "new_images.html",
        product=product,
        processed_images=processed_images
    )

@app.route("/set-thumbnail", methods=["POST"])
def set_thumbnail():
    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    product_id = product_entry.product_id
    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)

    # Get the selected image for the thumbnail
    thumbnail_image = request.form.get("thumbnail")
    if thumbnail_image:
        original_path = os.path.join(product_dir, thumbnail_image)
        thumbnail_path = os.path.join(product_dir, f"{product_id}-thumbnail.webp")
        shutil.copy(original_path, thumbnail_path)
        logger.debug(f"Set thumbnail from {original_path} to {thumbnail_path}")

    # Mark product as done
    product_entry.status = 'done'
    product_entry.processed_at = datetime.now(timezone.utc)
    db.session.commit()

    # Upload images to S3
    uploaded_files = upload_images_to_s3(product_dir)

    # Remove the local product_dir
    shutil.rmtree(product_dir, ignore_errors=True)
    logger.debug(f"Removed local directory {product_dir}")

    # Build the array of image objects for Medusa
    image_urls = [
        {"url": f"{S3_FILE_URL}/{fname}"}
        for fname in uploaded_files
    ]

    logger.debug(f"Image URLs to update in Medusa: {image_urls}")

    # Now update the product images in Medusa via Admin API
    try:
        update_medusa_product_images(product_id, image_urls)
    except Exception as e:
        logger.error(f"Failed to update Medusa product images for {product_id}: {e}")
        return "Failed to update product images in Medusa."

    return redirect(url_for("index"))

@app.route("/restart", methods=["POST"])
def restart():
    try:
        ProductProgress.query.update({ProductProgress.status: 'pending', ProductProgress.processed_at: None})
        db.session.commit()
        logger.info("Restarted all products to 'pending' status.")
    except Exception as e:
        logger.error(f"Error restarting products: {e}")
        return "Failed to restart products."

    return redirect(url_for("index"))

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))


# import logging
# import os
# import shutil
# from datetime import datetime, timezone
#
# import boto3
# import requests
# import tempfile
# from PIL import Image
# from flask import Flask, render_template, request, redirect, url_for
# from flask_sqlalchemy import SQLAlchemy
# from icrawler.builtin import GoogleImageCrawler
#
# from dotenv import load_dotenv
# from sqlalchemy import or_
#
# load_dotenv()
#
# MEDUSA_API_URL = os.getenv('MEDUSA_API_URL')
# PUBLISHABLE_KEY = os.getenv('NEXT_PUBLIC_MEDUSA_PUBLISHABLE_KEY')
# MEDUSA_ADMIN_URL = os.getenv('MEDUSA_ADMIN_URL')
#
# # S3 Configuration
# S3_FILE_URL = os.getenv('S3_FILE_URL')
# S3_BUCKET = os.getenv('S3_BUCKET')
# S3_REGION = os.getenv('S3_REGION')
# S3_ACCESS_KEY_ID = os.getenv('S3_ACCESS_KEY_ID')
# S3_SECRET_ACCESS_KEY = os.getenv('S3_SECRET_ACCESS_KEY')
# S3_ENDPOINT = os.getenv('S3_ENDPOINT')
#
# ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
# ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
#
# def get_jwt_token(email, password):
#     url = f"{MEDUSA_ADMIN_URL}/auth/user/emailpass"
#     payload = {"email": email, "password": password}
#     headers = {"Content-Type": "application/json"}
#     response = requests.post(url, json=payload, headers=headers)
#     response.raise_for_status()
#     return response.json()["token"]
#
# ADMIN_TOKEN = get_jwt_token(ADMIN_EMAIL, ADMIN_PASSWORD)
#
# s3_client = boto3.client(
#     's3',
#     aws_access_key_id=S3_ACCESS_KEY_ID,
#     aws_secret_access_key=S3_SECRET_ACCESS_KEY,
#     region_name=S3_REGION,
#     endpoint_url=S3_ENDPOINT
# )
#
# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)  # Adjust as needed
#
# console_handler = logging.StreamHandler()
# console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# console_handler.setFormatter(console_formatter)
# logger.addHandler(console_handler)
#
# app = Flask(__name__)
# app.secret_key = 'my_secret_key'
#
# # Database configuration
# app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DB_URI')
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# db = SQLAlchemy(app)
#
# class ProductProgress(db.Model):
#     id = db.Column(db.Integer, primary_key=True, autoincrement=True)
#     product_id = db.Column(db.String(255), unique=True, nullable=False)
#     title = db.Column(db.String(255), nullable=False)
#     status = db.Column(db.String(50), default='pending')  # 'pending', 'processing', 'done', 'skipped'
#     processed_images_count = db.Column(db.Integer, default=0)
#     error_message = db.Column(db.String(1024), nullable=True)
#     processed_at = db.Column(db.DateTime, nullable=True)
#
# with app.app_context():
#     db.create_all()
#
# OUTPUT_DIR = "./static/product_images"
# os.makedirs(OUTPUT_DIR, exist_ok=True)
#
# def fetch_shop_products():
#     headers = {"x-publishable-api-key": PUBLISHABLE_KEY}
#     products = []
#     offset = 0
#     limit = 50
#
#     while True:
#         url = f"{MEDUSA_API_URL}?offset={offset}&limit={limit}"
#         try:
#             response = requests.get(url, headers=headers)
#             response.raise_for_status()
#             batch = response.json().get("products", [])
#             if not batch:
#                 break
#             products.extend(batch)
#             offset += limit
#         except requests.RequestException as e:
#             logger.error(f"Error fetching products: {e}")
#             break
#
#     logger.debug(f"Total products fetched: {len(products)}")
#     return products
#
# def get_product_from_db():
#     return (
#         ProductProgress.query
#         .filter(or_(ProductProgress.status == 'pending', ProductProgress.status == 'processing'))
#         .order_by(ProductProgress.id.asc())
#         .first()
#     )
#
# @app.route("/load_products", methods=["GET", "POST"])
# def load_products():
#     products = fetch_shop_products()
#     new_products_count = 0
#     for prod in products:
#         product_id = prod.get("id")
#         title = prod.get("title", "unknown")
#         existing = ProductProgress.query.filter_by(product_id=product_id).first()
#         if not existing:
#             new_entry = ProductProgress(
#                 product_id=product_id,
#                 title=title,
#                 status='pending'
#             )
#             db.session.add(new_entry)
#             new_products_count += 1
#     db.session.commit()
#
#     if new_products_count > 0:
#         return "New products loaded into the database. <a href='/'>Go back</a>."
#     else:
#         return "No new products found. <a href='/'>Go back</a>."
#
# @app.route("/")
# def index():
#     product_entry = get_product_from_db()
#     if not product_entry:
#         return render_template("index.html")
#
#     product_entry.status = 'processing'
#     db.session.commit()
#
#     all_products = fetch_shop_products()
#     product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
#     if not product:
#         product_entry.status = 'error'
#         product_entry.error_message = "Product not found in store."
#         db.session.commit()
#         return "Product not found in the Medusa store."
#
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     os.makedirs(product_dir, exist_ok=True)
#
#     existing_images = product.get("images", [])
#     print(existing_images)
#     return render_template("current_images.html", product=product, existing_images=existing_images)
#
# from urllib.parse import urlparse
#
# def convert_to_webp(input_path):
#     """
#     Convert the given image file to WebP and remove the original file.
#     Returns the path to the newly created WebP file.
#     """
#     webp_path = os.path.splitext(input_path)[0] + ".webp"
#     with Image.open(input_path) as img:
#         img = img.convert("RGB")  # Ensure proper mode for WebP
#         img.save(webp_path, format="WEBP", quality=90)
#     os.remove(input_path)
#     return webp_path
#
# @app.route("/change-images", methods=["POST"])
# def change_images():
#     decision = request.form.get("decision")
#
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     all_products = fetch_shop_products()
#     product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
#     if not product:
#         product_entry.status = 'error'
#         product_entry.error_message = "Product not found in store during change-images."
#         db.session.commit()
#         return "Error: Product not found in the Medusa store."
#
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     os.makedirs(product_dir, exist_ok=True)
#
#     if decision == "no":
#         product_entry.status = 'skipped'
#         product_entry.processed_at = datetime.now(timezone.utc)
#         db.session.commit()
#         return redirect(url_for("index"))
#
#     existing_images = product["images"]
#     for image_data in existing_images:
#         image_url = image_data.get("url")
#         if not image_url:
#             continue
#
#         base_url = urlparse(image_url).netloc.replace('.', '_')
#         base_url_dir = os.path.join(product_dir, base_url)
#         os.makedirs(base_url_dir, exist_ok=True)
#
#         image_name = image_url.split("/")[-1]
#         existing_image_path = os.path.join(base_url_dir, image_name)
#
#         try:
#             response = requests.get(image_url, stream=True)
#             response.raise_for_status()
#             with open(existing_image_path, "wb") as file:
#                 for chunk in response.iter_content(1024):
#                     file.write(chunk)
#
#             # Convert downloaded image to WebP
#             existing_image_path = convert_to_webp(existing_image_path)
#
#         except requests.RequestException as e:
#             logger.error(f"Error downloading existing image {image_url}: {e}")
#
#     current_images_count = len([f for f in os.listdir(product_dir) if f.lower().endswith('webp')])
#     images_to_download = max(0, 15 - current_images_count)
#     if images_to_download > 0:
#         download_images(product_name, product_dir, max_num=images_to_download)
#
#     return redirect(url_for("validate_images"))
#
# def search_ecommerce_images(query, output_dir, max_images=10):
#     south_african_stores = ['takealot.com', 'incredible.co.za', 'makro.co.za', 'game.co.za', 'hificorp.co.za', 'firstshop.co.za']
#     ecommerce_results = {}
#
#     for store in south_african_stores:
#         domain_output_dir = os.path.join(output_dir, store.replace('.', '_'))
#         os.makedirs(domain_output_dir, exist_ok=True)
#
#         try:
#             download_images(query, domain_output_dir, max_num=max_images, domain=store)
#             # Convert all downloaded images in this directory to WebP
#             for f in os.listdir(domain_output_dir):
#                 fpath = os.path.join(domain_output_dir, f)
#                 if os.path.isfile(fpath) and not fpath.lower().endswith('webp'):
#                     try:
#                         convert_to_webp(fpath)
#                     except Exception as e:
#                         logger.error(f"Error converting {fpath} to webp: {e}")
#
#             valid_images = [f for f in os.listdir(domain_output_dir) if f.lower().endswith('webp')]
#             if valid_images:
#                 ecommerce_results[store] = valid_images
#             else:
#                 logger.warning(f"No images found for store: {store}. Cleaning up directory.")
#                 shutil.rmtree(domain_output_dir, ignore_errors=True)
#         except Exception as e:
#             logger.error(f"Failed to crawl images from {store}: {e}")
#             shutil.rmtree(domain_output_dir, ignore_errors=True)
#
#     return ecommerce_results
#
# def validate_image_dimensions(image_path):
#     try:
#         with Image.open(image_path) as img:
#             width, height = img.size
#             is_valid = width >= 800 and height >= 800
#             return is_valid, width, height
#     except Exception as e:
#         logger.error(f"Error validating image dimensions for {image_path}: {e}")
#         return False, 0, 0
#
# @app.route("/validate-images")
# def validate_images():
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     os.makedirs(product_dir, exist_ok=True)
#
#     product = fetch_shop_products()
#     product_data = next((p for p in product if p["id"] == product_entry.product_id), None)
#     current_product_images = product_data.get("images", []) if product_data else []
#
#     validated_current_product_images = []
#     for img_data in current_product_images:
#         image_url = img_data.get("url")
#         if image_url:
#             try:
#                 response = requests.get(image_url, stream=True)
#                 response.raise_for_status()
#                 with Image.open(response.raw) as img:
#                     width, height = img.size
#                     is_valid = width >= 800 and height >= 800
#                     validated_current_product_images.append({
#                         "url": image_url,
#                         "width": width,
#                         "height": height,
#                         "status": "green" if is_valid else "red"
#                     })
#             except Exception as e:
#                 logger.error(f"Error validating image dimensions for {image_url}: {e}")
#                 validated_current_product_images.append({
#                     "url": image_url,
#                     "width": None,
#                     "height": None,
#                     "status": "red"
#                 })
#
#     # Validate Google Images
#     google_images = [f for f in os.listdir(product_dir) if f.lower().endswith('webp')]
#     validated_google_images = []
#     for image in google_images:
#         image_path = os.path.join(product_dir, image)
#         is_valid, width, height = validate_image_dimensions(image_path)
#         validated_google_images.append({
#             "name": image,
#             "width": width,
#             "height": height,
#             "status": "green" if is_valid else "red"
#         })
#
#     # Fetch and validate e-commerce images
#     ecommerce_results = search_ecommerce_images(product_entry.title, product_dir)
#     validated_ecommerce_images = {}
#     for site, images in ecommerce_results.items():
#         site_images = []
#         for image in images:
#             image_path = os.path.join(product_dir, site.replace('.', '_'), image)
#             is_valid, width, height = validate_image_dimensions(image_path)
#             site_images.append({
#                 "name": image,
#                 "width": width,
#                 "height": height,
#                 "status": "green" if is_valid else "red"
#             })
#         validated_ecommerce_images[site] = site_images
#
#     return render_template(
#         "validate_images.html",
#         product=product_entry,
#         current_product_images=validated_current_product_images,
#         google_images=validated_google_images,
#         ecommerce_images=validated_ecommerce_images
#     )
#
# from shutil import rmtree
#
# @app.route("/validate", methods=["POST"])
# def validate():
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     all_products = fetch_shop_products()
#     product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     product_id = product_entry.product_id
#
#     selected_images = request.form.getlist("images")
#
#     processed_images = []
#     for i, image in enumerate(selected_images):
#         old_path = image if image.startswith("http") else os.path.join(product_dir, image)
#         new_name = f"{product_id}-image{i + 1}.webp"
#         new_path = os.path.join(product_dir, new_name)
#         resize_and_center_image(old_path, new_path)
#         processed_images.append(new_name)
#
#     # Remove unselected images or directories
#     for item in os.listdir(product_dir):
#         item_path = os.path.join(product_dir, item)
#         if item not in processed_images:
#             try:
#                 if os.path.isfile(item_path):
#                     os.remove(item_path)
#                 elif os.path.isdir(item_path):
#                     rmtree(item_path)
#             except Exception as e:
#                 logger.error(f"Error removing {item_path}: {e}")
#
#     return render_template(
#         "new_images.html",
#         product=product,
#         processed_images=processed_images
#     )
#
# @app.route("/set-thumbnail", methods=["POST"])
# def set_thumbnail():
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     product_id = product_entry.product_id
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#
#     # Get the selected image for the thumbnail
#     thumbnail_image = request.form.get("thumbnail")
#     if thumbnail_image:
#         original_path = os.path.join(product_dir, thumbnail_image)
#         thumbnail_path = os.path.join(product_dir, f"{product_id}-thumbnail.webp")
#         shutil.copy(original_path, thumbnail_path)
#
#     # Mark product as done
#     product_entry.status = 'done'
#     product_entry.processed_at = datetime.now(timezone.utc)
#     db.session.commit()
#
#     # Upload images to S3
#     uploaded_files = upload_images_to_s3(product_dir)
#
#     # Remove the local product_dir
#     shutil.rmtree(product_dir, ignore_errors=True)
#
#     # Build the array of image objects for Medusa
#     image_urls = [
#         {"url": f"{S3_FILE_URL}/{fname}"}
#         for fname in uploaded_files
#     ]
#
#     print(image_urls)
#
#     # Now update the product images in Medusa via Admin API
#     update_medusa_product_images(product_id, image_urls)
#
#     return redirect(url_for("index"))
#
# @app.route("/restart", methods=["POST"])
# def restart():
#     ProductProgress.query.update({ProductProgress.status: 'pending', ProductProgress.processed_at: None})
#     db.session.commit()
#     return redirect(url_for("index"))
#
# def download_images(query, output_dir, max_num=10, domain=None):
#     retries = 3
#     for attempt in range(retries):
#         try:
#             google_crawler = GoogleImageCrawler(storage={'root_dir': output_dir})
#             filtered_query = f"{query}"
#             if domain:
#                 filtered_query += f" site:{domain}"
#             google_crawler.crawl(keyword=filtered_query, max_num=max_num)
#
#             # Convert all downloaded images in output_dir to WebP
#             for f in os.listdir(output_dir):
#                 fpath = os.path.join(output_dir, f)
#                 if os.path.isfile(fpath) and not fpath.lower().endswith('webp'):
#                     try:
#                         convert_to_webp(fpath)
#                     except Exception as e:
#                         logger.error(f"Error converting {fpath} to webp: {e}")
#             return
#         except Exception as e:
#             logger.error(f"Error during crawling for query '{query}' (Attempt {attempt + 1}/{retries}): {e}")
#             if attempt < retries - 1:
#                 continue
#             else:
#                 raise
#
# def resize_and_center_image(input_path, output_path):
#     is_url = input_path.startswith("http")
#     if is_url:
#         temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".webp")
#         try:
#             response = requests.get(input_path, stream=True)
#             response.raise_for_status()
#             with open(temp_file.name, "wb") as file:
#                 for chunk in response.iter_content(1024):
#                     file.write(chunk)
#             input_path = temp_file.name
#         except Exception as e:
#             logger.error(f"Error downloading image from URL {input_path}: {e}")
#             raise
#
#     try:
#         with Image.open(input_path) as img:
#             img = img.convert("RGBA")
#             white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
#             img = Image.alpha_composite(white_background, img)
#             img = img.convert("RGB")
#
#             original_width, original_height = img.size
#             if original_width > original_height:
#                 new_width = 800
#                 new_height = int(800 * (original_height / original_width))
#             else:
#                 new_height = 800
#                 new_width = int(800 * (original_width / original_height))
#
#             resized_img = img.resize((new_width, new_height), Image.LANCZOS)
#             canvas = Image.new("RGB", (800, 800), (255, 255, 255))
#             x_offset = (800 - new_width) // 2
#             y_offset = (800 - new_height) // 2
#             canvas.paste(resized_img, (x_offset, y_offset))
#             canvas.save(output_path, format="WEBP", quality=90)
#     finally:
#         if is_url:
#             os.unlink(temp_file.name)
#
# def upload_images_to_s3(product_dir):
#     uploaded_files = []
#     for filename in os.listdir(product_dir):
#         if filename.lower().endswith('webp'):
#             file_path = os.path.join(product_dir, filename)
#             try:
#                 s3_client.upload_file(
#                     Filename=file_path,
#                     Bucket=S3_BUCKET,
#                     Key=filename,
#                     ExtraArgs={
#                         'ContentType': 'image/webp',
#                         'ACL': 'public-read'
#                     }
#                 )
#                 uploaded_files.append(filename)
#                 logger.info(f"Uploaded {filename} to S3 bucket {S3_BUCKET}")
#             except boto3.exceptions.S3UploadFailedError as e:
#                 logger.error(f"Upload failed for {filename}: {e}")
#             except Exception as e:
#                 logger.exception(f"Unexpected error during S3 upload for {filename}: {e}")
#     return uploaded_files
#
# def update_medusa_product_images(product_id, image_urls):
#     headers = {
#         "Authorization": f"Bearer {ADMIN_TOKEN}",
#         "Content-Type": "application/json"
#     }
#
#     thumbnail_url = next((image['url'] for image in image_urls if image['url'].endswith('thumbnail.webp')), None)
#     if not thumbnail_url:
#         if not image_urls:
#             raise ValueError("No images provided in the image URLs list.")
#         thumbnail_url = image_urls[0]['url']
#
#     data = {
#         "thumbnail": thumbnail_url,
#         "images": image_urls
#     }
#     url = f"{MEDUSA_ADMIN_URL}/admin/products/{product_id}"
#
#     logger.debug(f"Updating product images at {url} with data: {data}")
#     response = requests.post(url, headers=headers, json=data)
#     response.raise_for_status()
#     return response.json()
#
# if __name__ == '__main__':
#     app.run(debug=True, port=os.getenv("PORT", default=5000))


# import logging
# import os
# import shutil
# from datetime import datetime, timezone
#
# import boto3
# import requests
# import tempfile
# from PIL import Image
# from flask import Flask, render_template, request, redirect, url_for
# from flask_sqlalchemy import SQLAlchemy
# from icrawler.builtin import GoogleImageCrawler
#
# from dotenv import load_dotenv
# from sqlalchemy import or_
#
# load_dotenv()
#
# MEDUSA_API_URL = os.getenv('MEDUSA_API_URL')
# PUBLISHABLE_KEY = os.getenv('NEXT_PUBLIC_MEDUSA_PUBLISHABLE_KEY')
# MEDUSA_ADMIN_URL = os.getenv('MEDUSA_ADMIN_URL')
#
# # S3 Configuration
# S3_FILE_URL = os.getenv('S3_FILE_URL')
# S3_BUCKET = os.getenv('S3_BUCKET')
# S3_REGION = os.getenv('S3_REGION')
# S3_ACCESS_KEY_ID = os.getenv('S3_ACCESS_KEY_ID')
# S3_SECRET_ACCESS_KEY = os.getenv('S3_SECRET_ACCESS_KEY')
# S3_ENDPOINT = os.getenv('S3_ENDPOINT')
#
# ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
# ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
#
# def get_jwt_token(email, password):
#     url = f"{MEDUSA_ADMIN_URL}/auth/user/emailpass"
#     payload = {"email": email, "password": password}
#     headers = {"Content-Type": "application/json"}
#
#     response = requests.post(url, json=payload, headers=headers)
#     response.raise_for_status()
#     return response.json()["token"]
#
# ADMIN_TOKEN = get_jwt_token(ADMIN_EMAIL, ADMIN_PASSWORD)
#
# s3_client = boto3.client(
#     's3',
#     aws_access_key_id=S3_ACCESS_KEY_ID,
#     aws_secret_access_key=S3_SECRET_ACCESS_KEY,
#     region_name=S3_REGION,
#     endpoint_url=S3_ENDPOINT
# )
#
# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)  # Set logging level as needed (DEBUG, INFO, WARNING, ERROR, CRITICAL)
#
# # Configure logging handler (e.g., console handler for demonstration)
# console_handler = logging.StreamHandler()
# console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# console_handler.setFormatter(console_formatter)
# logger.addHandler(console_handler)
#
# app = Flask(__name__)
# app.secret_key = 'secret_key'  # Necessary for form submission, even if not using session state.
#
# # Database configuration
# app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DB_URI')
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# db = SQLAlchemy(app)
#
# # Models
# class ProductProgress(db.Model):
#     id = db.Column(db.Integer, primary_key=True, autoincrement=True)
#     product_id = db.Column(db.String(255), unique=True, nullable=False)
#     title = db.Column(db.String(255), nullable=False)
#     status = db.Column(db.String(50), default='pending')  # 'pending', 'processing', 'done', 'skipped'
#     processed_images_count = db.Column(db.Integer, default=0)
#     error_message = db.Column(db.String(1024), nullable=True)
#     processed_at = db.Column(db.DateTime, nullable=True)
#
# with app.app_context():
#     db.create_all()
#
# # Directory to store product images
# OUTPUT_DIR = "./static/product_images"
# os.makedirs(OUTPUT_DIR, exist_ok=True)
#
# def fetch_shop_products():
#     """
#     Fetch all products from the Medusa store using pagination.
#     """
#     headers = {"x-publishable-api-key": PUBLISHABLE_KEY}
#     products = []
#     offset = 0
#     limit = 50  # Medusa's default or maximum batch size
#
#     while True:
#         url = f"{MEDUSA_API_URL}?offset={offset}&limit={limit}"
#         try:
#             response = requests.get(url, headers=headers)
#             response.raise_for_status()
#             batch = response.json().get("products", [])
#             if not batch:
#                 # No more products to fetch
#                 break
#             products.extend(batch)
#             offset += limit  # Move to the next batch
#         except requests.RequestException as e:
#             logger.error(f"Error fetching products: {e}")
#             break
#
#     logger.debug(f"Total products fetched: {len(products)}")
#     return products
#
#
# def get_product_from_db():
#     """
#     Retrieve the next product that is either pending or processing.
#     Prioritize pending products first, followed by processing.
#     """
#     return (
#         ProductProgress.query
#         .filter(or_(ProductProgress.status == 'pending', ProductProgress.status == 'processing'))
#         .order_by(ProductProgress.id.asc())
#         .first()
#     )
#
# @app.route("/load_products", methods=["GET", "POST"])
# def load_products():
#     products = fetch_shop_products()
#     new_products_count = 0
#     for prod in products:
#         product_id = prod.get("id")
#         title = prod.get("title", "unknown")
#         existing = ProductProgress.query.filter_by(product_id=product_id).first()
#         if not existing:
#             new_entry = ProductProgress(
#                 product_id=product_id,
#                 title=title,
#                 status='pending'
#             )
#             db.session.add(new_entry)
#             new_products_count += 1
#     db.session.commit()
#
#     if new_products_count > 0:
#         return "New products loaded into the database. <a href='/'>Go back</a>."
#     else:
#         return "No new products found. <a href='/'>Go back</a>."
#
# @app.route("/")
# def index():
#     """
#     Display the current pending product and its images. If none pending, all are processed.
#     """
#     product_entry = get_product_from_db()
#     if not product_entry:
#         return render_template(
#             "index.html"
#         )
#
#     # Mark the product as processing (optional step)
#     product_entry.status = 'processing'
#     db.session.commit()
#
#     # Fetch all products from the store to get images
#     all_products = fetch_shop_products()
#     product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
#     if not product:
#         product_entry.status = 'error'
#         product_entry.error_message = "Product not found in store."
#         db.session.commit()
#         return "Product not found in the Medusa store."
#
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     os.makedirs(product_dir, exist_ok=True)
#
#     existing_images = product.get("images", [])
#     return render_template(
#         "current_images.html",
#         product=product,
#         existing_images=existing_images
#     )
#
# from urllib.parse import urlparse
#
# @app.route("/change-images", methods=["POST"])
# def change_images():
#     """
#     Handle the decision to change images or skip the product.
#     Include existing images and ensure a total of 15 images.
#     """
#     decision = request.form.get("decision")
#
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     # Fetch product data again
#     all_products = fetch_shop_products()
#     product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
#     if not product:
#         product_entry.status = 'error'
#         product_entry.error_message = "Product not found in store during change-images."
#         db.session.commit()
#         return "Error: Product not found in the Medusa store."
#
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     os.makedirs(product_dir, exist_ok=True)
#
#     if decision == "no":
#         # Skip the product
#         product_entry.status = 'skipped'
#         product_entry.processed_at = datetime.now(timezone.utc)
#         db.session.commit()
#         return redirect(url_for("index"))
#
#     # Proceed with downloading new images
#     existing_images = product["images"]
#     for image_data in existing_images:
#         image_url = image_data.get("url")
#         if not image_url:
#             continue
#
#         # Parse the base URL to create a unique folder for the images
#         base_url = urlparse(image_url).netloc.replace('.', '_')
#         base_url_dir = os.path.join(product_dir, base_url)
#         os.makedirs(base_url_dir, exist_ok=True)
#
#         image_name = image_url.split("/")[-1]
#         existing_image_path = os.path.join(base_url_dir, image_name)
#
#         # Download and save existing images to the directory
#         try:
#             response = requests.get(image_url, stream=True)
#             response.raise_for_status()
#             with open(existing_image_path, "wb") as file:
#                 for chunk in response.iter_content(1024):
#                     file.write(chunk)
#         except requests.RequestException as e:
#             logger.error(f"Error downloading existing image {image_url}: {e}")
#
#     # Count current images
#     current_images_count = len([f for f in os.listdir(product_dir) if f.lower().endswith(('jpg', 'jpeg', 'png', 'webp'))])
#
#     # Ensure total of 15 images
#     images_to_download = max(0, 15 - current_images_count)
#     if images_to_download > 0:
#         download_images(product_name, product_dir, max_num=images_to_download)
#
#     return redirect(url_for("validate_images"))
#
#
# def search_ecommerce_images(query, output_dir, max_images=10):
#     """
#     Crawl South African e-commerce websites for product images.
#     Skips stores that return no valid images.
#     """
#     south_african_stores = ['takealot.com', 'incredible.co.za', 'makro.co.za', 'game.co.za', 'hificorp.co.za', 'firstshop.com']
#     ecommerce_results = {}
#
#     for store in south_african_stores:
#         domain_output_dir = os.path.join(output_dir, store.replace('.', '_'))
#         os.makedirs(domain_output_dir, exist_ok=True)
#
#         try:
#             download_images(query, domain_output_dir, max_num=max_images, domain=store)
#
#             # Filter valid images
#             valid_images = [f for f in os.listdir(domain_output_dir) if f.lower().endswith(('jpg', 'jpeg', 'png', 'webp'))]
#             if valid_images:
#                 ecommerce_results[store] = valid_images
#             else:
#                 logger.warning(f"No images found for store: {store}. Cleaning up directory.")
#                 shutil.rmtree(domain_output_dir, ignore_errors=True)
#         except Exception as e:
#             logger.error(f"Failed to crawl images from {store}: {e}")
#             shutil.rmtree(domain_output_dir, ignore_errors=True)
#
#     return ecommerce_results
#
#
# def validate_image_dimensions(image_path):
#     """
#     Validate if the image dimensions meet the minimum size requirement.
#     Returns a tuple (valid: bool, width: int, height: int).
#     """
#     try:
#         with Image.open(image_path) as img:
#             width, height = img.size
#             is_valid = width >= 800 and height >= 800
#             return is_valid, width, height
#     except Exception as e:
#         logger.error(f"Error validating image dimensions for {image_path}: {e}")
#         return False, 0, 0
#
#
# @app.route("/validate-images")
# def validate_images():
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     os.makedirs(product_dir, exist_ok=True)
#
#     # Current Product Images
#     product = fetch_shop_products()
#     product_data = next((p for p in product if p["id"] == product_entry.product_id), None)
#     current_product_images = product_data.get("images", []) if product_data else []
#
#     validated_current_product_images = []
#     for img_data in current_product_images:
#         image_url = img_data.get("url")
#         if image_url:
#             try:
#                 # Download image temporarily for validation
#                 response = requests.get(image_url, stream=True)
#                 response.raise_for_status()
#                 with Image.open(response.raw) as img:
#                     width, height = img.size
#                     is_valid = width >= 800 and height >= 800
#                     validated_current_product_images.append({
#                         "url": image_url,
#                         "width": width,
#                         "height": height,
#                         "status": "green" if is_valid else "red"
#                     })
#             except Exception as e:
#                 logger.error(f"Error validating image dimensions for {image_url}: {e}")
#                 validated_current_product_images.append({
#                     "url": image_url,
#                     "width": None,
#                     "height": None,
#                     "status": "red"
#                 })
#
#     # Validate Google Images
#     google_images = [f for f in os.listdir(product_dir) if f.lower().endswith(('jpg', 'jpeg', 'png', 'webp'))]
#     validated_google_images = []
#     for image in google_images:
#         image_path = os.path.join(product_dir, image)
#         is_valid, width, height = validate_image_dimensions(image_path)
#         validated_google_images.append({
#             "name": image,
#             "width": width,
#             "height": height,
#             "status": "green" if is_valid else "red"
#         })
#
#     # Fetch and validate e-commerce images
#     ecommerce_results = search_ecommerce_images(product_entry.title, product_dir)
#     validated_ecommerce_images = {}
#     for site, images in ecommerce_results.items():
#         site_images = []
#         for image in images:
#             image_path = os.path.join(product_dir, site.replace('.', '_'), image)
#             is_valid, width, height = validate_image_dimensions(image_path)
#             site_images.append({
#                 "name": image,
#                 "width": width,
#                 "height": height,
#                 "status": "green" if is_valid else "red"
#             })
#         validated_ecommerce_images[site] = site_images
#
#     return render_template(
#         "validate_images.html",
#         product=product_entry,
#         current_product_images=validated_current_product_images,
#         google_images=validated_google_images,
#         ecommerce_images=validated_ecommerce_images
#     )
#
#
#
# from shutil import rmtree
#
# @app.route("/validate", methods=["POST"])
# def validate():
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     all_products = fetch_shop_products()
#     product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#     product_id = product_entry.product_id
#
#     selected_images = request.form.getlist("images")
#
#     processed_images = []
#     for i, image in enumerate(selected_images):
#         old_path = image if image.startswith("http") else os.path.join(product_dir, image)
#         new_name = f"{product_id}-image{i + 1}.webp"
#         new_path = os.path.join(product_dir, new_name)
#         resize_and_center_image(old_path, new_path)
#         processed_images.append(new_name)
#
#     # Remove unselected images or directories
#     for item in os.listdir(product_dir):
#         item_path = os.path.join(product_dir, item)
#         if item not in processed_images:
#             try:
#                 if os.path.isfile(item_path):
#                     os.remove(item_path)  # Remove files
#                 elif os.path.isdir(item_path):
#                     rmtree(item_path)  # Remove directories
#             except Exception as e:
#                 logger.error(f"Error removing {item_path}: {e}")
#
#     return render_template(
#         "new_images.html",
#         product=product,
#         processed_images=processed_images
#     )
#
#
# @app.route("/set-thumbnail", methods=["POST"])
# def set_thumbnail():
#     product_entry = ProductProgress.query.filter_by(status='processing').first()
#     if not product_entry:
#         return "No product currently in processing state."
#
#     product_id = product_entry.product_id
#     product_name = product_entry.title.replace(" ", "_").lower()
#     product_dir = os.path.join(OUTPUT_DIR, product_name)
#
#     # Get the selected image for the thumbnail
#     thumbnail_image = request.form.get("thumbnail")
#     if thumbnail_image:
#         original_path = os.path.join(product_dir, thumbnail_image)
#         thumbnail_path = os.path.join(product_dir, f"{product_id}-thumbnail.webp")
#         shutil.copy(original_path, thumbnail_path)
#
#     # Mark product as done
#     product_entry.status = 'done'
#     product_entry.processed_at = datetime.now(timezone.utc)
#     db.session.commit()
#
#     # Upload images to S3
#     uploaded_files = upload_images_to_s3(product_dir)
#
#     # Remove the local product_dir
#     shutil.rmtree(product_dir, ignore_errors=True)
#
#     # Build the array of image objects for Medusa
#     image_urls = [
#         {"url": f"{S3_FILE_URL}/{fname}"}
#         for fname in uploaded_files
#     ]
#
#     print(image_urls)
#
#     # Now update the product images in Medusa via Admin API
#     update_medusa_product_images(product_id, image_urls)
#
#     return redirect(url_for("index"))
#
# @app.route("/restart", methods=["POST"])
# def restart():
#     """
#     This could reset all products to pending if desired.
#     """
#     ProductProgress.query.update({ProductProgress.status: 'pending', ProductProgress.processed_at: None})
#     db.session.commit()
#     return redirect(url_for("index"))
#
# def download_images(query, output_dir, max_num=10, domain=None):
#     """
#     Download a specific number of images for a product.
#     Supports filtering by domain (e.g., e-commerce sites).
#     Includes retries for robustness.
#     """
#     retries = 3
#     for attempt in range(retries):
#         try:
#             google_crawler = GoogleImageCrawler(storage={'root_dir': output_dir})
#             filtered_query = f"{query}"
#             if domain:
#                 filtered_query += f" site:{domain}"
#             google_crawler.crawl(keyword=filtered_query, max_num=max_num)
#             return  # Exit on success
#         except Exception as e:
#             logger.error(f"Error during crawling for query '{query}' (Attempt {attempt + 1}/{retries}): {e}")
#             if attempt < retries - 1:
#                 continue
#             else:
#                 raise  # Re-raise the error after all retries fail
#
#
#
# def resize_and_center_image(input_path, output_path):
#     """
#     Resize the image to fit within 800x800 canvas while maintaining aspect ratio,
#     and ensure the background is explicitly white. Save in WebP format.
#     Handles both local file paths and URLs.
#     """
#     is_url = input_path.startswith("http")
#     if is_url:
#         # If input is a URL, download it to a temporary file
#         temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".webp")
#         try:
#             response = requests.get(input_path, stream=True)
#             response.raise_for_status()
#             with open(temp_file.name, "wb") as file:
#                 for chunk in response.iter_content(1024):
#                     file.write(chunk)
#             input_path = temp_file.name
#         except Exception as e:
#             logger.error(f"Error downloading image from URL {input_path}: {e}")
#             raise
#
#     try:
#         with Image.open(input_path) as img:
#             # Convert the image to RGBA to handle transparency
#             img = img.convert("RGBA")
#
#             # Create a white background
#             white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
#             img = Image.alpha_composite(white_background, img)
#
#             # Convert back to RGB for non-transparent saving
#             img = img.convert("RGB")
#
#             # Determine new dimensions to fit within 800x800
#             original_width, original_height = img.size
#             if original_width > original_height:
#                 new_width = 800
#                 new_height = int(800 * (original_height / original_width))
#             else:
#                 new_height = 800
#                 new_width = int(800 * (original_width / original_height))
#
#             # Resize the image
#             resized_img = img.resize((new_width, new_height), Image.LANCZOS)
#
#             # Create an 800x800 white canvas
#             canvas = Image.new("RGB", (800, 800), (255, 255, 255))  # Explicit white
#
#             # Calculate position to center the resized image
#             x_offset = (800 - new_width) // 2
#             y_offset = (800 - new_height) // 2
#
#             # Paste the resized image onto the white canvas
#             canvas.paste(resized_img, (x_offset, y_offset))
#
#             # Save the processed image in WebP format
#             canvas.save(output_path, format="WEBP", quality=90)
#     finally:
#         if is_url:
#             # Remove the temporary file
#             os.unlink(temp_file.name)
#
#
# def upload_images_to_s3(product_dir):
#     uploaded_files = []
#     for filename in os.listdir(product_dir):
#         if filename.lower().endswith(('webp',)):
#             file_path = os.path.join(product_dir, filename)
#             try:
#                 s3_client.upload_file(
#                     Filename=file_path,
#                     Bucket=S3_BUCKET,
#                     Key=filename,
#                     ExtraArgs={
#                         'ContentType': 'image/webp',
#                         'ACL': 'public-read'
#                     }
#                 )
#                 uploaded_files.append(filename)
#                 logger.info(f"Uploaded {filename} to S3 bucket {S3_BUCKET}")
#             except boto3.exceptions.S3UploadFailedError as e:
#                 logger.error(f"Upload failed for {filename}: {e}")
#             except Exception as e:
#                 logger.exception(f"Unexpected error during S3 upload for {filename}: {e}")
#     return uploaded_files
#
#
# def update_medusa_product_images(product_id, image_urls):
#     headers = {
#         "Authorization": f"Bearer {ADMIN_TOKEN}",
#         "Content-Type": "application/json"
#     }
#
#     # Find the thumbnail URL or default to the first image in the list
#     thumbnail_url = next((image['url'] for image in image_urls if image['url'].endswith('thumbnail.webp')), None)
#     if not thumbnail_url:
#         if not image_urls:
#             raise ValueError("No images provided in the image URLs list.")
#         thumbnail_url = image_urls[0]['url']  # Use the first image if no thumbnail is found
#
#     data = {
#         "thumbnail": thumbnail_url,
#         "images": image_urls
#     }
#     url = f"{MEDUSA_ADMIN_URL}/admin/products/{product_id}"
#
#     logger.debug(f"Updating product images at {url} with data: {data}")
#     response = requests.post(url, headers=headers, json=data)
#     response.raise_for_status()
#     return response.json()
#
# if __name__ == '__main__':
#     app.run(debug=True, port=os.getenv("PORT", default=5000))
