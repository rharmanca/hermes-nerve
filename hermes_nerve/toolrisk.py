"""Deterministic, content-aware tool risk classification.

Two callers share this module:

* ``gate`` uses :func:`strict_read_only_terminal` for *bypass* decisions. That
  path is deliberately narrow (no pipes, no env prefixes, no path-qualified
  executables) because a bypass skips the provider entirely.
* ``router`` uses :func:`classify_tool` for *risk scoring*. Scoring only moves
  the prior that decides whether an event is worth a provider call, so it may
  look deeper at command and code content (pipelines, ``sudo``, ``execute_code``
  bodies) without weakening enforcement.

Pure stdlib, no package imports, so it can be used anywhere without cycles.
Classification results carry fixed labels only; argument content is never
returned or logged.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Categories and priors
# --------------------------------------------------------------------------- #

READ_ONLY = "read_only"
NETWORK_READ = "network_read"
AMBIGUOUS = "ambiguous"
MUTATING = "mutating"
UNKNOWN = "unknown"

_CATEGORY_RANK = {READ_ONLY: 0, NETWORK_READ: 1, UNKNOWN: 2, AMBIGUOUS: 3, MUTATING: 4}
_CATEGORY_RISK = {READ_ONLY: 0.10, NETWORK_READ: 0.30, UNKNOWN: 0.36, AMBIGUOUS: 0.40, MUTATING: 0.68}


@dataclass(frozen=True)
class ToolClassification:
    category: str
    reason: str
    risk: float


def _result(category: str, reason: str, risk: float | None = None) -> ToolClassification:
    return ToolClassification(category, reason, _CATEGORY_RISK[category] if risk is None else risk)


def _worst(*items: ToolClassification) -> ToolClassification:
    return max(items, key=lambda item: _CATEGORY_RANK[item.category])


# --------------------------------------------------------------------------- #
# Tool-name tables
# --------------------------------------------------------------------------- #

READ_ONLY_TOOLS = frozenset({
    "read_file", "search_files", "tool_search", "tool_describe",
    "remote_worker_status", "remote_worker_result",
    "mnemosyne_recall", "mnemosyne_shared_recall", "mnemosyne_stats", "mnemosyne_shared_stats",
    "mnemosyne_get", "mnemosyne_graph_query", "mnemosyne_sync_status",
    "spotify_search",
    "skill_view", "skills_list", "web_search", "web_extract", "vision_analyze",
    "chat_history_lookup", "session_search",
    "kanban_show", "kanban_list", "kanban_attachments",
    "lcm_grep", "lcm_recall", "lcm_recent", "lcm_load_session", "lcm_describe",
    "lcm_expand", "lcm_expand_query", "lcm_status", "lcm_inspect", "lcm_doctor",
})

MUTATING_TOOLS = frozenset({
    "write_file", "patch", "skill_manage", "context_notes", "memory", "cronjob_manage",
    "process_manage", "send_message", "image_generate",
    "kanban_create", "kanban_complete", "kanban_block", "kanban_unblock", "kanban_comment",
    "kanban_link", "kanban_attach", "kanban_attach_url", "kanban_request_review",
    "kanban_request_changes", "kanban_schedule",
})

# Known tools whose effect depends on content we cannot inspect safely.
AMBIGUOUS_TOOLS = frozenset({
    "delegate_task", "browser_exec", "mcp__browser_exec", "browser_vault", "computer_use",
})

_TERMINAL_TOOLS = frozenset({"terminal", "shell", "bash"})
_CODE_TOOLS = frozenset({"execute_code"})

READ_ONLY_TOKENS = frozenset({
    "read", "get", "list", "search", "find", "status", "show", "inspect", "fetch",
    "grep", "rg", "cat", "pwd", "head", "tail", "diff", "view", "describe", "query",
    "lookup", "recall", "check", "count", "explain", "analyze", "compare", "preview",
    "health", "version", "info", "stats", "summary", "exists", "detect", "extract",
    "parse", "decode", "overview", "tree", "glob", "ls",
})

MUTATING_TOKENS = frozenset({
    "write", "edit", "update", "delete", "remove", "create", "commit", "push", "merge",
    "deploy", "restart", "stop", "send", "post", "publish", "install", "purchase", "apply",
    "save", "upload", "set", "attach", "manage", "kill", "move", "rename", "clone", "restore",
    "retire", "share", "unshare", "mark", "spawn", "start", "launch", "uninstall", "upgrade",
    "sync", "submit", "approve", "transfer", "pay", "order", "buy", "book", "cancel",
    "archive", "unarchive", "drop", "truncate", "purge", "wipe", "reset", "rotate", "revoke",
    "grant", "assign", "dispatch", "notify", "email", "message", "reply", "tweet", "comment",
    "react", "vote", "append", "insert", "put", "replace", "modify", "change", "toggle",
    "enable", "disable", "schedule", "trigger", "invoke", "execute", "release", "block",
    "unblock", "complete",
})

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _name_tokens(tool_name: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT_RE.split(str(tool_name or "").strip().lower()) if t]


# --------------------------------------------------------------------------- #
# Terminal commands
# --------------------------------------------------------------------------- #

SIMPLE_READ_ONLY_COMMANDS = frozenset({
    "pwd", "ls", "cat", "head", "tail", "wc", "stat", "du", "df", "file",
    "readlink", "basename", "dirname", "realpath", "which", "whereis", "whoami",
    "id", "uname", "uptime", "free", "ps", "printenv",
    "grep", "rg", "jq",
})
SAFE_GIT_SUBCOMMANDS = frozenset({
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "describe", "grep", "blame",
})
SHELL_META_RE = re.compile(r"(?:&&|\|\||[;|><`]|\$\()")

# Wider read-only set used only for scoring pipelines.
_PIPELINE_READ_ONLY_COMMANDS = SIMPLE_READ_ONLY_COMMANDS | frozenset({
    "cd", "echo", "printf", "true", "false", "test", "[", "sleep", "date", "hostname", "nproc",
    "env", "export", "type", "command", "hash", "sort", "uniq", "cut", "tr", "column", "paste",
    "comm", "nl", "tac", "rev", "fold", "fmt", "xxd", "od", "strings", "md5sum", "sha256sum",
    "sha1sum", "b2sum", "cksum", "cmp", "less", "more", "locate", "tree", "lsblk", "lsof",
    "ss", "netstat", "lscpu", "lsmem", "lspci", "lsusb", "dmesg", "journalctl", "top", "htop",
    "vmstat", "iostat", "ping", "dig", "nslookup", "host", "getent", "yes", "seq", "expr", "bc",
    "base64", "tput", "stty", "tty", "timedatectl", "hostnamectl",
    "ifconfig", "arp", "route", "lsattr", "getfacl", "pgrep", "pidof", "jobs",
    "history", "alias", "declare", "typeset", "local", "readonly", "shift", "wait", "return",
    "exit", "break", "continue", ":",
})

_MUTATING_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp", "dd", "mkfs", "chmod", "chown", "chgrp", "ln", "touch", "truncate",
    "kill", "pkill", "killall", "shutdown", "reboot", "halt", "poweroff", "mkdir", "install",
    "rsync", "scp", "sftp", "ftp", "tee", "useradd", "usermod", "userdel", "groupadd", "passwd",
    "iptables", "nft", "ufw", "umount", "swapoff", "swapon", "fdisk", "parted", "sfdisk",
    "service", "launchctl", "crontab", "shred", "wipefs", "mkswap", "setfacl", "chattr",
    "patch", "unzip", "tar", "gzip", "gunzip", "zip", "xz", "bzip2", "mktemp", "ssh",
})
_INTERPRETERS = frozenset({
    "sh", "bash", "zsh", "dash", "fish", "ksh", "python", "python3", "python2", "node", "nodejs",
    "deno", "bun", "ruby", "perl", "php", "lua", "Rscript", "julia", "pwsh", "powershell",
    "uv", "uvx", "npx", "pipx", "poetry", "pdm", "hatch", "tox", "nox", "make", "cmake", "ninja",
    "cargo", "go", "mvn", "gradle", "xargs", "parallel", "watch", "eval", "source", ".",
})
_PACKAGE_MANAGERS = frozenset({
    "pip", "pip3", "npm", "yarn", "pnpm", "apt", "apt-get", "dnf", "yum", "brew", "snap", "gem",
    "pacman", "apk", "conda", "mamba", "flatpak",
})
_PACKAGE_MANAGER_READ_ONLY = frozenset({
    "list", "show", "search", "info", "freeze", "check", "outdated", "view", "ls", "why",
    "--version", "-v", "-V", "help", "--help", "config", "cache", "policy", "depends", "rdepends",
    "download", "explain", "licenses", "audit", "doctor", "env",
})
_SYSTEMCTL_READ_ONLY = frozenset({
    "status", "is-active", "is-enabled", "is-failed", "is-system-running", "list-units",
    "list-unit-files", "list-timers", "list-sockets", "list-dependencies", "show", "cat",
    "show-environment", "get-default", "--version", "help",
})
_DOCKER_READ_ONLY = frozenset({
    "ps", "logs", "inspect", "images", "version", "info", "stats", "top", "port", "diff",
    "history", "search", "events", "--version",
})
_DOCKER_READ_ONLY_SUB = {
    "network": {"ls", "inspect"}, "volume": {"ls", "inspect"}, "container": {"ls", "inspect", "logs", "stats", "top", "port", "diff"},
    "image": {"ls", "inspect", "history"}, "compose": {"ps", "logs", "config", "version", "images", "top", "ls"},
    "system": {"df", "info", "events"}, "context": {"ls", "inspect", "show"},
}
_KUBECTL_READ_ONLY = frozenset({
    "get", "describe", "logs", "top", "version", "cluster-info", "explain", "api-resources",
    "api-versions", "config", "diff", "auth",
})
_GIT_MUTATING = frozenset({
    "push", "commit", "merge", "rebase", "reset", "checkout", "switch", "add", "rm", "mv",
    "clean", "stash", "cherry-pick", "revert", "tag", "fetch", "pull", "clone", "init", "am",
    "apply", "restore", "submodule", "worktree", "gc", "prune", "filter-branch", "notes",
    "update-ref", "update-index", "symbolic-ref", "replace", "bisect", "mergetool",
})
_GIT_LIST_OR_MUTATE = frozenset({"branch", "remote", "config", "tag"})
_WRAPPERS = frozenset({"sudo", "doas", "env", "nice", "ionice", "time", "nohup", "command", "builtin", "exec", "stdbuf", "chronic"})
_REDIRECT_TOKENS = frozenset({">", ">>", "&>", ">|", "&>>"})
_SEPARATOR_TOKENS = frozenset({"&&", "||", ";", "|", "|&", "&"})
_CURL_WRITE_FLAGS = frozenset({
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "-F", "--form",
    "-T", "--upload-file", "-o", "--output", "-O", "--remote-name", "--json", "-X", "--request",
})
_WGET_WRITE_FLAGS = frozenset({"--post-data", "--post-file", "--method", "-O", "--output-document", "--body-data", "--body-file"})

# CLIs whose effect is named by a verb somewhere in the argument list
# (`gws-work gmail users messages list`, `gh pr create`, `hermes plugins install`).
_VERB_CLIS = frozenset({
    "gws", "gws-work", "gws-personal", "gh", "glab", "aws", "gcloud", "az", "op", "bw", "bws",
    "hermes", "linear", "trello", "jira", "slack", "doctl", "flyctl", "fly", "vercel", "netlify",
    "wrangler", "supabase", "stripe", "twilio", "railway", "heroku",
})
_CLI_READ_VERBS = frozenset({
    "list", "ls", "view", "get", "show", "status", "diff", "checks", "search", "about", "export",
    "describe", "info", "version", "watch", "log", "logs", "cat", "read", "whoami", "which", "check",
    "help", "stat", "stats", "tree", "query", "lookup", "peek", "inspect", "compare", "find", "print",
    "count", "summary", "preview", "validate", "verify", "doctor", "health", "ping", "test",
})
_CLI_MUTATING_VERBS = frozenset({
    "send", "create", "update", "delete", "insert", "patch", "modify", "trash", "untrash", "move",
    "copy", "batchupdate", "import", "upload", "share", "append", "clear", "remove", "add", "edit",
    "merge", "close", "reopen", "comment", "review", "rerun", "cancel", "clone", "fork", "login",
    "logout", "push", "set", "write", "put", "post", "publish", "release", "deploy", "install",
    "uninstall", "upgrade", "restart", "start", "stop", "enable", "disable", "reply", "forward",
    "draft", "label", "archive", "empty", "dm", "rotate", "revoke", "grant", "approve", "transfer",
    "pay", "purge", "wipe", "reset", "sync", "apply", "run", "exec", "invoke", "trigger", "submit",
    "mark", "assign", "unassign", "rename", "replace", "purchase", "order", "book", "schedule",
    "attach", "detach", "link", "unlink", "lock", "unlock", "pin", "unpin", "mute", "unmute", "join",
    "leave", "invite", "kick", "ban", "unban", "star", "unstar", "watch-start", "generate", "new",
})
_DB_CLIS = frozenset({"sqlite3", "psql", "mysql", "mariadb", "duckdb", "clickhouse-client", "redis-cli"})
_SQL_WRITE_RE = re.compile(r"\b(?:insert|update|delete|drop|alter|create|replace|truncate|vacuum|attach|detach|grant|revoke|merge|upsert|copy|load|set|flushall|flushdb|del|hset|lpush|rpush|sadd|zadd|incr|decr|expire)\b|\bpragma\s+\w+\s*=", re.IGNORECASE)
_SQL_READ_RE = re.compile(r"\b(?:select|pragma|explain|with|show|describe|desc|keys|hget|hgetall|lrange|smembers|zrange|ttl|info|dbsize|scan|exists|type|mget)\b|\.(?:tables|schema|indexes|indices|dbinfo|mode|headers|timeout)\b", re.IGNORECASE)
_HEREDOC_RE = re.compile(
    r"<<-?\s*(?P<q>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=q)(?P<rest>[^\n]*)\n(?P<body>[\s\S]*?)\n[ \t]*(?P=tag)[ \t]*(?=\n|$)"
)
_HEREDOC_TOKEN_RE = re.compile(r"__heredoc(\d+)__")


def terminal_command(args: dict[str, Any] | None) -> str:
    args = args if isinstance(args, dict) else {}
    for key in ("command", "cmd", "script"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def strict_read_only_terminal(command: str) -> bool:
    """Return True only for deliberately narrow, obvious read-only shell shapes.

    This is the gate bypass predicate: it stays conservative on purpose.
    """
    if not command or SHELL_META_RE.search(command):
        return False
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not parts:
        return False

    # Do not bypass environment-prefixed or path-qualified executables.
    # `PATH=/tmp ls` can resolve attacker-controlled code and `LD_PRELOAD=... ls`
    # can execute arbitrary code before the nominally read-only program starts.
    if "=" in parts[0] or "/" in parts[0]:
        return False

    executable = parts[0]
    if executable in SIMPLE_READ_ONLY_COMMANDS:
        if executable == "rg":
            # ripgrep --pre/--pre-glob can execute an external preprocessor.
            if any(arg == "--pre" or arg.startswith("--pre=") or arg == "--pre-glob" or arg.startswith("--pre-glob=") for arg in parts[1:]):
                return False
        return True
    if executable == "git" and len(parts) >= 2:
        if parts[1] not in SAFE_GIT_SUBCOMMANDS:
            return False
        # diff/show/log/blame can be configured to invoke external helpers.
        if any(arg in {"--ext-diff", "--textconv"} or arg.startswith("--ext-diff=") or arg.startswith("--textconv=") for arg in parts[2:]):
            return False
        return True
    return False


def _tokenize_shell(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drop sudo/env/nice/timeout style prefixes and KEY=VALUE assignments."""
    out = list(words)
    while out:
        head = out[0]
        if "=" in head and not head.startswith("=") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head):
            out = out[1:]
            continue
        if head in _WRAPPERS:
            out = out[1:]
            # Skip option flags belonging to the wrapper (e.g. `sudo -u user`, `nice -n 10`).
            while out and out[0].startswith("-"):
                flag = out.pop(0)
                if flag in {"-u", "-g", "-n", "-c", "-p", "-E"} and out and not out[0].startswith("-") and flag != "-E":
                    out.pop(0)
            continue
        if head == "timeout":
            out = out[1:]
            while out and out[0].startswith("-"):
                flag = out.pop(0)
                if flag in {"-s", "--signal", "-k", "--kill-after"} and out:
                    out.pop(0)
            if out:
                out = out[1:]  # the duration
            continue
        break
    return out


