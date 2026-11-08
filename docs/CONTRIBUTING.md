# Contributing to Upload Ready

Thank you for contributing to Upload Ready. As an open-source, local-only desktop utility, we maintain high standards for privacy, correctness, readability, and security.

---

## Core Principles

1. **Strictly Local-Only:** We will reject any pull request that introduces network communication, telemetry, analytics, cloud API integrations, automatic background update checks, or runtime dependency downloads.
2. **Untrusted Input Defense:** Treat all imported files as untrusted. Enforce resource limits, validate magic byte headers (do not rely on file extensions), and prevent directory traversal.
3. **Synthetic Test Fixtures:** Never commit personal photos, scanned documents, receipts, or government IDs to the repository. All automated tests must use procedurally generated synthetic fixtures.
4. **Honest Limitations:** Never silently stretch images, discard frames, or claim that lossy conversions restore lost detail. If an operation cannot satisfy a constraint, report the limitation honestly.

---

## Development Setup

```powershell
# 1. Clone repository
git clone https://github.com/upload-ready/upload-ready.git
cd upload-ready

# 2. Create virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# 3. Install dependencies in editable mode
pip install -r requirements.txt
pip install -e .

# 4. Run tests
pytest -v
```

---

## Code Style & Architecture

- Maintain clear separation between `upload_ready.core` (pure domain processing, zero Qt dependencies), `upload_ready.cli` (command-line driver), and `upload_ready.gui` (PySide6 interface).
- Write descriptive function and parameter names. Avoid unnecessary wrapper layers, speculative factories, or excessive boilerplate.
- Add comments only to explain non-obvious constraints, safety invariants, and architectural decisions. Avoid decorative comment banners.

---

## Submitting Pull Requests

1. Ensure all 45+ unit and integration tests pass cleanly with `pytest -v`.
2. Verify that your changes do not introduce new warnings or unhandled exceptions.
3. If modifying core processing, ensure that both the CLI and desktop UI reflect the changes identically.

