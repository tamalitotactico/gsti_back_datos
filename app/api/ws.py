"""WebSockets.

/ws/device     el teléfono envía lotes de bioseñales y respuestas por un canal persistente.
/ws/dashboard  el tablero recibe alertas y eventos de ingesta en tiempo real.

Protocolo: el primer mensaje debe ser {"type": "auth", "token": "..."} (el token no va en
la URL para que no quede en logs). Luego, mensajes JSON {"type": ..., "data": {...}}.
"""
import asyncio
import contextlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.deps import device_from_token, staff_from_token
from app.db import get_sessionmaker
from app.schemas import QuestionnaireIn, SensorBatchIn
from app.services.ingest import BatchConflict, ingest_batch
from app.services.questionnaires import ResponseConflict, submit_response
from app.services.realtime import bus
from app.services.scoring import ScoringError

router = APIRouter()
AUTH_TIMEOUT_S = 10
POLICY_VIOLATION = 1008


async def _read_auth(ws: WebSocket) -> str | None:
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT_S)
    except (TimeoutError, json.JSONDecodeError, KeyError, WebSocketDisconnect):
        return None
    if not isinstance(msg, dict) or msg.get("type") != "auth" or not isinstance(msg.get("token"), str):
        return None
    return msg["token"]


def _err(detail, ref=None) -> dict:
    return {"type": "error", "ref": ref, "detail": detail}


@router.websocket("/ws/device")
async def ws_device(ws: WebSocket) -> None:
    await ws.accept()
    token = await _read_auth(ws)
    sm = get_sessionmaker()
    if token is None:
        await ws.close(code=POLICY_VIOLATION)
        return
    async with sm() as session:
        ctx = await device_from_token(session, token)
    if ctx is None or ctx.participant.status == "withdrawn":
        await ws.close(code=POLICY_VIOLATION)
        return
    device_id, participant_id = ctx.device.id, ctx.participant.id
    await ws.send_json({"type": "auth_ok", "participant_code": ctx.participant.code})

    try:
        while True:
            try:
                msg = await ws.receive_json()
            except json.JSONDecodeError:
                await ws.send_json(_err("JSON inválido"))
                continue
            received_at = datetime.now(UTC)
            mtype = msg.get("type") if isinstance(msg, dict) else None
            if mtype == "ping":
                await ws.send_json({"type": "pong"})
                continue
            if mtype not in ("sensor_batch", "questionnaire"):
                await ws.send_json(_err(f"tipo de mensaje no soportado: {mtype}"))
                continue
            data = msg.get("data")
            ref = (data.get("batch_id") or data.get("client_response_id")) if isinstance(data, dict) else None
            async with sm() as session:
                # Se recarga en cada mensaje: un retiro o revocación corta el canal al instante
                ctx = await device_from_token(session, token)
                if ctx is None or ctx.device.id != device_id or ctx.participant.id != participant_id \
                        or ctx.participant.status == "withdrawn":
                    await ws.close(code=POLICY_VIOLATION)
                    return
                try:
                    if mtype == "sensor_batch":
                        batch = SensorBatchIn.model_validate(data)
                        result = await ingest_batch(session, ctx.device, ctx.participant, batch, "websocket",
                                                    received_at)
                    else:
                        body = QuestionnaireIn.model_validate(data)
                        result = await submit_response(session, ctx.device, ctx.participant, body)
                    await ws.send_json({"type": "ack", "ref": ref, "data": result.model_dump(mode="json")})
                except ValidationError as e:
                    await session.rollback()
                    await ws.send_json(_err(json.loads(e.json(include_url=False, include_input=False)), ref))
                except (BatchConflict, ResponseConflict, ScoringError, ValueError) as e:
                    await session.rollback()
                    await ws.send_json(_err(str(e), ref))
    except WebSocketDisconnect:
        return


@router.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await ws.accept()
    token = await _read_auth(ws)
    if token is None:
        await ws.close(code=POLICY_VIOLATION)
        return
    async with get_sessionmaker()() as session:
        staff = await staff_from_token(session, token)
    if staff is None or staff.role not in ("admin", "clinician"):
        await ws.close(code=POLICY_VIOLATION)
        return

    queue = bus.subscribe()
    await ws.send_json({"type": "auth_ok", "role": staff.role})

    async def pump() -> None:
        while True:
            await ws.send_text(await queue.get())

    sender = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, json.JSONDecodeError, RuntimeError):
        pass
    finally:
        bus.unsubscribe(queue)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