def _classify_segment(words: list[str], depth: int = 0, heredocs: list[str] | None = None) -> ToolClassification:
    words = _strip_wrappers(words)
    if not words:
        return _result(READ_ONLY, "read-only-terminal")
    executable = words[0]
    if any(ch in executable for ch in "`$"):
        return _result(AMBIGUOUS, "terminal-ambiguous")
    base = executable.rsplit("/", 1)[-1]
    rest = words[1:]

    body = ""
    kept: list[str] = []
    for word in rest:
        match = _HEREDOC_TOKEN_RE.fullmatch(word)
        if match and heredocs is not None and int(match.group(1)) < len(heredocs):
            body = heredocs[int(match.group(1))]
        else:
            kept.append(word)
    rest = kept
    if body and depth < 2:
        positional = [w for w in rest if not w.startswith("-") or w == "-"]
        if base in {"python", "python3", "python2"} and positional in ([], ["-"]):
            return _as_terminal(classify_code(body))
        if base in {"sh", "bash", "zsh", "dash", "ksh"} and not positional:
            return classify_terminal(body, _depth=depth + 1)
    if base in _DB_CLIS:
        sql = " ".join(rest) + "\n" + body
        if _SQL_WRITE_RE.search(sql):
            return _result(MUTATING, "mutating-terminal")
        if _SQL_READ_RE.search(sql):
            return _result(READ_ONLY, "read-only-terminal")
        return _result(AMBIGUOUS, "terminal-ambiguous")
    if base in _VERB_CLIS:
        verbs = [w.lstrip("+").lower() for w in rest if not w.startswith("-")]
        if base in {"gh", "glab"} and "api" in verbs:
            method = "GET"
            for i, w in enumerate(rest):
                if w in {"-X", "--method"} and i + 1 < len(rest):
                    method = rest[i + 1].upper()
                elif w.startswith("--method="):
                    method = w.split("=", 1)[1].upper()
            if method not in {"GET", "HEAD"} or any(w in {"-f", "-F", "--field", "--raw-field", "--input"} for w in rest):
                return _result(MUTATING, "mutating-terminal")
            return _result(NETWORK_READ, "network-read-terminal")
        if any(v in _CLI_MUTATING_VERBS for v in verbs):
            return _result(MUTATING, "mutating-terminal")
        if any(v in _CLI_READ_VERBS for v in verbs) or any(w in {"--version", "--help", "-h", "-V"} for w in rest):
            return _result(READ_ONLY, "read-only-terminal")
        return _result(AMBIGUOUS, "terminal-ambiguous")

    if base in _MUTATING_COMMANDS:
        return _result(MUTATING, "mutating-terminal")
    if base == "sed":
        if any(a == "-i" or a.startswith("-i") and not a.startswith("-in") or a.startswith("--in-place") for a in rest):
            return _result(MUTATING, "mutating-terminal")
        return _result(READ_ONLY, "read-only-terminal")
    if base in {"awk", "gawk", "mawk", "nawk"}:
        joined = " ".join(rest)
        if "system(" in joined or re.search(r"print\s*>", joined) or "getline" in joined and "|" in joined:
            return _result(AMBIGUOUS, "terminal-ambiguous")
        return _result(READ_ONLY, "read-only-terminal")
    if base == "find":
        if any(a in {"-delete", "-fprint", "-fprintf", "-fls"} for a in rest):
            return _result(MUTATING, "mutating-terminal")
        if any(a in {"-exec", "-execdir", "-ok", "-okdir"} for a in rest):
            return _result(AMBIGUOUS, "terminal-ambiguous")
        return _result(READ_ONLY, "read-only-terminal")
    if base == "mount":
        return _result(READ_ONLY if not rest else MUTATING, "read-only-terminal" if not rest else "mutating-terminal")
    if base == "ip":
        if any(a in {"add", "del", "delete", "set", "flush", "change", "replace", "link"} for a in rest):
            return _result(MUTATING, "mutating-terminal")
        return _result(READ_ONLY, "read-only-terminal")
    if base == "rg":
        if any(a == "--pre" or a.startswith("--pre=") or a == "--pre-glob" or a.startswith("--pre-glob=") for a in rest):
            return _result(AMBIGUOUS, "terminal-ambiguous")
        return _result(READ_ONLY, "read-only-terminal")
    if base in _PIPELINE_READ_ONLY_COMMANDS:
        return _result(READ_ONLY, "read-only-terminal")
    if base == "git":
        # Skip global options such as `-C <dir>`, `-c k=v`, `--no-pager`, `--git-dir=...`.
        while rest and rest[0].startswith("-"):
            opt = rest.pop(0)
            if opt in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"} and rest:
                rest.pop(0)
        sub = rest[0] if rest else ""
        if sub in SAFE_GIT_SUBCOMMANDS:
            if any(a in {"--ext-diff", "--textconv"} or a.startswith("--ext-diff=") or a.startswith("--textconv=") for a in rest[1:]):
                return _result(AMBIGUOUS, "terminal-ambiguous")
            return _result(READ_ONLY, "read-only-terminal")
        if sub in _GIT_MUTATING:
            return _result(MUTATING, "mutating-terminal")
        if sub in _GIT_LIST_OR_MUTATE:
            flags = rest[1:]
            if sub == "config" and any(a in {"--get", "--get-all", "-l", "--list", "--get-regexp"} for a in flags):
                return _result(READ_ONLY, "read-only-terminal")
            if sub in {"branch", "remote", "tag"} and (not flags or all(a in {"-a", "-r", "-v", "-vv", "--list", "-l", "--show-current", "--all", "--verbose", "show", "get-url", "-n"} or a.startswith("--contains") or a.startswith("--merged") or a.startswith("--format") for a in flags)):
                return _result(READ_ONLY, "read-only-terminal")
            return _result(MUTATING, "mutating-terminal")
        return _result(AMBIGUOUS, "terminal-ambiguous")
    if base == "systemctl":
        if any(a in _SYSTEMCTL_READ_ONLY for a in rest):
            return _result(READ_ONLY, "read-only-terminal")
        return _result(MUTATING, "mutating-terminal")
    if base == "docker" or base == "podman":
        sub = rest[0] if rest else ""
        if sub in _DOCKER_READ_ONLY:
            return _result(READ_ONLY, "read-only-terminal")
        if sub in _DOCKER_READ_ONLY_SUB and len(rest) >= 2 and rest[1] in _DOCKER_READ_ONLY_SUB[sub]:
            return _result(READ_ONLY, "read-only-terminal")
        return _result(MUTATING, "mutating-terminal")
    if base == "kubectl":
        sub = rest[0] if rest else ""
        if sub in _KUBECTL_READ_ONLY:
            return _result(READ_ONLY, "read-only-terminal")
        return _result(MUTATING, "mutating-terminal")
    if base in _PACKAGE_MANAGERS:
        sub = rest[0] if rest else ""
        if sub in _PACKAGE_MANAGER_READ_ONLY:
            return _result(READ_ONLY, "read-only-terminal")
        if sub in {"run", "exec", "test", "start", "x", "dlx", "create"}:
            return _result(AMBIGUOUS, "terminal-ambiguous")
        return _result(MUTATING, "mutating-terminal")
    if base in {"curl", "wget", "http", "https", "httpie"}:
        flags = set(rest)
        if base == "curl":
            if flags & _CURL_WRITE_FLAGS or any(a.startswith(("-X", "--request=", "-d", "--data", "-o", "-T", "-F")) and a not in {"-XGET", "-XHEAD", "--request=GET", "--request=HEAD"} for a in rest):
                method = None
                for i, a in enumerate(rest):
                    if a in {"-X", "--request"} and i + 1 < len(rest):
                        method = rest[i + 1].upper()
                    elif a.startswith("-X") and len(a) > 2:
                        method = a[2:].upper()
                    elif a.startswith("--request="):
                        method = a.split("=", 1)[1].upper()
                if method in {"GET", "HEAD"} and not (flags & (_CURL_WRITE_FLAGS - {"-X", "--request"})):
                    return _result(NETWORK_READ, "network-read-terminal")
                return _result(MUTATING, "mutating-terminal")
            return _result(NETWORK_READ, "network-read-terminal")
        if base == "wget":
            if flags & _WGET_WRITE_FLAGS or any(a.startswith(("--post", "--method=", "--body")) for a in rest):
                return _result(MUTATING, "mutating-terminal")
            # wget writes a file by default unless -O- / --spider / -q0.
            if any(a in {"-O-", "-O", "--spider", "-qO-"} or a.startswith("--output-document=-") for a in rest):
                if "-O" in flags and not any(a in {"-O-", "-qO-"} for a in rest):
                    return _result(MUTATING, "mutating-terminal")
                return _result(NETWORK_READ, "network-read-terminal")
            return _result(MUTATING, "mutating-terminal")
        # httpie: method is the first positional argument
        method = next((a.upper() for a in rest if not a.startswith("-")), "GET")
        if method in {"GET", "HEAD", "OPTIONS"} or "://" in method.lower():
            return _result(NETWORK_READ, "network-read-terminal")
        return _result(MUTATING, "mutating-terminal")
    if base in _INTERPRETERS:
        if rest and rest[0] in {"--version", "-V", "-v", "version", "--help", "-h", "help"}:
            return _result(READ_ONLY, "read-only-terminal")
        if depth < 2 and "-c" in rest and rest.index("-c") + 1 < len(rest):
            inner = rest[rest.index("-c") + 1]
            if base in {"sh", "bash", "zsh", "dash", "ksh", "fish"}:
                return classify_terminal(inner, _depth=depth + 1)
            if base in {"python", "python3", "python2"}:
                return _as_terminal(classify_code(inner))
        return _result(AMBIGUOUS, "terminal-ambiguous")
    return _result(AMBIGUOUS, "terminal-ambiguous")


