# utils.py
import logging
import os
import tempfile

import boto3
import requests
from PIL import Image
from icrawler.builtin import GoogleImageCrawler

from app_config import Config


# Setup logger
def setup_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Adjust as needed

    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger

logger = setup_logger(__name__)

# Initialize S3 client once
s3_client = boto3.client(
    's3',
    aws_access_key_id=Config.S3_ACCESS_KEY_ID,
    aws_secret_access_key=Config.S3_SECRET_ACCESS_KEY,
    region_name=Config.S3_REGION,
    endpoint_url=Config.S3_ENDPOINT
)

def fetch_shop_products():
    headers = {"x-publishable-api-key": Config.PUBLISHABLE_KEY}
    products = []
    offset = 0
    limit = 50

    while True:
        url = f"{Config.MEDUSA_API_URL}?offset={offset}&limit={limit}"
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
                        Bucket=Config.S3_BUCKET,
                        Key=s3_key,
                        ExtraArgs={
                            'ContentType': 'image/webp',
                            'ACL': 'public-read'
                        }
                    )
                    uploaded_files.append(s3_key)
                    logger.info(f"Uploaded {s3_key} to S3 bucket {Config.S3_BUCKET}")
                except boto3.exceptions.S3UploadFailedError as e:
                    logger.error(f"Upload failed for {s3_key}: {e}")
                except Exception as e:
                    logger.exception(f"Unexpected error during S3 upload for {s3_key}: {e}")
    return uploaded_files

def update_medusa_product_images(product_id, image_urls, admin_token):
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }

    thumbnail_url = next((image['url'] for image in image_urls if image['url'].endswith('thumbnail.webp')), None)
    if not thumbnail_url:
        if not image_urls:
            raise ValueError("No images provided in the image URLs list.")
        thumbnail_url = image_urls[0]['url']


    # TODO: only set status to published when the admin has approved the product

    data = {
        "thumbnail": thumbnail_url,
        "images": image_urls,
        "status": "published"
    }
    url = f"{Config.MEDUSA_ADMIN_URL}/admin/products/{product_id}"

    logger.debug(f"Updating product images at {url} with data: {data}")
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    logger.info(f"Updated product {product_id} images in Medusa.")
    return response.json()

def get_jwt_token(email, password):
    url = f"{Config.MEDUSA_ADMIN_URL}/auth/user/emailpass"
    payload = {"email": email, "password": password}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()["token"]

