import socket, time

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

i = 0
while True:
    msg = f"hello {i}".encode()
    sock.sendto(msg, ("<broadcast>", 55555))
    print(f"sent: {msg}")
    i += 1
    time.sleep(1)