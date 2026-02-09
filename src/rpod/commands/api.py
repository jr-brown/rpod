"""Raw API commands for debugging and exploration."""

import json
import sys
from typing import Optional

from rpod.api import RunPodAPI, RunPodAPIError
from rpod.config import load_config


def cmd_api(query: str, variables: Optional[str] = None, raw: bool = False) -> int:
    """Execute a raw GraphQL query against RunPod API.

    Args:
        query: GraphQL query string
        variables: JSON string of variables (optional)
        raw: If True, print raw JSON without pretty-printing
    """
    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)

    vars_dict = None
    if variables:
        try:
            vars_dict = json.loads(variables)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in variables: {e}", file=sys.stderr)
            return 1

    try:
        result = api._request(query, vars_dict)
    except RunPodAPIError as e:
        print(f"API Error: {e}", file=sys.stderr)
        return 1

    if raw:
        print(json.dumps(result))
    else:
        print(json.dumps(result, indent=2))

    return 0


def cmd_api_pods(pod_id: Optional[str] = None) -> int:
    """Quick query: list pods or get pod details."""
    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)

    if pod_id:
        # Get specific pod with full details
        query = """
        query getPod($podId: String!) {
            pod(input: {podId: $podId}) {
                id
                name
                desiredStatus
                imageName
                costPerHr
                machine {
                    gpuDisplayName
                    podHostId
                }
                runtime {
                    uptimeInSeconds
                    ports {
                        ip
                        isIpPublic
                        privatePort
                        publicPort
                        type
                    }
                    gpus {
                        id
                        gpuUtilPercent
                        memoryUtilPercent
                    }
                }
            }
        }
        """
        try:
            result = api._request(query, {"podId": pod_id})
        except RunPodAPIError as e:
            print(f"API Error: {e}", file=sys.stderr)
            return 1

        print(json.dumps(result, indent=2))
    else:
        # List all pods
        query = """
        query {
            myself {
                pods {
                    id
                    name
                    desiredStatus
                    imageName
                    costPerHr
                    machine {
                        gpuDisplayName
                    }
                    runtime {
                        uptimeInSeconds
                        ports {
                            ip
                            isIpPublic
                            privatePort
                            publicPort
                            type
                        }
                    }
                }
            }
        }
        """
        try:
            result = api._request(query)
        except RunPodAPIError as e:
            print(f"API Error: {e}", file=sys.stderr)
            return 1

        print(json.dumps(result, indent=2))

    return 0


def cmd_api_gpus(raw: bool = False, min_vram: Optional[int] = None, all_regions: bool = False) -> int:
    """Quick query: list available GPU types.

    Args:
        raw: If True, output raw JSON instead of formatted table.
        min_vram: If set, only show GPUs with at least this much VRAM (in GB).
        all_regions: If True, bypass region_whitelist from .rpod.yaml.
    """
    from rpod.project_config import load_project_config

    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)
    project_config = load_project_config()

    # Resolve region whitelist to datacenter IDs
    datacenter_id: Optional[str] = None
    if not all_regions and project_config.region_whitelist:
        try:
            datacenter_id = api.resolve_regions(project_config.region_whitelist)
            print(f"Regions: {', '.join(project_config.region_whitelist)} (use --all-regions to bypass)")
        except RunPodAPIError as e:
            print(f"Error resolving regions: {e}", file=sys.stderr)
            return 1

    # Build lowestPrice input with optional datacenter constraint
    if datacenter_id:
        lowest_price_input = f'input: {{gpuCount: 1, dataCenterId: "{datacenter_id}"}}'
    else:
        lowest_price_input = "input: {gpuCount: 1}"

    query = f"""
    query {{
        gpuTypes {{
            id
            displayName
            memoryInGb
            secureCloud
            communityCloud
            lowestPrice({lowest_price_input}) {{
                minimumBidPrice
                uninterruptablePrice
            }}
        }}
    }}
    """
    try:
        result = api._request(query)
    except RunPodAPIError as e:
        print(f"API Error: {e}", file=sys.stderr)
        return 1

    if raw:
        print(json.dumps(result, indent=2))
        return 0

    gpus = result.get("gpuTypes", [])

    if min_vram is not None:
        gpus = [g for g in gpus if g.get("memoryInGb", 0) >= min_vram]

    # If region-filtered, remove GPUs with no availability in those regions
    if datacenter_id:
        gpus = [g for g in gpus if (g.get("lowestPrice") or {}).get("uninterruptablePrice") is not None]

    # Sort by on-demand price (unavailable last)
    def sort_key(g: dict) -> float:
        p = (g.get("lowestPrice") or {}).get("uninterruptablePrice")
        return p if p is not None else 9999.0

    gpus.sort(key=sort_key)

    # Print formatted table
    print(f"{'GPU':<25s} {'VRAM':>5s}  {'$/hr':>6s}  {'Bid':>6s}  {'Cloud':>10s}  ID")
    print("-" * 90)
    for g in gpus:
        lp = g.get("lowestPrice") or {}
        price = lp.get("uninterruptablePrice")
        bid = lp.get("minimumBidPrice")
        price_str = f"${price:.2f}" if price else "n/a"
        bid_str = f"${bid:.2f}" if bid else "-"
        clouds = []
        if g.get("secureCloud"):
            clouds.append("secure")
        if g.get("communityCloud"):
            clouds.append("community")
        cloud_str = ",".join(clouds) or "-"
        print(
            f"{g['displayName']:<25s} {g['memoryInGb']:>4d}G  {price_str:>6s}  {bid_str:>6s}  {cloud_str:>10s}  {g['id']}"
        )

    return 0


def cmd_templates(raw: bool = False) -> int:
    """List RunPod templates via REST API."""
    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)

    try:
        result = api.list_templates()
    except RunPodAPIError as e:
        print(f"API Error: {e}", file=sys.stderr)
        return 1

    if raw:
        print(json.dumps(result, indent=2))
        return 0

    templates = None
    if isinstance(result, list):
        templates = result
    elif isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            templates = data
        elif isinstance(data, dict) and isinstance(data.get("templates"), list):
            templates = data.get("templates")
        elif isinstance(result.get("templates"), list):
            templates = result.get("templates")

    if not templates:
        print(json.dumps(result, indent=2))
        return 0

    print(f"{'ID':<14s} {'Name':<28s} {'Image':<36s} {'Public':<6s}")
    print("-" * 90)
    for t in templates:
        tid = str(t.get("id") or t.get("templateId") or t.get("_id") or "-")
        name = str(t.get("name") or t.get("templateName") or "-")
        image = str(t.get("imageName") or t.get("image") or "-")
        public = t.get("isPublic")
        if public is None:
            public = t.get("public")
        public_str = "yes" if public is True else ("no" if public is False else "-")
        print(f"{tid:<14s} {name:<28.28s} {image:<36.36s} {public_str:<6s}")

    return 0
