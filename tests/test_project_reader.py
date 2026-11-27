import io
import unittest
import zipfile

from core.project_reader import read_zip_bytes


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


class ProjectReaderTests(unittest.TestCase):
    def test_reads_source_file_from_zip(self):
        payload = make_zip([("src/main.py", "print('hello')")])
        context, file_count, text_count = read_zip_bytes(payload)
        self.assertIn("src/main.py", context)
        self.assertIn("print('hello')", context)
        self.assertEqual(file_count, 1)
        self.assertEqual(text_count, 1)

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
