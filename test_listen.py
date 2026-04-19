import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", 55555))
print("Listening on UDP 55555... (Ctrl+C to stop)")

while True:
    data, addr = sock.recvfrom(65536)
    print(f"Got {len(data)} bytes from {addr}: {data[:80]!r}")