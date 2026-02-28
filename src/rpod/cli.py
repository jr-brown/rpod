"""CLI argument parsing and dispatch for rpod."""

import argparse
import sys
from typing import Optional


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="rpod",
        description="RunPod CLI tool for managing GPU instances",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # === Pod Lifecycle (API) ===

    # create
    create_p = subparsers.add_parser("create", help="Create a new pod")
    create_p.add_argument("name", help="Name for the pod")
    create_p.add_argument("--gpu", help="GPU type (e.g., 'NVIDIA H100 80GB HBM3'). Can be set via default_gpu in .rpod.yaml")
    create_p.add_argument(
        "--image",
        default=None,
        help="Docker image",
    )
    create_p.add_argument(
        "--template-id",
        help="RunPod template ID (overrides --image)",
    )
    create_p.add_argument("--volume-size", type=int, default=None, help="Volume size in GB (default: 50)")
    create_p.add_argument(
        "--workspace",
        default=None,
        help="Workspace path (default: /workspace/<cwd-name>)",
    )
    create_p.add_argument("--gpu-count", type=int, default=1, help="Number of GPUs")
    create_p.add_argument(
        "--container-disk",
        type=int,
        default=None,
        help="Container disk size in GB (root filesystem, default: 100)",
    )
    create_p.add_argument(
        "--bootstrap",
        action="store_true",
        help="Full bootstrap: install rsync, push code, push .env, run setup",
    )
    create_p.add_argument(
        "--setup-follow",
        action="store_true",
        help="Stream setup output during bootstrap (otherwise runs in background)",
    )
    create_p.add_argument(
        "--models",
        metavar="LIST",
        help="Comma-separated models to download during bootstrap (e.g., 'Qwen/Qwen2.5-Coder-32B-Instruct')",
    )
    create_p.add_argument(
        "--all-regions",
        action="store_true",
        help="Bypass region_whitelist from .rpod.yaml (place pod in any region)",
    )
    create_p.add_argument(
        "--cpu",
        action="store_true",
        help="Create CPU-only pod (no GPU, cheapest option for orchestration)",
    )
    create_p.add_argument(
        "--cpu-type",
        default=None,
        help="CPU instance type (e.g., 'cpu3c-2-4')",
    )

    # stop
    stop_p = subparsers.add_parser("stop", help="Stop a running pod (keeps storage)")
    stop_p.add_argument("names", nargs="+", help="Pod name(s) or RunPod pod ID(s)")

    # start
    start_p = subparsers.add_parser("start", help="Start a stopped pod")
    start_p.add_argument("names", nargs="+", help="Pod name(s) or RunPod pod ID(s)")
    start_p.add_argument("--name-as", dest="name_as", help="Register pod under this name (when starting by pod ID, only valid with single pod)")
    start_p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for SSH info")

    # terminate
    terminate_p = subparsers.add_parser("terminate", help="Terminate pod (destroys it)")
    terminate_p.add_argument("names", nargs="+", help="Pod name(s) or RunPod pod ID(s)")
    terminate_p.add_argument("-f", "--force", action="store_true", help="Skip confirmation")

    # === Pod Registry ===

    # list
    list_p = subparsers.add_parser("list", aliases=["ls"], help="List registered pods")
    list_p.add_argument("--json", action="store_true", help="Output as JSON")
    list_p.add_argument("--refresh", action="store_true", help="Refresh status from API")

    # templates
    templates_p = subparsers.add_parser("templates", help="List RunPod templates")
    templates_p.add_argument("--raw", action="store_true", help="Output raw JSON")

    # register
    register_p = subparsers.add_parser("register", help="Manually register an existing pod")
    register_p.add_argument("name", help="Name for the pod")
    register_p.add_argument("ip", help="Pod IP address")
    register_p.add_argument("port", type=int, help="SSH port")
    register_p.add_argument("--workspace", default="/workspace", help="Workspace path")
    register_p.add_argument("--pod-id", help="RunPod API ID (for lifecycle commands)")

    # remove
    remove_p = subparsers.add_parser("remove", aliases=["rm"], help="Remove pod from registry")
    remove_p.add_argument("names", nargs="+", help="Pod name(s)")
    remove_p.add_argument("-f", "--force", action="store_true", help="Skip confirmation")

    # connect
    connect_p = subparsers.add_parser("connect", aliases=["ssh"], help="Interactive SSH session")
    connect_p.add_argument("name", help="Pod name")

    # === Execution ===

    # exec
    exec_p = subparsers.add_parser("exec", help="Execute command on pod")
    exec_p.add_argument("name", help="Pod name")
    exec_p.add_argument("cmd", help="Command to execute")
    exec_p.add_argument("-t", "--tmux", metavar="SESSION", help="Run in tmux session")
    exec_p.add_argument("--log", metavar="FILE", help="Log tmux output to file")
    exec_p.add_argument("--gpu", metavar="DEVICES", help="GPU device(s) to use (e.g., '0' or '0,1')")

    # === Sync ===

    # push
    push_p = subparsers.add_parser("push", help="Push local directory to pod")
    push_p.add_argument("name", help="Pod name")
    push_p.add_argument("--path", default=".", help="Local path to push")
    push_p.add_argument("--remote", help="Remote path (default: pod's workspace)")
    push_p.add_argument(
        "--exclude", "-e",
        nargs="*",
        metavar="PATTERN",
        help="Extra exclude patterns (added to .rpod.yaml push_excludes)",
    )
    push_p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds (default: 300)",
    )

    # pull
    pull_p = subparsers.add_parser("pull", help="Pull directory from pod")
    pull_p.add_argument("name", help="Pod name")
    pull_p.add_argument("remote", help="Remote path")
    pull_p.add_argument("--local", default=".", help="Local destination")
    pull_p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds (default: 300)",
    )

    # === Monitoring ===

    # status
    status_p = subparsers.add_parser("status", help="Show pod status (GPU, disk, processes)")
    status_p.add_argument("name", help="Pod name")
    status_p.add_argument(
        "--storage",
        action="store_true",
        help="Include storage breakdown (can be slow)",
    )

    # jobs
    jobs_p = subparsers.add_parser("jobs", help="List tmux sessions")
    jobs_p.add_argument("name", help="Pod name")

    # logs
    logs_p = subparsers.add_parser("logs", help="View tmux session logs")
    logs_p.add_argument("name", help="Pod name")
    logs_p.add_argument("session", help="Tmux session name")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    logs_p.add_argument("-n", "--lines", type=int, default=50, help="Number of lines to show")

    # kill-session
    kill_p = subparsers.add_parser("kill-session", help="Kill a tmux session")
    kill_p.add_argument("name", help="Pod name")
    kill_p.add_argument("session", help="Tmux session name to kill")

    # doctor
    doctor_p = subparsers.add_parser("doctor", help="Run diagnostics on a pod")
    doctor_p.add_argument("name", help="Pod name")

    # === API Commands ===

    # api - raw GraphQL query
    api_p = subparsers.add_parser("api", help="Raw GraphQL API query")
    api_p.add_argument("query", help="GraphQL query string")
    api_p.add_argument("-v", "--vars", help="Variables as JSON string")
    api_p.add_argument("--raw", action="store_true", help="Output raw JSON (no pretty-print)")

    # api-pods - quick pod query
    api_pods_p = subparsers.add_parser("api-pods", help="Query pods from API")
    api_pods_p.add_argument("pod_id", nargs="?", help="Pod ID (optional, lists all if omitted)")

    # list-gpus - list GPU types
    api_gpus_p = subparsers.add_parser("list-gpus", help="List available GPU types")
    api_gpus_p.add_argument("--raw", action="store_true", help="Output raw JSON")
    api_gpus_p.add_argument(
        "--min-vram", type=int, metavar="GB",
        help="Only show GPUs with at least this much VRAM (in GB)",
    )
    api_gpus_p.add_argument(
        "--all-regions",
        action="store_true",
        help="Bypass region_whitelist from .rpod.yaml (show GPUs in all regions)",
    )

    # === Environment ===

    # env - manage environment variables on pod
    env_p = subparsers.add_parser("env", help="Manage environment variables on pod")
    env_sub = env_p.add_subparsers(dest="env_command", help="env sub-commands")

    env_push_p = env_sub.add_parser("push", help="Push local .env file to pod")
    env_push_p.add_argument("name", help="Pod name")
    env_push_p.add_argument("--file", default=".env", help="Local env file (default: .env)")

    env_list_p = env_sub.add_parser("list", help="List env vars on pod (values masked)")
    env_list_p.add_argument("name", help="Pod name")

    # === Setup ===

    # setup - full pod setup (tools + deps + models)
    setup_p = subparsers.add_parser("setup", help="Set up pod (tools, deps, datasets)")
    setup_p.add_argument("name", help="Pod name")
    setup_p.add_argument(
        "--models",
        metavar="LIST",
        help="Comma-separated models to download (default: none)",
    )
    setup_p.add_argument(
        "--follow",
        action="store_true",
        help="Stream setup output (default: run in background)",
    )

    # setup-log
    setup_log_p = subparsers.add_parser("setup-log", help="Show latest setup log")
    setup_log_p.add_argument("name", help="Pod name")
    setup_log_p.add_argument("-n", "--lines", type=int, default=200, help="Number of lines to show")
    setup_log_p.add_argument("-f", "--follow", action="store_true", help="Follow log output")

    # === Download ===

    # download-model - download a HuggingFace model on a pod
    dl_p = subparsers.add_parser("download-model", help="Download a HuggingFace model on a pod")
    dl_p.add_argument("name", help="Pod name")
    dl_p.add_argument("model", help="Model name (e.g., 'Qwen/Qwen2.5-Coder-32B-Instruct')")
    dl_p.add_argument("--local-dir", help="Download to specific directory instead of HF cache")

    # hf-upload - upload a directory to HuggingFace from a pod
    ul_p = subparsers.add_parser("hf-upload", help="Upload a directory to HuggingFace from a pod")
    ul_p.add_argument("name", help="Pod name")
    ul_p.add_argument("local_path", help="Path on pod to upload (e.g., local/models/adapters/my-adapter)")
    ul_p.add_argument("repo_id", help="HuggingFace repo (e.g., username/my-model)")
    ul_p.add_argument("--repo-type", default="model", choices=["model", "dataset", "space"], help="Repo type")
    ul_p.add_argument("--public", action="store_true", help="Make repo public (default is private)")
    # Merged model handling for stacked adapters
    ul_p.add_argument(
        "--no-merged-base",
        action="store_true",
        help="Skip uploading merged model (adapter will point to original base model)",
    )
    ul_p.add_argument(
        "--merged-base-name",
        metavar="NAME",
        help="Override name for uploaded merged model repo",
    )
    ul_p.add_argument(
        "--use-existing-merged",
        nargs="?",
        const="auto",
        metavar="REPO",
        help="Point adapter at existing HF merged model, don't upload. If repo omitted, uses auto-generated name.",
    )

    # === Cleanup ===

    # clean - clean up space-wasting files
    clean_p = subparsers.add_parser("clean", help="Clean up space-wasting files on pod")
    clean_p.add_argument("name", help="Pod name")
    clean_p.add_argument(
        "targets",
        nargs="*",
        help="Targets to clean: tmp, checkpoints, pycache, logs, all (default: from .rpod.yaml or 'tmp')",
    )
    clean_p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be cleaned without actually cleaning",
    )

    return parser


