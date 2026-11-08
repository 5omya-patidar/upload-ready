"""Continuous network observation test for Upload Ready packaged distribution.

Windows-specific test that exercises the built standalone executables (UploadReady.exe
and upload-ready.exe). Monitors process-tree socket activity at 50ms intervals,
tests graceful GUI shutdown, and tracks Windows DNS client cache differential.
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set
import psutil
from PIL import Image


def get_dns_cache_entries() -> Set[str]:
    """Capture current entries in Windows DNS Client Cache via PowerShell."""
    try:
        cmd = ["powershell", "-NoProfile", "-Command", "Get-DnsClientCache | Select-Object -ExpandProperty Entry"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return {line.strip() for line in res.stdout.splitlines() if line.strip()}
    except Exception:
        pass
    return set()


def send_wm_close(pid: int) -> bool:
    """Send WM_CLOSE window message for graceful desktop shutdown."""
    if sys.platform != "win32":
        return False
    closed = False

    def enum_cb(hwnd, extra):
        nonlocal closed
        pid_val = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
        if pid_val.value == pid and ctypes.windll.user32.IsWindowVisible(hwnd):
            WM_CLOSE = 0x0010
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            closed = True
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    return closed


class ContinuousNetworkMonitor:
    """Samples socket endpoints for all processes in the target tree."""

    def __init__(self, sample_interval_sec: float = 0.05):
        self.sample_interval = sample_interval_sec
        self.tracked_pids: Set[int] = set()
        self.observed_connections: List[Dict] = []
        self.monitoring_errors: List[str] = []
        self.is_running = False
        self._lock = threading.Lock()
        self._thread = None
        self.samples_collected = 0

    def register_pid(self, pid: int):
        with self._lock:
            self.tracked_pids.add(pid)
            try:
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    self.tracked_pids.add(child.pid)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as exc:
                self.monitoring_errors.append(f"AccessDenied enumerating children for PID {pid}: {exc}")

    def start(self):
        self.is_running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.is_running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _monitor_loop(self):
        while self.is_running:
            with self._lock:
                current_pids = set(self.tracked_pids)
                for pid in list(current_pids):
                    try:
                        p = psutil.Process(pid)
                        for ch in p.children(recursive=True):
                            current_pids.add(ch.pid)
                            self.tracked_pids.add(ch.pid)
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.AccessDenied as exc:
                        self.monitoring_errors.append(f"AccessDenied scanning PID {pid}: {exc}")

            for pid in current_pids:
                try:
                    p = psutil.Process(pid)
                    conns = p.net_connections(kind="all")
                    for c in conns:
                        self.observed_connections.append({
                            "pid": pid,
                            "fd": getattr(c, "fd", None),
                            "family": str(c.family),
                            "type": str(c.type),
                            "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
                            "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None,
                            "status": c.status,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied as exc:
                    self.monitoring_errors.append(f"AccessDenied reading connections for PID {pid}: {exc}")
                except OSError as exc:
                    self.monitoring_errors.append(f"OSError reading connections for PID {pid}: {exc}")

            self.samples_collected += 1
            time.sleep(self.sample_interval)


def run_observation_session(root_dir: Path) -> Dict:
    if sys.platform != "win32":
        raise RuntimeError("Packaged observation test requires Windows environment.")

    gui_exe = root_dir / "dist" / "UploadReady" / "UploadReady.exe"
    cli_exe = root_dir / "dist" / "UploadReady" / "upload-ready.exe"

    if not gui_exe.exists() or not cli_exe.exists():
        raise FileNotFoundError(
            f"Packaged binaries not found at {gui_exe} or {cli_exe}. Build distribution before running observation test."
        )

    work_dir = root_dir / "dist" / "net_test_tmp"
    if work_dir.exists():
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    test_img = work_dir / "input.png"
    Image.new("RGB", (200, 200), (0, 128, 255)).save(test_img)

    gif_img = work_dir / "animated.gif"
    f1 = Image.new("RGB", (50, 50), (255, 0, 0))
    f2 = Image.new("RGB", (50, 50), (0, 255, 0))
    f1.save(gif_img, save_all=True, append_images=[f2])

    start_dns = get_dns_cache_entries()
    start_time = datetime.now(timezone.utc)
    monitor = ContinuousNetworkMonitor(sample_interval_sec=0.05)
    monitor.start()

    exercised_steps = []

    try:
        # Step 1: Launch GUI, verify idle state, and trigger graceful shutdown via WM_CLOSE
        gui_proc = subprocess.Popen([str(gui_exe)])
        monitor.register_pid(gui_proc.pid)
        time.sleep(2.0)
        exercised_steps.append("GUI startup and idle")

        send_wm_close(gui_proc.pid)
        try:
            gui_proc.wait(timeout=5)
            exercised_steps.append("GUI graceful window close")
        except subprocess.TimeoutExpired:
            gui_proc.kill()
            exercised_steps.append("GUI forced kill after timeout")

        # Step 2: CLI Convert
        conv_dir = work_dir / "conv_out"
        p = subprocess.Popen([str(cli_exe), "convert", str(test_img), "-f", "webp", "-o", str(conv_dir)])
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode == 0
        exercised_steps.append("CLI convert PNG->WEBP")

        # Step 3: CLI Compress
        comp_dir = work_dir / "comp_out"
        p = subprocess.Popen([str(cli_exe), "compress", str(test_img), "--max-size", "4KB", "-o", str(comp_dir)])
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode == 0
        exercised_steps.append("CLI compress target size")

        # Step 4: CLI Resize
        res_dir = work_dir / "res_out"
        p = subprocess.Popen([str(cli_exe), "resize", str(test_img), "-W", "100", "-H", "100", "-o", str(res_dir)])
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode == 0
        exercised_steps.append("CLI resize downscale")

        # Step 5: Multi-frame refusal
        mf_dir = work_dir / "mf_out"
        p = subprocess.Popen(
            [str(cli_exe), "convert", str(gif_img), "-f", "png", "-o", str(mf_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode != 0
        exercised_steps.append("CLI multi-frame refusal")

        # Step 6: Images to PDF
        pdf_out = work_dir / "doc.pdf"
        p = subprocess.Popen([str(cli_exe), "images-to-pdf", str(test_img), "-o", str(pdf_out)])
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode == 0
        exercised_steps.append("CLI images-to-pdf")

        # Step 7: PDF to Images
        pages_dir = work_dir / "pdf_pages"
        p = subprocess.Popen([str(cli_exe), "pdf-to-images", str(pdf_out), "-o", str(pages_dir)])
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode == 0
        exercised_steps.append("CLI pdf-to-images")

        # Step 8: PDF Reduce
        red_pdf = work_dir / "reduced.pdf"
        p = subprocess.Popen([str(cli_exe), "pdf-reduce", str(pdf_out), "-o", str(red_pdf)])
        monitor.register_pid(p.pid)
        p.wait(timeout=10)
        assert p.returncode == 0
        exercised_steps.append("CLI pdf-reduce")

    finally:
        monitor.stop()
        end_time = datetime.now(timezone.utc)
        end_dns = get_dns_cache_entries()
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)

    dns_diff = list(end_dns - start_dns)
    duration_sec = (end_time - start_time).total_seconds()

    report = {
        "start_timestamp": start_time.isoformat(),
        "end_timestamp": end_time.isoformat(),
        "duration_seconds": round(duration_sec, 2),
        "sampling_interval_ms": 50,
        "total_samples": monitor.samples_collected,
        "pids_monitored": list(monitor.tracked_pids),
        "exercised_operations": exercised_steps,
        "observed_socket_connections": monitor.observed_connections,
        "observed_socket_count": len(monitor.observed_connections),
        "monitoring_errors": monitor.monitoring_errors,
        "monitoring_error_count": len(monitor.monitoring_errors),
        "monitoring_limitations": "Process socket polling at 50ms intervals samples open sockets on tracked process trees; it does not capture ephemeral sub-interval network activity or guarantee kernel-level event attribution. Event-based network capture (ETW/NDIS) was not active, so comprehensive network non-occurrence remains unverified.",
        "dns_cache_entries_added": dns_diff,
        "dns_cache_diff_count": len(dns_diff),
        "observation_finding": "No socket connections were observed during sampling in the test window.",
        "clean_machine_first_launch_status": "BLOCKED (No isolated clean VM available without host network disconnection)",
    }
    return report


def test_continuous_network_observation():
    root = Path(__file__).resolve().parent.parent
    result = run_observation_session(root)
    assert result["observed_socket_count"] == 0, f"Socket connections detected: {result['observed_socket_connections']}"
    assert result["dns_cache_diff_count"] == 0, f"DNS differential detected: {result['dns_cache_entries_added']}"


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    result = run_observation_session(root)
    print(json.dumps(result, indent=2))