def _as_terminal(item: ToolClassification) -> ToolClassification:
    """Re-label a code classification with terminal reason labels."""
    reason = {READ_ONLY: "read-only-terminal", NETWORK_READ: "network-read-terminal", MUTATING: "mutating-terminal"}.get(item.category, "terminal-ambiguous")
    return _result(item.category if item.category != UNKNOWN else AMBIGUOUS, reason)


def classify_terminal(command: str, *, _depth: int = 0) -> ToolClassification:
    """Pipeline-aware classification of a shell command for scoring purposes."""
    command = str(command or "").strip()
    if not command:
        return _result(UNKNOWN, "unknown-tool-risk")
    if strict_read_only_terminal(command):
        return _result(READ_ONLY, "read-only-terminal")

    heredocs: list[str] = []
    expands_in_body = False

    def _lift_heredoc(match: re.Match[str]) -> str:
        nonlocal expands_in_body
        body = match.group("body")
        if not match.group("q") and ("$(" in body or "`" in body):
            expands_in_body = True  # unquoted tag: the shell expands substitutions inside the body
        heredocs.append(body)
        return f" __heredoc{len(heredocs) - 1}__ {match.group('rest')}\n"

    command = _HEREDOC_RE.sub(_lift_heredoc, command)
    if expands_in_body or "`" in command or "$(" in command or "<(" in command or ">(" in command:
        return _result(AMBIGUOUS, "terminal-ambiguous")
    tokens = _tokenize_shell(command)
    if not tokens:
        return _result(AMBIGUOUS, "terminal-ambiguous")

    segments: list[list[str]] = [[]]
    piped_into_interpreter = False
    worst = _result(READ_ONLY, "read-only-terminal")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SEPARATOR_TOKENS:
            if tok in {"|", "|&"} and i + 1 < len(tokens):
                nxt = _strip_wrappers(tokens[i + 1:i + 4])
                if nxt and nxt[0].rsplit("/", 1)[-1] in {"sh", "bash", "zsh", "dash", "python", "python3", "node", "perl", "ruby", "xargs"}:
                    piped_into_interpreter = True
            segments.append([])
            i += 1
            continue
        if tok in {"(", ")", "{", "}"}:
            worst = _worst(worst, _result(AMBIGUOUS, "terminal-ambiguous"))
            i += 1
            continue
        if tok in _REDIRECT_TOKENS or re.fullmatch(r"\d*(>>?|&>|>\|)", tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if target not in {"/dev/null", "/dev/stdout", "/dev/stderr"} and not target.startswith("&"):
                worst = _worst(worst, _result(MUTATING, "mutating-terminal"))
            i += 2
            continue
        if tok in {">&", "<&", "<", "<<", "<<<", "2>&1", "1>&2"} or re.fullmatch(r"\d?[<>]&\d?", tok):
            i += 2 if tok in {"<", "<<", "<<<"} else 1
            continue
        segments[-1].append(tok)
        i += 1

    for segment in segments:
        if segment:
            worst = _worst(worst, _classify_segment(segment, depth=_depth, heredocs=heredocs))
    if piped_into_interpreter:
        worst = _worst(worst, _result(MUTATING, "mutating-terminal"))
    return worst


# --------------------------------------------------------------------------- #
# execute_code bodies
# --------------------------------------------------------------------------- #

_CODE_KEYS = ("code", "source", "script", "python")

_PY_READ_ONLY_TOOL_RE = re.compile(r"\b(?:web_search|web_extract|read_file|search_files)\s*\(")
_PY_MUTATING_RE = re.compile(
    r"""
    \b(?:write_file|patch|terminal)\s*\(                                   # hermes_tools writers / shell
  | \bsubprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)\s*\(   # non-literal shell (literals were scrubbed)
  | \bimport\b[^\n]*\b(?:write_file|patch|terminal)\b[^\n]*\bas\b          # aliasing a writer
  | \bos\.(?:system|popen|exec\w*|spawn\w*|remove|unlink|rmdir|removedirs|rename|renames|replace
            |makedirs|mkdir|chmod|chown|chroot|kill|killpg|truncate|symlink|link|utime|_exit|putenv|unsetenv)\b
  | \bos\.environ\s*\[[^\]]*\]\s*=
  | \bos\.environ\.(?:update|pop|setdefault|clear)\s*\(
  | \bshutil\.(?:rmtree|move|copy\w*|chown|make_archive|unpack_archive)\b
  | \.(?:write_text|write_bytes|unlink|rmdir|mkdir|rename|replace|touch|chmod|symlink_to|hardlink_to|lchmod)\s*\(
  | \bopen\s*\([^)]*?,\s*['"][rb+]*[wax][rwaxb+]*['"]
  | \bopen\s*\([^)]*?mode\s*=\s*['"][^'"]*[wax+]
  | \b(?:requests|httpx|aiohttp)\.(?:post|put|patch|delete)\s*\(
  | \.(?:post|put|patch|delete)\s*\(
  | \bsmtplib\b | \bftplib\b | \bparamiko\b | \bfabric\b | \btelnetlib\b
  | \bsocket\.(?:socket|create_connection)\s*\(
  | \bsqlite3\b[\s\S]*?\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE)\b
  | \.commit\s*\(
  | \bpickle\.dump\s*\( | \bjson\.dump\s*\( | \bcsv\.writer\s*\( | \.to_(?:csv|excel|json|parquet|sql|pickle)\s*\(
  | \bzipfile\.ZipFile\s*\([^)]*['"][wax]
  | \btarfile\.open\s*\([^)]*['"][wax]
  | \bsignal\.(?:raise_signal|pthread_kill)\s*\(
  | \bsys\.(?:exit|stdout\.write|stderr\.write)\s*\(                       # stdout writes are harmless but exit halts runs
    """,
    re.X,
)
_PY_SUBPROCESS_RE = re.compile(
    r"\bsubprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)\s*\(\s*"
    r"(?P<arg>\[[^\]]*\]|\"[^\"\n]*\"|'[^'\n]*')"
)
_PY_TERMINAL_CALL_RE = re.compile(r"\bterminal\s*\(\s*(?:command\s*=\s*)?(?P<arg>\"[^\"\n]*\"|'[^'\n]*')")
_PY_AMBIGUOUS_RE = re.compile(
    r"""
    \b__import__\s*\( | \bimportlib\b | \beval\s*\( | \bexec\s*\( | \bcompile\s*\(
  | \bgetattr\s*\(\s*(?:__builtins__|builtins|os|sys|shutil|subprocess|hermes_tools)\b
  | \bglobals\s*\(\s*\)\s*\[ | \bvars\s*\(\s*\)\s*\[
  | \bbase64\.b64decode\s*\( | \bcodecs\.decode\s*\( | \bmarshal\b | \bctypes\b | \bcffi\b
  | \bsubprocess\b | \bpty\b | \bmultiprocessing\b | \bthreading\.Thread\b
  | \bos\.(?!path\b|listdir\b|walk\b|getcwd\b|environ\.get\b|environ\b|stat\b|scandir\b|getenv\b|cpu_count\b|getpid\b|getuid\b|getlogin\b|uname\b|sep\b|linesep\b|name\b|access\b|readlink\b|fspath\b|get_terminal_size\b|pathsep\b|devnull\b)\w+
  | \bsys\.(?:modules|settrace|setprofile|path\.(?:insert|append))\b
  | \burllib\.request\.(?:urlopen|Request|urlretrieve)\s*\( | \bhttp\.client\b | \bwebsocket
  | \bsocket\b
  | \bsqlite3\b | \bpsycopg\w*\b | \bpymysql\b | \bsqlalchemy\b | \bpymongo\b | \bredis\b
  | \bhermes_tools\.\w+\s*\(   # dynamic tool access not matched as read-only above
    """,
    re.X,
)
_PY_NETWORK_READ_RE = re.compile(r"\b(?:requests|httpx)\.(?:get|head|options)\s*\(|\burllib\.request\.urlopen\s*\(")


def code_body(args: dict[str, Any] | None) -> str:
    args = args if isinstance(args, dict) else {}
    for key in _CODE_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _strip_py_comments_and_docstrings(code: str) -> str:
    # Rough but deterministic: drop full-line comments and triple-quoted blocks so
    # explanatory prose cannot trigger or mask markers.
    code = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '""', code)
    return "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))


