import socket
import struct
import threading
import queue
import json
import uuid
import time
import io

PORT = 55555
MAX_UDP_CHUNK = 1400       # safe MTU-friendly chunk size
MAGIC = b"CLPB"

# --- Redundancy ------------------------------------------------------------
# WiFi broadcast has no retry at the radio level, so ~1-2% of packets are
# just gone. We send each chunk REDUNDANCY times with a rotating offset so
# the same chunk isn't in the same time-slice across rounds (a burst of RF
# interference can wipe out several consecutive packets). Duplicates are
# free on the receiver — it just overwrites the same chunk slot.
#
# At 1.3% loss and REDUNDANCY=3, probability a given chunk is missing from
# all 3 rounds is 0.013^3 ≈ 2e-6. For a 60-chunk image: essentially perfect.
REDUNDANCY = 3

# --- Image transport -------------------------------------------------------
# Re-encode images as JPEG before sending. Typical screenshot: 400KB PNG ->
# ~60KB JPEG, so ~7x fewer chunks on the wire. Lossy but imperceptible at
# q=85 for screenshots and photos. Receiver treats it as any image format.
JPEG_QUALITY = 85


class Broadcaster:
    """
    Drains an outgoing queue and sends packets over UDP broadcast.
    Large payloads are chunked and sent redundantly for WiFi loss tolerance.
    """

    def __init__(self, outgoing_queue, port=PORT, instance_id=None):
        self.queue = outgoing_queue
        self.port = port
        self.instance_id = instance_id
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._stop = threading.Event()

    def send_loop(self):
        while not self._stop.is_set():
            try:
                packet = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._send_packet(packet)
            except Exception as e:
                print(f"[broadcaster] send failed: {e}")

    def _maybe_jpeg(self, packet):
        """Transcode image content to JPEG. Big bandwidth win."""
        if packet["type"] != "image":
            return packet
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(packet["content"]))
            # JPEG can't handle alpha — flatten onto white if we have it.
            if img.mode in ("RGBA", "LA", "P"):
                if img.mode == "P":
                    img = img.convert("RGBA")
                if img.mode == "RGBA":
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                else:
                    img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)

            new_packet = dict(packet)
            old_n, new_n = len(packet["content"]), len(buf.getvalue())
            new_packet["content"] = buf.getvalue()
            print(f"[broadcaster] JPEG transcode {old_n} -> {new_n} bytes "
                  f"({100 * new_n // max(old_n, 1)}% of original)")
            return new_packet
        except Exception as e:
            print(f"[broadcaster] JPEG transcode failed, sending raw: {e}")
            return packet

    def _send_packet(self, packet):
        packet = self._maybe_jpeg(packet)

        # --- Serialize: separate metadata (JSON) from raw payload ---------
        if packet["type"] == "image":
            meta = {k: v for k, v in packet.items() if k != "content"}
            payload = packet["content"]                    # raw image bytes
        elif packet["type"] == "files":
            meta = dict(packet)
            payload = b""
        else:  # text
            meta = {k: v for k, v in packet.items() if k != "content"}
            payload = packet["content"].encode("utf-8")

        meta["sender_id"] = self.instance_id
        meta_bytes = json.dumps(meta).encode("utf-8")

        # body = meta\0\0payload  (receiver splits on first \x00\x00)
        body = meta_bytes + b"\x00\x00" + payload
        packet_id = uuid.uuid4().bytes[:8]

        # --- Chunk (no padding; receiver reassembles by length) -----------
        chunks = []
        for i in range(0, len(body), MAX_UDP_CHUNK):
            chunks.append(body[i: i + MAX_UDP_CHUNK])
        if not chunks:
            chunks = [b""]
        total = len(chunks)

        # Single-chunk packets: just send once (no need for redundancy,
        # single-packet UDP either arrives or doesn't — can't half-arrive).
        rounds = REDUNDANCY if total > 1 else 1

        print(f"[broadcaster] {packet_id.hex()} type={packet['type']} "
              f"chunks={total} rounds={rounds} body_bytes={len(body)}")

        # --- Transmit with rotating offsets per round ----------------------
        # Offset = total/rounds * round_num so the same chunk sits in a
        # different time-slice each round. Defeats time-correlated bursts
        # of packet loss.
        for r in range(rounds):
            offset = (r * total // rounds) if rounds > 1 else 0
            for i in range(total):
                idx = (i + offset) % total
                # Header: MAGIC(4) + packet_id(8) + idx(2) + total(2) = 16 bytes
                header = MAGIC + packet_id + struct.pack("<HH", idx, total)
                self.sock.sendto(header + chunks[idx],
                                 ("<broadcast>", self.port))
                # Pace the burst. UDP broadcast over WiFi has no retry, and
                # the receiver's kernel buffer is small — blasting chunks
                # back-to-back drops packets. ~2000 chunks/sec is plenty.
                if total > 1:
                    time.sleep(0.0005)

    def stop(self):
        self._stop.set()