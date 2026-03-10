import json
import logging
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from handlers.screenshot_handler import ScreenshotHandler

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client connected: {client_id}")

    async def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
        logger.info(f"Client disconnected: {client_id}")

    async def send_command(self, client_id: str, command: dict):
        ws = self.active_connections.get(client_id)
        if ws:
            await ws.send_json(command)

    async def broadcast_command(self, command: dict):
        for ws in self.active_connections.values():
            await ws.send_json(command)

    def get_clients(self) -> list:
        return list(self.active_connections.keys())


manager = ConnectionManager()
screenshot_handler = ScreenshotHandler()


@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(client_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            result = await handle_message(client_id, data)
            if result is not None:
                await websocket.send_json(result)
    except WebSocketDisconnect:
        await manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"WebSocket error for {client_id}: {e}")
        await manager.disconnect(client_id)


async def handle_message(client_id: str, data: dict) -> dict | None:
    msg_type = data.get("type")

    if msg_type == "screenshot":
        result = await screenshot_handler.process(data, client_id=client_id)
        return {
            "type": "result",
            "success": True,
            "data": result,
            "message": "处理完成"
        }

    elif msg_type == "status":
        logger.info(f"Client {client_id} status: {data.get('status')} - {data.get('message')}")
        return None

    return None
