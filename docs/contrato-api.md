# Contrato de la API para la app móvil

Base: `https://<servidor>/api/v1` · Documentación interactiva: `/docs` (Swagger) · Todas las fechas en ISO 8601 **con zona horaria** (`2026-10-16T09:30:00-05:00` o `...Z`).

## 1. Enrolamiento del teléfono

El equipo registra al participante en el tablero y le entrega un código de 8 caracteres (vigente 7 días).

```http
POST /devices/register
{ "enrollment_code": "K7QM-2XRP", "kind": "smartphone", "platform": "android-14", "model": "Redmi 13C", "app_version": "0.1.0" }
→ 201 { "device_id": "…", "participant_code": "P0007", "device_token": "dev_…" }
```

`device_token` se muestra **una sola vez**: guardarlo en el almacenamiento cifrado del sistema (Android Keystore / EncryptedSharedPreferences). Todas las llamadas siguientes llevan `Authorization: Bearer dev_…`.

## 2. Envío de bioseñales e indicadores (REST)

```http
POST /ingest/sensors
{
  "batch_id": "0b6c…",               // UUID generado en el teléfono; si falla la red, reenviar el MISMO
  "sent_at": "2026-10-16T08:00:05-05:00",
  "observations": [
    { "metric": "steps",   "source": "health_connect", "window_start": "2026-10-16T07:00:00-05:00", "window_end": "2026-10-16T07:30:00-05:00", "value": 412 },
    { "metric": "at_home", "source": "phone_gps",      "window_start": "2026-10-16T07:00:00-05:00", "window_end": "2026-10-16T07:30:00-05:00", "value": 1 },
    { "metric": "app_use", "source": "phone_usage",    "window_start": "2026-10-16T07:00:00-05:00", "window_end": "2026-10-16T07:30:00-05:00", "value": 1 },
    { "metric": "sleep_minutes", "source": "fitbit",   "window_start": "2026-10-15T23:10:00-05:00", "window_end": "2026-10-16T06:40:00-05:00", "value": 398 }
  ]
}
→ 202 { "batch_id": "0b6c…", "received": 4, "accepted": 4, "duplicate": 0, "rejected": 0, "rejections": [], "replayed": false }
```

| metric | Granularidad | Valor | Nota |
|---|---|---|---|
| `steps` | bloque de 30 min (empieza en :00 o :30) | entero 0–8000 | |
| `at_home` | bloque de 30 min | 0 / 1 | **La casa se infiere en el teléfono** (ubicación nocturna más frecuente). No enviar coordenadas GPS. |
| `app_use` | bloque de 30 min | 0 / 1 | 1 si se usó alguna app en el bloque. No enviar nombres de apps. |
| `sleep_minutes` | noche (1–18 h) | 0–1080 | La noche se asigna al día en que despierta. |
| `heart_rate_mean` | bloque de 30 min | 25–230 | Opcional según reloj. |
| `resting_heart_rate` | día | 25–150 | Opcional. |
| `hrv_rmssd_night` | noche | 1–300 ms | Opcional. |
| `spo2_night_mean` | noche | 50–100 % | Opcional; validar si el reloj la mide de verdad. |

Reglas: máximo 5000 observaciones por lote; se aceptan datos de hasta 60 días atrás (acumulados sin red); un bloque repetido se ignora (`duplicate`); los inválidos se rechazan uno por uno sin tumbar el lote, con `index` y `reason`. Catálogo vivo: `GET /catalog/metrics`.

## 3. Cuestionarios y EMA

```http
POST /questionnaires/responses
{
  "client_response_id": "5d1e…",     // UUID del teléfono (idempotencia)
  "instrument": "EMA_DAILY",
  "context": "ema",                  // baseline | weekly | biweekly | monthly | ema | followup | final | adhoc
  "ema_prompt_id": "a3f0…",          // solo si responde a un aviso EMA
  "started_at": "2026-10-16T10:12:00-05:00",
  "completed_at": "2026-10-16T10:13:10-05:00",
  "answers": [ { "item": 1, "value": 6 }, { "item": 2, "value": 4 }, { "item": 3, "value": 2 }, { "item": 4, "value": 7 } ]
}
→ 201 { "response_id": "…", "replayed": false, "support_resources": false }
```

- El teléfono **no recibe puntajes ni alertas**. Si `support_resources` es `true`, la app debe mostrar de inmediato la pantalla de recursos de ayuda (p. ej., Línea 113, opción 5).
- Saltos permitidos: C-SSRS omite 3–5 si el ítem 2 = 0 y el 7 si el 6 = 0; BSS omite 6–19 si los ítems 4 y 5 = 0.
- Bloque del chatbot según el artículo: `PHQ9` (ítems 1-9, 0-3), `GAD7` (1-7, 0-3), `ULS6` (1-6, 1-4) y `CSSRS_SCREEN` (1-7, 0/1).
- BHS (complementario): enviar `1` = "Verdadero", `0` = "Falso"; la clave se aplica en el servidor.
- Los textos de los ítems los pone la app (respetar la licencia de cada instrumento). Catálogo con rangos: `GET /catalog/instruments`.

Avisos EMA: `GET /ema/prompts?hours=48` devuelve los vigentes y próximos para agendar notificaciones locales; al mostrar la notificación, `POST /ema/prompts/{id}/delivered`.

## 4. Canal WebSocket (opcional)

`wss://<servidor>/ws/device`. Primer mensaje: `{"type":"auth","token":"dev_…"}` → `{"type":"auth_ok","participant_code":"P0007"}`.
Luego `{"type":"sensor_batch","data":{…mismo cuerpo que REST…}}` o `{"type":"questionnaire","data":{…}}` → `{"type":"ack","ref":"<batch_id|client_response_id>","data":{…}}` o `{"type":"error","ref":…,"detail":…}`. `{"type":"ping"}` → `{"type":"pong"}`.

## 5. Errores

`401` token inválido o revocado (volver a enrolar) · `403` participante retirado (detener la recolección) · `409` conflicto de idempotencia · `422` validación (el detalle indica el campo).
