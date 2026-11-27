import io
import tarfile
import unittest
import zipfile

from core.project_reader import read_single_source, read_tar_bytes, read_zip_bytes


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


def make_tar(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in entries:
            raw = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


class ProjectReaderTests(unittest.TestCase):
    def test_reads_source_file_from_zip(self):
        payload = make_zip([("src/main.py", "print('hello')")])
        context, file_count, text_count = read_zip_bytes(payload)
        self.assertIn("src/main.py", context)
        self.assertIn("print('hello')", context)
        self.assertEqual(file_count, 1)
        self.assertEqual(text_count, 1)

    def test_reads_multiple_language_files_from_zip(self):
        payload = make_zip([
            ("frontend/App.tsx", "export default function App() {}"),
            ("android/MainActivity.kt", "class MainActivity"),
            ("backend/main.go", "package main"),
            ("infra/Dockerfile", "FROM python:3.12"),
        ])
        context, file_count, text_count = read_zip_bytes(payload)
        self.assertEqual(file_count, 4)
        self.assertEqual(text_count, 4)
        self.assertIn("export default function App()", context)
        self.assertIn("class MainActivity", context)
        self.assertIn("package main", context)
        self.assertIn("FROM python:3.12", context)

    def test_reads_tar_gz_project(self):
        payload = make_tar([("src/main.rs", "fn main() {}")])
        context, file_count, text_count = read_tar_bytes(payload)
        self.assertIn("src/main.rs", context)
        self.assertIn("fn main() {}", context)
        self.assertEqual((file_count, text_count), (1, 1))

    def test_reads_single_python_source(self):
        context, file_count, text_count = read_single_source("bot.py", b"print('hello')")
        self.assertIn("bot.py", context)
        self.assertIn("print('hello')", context)
        self.assertEqual((file_count, text_count), (1, 1))

    def test_rejects_parent_directory_path(self):
        payload = make_zip([("../outside.py", "print('bad')")])
        with self.assertRaises(ValueError):
            read_zip_bytes(payload)

    def test_skips_environment_secret_file(self):
        payload = make_zip([(".env", "TOKEN=do-not-read"), ("app.py", "print('ok')")])
        context, _, _ = read_zip_bytes(payload)
        self.assertNotIn("do-not-read", context)
        self.assertIn("app.py", context)


if __name__ == "__main__":
    unittest.main()
