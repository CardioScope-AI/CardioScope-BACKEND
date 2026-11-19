import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.init_admin import main, read_password_file, validate_credentials
from app.passwords import hash_password, verify_password


class AdminInitializationTests(unittest.TestCase):
    def test_unknown_arguments_are_not_echoed(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as error:
            main(["--username", "admin", "--password", "test-accidental-secret"])
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn("test-accidental-secret", output.getvalue())

    def test_argon2_hash_is_salted_and_verifiable(self):
        password = "a-test-only-password"
        first, second = hash_password(password), hash_password(password)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("$argon2id$"))
        self.assertNotIn(password, first)
        self.assertTrue(verify_password(first, password))
        self.assertFalse(verify_password(first, "wrong-test-password"))
        self.assertFalse(verify_password("invalid-hash", password))

    def test_password_policy_and_username_reject_invalid_values(self):
        for username, password in [("", "a-test-only-password"),
                                   ("bad name", "a-test-only-password"),
                                   ("admin", "short"), ("admin", "x" * 129),
                                   ("admin", "a-test-only-password\n")]:
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    validate_credentials(username, password)

    def test_secret_file_keeps_unicode_and_spaces_removes_only_line_ending(self):
        password = "  phrase-secrète-de-test  "
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_bytes((password + "\r\n").encode("utf-8"))
            self.assertEqual(read_password_file(path), password)
            path.write_bytes(b"x" * 1025)
            with self.assertRaises(ValueError):
                read_password_file(path)

    def test_noninteractive_mode_does_not_fall_back_to_echoing_password(self):
        output = io.StringIO()
        with patch("app.init_admin.sys.stdin.isatty", return_value=False), \
             patch("app.init_admin.get_engine") as engine, contextlib.redirect_stderr(output):
            self.assertEqual(main(["--username", "admin"]), 1)
            engine.assert_not_called()

    def test_invalid_secret_file_does_not_echo_content_or_path(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-secret"
            path.write_bytes(b"\xffSECRET")
            with contextlib.redirect_stderr(output), patch("app.init_admin.get_engine") as engine:
                self.assertEqual(main(["--username", "admin", "--password-file", str(path)]), 1)
                engine.assert_not_called()
            self.assertNotIn("SECRET", output.getvalue())
            self.assertNotIn(str(path), output.getvalue())

    def test_confirmation_mismatch_does_not_connect_to_database(self):
        with patch("app.init_admin.sys.stdin.isatty", return_value=True), \
             patch("app.init_admin.getpass.getpass", side_effect=["first-test-password", "second-test-password"]), \
             patch("app.init_admin.get_engine") as engine, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--username", "admin"]), 1)
            engine.assert_not_called()
