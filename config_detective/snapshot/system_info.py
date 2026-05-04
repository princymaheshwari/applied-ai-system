"""System-level information detection.

Captures:
- OS type and release (Linux, macOS, Windows)
- Kernel version
- CPU architecture (x86_64, arm64, etc.)
- C library type (glibc vs musl - critical for binary compatibility)
- Hostname
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path

from config_detective.snapshot.models import LibcType, OSType, SystemInfo


def _run_command(cmd: list[str], timeout: int = 5) -> str | None:
    """Run a command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _read_file_safely(path: str | Path) -> str | None:
    """Read a file's contents, returning None on any error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def detect_os_type() -> OSType:
    """Detect the operating system type."""
    system = platform.system().lower()
    if system == "linux":
        return OSType.LINUX
    elif system == "darwin":
        return OSType.DARWIN
    elif system == "windows":
        return OSType.WINDOWS
    else:
        return OSType.UNKNOWN


def detect_os_release() -> str | None:
    """Detect the OS release/distribution name.

    Returns strings like:
    - "Ubuntu 22.04.3 LTS"
    - "macOS 14.0"
    - "Windows 10"
    - "Alpine Linux 3.18"
    """
    system = platform.system().lower()

    if system == "linux":
        # Try /etc/os-release (standard on modern Linux)
        os_release = _read_file_safely("/etc/os-release")
        if os_release:
            # Parse PRETTY_NAME or NAME + VERSION
            for line in os_release.splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip('"')

            # Fall back to NAME + VERSION_ID
            name = None
            version = None
            for line in os_release.splitlines():
                if line.startswith("NAME="):
                    name = line.split("=", 1)[1].strip('"')
                elif line.startswith("VERSION_ID="):
                    version = line.split("=", 1)[1].strip('"')
            if name:
                return f"{name} {version}" if version else name

        # Try lsb_release
        lsb = _run_command(["lsb_release", "-ds"])
        if lsb:
            return lsb.strip('"')

        # Fall back to uname
        return platform.release()

    elif system == "darwin":
        # macOS
        version = platform.mac_ver()[0]
        return f"macOS {version}" if version else "macOS"

    elif system == "windows":
        # Windows
        version = platform.version()
        release = platform.release()
        return f"Windows {release}" if release else f"Windows {version}"

    return None


def detect_kernel_version() -> str | None:
    """Detect the kernel version."""
    system = platform.system().lower()

    if system == "linux":
        # uname -r gives kernel version
        return _run_command(["uname", "-r"]) or platform.release()

    elif system == "darwin":
        # Darwin kernel version
        return _run_command(["uname", "-r"]) or platform.release()

    elif system == "windows":
        # Windows build number
        return platform.version()

    return platform.release()


def detect_architecture() -> str:
    """Detect CPU architecture."""
    machine = platform.machine().lower()

    # Normalize common variants
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    elif machine in ("aarch64", "arm64"):
        return "arm64"
    elif machine in ("i386", "i686", "x86"):
        return "x86"
    elif machine.startswith("armv"):
        return machine  # armv7l, armv6l, etc.

    return machine or "unknown"


def detect_libc() -> tuple[LibcType, str | None]:
    """Detect the C library type and version.

    This is critical for binary compatibility - binaries compiled against
    glibc won't work on musl (Alpine) and vice versa.

    Returns:
        Tuple of (LibcType, version_string)
    """
    system = platform.system().lower()

    if system == "windows":
        # Windows uses MSVCRT/UCRT, not glibc/musl
        return LibcType.UNKNOWN, None

    if system == "darwin":
        # macOS uses libSystem which includes libc
        return LibcType.UNKNOWN, None

    # Linux - need to detect glibc vs musl

    # Method 1: Check ldd output
    ldd_output = _run_command(["ldd", "--version"])
    if ldd_output:
        if "musl" in ldd_output.lower():
            # musl ldd output: "musl libc (x86_64)\nVersion 1.2.3"
            match = re.search(r"Version\s+(\d+\.\d+(?:\.\d+)?)", ldd_output)
            version = match.group(1) if match else None
            return LibcType.MUSL, version
        elif "glibc" in ldd_output.lower() or "gnu" in ldd_output.lower():
            # glibc ldd output: "ldd (GNU libc) 2.35"
            match = re.search(r"(\d+\.\d+(?:\.\d+)?)", ldd_output)
            version = match.group(1) if match else None
            return LibcType.GLIBC, version

    # Method 2: Check for musl-specific paths
    if Path("/lib/ld-musl-x86_64.so.1").exists() or Path("/lib/ld-musl-aarch64.so.1").exists():
        # Try to get version from the library
        for musl_path in ["/lib/ld-musl-x86_64.so.1", "/lib/ld-musl-aarch64.so.1"]:
            if Path(musl_path).exists():
                version_output = _run_command([musl_path])
                if version_output:
                    match = re.search(r"Version\s+(\d+\.\d+(?:\.\d+)?)", version_output)
                    return LibcType.MUSL, match.group(1) if match else None
        return LibcType.MUSL, None

    # Method 3: Check /etc/os-release for Alpine
    os_release = _read_file_safely("/etc/os-release")
    if os_release and "alpine" in os_release.lower():
        return LibcType.MUSL, None

    # Method 4: Try to get glibc version via ctypes
    try:
        libc = ctypes.CDLL("libc.so.6")
        gnu_get_libc_version = libc.gnu_get_libc_version
        gnu_get_libc_version.restype = ctypes.c_char_p
        version = gnu_get_libc_version().decode("utf-8")
        return LibcType.GLIBC, version
    except (OSError, AttributeError):
        pass

    # Default assumption for most Linux distros
    return LibcType.GLIBC, None


def detect_hostname() -> str | None:
    """Detect the system hostname."""
    try:
        return socket.gethostname()
    except OSError:
        return None


def capture_system_info() -> SystemInfo:
    """Capture all system-level information."""
    libc_type, libc_version = detect_libc()

    return SystemInfo(
        os_type=detect_os_type(),
        os_release=detect_os_release(),
        kernel=detect_kernel_version(),
        architecture=detect_architecture(),
        libc_type=libc_type,
        libc_version=libc_version,
        hostname=detect_hostname(),
    )
