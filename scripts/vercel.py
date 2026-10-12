"""Run the project's installed Vercel CLI without a global Node.js installation."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".local" / "deployment"


def main():
    metadata = TOOLS / "toolchain.json"
    cli = TOOLS / "vercel-cli" / "node_modules" / "vercel" / "dist" / "index.js"
    if not metadata.is_file() or not cli.is_file():
        raise SystemExit("Project deployment tools are missing. See docs/VERCEL.md.")
    node_dir = (ROOT / json.loads(metadata.read_text())["node_dir"]).resolve()
    if not node_dir.is_relative_to(TOOLS.resolve()):
        raise SystemExit("The deployment interpreter must be inside this project.")
    env = os.environ.copy()
    env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    env["VERCEL_TELEMETRY_DISABLED"] = "1"
    env["NO_UPDATE_NOTIFIER"] = "1"
    env["npm_config_cache"] = str(TOOLS / "npm-cache")
    env["XDG_CACHE_HOME"] = str(TOOLS / "cache")
    temporary = TOOLS / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env.update({"TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary)})
    command = [
        str(node_dir / "node.exe"), str(cli),
        "--global-config", str(TOOLS / "vercel-auth"),
        *sys.argv[1:],
    ]
    try:
        return subprocess.call(command, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
