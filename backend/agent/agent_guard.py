"""What the chat's agent is kept away from (보호 구역), and what it may run without asking.

The agent works on the user's PC with a terminal and file tools, under the user's own account, so nothing here is a
sandbox: these are the paths its file tools refuse and its commands may not name (runtime/hermes/worker.py: protect,
guard_files, denied_commands). They are a second line behind the approvals, against an agent that was talked into
reading a key file or into editing the app's own data behind its back.

Three kinds of path are protected:

- the app's own: the Wiki's database and its backups, and the copies kept of Office originals (documents are read
  and written through the app's tools, which keep the trash and the search index right), and the app's settings
  folder, which the main process names (agent.configure). The rest of the Wiki folder stays open: the agent's own
  home, and what the user may hand to it (the CSV files they exported, the screenshots of the web records).
- the places credentials are kept in the user's home (SSH and GPG keys, cloud and registry logins, the browsers'
  profiles), unless the user turned that off.
- the folders the user added.
"""

import os
import sys
from pathlib import Path

from .storage import BACKUPS, SIDECARS
from .writeback import BACKUPS as ORIGINALS

# Folders and files of the user's home that hold keys and logins.
CREDENTIALS = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".config/gh",
    ".config/gcloud",
    ".password-store",
    ".local/share/keyrings",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".pgpass",
)
# The browsers' own profiles (cookies and saved logins) and the system's key stores, by platform.
PROFILES = {
    "linux": (
        ".config/google-chrome",
        ".config/chromium",
        ".config/microsoft-edge",
        ".config/BraveSoftware",
        ".mozilla/firefox",
    ),
    "darwin": (
        "Library/Application Support/Google/Chrome",
        "Library/Application Support/Microsoft Edge",
        "Library/Application Support/BraveSoftware",
        "Library/Application Support/Firefox/Profiles",
        "Library/Keychains",
        "Library/Cookies",
    ),
    "win32": (
        "AppData/Local/Google/Chrome/User Data",
        "AppData/Local/Microsoft/Edge/User Data",
        "AppData/Local/BraveSoftware",
        "AppData/Roaming/Mozilla/Firefox/Profiles",
        "AppData/Local/Microsoft/Credentials",
        "AppData/Roaming/Microsoft/Credentials",
        "AppData/Roaming/Microsoft/Protect",
    ),
}
# What the app keeps for itself in the Wiki folder, beside the database and SQLite's files next to it.
WIKI_OWN = (BACKUPS, ORIGINALS)
MAX_ZONES = 400
MAX_FOLDERS = 50
MAX_BLOCKED = 50
MAX_ALLOWED = 200
PATH_CHARS = 1024
COMMAND_CHARS = 1000
# How deep a protected folder is opened up to leave the agent's own folders reachable inside it.
DEPTH = 12


def real(path):
    return os.path.realpath(str(path))


def under(path, root):
    """Whether `path` is `root` or lies inside it (both real paths)."""
    path, root = os.path.normcase(path), os.path.normcase(root)
    return path == root or path.startswith(root.rstrip("/\\") + os.sep)


def absolute(values, limit):
    """The absolute paths among `values`, each once and at most `limit`; anything else is left out."""
    kept = []
    for value in values if isinstance(values, (list, tuple)) else ():
        if not isinstance(value, str) or not 0 < len(value) <= PATH_CHARS or "\x00" in value:
            continue
        if os.path.isabs(value) and value not in kept:
            kept.append(value)
    return kept[:limit]


def lines(values, limit, chars):
    """Short single-line texts among `values` (commands, patterns), each once and at most `limit`."""
    kept = []
    for value in values if isinstance(values, (list, tuple)) else ():
        if not isinstance(value, str):
            continue
        value = value.strip()
        if 0 < len(value) <= chars and not any(ord(char) < 32 or ord(char) == 127 for char in value):
            if value not in kept:
                kept.append(value)
    return kept[:limit]


def credential_paths(home=None, platform=None):
    """Where keys and logins are kept under the user's home (the real one: the agent's own HOME is its folder in
    the Wiki)."""
    home = Path(home) if home else Path.home()
    names = CREDENTIALS + PROFILES.get(platform or sys.platform, PROFILES["linux"])
    return [str(home.joinpath(*name.split("/"))) for name in names]


def zones(roots, keep):
    """The protected paths: `roots`, each as its real path, around what must stay reachable (`keep`: the agent's
    home and the folder it works in). A root that holds a kept folder, or is one, is replaced by what lies in it
    beside the way to the kept folders: so the app's own folder is protected around the agent's home, and the Wiki's
    files stay closed even where the user made the Wiki folder the agent's work folder. A root inside a kept folder
    is protected as it is (a key folder inside the work folder stays closed). Sorted, so that the same folders give
    the same list."""
    keep = [real(path) for path in keep if path]
    found = set()

    def add(path, depth):
        if len(found) >= MAX_ZONES:
            return
        if not any(under(kept, path) for kept in keep) or depth >= DEPTH:
            found.add(path)
            return
        # A kept folder met on the way down is the agent's own: it stays open, with all that is in it.
        if depth and any(os.path.normcase(kept) == os.path.normcase(path) for kept in keep):
            return
        try:
            names = sorted(os.listdir(path))
        except OSError:
            return
        for name in names:
            add(os.path.join(path, name), depth + 1)

    for root in roots:
        if root:
            path = real(root)
            # The root of a drive is never a zone: everything would be.
            if os.path.dirname(path) != path:
                add(path, 0)
    return sorted(found)


def protected(wiki, home, workspace, settings):
    """The paths a turn's agent may not touch, from where the app keeps its things and what the user set
    (`settings`: {"app": [the app's own folders], "credentials": bool, "folders": [the user's]}). The app's and the
    credentials' are taken first: no more than MAX_ZONES paths are kept. The Wiki folder itself stays open around
    what the app keeps in it, also where the app's settings folder holds the Wiki folder."""
    wiki = Path(wiki)
    database = wiki / "wiki.db"
    # By name: the files beside the database and the backup folders may not be there yet when the turn starts.
    roots = [str(database), *(str(database) + suffix for suffix in SIDECARS), *(str(wiki / name) for name in WIKI_OWN)]
    roots += absolute(settings.get("app"), 8)
    if settings.get("credentials", True):
        roots += credential_paths()
    roots += absolute(settings.get("folders"), MAX_FOLDERS)
    return zones(roots, [str(home), workspace, str(wiki)])
