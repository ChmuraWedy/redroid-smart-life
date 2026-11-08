import ctypes
import os
import selectors
import socket
import struct
import sys

path = sys.argv[1]
index = int(sys.argv[2])
headers = {1: (4, 3, 1), 2: (5, 3, 2), 3: (4, 3, 1), 5: (5, 3, 2)}


def open_controller():
    controller = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    address = struct.pack("=HHH", socket.AF_BLUETOOTH, index, 1)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.bind(controller.fileno(), address, len(address)):
        controller.close()
        raise OSError(ctypes.get_errno(), "HCI user channel")
    return controller


def relay(client):
    with open_controller() as controller, selectors.DefaultSelector() as selector:
        client.settimeout(4)
        selector.register(client, selectors.EVENT_READ)
        selector.register(controller, selectors.EVENT_READ)
        pending = bytearray()
        advertisers = set()
        while True:
            for key, _ in selector.select():
                if key.fileobj is controller:
                    event = controller.recv(65540)
                    if not event:
                        return
                    if event[:2] == b"\x04\x3e" and event[3:4] in (b"\x02", b"\x0d"):
                        print("Forwarded BLE advertisement event", flush=True)
                        position = 5
                        for _ in range(event[4]):
                            if event[3] == 2:
                                address_start, length_offset, header_length = 2, 8, 9
                                trailing_length = 1
                            else:
                                address_start, length_offset, header_length = 3, 23, 24
                                trailing_length = 0
                            if position + header_length > len(event):
                                break
                            mac = event[position + address_start:position + address_start + 6][::-1].hex(":")
                            data_length = event[position + length_offset]
                            if mac not in advertisers:
                                advertisers.add(mac)
                                print(f"Radio advertiser {mac}", flush=True)
                            position += header_length + data_length + trailing_length
                    if event[:2] == b"\x04\x0e" and len(event) >= 7:
                        opcode = int.from_bytes(event[4:6], "little")
                        print(f"HCI complete {opcode:#06x}: {event[6]:#04x}, credits={event[3]}", flush=True)
                    # Keep Android's legacy stack to one outstanding command.
                    if event[:2] == b"\x04\x0e" and len(event) >= 7 and event[3] > 1:
                        event = event[:3] + b"\x01" + event[4:]
                    if event[:2] == b"\x04\x0f" and len(event) >= 7 and event[4] > 1:
                        event = event[:4] + b"\x01" + event[5:]
                    client.sendall(event)
                    continue
                data = client.recv(65540)
                if not data:
                    return
                pending.extend(data)
                while pending:
                    header, offset, width = headers[pending[0]]
                    if len(pending) < header:
                        break
                    length = int.from_bytes(pending[offset:offset + width], "little")
                    if pending[0] == 5:
                        length &= 0x3fff
                    end = header + length
                    if len(pending) < end:
                        break
                    if pending[0] == 1:
                        opcode = int.from_bytes(pending[1:3], "little")
                        print(f"HCI command {opcode:#06x}", flush=True)
                    # These Android vendor opcodes overlap with unrelated Intel commands.
                    if pending[0] == 1 and opcode in (0xfd53, 0xfd5b):
                        client.sendall(b"\x04\x0e\x04\x01" + pending[1:3] + b"\x01")
                    else:
                        controller.sendall(pending[:end])
                    del pending[:end]


with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
    listener.bind(path)
    try:
        os.chown(path, 1002, 1002)
        os.chmod(path, 0o660)
        listener.listen(1)
        print("HCI bridge ready", flush=True)
        client, _ = listener.accept()
        with client:
            relay(client)
    finally:
        os.unlink(path)
