"""Script to build clean source distribution archive (UploadReady-v1.0.0-rc2-source.zip)."""

import hashlib
import sys
import zipfile
from pathlib import Path


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build_source_zip():
    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_zip = root / "UploadReady-v1.0.0-rc2-source.zip"

    exclude_dirs = {
        ".git",
        ".venv",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".idea",
        ".vscode",
    }
    exclude_exts = {".pyc", ".pyo", ".zip", ".exe"}

    print(f"[*] Packaging source archive: {out_zip}")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            # Check exclusions
            parts = p.relative_to(root).parts
            if any(part in exclude_dirs for part in parts):
                continue
            if p.suffix.lower() in exclude_exts:
                continue
            if p.name in (".DS_Store", "Thumbs.db"):
                continue

            rel_path = p.relative_to(root)
            zipf.write(p, rel_path)

    size_kb = out_zip.stat().st_size / 1024
    sha = compute_file_sha256(out_zip)
    print(f"[+] Clean source archive created: {out_zip} ({size_kb:.1f} KB)")
    print(f"    SHA-256: {sha}")

    # Also place copy in dist/
    dist_zip = dist_dir / out_zip.name
    import shutil
    shutil.copy2(out_zip, dist_zip)
    print(f"[+] Dist copy placed: {dist_zip}")
    return out_zip


if __name__ == "__main__":
    build_source_zip()

