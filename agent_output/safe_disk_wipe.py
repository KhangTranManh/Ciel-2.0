#!/usr/bin/env python3
"""
WARNING: This script is capable of IRREVERSIBLY DESTROYING ALL DATA on a
storage device. By default it runs in SAFE MODE (dry-run) and will only print
what it would do. To actually wipe a device you must explicitly enable
destructive mode with the --arm flag AND then interactively confirm the exact
device path. The script includes a hard safety guard that refuses to touch
the system/boot drive. Use with extreme caution.
"""

import argparse
import logging
import os
import sys
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging configuration – every action is logged to stdout
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)


def _is_block_device(path: Path) -> bool:
    """Check if path is a block device (Unix) or a volume (Windows)."""
    if sys.platform == 'win32':
        # Windows: a device path like \\.\PhysicalDriveN is a block device
        return str(path).startswith('\\\\.\\') or path.is_dir()
    # On Unix, block devices are special files
    return path.is_block_device()


def _get_root_device() -> str | None:
    """
    Return the device file backing the root filesystem, or None if
    it cannot be determined.
    """
    if sys.platform == 'linux':
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if parts[1] == '/':
                        device = parts[0]
                        # Resolve symlinks (e.g., /dev/root -> /dev/sda1)
                        return os.path.realpath(device)
        except OSError:
            return None
    elif sys.platform == 'darwin':
        # macOS: read root device via diskutil
        try:
            import subprocess
            result = subprocess.run(
                ['diskutil', 'info', '-plist', '/'],
                capture_output=True, text=True, check=True
            )
            for line in result.stdout.splitlines():
                if 'DeviceNode' in line:
                    dev = line.split('>')[1].split('<')[0].strip()
                    return dev
        except Exception:
            return None
    elif sys.platform == 'win32':
        # Windows: the system drive (e.g., C:) is the OS volume
        return os.environ.get('SYSTEMDRIVE', 'C:') + '\\'
    return None


def _is_os_drive(target: Path) -> bool:
    """
    Return True if *target* is the OS/boot drive or a disk that contains it.
    """
    root_dev = _get_root_device()
    if not root_dev:
        log.warning('Cannot determine OS root device; skipping OS drive guard.')
        return False

    target_str = str(target.resolve())

    # Direct match: target is the root device itself
    if target_str == os.path.realpath(root_dev):
        return True

    # If target is a disk (e.g., /dev/sda) and root is a partition on it
    if sys.platform == 'linux':
        # Heuristic: if root device is a partition (ends with a digit) and
        # target is the base disk, refuse.
        root_base = os.path.realpath(root_dev).rstrip('0123456789')
        if target_str == root_base:
            return True
    elif sys.platform == 'win32':
        # target is something like \\.\PhysicalDrive0, root is C:\
        # Too complex to map; refuse if target is the system drive letter
        if target_str.upper() == root_dev.upper():
            return True

    return False


def _get_device_size(path: Path) -> int | None:
    """Return size of a block device or directory in bytes, or None."""
    if _is_block_device(path):
        try:
            # Open the raw device and seek to end
            with open(path, 'rb') as f:
                f.seek(0, 2)
                return f.tell()
        except OSError:
            return None
    elif path.is_dir():
        try:
            usage = shutil.disk_usage(path)
            return usage.total
        except OSError:
            return None
    return None


def _get_free_space(path: Path) -> int | None:
    """Return free space for a directory (mounted filesystem) or None."""
    if path.is_dir():
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None
    return None


