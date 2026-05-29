# captures/ — USB captures & dissectors

`.pcapng` USB captures (`usbmon` + Wireshark) and the Lua dissector(s) for the Goodix
protocol. Capture contents are gitignored (`*.pcapng`) — they can contain image/biometric
data and are large. Keep a committed Lua dissector here only if it carries no captured
data; otherwise keep it in `docs/protocol/` notes.

Workflow (RE best practice): `sudo modprobe usbmon`, capture in Wireshark filtered to the
reader's bus/device, repeat each action several times, save as `.pcapng`, diff. Hot-reload
the dissector (Ctrl+Shift+L) while decoding.
