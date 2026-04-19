import queue
import threading
import uuid
import Clipboard as Cp
from broadcaster import Broadcaster
from receiver import Receiver


class AppCore:
    def __init__(self, on_packet):
        self.on_packet = on_packet

        # Stable ID for this run — lets the receiver drop our own broadcasts
        self.instance_id = uuid.uuid4().hex[:16]

        # Clipboard (local)
        self.clipboard_queue = queue.Queue()
        self.listener = Cp.ClipboardListener(self.clipboard_queue)

        # Network
        self.outgoing_queue = queue.Queue()
        self.incoming_queue = queue.Queue()
        self.broadcaster = Broadcaster(self.outgoing_queue,
                                       instance_id=self.instance_id)
        self.receiver = Receiver(self.incoming_queue,
                                 instance_id=self.instance_id)

    def start(self):
        threading.Thread(target=self.listener.listen, daemon=True).start()
        threading.Thread(target=self.broadcaster.send_loop, daemon=True).start()
        threading.Thread(target=self.receiver.listen, daemon=True).start()

    def drain(self):
        # Local clipboard events — tag them as local
        while True:
            try:
                p = self.clipboard_queue.get_nowait()
                p["source"] = "local"
                self.on_packet(p)
            except queue.Empty:
                break

        # Incoming broadcasts — tag them as remote
        while True:
            try:
                p = self.incoming_queue.get_nowait()
                p["source"] = "remote"
                self.on_packet(p)
            except queue.Empty:
                break

    def broadcast(self, packet):
        """UI calls this when the user clicks 'send' on a selected item."""
        self.outgoing_queue.put(packet)

    def copy_to_clipboard(self, packet):
        """UI calls this when the user clicks 'copy' on a row. Returns
        True/False for success so the UI can show appropriate feedback."""
        return self.listener.write(packet)