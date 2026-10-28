from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import MutableMapping


def detect_java_home(env: MutableMapping[str, str] | None = None) -> str | None:
    """Return a usable JAVA_HOME value from env or the java executable path."""

    env = env or os.environ
    configured = str(env.get("JAVA_HOME") or "").strip()
    if configured:
        return configured

    java_path = shutil.which("java")
    if java_path:
        executable = Path(java_path).resolve()
        if executable.parent.name == "bin":
            java_home = executable.parent.parent
            if (java_home / "bin" / "java").exists():
                return str(java_home)

    for candidate in (
        Path("/usr/lib/jvm/default-java"),
        Path("/usr/lib/jvm/java-21-openjdk-amd64"),
        Path("/usr/lib/jvm/java-17-openjdk-amd64"),
        Path("/usr/lib/jvm/java-11-openjdk-amd64"),
    ):
        if (candidate / "bin" / "java").exists():
            return str(candidate)
    return None


def ensure_java_home(env: MutableMapping[str, str] | None = None) -> str | None:
    """Populate JAVA_HOME when it can be inferred."""

    env = env or os.environ
    java_home = detect_java_home(env)
    if java_home and not str(env.get("JAVA_HOME") or "").strip():
        env["JAVA_HOME"] = java_home
    return java_home
