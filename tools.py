"""
tools.py — LangChain tools for file system operations, terminal commands, and grep.

Safety:
  - File operations are sandboxed to a configurable WORKSPACE directory.
    Any path that escapes it (via ".." or symlinks) is rejected.
  - Terminal commands are checked against a blocklist of destructive patterns
    (rm -rf /, format, shutdown, etc.).
  - Delete operations require the target to live inside the workspace.

Ref: https://docs.langchain.com/oss/python/langchain/tools
"""

import os
import re
import subprocess
from pathlib import Path

from langchain.tools import tool

# ---------------------------------------------------------------------------
# Workspace sandbox — all file operations are confined here.
# Set the TOOL_WORKSPACE env-var to override, otherwise defaults to cwd.
# ---------------------------------------------------------------------------
WORKSPACE = Path(os.getenv("TOOL_WORKSPACE", os.getcwd())).resolve()

# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def _safe_path(raw: str) -> Path:
    """Resolve *raw* against WORKSPACE and verify it stays inside it."""
    target = (WORKSPACE / raw).resolve()
    if not str(target).startswith(str(WORKSPACE)):
        raise PermissionError(
            f"Access denied — path escapes the workspace: {raw}"
        )
    return target


_BLOCKED_PATTERNS = [
    r"rm\s+.*-\s*r\s*f.*\/",       # rm -rf /
    r"mkfs\b",                       # format filesystem
    r"dd\s+if=",                     # raw disk write
    r"format\s+[a-zA-Z]:",          # Windows format drive
    r"\bshutdown\b",                 # power off
    r"\breboot\b",                   # restart
    r"\bhalt\b",                     # halt system
    r"\binit\s+[06]\b",             # SysV runlevel 0 or 6
    r">\s*/dev/sd",                  # overwrite disk device
    r"del\s+/s\s+/q\s+[cC]:\\",     # Windows recursive delete C:\
    r":\(\)\{.*\}",                  # fork bomb
]
BLOCKED_COMMANDS = re.compile("|".join(_BLOCKED_PATTERNS), re.IGNORECASE)

BLOCKED_WRITE_PATTERNS = re.compile(
    r"""
      /etc/passwd
      | /etc/shadow
      | /etc/sudoers
      | \.ssh/authorized_keys
      | \.bashrc
      | \.bash_profile
      | \.profile
      | \.zshrc
      | system32
      | boot\.ini
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _check_command(cmd: str) -> None:
    if BLOCKED_COMMANDS.search(cmd):
        raise PermissionError(f"Blocked — potentially destructive command: {cmd}")


def _check_write_path(p: Path) -> None:
    if BLOCKED_WRITE_PATTERNS.search(str(p)):
        raise PermissionError(f"Blocked — writing to sensitive path: {p}")


# ---------------------------------------------------------------------------
# Tool: read_file
# ---------------------------------------------------------------------------

@tool
def read_file(path: str) -> str:
    """Read and return the full contents of a file.

    Args:
        path: Relative or absolute path to the file (resolved inside workspace).
    """
    target = _safe_path(path)
    if not target.is_file():
        return f"Error: '{path}' does not exist or is not a file."
    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# Tool: write_file
# ---------------------------------------------------------------------------

@tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with the given content.

    Args:
        path: Relative or absolute path (resolved inside workspace).
        content: Text to write into the file.
    """
    target = _safe_path(path)
    _check_write_path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} chars to {target.relative_to(WORKSPACE)}."
    except Exception as e:
        return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# Tool: edit_file
# ---------------------------------------------------------------------------

@tool
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace the first occurrence of old_text with new_text in a file.

    Args:
        path: Relative or absolute path (resolved inside workspace).
        old_text: Exact text to find in the file.
        new_text: Replacement text.
    """
    target = _safe_path(path)
    _check_write_path(target)
    if not target.is_file():
        return f"Error: '{path}' does not exist or is not a file."
    try:
        original = target.read_text(encoding="utf-8")
        if old_text not in original:
            return "Error: old_text not found in the file — no changes made."
        updated = original.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        return f"Replaced first occurrence in {target.relative_to(WORKSPACE)}."
    except Exception as e:
        return f"Error editing file: {e}"


# ---------------------------------------------------------------------------
# Tool: delete_file
# ---------------------------------------------------------------------------

@tool
def delete_file(path: str) -> str:
    """Delete a file from the workspace.

    Args:
        path: Relative or absolute path (resolved inside workspace).
    """
    target = _safe_path(path)
    _check_write_path(target)
    if not target.exists():
        return f"Error: '{path}' does not exist."
    if target.is_dir():
        return "Error: path is a directory — only files can be deleted with this tool."
    try:
        target.unlink()
        return f"Deleted {target.relative_to(WORKSPACE)}."
    except Exception as e:
        return f"Error deleting file: {e}"


# ---------------------------------------------------------------------------
# Tool: run_terminal
# ---------------------------------------------------------------------------

@tool
def run_terminal(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return its combined stdout + stderr.

    Args:
        command: The shell command to run.
        timeout: Max seconds to wait (default 30). Long-running commands are killed after this.
    """
    _check_command(command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(WORKSPACE),
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n--- stderr ---\n" + result.stderr) if output else result.stderr
        if not output:
            output = "(no output)"
        return f"[exit {result.returncode}]\n{output}"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s."
    except Exception as e:
        return f"Error running command: {e}"


# ---------------------------------------------------------------------------
# Tool: grep_search
# ---------------------------------------------------------------------------

@tool
def grep_search(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search for a regex pattern in files under the given directory.

    Uses ripgrep (rg) if available, otherwise falls back to a recursive Python search.

    Args:
        pattern: Regex pattern to search for.
        path: Directory to search in (relative to workspace). Defaults to workspace root.
        file_glob: Optional glob to filter files (e.g. '*.py', '*.txt').
    """
    search_dir = _safe_path(path)
    if not search_dir.is_dir():
        return f"Error: '{path}' is not a directory."

    rg_cmd = ["rg", "--no-heading", "--line-number", "--max-count", "50", pattern]
    if file_glob:
        rg_cmd.extend(["--glob", file_glob])
    rg_cmd.append(str(search_dir))

    try:
        result = subprocess.run(
            rg_cmd,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(WORKSPACE),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if result.returncode == 1:
            return "No matches found."
        # rg not installed or other error — fall through to Python search
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return "Error: search timed out."

    # Fallback: pure-Python recursive search
    compiled = re.compile(pattern)
    matches: list[str] = []
    glob_pattern = file_glob if file_glob else "*"
    for file in search_dir.rglob(glob_pattern):
        if not file.is_file():
            continue
        try:
            for i, line in enumerate(file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if compiled.search(line):
                    rel = file.relative_to(WORKSPACE)
                    matches.append(f"{rel}:{i}:{line}")
                    if len(matches) >= 50:
                        matches.append("... (truncated at 50 matches)")
                        return "\n".join(matches)
        except Exception:
            continue
    return "\n".join(matches) if matches else "No matches found."


# ---------------------------------------------------------------------------
# Export all tools as a list for easy import.
# ---------------------------------------------------------------------------
ALL_TOOLS = [
    read_file,
    write_file,
    edit_file,
    delete_file,
    run_terminal,
    grep_search,
]
