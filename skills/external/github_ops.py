import os
import subprocess
from pathlib import Path
from langchain_core.tools import StructuredTool

# ==========================================
# SYSTEM PROMPT — GIT CONTROL
# ==========================================
GIT_SYSTEM_PROMPT = """
[CIEL-GIT VERSION CONTROL ARMORY]
Role: You are "Ciel-Git", a Version Control module.

[CONTEXT]
- You can discover, inspect, and manage Git repositories on the Master's machine.
- All destructive operations (commit, push) require Master's explicit confirmation.
- You NEVER commit sensitive files (.env, API keys, tokens, credentials).

[AVAILABLE GIT TOOLS]
1. `git_list_repos` — Scan a directory to discover all Git repositories.
2. `git_status` — Show current branch, changed/staged/untracked files.
3. `git_diff` — Show actual code changes (staged + unstaged).
4. `git_commit_and_push` — Preview what will be committed. DOES NOT PUSH YET. Returns a preview for Master to confirm.
5. `git_confirm_push` — Actually commit and push AFTER Master confirms the preview.

[STRICT GIT RULES]
1. ALWAYS use `git_status` or `git_diff` BEFORE `git_commit_and_push` so the Master can review changes.
2. NEVER call `git_confirm_push` unless the Master explicitly says "yes", "confirm", "go ahead", "do it", or similar approval.
3. When showing git status or diff, format the output clearly with sections for staged, unstaged, and untracked files.
4. If `git_commit_and_push` reports sensitive files were excluded, ALWAYS tell the Master which files were excluded and why.
5. For `git_list_repos`, default search_path to "D:/" if the Master doesn't specify a path.

[OUTPUT FORMAT]
* **Branch:** current branch name
* **Changes:** list of modified/added/deleted files
* **Status:** clean / dirty / ahead of remote
"""

# ==========================================
# SENSITIVE FILE PATTERNS
# ==========================================
SENSITIVE_PATTERNS = [
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.jks",
    "id_rsa",
    "id_ed25519",
]

SENSITIVE_EXACT = {
    ".env", ".env.local", ".env.production", ".env.staging",
    "credentials.json", "id_rsa", "id_ed25519",
}

SENSITIVE_EXTENSIONS = {".key", ".pem", ".p12", ".pfx", ".jks"}


def _is_sensitive_file(filepath: str) -> bool:
    """Check if a file is sensitive and should never be committed."""
    name = Path(filepath).name.lower()
    suffix = Path(filepath).suffix.lower()

    if name in SENSITIVE_EXACT:
        return True
    if suffix in SENSITIVE_EXTENSIONS:
        return True
    if name.startswith("token") and name.endswith(".json"):
        return True

    return False