def download_images(query, product_dir, max_num=10, domain=None, product_id=None):
    """
    Download images based on a query and optional domain into the specified product directory.
    Converts downloaded images to WEBP format and renames them according to product_id and domain.

    Parameters:
        query (str): The search query for images.
        product_dir (str): The directory where images will be saved.
        max_num (int): The maximum number of images to download.
        domain (str, optional): The domain to restrict the search. If 'google.com', do not append 'site:google.com'.
        product_id (str, optional): The ID of the product, used for renaming images.

    Returns:
        list: A list of final image filenames in WEBP format.
    """
    try:
        # Ensure the product directory exists
        os.makedirs(product_dir, exist_ok=True)

        logger.debug(f"Ensured product directory exists: {product_dir}")

        final_names = []  # List to store all final image filenames

        # Step 1: Fetch and Download Existing Images from Medusa
        if product_id:
            headers = {"x-publishable-api-key": Config.PUBLISHABLE_KEY}
            medusa_product_url = f"{Config.MEDUSA_API_URL}/{product_id}"

            try:
                logger.debug(f"Fetching product data from Medusa: {medusa_product_url}")
                response = requests.get(medusa_product_url, headers=headers)
                response.raise_for_status()
                product_data = response.json()
                product = product_data.get('product')  # Adjust based on Medusa API response format

                if not product:
                    logger.error(f"No product data found in Medusa response for product ID: {product_id}")
                else:
                    existing_images = product.get('images', [])
                    logger.info(f"Found {len(existing_images)} existing images for product ID: {product_id}.")

                    for idx, image_data in enumerate(existing_images, start=1):
                        image_url = image_data.get("url")
                        if not image_url:
                            logger.warning(f"No URL found for image data: {image_data}")
                            continue
                        try:
                            logger.debug(f"Downloading existing image {idx} from URL: {image_url}")
                            img_response = requests.get(image_url, stream=True)
                            img_response.raise_for_status()
                            temp_path = os.path.join(product_dir, f"temp_existing_{idx}.jpg")

                            with open(temp_path, "wb") as file:
                                for chunk in img_response.iter_content(1024):
                                    if chunk:
                                        file.write(chunk)
                            logger.debug(f"Downloaded existing image to temporary path: {temp_path}")

                            # Convert to WebP format
                            webp_path = convert_to_webp(temp_path)
                            logger.debug(f"Converted existing image to WebP: {webp_path}")

                            # Rename to product_id-existing-{idx}.webp
                            new_name = f"{product_id}-existing-{idx}.webp"
                            new_path = os.path.join(product_dir, new_name)
                            os.rename(webp_path, new_path)
                            logger.debug(f"Renamed and moved existing image to: {new_path}")

                            # Remove the temporary JPEG file
                            os.remove(temp_path)
                            logger.debug(f"Removed temporary file: {temp_path}")

                            # Append the new name to final_names
                            final_names.append(new_name)
                        except requests.RequestException as e:
                            logger.error(f"Error downloading existing image {image_url}: {e}")
                        except Exception as e:
                            logger.error(f"Error processing existing image {image_url}: {e}")
            except requests.RequestException as e:
                logger.error(f"Error fetching product {product_id} from Medusa: {e}")
            except Exception as e:
                logger.error(f"Unexpected error fetching or processing existing images: {e}")
        else:
            logger.warning("Product ID not provided. Skipping download of existing images from Medusa.")

        # List existing files before crawling to identify new downloads
        existing_files = set(os.listdir(product_dir))

        # Initialize the Google Image Crawler
        google_crawler = GoogleImageCrawler(storage={'root_dir': product_dir})

        # Construct the filtered query based on the domain
        filtered_query = query
        if domain:
            if domain.lower() != "google.com":
                filtered_query += f" site:{domain}"
                logger.debug(f"Appended 'site:{domain}' to the query.")
            else:
                logger.debug(f"Domain is 'google.com'; not appending 'site:{domain}' to the query.")

        logger.debug(f"Crawling with query: '{filtered_query}' | Saving to: '{product_dir}' | Max images: {max_num}")

        # Perform the image crawl
        google_crawler.crawl(keyword=filtered_query, max_num=max_num)

        # Identify new files downloaded during this crawl
        new_files = [f for f in os.listdir(product_dir) if f not in existing_files]
        logger.debug(f"New files downloaded: {new_files}")

        # Prepare to convert and rename images
        idx = 1

        # Determine the source name for renaming
        if domain and domain.lower() != "google.com":
            source_name = domain.replace('.', '_')
        else:
            source_name = "google"

        logger.debug(f"Source name for renaming: '{source_name}'")

        # Process each new file
        for f in new_files:
            fpath = os.path.join(product_dir, f)
            if os.path.isfile(fpath):
                if not fpath.lower().endswith('.webp'):
                    try:
                        # Convert the image to WEBP format
                        webp_path = convert_to_webp(fpath)
                        logger.debug(f"Converted '{fpath}' to WEBP format at '{webp_path}'.")

                        # Construct the new filename
                        new_name = f"{product_id}-{source_name}-{idx}.webp"
                        new_path = os.path.join(product_dir, new_name)

                        # Rename the converted WEBP image
                        os.rename(webp_path, new_path)
                        logger.debug(f"Renamed and moved image to '{new_path}'.")

                        final_names.append(new_name)
                        idx += 1

                        # Optionally, remove the original file if convert_to_webp doesn't do so
                        if os.path.exists(fpath):
                            os.remove(fpath)
                            logger.debug(f"Removed original file '{fpath}'.")
                    except Exception as e:
                        logger.error(f"Error converting '{fpath}' to WEBP: {e}")
                else:
                    # If the file is already in WEBP format, just rename it
                    try:
                        new_name = f"{product_id}-{source_name}-{idx}.webp"
                        new_path = os.path.join(product_dir, new_name)
                        os.rename(fpath, new_path)
                        logger.debug(f"Renamed existing WEBP image to '{new_path}'.")

                        final_names.append(new_name)
                        idx += 1
                    except Exception as e:
                        logger.error(f"Error renaming '{fpath}' to '{new_name}': {e}")

        logger.info(f"Downloaded and processed {len(final_names)} images for query '{filtered_query}'.")
        return final_names

    except Exception as e:
        logger.error(f"Unexpected error in download_images: {e}")
        return []

