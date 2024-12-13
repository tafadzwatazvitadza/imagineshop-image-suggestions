import logging
import os
import shutil
from datetime import datetime, timezone

import boto3
import requests
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
logger.setLevel(logging.DEBUG)  # Set logging level as needed (DEBUG, INFO, WARNING, ERROR, CRITICAL)

# Configure logging handler (e.g., console handler for demonstration)
console_handler = logging.StreamHandler()
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

app = Flask(__name__)
app.secret_key = 'secret_key'  # Necessary for form submission, even if not using session state.

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DB_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Models
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

# Directory to store product images
OUTPUT_DIR = "./static/product_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_shop_products():
    """
    Fetch all products from the Medusa store using pagination.
    """
    headers = {"x-publishable-api-key": PUBLISHABLE_KEY}
    products = []
    offset = 0
    limit = 50  # Medusa's default or maximum batch size

    while True:
        url = f"{MEDUSA_API_URL}?offset={offset}&limit={limit}"
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            batch = response.json().get("products", [])
            if not batch:
                # No more products to fetch
                break
            products.extend(batch)
            offset += limit  # Move to the next batch
        except requests.RequestException as e:
            logger.error(f"Error fetching products: {e}")
            break

    logger.debug(f"Total products fetched: {len(products)}")
    return products


def get_product_from_db():
    """
    Retrieve the next product that is either pending or processing.
    Prioritize pending products first, followed by processing.
    """
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
    """
    Display the current pending product and its images. If none pending, all are processed.
    """
    product_entry = get_product_from_db()
    if not product_entry:
        return render_template(
            "index.html"
        )

    # Mark the product as processing (optional step)
    product_entry.status = 'processing'
    db.session.commit()

    # Fetch all products from the store to get images
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
    return render_template(
        "current_images.html",
        product=product,
        existing_images=existing_images
    )

@app.route("/change-images", methods=["POST"])
def change_images():
    """
    Handle the decision to change images or skip the product.
    Include existing images and ensure a total of 15 images.
    """
    decision = request.form.get("decision")

    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    # Fetch product data again
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
        # Skip the product
        product_entry.status = 'skipped'
        product_entry.processed_at = datetime.now(timezone.utc)
        db.session.commit()
        return redirect(url_for("index"))

    # Proceed with downloading new images
    existing_images = product["images"]
    for image_data in existing_images:
        image_url = image_data.get("url")
        if not image_url:
            continue

        image_name = image_url.split("/")[-1]
        existing_image_path = os.path.join(product_dir, image_name)

        # Download and save existing images to the directory
        try:
            response = requests.get(image_url, stream=True)
            response.raise_for_status()
            with open(existing_image_path, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
        except requests.RequestException as e:
            print(f"Error downloading existing image {image_url}: {e}")

    # Count current images
    current_images_count = len([f for f in os.listdir(product_dir) if f.lower().endswith(('jpg', 'jpeg', 'png'))])

    # Ensure total of 15 images
    images_to_download = max(0, 15 - current_images_count)
    if images_to_download > 0:
        download_images(product_name, product_dir, max_num=images_to_download)

    return redirect(url_for("validate_images"))

@app.route("/validate-images")
def validate_images():
    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)
    images = [f for f in os.listdir(product_dir) if f.lower().endswith(('jpg', 'jpeg', 'png'))]

    # Fetch product to display product info
    all_products = fetch_shop_products()
    product = next((p for p in all_products if p["id"] == product_entry.product_id), None)

    return render_template(
        "validate_images.html",
        product=product,
        images=images
    )