def _run_git(args: list, cwd: str, timeout: int = 30) -> dict:
    """Run a git command and return structured result."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Git command timed out after {timeout}s", "returncode": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "Git is not installed or not in PATH.", "returncode": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


from skills._result import make_result as _make_result


# ==========================================
# MAIN TOOL BUILDER
# ==========================================
def get_github_tools() -> dict:
    try:
        tools = []

        # ======================
        # TOOL 1: git_list_repos
        # ======================
        def git_list_repos(search_path: str) -> dict:
            """Scan a directory to discover all Git repositories. Searches deeply. Optimized for D:\\ drive."""
            search = Path(search_path)
            if not search.exists():
                return _make_result(False, code="PATH_NOT_FOUND",
                                    message=f"Path '{search_path}' does not exist.",
                                    tool_name="git_list_repos")
            if not search.is_dir():
                return _make_result(False, code="NOT_A_DIRECTORY",
                                    message=f"'{search_path}' is not a directory.",
                                    tool_name="git_list_repos")

            # Directories to skip for performance
            skip_dirs = {
                ".venv", "venv", "node_modules", "__pycache__", ".tox",
                "site-packages", ".conda", "AppData", "Windows", "Program Files",
                "Program Files (x86)", "$Recycle.Bin", "System Volume Information",
                ".git", "dist", "build", ".next", "target"
            }

            repos = []
            max_repos = 50  # Safety cap

            def scan(directory: Path, depth: int = 0, max_depth: int = 8):
                """Recursively scan for .git directories."""
                if len(repos) >= max_repos:
                    return
                try:
                    for entry in directory.iterdir():
                        if len(repos) >= max_repos:
                            return
                        if not entry.is_dir():
                            continue
                        if entry.name in skip_dirs:
                            continue

                        # Found a git repo!
                        git_dir = entry / ".git"
                        if git_dir.exists() and git_dir.is_dir():
                            # Get remote URL
                            remote_result = _run_git(["remote", "get-url", "origin"], str(entry), timeout=5)
                            remote_url = remote_result["stdout"] if remote_result["success"] else "No remote"

                            # Get current branch
                            branch_result = _run_git(["branch", "--show-current"], str(entry), timeout=5)
                            branch = branch_result["stdout"] if branch_result["success"] else "unknown"

                            repos.append({
                                "path": str(entry),
                                "remote": remote_url,
                                "branch": branch
                            })
                            # Don't descend into git repos (no nested repos)
                            continue

                        # Keep scanning deeper
                        if depth < max_depth:
                            scan(entry, depth + 1, max_depth)

                except PermissionError:
                    pass  # Skip directories we can't access
                except Exception:
                    pass  # Skip any errors and continue scanning

            scan(search)

            if not repos:
                return _make_result(True, data={
                    "message": f"No Git repositories found under '{search_path}'.",
                    "repos": []
                }, tool_name="git_list_repos")

            # Format results
            lines = [f"Found {len(repos)} Git repo(s) under '{search_path}':\n"]
            for i, repo in enumerate(repos, 1):
                lines.append(f"  {i}. {repo['path']}")
                lines.append(f"     Branch: {repo['branch']} | Remote: {repo['remote']}")

            return _make_result(True, data={
                "message": "\n".join(lines),
                "repos": repos
            }, tool_name="git_list_repos")

        tools.append(StructuredTool.from_function(
            func=git_list_repos,
            name="git_list_repos",
            description="Scan a directory to discover all Git repositories. Default search path is 'D:/'. "
                        "Returns list of repos with their remote URLs and current branches. "
                        "YOU MUST USE THIS TOOL when the Master asks to find, list, or scan for Git projects."
        ))

        # ======================
        # TOOL 2: git_status
        # ======================
        def git_status(repo_path: str) -> dict:
            """Show current branch, changed, staged, and untracked files in a Git repository."""
            repo = Path(repo_path)
            if not (repo / ".git").exists():
                return _make_result(False, code="NOT_A_REPO",
                                    message=f"'{repo_path}' is not a Git repository (no .git directory found).",
                                    tool_name="git_status")

            # Get current branch
            branch_result = _run_git(["branch", "--show-current"], repo_path)
            branch = branch_result["stdout"] if branch_result["success"] else "unknown"

            # Get status
            status_result = _run_git(["status", "--porcelain"], repo_path)
            if not status_result["success"]:
                return _make_result(False, code="GIT_STATUS_FAILED",
                                    message=f"Failed to get status: {status_result['stderr']}",
                                    tool_name="git_status")

            # Parse porcelain output
            staged = []
            unstaged = []
            untracked = []

            for line in status_result["stdout"].split("\n"):
                if not line.strip():
                    continue
                index_status = line[0] if len(line) > 0 else " "
                work_status = line[1] if len(line) > 1 else " "
                filename = line[3:] if len(line) > 3 else line.strip()

                if index_status == "?":
                    untracked.append(filename)
                elif index_status != " ":
                    staged.append(f"[{index_status}] {filename}")
                if work_status != " " and work_status != "?":
                    unstaged.append(f"[{work_status}] {filename}")

            # Get ahead/behind info
            upstream_result = _run_git(["rev-list", "--left-right", "--count", f"{branch}...origin/{branch}"], repo_path)
            ahead_behind = ""
            if upstream_result["success"] and upstream_result["stdout"]:
                parts = upstream_result["stdout"].split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])
                    if ahead > 0:
                        ahead_behind = f" (ahead of remote by {ahead} commit(s))"
                    elif behind > 0:
                        ahead_behind = f" (behind remote by {behind} commit(s))"
                    else:
                        ahead_behind = " (up to date with remote)"

            # Format output
            lines = [f"📂 Repository: {repo_path}"]
            lines.append(f"🌿 Branch: {branch}{ahead_behind}")
            lines.append("")

            if not staged and not unstaged and not untracked:
                lines.append("✅ Working tree is clean. Nothing to commit.")
            else:
                if staged:
                    lines.append(f"📦 Staged ({len(staged)}):")
                    for f in staged:
                        lines.append(f"   {f}")
                if unstaged:
                    lines.append(f"📝 Modified (unstaged) ({len(unstaged)}):")
                    for f in unstaged:
                        lines.append(f"   {f}")
                if untracked:
                    lines.append(f"❓ Untracked ({len(untracked)}):")
                    for f in untracked:
                        lines.append(f"   {f}")

            return _make_result(True, data={"message": "\n".join(lines)}, tool_name="git_status")

        tools.append(StructuredTool.from_function(
            func=git_status,
            name="git_status",
            description="Show current branch, changed/staged/untracked files in a Git repository. "
                        "Provide the full repo_path (e.g. 'D:/Ciel-2.0'). "
                        "YOU MUST USE THIS TOOL when the Master asks about git status, changes, or what has been modified."
        ))

        # ======================
        # TOOL 3: git_diff
        # ======================
        def git_diff(repo_path: str) -> dict:
            """Show actual code changes (staged + unstaged) in a Git repository."""
            repo = Path(repo_path)
            if not (repo / ".git").exists():
                return _make_result(False, code="NOT_A_REPO",
                                    message=f"'{repo_path}' is not a Git repository.",
                                    tool_name="git_diff")

            # Get unstaged diff
            unstaged_result = _run_git(["diff"], repo_path, timeout=15)
            # Get staged diff
            staged_result = _run_git(["diff", "--cached"], repo_path, timeout=15)

            unstaged_diff = unstaged_result["stdout"] if unstaged_result["success"] else ""
            staged_diff = staged_result["stdout"] if staged_result["success"] else ""

            if not unstaged_diff and not staged_diff:
                return _make_result(True, data={
                    "message": f"📂 {repo_path}\n\n✅ No differences found. Working tree matches the last commit."
                }, tool_name="git_diff")

            MAX_CHARS = 3000
            lines = [f"📂 Repository: {repo_path}\n"]

            if staged_diff:
                lines.append("═══ STAGED CHANGES (will be committed) ═══")
                if len(staged_diff) > MAX_CHARS:
                    lines.append(staged_diff[:MAX_CHARS])
                    lines.append(f"\n... [TRUNCATED — {len(staged_diff)} total chars, showing first {MAX_CHARS}]")
                else:
                    lines.append(staged_diff)

            if unstaged_diff:
                lines.append("\n═══ UNSTAGED CHANGES (not yet staged) ═══")
                remaining = MAX_CHARS - len(staged_diff) if staged_diff else MAX_CHARS
                remaining = max(remaining, 500)  # Always show at least 500 chars
                if len(unstaged_diff) > remaining:
                    lines.append(unstaged_diff[:remaining])
                    lines.append(f"\n... [TRUNCATED — {len(unstaged_diff)} total chars, showing first {remaining}]")
                else:
                    lines.append(unstaged_diff)

            return _make_result(True, data={"message": "\n".join(lines)}, tool_name="git_diff")

        tools.append(StructuredTool.from_function(
            func=git_diff,
            name="git_diff",
            description="Show actual code changes (both staged and unstaged diffs) in a Git repository. "
                        "Output is truncated to avoid token overflow. "
                        "YOU MUST USE THIS TOOL when the Master asks to see the diff, code changes, or what was modified."
        ))

        # ======================
        # TOOL 4: git_commit_and_push (PREVIEW ONLY)
        # ======================
        def git_commit_and_push(repo_path: str, message: str) -> dict:
            """Stage all changes and show a PREVIEW of what will be committed. Does NOT push yet — waits for Master's confirmation."""
            repo = Path(repo_path)
            if not (repo / ".git").exists():
                return _make_result(False, code="NOT_A_REPO",
                                    message=f"'{repo_path}' is not a Git repository.",
                                    tool_name="git_commit_and_push")

            if not message or not message.strip():
                return _make_result(False, code="MISSING_MESSAGE",
                                    message="Commit message cannot be empty.",
                                    tool_name="git_commit_and_push")

            # Step 1: Stage all changes
            stage_result = _run_git(["add", "-A"], repo_path)
            if not stage_result["success"]:
                return _make_result(False, code="STAGE_FAILED",
                                    message=f"Failed to stage changes: {stage_result['stderr']}",
                                    tool_name="git_commit_and_push")

            # Step 2: Check for sensitive files and unstage them
            staged_result = _run_git(["diff", "--cached", "--name-only"], repo_path)
            sensitive_removed = []
            if staged_result["success"] and staged_result["stdout"]:
                for filepath in staged_result["stdout"].split("\n"):
                    filepath = filepath.strip()
                    if filepath and _is_sensitive_file(filepath):
                        _run_git(["reset", "HEAD", filepath], repo_path)
                        sensitive_removed.append(filepath)

            # Step 3: Get the final staged file list for preview
            final_staged = _run_git(["diff", "--cached", "--stat"], repo_path)
            file_list = _run_git(["diff", "--cached", "--name-status"], repo_path)

            if not file_list["success"] or not file_list["stdout"].strip():
                # Nothing left to commit after removing sensitive files
                msg = "Nothing to commit."
                if sensitive_removed:
                    msg += f"\n\n⚠️ Sensitive files were excluded: {', '.join(sensitive_removed)}"
                    msg += "\nAll remaining changes are already committed or there are no changes."
                return _make_result(True, data={"message": msg}, tool_name="git_commit_and_push")

            # Step 4: Get current branch
            branch_result = _run_git(["branch", "--show-current"], repo_path)
            branch = branch_result["stdout"] if branch_result["success"] else "unknown"

            # Step 5: Build preview
            lines = [
                "═══════════════════════════════════════",
                "   📋 COMMIT PREVIEW — AWAITING CONFIRMATION",
                "═══════════════════════════════════════",
                "",
                f"📂 Repository: {repo_path}",
                f"🌿 Branch: {branch}",
                f"💬 Commit message: \"{message}\"",
                "",
                "📦 Files to be committed:",
            ]

            for file_line in file_list["stdout"].split("\n"):
                if file_line.strip():
                    lines.append(f"   {file_line.strip()}")

            if final_staged["success"] and final_staged["stdout"]:
                lines.append("")
                lines.append("📊 Stats:")
                # Just show the summary line (last line of --stat)
                stat_lines = final_staged["stdout"].strip().split("\n")
                if stat_lines:
                    lines.append(f"   {stat_lines[-1].strip()}")

            if sensitive_removed:
                lines.append("")
                lines.append(f"⚠️ EXCLUDED sensitive files: {', '.join(sensitive_removed)}")

            lines.append("")
            lines.append("═══════════════════════════════════════")
            lines.append("⏳ Waiting for your confirmation.")
            lines.append('   Say "yes" or "confirm" to commit and push.')
            lines.append('   Say "no" or "cancel" to abort.')
            lines.append("═══════════════════════════════════════")

            return _make_result(True, data={"message": "\n".join(lines)}, tool_name="git_commit_and_push")

        tools.append(StructuredTool.from_function(
            func=git_commit_and_push,
            name="git_commit_and_push",
            description="Stage all changes (excluding sensitive files like .env) and show a PREVIEW of what will be committed. "
                        "This tool does NOT push — it shows a preview and asks for Master's confirmation. "
                        "After the Master confirms, use git_confirm_push to actually commit and push. "
                        "YOU MUST USE THIS TOOL when the Master asks to commit, push, or deploy code changes."
        ))

        # ======================
        # TOOL 5: git_confirm_push (ACTUAL EXECUTION)
        # ======================
        def git_confirm_push(repo_path: str, message: str) -> dict:
            """Actually commit staged changes and push to remote. ONLY call this after Master has confirmed the preview."""
            repo = Path(repo_path)
            if not (repo / ".git").exists():
                return _make_result(False, code="NOT_A_REPO",
                                    message=f"'{repo_path}' is not a Git repository.",
                                    tool_name="git_confirm_push")

            # Double-check: are there staged changes?
            check = _run_git(["diff", "--cached", "--name-only"], repo_path)
            if not check["success"] or not check["stdout"].strip():
                return _make_result(False, code="NOTHING_STAGED",
                                    message="No staged changes found. Run git_commit_and_push first to stage changes.",
                                    tool_name="git_confirm_push")

            # Safety: one more pass to remove any sensitive files
            for filepath in check["stdout"].split("\n"):
                filepath = filepath.strip()
                if filepath and _is_sensitive_file(filepath):
                    _run_git(["reset", "HEAD", filepath], repo_path)

            # Commit
            commit_result = _run_git(["commit", "-m", message], repo_path)
            if not commit_result["success"]:
                return _make_result(False, code="COMMIT_FAILED",
                                    message=f"Commit failed: {commit_result['stderr']}",
                                    tool_name="git_confirm_push")

            # Push
            push_result = _run_git(["push"], repo_path, timeout=60)
            if not push_result["success"]:
                # Commit succeeded but push failed — inform user
                return _make_result(False, code="PUSH_FAILED",
                                    message=f"✅ Commit succeeded, but push failed:\n{push_result['stderr']}\n\n"
                                            f"The commit is saved locally. You can try pushing manually with 'git push'.",
                                    tool_name="git_confirm_push")

            # Get the short commit hash
            hash_result = _run_git(["rev-parse", "--short", "HEAD"], repo_path)
            commit_hash = hash_result["stdout"] if hash_result["success"] else "unknown"

            branch_result = _run_git(["branch", "--show-current"], repo_path)
            branch = branch_result["stdout"] if branch_result["success"] else "unknown"

            return _make_result(True, data={
                "message": (
                    f"✅ Successfully committed and pushed!\n\n"
                    f"📂 Repository: {repo_path}\n"
                    f"🌿 Branch: {branch}\n"
                    f"🔖 Commit: {commit_hash}\n"
                    f"💬 Message: \"{message}\"\n"
                    f"🚀 Pushed to remote."
                )
            }, tool_name="git_confirm_push")

        tools.append(StructuredTool.from_function(
            func=git_confirm_push,
            name="git_confirm_push",
            description="Actually commit and push staged changes to the remote repository. "
                        "ONLY call this tool AFTER the Master has explicitly confirmed the preview from git_commit_and_push. "
                        "NEVER call this tool without Master's approval — this is a CRITICAL SAFETY RULE."
        ))

        return {"tools": tools, "prompt": GIT_SYSTEM_PROMPT}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm Git toolkit: {e}")
        return {"tools": [], "prompt": ""}