def _literal_command(arg: str) -> str:
    arg = arg.strip()
    if arg.startswith("["):
        try:
            parts = shlex.split(arg[1:-1].replace(",", " "), posix=True)
        except ValueError:
            return ""
        return " ".join(parts)
    return arg[1:-1]


def classify_code(code: str) -> ToolClassification:
    """Classify an ``execute_code`` body by the side-effect channels it uses."""
    raw = str(code or "")
    if not raw.strip():
        return _result(UNKNOWN, "unknown-tool-risk")
    text = _strip_py_comments_and_docstrings(raw)

    worst = _result(READ_ONLY, "execute-code-read-only")
    shell_found = False
    for match in list(_PY_SUBPROCESS_RE.finditer(text)) + list(_PY_TERMINAL_CALL_RE.finditer(text)):
        shell_found = True
        inner = _literal_command(match.group("arg"))
        inner_cls = classify_terminal(inner) if inner else _result(AMBIGUOUS, "execute-code-shell")
        if inner_cls.category == READ_ONLY:
            worst = _worst(worst, _result(READ_ONLY, "execute-code-read-only"))
        elif inner_cls.category == NETWORK_READ:
            worst = _worst(worst, _result(NETWORK_READ, "execute-code-network-read"))
        elif inner_cls.category == MUTATING:
            worst = _worst(worst, _result(MUTATING, "execute-code-mutating"))
        else:
            worst = _worst(worst, _result(AMBIGUOUS, "execute-code-shell"))

    # Remove the literal shell calls we already judged so the generic markers do
    # not re-flag them as mutating/ambiguous.
    scrubbed = _PY_SUBPROCESS_RE.sub("__shell__()", text)
    scrubbed = _PY_TERMINAL_CALL_RE.sub("__shell__()", scrubbed)
    if shell_found:
        scrubbed = re.sub(r"\bsubprocess\b", "__shellmod__", scrubbed)

    if _PY_MUTATING_RE.search(scrubbed):
        return _worst(worst, _result(MUTATING, "execute-code-mutating"))
    if _PY_AMBIGUOUS_RE.search(scrubbed):
        return _worst(worst, _result(AMBIGUOUS, "execute-code-ambiguous"))
    if _PY_NETWORK_READ_RE.search(scrubbed):
        return _worst(worst, _result(NETWORK_READ, "execute-code-network-read"))
    return _worst(worst, _result(READ_ONLY, "execute-code-read-only"))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def classify_tool(tool_name: str, args: dict[str, Any] | None = None, *, _depth: int = 0) -> ToolClassification:
    """Classify a tool call by name and, where safe, by its arguments.

    The returned reason is a fixed label; it never contains argument content.
    """
    name = str(tool_name or "").strip()
    lowered = name.lower()
    args = args if isinstance(args, dict) else {}

    if not lowered:
        return _result(UNKNOWN, "unknown-tool-risk")
    if lowered in _TERMINAL_TOOLS:
        command = terminal_command(args)
        return classify_terminal(command) if command else _result(AMBIGUOUS, "terminal-ambiguous")
    if lowered in _CODE_TOOLS:
        body = code_body(args)
        return classify_code(body) if body else _result(AMBIGUOUS, "execute-code-ambiguous")
    if lowered == "tool_call" and _depth < 2:
        calls = args.get("calls")
        if isinstance(calls, list) and calls:
            inner = [
                classify_tool(str(c.get("name") or ""), c.get("arguments") if isinstance(c.get("arguments"), dict) else {}, _depth=_depth + 1)
                for c in calls if isinstance(c, dict)
            ]
            if inner:
                return _worst(*inner)
        return _result(AMBIGUOUS, "ambiguous-tool")
    if lowered in READ_ONLY_TOOLS:
        return _result(READ_ONLY, "read-only-tool")
    if lowered in MUTATING_TOOLS:
        return _result(MUTATING, "mutating-tool")
    if lowered in AMBIGUOUS_TOOLS:
        return _result(AMBIGUOUS, "ambiguous-tool")

    tokens = _name_tokens(lowered)
    # MCP names look like mcp__server__tool; judge the tool part, not the server.
    if tokens[:1] == ["mcp"] and len(tokens) > 2:
        tokens = tokens[2:]
    if any(t in MUTATING_TOKENS for t in tokens):
        return _result(MUTATING, "mutating-tool")
    if any(t in READ_ONLY_TOKENS for t in tokens):
        return _result(READ_ONLY, "read-only-tool")
    return _result(UNKNOWN, "unknown-tool-risk")
