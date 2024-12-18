# routes/auth.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required
from models import db, User
from forms import LoginForm, RegistrationForm

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    login_form = LoginForm()

    if request.method == 'POST' and login_form.validate_on_submit():
        user = User.query.filter_by(email=login_form.email.data).first()

        if user and user.verify_password(login_form.password.data):
            login_user(user)
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('product.list_products'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', login_form=login_form)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    registration_form = RegistrationForm()

    if request.method == 'POST' and registration_form.validate_on_submit():
        new_user = User(
            username=registration_form.username.data,
            email=registration_form.email.data
        )
        new_user.password = registration_form.password.data  # Uses the password setter

        db.session.add(new_user)
        db.session.commit()
        flash('Your account has been created! You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html', registration_form=registration_form)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
