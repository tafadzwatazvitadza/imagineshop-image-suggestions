# routes/product.py
from flask import Blueprint, render_template, redirect, url_for, request, flash
from sqlalchemy import case, and_

from forms import LoadProductsForm
from models import db, ProductProgress
from tasks import process_product_images
from utils import fetch_shop_products
from flask_login import current_user, login_required

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

    # Define priority using SQLAlchemy's case
    priority = case(
        (ProductProgress.user_id == user.id, 1),  # Products assigned to me
        (ProductProgress.status == 'processing', 2),  # Then processing
        ((ProductProgress.status == 'pending') & (ProductProgress.user_id == None), 3),  # Then pending, unassigned
        ((ProductProgress.status == 'processing') & (ProductProgress.user_id != user.id), 4),
        # Then processing by others
    )

    products = ProductProgress.query.filter(
        and_(
            ProductProgress.status != 'done',
            ProductProgress.status != 'skipped'
        )
    ).order_by(priority).paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "products.html",
        products=products,
        load_products_form=load_products_form,
        process_products_form=process_products_form,
    )



@product_bp.route("/load_products", methods=["POST"])
def load_products():
    if not current_user:
        return redirect(url_for("auth.login"))

    products = fetch_shop_products()
    new_entries = []
    for prod in products:
        product_id = prod.get("id")
        title = prod.get("title", "unknown")
        handle = prod.get("handle", "unknown")
        thumbnail = prod.get("thumbnail", "unknown")
        description = prod.get("description", "unknown")
        if not ProductProgress.query.filter_by(product_id=product_id).first():
            new_entries.append(ProductProgress(product_id=product_id, title=title, handle=handle, thumbnail=thumbnail, description=description, status='pending'))
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

