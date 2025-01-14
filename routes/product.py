# routes/product.py
import requests
from flask import Blueprint, flash

from routes.auth import admin_required
from tasks import process_product_images
from utils import fetch_shop_products, get_jwt_token, logger
from app_config import Config

product_bp = Blueprint('product', __name__)



from flask import Blueprint, render_template, redirect, url_for, request
from flask_login import current_user, login_required
from sqlalchemy import case, and_, or_
from forms import LoadProductsForm
from models import db, ProductProgress

product_bp = Blueprint('product', __name__)


@product_bp.route("/")
@login_required
def list_products():
    user = current_user
    load_products_form = LoadProductsForm()
    process_products_form = LoadProductsForm()

    if not user.is_authenticated:
        return redirect(url_for("auth.login"))

    page = request.args.get('page', 1, type=int)
    per_page = 10

    # Base query: exclude 'done' and 'skipped'
    base_query = ProductProgress.query.filter(
        and_(
            ProductProgress.status != 'done',
            ProductProgress.status != 'skipped'
        )
    )

    # Distinguish Admin vs. Worker
    if user.role.lower() == 'admin':
        # Admin sees all in base query
        products_query = base_query

        # Priority for Admin:
        # 1. assigned to me
        # 2. processing
        # 3. pending/unassigned
        # 4. processing but assigned to others (optional)
        priority = case(
            (ProductProgress.user_id == user.id, 1),
            (ProductProgress.status == 'processing', 2),
            ((ProductProgress.status == 'pending') & (ProductProgress.user_id == None), 3),
            ((ProductProgress.status == 'processing') & (ProductProgress.user_id != user.id), 4),
        )

    else:
        # Worker sees tasks assigned to them OR unassigned
        products_query = base_query.filter(
            or_(
                ProductProgress.user_id == user.id,
                ProductProgress.user_id.is_(None)
            )
        )

        # Priority for Worker:
        # 1. assigned to me
        # 2. pending/unassigned
        priority = case(
            (ProductProgress.user_id == user.id, 1),
            ((ProductProgress.status == 'pending') & (ProductProgress.user_id == None), 2),
        )

    # Order by priority and paginate
    products = products_query.order_by(priority).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    return render_template(
        "products.html",
        products=products,
        load_products_form=load_products_form,
        process_products_form=process_products_form,
    )


# @product_bp.route("/")
# @login_required
# def list_products():
#     user = current_user
#     load_products_form = LoadProductsForm()
#     process_products_form = LoadProductsForm()
#
#     if not user.is_authenticated:
#         return redirect(url_for("auth.login"))
#
#     page = request.args.get('page', 1, type=int)
#     per_page = 10
#
#     # Define priority using SQLAlchemy's case
#     priority = case(
#         (ProductProgress.user_id == user.id, 1),  # Products assigned to me
#         (ProductProgress.status == 'processing', 2),  # Then processing
#         ((ProductProgress.status == 'pending') & (ProductProgress.user_id == None), 3),  # Then pending, unassigned
#         ((ProductProgress.status == 'processing') & (ProductProgress.user_id != user.id), 4),
#         # Then processing by others
#     )
#
#     products = ProductProgress.query.filter(
#         and_(
#             ProductProgress.status != 'done',
#             ProductProgress.status != 'skipped'
#         )
#     ).order_by(priority).paginate(page=page, per_page=per_page, error_out=False)
#
#     return render_template(
#         "products.html",
#         products=products,
#         load_products_form=load_products_form,
#         process_products_form=process_products_form,
#     )

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



@product_bp.route("/load_products", methods=["POST"])
def load_products():
    if not current_user:
        return redirect(url_for("auth.login"))

    try:
        # Get Medusa token
        admin_token = get_jwt_token(Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)

        # Fetch products with 'proposed' status and limit to 50
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
        }

        products = []
        offset = 0
        limit = 50

        while True:
            url = f"{Config.MEDUSA_ADMIN_URL}/admin/products?offset={offset}&limit={limit}&status[]=proposed"
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

    except Exception as e:
        flash(f"Error fetching products: {e}", "danger")
        products = []

    new_entries = []
    for prod in products:
        product_id = prod.get("id")
        title = prod.get("title", "unknown")
        handle = prod.get("handle", "unknown")
        thumbnail = prod.get("thumbnail", "unknown")
        description = prod.get("description", "unknown")
        if not ProductProgress.query.filter_by(product_id=product_id).first():
            new_entries.append(ProductProgress(
                product_id=product_id,
                title=title,
                handle=handle,
                thumbnail=thumbnail,
                description=description,
                status='pending'
            ))
    if new_entries:
        db.session.bulk_save_objects(new_entries)
        db.session.commit()
        flash(f"Loaded {len(new_entries)} new products.", "success")
    else:
        flash("No new products found.", "info")
    return redirect(url_for("product.list_products"))



@product_bp.route("/process/<string:product_id>", methods=["POST"])
def process_product(product_id):
    user = current_user
    if not user.is_authenticated:
        return redirect(url_for("auth.login"))

    product_entry = ProductProgress.query.filter_by(product_id=product_id).first_or_404()
    if product_entry.status != 'pending' and product_entry.status != 'processing':
        flash("Product is not in a pending or processing state.", "warning")
        return redirect(url_for("product.list_products"))

    # Assign the product to the current user's ID and set status to 'processing'
    product_entry.user_id = user.id  # Use user.id instead of the entire user object
    product_entry.status = 'processing'
    db.session.commit()

    # Trigger Celery task (if using asynchronous processing)
    process_product_images.delay(product_id)

    flash(f"Started processing product '{product_entry.title}'.", "info")
    return redirect(url_for("image.validate_images", product_id=product_id))



@product_bp.route("/admin_dashboard")
@admin_required
def admin_dashboard():
    # Only admins can see this dashboard
    # ...
    return render_template("admin_dashboard.html")