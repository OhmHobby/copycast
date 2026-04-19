import socket
import struct
import json
import threading
import time

PORT = 55555
MAGIC = b"CLPB"
HEADER_SIZE = 16   # MAGIC(4) + packet_id(8) + idx(2) + total(2)


class Receiver:
    """Listens for broadcast packets, reassembles chunks, pushes to queue.

    Duplicate chunks (from redundant sends) are harmless — they overwrite
    the same slot. We assemble as soon as all indices 0..total-1 are seen.
    """

    def __init__(self, incoming_queue, port=PORT, instance_id=None):
        self.queue = incoming_queue
        self.port = port
        self.instance_id = instance_id
        self._stop = threading.Event()
        # packet_id -> { chunks: {idx: bytes}, total, first_seen, sender, done }
        self._buffers = {}

    def listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Default Windows UDP recv buffer is ~64KB. Large image bursts
        # overflow it and the kernel silently drops chunks. Ask for 4MB.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        sock.bind(("", self.port))
        sock.settimeout(0.5)

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65536)
            except socket.timeout:
                self._gc_stale_buffers()
                continue

            if len(data) < HEADER_SIZE or data[:4] != MAGIC:
                continue  # not our packet

            packet_id = data[4:12]
            idx, total = struct.unpack("<HH", data[12:16])
            chunk = data[HEADER_SIZE:]

            buf = self._buffers.setdefault(packet_id, {
                "chunks": {},
                "total": total,
                "first_seen": time.time(),
                "sender": addr[0],
                "done": False,
            })
            # Skip late-arriving chunks for already-assembled packets.
            # We keep the buffer around briefly so we don't re-deliver.
            if buf["done"]:
                continue

            buf["chunks"][idx] = chunk

            if len(buf["chunks"]) == buf["total"]:
                self._assemble_and_enqueue(packet_id, buf)
                buf["done"] = True

    def _assemble_and_enqueue(self, packet_id, buf):
        body = b"".join(buf["chunks"][i] for i in range(buf["total"]))
        try:
            meta_bytes, payload = body.split(b"\x00\x00", 1)
            meta = json.loads(meta_bytes.decode("utf-8"))
        except Exception as e:
            print(f"[receiver] parse failed for {packet_id.hex()}: {e}")
            return

        # Drop our own broadcasts (identified by sender_id, not IP, so two
        # instances on the same machine still work).
        if meta.get("sender_id") == self.instance_id:
            return

        if meta["type"] == "image":
            meta["content"] = payload
        elif meta["type"] == "files":
            pass  # content already in meta
        else:  # text
            meta["content"] = payload.decode("utf-8", errors="replace")

        meta["sender"] = buf["sender"]
        self.queue.put(meta)

        print(f"[receiver] OK {packet_id.hex()} "
              f"type={meta.get('type')} "
              f"{buf['total']} chunks from {buf['sender']}")

    def _gc_stale_buffers(self):
        """Drop partial packets older than 5 seconds, and fully-assembled
        ones older than 2s (enough time for late duplicates to stop arriving)."""
        now = time.time()
        stale = []
        for pid, b in self._buffers.items():
            age = now - b["first_seen"]
            if b["done"] and age > 2:
                stale.append((pid, False))
            elif not b["done"] and age > 5:
                stale.append((pid, True))
        for pid, was_incomplete in stale:
            b = self._buffers[pid]
            if was_incomplete:
                have = len(b["chunks"])
                missing = sorted(set(range(b["total"])) - set(b["chunks"].keys()))
                shown = missing[:10]
                ellipsis = "..." if len(missing) > 10 else ""
                print(f"[receiver] DROPPED {pid.hex()} — had {have}/{b['total']}, "
                      f"missing {shown}{ellipsis} from {b['sender']}")
            del self._buffers[pid]

    def stop(self):
        self._stop.set()