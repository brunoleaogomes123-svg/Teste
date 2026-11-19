import hashlib
import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import subprocess
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from urllib.parse import quote

import click
from flask import Flask, abort, current_app, g, jsonify, request, send_from_directory, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN_LENGTH = 12
RESET_TOKEN_LIFETIME = timedelta(minutes=30)
MAX_CHAT_MESSAGE_LENGTH = 1000


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.email = row["email"]
        self.role = row["role"]
        self.auth_version = row["auth_version"]

    @property
    def is_admin(self):
        return self.role == "admin"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            timeout=10,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    del _error
    db = g.pop("db", None)
    if db is not None:
        db.close()


def initialize_db(app):
    database_path = os.path.abspath(app.config["DATABASE"])
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    with app.app_context():
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                auth_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_admin
                ON users(role) WHERE role = 'admin';
            CREATE TABLE IF NOT EXISTS reset_tokens (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 1000),
                created_at TEXT NOT NULL
            );
            """
        )
        db.commit()


def normalize_email(value):
    return value.strip().lower() if isinstance(value, str) else ""


def valid_username(value):
    return isinstance(value, str) and USERNAME_PATTERN.fullmatch(value) is not None


def valid_email(value):
    return isinstance(value, str) and EMAIL_PATTERN.fullmatch(value) is not None


def json_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Envie os dados no formato JSON.")
    return payload


def send_reset_email(app, recipient, reset_url):
    message = EmailMessage()
    message["Subject"] = "Redefinição de senha - Tuzz Logística"
    message["From"] = app.config["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(
        "Recebemos uma solicitação para redefinir sua senha.\n\n"
        f"Acesse este link nos próximos 30 minutos:\n{reset_url}\n\n"
        "Se você não solicitou a redefinição, ignore esta mensagem."
    )

    with smtplib.SMTP(
        app.config["SMTP_HOST"],
        app.config["SMTP_PORT"],
        timeout=15,
    ) as smtp:
        if app.config["SMTP_USE_TLS"]:
            smtp.starttls(context=ssl.create_default_context())
        if app.config["SMTP_USERNAME"]:
            smtp.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
        smtp.send_message(message)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify(error="Acesso restrito ao administrador."), 403
        return view(*args, **kwargs)

    return wrapped


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get(
            "DATABASE_PATH",
            os.path.join(app.instance_path, "acuracidade.sqlite3"),
        ),
        PHP_EXECUTABLE=os.environ.get("PHP_EXECUTABLE", "php"),
        PHP_EXTENSION_DIR=os.environ.get("PHP_EXTENSION_DIR", ""),
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_FROM=os.environ.get("SMTP_FROM", ""),
        SMTP_USE_TLS=os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        WTF_CSRF_TIME_LIMIT=3600,
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)

    CSRFProtect(app)
    login_manager = LoginManager(app)
    limiter = Limiter(
        get_remote_address,
        app=app,
        storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        default_limits=[],
    )
    app.extensions["auth_limiter"] = limiter
    app.teardown_appcontext(close_db)
    initialize_db(app)

    @login_manager.user_loader
    def load_user(user_id):
        row = get_db().execute(
            "SELECT id, username, email, role, auth_version FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row or session.get("auth_version") != row["auth_version"]:
            return None
        return User(row)

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify(error="Faça login para continuar."), 401

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        del error
        return jsonify(error="A solicitação expirou. Atualize a página e tente novamente."), 400

    @app.errorhandler(400)
    def bad_request(error):
        if request.path.startswith("/api/"):
            return jsonify(error=error.description), 400
        return error

    @app.after_request
    def disable_api_cache(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return send_from_directory(app.root_path, "index.html")

    @app.get("/style.css")
    def stylesheet():
        return send_from_directory(app.root_path, "style.css")

    @app.get("/script.js")
    def javascript():
        return send_from_directory(app.root_path, "script.js")

    @app.get("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(os.path.join(app.root_path, "assets"), filename)

    @app.get("/api/csrf-token")
    def csrf_token():
        return jsonify(csrf_token=generate_csrf())

    @app.get("/api/me")
    def session_user():
        if not current_user.is_authenticated:
            return jsonify(authenticated=False)
        return jsonify(
            authenticated=True,
            id=current_user.id,
            username=current_user.username,
            email=current_user.email,
            is_admin=current_user.is_admin,
        )

    @app.post("/api/login")
    @limiter.limit("10 per minute")
    def login():
        payload = json_payload()
        username = payload.get("username", "")
        password = payload.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            return jsonify(error="Usuário ou senha inválidos."), 401
        row = get_db().execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return jsonify(error="Usuário ou senha inválidos."), 401
        logout_user()
        session.clear()
        user = User(row)
        login_user(user)
        session["auth_version"] = user.auth_version
        return jsonify(
            authenticated=True,
            id=user.id,
            username=user.username,
            email=user.email,
            is_admin=user.is_admin,
            csrf_token=generate_csrf(),
        )

    @app.post("/api/logout")
    @login_required
    def logout():
        logout_user()
        session.clear()
        return jsonify(logged_out=True, csrf_token=generate_csrf())

    @app.get("/api/admin/users")
    @admin_required
    def list_users():
        rows = get_db().execute(
            "SELECT id, username, email, role, created_at FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return jsonify(
            users=[
                {
                    "id": row["id"],
                    "username": row["username"],
                    "email": row["email"],
                    "role": row["role"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        )

    @app.post("/api/admin/users")
    @limiter.limit("30 per hour")
    @admin_required
    def create_user():
        payload = json_payload()
        username = payload.get("username")
        email = normalize_email(payload.get("email"))
        password = payload.get("password")
        if not valid_username(username):
            return jsonify(error="Use um usuário com 3 a 32 letras, números, ponto, hífen ou sublinhado."), 400
        if not valid_email(email):
            return jsonify(error="Informe um e-mail válido."), 400
        if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
            return jsonify(error=f"A senha deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres."), 400

        try:
            cursor = get_db().execute(
                """
                INSERT INTO users (username, email, password_hash, role, created_at)
                VALUES (?, ?, ?, 'user', ?)
                """,
                (
                    username.strip(),
                    email,
                    generate_password_hash(password),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            return jsonify(error="Esse usuário ou e-mail já está cadastrado."), 409
        return jsonify(id=cursor.lastrowid, username=username.strip(), email=email, role="user"), 201

    def chat_php_response(payload):
        environment = os.environ.copy()
        environment["CHAT_DATABASE_PATH"] = os.path.abspath(app.config["DATABASE"])
        php_command = [app.config["PHP_EXECUTABLE"]]
        if app.config["PHP_EXTENSION_DIR"]:
            php_command.extend(
                [
                    "-d",
                    f"extension_dir={app.config['PHP_EXTENSION_DIR']}",
                    "-d",
                    "extension=sqlite3",
                    "-d",
                    "extension=pdo_sqlite",
                ]
            )
        php_command.append(os.path.join(app.root_path, "chat.php"))
        try:
            result = subprocess.run(
                php_command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                check=False,
                encoding="utf-8",
                env=environment,
                timeout=10,
            )
        except FileNotFoundError:
            app.logger.error("PHP CLI não encontrado para atender o chat.")
            return jsonify(error="O PHP CLI é necessário para usar o chat."), 503
        except subprocess.TimeoutExpired:
            app.logger.error("O processamento PHP do chat excedeu o tempo limite.")
            return jsonify(error="O chat demorou demais para responder. Tente novamente."), 504

        if result.returncode != 0:
            app.logger.error("Falha no PHP do chat: %s", result.stderr[-2000:])
            return jsonify(error="Não foi possível processar o chat agora."), 502
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            app.logger.error("O PHP do chat retornou uma resposta inválida.")
            return jsonify(error="O serviço de chat retornou uma resposta inválida."), 502
        if not isinstance(response, dict) or not isinstance(response.get("status"), int):
            app.logger.error("O PHP do chat retornou um formato inválido.")
            return jsonify(error="O serviço de chat retornou uma resposta inválida."), 502
        status = response.pop("status")
        return jsonify(response), status

    @app.get("/api/chat/messages")
    @login_required
    def list_chat_messages():
        after = request.args.get("after")
        if after is not None:
            if not after.isascii() or not after.isdecimal() or len(after) > 19:
                return jsonify(error="O marcador de mensagens é inválido."), 400
            after_id = int(after)
            if after_id > 9223372036854775807:
                return jsonify(error="O marcador de mensagens é inválido."), 400
            after = str(after_id)
        return chat_php_response(
            {
                "action": "list",
                "after": after,
            }
        )

    @app.delete("/api/chat/messages/<int:message_id>")
    @limiter.limit("30 per minute")
    @login_required
    def delete_chat_message(message_id):
        return chat_php_response(
            {
                "action": "delete",
                "message_id": message_id,
                "user_id": int(current_user.id),
                "is_admin": current_user.is_admin,
            }
        )

    @app.post("/api/chat/messages")
    @limiter.limit("30 per minute")
    @login_required
    def send_chat_message():
        payload = json_payload()
        body = payload.get("body")
        if not isinstance(body, str):
            return jsonify(error="Escreva uma mensagem antes de enviar."), 400
        body = body.strip()
        if not body:
            return jsonify(error="A mensagem não pode ficar vazia."), 400
        if len(body) > MAX_CHAT_MESSAGE_LENGTH:
            return jsonify(
                error=f"A mensagem deve ter no máximo {MAX_CHAT_MESSAGE_LENGTH} caracteres."
            ), 400
        return chat_php_response(
            {
                "action": "send",
                "body": body,
                "user_id": int(current_user.id),
            }
        )

    @app.post("/api/password-reset/request")
    @limiter.limit("3 per hour")
    def request_password_reset():
        payload = json_payload()
        email = normalize_email(payload.get("email"))
        if not valid_email(email):
            return jsonify(error="Informe um e-mail válido."), 400
        if not (
            app.config["SMTP_HOST"]
            and app.config["SMTP_FROM"]
            and app.config["PUBLIC_BASE_URL"]
        ):
            return jsonify(error="A recuperação por e-mail não está configurada no servidor."), 503

        row = get_db().execute(
            "SELECT id, email FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        if not row:
            return jsonify(
                message="Se o e-mail estiver cadastrado, você receberá instruções para redefinir a senha."
            ), 202

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + RESET_TOKEN_LIFETIME).isoformat()
        db = get_db()
        db.execute("DELETE FROM reset_tokens WHERE user_id = ?", (row["id"],))
        db.execute(
            "INSERT INTO reset_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (row["id"], token_hash, expires_at),
        )
        db.commit()

        reset_url = (
            f"{app.config['PUBLIC_BASE_URL'].rstrip('/')}/#reset={quote(token)}"
        )
        try:
            send_reset_email(app, row["email"], reset_url)
        except (OSError, smtplib.SMTPException):
            logging.exception("Falha ao enviar e-mail de redefinição de senha.")
            db.execute("DELETE FROM reset_tokens WHERE token_hash = ?", (token_hash,))
            db.commit()
            return jsonify(error="Não foi possível enviar o e-mail agora. Tente novamente mais tarde."), 502
        return jsonify(
            message="Se o e-mail estiver cadastrado, você receberá instruções para redefinir a senha."
        ), 202

    @app.post("/api/password-reset/confirm")
    @limiter.limit("10 per hour")
    def confirm_password_reset():
        payload = json_payload()
        token = payload.get("token")
        password = payload.get("password")
        if not isinstance(token, str) or not token or not isinstance(password, str):
            return jsonify(error="Link de recuperação inválido ou expirado."), 400
        if len(password) < PASSWORD_MIN_LENGTH:
            return jsonify(error=f"A senha deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres."), 400

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """
            SELECT user_id FROM reset_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if not row:
            db.rollback()
            return jsonify(error="Link de recuperação inválido ou expirado."), 400

        db.execute(
            "UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE id = ?",
            (generate_password_hash(password), row["user_id"]),
        )
        db.execute(
            "UPDATE reset_tokens SET used_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        db.execute("DELETE FROM reset_tokens WHERE user_id = ? AND token_hash != ?", (row["user_id"], token_hash))
        db.commit()
        return jsonify(message="Senha redefinida. Você já pode entrar com a nova senha.")

    @app.cli.command("create-admin")
    def create_admin():
        """Create the single administrator account during initial setup."""
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
            raise click.ClickException("Já existe uma conta ADM.")

        username = click.prompt("Usuário ADM").strip()
        email = normalize_email(click.prompt("E-mail ADM"))
        password = click.prompt(
            "Senha ADM (mínimo 12 caracteres)",
            hide_input=True,
            confirmation_prompt=True,
        )
        if not valid_username(username):
            raise click.ClickException("O usuário deve ter de 3 a 32 caracteres válidos.")
        if not valid_email(email):
            raise click.ClickException("Informe um e-mail válido.")
        if len(password) < PASSWORD_MIN_LENGTH:
            raise click.ClickException(f"A senha deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres.")
        try:
            db.execute(
                """
                INSERT INTO users (username, email, password_hash, role, created_at)
                VALUES (?, ?, ?, 'admin', ?)
                """,
                (
                    username,
                    email,
                    generate_password_hash(password),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.commit()
        except sqlite3.IntegrityError as error:
            raise click.ClickException("Esse usuário ou e-mail já está cadastrado.") from error
        click.echo("Conta ADM criada.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
