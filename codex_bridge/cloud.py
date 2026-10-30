"""Official host-side Remote Control diagnostics; no private relay API client."""
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

from .rpc import AppServer


HOST_METHODS = frozenset({
    "remoteControl/enable", "remoteControl/disable", "remoteControl/status/read",
    "remoteControl/pairing/start", "remoteControl/pairing/status",
    "remoteControl/client/list", "remoteControl/client/revoke",
})


async def inspect_cloud():
    """Inspect schemas from the installed binary, without contacting the relay."""
    with tempfile.TemporaryDirectory(prefix="codex-bridge-schema-") as root:
        process = await asyncio.create_subprocess_exec(
            "codex", "app-server", "generate-json-schema", "--experimental", "--out", root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.communicate(), 30)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            raise RuntimeError("Codex schema generation failed; inspect your CLI installation")
        schema = json.loads(Path(root, "ClientRequest.json").read_text())
    methods = sorted({
        method for variant in schema.get("oneOf", [])
        for method in variant.get("properties", {}).get("method", {}).get("enum", [])
        if method.startswith("remoteControl/")
    })
    if not methods:
        raise RuntimeError("No Remote Control methods found; schema layout or CLI may differ")
    return {
        "type": "cloud_capabilities", "remote_control_methods": methods,
        "unclassified_methods": sorted(set(methods) - HOST_METHODS),
        "scope": "installed App Server request schema; not a scan of private cloud APIs",
        "bridge_cloud_client_implemented": False,
        "pairing_status_is_accept": False,
    }


def error_category(exc):
    # Error bodies may contain host identity, pairing artifacts, or account details.
    # Return a coarse diagnostic instead of logging the upstream exception.
    value = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return "timeout"
    if "multi-factor" in value or "mfa" in value:
        return "mfa_required"
    if "401" in value or "sign in" in value or "authentication" in value:
        return "authentication_required"
    if "403" in value or "requirements" in value or "permission" in value:
        return "access_denied"
    if "429" in value or "retry after" in value:
        return "rate_limited"
    return "rpc_or_connection_error"


def transport_diagnostic(lines):
    """Extract only HTTP status from structured relay warnings, never raw fields."""
    for line in reversed(list(lines)):
        try:
            fields = json.loads(line).get("fields", {})
        except (ValueError, AttributeError):
            continue
        if fields.get("message") != "failed to connect to app-server remote control websocket":
            continue
        error = str(fields.get("error", ""))
        match = re.search(r"HTTP[^0-9]{0,15}([1-5][0-9][0-9])", error, re.I)
        if match:
            status = int(match[1])
            return {"http_status": status,
                    "category": "conflict" if status == 409 else "handshake_or_server_error"}
    return {}


async def probe_host(server, emit, timeout=30):
    """Temporarily enable a dedicated server; never alter daemon preferences.

    Pairing artifacts remain only in memory and are never included in results.
    This validates the HOST role, not two-host connectivity or a client role.
    """
    stage = "status_read"
    enable_attempted = False
    report = {"type": "cloud_probe", "host_probe": "failed", "model_called": False,
              "pairing_code_issued": False, "a2a_verified": False,
              "client_role": "not_implemented", "cleanup": "not_needed"}
    try:
        status = await server.call("remoteControl/status/read", {})
        emit({"stage": stage, "status": status["status"]})
        stage = "enable_ephemeral"
        enable_attempted = True
        await server.call("remoteControl/enable", {"ephemeral": True})
        stage = "pairing_start"
        pairing = await server.call("remoteControl/pairing/start", {"manualCode": True})
        code = pairing.get("manualPairingCode")
        if not isinstance(code, str) or not code:
            raise RuntimeError("Remote Control did not issue a manual code")
        report["pairing_code_issued"] = True
        emit({"stage": stage, "code_issued": True, "code": "<redacted>"})
        stage = "pairing_status"
        result = await server.call("remoteControl/pairing/status", {"manualPairingCode": code})
        if not isinstance(result.get("claimed"), bool):
            raise RuntimeError("Unexpected pairing status response")
        report["pairing_claimed"] = result["claimed"]
        stage = "relay_connect"
        async with asyncio.timeout(timeout):
            while True:
                status = await server.call("remoteControl/status/read", {})
                if status["status"] == "connected":
                    break
                if status["status"] in {"errored", "disabled"}:
                    report["relay_status"] = status["status"]
                    raise RuntimeError("Remote Control connection failed")
                await asyncio.sleep(0.5)
        report["relay_connected"] = True
        emit({"stage": stage, "status": "connected"})
        report["host_probe"] = "passed"
    except (RuntimeError, TimeoutError, OSError) as exc:
        report["failed_stage"] = stage
        report["error_category"] = error_category(exc)
        diagnostic = transport_diagnostic(getattr(server, "stderr_tail", ()))
        if diagnostic:
            report["transport_diagnostic"] = diagnostic
    finally:
        if enable_attempted:
            try:
                result = await server.call("remoteControl/disable", {"ephemeral": True})
                report["cleanup"] = "passed" if result.get("status") == "disabled" else "failed"
            except (RuntimeError, TimeoutError, OSError):
                report["cleanup"] = "failed"
    return report


async def cloud_probe(timeout=30):
    def emit(value):
        print(json.dumps(value), flush=True)

    # A separate process, not `app-server proxy` into a user's active daemon.
    env = {**os.environ, "RUST_LOG": "codex_app_server_transport=warn", "LOG_FORMAT": "json"}
    async with AppServer(timeout=45, experimental=True, env=env) as server:
        report = await probe_host(server, emit, timeout)
    emit(report)
    if report["host_probe"] != "passed" or report["cleanup"] != "passed":
        return 1
    return 0