def _init_logging() -> None:
    """Initialize logging from config files.

    Priority: .rpod.yaml log_level > ~/.rpod/config.toml log_level > off
    """
    from rpod.logging import init_logging

    log_level = "off"

    # Try global config first
    try:
        from rpod.config import load_config

        config = load_config()
        log_level = config.log_level
    except Exception:
        pass  # Config may not exist yet

    # Project config overrides global
    try:
        from rpod.project_config import load_project_config

        project_config = load_project_config()
        if project_config.log_level:
            log_level = project_config.log_level
    except Exception:
        pass

    init_logging(log_level)


def main(args: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 1

    # Initialize logging
    _init_logging()

    from rpod.logging import log_command, log_command_result, log_error
    import time

    # Dispatch to command handlers
    # Handle aliases
    command = parsed.command
    if command == "ls":
        command = "list"
    elif command == "rm":
        command = "remove"
    elif command == "ssh":
        command = "connect"

    # Log the command
    log_command(command, vars(parsed))
    start_time = time.monotonic()

    result: int = 1  # Default to error

    try:
        if command == "create":
            from rpod.commands.lifecycle import cmd_create
            result = cmd_create(
                parsed.name,
                parsed.gpu,
                parsed.image,
                parsed.template_id,
                parsed.volume_size,
                parsed.workspace,
                parsed.gpu_count,
                parsed.container_disk,
                parsed.bootstrap,
                parsed.models,
                parsed.setup_follow,
                parsed.all_regions,
                parsed.cpu,
                parsed.cpu_type,
            )
        elif command == "stop":
            from rpod.commands.lifecycle import cmd_stop
            result = cmd_stop(parsed.names)
        elif command == "start":
            from rpod.commands.lifecycle import cmd_start
            result = cmd_start(parsed.names, parsed.name_as, parsed.no_wait)
        elif command == "terminate":
            from rpod.commands.lifecycle import cmd_terminate
            result = cmd_terminate(parsed.names, parsed.force)
        elif command == "list":
            from rpod.commands.pods import cmd_list
            result = cmd_list(parsed.json, parsed.refresh)
        elif command == "templates":
            from rpod.commands.api import cmd_templates
            result = cmd_templates(parsed.raw)
        elif command == "register":
            from rpod.commands.pods import cmd_register
            result = cmd_register(
                parsed.name,
                parsed.ip,
                parsed.port,
                parsed.workspace,
                parsed.pod_id,
            )
        elif command == "remove":
            from rpod.commands.pods import cmd_remove
            result = cmd_remove(parsed.names, parsed.force)
        elif command == "connect":
            from rpod.commands.pods import cmd_connect
            result = cmd_connect(parsed.name)
        elif command == "exec":
            from rpod.commands.exec import cmd_exec
            result = cmd_exec(parsed.name, parsed.cmd, parsed.tmux, parsed.log, parsed.gpu)
        elif command == "push":
            from rpod.commands.sync import cmd_push
            result = cmd_push(parsed.name, parsed.path, parsed.remote, parsed.exclude, parsed.timeout)
        elif command == "pull":
            from rpod.commands.sync import cmd_pull
            result = cmd_pull(parsed.name, parsed.remote, parsed.local, parsed.timeout)
        elif command == "status":
            from rpod.commands.monitor import cmd_status
            result = cmd_status(parsed.name, include_storage=parsed.storage)
        elif command == "jobs":
            from rpod.commands.monitor import cmd_jobs
            result = cmd_jobs(parsed.name)
        elif command == "logs":
            from rpod.commands.monitor import cmd_logs
            result = cmd_logs(parsed.name, parsed.session, parsed.follow, parsed.lines)
        elif command == "kill-session":
            from rpod.commands.monitor import cmd_kill_session
            result = cmd_kill_session(parsed.name, parsed.session)
        elif command == "doctor":
            from rpod.commands.monitor import cmd_doctor
            result = cmd_doctor(parsed.name)
        elif command == "api":
            from rpod.commands.api import cmd_api
            result = cmd_api(parsed.query, parsed.vars, parsed.raw)
        elif command == "api-pods":
            from rpod.commands.api import cmd_api_pods
            result = cmd_api_pods(parsed.pod_id)
        elif command == "list-gpus":
            from rpod.commands.api import cmd_api_gpus
            result = cmd_api_gpus(raw=parsed.raw, min_vram=parsed.min_vram, all_regions=parsed.all_regions)
        elif command == "env":
            if not hasattr(parsed, "env_command") or not parsed.env_command:
                parser.parse_args(["env", "--help"])
                result = 1
            elif parsed.env_command == "push":
                from rpod.commands.env import cmd_env_push
                result = cmd_env_push(parsed.name, parsed.file)
            elif parsed.env_command == "list":
                from rpod.commands.env import cmd_env_list
                result = cmd_env_list(parsed.name)
            else:
                print(f"Unknown env command: {parsed.env_command}", file=sys.stderr)
                result = 1
        elif command == "setup":
            from rpod.commands.setup import cmd_setup
            result = cmd_setup(parsed.name, parsed.models, follow=parsed.follow)
        elif command == "setup-log":
            from rpod.commands.monitor import cmd_setup_log
            result = cmd_setup_log(parsed.name, parsed.lines, parsed.follow)
        elif command == "download-model":
            from rpod.commands.download import cmd_download_model
            result = cmd_download_model(parsed.name, parsed.model, parsed.local_dir)
        elif command == "hf-upload":
            from rpod.commands.download import cmd_hf_upload
            result = cmd_hf_upload(
                parsed.name,
                parsed.local_path,
                parsed.repo_id,
                parsed.repo_type,
                parsed.public,
                parsed.no_merged_base,
                parsed.merged_base_name,
                parsed.use_existing_merged,
            )
        elif command == "clean":
            from rpod.commands.clean import cmd_clean
            result = cmd_clean(parsed.name, parsed.targets, parsed.dry_run)
        else:
            print(f"Unknown command: {command}", file=sys.stderr)
            result = 1

        # Log successful completion
        log_command_result(command, result, int((time.monotonic() - start_time) * 1000))
        return result

    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        log_command_result(command, 130, int((time.monotonic() - start_time) * 1000))
        return 130
    except Exception as e:
        log_error(f"Command {command} failed: {e}", e)
        log_command_result(command, 1, int((time.monotonic() - start_time) * 1000))
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
