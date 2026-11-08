"""Synthetic test fixtures for Upload Ready.

Zero real documents or IDs. All test inputs are procedurally generated in temporary storage.
"""

import io
from pathlib import Path
import pytest
from PIL import Image
import pypdf
from PySide6.QtWidgets import QApplication

from upload_ready.core.constants import UnitStandard


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication for GUI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["upload-ready-test"])
    yield app
    app.processEvents()


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Convenient isolated temporary directory for test outputs."""
    out = tmp_path / "test_out"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture
def synthetic_rgb_png(tmp_path: Path) -> Path:
    """Generate a clean synthetic 200x150 RGB PNG image."""
    img_path = tmp_path / "synthetic_rgb.png"
    img = Image.new("RGB", (200, 150), color=(50, 120, 200))
    # Draw simple gradient/pattern
    for x in range(50, 150):
        for y in range(40, 110):
            img.putpixel((x, y), (220, 80, 40))
    img.save(img_path, format="PNG")
    return img_path


@pytest.fixture
def synthetic_rgba_png(tmp_path: Path) -> Path:
    """Generate a synthetic 100x100 RGBA PNG image with transparent corners."""
    img_path = tmp_path / "synthetic_alpha.png"
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    # Add an opaque colored circle in the center
    for x in range(25, 75):
        for y in range(25, 75):
            img.putpixel((x, y), (0, 200, 100, 255))
    img.save(img_path, format="PNG")
    return img_path


@pytest.fixture
def synthetic_jpeg_with_exif(tmp_path: Path) -> Path:
    """Generate a synthetic JPEG with EXIF orientation metadata (Orientation=6: rotated 90 CW)."""
    img_path = tmp_path / "synthetic_exif.jpg"
    img = Image.new("RGB", (120, 60), color=(180, 50, 50))
    # EXIF orientation tag 0x0112 (274)
    exif = img.getexif()
    exif[0x0112] = 6  # Rotated 90 CW
    img.save(img_path, format="JPEG", exif=exif)
    return img_path


@pytest.fixture
def synthetic_multipage_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic 3-page PDF with distinct colors on each page."""
    pdf_path = tmp_path / "synthetic_doc.pdf"
    writer = pypdf.PdfWriter()
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    
    for c in colors:
        buf = io.BytesIO()
        page_img = Image.new("RGB", (300, 400), color=c)
        page_img.save(buf, format="PDF", resolution=72.0)
        buf.seek(0)
        reader = pypdf.PdfReader(buf)
        writer.add_page(reader.pages[0])
        
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path


@pytest.fixture
def synthetic_encrypted_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic password-protected PDF."""
    pdf_path = tmp_path / "encrypted_doc.pdf"
    writer = pypdf.PdfWriter()
    buf = io.BytesIO()
    page_img = Image.new("RGB", (200, 200), color=(100, 100, 100))
    page_img.save(buf, format="PDF", resolution=72.0)
    buf.seek(0)
    reader = pypdf.PdfReader(buf)
    writer.add_page(reader.pages[0])
    writer.encrypt(user_password="testpass123", owner_password="ownerpass123")
    
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path


@pytest.fixture
def synthetic_corrupt_file(tmp_path: Path) -> Path:
    """Generate a corrupt file with invalid header bytes."""
    corrupt_path = tmp_path / "corrupt.png"
    with open(corrupt_path, "wb") as f:
        f.write(b"NOT_A_VALID_HEADER_FOR_ANYTHING_CORRUPTED_STREAM")
    return corrupt_path
