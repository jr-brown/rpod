# rpod - Issues & Improvement Ideas

This document tracks open issues and potential improvements for the rpod CLI tool.

## Open Issues

### 0. Shell quoting and argument injection in remote commands

**Problem:** Several commands build remote shell strings using unquoted user-controlled
values (e.g., `workdir`, `workspace`, `session`, `log_file`). This can break on
spaces and allows unintended shell interpretation if values contain metacharacters.

**Affected paths:**
- `tools/rpod/commands/exec.py`
- `tools/rpod/commands/monitor.py`
- `tools/rpod/commands/clean.py`
- `tools/rpod/commands/env.py`

**Potential fix:**
- Shell-escape paths and identifiers (e.g., `shlex.quote`) or validate/whitelist
  allowed characters for names/paths.

### 0.1 rsync SSH key path not shell-escaped

**Problem:** `_ssh_string()` builds a single `rsync -e` command string without
quoting the SSH key path. If the key path contains spaces or shell-sensitive
characters, sync operations fail.

**Affected paths:**
- `tools/rpod/ssh.py`

**Potential fix:**
- Quote/escape the key path or use a list-based command for `rsync -e`.

### 0.2 `.env` heredoc terminator collision (rare)

**Problem:** `rpod env push` uploads `.env` via heredoc. If the file contains the
literal line `RPOD_ENV_EOF`, the upload truncates early.

**Affected paths:**
- `tools/rpod/commands/env.py`

**Potential fix:**
- Use a randomized heredoc marker or transfer via `scp` with explicit port args.

### 1. Disk quota errors are unclear

**Problem:** When disk quota is exceeded during model operations, the error is buried in a Python traceback.

**Potential fix:**
- Detect common quota errors and suggest solutions (increase container disk, run `rpod clean`, etc.)

### 2. Bootstrap output is duplicated in logs

**Problem:** During `rpod create --bootstrap`, if SSH connections are retried (e.g., while waiting for pod to become ready), the output from setup commands can appear multiple times in the task output. This is a logging artifact - setup doesn't actually run multiple times, but the duplicated output can be confusing.

**Observed:** The same rsync file list, dependency downloads, and "=== Installing common tools ===" messages appear 3-4 times in the output log.

**Root cause:** Likely related to SSH retry logic during bootstrap when the pod is still initializing. Each retry may capture/echo previous output.

**Potential fix:**
- Deduplicate output in the logging layer
- Or use a fresh output buffer for each SSH attempt

---

## Improvement Ideas

### Medium Priority

1. **Resource estimation**
   - Estimate resource requirements before running:
     ```bash
     rpod estimate --config train.yaml
     # Estimated: 73GB VRAM, 91GB disk
     # Recommended: H100 80GB, 150GB volume
     ```

2. **Multi-pod workflows / parallel evals**
   - Support running commands across multiple pods
   - Helpers for parallel GPU execution:
     ```bash
     rpod parallel eval --gpu-each 1 "cmd1" "cmd2" "cmd3"
     # Automatically assigns CUDA_VISIBLE_DEVICES=0, 1, 2
     ```

3. **Pod health monitoring (`rpod watch`)**
   - Continuous status monitoring with alerts
   - Optional webhook/notification integration

### Low Priority

4. **Clear tmux history on new command**
   - `rpod logs` shows full tmux scrollback including old errors from previous runs
   - Makes it confusing to see if current run succeeded or failed
   - Fix: Have `rpod exec -t` clear tmux scrollback before running new command
   - Alternative: Add `rpod logs session --clear` to clear history manually
   - Implementation: `tmux clear-history -t {session}` before sending command

5. **Auto-recovery**
   - Detect when pod loses IP or exits unexpectedly
   - Option: `rpod create dev --auto-restart`

6. **Cost tracking**
   - Track cumulative costs per pod
   - Alerts when spending exceeds threshold
   - `rpod status dev --cost` shows accumulated spend

7. **Pod templates**
   - Save and reuse pod configurations:
     ```bash
     rpod save-template dev --name "h100-training"
     rpod create new-pod --template "h100-training"
     ```

8. **Auto-detect stale cache directories**
   - During `rpod status`, flag potentially stale directories
   - Old checkpoints, orphaned merged models, etc.

9. **Symlink /tmp to /workspace**
   - Option in `rpod setup` to symlink `/tmp` → `/workspace/tmp`
   - Prevents root filesystem from filling up

---

*Last updated: 2026-02-06*
