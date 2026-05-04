"""OS-level package detection and listing.

Supports:
- dpkg/apt (Debian, Ubuntu)
- brew (macOS)
- rpm (RHEL, CentOS, Fedora)
- pacman (Arch Linux)
- apk (Alpine Linux)

On Windows, OS package detection is limited - we note that and move on.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess

from config_detective.snapshot.models import OSPackage, PackageManager


def _run_command(cmd: list[str], timeout: int = 30) -> tuple[str, bool]:
    """Run a shell command and return (stdout, success)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout, result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", False


def _detect_package_manager() -> PackageManager:
    """Detect which package manager is available on this system."""
    system = platform.system().lower()

    if system == "windows":
        return PackageManager.UNKNOWN

    # Check for common package managers in order of preference
    if shutil.which("dpkg"):
        return PackageManager.DPKG
    if shutil.which("apt"):
        return PackageManager.APT
    if shutil.which("brew"):
        return PackageManager.BREW
    if shutil.which("rpm"):
        return PackageManager.RPM
    if shutil.which("pacman"):
        return PackageManager.PACMAN

    return PackageManager.UNKNOWN


def _parse_dpkg_output(output: str) -> list[OSPackage]:
    """Parse output of `dpkg -l`.

    Format:
    ii  package-name   1.2.3-4   amd64   Description here
    """
    packages: list[OSPackage] = []

    for line in output.splitlines():
        # Skip header lines and malformed lines
        if not line.startswith("ii ") and not line.startswith("hi "):
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        # parts[0] = status (ii/hi)
        # parts[1] = package name
        # parts[2] = version
        # parts[3] = architecture
        # rest = description
        name = parts[1]
        # Handle architecture suffix like "libc6:amd64"
        if ":" in name:
            name = name.split(":")[0]

        version = parts[2]
        arch = parts[3] if len(parts) > 3 else None
        description = " ".join(parts[4:]) if len(parts) > 4 else None

        packages.append(
            OSPackage(
                name=name,
                version=version,
                architecture=arch,
                description=description,
            )
        )

    return packages


def _parse_brew_output(output: str) -> list[OSPackage]:
    """Parse output of `brew list --versions`.

    Format:
    package-name 1.2.3
    package-name 1.2.3 1.2.4  (multiple versions)
    """
    packages: list[OSPackage] = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        name = parts[0]
        # Take the last version if multiple are installed
        version = parts[-1]

        packages.append(OSPackage(name=name, version=version))

    return packages


def _parse_rpm_output(output: str) -> list[OSPackage]:
    """Parse output of `rpm -qa --queryformat`.

    We use a custom format: name|version|arch
    """
    packages: list[OSPackage] = []

    for line in output.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue

        parts = line.split("|")
        if len(parts) >= 2:
            packages.append(
                OSPackage(
                    name=parts[0],
                    version=parts[1],
                    architecture=parts[2] if len(parts) > 2 else None,
                )
            )

    return packages


def _parse_pacman_output(output: str) -> list[OSPackage]:
    """Parse output of `pacman -Q`.

    Format:
    package-name 1.2.3-4
    """
    packages: list[OSPackage] = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) >= 2:
            packages.append(OSPackage(name=parts[0], version=parts[1]))

    return packages


def _parse_apk_output(output: str) -> list[OSPackage]:
    """Parse output of `apk info -v`.

    Format:
    package-name-1.2.3-r4
    """
    packages: list[OSPackage] = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        # Alpine package names can contain hyphens, version starts after last hyphen
        # before a digit. Example: musl-1.2.3-r4, openssl-libs-3.0.8-r0
        match = re.match(r"^(.+)-(\d[^-]*(?:-r\d+)?)$", line)
        if match:
            packages.append(OSPackage(name=match.group(1), version=match.group(2)))
        else:
            # Fallback: just use the whole line as name
            packages.append(OSPackage(name=line, version="unknown"))

    return packages


def capture_os_packages() -> tuple[list[OSPackage], PackageManager]:
    """Capture installed OS-level packages.

    Returns:
        Tuple of (list of packages, detected package manager)
    """
    pkg_manager = _detect_package_manager()

    if pkg_manager == PackageManager.DPKG:
        output, success = _run_command(["dpkg", "-l"])
        if success:
            return _parse_dpkg_output(output), pkg_manager

    elif pkg_manager == PackageManager.APT:
        # apt list --installed is slower but more accurate
        # Fall back to dpkg which is always present on apt systems
        output, success = _run_command(["dpkg", "-l"])
        if success:
            return _parse_dpkg_output(output), PackageManager.DPKG

    elif pkg_manager == PackageManager.BREW:
        output, success = _run_command(["brew", "list", "--versions"])
        if success:
            return _parse_brew_output(output), pkg_manager

    elif pkg_manager == PackageManager.RPM:
        output, success = _run_command(
            ["rpm", "-qa", "--queryformat", "%{NAME}|%{VERSION}|%{ARCH}\n"]
        )
        if success:
            return _parse_rpm_output(output), pkg_manager

    elif pkg_manager == PackageManager.PACMAN:
        output, success = _run_command(["pacman", "-Q"])
        if success:
            return _parse_pacman_output(output), pkg_manager

    # Also check for apk (Alpine) even if we didn't detect it initially
    if shutil.which("apk"):
        output, success = _run_command(["apk", "info", "-v"])
        if success:
            return _parse_apk_output(output), PackageManager.UNKNOWN

    # No package manager found or command failed
    return [], PackageManager.UNKNOWN
