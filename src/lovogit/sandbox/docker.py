import asyncio
import json
import re
import tarfile
from io import BytesIO
from pathlib import Path

from lovogit.cfg.sandbox import SandboxSettings

from .storage import SandboxError, repository_tar


class DockerRuntime:
    def __init__(self, settings: SandboxSettings):
        self._settings = settings

    async def image_info(self) -> tuple[str, str]:
        code, output = await self._command(["image", "inspect", self._settings.image])
        if code:
            raise SandboxError(
                "Sandbox image inspection failed; build image and check Docker daemon"
            )
        metadata = json.loads(output)[0]
        return metadata["Id"], f"{metadata['Os']}/{metadata['Architecture']}"

    async def execute(
        self,
        identifier: str,
        image: str,
        mode: str,
        request: dict,
        *,
        source: Path | None = None,
        bundle: Path | None = None,
        timeout: int,
        network: str,
    ) -> tuple[dict, bytes, dict[str, bytes]]:
        if network in {"host", "bridge", "default"} or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", network
        ):
            raise ValueError(
                "Sandbox requires none or an explicitly filtered custom network"
            )
        name = f"lovogit-sandbox-{identifier}"
        artifacts_volume = f"{name}-artifacts"
        limits = self._settings.limits
        arguments = [
            "create",
            "--name",
            name,
            "--label",
            "lovogit.sandbox=true",
            "--user",
            "1000:1000",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--read-only",
            "--cpus",
            str(limits.cpus),
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--pids-limit",
            str(limits.pids_limit),
            "--ulimit",
            "nofile=1024:1024",
            "--network",
            network,
            "--ipc=private",
            "--init",
            "--log-driver=none",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={limits.memory_mb}m,uid=1000,gid=1000,mode=1777",
            "--mount",
            f"type=volume,src=lovogit-sandbox-env-{request['environment_id']},dst=/workspace"
            + (",readonly" if mode == "test" else ""),
            "--mount",
            f"type=volume,src={artifacts_volume},dst=/artifacts",
            "--env",
            "HOME=/tmp",
            "--env",
            "UV_CACHE_DIR=/tmp/uv-cache",
            "--env",
            "UV_PYTHON_DOWNLOADS=never",
            "--env",
            "UV_FIND_LINKS=/opt/wheels",
            "--env",
            f"UV_OFFLINE={str(network == 'none').lower()}",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONPATH=/workspace/repository:/workspace/repository/src",
            "--workdir",
            "/workspace/repository",
            "--entrypoint",
            "/usr/local/bin/python",
        ]
        if bundle is not None:
            arguments += ["--mount", f"type=bind,src={bundle},dst=/generated,readonly"]
        arguments += [image, "-I", "/opt/sandbox/worker.py", mode, json.dumps(request)]
        output = b""
        files = {}
        state = {}
        try:
            code, _ = await self._command(arguments)
            if code:
                raise SandboxError(f"Sandbox container creation failed: {identifier}")
            if source is not None:
                code, _ = await self._command(
                    ["cp", "-a", "-", f"{name}:/workspace/repository"],
                    data=repository_tar(source),
                )
                if code:
                    raise SandboxError(f"Sandbox source transfer failed: {identifier}")
            try:
                code, output = await self._command(
                    ["start", "--attach", name],
                    timeout=timeout,
                    limit=min(
                        limits.max_output_bytes, self._settings.max_artifact_bytes
                    ),
                    merge=True,
                )
                if code == 124:
                    await self._command(["kill", name])
                    state["TimedOut"] = True
            except TimeoutError:
                await self._command(["kill", name])
                state["TimedOut"] = True
            code, metadata = await self._command(
                ["inspect", "--format", "{{json .State}}", name]
            )
            if code:
                raise SandboxError(f"Sandbox container inspection failed: {identifier}")
            state.update(json.loads(metadata))
            for filename in (
                "preparation.json",
                "resolved.lock",
                "report.json",
                "coverage.json",
            ):
                code, data = await self._command(
                    ["cp", f"{name}:/artifacts/{filename}", "-"],
                    limit=self._settings.max_artifact_bytes + 16384,
                )
                if code:
                    continue
                try:
                    with tarfile.open(fileobj=BytesIO(data)) as archive:
                        entries = archive.getmembers()
                        if (
                            len(entries) != 1
                            or not entries[0].isfile()
                            or entries[0].size > self._settings.max_artifact_bytes
                        ):
                            raise SandboxError(
                                f"Invalid sandbox artifact: {identifier}/{filename}"
                            )
                        stream = archive.extractfile(entries[0])
                        if stream is not None:
                            files[filename] = stream.read(
                                self._settings.max_artifact_bytes + 1
                            )
                except tarfile.TarError as exc:
                    raise SandboxError(
                        f"Invalid sandbox artifact archive: {identifier}/{filename}"
                    ) from exc
            return state, output, files
        finally:
            await self._command(["rm", "--force", name])
            await self._command(["volume", "rm", artifacts_volume])

    async def cleanup(self, identifier: str, *, environment: bool = False) -> None:
        await self._command(["rm", "--force", f"lovogit-sandbox-{identifier}"])
        await self._command(["volume", "rm", f"lovogit-sandbox-{identifier}-artifacts"])
        if environment:
            code, output = await self._command(
                ["volume", "rm", "--force", f"lovogit-sandbox-env-{identifier}"]
            )
            if code and b"no such volume" not in output.lower():
                raise SandboxError(f"Sandbox volume cleanup failed: {identifier}")

    async def _command(
        self,
        arguments: list[str],
        *,
        data: bytes | None = None,
        timeout: int = 30,
        limit: int | None = None,
        merge: bool = False,
    ) -> tuple[int, bytes]:
        cap = limit or self._settings.max_artifact_bytes
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                *arguments,
                stdin=asyncio.subprocess.PIPE if data else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT if merge else asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SandboxError("Sandbox Docker CLI is unavailable") from exc

        buffers = [bytearray(), bytearray()]

        async def drain(stream: asyncio.StreamReader | None, output: bytearray) -> None:
            if stream is None:
                return
            while chunk := await stream.read(65536):
                output.extend(chunk[: max(0, cap - len(output))])

        async def send() -> None:
            if data and process.stdin:
                try:
                    process.stdin.write(data)
                    await process.stdin.drain()
                except BrokenPipeError, ConnectionResetError:
                    pass
                finally:
                    process.stdin.close()

        async def communicate() -> None:
            await asyncio.gather(
                send(),
                drain(process.stdout, buffers[0]),
                drain(process.stderr, buffers[1]),
            )
            await process.wait()

        try:
            async with asyncio.timeout(timeout):
                await communicate()
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            return 124, bytes(buffers[0] + buffers[1])[:cap]
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        output = (
            bytes(buffers[0])
            if process.returncode == 0
            else bytes(buffers[0] + buffers[1])[:cap]
        )
        return process.returncode, output
