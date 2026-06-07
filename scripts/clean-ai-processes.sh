#!/usr/bin/env bash
set -euo pipefail

action="list"
target="all"
force=0
include_self=0
include_claude_helpers=1
show_detail=0
show_vscode_total=1
show_vscode_summary=0
verbose=0
grace_seconds=3

usage() {
  cat <<'USAGE'
Usage:
  scripts/clean-ai-processes.sh [options]

Options:
  --list                 Show detailed matching Codex/Claude processes.
  --target all           Match Codex and Claude processes. This is the default.
  --target codex         Match only Codex processes.
  --target claude        Match only Claude processes and Claude helper shells.
  --codex                Shortcut for --target codex.
  --claude               Shortcut for --target claude.
  --kill                 Send TERM to matching Codex/Claude processes.
  --force                With --kill, send KILL to survivors after a short grace period.
  --include-self         Also match this script's parent process chain.
  --no-claude-helpers    Do not match helper shells launched from ~/.claude snapshots.
  --vscode               Show VS Code Server process count, RSS, and top memory processes.
  --vscode-summary       Same as --vscode.
  --no-vscode-summary    Hide VS Code Server process count and RSS.
  --verbose              Show the full command line for each matched process.
  -h, --help             Show this help.

Default:
  With no options, print total Codex, Claude, and VS Code Server memory summary.

Safety:
  By default the script skips its own parent process chain. If you run it from inside
  Codex, it will not kill the current Codex session unless --include-self is set.
USAGE
}

while (($#)); do
  case "$1" in
    --list)
      action="list"
      show_detail=1
      ;;
    --target)
      if (($# < 2)); then
        echo "--target requires one of: all, codex, claude" >&2
        exit 2
      fi
      target="$2"
      show_detail=1
      case "$target" in
        all|codex|claude) ;;
        *)
          echo "Invalid --target: $target. Expected one of: all, codex, claude" >&2
          exit 2
          ;;
      esac
      shift
      ;;
    --codex)
      target="codex"
      show_detail=1
      ;;
    --claude)
      target="claude"
      show_detail=1
      ;;
    --kill)
      action="kill"
      show_detail=1
      ;;
    --force)
      force=1
      ;;
    --include-self)
      include_self=1
      ;;
    --no-claude-helpers)
      include_claude_helpers=0
      ;;
    --vscode-summary)
      show_vscode_summary=1
      ;;
    --vscode)
      show_vscode_summary=1
      ;;
    --no-vscode-summary)
      show_vscode_total=0
      show_vscode_summary=0
      ;;
    --verbose)
      verbose=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

declare -A skip_pids=()
if ((include_self == 0)); then
  current_pid=$$
  while [[ -n "${current_pid:-}" && "$current_pid" != "0" ]]; do
    skip_pids["$current_pid"]=1
    current_pid="$(ps -o ppid= -p "$current_pid" 2>/dev/null | awk '{print $1}')"
  done
fi

declare -a target_lines=()
declare -a target_pids=()

format_age() {
  local value="$1"
  if [[ "$value" == *-* ]]; then
    local days="${value%%-*}"
    local rest="${value#*-}"
    echo "${days}d ${rest}"
  else
    echo "$value"
  fi
}

shorten_path() {
  local value="$1"
  if [[ -z "$value" ]]; then
    echo "-"
    return
  fi
  value="${value/#$HOME/~}"
  if ((${#value} > 46)); then
    echo "...${value: -43}"
  else
    echo "$value"
  fi
}

process_cwd() {
  local pid="$1"
  readlink "/proc/$pid/cwd" 2>/dev/null || echo "-"
}

short_command() {
  local kind="$1"
  local args="$2"

  case "$kind" in
    claude)
      echo "claude"
      ;;
    codex|codex-node)
      if [[ "$args" == *" resume "* ]]; then
        local session="${args##* resume }"
        session="${session%% *}"
        echo "codex resume ${session:0:8}..."
      else
        echo "codex"
      fi
      ;;
    claude-helper)
      echo "claude shell-snapshot helper"
      ;;
    *)
      echo "${args:0:80}"
      ;;
  esac
}

