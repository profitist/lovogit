import asyncio
import ipaddress
import socket
import ssl

import httpcore
import httpx


class BlockedDestination(Exception):
    pass


class CheckedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend=None, resolver=None):
        self._backend = backend or httpcore.AnyIOBackend()
        self._resolver = resolver

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if host.lower().rstrip(".") == "localhost" or host.lower().rstrip(".").endswith(
            ".localhost"
        ):
            raise BlockedDestination("Localhost destination is forbidden")
        try:
            address = ipaddress.ip_address(host)
            addresses = [str(address)]
        except ValueError:
            if self._resolver is None:
                records = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
                addresses = list(dict.fromkeys(item[4][0] for item in records))
            else:
                addresses = await self._resolver(host, port)
        if not addresses:
            raise OSError("DNS returned no addresses")
        for value in addresses:
            address = ipaddress.ip_address(value)
            mapped = getattr(address, "ipv4_mapped", None)
            if (
                not address.is_global
                or address.is_multicast
                or (
                    mapped is not None and (not mapped.is_global or mapped.is_multicast)
                )
            ):
                raise BlockedDestination("Non-public destination is forbidden")
        return await self._backend.connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise BlockedDestination("Unix sockets are forbidden")

    async def sleep(self, seconds):
        await self._backend.sleep(seconds)


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self):
        await self._stream.aclose()


class SourceDocumentTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend=None):
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=backend or CheckedNetworkBackend(),
            retries=0,
            max_keepalive_connections=0,
        )

    async def handle_async_request(self, request):
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self._pool.aclose()
