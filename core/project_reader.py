"""Safe source and project archive readers for ZenoAI.

Uploaded files are inspected as text only; nothing from an upload is executed.
"""
import io
import os
import tarfile
import zipfile
from pathlib import PurePosixPath

from config import config

# Common source, markup, config, infrastructure, and documentation formats.
TEXT_EXTENSIONS = {
    # Python and data science
    ".py", ".pyw", ".pyi", ".ipynb", ".r", ".rmd",
    # JavaScript / TypeScript / web
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".astro", ".wasm",
    # JVM / Android
    ".java", ".kt", ".kts", ".scala", ".sc", ".groovy", ".gradle",
    ".gradle.kts", ".properties",
    # Mobile / desktop
    ".dart", ".swift", ".m", ".mm", ".h", ".hpp", ".c", ".cc", ".cpp",
    ".cxx", ".cs", ".fs", ".fsx", ".vb",
    # Systems / backend
    ".go", ".rs", ".php", ".rb", ".pl", ".pm", ".lua", ".ex", ".exs",
    ".erl", ".hrl", ".hs", ".lhs", ".clj", ".cljs", ".cljc", ".edn",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd",
    # Data / query / config
    ".json", ".jsonc", ".json5", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".xml", ".xsd", ".xsl", ".sql", ".graphql", ".gql",
    ".proto", ".avsc", ".env.example",
    # Infrastructure / build
    ".tf", ".tfvars", ".hcl", ".dockerignore", ".gitignore", ".gitattributes",
    ".cmake", ".mk", ".make", ".gradle", ".lock",
    # Docs / templates
    ".md", ".mdx", ".rst", ".txt", ".tex", ".vue", ".j2", ".jinja",
}
SPECIAL_TEXT_NAMES = {
    "dockerfile", "makefile", "procfile", "justfile", "vagrantfile",
    "gemfile", "rakefile", "brewfile", "license", "readme", "notice",
    "cmakelists.txt", "requirements.txt", "pipfile", "poetry.lock",
    "cargo.toml", "go.mod", "go.sum", "package.json", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "composer.json", "composer.lock",
    "pubspec.yaml", "pubspec.lock", "build.gradle", "settings.gradle",
    ".gitignore", ".dockerignore", ".editorconfig",
}
SKIP_PARTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", "target", ".next", ".nuxt", ".idea", ".vscode",
}
SKIP_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials.json", "secrets.json", "service-account.json",
}
MAX_CONTEXT_CHARS = 180000


def _safe_name(name):
    normalized = str(name).replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        bool(normalized)
        and not path.is_absolute()
        and not normalized.startswith("/")
        and "\x00" not in normalized
        and ".." not in path.parts
    )


def _is_text_name(name):
    lower = PurePosixPath(name).name.lower()
    suffix = PurePosixPath(lower).suffix
    return suffix in TEXT_EXTENSIONS or lower in SPECIAL_TEXT_NAMES or lower.endswith(".env.example")


def _decode_text(raw):
    if b"\x00" in raw[:4096]:
        return None
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def _read_archive_member(name, raw, tree, excerpts, max_text):
    if not _safe_name(name):
        raise ValueError("Unsafe archive path detected. The archive was rejected.")
    parts = PurePosixPath(name).parts
    if any(part in SKIP_PARTS for part in parts):
        return
    base = PurePosixPath(name).name
    if base.lower() in SKIP_NAMES or base.lower().startswith(".env."):
        tree.append(f"[skipped sensitive file] {name}")
        return
    tree.append(name)
    if not _is_text_name(name):
        return
    if len(raw) > max_text:
        excerpts.append(f"--- {name} ---\n[Skipped: file exceeds {config.max_file_text_kb} KB text limit]")
        return
    text = _decode_text(raw)
    if text is not None:
        excerpts.append(f"--- FILE: {name} ---\n{text}")


def _finish_archive(tree, excerpts):
    if not tree:
        raise ValueError("No readable project entries were found in this archive.")
    tree_text = "PROJECT TREE:\n" + "\n".join(tree[:config.max_archive_files])
    combined = tree_text + "\n\nSOURCE EXCERPTS:\n" + "\n\n".join(excerpts)
    if len(combined) > MAX_CONTEXT_CHARS:
        combined = combined[:MAX_CONTEXT_CHARS] + "\n\n[Context truncated to fit safe analysis limits.]"
    return combined, len(tree), len(excerpts)


