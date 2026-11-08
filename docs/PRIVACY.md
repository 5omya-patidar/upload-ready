# Privacy & Data Handling Policy

Upload Ready is engineered with a strict **local-only architecture**. This document specifies how files, data, and system resources are handled during operation.

---

## 1. Zero Outbound Network Connectivity

- **No Uploads:** Your images, documents, and extracted text never leave your computer.
- **No Telemetry or Analytics:** The application does not collect usage metrics, session timestamps, device IDs, or crash dumps.
- **No Automatic Update Checks:** Upload Ready will never poll an external server for new versions. Updates are downloaded and applied manually outside the application.
- **No Remote Resources:** All fonts, UI stylesheets, codecs, and PDF rendering libraries are bundled directly inside the offline package. No CDN calls or remote APIs are ever contacted.

---

## 2. In-Memory Processing & Temporary Files

- **Memory-First Execution:** Operations (such as resizing, format conversions, and PDF page assembly) execute in system memory (`RAM`) using high-performance stream buffers (`io.BytesIO`) where possible.
- **Per-Job Temporary Workspaces:** When disk buffering is required, temporary files are written to a private subfolder inside the operating system temporary directory:
  ```
  %TEMP%\upload_ready_workspace\job_<timestamp>_<pid>\
  ```
- **Guaranteed Cleanup:** The per-job temporary workspace is wrapped in an atomic context manager (`TempJobWorkspace`) that purges all temporary files upon normal job completion, handled errors, or user cancellation.
- **Orphan Cleanup:** On application startup, Upload Ready automatically scans `%TEMP%\upload_ready_workspace` and deletes abandoned temporary directories older than 1 hour left behind by past system crashes or unexpected OS shutdowns.

---

## 3. Logging & Sensitive Information

- **No Document Payload Logging:** Document text, image pixels, and metadata values are never printed to logs or standard error streams.
- **Path & Filename Sanitization:** Log entries do not store absolute folder paths, user profile directory names, or document filenames. Files are referenced anonymously (e.g., `[file: *.pdf]`).
- **Passwords:** PDF decryption passwords entered by the user are held transiently in memory for the single operation and are never written to disk, stored in settings, or logged.

---

## 4. Preservation of Originals

- **No Accidental Overwrites:** Output files are written to a user-specified destination folder. If an output filename already exists, Upload Ready automatically appends a numeric suffix (e.g., `image_1.png`, `image_2.png`) unless the user explicitly checks the "Allow overwrite" box. Original source files are opened read-only and are never modified in-place.

---

## 5. Honest Limits on Deletion

Upload Ready deletes temporary files using standard operating system deletion APIs (`shutil.rmtree`). We do not claim forensic multi-pass cryptographic erasure of sectors on solid-state drives (SSDs), flash media, or OS paging files.

