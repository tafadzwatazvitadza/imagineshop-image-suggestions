import mimetypes
from datetime import datetime

import requests
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename

from app_config import Config
from forms import CreateBrandForm
from utils import s3_client

# Initialize the Blueprint
brands_bp = Blueprint('brands', __name__)


def upload_to_s3(file, filename):
    # Determine the content type based on the file extension
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or 'application/octet-stream'  # Default if type cannot be determined

    s3_client.upload_fileobj(
        file,
        Config.S3_BUCKET,
        filename,
        ExtraArgs={
            "ContentType": content_type,         # Set MIME type based on file extension
            "ContentDisposition": "inline"       # Display in browser instead of downloading
        }
    )
    return f"https://{Config.S3_BUCKET}.s3.{Config.S3_REGION}.amazonaws.com/{filename}"


# Helper to get Medusa token
def get_medusa_token():
    auth_url = f"{Config.MEDUSA_ADMIN_URL}/auth/user/emailpass"
    auth_body = {
        "email": Config.ADMIN_EMAIL,
        "password": Config.ADMIN_PASSWORD
    }
    try:
        auth_response = requests.post(auth_url, json=auth_body)
        auth_response.raise_for_status()
        token = auth_response.json().get("token")
        if not token:
            raise Exception("Authentication token not found in response.")
        return token
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to authenticate with Medusa: {e}")

# Route to view all brands
@brands_bp.route("/brands", methods=["GET"])
def view_all_brands():
    try:
        # Get Medusa token
        token = get_medusa_token()

        # Fetch brands using the token
        headers = {
            "Authorization": f"Bearer {token}"
        }
        response = requests.get(f"{Config.MEDUSA_ADMIN_URL}/admin/brands", headers=headers)
        response.raise_for_status()
        brands = response.json().get('brands', [])
    except Exception as e:
        flash(f"Error fetching brands: {e}", "danger")
        brands = []

    return render_template('brands/brands.html', brands=brands)

# Route to create a new brand
@brands_bp.route("/create-brand", methods=["GET", "POST"])
def create_brand():
    create_brand_form = CreateBrandForm()

    if request.method == 'POST' and create_brand_form.validate_on_submit():
        name = create_brand_form.name.data.strip()
        logo = create_brand_form.logo.data

        if not logo:
            flash("Logo file is required.", "warning")
            return render_template('brands/create_brand.html', create_brand_form=create_brand_form)

        # Secure the filename and handle cases where the extension might be missing
        original_filename = secure_filename(logo.filename)
        if '.' in original_filename:
            extension = original_filename.rsplit('.', 1)[1].lower()
        else:
            extension = 'png'  # Default extension if none provided

        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        filename = f"{secure_filename(name)}_logo_{timestamp}.{extension}"

        try:
            # Upload the logo to S3
            logo_url = upload_to_s3(logo, filename)

            # Get Medusa token
            token = get_medusa_token()

            # Prepare data for Medusa
            medusa_payload = {
                "name": name,
                "logo": logo_url
            }

            # Send the data to the Medusa database
            medusa_url = f"{Config.MEDUSA_ADMIN_URL}/admin/brands"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            medusa_response = requests.post(medusa_url, json=medusa_payload, headers=headers)
            medusa_response.raise_for_status()

            # Flash success message
            flash(f"Brand '{name}' has been created successfully.", "success")

            # Redirect to brands page
            return redirect(url_for('brands.view_all_brands'))

        except requests.exceptions.HTTPError as http_err:
            error_detail = http_err.response.json().get('message', str(http_err))
            flash(f"HTTP error occurred: {error_detail}", "danger")
        except Exception as err:
            flash(f"An error occurred: {err}", "danger")

    # Render HTML form for GET requests or if validation fails
    return render_template('brands/create_brand.html', create_brand_form=create_brand_form)




# import mimetypes
# from datetime import datetime, date
#
# import boto3
# from dotenv import load_dotenv
# from flask import request, jsonify, render_template, redirect, url_for, Blueprint
#
# from app_config import Config
# from forms import CreateBannerForm, CreateBrandForm
# from models import db, Banner
# from utils import s3_client
#
# # Load environment variables
# load_dotenv()
#
# # Initialize the Blueprint
# brands_bp = Blueprint('brands', __name__)
#
# # Helper to upload file to S3
# def upload_to_s3(file, filename):
#     # Determine the content type based on the file extension
#     content_type, _ = mimetypes.guess_type(filename)
#     content_type = content_type or 'application/octet-stream'  # Default if type cannot be determined
#
#     s3_client.upload_fileobj(
#         file,
#         Config.S3_BUCKET,
#         filename,
#         ExtraArgs={
#             "ContentType": content_type,         # Set MIME type based on file extension
#             "ContentDisposition": "inline"       # Display in browser instead of downloading
#         }
#     )
#     return f"https://{Config.S3_BUCKET}.s3.{Config.S3_REGION}.amazonaws.com/{filename}"
#
#
# # Root route to view all banners
# @brands_bp.route("/brands", methods=["GET"])
# def view_all_brands():
#
#     banners = []
#     # get banners from the medusa database
#     return render_template('brands/brands.html', banners=banners)
#
#
# # Route to create a new banner
# @brands_bp.route("/create-banner", methods=["GET", "POST"])
# def create_brand():
#     create_brand_form = CreateBrandForm()
#
#     if request.method == 'POST' and create_brand_form.validate_on_submit():
#
#         name = create_brand_form.name.data,
#         logo = create_brand_form.logo.data
#
#         # upload the logo to s3
#         # logo_url = upload_to_s3(logo, f"{name}_logo.png")
#
#         # retrieve token example
#         #     curl - X
#         #     POST
#         #     'http://localhost:9000/auth/user/emailpass' \
#         #     - H
#         #     'Content-Type: application/json' \
#         #     - -data - raw
#         #     '{
#         #     "email": Config.ADMIN_EMAIL,
#         #     "password": Config.ADMIN_PASSWORD
#         # }'
#
#         # send the data to the medusa database
#         #     curl - X
#         #     POST
#         #     '{Config.MEDUSA_ADMIN_URL}/admin/brands' \
#         #     - H
#         #     'Content-Type: application/json' \
#         #     - H
#         #     'Authorization: Bearer {token}' \
#         #     - -data
#         #     '{
#         #     "name": name
#         #     "logo": logo_url
#         # }'
#
#
#         # set flash message
#         # flash = flash(
#         #     f"Brand '{name}' has been updated successfully.",
#         #     "success"
#         # )
#
#
#         # on success redirect to bannners
#
#         return redirect(url_for('banners.view_all_brands'))
#
#     # Render HTML form for GET requests
#     return render_template('banners/create_brand.html', create_brand_form=create_brand_form)