"""Persistent local configuration, independent of production credentials."""

import os
from pathlib import Path
import secrets

from dotenv import load_dotenv


def prepare_local_environment(root):
    root = Path(root)
    load_dotenv(root / ".env")
    if os.environ.get("DJANGO_SECRET_KEY", "").strip():
        return
    directory = root / ".local"
    directory.mkdir(exist_ok=True)
    key_path = directory / "secret-key"
    try:
        # Exclusive creation preserves the key across server restarts.
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(secrets.token_urlsafe(64))
    key = key_path.read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise RuntimeError("The local signing key is invalid. Restore .local/secret-key or set DJANGO_SECRET_KEY in .env.")
    os.environ["DJANGO_SECRET_KEY"] = key
