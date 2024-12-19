# forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.fields.datetime import DateField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError
from models import User

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6, max=60)])
    submit = SubmitField('Login')

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=150)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6, max=60)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already exists. Please choose a different one.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Email already registered. Please log in or use a different email.')

class LoadProductsForm(FlaskForm):
    submit = SubmitField('Submit')

class ProcessProductsForm(FlaskForm):
    submit = SubmitField('Submit')

class DefaultForm(FlaskForm):
    submit = SubmitField('Submit')

class CreateBannerForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(min=1, max=100)])
    image = StringField('Banner Artwork', validators=[])
    collection_path = StringField('Collection Path', validators=[DataRequired(), Length(min=1, max=100)])
    coupon_code = StringField('Coupon Code', validators=[Length(min=0, max=50)])
    expiry_date = DateField('Expiry Date', validators=[])
    submit = SubmitField('Save Changes')
