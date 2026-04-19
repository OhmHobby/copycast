import win32gui
import win32api
import win32clipboard
import ctypes
import time
import queue
import io
import uuid
from PIL import Image   # pip install pillow

WM_CLIPBOARDUPDATE = 0x031D


class ClipboardListener:
    def __init__(self, data_queue):
        self.queue = data_queue
        self.hwnd = None
        # When we write to the clipboard ourselves (via `write`), Windows still
        # fires WM_CLIPBOARDUPDATE — so we bump this timestamp to tell the
        # listener to ignore the next change for a short window.
        self._suppress_until = 0.0

    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            self.on_clipboard_change()
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def on_clipboard_change(self):
        # Skip the update if it was our own write
        if time.time() < self._suppress_until:
            return

        time.sleep(0.05)  # let the source app finish writing

        try:
            win32clipboard.OpenClipboard()
        except Exception:
            return

        try:
            packet = self._read_clipboard()
            if packet is not None:
                self.queue.put(packet)
        except Exception as e:
            print(f"Error reading clipboard: {e}")
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _read_clipboard(self):
        """Probe formats richest-first. Returns a packet dict or None."""
        # --- IMAGE (CF_DIB = Device Independent Bitmap) ---
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
            dib = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
            # CF_DIB is a bitmap without the BITMAPFILEHEADER. Pillow needs
            # the header prepended to parse it. The easiest way: wrap it in
            # the BMP file format manually.
            img = self._dib_to_image(dib)
            if img is not None:
                # Re-encode as PNG bytes — small, lossless, universally supported
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return {
                    "type": "image",
                    "content": buf.getvalue(),   # raw PNG bytes
                    "width": img.width,
                    "height": img.height,
                    "timestamp": time.time(),
                }

        # --- FILES (CF_HDROP = drag-n-drop style file list) ---
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
            paths = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
            return {
                "type": "files",
                "content": list(paths),
                "timestamp": time.time(),
            }

        # --- TEXT ---
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return {
                "type": "text",
                "content": data,
                "timestamp": time.time(),
            }

        return None

    def write(self, packet):
        """
        Write a packet's content back to the system clipboard.

        Returns True on success, False otherwise. Briefly suppresses our own
        change-listener so we don't pick up the write and re-broadcast it.
        """
        # Open the suppression window BEFORE we touch the clipboard. 500ms is
        # plenty — the WM_CLIPBOARDUPDATE usually arrives within a few ms.
        self._suppress_until = time.time() + 0.5

        try:
            win32clipboard.OpenClipboard()
        except Exception as e:
            print(f"Clipboard write: couldn't open: {e}")
            return False

        ok = False
        try:
            win32clipboard.EmptyClipboard()
            t = packet.get("type")

            if t == "text":
                win32clipboard.SetClipboardData(
                    win32clipboard.CF_UNICODETEXT, packet["content"])
                ok = True

            elif t == "image":
                # Reverse of _dib_to_image: re-encode PNG bytes as a DIB. CF_DIB
                # is a BMP body minus the 14-byte BITMAPFILEHEADER, so we save
                # to BMP and slice off the header.
                img = Image.open(io.BytesIO(packet["content"]))
                if img.mode == "RGBA":
                    img = img.convert("RGB")   # BMP clipboard + alpha is flaky
                buf = io.BytesIO()
                img.save(buf, "BMP")
                win32clipboard.SetClipboardData(
                    win32clipboard.CF_DIB, buf.getvalue()[14:])
                ok = True

            elif t == "files":
                # The paths are from the sender's machine and almost certainly
                # don't resolve on ours. Putting them on as CF_HDROP would
                # produce broken file refs — fall back to newline-joined text.
                paths = packet.get("content", [])
                win32clipboard.SetClipboardData(
                    win32clipboard.CF_UNICODETEXT, "\n".join(paths))
                ok = True

        except Exception as e:
            print(f"Clipboard write failed: {e}")
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

        return ok

    @staticmethod
    def _dib_to_image(dib_bytes):
        """
        CF_DIB is a BMP body (BITMAPINFOHEADER + pixel data) without the
        14-byte BITMAPFILEHEADER at the front. Pillow can read BMPs, so we
        reconstruct the file header and hand the whole thing off.
        """
        try:
            import struct
            # Pull bit depth & offset info from BITMAPINFOHEADER to compute pixel offset
            header_size = struct.unpack_from("<I", dib_bytes, 0)[0]
            bits_per_pixel = struct.unpack_from("<H", dib_bytes, 14)[0]
            num_colors = struct.unpack_from("<I", dib_bytes, 32)[0]
            if num_colors == 0 and bits_per_pixel <= 8:
                num_colors = 1 << bits_per_pixel
            palette_size = num_colors * 4
            pixel_offset = 14 + header_size + palette_size

            file_header = (
                b"BM"
                + (len(dib_bytes) + 14).to_bytes(4, "little")
                + b"\x00\x00\x00\x00"
                + pixel_offset.to_bytes(4, "little")
            )
            return Image.open(io.BytesIO(file_header + dib_bytes))
        except Exception as e:
            print(f"DIB decode failed: {e}")
            return None

    def listen(self):
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self.wnd_proc
        # Window class names are a Windows-global registry. If two instances
        # of the app run on the same machine, the second RegisterClass would
        # fail with "class already exists" — so suffix a random token.
        wc.lpszClassName = f"ClipboardListener_{uuid.uuid4().hex[:8]}"
        hInstance = win32api.GetModuleHandle(None)
        class_atom = win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(
            class_atom, "Listener", 0, 0, 0, 0, 0, 0, 0, hInstance, None
        )
        ctypes.windll.user32.AddClipboardFormatListener(self.hwnd)
        win32gui.PumpMessages()