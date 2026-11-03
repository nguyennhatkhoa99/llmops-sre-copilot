"""Read plant signals over OPC UA (asyncua). Read only.

This class has no write path on purpose: the AI service only ever proposes
setpoints; an engineer applies them on the DCS. Use an OPC UA account that has
read permission only, so the guarantee holds on the server side too.
Tag names in config map to node ids, for example ``"ns=2;s=Line1.Temp"``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from asyncua import Client, ua

from beyondpilot.core.contracts import TagValue
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        endpoint: str,
        nodes: Mapping[str, str],
        resilience: ResilienceConfig,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.endpoint, self.nodes, self.resilience = endpoint, dict(nodes), resilience
        self.username, self.password = username, password

    async def _read(self, tags: Sequence[str]) -> dict[str, TagValue]:
        client = Client(url=self.endpoint)
        if self.username:
            client.set_user(self.username)
            client.set_password(self.password or "")
        async with client:
            out = {}
            for tag in tags:
                node = client.get_node(self.nodes[tag])
                dv = await node.read_data_value()
                ts = dv.SourceTimestamp or dv.ServerTimestamp
                out[tag] = TagValue(
                    float(dv.Value.Value),
                    ts.timestamp() if ts else 0.0,
                    dv.StatusCode is None or dv.StatusCode.value == ua.StatusCodes.Good,
                )
            return out

    async def read_tags(self, tags: Sequence[str]) -> dict[str, TagValue]:
        unknown = [t for t in tags if t not in self.nodes]
        if unknown:
            raise AppError(ErrorCode.CONFIG_INVALID, f"no OPC UA node for tags {unknown}")
        return await call_with_retry(self._read, tags, config=self.resilience, op="ot.opcua")
