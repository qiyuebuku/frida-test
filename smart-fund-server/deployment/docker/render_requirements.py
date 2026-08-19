"""Render container dependencies while replacing the local jettask wheel URL."""

from pathlib import Path
import tomllib


project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
requirements = [
    item
    for item in project["dependencies"]
    if not item.lower().startswith("jettask-python")
]
Path("/tmp/requirements.txt").write_text(
    "\n".join(requirements) + "\n",
    encoding="utf-8",
)