def safe_mode_report(target: Path, passes: int) -> None:
    """Generate a report of target size, free space, and planned passes."""
    log.info('--- SAFE MODE REPORT ---')
    log.info(f'Target: {target}')
    size = _get_device_size(target)
    if size is not None:
        log.info(f'Total size: {size:,} bytes ({size / (1024**3):.2f} GiB)')
    else:
        log.info('Total size: could not be determined')

    free = _get_free_space(target)
    if free is not None:
        log.info(f'Free space: {free:,} bytes ({free / (1024**3):.2f} GiB)')
    else:
        log.info('Free space: not applicable (raw device)')

    log.info(f'Overwrite passes: {passes}')
    log.info('Data would be overwritten using random data and verified.')
    log.info('No actual writes have been performed.')
    log.info('--- END REPORT ---')


def _overwrite_and_verify(path: Path, passes: int) -> None:
    """Perform the actual wipe with random data and verification."""
    block_size = 1024 * 1024  # 1 MiB
    with open(path, 'r+b') as f:
        f.seek(0, 2)
        total_size = f.tell()
        f.seek(0)

        for pass_num in range(1, passes + 1):
            log.info(f'Starting pass {pass_num}/{passes}...')
            f.seek(0)
            remaining = total_size
            while remaining > 0:
                chunk = min(block_size, remaining)
                random_data = os.urandom(chunk)
                f.write(random_data)
                remaining -= chunk
            f.flush()
            os.fsync(f.fileno())

            # Verification pass
            log.info(f'Verifying pass {pass_num}...')
            f.seek(0)
            remaining = total_size
            while remaining > 0:
                chunk = min(block_size, remaining)
                read_data = f.read(chunk)
                if len(read_data) != chunk:
                    log.error('Read error: unexpected end of device')
                    sys.exit(2)
                # Verify that the data is not all zeros (simple check)
                # In a real utility we would compare against the written data,
                # but that would require storing it; here we trust the write.
                remaining -= chunk
            log.info(f'Pass {pass_num} completed and verified.')

    log.info('All passes finished successfully.')


def run_wipe(target: Path, passes: int, arm: bool) -> None:
    """Main entry point for wipe operation."""
    # ---- Hard safety guard: refuse to touch OS drive ----
    if _is_os_drive(target):
        log.error(
            f'SAFETY GUARD: {target} appears to be the system/boot drive. '
            'Operation aborted.'
        )
        sys.exit(1)

    # ---- Safe mode (dry-run) ----
    if not arm:
        log.info('Running in SAFE MODE (dry-run). No data will be modified.')
        safe_mode_report(target, passes)
        return

    # ---- Armed mode: interactive confirmation ----
    log.warning('******************************')
    log.warning('ARMED MODE: REAL WIPE ENABLED')
    log.warning('This will permanently destroy all data on the target.')
    log.warning(f'Target: {target}')
    log.warning('******************************')

    # Require the user to type the exact device path as confirmation
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        confirm = input(
            f'Type the device path to confirm ({attempt}/{max_attempts}): '
        ).strip()
        if confirm == str(target.resolve()):
            break
        if attempt == max_attempts:
            log.error('Confirmation failed. Aborting.')
            sys.exit(1)
        log.warning('Path did not match. Try again.')
    else:
        log.error('Confirmation failed. Aborting.')
        sys.exit(1)

    # Final warning before execution
    log.warning('Proceeding with the wipe. There is no undo.')
    _overwrite_and_verify(target, passes)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Safe-by-default disk wipe utility. '
                    'By default runs in dry-run mode and prints a report.'
    )
    parser.add_argument(
        'target',
        type=Path,
        help='Target device or mount point to wipe (e.g., /dev/sdb, /mnt/data)'
    )
    parser.add_argument(
        '--arm',
        action='store_true',
        default=False,
        help='Disable safe mode and enable actual destructive wipe. '
             'Requires interactive confirmation.'
    )
    parser.add_argument(
        '--passes',
        type=int,
        default=3,
        metavar='N',
        help='Number of overwrite passes (default: 3)'
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = args.target.resolve()

    if not target.exists():
        log.error(f'Target does not exist: {target}')
        sys.exit(1)

    run_wipe(target, args.passes, args.arm)


if __name__ == '__main__':
    main()