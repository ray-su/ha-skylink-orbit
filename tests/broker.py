"""Small controlled MQTT 3.1.1 peer for protocol tests, not a production broker."""

import asyncio
import json
import struct


def packet(kind: int, body: bytes) -> bytes:
    length = len(body)
    encoded = bytearray()
    while True:
        digit = length % 128
        length //= 128
        encoded.append(digit | (128 if length else 0))
        if not length:
            return bytes([kind]) + encoded + body


class Broker:
    def __init__(self):
        self.snapshot = [
            {
                "hub_id": "hub-test",
                "hub_name": "Garage",
                "type": "GDO",
                "online": True,
                "reported": {"mdev": {"door": 4}},
            }
        ]
        self.subscriptions = []
        self.published = asyncio.Queue()
        self.connections = []
        self.tasks = set()
        self.respond = True
        self.connack = 0
        self.before_snapshot = None
        self.connected = asyncio.Event()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.append(writer)
        try:
            while True:
                header = (await reader.readexactly(1))[0]
                length, multiplier = 0, 1
                while True:
                    digit = (await reader.readexactly(1))[0]
                    length += (digit & 127) * multiplier
                    if digit < 128:
                        break
                    multiplier *= 128
                body = await reader.readexactly(length)
                kind = header >> 4
                if kind == 1:
                    writer.write(packet(0x20, bytes([0, self.connack])))
                    self.connected.set()
                elif kind == 8:
                    offset, codes = 2, []
                    while offset < len(body):
                        size = struct.unpack("!H", body[offset : offset + 2])[0]
                        self.subscriptions.append(
                            body[offset + 2 : offset + 2 + size].decode()
                        )
                        offset += 2 + size
                        codes.append(body[offset])
                        offset += 1
                    writer.write(packet(0x90, body[:2] + bytes(codes)))
                elif kind == 3:
                    size = struct.unpack("!H", body[:2])[0]
                    topic = body[2 : 2 + size].decode()
                    payload = json.loads(body[2 + size :])
                    await self.published.put((topic, payload, header))
                    if topic.endswith("/get") and self.respond:
                        if self.before_snapshot is not None:
                            await self.send(
                                topic.removesuffix("/get") + "/update/result",
                                {"data": self.before_snapshot},
                            )
                        await self.send(topic + "/result", {"data": self.snapshot})
                elif kind == 12:
                    writer.write(packet(0xD0, b""))
                elif kind == 14:
                    break
                await writer.drain()
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.connections.remove(writer)
            self.tasks.discard(task)

    async def send(self, topic, payload):
        encoded = topic.encode()
        body = struct.pack("!H", len(encoded)) + encoded + json.dumps(payload).encode()
        for writer in list(self.connections):
            writer.write(packet(0x30, body))
            await writer.drain()

    async def disconnect(self):
        for writer in list(self.connections):
            writer.close()
        if self.tasks:
            await asyncio.gather(*list(self.tasks))
