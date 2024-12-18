# routes/image.py
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import current_user, login_required

import forms
from forms import DefaultForm
from models import db, ProductProgress
from utils import (
    validate_image_dimensions,
    resize_and_center_image,
    search_ecommerce_images,
    upload_images_to_s3,
    update_medusa_product_images,
    get_jwt_token
)

from datetime import datetime, timezone
import os
import shutil
import logging
from app_config import Config  # Import Config for access to environment variables

# Initialize the Blueprint
image_bp = Blueprint('image', __name__)

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set appropriate logging level
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
)
handler.setFormatter(formatter)
logger.addHandler(handler)

@image_bp.route("/validate-images/<string:product_id>", methods=["GET", "POST"])
def validate_images(product_id):
    default_form = DefaultForm()
    user = current_user
    if not user:
        flash("Please log in to continue.", "warning")
        return redirect(url_for("auth.login"))

    # Fetch the specific product assigned to the current user
    product_entry = ProductProgress.query.filter_by(
        product_id=product_id,
        status='processing',
        user_id=user.id
    ).first()

    if not product_entry:
        flash("No product currently in processing state for you.", "error")
        return redirect(url_for("product.list_products"))

    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join("static", "product_images", product_name)
    os.makedirs(product_dir, exist_ok=True)

    if request.method == "POST":
        # Handle form submission for validating images
        selected_images = request.form.getlist("images")

        if not selected_images:
            flash("No images selected. Please select at least one image.", "error")
            return redirect(url_for("image.validate_images", product_id=product_id))

        # Remove unselected images immediately
        for image in os.listdir(product_dir):
            if image not in selected_images:
                image_path = os.path.join(product_dir, image)
                try:
                    os.remove(image_path)
                    logger.debug(f"Deleted unselected image: {image_path}")
                except Exception as e:
                    logger.error(f"Failed to delete unselected image {image_path}: {e}")
                    flash(f"Failed to delete some unselected images: {image}", "warning")

        # Redirect to the next step (confirm_image_selection)
        return redirect(url_for("image.confirm_image_selection", product_id=product_id))

    # If GET request, fetch and display images for validation
    all_images = [f for f in os.listdir(product_dir) if f.lower().endswith(('webp', 'jpg', 'jpeg', 'png'))]
    validated_local_images = []
    for image in all_images:
        image_path = os.path.join(product_dir, image)
        is_valid, width, height = validate_image_dimensions(image_path)
        validated_local_images.append({
            "name": image,
            "width": width,
            "height": height,
            "status": "green" if is_valid else "red"
        })

    # Categorize local images into types
    google_images = [img for img in validated_local_images if 'google' in img['name'].lower()]
    existing_images = [
        img for img in validated_local_images
        if '-existing-' in img['name'].lower()
    ]
    ecommerce_images = {}  # Ensure it's a dictionary
    ecommerce_results = search_ecommerce_images(product_entry.title, product_dir, product_entry.product_id)
    for store, images in ecommerce_results.items():
        ecommerce_images[store] = []
        for image in images:
            image_path = os.path.join(product_dir, image)
            is_valid, width, height = validate_image_dimensions(image_path)
            ecommerce_images[store].append({
                "name": image,
                "width": width,
                "height": height,
                "status": "green" if is_valid else "red"
            })

    return render_template(
        "validate_images.html",
        product=product_entry,
        google_images=google_images,
        ecommerce_images=ecommerce_images,
        existing_images=existing_images,
        form=default_form
    )

@image_bp.route("/confirm-image-selection/<string:product_id>", methods=["POST", "GET"])
@login_required  # Ensures the user is logged in
def confirm_image_selection(product_id):
    """
    Handle the confirmation of image selection for a given product.
    Supports both GET and POST methods.
    """

    # Fetch the product based on product_id and current_user
    product_entry = ProductProgress.query.filter_by(
        product_id=product_id,
    ).first()

    if not product_entry:
        flash("No product currently in processing state for you.", "error")
        return redirect(url_for("product.list_products"))

    # Prepare product directory path
    product_name = product_entry.title.replace(" ", "_").lower()
    product_dir = os.path.join(current_app.root_path, "static", "product_images", product_name)

    if request.method == "GET":
        return handle_get_request(product_dir, product_entry)

    # POST: Handle thumbnail selection and finalize process
    return handle_post_request(product_dir, product_entry)


def handle_get_request(product_dir, product_entry):
    """
    Handle GET requests to display selected images for confirmation.
    """
    if not os.path.exists(product_dir):
        flash("No images found for the selected product.", "error")
        return redirect(url_for("product.list_products"))

    images = [
        f for f in os.listdir(product_dir)
        if f.lower().endswith(('webp', 'jpg', 'jpeg', 'png'))
    ]

    if not images:
        flash("No images available after validation.", "error")
        return redirect(url_for("product.list_products"))

    return render_template(
        "confirm_image_selection.html",
        images=images,
        product_id=product_entry.product_id,
        product_name=product_entry.title,
    )