collect_targets() {
  target_lines=()
  target_pids=()

  while read -r pid ppid pgid stat rss etime comm args; do
    [[ -z "${pid:-}" ]] && continue
    [[ -n "${skip_pids[$pid]:-}" ]] && continue

    kind=""
    if [[ "$comm" == "claude" ]]; then
      kind="claude"
    elif [[ "$comm" == "codex" ]]; then
      kind="codex"
    elif [[ "$comm" == "node" && ( "$args" == *"/bin/codex"* || "$args" == *"/@openai/codex"* || "$args" == *"/node_modules/@openai/codex"* ) ]]; then
      kind="codex-node"
    elif ((include_claude_helpers == 1)) && [[ "$args" == *"/.claude/shell-snapshots/"* || "$args" == *"/tmp/claude-"* ]]; then
      kind="claude-helper"
    fi

    [[ -z "$kind" ]] && continue
    if [[ "$target" == "codex" && "$kind" != codex* ]]; then
      continue
    fi
    if [[ "$target" == "claude" && "$kind" != claude* ]]; then
      continue
    fi
    target_pids+=("$pid")
    target_lines+=("$pid"$'\t'"$ppid"$'\t'"$rss"$'\t'"$etime"$'\t'"$stat"$'\t'"$kind"$'\t'"$(process_cwd "$pid")"$'\t'"$args")
  done < <(ps -eo pid=,ppid=,pgid=,stat=,rss=,etime=,comm=,args=)
}

print_targets() {
  local total_rss=0
  local count=0
  local codex_count=0
  local claude_count=0
  local codex_rss=0
  local claude_rss=0

  for line in "${target_lines[@]}"; do
    IFS=$'\t' read -r pid ppid rss etime stat kind cwd args <<<"$line"
    total_rss=$((total_rss + rss))
    count=$((count + 1))
    if [[ "$kind" == codex* ]]; then
      codex_count=$((codex_count + 1))
      codex_rss=$((codex_rss + rss))
    elif [[ "$kind" == claude* ]]; then
      claude_count=$((claude_count + 1))
      claude_rss=$((claude_rss + rss))
    fi
  done

  if ((show_detail == 0)); then
    printf "\nAI process memory summary\n"
    printf "Total: %d process(es), RSS: %.1f MiB\n" "$count" "$(awk "BEGIN {print $total_rss / 1024}")"
    printf "Codex: %d process(es), RSS: %.1f MiB\n" "$codex_count" "$(awk "BEGIN {print $codex_rss / 1024}")"
    printf "Claude: %d process(es), RSS: %.1f MiB\n" "$claude_count" "$(awk "BEGIN {print $claude_rss / 1024}")"
    if ((show_vscode_total == 1)); then
      print_vscode_total
    fi
    return
  fi

  printf "\nAI process cleanup candidates\n"
  printf "%-8s %-8s %-9s %-13s %-15s %-46s %s\n" "PID" "PPID" "RSS(MiB)" "AGE" "KIND" "CWD" "COMMAND"
  for line in "${target_lines[@]}"; do
    IFS=$'\t' read -r pid ppid rss etime stat kind cwd args <<<"$line"
    printf "%-8s %-8s %-9s %-13s %-15s %-46s %s\n" \
      "$pid" "$ppid" "$((rss / 1024))" "$(format_age "$etime")" "$kind" "$(shorten_path "$cwd")" "$(short_command "$kind" "$args")"
    if ((verbose == 1)); then
      printf "  full: %s\n" "$args"
    fi
  done

  printf "\nMatched: %d process(es), RSS: %.1f MiB\n" "$count" "$(awk "BEGIN {print $total_rss / 1024}")"
  if ((${#target_pids[@]} > 0)); then
    case "$target" in
      codex)
        printf "Kill command: %s --codex --kill\n" "$0"
        ;;
      claude)
        printf "Kill command: %s --claude --kill\n" "$0"
        ;;
      *)
        printf "Kill command: %s --kill\n" "$0"
        ;;
    esac
  fi
}

print_vscode_total() {
  ps -eo pid=,rss=,comm=,args= | awk '
    /\.vscode-server|vscode-remote-containers|code-server/ && $0 !~ /awk/ {
      count += 1
      rss += $2
    }
    END {
      printf "VS Code Server: %d process(es), RSS: %.1f MiB\n", count, rss / 1024
    }
  '
}

