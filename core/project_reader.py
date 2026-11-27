import io
import os
import zipfile
from pathlib import PurePosixPath
from config import config

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts", ".dart",
    ".html", ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".md", ".txt", ".sql", ".sh", ".bat", ".ps1", ".xml", ".gradle",
    ".properties", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".php",
    ".rb", ".swift", ".vue", ".svelte", ".env.example"
}
SKIP_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
SKIP_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}


def _safe_name(name):
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return bool(normalized) and not path.is_absolute() and ".." not in path.parts and not normalized.startswith("/")


def read_zip_bytes(data):
    if len(data) > config.max_upload_mb * 1024 * 1024:
        raise ValueError(f"Upload is larger than the {config.max_upload_mb} MB limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That file is not a valid ZIP archive.")
    infos = archive.infolist()
    if len(infos) > config.max_archive_files:
        raise ValueError(f"Archive has too many entries (limit: {config.max_archive_files}).")
    total_size = sum(info.file_size for info in infos if not info.is_dir())
    if total_size > config.max_archive_unpacked_mb * 1024 * 1024:
        raise ValueError(f"Unpacked archive exceeds {config.max_archive_unpacked_mb} MB.")
    tree, excerpts = [], []
    max_text = config.max_file_text_kb * 1024
    for info in infos:
        if info.is_dir():
            continue
        if not _safe_name(info.filename):
            raise ValueError("Unsafe ZIP path detected. The archive was rejected.")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            continue
        parts = PurePosixPath(info.filename).parts
        if any(part in SKIP_PARTS for part in parts):
            continue
        name = PurePosixPath(info.filename).name
        if name.lower() in SKIP_NAMES:
            tree.append(f"[skipped sensitive file] {info.filename}")
            continue
        tree.append(info.filename)
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in TEXT_EXTENSIONS and name.lower() not in {"dockerfile", "makefile", "procfile"}:
            continue
        if info.file_size > max_text:
            excerpts.append(f"--- {info.filename} ---\n[Skipped: file exceeds {config.max_file_text_kb} KB text limit]")
            continue
        raw = archive.read(info)
        if b"\x00" in raw[:4096]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except Exception:
                continue
        excerpts.append(f"--- FILE: {info.filename} ---\n{text}")
    if not tree:
        raise ValueError("No readable project entries were found in this ZIP.")
    tree_text = "PROJECT TREE:\n" + "\n".join(tree[:config.max_archive_files])
    file_text = "\n\n".join(excerpts)
    budget = 180000
    combined = tree_text + "\n\nSOURCE EXCERPTS:\n" + file_text
    if len(combined) > budget:
        combined = combined[:budget] + "\n\n[Context truncated to fit safe analysis limits.]"
    return combined, len(tree), len(excerpts)


def read_single_source(filename, data):
    if len(data) > config.max_upload_mb * 1024 * 1024:
        raise ValueError(f"Upload is larger than the {config.max_upload_mb} MB limit.")
    name = os.path.basename(filename or "uploaded-file")
    if name.lower() in SKIP_NAMES:
        raise ValueError("This filename looks like a secrets file and will not be analyzed.")
    if len(data) > config.max_file_text_kb * 1024:
        raise ValueError(f"File is larger than the {config.max_file_text_kb} KB text limit.")
    if b"\x00" in data[:4096]:
        raise ValueError("This looks like a binary file. Please upload source code or a ZIP project.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Could not decode this file as UTF-8 text.")
    return f"--- FILE: {name} ---\n{text}", 1, 1