def handle_post_request(product_dir, product_entry):
    """
    Handle POST requests to process all images in the product folder, set a thumbnail, upload to S3,
    update Medusa, and mark the product as done.
    """
    # Get the selected thumbnail from form data
    thumbnail_image = request.form.get("thumbnail")

    print(thumbnail_image)

    # Validate that a thumbnail image is selected
    if not thumbnail_image:
        flash("Please select a thumbnail image.", "error")
        return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))

    try:
        # Process all images in the product directory
        processed_images = []
        thumbnail_new_name = f"{product_entry.product_id}-thumbnail.webp"

        for i, image_name in enumerate(os.listdir(product_dir), start=1):
            image_path = os.path.join(product_dir, image_name)
            if os.path.isfile(image_path) and image_name.lower().endswith(('webp', 'jpg', 'jpeg', 'png')):
                # Check if the current image is the selected thumbnail
                if image_name == thumbnail_image:
                    new_image_name = thumbnail_new_name  # Rename the thumbnail image
                else:
                    new_image_name = f"{product_entry.product_id}-image{i}.webp"

                new_image_path = os.path.join(product_dir, new_image_name)
                resize_and_center_image(image_path, new_image_path)
                processed_images.append(new_image_name)

                # Remove the original image
                os.remove(image_path)
                logger.debug(f"Processed and renamed image: {image_name} -> {new_image_name}")

        print(processed_images)

        # Validate that the thumbnail exists among processed images
        if thumbnail_new_name not in processed_images:
            flash("Selected thumbnail image is not valid or missing after processing.", "error")
            return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))

        # Set the thumbnail
        if not set_thumbnail(product_dir, thumbnail_new_name, product_entry.product_id):
            # Thumbnail setting failure
            return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))

        # Upload processed images to S3
        uploaded_files = upload_images_to_s3_wrapper(product_dir)
        if not uploaded_files:
            # Handle S3 upload failure
            return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))

        # Remove the local product directory after successful upload
        remove_local_directory(product_dir)

        # Prepare URLs for Medusa
        image_urls = prepare_image_urls(uploaded_files)
        print(image_urls)

        # Update product images in Medusa
        if not update_medusa_images(product_entry.product_id, image_urls):
            # Handle Medusa update failure
            return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))

        # Mark the product as done
        if not mark_product_as_done(product_entry):
            # Handle database update failure
            return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))

        # Success message
        flash(
            f"Product '{product_entry.title}' has been updated successfully. "
            f"<br>"
            f"<a target='_blank' class='text-blue-400' href='https://www.imagineshop.co.za/za/products/{product_entry.handle}'>View In Shop</a>",
            "success"
        )
        return redirect(url_for("product.list_products"))

    except Exception as e:
        # General error handling
        logger.error(f"Error during image processing: {e}")
        flash("An error occurred while processing the images. Please try again.", "error")
        return redirect(url_for("image.confirm_image_selection", product_id=product_entry.product_id))



def process_selected_images(product_dir, selected_images, product_id):
    """
    Rename and resize selected images.
    """
    renamed_images = []
    for i, image in enumerate(selected_images, start=1):
        old_path = os.path.join(product_dir, image)
        new_name = f"{product_id}-image{i}.webp"
        new_path = os.path.join(product_dir, new_name)
        try:
            resize_and_center_image(old_path, new_path)
            renamed_images.append(new_name)
            logger.debug(f"Resized and renamed image from {old_path} to {new_path}")
        except Exception as e:
            logger.error(f"Failed to resize and rename image {old_path}: {e}")
            flash(f"Failed to process image: {image}", "error")
            return False

    # Remove original selected images after renaming
    for image in selected_images:
        image_path = os.path.join(product_dir, image)
        try:
            os.remove(image_path)
            logger.debug(f"Deleted original selected image: {image_path}")
        except Exception as e:
            logger.error(f"Failed to delete original image {image_path}: {e}")

    return True


def set_thumbnail(product_dir, thumbnail_name, product_id):
    """
    Ensure the selected thumbnail has the correct name and is processed.
    """
    thumbnail_path = os.path.join(product_dir, thumbnail_name)
    target_path = os.path.join(product_dir, f"{product_id}-thumbnail.webp")

    # Skip renaming if the file is already correctly named
    if thumbnail_path == target_path:
        logger.debug(f"Thumbnail already set: {thumbnail_path}")
        return True

    # Attempt to rename the file
    try:
        os.rename(thumbnail_path, target_path)
        logger.debug(f"Thumbnail renamed: {thumbnail_path} -> {target_path}")
        return True
    except Exception as e:
        logger.error(f"Error setting thumbnail: {e}")
        return False



def upload_images_to_s3_wrapper(product_dir):
    """
    Upload images to S3 and handle exceptions.
    """
    try:
        uploaded_files = upload_images_to_s3(product_dir)
        logger.debug(f"Uploaded files to S3: {uploaded_files}")
        return uploaded_files
    except Exception as e:
        logger.error(f"Failed to upload images to S3: {e}")
        flash("Failed to upload images to S3.", "error")
        return None


def remove_local_directory(product_dir):
    """
    Remove the local product directory after successful upload.
    """
    try:
        shutil.rmtree(product_dir, ignore_errors=True)
        logger.debug(f"Removed local directory {product_dir}")
    except Exception as e:
        logger.error(f"Failed to remove local directory {product_dir}: {e}")
        # Depending on your application's requirements, you might want to flash a message or handle this differently


def prepare_image_urls(uploaded_files):
    """
    Prepare the list of image URLs for Medusa.
    """
    return [{"url": f"{Config.S3_FILE_URL}/{fname}"} for fname in uploaded_files]


def update_medusa_images(product_id, image_urls):
    """
    Update product images in Medusa via Admin API.
    """
    try:
        admin_token = get_jwt_token(Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        update_medusa_product_images(product_id, image_urls, admin_token)
        logger.debug(f"Updated Medusa product images for {product_id} with URLs: {image_urls}")
        return True
    except Exception as e:
        logger.error(f"Failed to update Medusa product images: {e}")
        flash("Failed to update product images in Medusa.", "error")
        return False


def mark_product_as_done(product_entry):
    """
    Mark the product as done and commit the changes to the database.
    """
    product_entry.status = 'done'
    product_entry.processed_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        logger.debug(f"Marked product {product_entry.product_id} as done.")
        return True
    except Exception as e:
        logger.error(f"Failed to commit changes to the database: {e}")
        flash("Failed to update product status.", "error")
        return False