print_vscode_summary() {
  printf "\nVS Code Server summary\n"
  ps -eo pid=,rss=,comm=,args= | awk '
    /\.vscode-server|vscode-remote-containers|code-server/ && $0 !~ /awk/ {
      count += 1
      rss += $2
      kind = "other"
      if ($3 == "node") {
        kind = "node"
      } else if ($3 == "bash") {
        kind = "terminal-bash"
      } else if ($3 == "sh") {
        kind = "launcher-sh"
      } else if ($3 == "cpuUsage.sh") {
        kind = "cpuUsage"
      }
      kind_count[kind] += 1
      kind_rss[kind] += $2
    }
    END {
      printf "Total: %d process(es), RSS: %.1f MiB\n", count, rss / 1024
      for (kind in kind_count) {
        printf "  %-14s %3d process(es), RSS: %.1f MiB\n", kind, kind_count[kind], kind_rss[kind] / 1024
      }
    }
  '

  printf "\nVS Code Server top memory processes\n"
  printf "%-8s %-9s %-18s %s\n" "PID" "RSS(MiB)" "TYPE" "COMMAND"
  ps -eo pid=,rss=,comm=,args= --sort=-rss | awk '
    /\.vscode-server|vscode-remote-containers|code-server/ && $0 !~ /awk/ {
      kind = "other"
      command = $4
      if ($3 == "node" && $0 ~ /--type=extensionHost/) {
        kind = "extensionHost"
      } else if ($3 == "node" && $0 ~ /server-main\.js/) {
        kind = "server-main"
      } else if ($3 == "node" && $0 ~ /--type=ptyHost/) {
        kind = "ptyHost"
      } else if ($3 == "node" && $0 ~ /--type=fileWatcher/) {
        kind = "fileWatcher"
      } else if ($3 == "node" && $0 ~ /markdown-language-features/) {
        kind = "markdown-server"
      } else if ($3 == "node" && $0 ~ /json-language-features/) {
        kind = "json-server"
      } else if ($3 == "node" && $0 ~ /vscode-remote-containers/) {
        kind = "remote-containers"
      } else if ($3 == "node") {
        kind = "node"
      } else if ($3 == "bash") {
        kind = "terminal-bash"
        command = "integrated terminal shell"
      } else if ($3 == "sh") {
        kind = "launcher-sh"
        command = "VS Code WSL launcher"
      }

      if (kind == "extensionHost") {
        command = "VS Code extension host"
      } else if (kind == "server-main") {
        command = "VS Code server main"
      } else if (kind == "ptyHost") {
        command = "integrated terminal host"
      } else if (kind == "fileWatcher") {
        command = "file watcher"
      } else if (kind == "markdown-server") {
        command = "markdown language server"
      } else if (kind == "json-server") {
        command = "json language server"
      } else if (kind == "remote-containers") {
        command = "Remote Containers extension server"
      }

      printf "%-8s %-9.0f %-18s %s\n", $1, $2 / 1024, kind, command
      shown += 1
      if (shown >= 8) {
        exit
      }
    }
  '
}

collect_targets
print_targets

if ((show_vscode_summary == 1)); then
  echo
  print_vscode_summary
fi

if [[ "$action" != "kill" ]]; then
  exit 0
fi

if ((${#target_pids[@]} == 0)); then
  echo "Nothing to kill."
  exit 0
fi

echo
echo "Sending TERM to: ${target_pids[*]}"
for pid in "${target_pids[@]}"; do
  kill -TERM "$pid" 2>/dev/null || true
done

sleep "$grace_seconds"

declare -a survivors=()
for pid in "${target_pids[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    survivors+=("$pid")
  fi
done

if ((${#survivors[@]} == 0)); then
  echo "All matched processes exited."
  exit 0
fi

if ((force == 0)); then
  echo "Still running after ${grace_seconds}s: ${survivors[*]}"
  echo "Re-run with --kill --force to send KILL to survivors."
  exit 1
fi

echo "Sending KILL to survivors: ${survivors[*]}"
for pid in "${survivors[@]}"; do
  kill -KILL "$pid" 2>/dev/null || true
done
