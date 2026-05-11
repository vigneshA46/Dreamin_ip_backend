import asyncio
import websockets
import json

async def test():
    uri = "ws://127.0.0.1:8000/ws"

    async with websockets.connect(uri) as ws:
        print("Connected to server")

        while True:
            data = await ws.recv()
            print("Received:", data)

asyncio.run(test())