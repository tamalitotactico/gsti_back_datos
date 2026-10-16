"""Simula un teléfono participante contra una API en ejecución.

Enrola un participante (con credenciales de staff), registra el dispositivo, envía N días
de bloques de 30 min (pasos, en casa, uso de apps) + sueño nocturno, por REST y por
WebSocket, responde una EMA y un PHQ-9, y muestra la latencia medida (H4).

Uso:
    python -m scripts.simulate_device --base http://localhost:8000 \
        --email admin@unsa.edu.pe --password '...' --days 3
Datos 100 % sintéticos.
"""
import argparse
import asyncio
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import websockets
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")


def synthetic_day(day_start: datetime, rng: random.Random) -> list[dict]:
    obs = []
    for k in range(48):
        ws_ = day_start + timedelta(minutes=30 * k)
        we = ws_ + timedelta(minutes=30)
        hour = ws_.astimezone(LIMA).hour
        awake = 7 <= hour < 23
        obs += [
            {"metric": "steps", "source": "health_connect", "window_start": ws_.isoformat(),
             "window_end": we.isoformat(), "value": rng.randint(50, 900) if awake else 0},
            {"metric": "at_home", "source": "phone_gps", "window_start": ws_.isoformat(),
             "window_end": we.isoformat(), "value": 0 if 9 <= hour < 17 and rng.random() < 0.8 else 1},
            {"metric": "app_use", "source": "phone_usage", "window_start": ws_.isoformat(),
             "window_end": we.isoformat(), "value": 1 if awake and rng.random() < 0.7 else 0},
        ]
    night_end = day_start + timedelta(hours=7)
    obs.append({"metric": "sleep_minutes", "source": "fitbit", "window_start": (night_end - timedelta(hours=9)).isoformat(),
                "window_end": night_end.isoformat(), "value": rng.randint(300, 480)})
    return obs


async def run(base: str, email: str, password: str, days: int) -> None:
    rng = random.Random(7)
    async with httpx.AsyncClient(base_url=base, timeout=30) as c:
        tok = (await c.post("/api/v1/auth/login", json={"email": email, "password": password})).raise_for_status().json()
        staff = {"Authorization": f"Bearer {tok['access_token']}"}
        enr = (await c.post("/api/v1/participants", headers=staff,
                            json={"consent_version": "v1-sim", "cohort": "simulacion", "age_band": "21-23"})
               ).raise_for_status().json()
        code = enr["participant"]["code"]
        reg = (await c.post("/api/v1/devices/register", json={"enrollment_code": enr["enrollment_code"],
                                                              "platform": "android-14", "model": "sim"})
               ).raise_for_status().json()
        dev = {"Authorization": f"Bearer {reg['device_token']}"}
        print(f"Participante {code} con dispositivo {reg['device_id']}")

        today = datetime.now(UTC).replace(hour=5, minute=0, second=0, microsecond=0)  # 00:00 Lima
        for d in range(days, 0, -1):
            day = today - timedelta(days=d)
            r = (await c.post("/api/v1/ingest/sensors", headers=dev,
                              json={"batch_id": str(uuid.uuid4()), "observations": synthetic_day(day, rng)})
                 ).raise_for_status().json()
            print(f"REST  {day.date()}: aceptadas={r['accepted']} rechazadas={r['rejected']}")

        sched = (await c.post(f"/api/v1/participants/{code}/ema-schedule", headers=staff,
                              json={"start_date": str((datetime.now(UTC) - timedelta(hours=5)).date()), "days": 2,
                                    "prompts_per_day": 3, "window_start_hour": 0, "window_end_hour": 24,
                                    "seed": 1})).raise_for_status().json()
        print(f"EMA programadas: {len(sched)}")

        phq = [{"item": i, "value": rng.randint(0, 2)} for i in range(1, 9)] + [{"item": 9, "value": 0}]
        ack = (await c.post("/api/v1/questionnaires/responses", headers=dev, json={
            "client_response_id": str(uuid.uuid4()), "instrument": "PHQ9", "context": "baseline",
            "completed_at": datetime.now(UTC).isoformat(), "answers": phq})).raise_for_status().json()
        print(f"PHQ-9 registrado; mostrar recursos de ayuda: {ack['support_resources']}")

    ws_url = base.replace("http", "ws", 1) + "/ws/device"
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "auth", "token": reg["device_token"]}))
        print("WS", json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "sensor_batch", "data": {
            "batch_id": str(uuid.uuid4()), "observations": synthetic_day(today, rng)[:20]}}))
        print("WS ack", json.loads(await ws.recv())["data"])

    async with httpx.AsyncClient(base_url=base, timeout=30) as c:
        lat = (await c.get("/api/v1/metrics/latency", headers=staff)).raise_for_status().json()
        print("Latencia (H4):", lat)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    asyncio.run(run(a.base, a.email, a.password, a.days))
