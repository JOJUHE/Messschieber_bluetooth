import asyncio
from bleak import BleakScanner
from bleak import BleakClient



async def main_scan():
    print("Scanne 10 Sekunden nach BLE-Geräten…")
    try:
        devices = await BleakScanner.discover(timeout=10.0)
    except Exception as e:
        print("Fehler bei discover():", e)
        # return

    print(f"Anzahl gefundener Geräte: {len(devices)}")

    for i, device in enumerate(devices):
        print(f"\n--- Gerät {i+1} ---")
        try:
            print("Name:", device.name)
            print("Address:", device.address)
            print("RSSI:", device.rssi)
            print("Metadata:", device.metadata)
        except Exception as e:
            print("Fehler beim Auslesen eines Geräts:", e)




# ADDRESS = "56836446-7BB3-0033-9883-7E514D90732B"  # B-00014265
# ADDRESS = "7710AC70-264A-25CF-CC4C-CEB90B1E0984"
ADDRESS = "7710AC70-264A-25CF-CC4C-CEB90B1E0984"
CHAR_UUID = "FF00"


async def mainalt():
    print("Verbinde mit Messschieber…")
    async with BleakClient(ADDRESS) as client:
        print("Verbunden!")
        services = await client.get_services()
        for service in services:
            print("Service:", service.uuid)
            for char in service.characteristics:
                print("  Characteristic:", char.uuid, char.properties)

asyncio.run(main_scan())
exit(0)
# asyncio.run(mainalt())


import asyncio
from bleak import BleakClient
import subprocess

ADDRESS = "7710AC70-264A-25CF-CC4C-CEB90B1E0984"
# ADDRESS = "56836446-7BB3-0033-9883-7E514D90732B"#   |  B-00014265
CHAR_UUID = "FFFF"

def copy_to_clipboard(text):
    p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
    p.communicate(input=text.encode('utf-8'))

def decode_measurement(data: bytes):
    # Beispiel: 0200FF0000089900
    # Wir extrahieren die beiden Bytes für den Messwert
    raw = data[2] | (data[3] << 8)

    # Viele Messschieber senden in 1/100 mm
    value = raw / 100.0
    return value

def notification_handler(sender, data):
    value = decode_measurement(data)
    print("Messwert:", value)
    copy_to_clipboard(str(value))
    print("→ In Zwischenablage kopiert")

async def main():
    print("Verbinde mit Messschieber…")
    async with BleakClient(ADDRESS) as client:
        print("Verbunden!")
        await client.start_notify(CHAR_UUID, notification_handler)
        print("Warte auf Messwerte…")
        while True:
            await asyncio.sleep(1)

asyncio.run(main())



