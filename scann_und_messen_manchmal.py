import asyncio
from bleak import BleakClient

ADDRESS = "56836446-7BB3-0033-9883-7E514D90732B"

async def main():
    print("Verbinde…")
    async with BleakClient(ADDRESS) as client:
        print("Verbunden!")
        services = await client.get_services()
        for s in services:
            print("Service:", s.uuid)
            for c in s.characteristics:
                print("  Char:", c.uuid, c.properties)

asyncio.run(main())
