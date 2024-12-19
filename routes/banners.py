import mimetypes
from datetime import datetime, date

import boto3
from dotenv import load_dotenv
from flask import request, jsonify, render_template, redirect, url_for, Blueprint

from app_config import Config
from forms import CreateBannerForm
from models import db, Banner

# Load environment variables
load_dotenv()

# Initialize the Blueprint
banners_bp = Blueprint('banners', __name__)

s3_client = boto3.client(
    's3',
    aws_access_key_id=Config.S3_ACCESS_KEY_ID,
    aws_secret_access_key=Config.S3_SECRET_ACCESS_KEY,
    region_name=Config.S3_REGION,
    endpoint_url=Config.S3_ENDPOINT
)


# Helper to upload file to S3
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


# Root route to view all banners
@banners_bp.route("/banners", methods=["GET"])
def view_all_banners():
    banners = Banner.query.all()
    create_banner_form = CreateBannerForm()
    return render_template('banners/banners.html', banners=banners, create_banner_form=create_banner_form)


# Route to create a new banner
@banners_bp.route("/create-banner", methods=["GET", "POST"])
def create_banner():
    create_banner_form = CreateBannerForm()

    if request.method == 'POST' and create_banner_form.validate_on_submit():

        title = create_banner_form.title.data,
        collection_path = create_banner_form.collection_path.data,
        coupon_code = create_banner_form.coupon_code.data,
        expiry_date = create_banner_form.expiry_date.data

        # Handle file upload
        image_url = None
        if 'image' in request.files:
            image_file = create_banner_form.image.data
            if image_file.filename:  # Check if filename is present
                filename = f"banners/{image_file.filename}"
                image_url = upload_to_s3(image_file, filename)

        # Create a new Banner entry
        banner = Banner(
            title=title,
            image_url=image_url,
            collection_path=collection_path,
            coupon_code=coupon_code,
            expiry_date=expiry_date
        )
        db.session.add(banner)
        db.session.commit()
        return redirect(url_for('banners.view_all_banners'))

    # Render HTML form for GET requests
    return render_template('banners/create_banner.html', create_banner_form=create_banner_form)


@banners_bp.route("/banners/<int:id>/update", methods=["GET", "POST"])
def update_banner(id):
    banner = Banner.query.get_or_404(id)
    update_banner_form = CreateBannerForm(obj=banner)

    if request.method == 'POST':
        data = request.form
        banner.title = data.get('title', banner.title)
        banner.collection_path = data.get('collection_path', banner.collection_path)
        banner.coupon_code = data.get('coupon_code', banner.coupon_code)

        expiry_date = data.get('expiry_date')
        banner.expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d') if expiry_date else banner.expiry_date

        # Handle file upload
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file.filename:  # Check if filename is present
                filename = f"banners/{image_file.filename}"
                banner.image_url = upload_to_s3(image_file, filename)

        db.session.commit()
        return redirect(url_for('banners.view_all_banners'))

    # Render HTML form for editing
    return render_template('banners/update_banner.html', banner=banner, update_banner_form=update_banner_form)



# Route to delete a banner
@banners_bp.route("/banners/<int:id>/delete", methods=["POST"])
def delete_banner(id):
    if request.form.get('_method') == 'DELETE':
        banner = Banner.query.get_or_404(id)
        db.session.delete(banner)
        db.session.commit()
        return redirect(url_for('banners.view_all_banners'))
    return "Method Not Allowed", 405



# Route to get banners for API consumption
@banners_bp.route("/api/banners", methods=["GET"])
def get_banners():
    # Filter banners to include those without expiry or with today/future expiry date
    today = date.today()
    banners = Banner.query.filter(
        (Banner.expiry_date == None) | (Banner.expiry_date >= today)
    ).all()

    # Prepare banners data for JSON response
    banners_data = [
        {
            "title": banner.title,
            "image_url": banner.image_url,
            "collection_path": banner.collection_path,
            "coupon_code": banner.coupon_code,
            "expiry_date": (
                "Valid until today" if banner.expiry_date == today else
                f"Valid until {banner.expiry_date.strftime('%Y-%m-%d')}"
            ) if banner.expiry_date else None
        }
        for banner in banners
    ]

    # Return JSON response
    return jsonify(banners_data)