def _check_upload_size(data):
    if len(data) > config.max_upload_mb * 1024 * 1024:
        raise ValueError(f"Upload is larger than the {config.max_upload_mb} MB limit.")


def read_zip_bytes(data):
    _check_upload_size(data)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a valid ZIP archive.") from exc
    with archive:
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
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                continue
            # Avoid pathological compression ratios before reading a member.
            if info.file_size > 0 and info.compress_size == 0:
                raise ValueError("Archive contains an invalid compressed entry.")
            if info.compress_size and info.file_size / info.compress_size > 1000:
                raise ValueError("Archive contains a suspiciously compressed entry.")
            if not _safe_name(info.filename):
                raise ValueError("Unsafe ZIP path detected. The archive was rejected.")
            parts = PurePosixPath(info.filename).parts
            if any(part in SKIP_PARTS for part in parts):
                continue
            base = PurePosixPath(info.filename).name
            if base.lower() in SKIP_NAMES or base.lower().startswith(".env."):
                tree.append(f"[skipped sensitive file] {info.filename}")
                continue
            tree.append(info.filename)
            if not _is_text_name(info.filename):
                continue
            if info.file_size > max_text:
                excerpts.append(f"--- {info.filename} ---\n[Skipped: file exceeds {config.max_file_text_kb} KB text limit]")
                continue
            raw = archive.read(info)
            text = _decode_text(raw)
            if text is not None:
                excerpts.append(f"--- FILE: {info.filename} ---\n{text}")
        return _finish_archive(tree, excerpts)


def read_tar_bytes(data):
    """Read .tar, .tar.gz, .tgz, .tar.bz2 and .tar.xz archives."""
    _check_upload_size(data)
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("That file is not a supported or valid TAR archive.") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > config.max_archive_files:
            raise ValueError(f"Archive has too many entries (limit: {config.max_archive_files}).")
        files = [m for m in members if m.isfile()]
        total_size = sum(m.size for m in files)
        if total_size > config.max_archive_unpacked_mb * 1024 * 1024:
            raise ValueError(f"Unpacked archive exceeds {config.max_archive_unpacked_mb} MB.")
        tree, excerpts = [], []
        max_text = config.max_file_text_kb * 1024
        for member in files:
            if not _safe_name(member.name):
                raise ValueError("Unsafe TAR path detected. The archive was rejected.")
            parts = PurePosixPath(member.name).parts
            if any(part in SKIP_PARTS for part in parts):
                continue
            base = PurePosixPath(member.name).name
            if base.lower() in SKIP_NAMES or base.lower().startswith(".env."):
                tree.append(f"[skipped sensitive file] {member.name}")
                continue
            tree.append(member.name)
            if not _is_text_name(member.name):
                continue
            if member.size > max_text:
                excerpts.append(f"--- {member.name} ---\n[Skipped: file exceeds {config.max_file_text_kb} KB text limit]")
                continue
            stream = archive.extractfile(member)
            raw = stream.read(max_text + 1) if stream else b""
            text = _decode_text(raw)
            if text is not None:
                excerpts.append(f"--- FILE: {member.name} ---\n{text}")
        return _finish_archive(tree, excerpts)


def read_single_source(filename, data):
    _check_upload_size(data)
    name = os.path.basename(filename or "uploaded-file")
    if name.lower() in SKIP_NAMES or name.lower().startswith(".env."):
        raise ValueError("This filename looks like a secrets file and will not be analyzed.")
    if len(data) > config.max_file_text_kb * 1024:
        raise ValueError(f"File is larger than the {config.max_file_text_kb} KB text limit.")
    text = _decode_text(data)
    if text is None:
        raise ValueError("This looks like a binary or unsupported text file. Please upload source code or a project archive.")
    if not _is_text_name(name):
        raise ValueError(f"Unsupported source/document extension: {PurePosixPath(name).suffix or '(none)'}.")
    return f"--- FILE: {name} ---\n{text}", 1, 1
