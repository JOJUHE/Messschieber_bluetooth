import asyncio
from bleak import BleakScanner

def callback(device, advertisement_data):
    if "S30 5822 LE" in str(device.name) :
        try:
            print("------------------------------------")
            print("Name:", device.name)
            print("Address:", device.address)
            print("RSSI:", advertisement_data.rssi)
            print("Metadata:", device.metadata)
            print("Manufacturer:", advertisement_data.manufacturer_data)
        except Exception as e:
            print("Fehler im Callback:", e)

async def main():
    scanner = BleakScanner(callback)
    await scanner.start()
    print("Scanning for 20 Seconds…")
    await asyncio.sleep(20.0)
    await scanner.stop()
    print("Scan beendet.")

asyncio.run(main())

'''
🟧 Was das jetzt bedeutet
Wir sind jetzt an dem Punkt, an dem wir nicht mehr scannen müssen.
Wir haben:
• Name: S30 5822 LE
• MAC: 659127C1-8E34-5CC9-EDC5-A40E65054C60
• Service: FE07
• Manufacturer ID: 1447
• Advertising stabil
• Gerät frei (nicht verbunden)
'''
