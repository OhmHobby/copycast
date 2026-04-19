# Copycast

> AirDrop for your clipboard. Copy on one machine, everyone on your LAN gets it.

Small, opinionated clipboard broadcaster for teams sharing a network — a hackathon room, a shared hotspot, your apartment WiFi. Copy something on one machine; every teammate running Copycast sees it appear in their app, one click to drop it into their own clipboard.

![TODO: screenshot]

## What it does

- Captures **text**, **images** (screenshots, copied pictures), and **file path lists** from the system clipboard
- Selective broadcast — you pick what to send, nothing goes out automatically
- One-click copy-to-clipboard on any received item
- Visually distinguishes local vs. remote entries in the list
- Light/dark theme
- Works with two instances on the same machine (useful for testing)

## What it deliberately isn't

This is a **trust-everyone** tool. No authentication. No encryption. Anyone on the same WiFi with a UDP listener on port 55555 sees everything you broadcast.

Don't use it:

- On public WiFi
- For passwords, API keys, tokens, anything sensitive
- On networks you don't control or trust

Do use it:

- In a hackathon room with your team
- On a personal hotspot with your friends
- At home, for quick paste-between-laptops

## Requirements

- Windows (uses `win32clipboard`)
- Python 3.8+
- `pywin32` and `Pillow`

```
pip install pywin32 Pillow
```

## Running it

```
python main.py
```

Launch on every teammate's machine. No IPs to type, no room to join — instances find each other via UDP broadcast.

## How it works

**Clipboard capture** (`Clipboard.py`): a hidden Win32 window registers as a clipboard format listener. When anything changes, we probe formats in order (image → files → text) and package whatever we find into a packet.

**Broadcast** (`broadcaster.py`): packets go out as UDP broadcasts on port 55555. Images are transcoded to JPEG first (typically ~10x smaller than the PNG form clipboard delivers). Multi-chunk packets are chunked at 1400 bytes and sent 3× with rotating time offsets to survive ~1–2% WiFi broadcast loss.

**Receive** (`receiver.py`): listens on port 55555, reassembles chunked packets, filters out our own broadcasts by `sender_id`, and pushes completed packets to the UI.

**UI** (`main.py` + `app_core.py`): Tk-based list of entries, newest first. Remote entries get a tint + arrow marker; local entries can be selected and re-broadcast. A copy button on each row writes the content back into the system clipboard.

## Known rough edges

- **Router "client isolation"** (aka AP isolation) blocks client-to-client traffic — common on phone hotspots and some routers. If Copycast "can't see peers," try a different network.
- **Windows Firewall**: first-launch prompt needs "Allow access" for both Private and Public networks.
- **Files**: sent as a list of paths, which almost certainly don't resolve on the receiving machine. The receiver falls back to pasting the path strings as text.
- **No peer discovery yet** — you can't see who's online. You just see items as they arrive.
- **Windows only** at the moment. The architecture is portable; the clipboard integration isn't.

## Files

| File             | What it does                               |
| ---------------- | ------------------------------------------ |
| `main.py`        | Tk UI                                      |
| `app_core.py`    | Glues clipboard listener + network together |
| `Clipboard.py`   | Windows clipboard read/write               |
| `broadcaster.py` | Outgoing UDP with JPEG + redundancy        |
| `receiver.py`    | Incoming UDP + chunk reassembly            |

## License

MIT
