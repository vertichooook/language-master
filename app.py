import os
from functools import wraps
from flask import Flask, render_template, redirect, url_for, flash, request, session
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from languages import LANGUAGES, get_language_choices  # ← Импортируем языки

app = Flask(__name__)

# --- КОНФИГУРАЦИЯ ---
app.config['SECRET_KEY'] = 'super-secret-key-change-in-production'

adatabase_url = os.getenv('DATABASE_URL')
if database_url:
    # Для PostgreSQL (Render)
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Для локальной разработки (SQLite)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'database.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

try:
    os.makedirs(app.instance_path)
except OSError:
    pass

db = SQLAlchemy(app)


# --- МОДЕЛИ БАЗЫ ДАННЫХ ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    words = db.relationship('Word', backref='owner', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Word(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original = db.Column(db.String(100), nullable=False)
    translation = db.Column(db.String(100), nullable=False)
    source_lang = db.Column(db.String(20), nullable=False)
    target_lang = db.Column(db.String(20), nullable=False)
    definition = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


# --- ФОРМЫ ---

class RegistrationForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Пароль', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Подтвердите пароль', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Зарегистрироваться')


class LoginForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    submit = SubmitField('Войти')


class TranslationForm(FlaskForm):
    word = StringField('Введите слово или фразу', validators=[DataRequired()])
    source_language = SelectField('📥 С языка', choices=get_language_choices())  # ← Динамические языки
    target_language = SelectField('📤 На язык', choices=get_language_choices())  # ← Динамические языки
    submit = SubmitField('🔍 Перевести')


# --- ДЕКОРАТОР ДЛЯ ЗАЩИТЫ МАРШРУТОВ ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('⚠️ Пожалуйста, войдите в аккаунт для доступа к этой странице', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# --- ЛОГИКА ПЕРЕВОДА ---

def get_translation_and_definition(text, source_lang, target_lang):
    """
    Получает перевод и определение через внешние API.
    Поддерживает все языки из languages.py
    """
    # 1. Перевод (MyMemory API - поддерживает 100+ языков)
    lang_pair = f"{source_lang}|{target_lang}"
    translate_url = f"https://api.mymemory.translated.net/get?q={text}&langpair={lang_pair}"

    try:
        trans_response = requests.get(translate_url, timeout=10)
        trans_data = trans_response.json()
        translation = trans_data.get('responseData', {}).get('translatedText', 'Ошибка перевода')
    except Exception:
        translation = "Ошибка сети"

    # 2. Определение (Free Dictionary API - ограниченная поддержка)
    definition = "Определение не найдено"

    # API поддерживает только: en, de, es, fr, it, nl, pl, pt, ru, sv
    supported_def_langs = ['en', 'de', 'es', 'fr', 'it', 'nl', 'pl', 'pt', 'ru', 'sv']

    if target_lang in supported_def_langs:
        definition_url = f"https://api.dictionaryapi.dev/api/v2/entries/{target_lang}/{translation}"
        try:
            def_response = requests.get(definition_url, timeout=5)
            if def_response.status_code == 200:
                data = def_response.json()[0]
                definition = data['meanings'][0]['definitions'][0]['definition']
        except:
            pass

    return translation, definition


# --- МАРШРУТЫ ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        flash('ℹ️ Вы уже вошли в аккаунт', 'info')
        return redirect(url_for('index'))

    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(username=form.username.data).first()
        if existing_user:
            flash('❌ Это имя пользователя уже занято', 'warning')
            return render_template('register.html', form=form)

        existing_email = User.query.filter_by(email=form.email.data).first()
        if existing_email:
            flash('❌ Этот email уже зарегистрирован', 'warning')
            return render_template('register.html', form=form)

        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash('✅ Аккаунт успешно создан! Теперь войдите.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        flash('ℹ️ Вы уже вошли в аккаунт', 'info')
        return redirect(url_for('index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user and user.check_password(form.password.data):
            session['user_id'] = user.id
            session['username'] = user.username
            flash(f'👋 Добро пожаловать, {user.username}!', 'success')
            return redirect(url_for('index'))
        else:
            flash('❌ Неверное имя пользователя или пароль', 'warning')

    return render_template('login.html', form=form)


@app.route('/logout')
def logout():
    session.clear()
    flash('👋 Вы успешно вышли из аккаунта', 'info')
    return redirect(url_for('index'))


@app.route('/translate', methods=['GET', 'POST'])
def translate():
    form = TranslationForm()
    result = None

    if request.method == 'GET':
        form.source_language.data = 'ru'
        form.target_language.data = 'en'

    if form.validate_on_submit():
        original_word = form.word.data
        source_lang = form.source_language.data
        target_lang = form.target_language.data

        translated_text, definition = get_translation_and_definition(original_word, source_lang, target_lang)

        result = {
            'original': original_word,
            'translated': translated_text,
            'definition': definition,
            'source_lang': source_lang,
            'target_lang': target_lang,
            'source_name': LANGUAGES.get(source_lang, source_lang),
            'target_name': LANGUAGES.get(target_lang, target_lang)
        }

    return render_template('translate.html', form=form, result=result, languages=LANGUAGES)


@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    user = User.query.get(session['user_id'])

    if request.method == 'POST':
        original = request.form.get('original')
        translation = request.form.get('translation')
        source_lang = request.form.get('source_lang')
        target_lang = request.form.get('target_lang')
        definition = request.form.get('definition')

        new_word = Word(
            original=original,
            translation=translation,
            source_lang=source_lang,
            target_lang=target_lang,
            definition=definition,
            owner=user
        )
        db.session.add(new_word)
        db.session.commit()
        flash('✅ Слово сохранено в словарь!', 'success')
        return redirect(url_for('dashboard'))

    words = Word.query.filter_by(user_id=user.id).all()
    return render_template('dashboard.html', words=words, username=user.username, languages=LANGUAGES)


@app.route('/profile')
@login_required
def profile():
    user = User.query.get(session['user_id'])
    words_count = Word.query.filter_by(user_id=user.id).count()
    return render_template('profile.html', user=user, words_count=words_count)


@app.route('/delete_word/<int:word_id>')
@login_required
def delete_word(word_id):
    word = Word.query.get_or_404(word_id)
    if word.user_id == session['user_id']:
        db.session.delete(word)
        db.session.commit()
        flash('🗑️ Слово удалено из словаря', 'info')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    # Для локальной разработки
    app.run(debug=True)

# Для Production (Render, PythonAnywhere) добавьте в конец файла:
if os.getenv('RENDER') or os.getenv('PYTHONANYWHERE'):
    with app.app_context():
        db.create_all()