@app.route("/validate", methods=["POST"])
def validate():
    product_entry = ProductProgress.query.filter_by(status='processing').first()
    if not product_entry:
        return "No product currently in processing state."

    all_products = fetch_shop_products()
    product = next((p for p in all_products if p["id"] == product_entry.product_id), None)
    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(OUTPUT_DIR, product_name)
    product_id = product_entry.product_id

    selected_images = request.form.getlist("images")

    processed_images = []
    for i, image in enumerate(selected_images):
        old_path = os.path.join(product_dir, image)
        new_name = f"{product_id}-image{i + 1}.png"
        new_path = os.path.join(product_dir, new_name)
        resize_and_center_image(old_path, new_path)
        processed_images.append(new_name)

    # Remove unselected images
    for image in os.listdir(product_dir):
        if image not in processed_images:
            os.remove(os.path.join(product_dir, image))

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
        thumbnail_path = os.path.join(product_dir, f"{product_id}-thumbnail.png")
        shutil.copy(original_path, thumbnail_path)

    # Mark product as done
    product_entry.status = 'done'
    product_entry.processed_at = datetime.now(timezone.utc)
    db.session.commit()

    # Upload images to S3
    uploaded_files = upload_images_to_s3(product_dir)

    # Remove the local product_dir
    shutil.rmtree(product_dir, ignore_errors=True)

    # Build the array of image objects for Medusa
    image_urls = [
        {"url": f"{S3_FILE_URL}/{fname}"}
        for fname in uploaded_files
    ]

    print(image_urls)

    # Now update the product images in Medusa via Admin API
    update_medusa_product_images(product_id, image_urls)

    return redirect(url_for("index"))

@app.route("/restart", methods=["POST"])
def restart():
    """
    This could reset all products to pending if desired.
    """
    ProductProgress.query.update({ProductProgress.status: 'pending', ProductProgress.processed_at: None})
    db.session.commit()
    return redirect(url_for("index"))

def download_images(query, output_dir, max_num=10):
    """
    Download a specific number of images for a product with a preference for white backgrounds.
    """
    google_crawler = GoogleImageCrawler(storage={'root_dir': output_dir})
    filtered_query = f"{query} on white background"
    google_crawler.crawl(keyword=filtered_query, max_num=max_num)

def resize_and_center_image(input_path, output_path):
    """
    Resize the image to fit within 800x800 canvas while maintaining aspect ratio,
    and ensure the background is explicitly white.
    """
    with Image.open(input_path) as img:
        # Convert the image to RGBA to handle transparency
        img = img.convert("RGBA")

        # Create a white background
        white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(white_background, img)

        # Convert back to RGB for non-transparent saving
        img = img.convert("RGB")

        # Determine new dimensions to fit within 800x800
        original_width, original_height = img.size
        if original_width > original_height:
            new_width = 800
            new_height = int(800 * (original_height / original_width))
        else:
            new_height = 800
            new_width = int(800 * (original_width / original_height))

        # Resize the image
        resized_img = img.resize((new_width, new_height), Image.LANCZOS)

        # Create a 800x800 white canvas
        canvas = Image.new("RGB", (800, 800), (255, 255, 255))  # Explicit white

        # Calculate position to center the resized image
        x_offset = (800 - new_width) // 2
        y_offset = (800 - new_height) // 2

        # Paste the resized image onto the white canvas
        canvas.paste(resized_img, (x_offset, y_offset))

        # Save the processed image
        canvas.save(output_path, format="PNG", quality=100)

def upload_images_to_s3(product_dir):
    uploaded_files = []
    for filename in os.listdir(product_dir):
        if filename.lower().endswith(('jpg', 'jpeg', 'png')):
            file_path = os.path.join(product_dir, filename)
            try:
                s3_client.upload_file(
                    Filename=file_path,
                    Bucket=S3_BUCKET,
                    Key=filename,
                    ExtraArgs={
                        'ContentType': 'image/png',
                        'ACL': 'public-read'
                    }
                )
                uploaded_files.append(filename)
                print(f"Uploaded {filename} to S3 bucket {S3_BUCKET}")
            except boto3.exceptions.S3UploadFailedError as e:
                logger.error(f"Upload failed for {filename}: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error during S3 upload for {filename}: {e}")

    return uploaded_files


def update_medusa_product_images(product_id, image_urls):
    headers = {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "Content-Type": "application/json"
    }

    # Find the thumbnail URL or default to the first image in the list
    thumbnail_url = next((image['url'] for image in image_urls if image['url'].endswith('thumbnail.png')), None)
    if not thumbnail_url:
        if not image_urls:
            raise ValueError("No images provided in the image URLs list.")
        thumbnail_url = image_urls[0]['url']  # Use the first image if no thumbnail is found

    data = {
        "thumbnail": thumbnail_url,
        "images": image_urls
    }
    url = f"{MEDUSA_ADMIN_URL}/admin/products/{product_id}"

    logger.debug(f"Updating product images at {url} with data: {data}")
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
