# GSTI · Backend

Servidor del *Sistema Móvil Inteligente de Monitoreo Comportamental para la Detección Temprana de Riesgo Suicida en Estudiantes de Sistemas de la UNSA*.

Recibe en JSON (REST y WebSocket) las bioseñales del wearable, los indicadores del smartphone y las respuestas EMA/cuestionarios; los guarda **seudonimizados** en PostgreSQL; puntúa los instrumentos; y genera **alertas para revisión humana** en el tablero. El sistema no diagnostica ni contacta al estudiante por sí mismo.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16 · Alembic · Docker Compose.

## Arranque rápido (Docker)

```bash
cp .env.example .env          # completar contraseñas y secretos (instrucciones dentro)
docker compose up -d --build  # crea la BD, roles, migra y levanta la API en :8000
docker compose exec api python -m scripts.create_admin --email tu@unsa.edu.pe --name "Tu Nombre"
```

- API y Swagger: http://localhost:8000/docs
- Simular un teléfono con datos sintéticos:
  `python -m scripts.simulate_device --email tu@unsa.edu.pe --password '…' --days 3`

## Pruebas

Requieren un PostgreSQL con superusuario (no hace falta que la API esté corriendo):

```bash
pip install -r requirements-dev.txt
TEST_ADMIN_DSN=postgresql://gsti_owner:<pwd>@localhost:5432/postgres pytest
```

Cubren puntuación de instrumentos, reglas de alerta, permisos por rol, idempotencia, rechazo de datos inválidos, cifrado de identidad, EMA y adherencia, WebSockets, retiro del participante y las vistas analíticas.

## Diseño de privacidad

| Capa | Qué contiene | Quién accede |
|---|---|---|
| `core` | Datos de investigación con seudónimo (`P0001`); sin nombres, correos ni coordenadas | API (`gsti_api`) |
| `identity` | Nombre, correo y teléfono **cifrados** (Fernet; clave fuera de la BD) y código de estudiante como HMAC | API; se revela solo a admin/clínico, con registro en auditoría |
| `analytics` | Vistas persona-día y persona-semana listas para el modelo | Equipo de modelado (`gsti_analyst`), que **no** ve `core` ni `identity` |
| `admin` | Personal y bitácora de auditoría (solo anexar) | API |

Minimización: la ubicación "en casa" se calcula en el teléfono y solo llega un 0/1 por bloque; el uso de apps llega como 0/1 sin nombres; la edad se guarda en bandas desde 18 años. El retiro del consentimiento revoca dispositivos, cancela EMA futuras y borra la identidad; con `purge_data` borra todo.

## Protocolo de alertas

| Regla | Disparador | Severidad |
|---|---|---|
| `cssrs_positive` | C-SSRS: ideación nivel 1 / nivel 2–3 o conducta en la vida / nivel 4–5 o conducta en 3 meses | baja / moderada / alta |
| `bss_positive` | BSS total ≥ `BSS_ALERT_CUTOFF` (activa derivación independiente del modelo) | alta |
| `phq9_item9` | PHQ-9 ítem 9 ≥ 1 (≥ 2: alta) | moderada / alta |
| `nssi_reported` | Autolesión no suicida reportada | moderada |
| `adversity_disclosed` | Alguna adversidad reportada | baja |
| `model_positive` | Predicción persona-semana ≥ umbral | moderada |

**Los puntos de corte son configurables y deben fijarlos el equipo clínico y el comité de ética** (los valores por defecto son conservadores). Cada alerta pasa por `abierta → reconocida → derivada/cerrada` con nota y responsable.

## Mapa de endpoints

- Personal: `/auth/login`, `/participants` (enrolar, resumen, identidad, retiro, calendario EMA), `/alerts`, `/predictions`, `/metrics/latency` (H4), `/metrics/adherence` (H5).
- Teléfono: `/devices/register`, `/ingest/sensors`, `/questionnaires/responses`, `/ema/prompts`, `/catalog/*`.
- Tiempo real: `/ws/device` (ingesta) y `/ws/dashboard` (alertas en vivo).

Contrato detallado para el equipo de la app: [docs/contrato-api.md](docs/contrato-api.md). Esquema SQL comentado: [migrations/sql/0001_schema.sql](migrations/sql/0001_schema.sql).

## Limitaciones conocidas

- Un solo proceso de uvicorn: el bus del WebSocket es en memoria. Para escalar, migrar a `LISTEN/NOTIFY` o Redis.
- Las vistas analíticas fijan la zona `America/Lima`.
- Desplegar detrás de HTTPS (proxy inverso) antes de usar datos reales.
- Aún no hay límite de intentos en `/auth/login` ni `/devices/register`; agregarlo (proxy o middleware) antes del piloto.
