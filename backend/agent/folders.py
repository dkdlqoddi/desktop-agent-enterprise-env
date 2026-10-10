"""Removing the private scratch folders of imports (Office pictures, file copies)."""

import contextlib
import os
import shutil
import stat
import sys
import tempfile
import time

# The folders the importers make in the temporary folder. They can hold pictures, copies of the files being read and
# a whole Excel sheet as a web page.
SCRATCH_PREFIXES = ("private-agent-office-", "private-agent-pictures-")


def remove_tree(path, seconds=2):
    """Delete a scratch folder. Windows refuses to delete a read-only file, and one that another program (antivirus,
    an Office app closing it) still holds for a moment: such a file is made writable and tried again for `seconds`.
    What still fails is left, as before."""
    deadline = time.monotonic() + seconds

    def retry(function, name, _):
        if function not in (os.unlink, os.remove, os.rmdir):
            return  # A folder that cannot be listed holds nothing to delete.
        with contextlib.suppress(OSError):
            os.chmod(name, stat.S_IWRITE)
        while True:
            try:
                function(name)
                return
            except FileNotFoundError:
                return
            except OSError:
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.1)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def sweep_scratch(directory=None, older_than=600, now=None):
    """Remove the scratch folders earlier runs left behind: the app was ended before an import could clean up, an
    Office app still wrote into a folder, or antivirus held a file past the wait. One younger than `older_than` seconds
    may still belong to a backend that is closing, and stays. The names of the folders removed."""
    root = directory or tempfile.gettempdir()
    now = time.time() if now is None else now
    removed = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith(SCRATCH_PREFIXES):
            continue
        try:
            if (
                not entry.is_dir(follow_symlinks=False)
                or entry.is_symlink()
                or now - entry.stat(follow_symlinks=False).st_mtime < older_than
            ):
                continue
            remove_tree(entry.path)
        except OSError:
            continue
        if not os.path.lexists(entry.path):
            removed.append(entry.name)
    return removed
