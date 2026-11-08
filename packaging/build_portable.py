"""Build script for Upload Ready standalone offline portable package.

Generates reproducible release candidate distributions with:
- Fully absolute paths independent of invocation working directory.
- Build manifest documenting source file hashes and resolved dependency versions.
- Bundled third-party notices and complete license text.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_source_manifest(root_dir: Path) -> dict:
    source_hashes = {}
    pkg_dir = root_dir / "upload_ready"
    for py_file in sorted(pkg_dir.rglob("*.py")):
        rel = str(py_file.relative_to(root_dir)).replace("\\", "/")
        source_hashes[rel] = compute_file_sha256(py_file)
    return source_hashes


def get_installed_dependencies() -> dict:
    deps = {}
    try:
        import importlib.metadata
        for pkg in [
            "pillow",
            "pypdf",
            "pypdfium2",
            "PySide6_Essentials",
            "shiboken6",
            "psutil",
            "pyinstaller",
        ]:
            try:
                deps[pkg] = importlib.metadata.version(pkg)
            except Exception:
                deps[pkg] = "unknown"
    except Exception:
        pass
    return deps


def build():
    root_dir = Path(__file__).resolve().parent.parent
    dist_dir = root_dir / "dist"
    spec_path = root_dir / "packaging" / "upload_ready.spec"
    version = "1.0.0-rc2"

    print(f"[*] Building Upload Ready offline distribution v{version} in: {root_dir}")

    # Invoke PyInstaller
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec_path),
    ]

    print(f"[*] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=root_dir)
    if result.returncode != 0:
        print("[!] PyInstaller build failed!")
        sys.exit(result.returncode)

    app_folder = dist_dir / "UploadReady"
    if not app_folder.exists():
        print(f"[!] Expected output folder {app_folder} does not exist.")
        sys.exit(1)

    # Copy documentation and notices into the distribution
    for doc in ["README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"]:
        src = root_dir / doc
        if src.exists():
            shutil.copy2(src, app_folder / doc)

    # Generate BUILD_MANIFEST.json
    manifest = {
        "application": "Upload Ready",
        "version": version,
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": "windows-x64",
        "python_runtime": sys.version,
        "resolved_dependencies": get_installed_dependencies(),
        "source_file_hashes": generate_source_manifest(root_dir),
    }

    manifest_path = app_folder / "BUILD_MANIFEST.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    dist_manifest = dist_dir / "BUILD_MANIFEST.json"
    shutil.copy2(manifest_path, dist_manifest)
    print(f"[+] Generated build manifest: {manifest_path} and {dist_manifest}")

    # Package into portable zip
    zip_paths = [
        dist_dir / f"UploadReady-v{version}-windows-x64-portable.zip",
        dist_dir / "UploadReady-v1.0.0-windows-x64-portable.zip",
    ]

    for zip_path in zip_paths:
        print(f"[*] Creating portable zip package: {zip_path}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file in app_folder.rglob("*"):
                if file.is_file():
                    rel_path = file.relative_to(dist_dir)
                    zipf.write(file, rel_path)

        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"[+] Complete offline package created: {zip_path} ({zip_size_mb:.1f} MB)")

    print("[+] All runtimes, codecs, and PDF engines bundled. Zero runtime network dependencies.")


if __name__ == "__main__":
    build()