def fetch_images_from_own_api(product_id, product_dir):
    """
    Fetch images for a product from the own imagineshop.co.za API.

    Parameters:
        product_id (str): The ID of the product.
        product_dir (str): The directory where images will be saved.

    Returns:
        list: A list of image filenames in WEBP format.
    """
    headers = {"x-publishable-api-key": Config.PUBLISHABLE_KEY}
    images_filenames = []

    try:
        url = f"{Config.MEDUSA_API_URL}/products/{product_id}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        product_data = response.json()
        product = product_data.get('product')  # Adjust based on Medusa API response format

        if not product:
            logger.error(f"No product data found in Medusa response for product ID: {product_id}")
            return []

        existing_images = product.get('images', [])
        logger.info(f"Found {len(existing_images)} existing images for product ID: {product_id} from own API.")

        for idx, image_data in enumerate(existing_images, start=1):
            image_url = image_data.get("url")
            if not image_url:
                logger.warning(f"No URL found for image data: {image_data}")
                continue
            try:
                logger.debug(f"Downloading image {idx} from URL: {image_url}")
                img_response = requests.get(image_url, stream=True)
                img_response.raise_for_status()
                temp_path = os.path.join(product_dir, f"temp_existing_{idx}.jpg")

                with open(temp_path, "wb") as file:
                    for chunk in img_response.iter_content(1024):
                        if chunk:
                            file.write(chunk)
                logger.debug(f"Downloaded image to temporary path: {temp_path}")

                # Convert to WebP format
                webp_path = convert_to_webp(temp_path)
                logger.debug(f"Converted image to WebP: {webp_path}")

                # Rename to product_id-own-{idx}.webp
                new_name = f"{product_id}-own-{idx}.webp"
                new_path = os.path.join(product_dir, new_name)
                os.rename(webp_path, new_path)
                logger.debug(f"Renamed and moved image to: {new_path}")

                # Remove the temporary JPEG file
                os.remove(temp_path)
                logger.debug(f"Removed temporary file: {temp_path}")

                # Append the new name to images_filenames
                images_filenames.append(new_name)
            except requests.RequestException as e:
                logger.error(f"Error downloading image {image_url}: {e}")
            except Exception as e:
                logger.error(f"Error processing image {image_url}: {e}")

        return images_filenames

    except requests.RequestException as e:
        logger.error(f"Error fetching product {product_id} from Medusa: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching or processing images from own API: {e}")
        return []

def search_ecommerce_images(query, product_dir, product_id, max_images=10):
    south_african_stores = [
        'imagineshop.co.za', 'google.com', 'takealot.com', 'incredible.co.za',
        'makro.co.za', 'game.co.za', 'hificorp.co.za', 'firstshop.co.za'
    ]
    ecommerce_results = {}

    for store in south_african_stores:
        try:
            if store.lower() == 'imagineshop.co.za':
                # Handle imagineshop.co.za separately
                images = fetch_images_from_own_api(product_id, product_dir)
                if images:
                    ecommerce_results[store] = images
                else:
                    logger.warning(f"No images found from own API for store: {store}.")
            else:
                # Use the existing download_images function for other stores
                store_images = download_images(query, product_dir, max_num=max_images, domain=store, product_id=product_id)
                if store_images:
                    ecommerce_results[store] = store_images
                else:
                    logger.warning(f"No images found for store: {store}.")
        except Exception as e:
            logger.error(f"Failed to process images from {store}: {e}")

    return ecommerce_results