"""RunPod REST API client."""

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Optional, Union

from rpod.logging import log_api, log_error

RUNPOD_API_URL = "https://api.runpod.io/graphql"
RUNPOD_REST_URL = "https://rest.runpod.io/v1"


@dataclass
class PodStatus:
    """Pod status from API."""

    pod_id: str
    name: str
    status: str  # RUNNING, STOPPED, EXITED, etc.
    gpu_type: Optional[str]
    public_ip: Optional[str]
    ssh_port: Optional[int]
    cost_per_hour: Optional[float | int]


class RunPodAPIError(Exception):
    """Error from RunPod API."""

    pass


class RunPodAPI:
    """Client for RunPod GraphQL API."""

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def _request(
        self, query: str, variables: Optional[dict] = None, operation: str = "query"
    ) -> dict[str, Any]:
        """Make a GraphQL request to RunPod API."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        log_api(operation, variables=variables or {})
        start_time = time.monotonic()

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            RUNPOD_API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "rpod/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            duration_ms = int((time.monotonic() - start_time) * 1000)
            log_api(operation, error=f"HTTP {e.code}: {body}", duration_ms=duration_ms)
            raise RunPodAPIError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            log_api(operation, error=f"Connection error: {e.reason}", duration_ms=duration_ms)
            raise RunPodAPIError(f"Connection error: {e.reason}") from e

        if "errors" in result:
            errors = result["errors"]
            msg = errors[0].get("message", str(errors)) if errors else str(errors)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            log_api(operation, error=f"GraphQL error: {msg}", duration_ms=duration_ms)
            raise RunPodAPIError(f"GraphQL error: {msg}")

        duration_ms = int((time.monotonic() - start_time) * 1000)
        log_api(operation, response=result.get("data", {}), duration_ms=duration_ms)
        return result.get("data", {})

    def _rest_request(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        operation: str = "rest",
    ) -> Union[dict[str, Any], list[Any]]:
        """Make a REST request to RunPod API."""
        url = f"{RUNPOD_REST_URL}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "rpod/1.0",
            },
            method=method,
        )

        log_api(operation, variables=payload or {})
        start_time = time.monotonic()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                result = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            duration_ms = int((time.monotonic() - start_time) * 1000)
            log_api(operation, error=f"HTTP {e.code}: {body}", duration_ms=duration_ms)
            raise RunPodAPIError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            log_api(operation, error=f"Connection error: {e.reason}", duration_ms=duration_ms)
            raise RunPodAPIError(f"Connection error: {e.reason}") from e

        duration_ms = int((time.monotonic() - start_time) * 1000)
        log_response: dict[str, Any]
        if isinstance(result, dict):
            log_response = result
        else:
            log_response = {"data": result}
        log_api(operation, response=log_response, duration_ms=duration_ms)
        return result

    def list_templates(self) -> Union[dict[str, Any], list[Any]]:
        """List available templates via REST API."""
        return self._rest_request("GET", "/templates", operation="list_templates")

    def list_pods(self) -> list[PodStatus]:
        """List all pods for this account."""
        query = """
        query {
            myself {
                pods {
                    id
                    name
                    desiredStatus
                    runtime {
                        gpus {
                            gpuUtilPercent
                        }
                    }
                    machine {
                        gpuDisplayName
                    }
                    costPerHr
                }
            }
        }
        """
        data = self._request(query, operation="list_pods")
        pods_data = data.get("myself", {}).get("pods", [])

        pods = []
        for p in pods_data:
            pods.append(
                PodStatus(
                    pod_id=p["id"],
                    name=p["name"],
                    status=p.get("desiredStatus", "UNKNOWN"),
                    gpu_type=p.get("machine", {}).get("gpuDisplayName"),
                    public_ip=None,  # Need separate query for network info
                    ssh_port=None,
                    cost_per_hour=p.get("costPerHr"),
                )
            )
        return pods

    def get_pod(self, pod_id: str) -> PodStatus:
        """Get detailed info for a specific pod."""
        query = """
        query getPod($podId: String!) {
            pod(input: {podId: $podId}) {
                id
                name
                desiredStatus
                runtime {
                    uptimeInSeconds
                    ports {
                        ip
                        isIpPublic
                        privatePort
                        publicPort
                    }
                    gpus {
                        gpuUtilPercent
                    }
                }
                machine {
                    gpuDisplayName
                }
                costPerHr
            }
        }
        """
        data = self._request(query, {"podId": pod_id}, operation="get_pod")
        p = data.get("pod")
        if not p:
            raise RunPodAPIError(f"Pod not found: {pod_id}")

        # Extract SSH port (port 22 mapping)
        public_ip = None
        ssh_port = None
        runtime = p.get("runtime") or {}
        ports = runtime.get("ports") or []
        for port in ports:
            if port.get("privatePort") == 22 and port.get("isIpPublic"):
                public_ip = port.get("ip")
                ssh_port = port.get("publicPort")
                break

        return PodStatus(
            pod_id=p["id"],
            name=p["name"],
            status=p.get("desiredStatus", "UNKNOWN"),
            gpu_type=p.get("machine", {}).get("gpuDisplayName"),
            public_ip=public_ip,
            ssh_port=ssh_port,
            cost_per_hour=p.get("costPerHr"),
        )

    # Map user-friendly region names to datacenter ID prefixes
    REGION_PREFIX_MAP: dict[str, list[str]] = {
        "NORTH_AMERICA": ["US-", "CA-"],
        "EUROPE": ["EU-", "EUR-"],
        "ASIA": ["AP-", "SEA-"],
        "OCEANIA": ["OC-"],
    }

    def list_datacenters(self) -> list[dict[str, Any]]:
        """List all RunPod datacenters.

        Returns:
            List of dicts with keys: id, name, location.
        """
        query = """
        query {
            dataCenters {
                id
                name
                location
            }
        }
        """
        data = self._request(query, operation="list_datacenters")
        return data.get("dataCenters", [])

    def resolve_regions(self, regions: list[str]) -> str:
        """Resolve region names to a comma-separated string of datacenter IDs.

        Region names are mapped to datacenter ID prefixes:
            NORTH_AMERICA → US-*, CA-*
            EUROPE → EU-*, EUR-*
            ASIA → AP-*, SEA-*
            OCEANIA → OC-*

        Args:
            regions: List of region names (e.g., ["NORTH_AMERICA", "EUROPE"]).

        Returns:
            Comma-separated datacenter IDs (e.g., "US-TX-3,US-GA-1,EU-RO-1").

        Raises:
            RunPodAPIError: If a region name is unknown or no datacenters match.
        """
        # Validate region names
        unknown = [r for r in regions if r not in self.REGION_PREFIX_MAP]
        if unknown:
            raise RunPodAPIError(
                f"Unknown region(s): {unknown}. "
                f"Valid regions: {sorted(self.REGION_PREFIX_MAP.keys())}"
            )

        # Collect prefixes for requested regions
        prefixes: list[str] = []
        for region in regions:
            prefixes.extend(self.REGION_PREFIX_MAP[region])

        datacenters = self.list_datacenters()
        matched_ids = [
            dc["id"] for dc in datacenters
            if any(dc["id"].startswith(prefix) for prefix in prefixes)
        ]
        if not matched_ids:
            raise RunPodAPIError(
                f"No datacenters found for regions: {regions} "
                f"(prefixes: {prefixes})"
            )
        return ",".join(matched_ids)

    def create_pod(
        self,
        name: str,
        gpu_type: str,
        image: Optional[str] = None,
        template_id: Optional[str] = None,
        volume_size: int = 50,
        volume_mount: str = "/workspace",
        gpu_count: int = 1,
        container_disk: int = 100,
        datacenter_id: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> str:
        """Create a new pod via GraphQL mutation.

        Args:
            name: Pod name
            gpu_type: GPU type (e.g., "NVIDIA H100 80GB HBM3", "NVIDIA RTX A4000")
            image: Docker image (required unless template_id is provided)
            template_id: RunPod template ID (optional)
            volume_size: Persistent volume size in GB
            volume_mount: Mount path for volume
            gpu_count: Number of GPUs
            container_disk: Container disk size in GB (root filesystem)
            datacenter_id: Comma-separated datacenter IDs to constrain placement (optional)
            env: Environment variables to set on the pod (optional)

        Returns:
            Pod ID
        """
        if not template_id and not image:
            raise RunPodAPIError("Either image or template_id is required to create a pod")

        query = """
        mutation createPod($input: PodFindAndDeployOnDemandInput!) {
            podFindAndDeployOnDemand(input: $input) {
                id
                desiredStatus
            }
        }
        """
        input_vars: dict[str, Any] = {
            "name": name,
            "gpuTypeId": gpu_type,
            "gpuCount": gpu_count,
            "volumeInGb": volume_size,
            "volumeMountPath": volume_mount,
            "containerDiskInGb": container_disk,
            "supportPublicIp": True,
            "ports": "22/tcp",
            "cloudType": "ALL",
        }
        if template_id:
            input_vars["templateId"] = template_id
        else:
            input_vars["imageName"] = image
        if datacenter_id:
            input_vars["dataCenterId"] = datacenter_id
        if env:
            input_vars["env"] = [{"key": k, "value": v} for k, v in env.items()]

        data = self._request(query, {"input": input_vars}, operation="create_pod")
        pod = data.get("podFindAndDeployOnDemand", {})
        pod_id = pod.get("id")
        if not pod_id:
            raise RunPodAPIError("Failed to create pod - no pod ID in response")
        return pod_id

    def stop_pod(self, pod_id: str) -> None:
        """Stop a running pod (preserves storage)."""
        query = """
        mutation stopPod($podId: String!) {
            podStop(input: {podId: $podId}) {
                id
                desiredStatus
            }
        }
        """
        self._request(query, {"podId": pod_id}, operation="stop_pod")

    def start_pod(self, pod_id: str) -> None:
        """Start a stopped pod."""
        query = """
        mutation startPod($podId: String!) {
            podResume(input: {podId: $podId, gpuCount: 1}) {
                id
                desiredStatus
            }
        }
        """
        self._request(query, {"podId": pod_id}, operation="start_pod")

    def terminate_pod(self, pod_id: str) -> None:
        """Terminate a pod (destroys it permanently)."""
        query = """
        mutation terminatePod($podId: String!) {
            podTerminate(input: {podId: $podId})
        }
        """
        self._request(query, {"podId": pod_id}, operation="terminate_pod")
