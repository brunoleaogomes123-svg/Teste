import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

import app as backend


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = backend.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": os.path.join(self.temp_dir.name, "users.sqlite3"),
                "WTF_CSRF_ENABLED": False,
                "RATELIMIT_ENABLED": False,
                "SMTP_HOST": "smtp.example.test",
                "SMTP_FROM": "access@example.test",
                "PUBLIC_BASE_URL": "https://app.example.test",
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db = backend.get_db()
            db.executemany(
                """
                INSERT INTO users (username, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00')
                """,
                [
                    ("admin", "admin@example.test", generate_password_hash("admin-password-123"), "admin"),
                    ("worker", "worker@example.test", generate_password_hash("worker-password-123"), "user"),
                ],
            )
            db.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self, username="admin", password="admin-password-123"):
        return self.client.post(
            "/api/login",
            json={"username": username, "password": password},
        )

    def test_only_admin_can_create_users(self):
        unauthenticated = self.client.post(
            "/api/admin/users",
            json={
                "username": "new-user",
                "email": "new@example.test",
                "password": "new-user-password-123",
            },
        )
        self.assertEqual(unauthenticated.status_code, 401)

        self.assertEqual(
            self.login("worker", "worker-password-123").status_code,
            200,
        )
        forbidden = self.client.post(
            "/api/admin/users",
            json={
                "username": "new-user",
                "email": "new@example.test",
                "password": "new-user-password-123",
                "role": "admin",
            },
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.post("/api/logout")
        self.assertEqual(self.login().status_code, 200)
        created = self.client.post(
            "/api/admin/users",
            json={
                "username": "new-user",
                "email": "new@example.test",
                "password": "new-user-password-123",
                "role": "admin",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["role"], "user")

    def test_chat_is_shared_by_authenticated_users(self):
        self.assertEqual(self.client.get("/api/chat/messages").status_code, 401)
        self.assertEqual(
            self.client.post("/api/chat/messages", json={"body": "Olá"}).status_code,
            401,
        )

        self.assertEqual(self.login().status_code, 200)
        empty = self.client.post("/api/chat/messages", json={"body": "   "})
        too_long = self.client.post(
            "/api/chat/messages",
            json={"body": "x" * (backend.MAX_CHAT_MESSAGE_LENGTH + 1)},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(too_long.status_code, 400)

        sent = self.client.post(
            "/api/chat/messages",
            json={"body": "Mensagem do administrador"},
        )
        self.assertEqual(sent.status_code, 201)
        self.client.post("/api/logout")

        self.assertEqual(
            self.login("worker", "worker-password-123").status_code,
            200,
        )
        received = self.client.get("/api/chat/messages")
        self.assertEqual(received.status_code, 200)
        self.assertEqual(
            received.json["messages"][0]["username"],
            "admin",
        )
        self.assertEqual(
            received.json["messages"][0]["body"],
            "Mensagem do administrador",
        )

        worker_sent = self.client.post(
            "/api/chat/messages",
            json={"body": "Resposta do usuário"},
        )
        self.assertEqual(worker_sent.status_code, 201)
        self.assertEqual(
            self.client.get(
                f"/api/chat/messages?after={sent.json['message']['id']}"
            ).json["messages"][0]["body"],
            "Resposta do usuário",
        )

    def test_chat_rejects_invalid_message_cursor(self):
        self.assertEqual(self.login().status_code, 200)
        response = self.client.get("/api/chat/messages?after=abc")
        self.assertEqual(response.status_code, 400)

    def test_chat_delete_permissions_for_author_and_admin(self):
        self.assertEqual(
            self.client.delete("/api/chat/messages/1").status_code,
            401,
        )
        self.assertEqual(self.login().status_code, 200)
        admin_message = self.client.post(
            "/api/chat/messages",
            json={"body": "Mensagem do administrador"},
        ).json["message"]
        self.client.post("/api/logout")

        self.assertEqual(
            self.login("worker", "worker-password-123").status_code,
            200,
        )
        forbidden = self.client.delete(
            f"/api/chat/messages/{admin_message['id']}"
        )
        self.assertEqual(forbidden.status_code, 403)

        worker_message = self.client.post(
            "/api/chat/messages",
            json={"body": "Mensagem do usuário"},
        ).json["message"]
        deleted_by_author = self.client.delete(
            f"/api/chat/messages/{worker_message['id']}"
        )
        self.assertEqual(deleted_by_author.status_code, 200)

        moderated_message = self.client.post(
            "/api/chat/messages",
            json={"body": "Mensagem para moderação"},
        ).json["message"]
        self.client.post("/api/logout")

        self.assertEqual(self.login().status_code, 200)
        deleted_by_admin = self.client.delete(
            f"/api/chat/messages/{moderated_message['id']}"
        )
        self.assertEqual(deleted_by_admin.status_code, 200)
        self.assertEqual(
            self.client.get("/api/chat/messages").json["messages"][0]["id"],
            admin_message["id"],
        )
        missing = self.client.delete(
            f"/api/chat/messages/{moderated_message['id']}"
        )
        self.assertEqual(missing.status_code, 404)

    def test_password_reset_expires_by_use_and_invalidates_sessions(self):
        self.assertEqual(self.login("worker", "worker-password-123").status_code, 200)
        with patch("app.send_reset_email") as send_email:
            requested = self.client.post(
                "/api/password-reset/request",
                json={"email": "worker@example.test"},
            )
        self.assertEqual(requested.status_code, 202)

        reset_url = send_email.call_args.args[2]
        token = urlparse(reset_url).fragment.removeprefix("reset=")
        reset = self.client.post(
            "/api/password-reset/confirm",
            json={"token": token, "password": "replacement-password-456"},
        )
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(self.client.get("/api/me").json["authenticated"])

        reused = self.client.post(
            "/api/password-reset/confirm",
            json={"token": token, "password": "another-password-789"},
        )
        self.assertEqual(reused.status_code, 400)
        self.assertEqual(self.login("worker", "worker-password-123").status_code, 401)
        self.assertEqual(self.login("worker", "replacement-password-456").status_code, 200)

    def test_expired_password_reset_link_is_rejected(self):
        with patch("app.send_reset_email") as send_email:
            self.client.post(
                "/api/password-reset/request",
                json={"email": "worker@example.test"},
            )
        token = urlparse(send_email.call_args.args[2]).fragment.removeprefix("reset=")
        with self.app.app_context():
            backend.get_db().execute(
                "UPDATE reset_tokens SET expires_at = '2000-01-01T00:00:00+00:00'"
            )
            backend.get_db().commit()
        expired = self.client.post(
            "/api/password-reset/confirm",
            json={"token": token, "password": "replacement-password-456"},
        )
        self.assertEqual(expired.status_code, 400)

    def test_cli_creates_the_single_admin_account(self):
        admin_dir = tempfile.TemporaryDirectory()
        self.addCleanup(admin_dir.cleanup)
        admin_app = backend.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "admin-cli-test-secret",
                "DATABASE": os.path.join(admin_dir.name, "admin.sqlite3"),
                "RATELIMIT_ENABLED": False,
            }
        )
        runner = admin_app.test_cli_runner()
        result = runner.invoke(
            args=["create-admin"],
            input="root\nroot@example.test\nroot-password-123\nroot-password-123\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        duplicate = runner.invoke(args=["create-admin"])
        self.assertNotEqual(duplicate.exit_code, 0)

    def test_write_requests_require_csrf_token(self):
        csrf_app = backend.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "csrf-test-secret",
                "DATABASE": os.path.join(self.temp_dir.name, "csrf.sqlite3"),
            }
        )
        response = csrf_app.test_client().post(
            "/api/login",
            json={"username": "admin", "password": "password"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
