# API Cheese Bot — Documentación de Endpoints

**Base URL:** `https://<servidor>/`  
**Autenticación:** Todas las rutas requieren una API Key enviada en el header:
```
X-API-Key: <tu_api_key>
```

---

## Prefijo `/erp`

---

### `POST /erp/send-whatsapp`
Envía un mensaje de texto libre a un contacto por WhatsApp.

**Restricción:** Solo funciona si el contacto envió un mensaje en las últimas 24 horas (ventana de META). Si la ventana expiró, se devuelve un error 422.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `contact_id` | string | ✅ | ID del contacto en el ERP |
| `message` | string | ✅ | Texto a enviar |

**Respuesta exitosa `200`:**
```json
{ "status": "ok", "phone": "+34612345678" }
```

**Errores posibles:**
| Código | Causa |
|---|---|
| `404` | El contacto no existe en el ERP |
| `422` | El contacto no tiene teléfono registrado, no hay historial de mensajes o la ventana de 24h expiró |
| `502` | Error de comunicación con el ERP o con la API de WhatsApp |

---

### `POST /erp/send-telegram`
Envía un mensaje de texto libre a un usuario por Telegram.

**No requiere ventana de tiempo**, el mensaje se envía directamente.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `contact_id` | string | ✅ | Telegram Chat ID del destinatario |
| `message` | string | ✅ | Texto a enviar |

**Respuesta exitosa `200`:**
```json
{ "status": "ok", "chat_id": "123456789" }
```

**Errores posibles:**
| Código | Causa |
|---|---|
| `502` | Error al enviar el mensaje a través de la API de Telegram |

---

### `POST /erp/ticket-status`
Notifica al cliente el resultado de su reserva (aprobada, rechazada o expirada) mediante WhatsApp. El mensaje es generado automáticamente según el estado enviado.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `contact_id` | string | ✅ | ID del contacto en el ERP |
| `ticket_id` | string | ✅ | ID del ticket afectado |
| `new_status` | string (enum) | ✅ | Estado: `approved`, `rejected` o `expired` |
| `observations` | string | ❌ | Texto adicional del operador que se incluye en el mensaje |

**Respuesta exitosa `200`:**
```json
{ "status": "ok", "phone": "+34612345678" }
```

**Errores posibles:**
| Código | Causa |
|---|---|
| `404` | El contacto no existe en el ERP |
| `422` | El contacto no tiene teléfono registrado |
| `502` | Error con el ERP o con la API de WhatsApp |

---

### `POST /erp/activity-completed`
Notifica que un cliente completó una actividad. Endpoint preparado para disparar el envío de una encuesta de satisfacción (lógica pendiente de implementación).

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `contact_id` | string | ✅ | ID del contacto en el ERP |
| `experience_id` | string | ✅ | ID de la experiencia completada |
| `slot_id` | string | ✅ | ID del slot en que se realizó la actividad |
| `ticket_id` | string | ✅ | ID del ticket asociado |

**Respuesta `200`:**
```json
{ "status": "pending_implementation" }
```

> ⚠️ Este endpoint aún no tiene lógica activa. Responde siempre con `pending_implementation`.

---

### `GET /erp/prompt`
Devuelve el prompt de sistema actual del agente de IA.

**Sin body ni parámetros.**

**Respuesta exitosa `200`:**
```json
{ "prompt": "Eres un asistente experto en..." }
```

**Errores posibles:**
| Código | Causa |
|---|---|
| `404` | El archivo de prompt no existe en el servidor |

---

### `PUT /erp/prompt`
Reemplaza el prompt de sistema del agente de IA. El cambio tiene efecto inmediato en el próximo mensaje procesado.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `prompt` | string | ✅ | Texto completo del nuevo prompt |

**Respuesta exitosa `200`:**
```json
{ "status": "ok", "chars": "1542" }
```

**Errores posibles:**
| Código | Causa |
|---|---|
| `500` | Error al escribir el archivo en disco |

---

### `POST /erp/take-control/whatsapp`
Desactiva las respuestas automáticas del bot de IA para un número de WhatsApp específico. Esto permite que un operador humano tome el control de la conversación.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `phone` | string | ✅ | Número de WhatsApp del cliente (ej: `+59899000000`) |

**Respuesta exitosa `200`:**
```json
{ "status": "controlled", "phone": "+59899000000" }
```

---

### `POST /erp/release-control/whatsapp`
Reactiva las respuestas automáticas del bot de IA para un número de WhatsApp que estaba bajo control humano.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `phone` | string | ✅ | Número de WhatsApp del cliente (ej: `+59899000000`) |

**Respuesta exitosa `200`:**
```json
{ "status": "released", "phone": "+59899000000" }
```

---

### `POST /erp/take-control/telegram`
Desactiva las respuestas automáticas del bot de IA para un chat de Telegram específico.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `chat_id` | string | ✅ | Telegram Chat ID del cliente |

**Respuesta exitosa `200`:**
```json
{ "status": "controlled", "chat_id": "123456789" }
```

---

### `POST /erp/release-control/telegram`
Reactiva las respuestas automáticas del bot de IA para un chat de Telegram que estaba bajo control humano.

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `chat_id` | string | ✅ | Telegram Chat ID del cliente |

**Respuesta exitosa `200`:**
```json
{ "status": "released", "chat_id": "123456789" }
```

---

## Prefijo `/chat`

---

### `GET /chat/users`
Devuelve la lista de todos los usuarios registrados en la base de datos del bot.

**Sin body ni parámetros.**

**Respuesta exitosa `200` — array de objetos `User`:**
```json
[
  {
    "phone": "+34612345678",
    "name": "María García",
    "email": "maria@ejemplo.com",
    "resume": "Interesada en rutas del queso",
    "permissions": null,
    "created_at": "2026-01-15T10:23:00",
    "updated_at": "2026-03-20T08:00:00",
    "last_interaction": "2026-03-24T17:45:00"
  }
]
```

**Campos del objeto `User`:**
| Campo | Tipo | Nullable | Descripción |
|---|---|---|---|
| `phone` | string | ❌ | Número de teléfono (clave primaria) |
| `name` | string | ✅ | Nombre del usuario |
| `email` | string (email) | ✅ | Correo electrónico |
| `resume` | string | ✅ | Resumen del perfil generado por el agente |
| `permissions` | string | ✅ | Permisos especiales del usuario |
| `created_at` | datetime (ISO 8601) | ❌ | Fecha de creación |
| `updated_at` | datetime (ISO 8601) | ❌ | Última actualización |
| `last_interaction` | datetime (ISO 8601) | ❌ | Última vez que interactuó con el bot |

---

### `GET /chat/users/{phone}`
Devuelve los datos de un usuario específico por número de teléfono.

**Path param:**
| Param | Tipo | Descripción |
|---|---|---|
| `phone` | string | Número de teléfono del usuario |

**Respuesta exitosa `200`:** Objeto `User` (misma estructura que el listado anterior).

---

### `GET /chat/messages/{phone}`
Devuelve el historial de mensajes de un usuario.

**Path param:**
| Param | Tipo | Descripción |
|---|---|---|
| `phone` | string | Número de teléfono del usuario |

**Respuesta exitosa `200` — array de objetos `Message`:**
```json
[
  {
    "user_phone": "+34612345678",
    "role": "user",
    "message": "¿Cuáles son las rutas disponibles?",
    "tools_used": null,
    "created_at": "2026-03-24T17:45:00"
  },
  {
    "user_phone": "+34612345678",
    "role": "assistant",
    "message": "Tenemos estas rutas disponibles...",
    "tools_used": "get_routes,get_availability",
    "created_at": "2026-03-24T17:45:05"
  }
]
```

**Campos del objeto `Message`:**
| Campo | Tipo | Nullable | Descripción |
|---|---|---|---|
| `user_phone` | string | ❌ | Teléfono del usuario al que pertenece el mensaje |
| `role` | string | ✅ | `user` o `assistant` |
| `message` | string | ✅ | Contenido del mensaje |
| `tools_used` | string | ✅ | Herramientas que el agente utilizó para responder (separadas por coma) |
| `created_at` | datetime (ISO 8601) | ❌ | Fecha y hora del mensaje |

---

### `GET /chat/reminders`
Devuelve la lista de recordatorios registrados en el sistema. Permite filtrar por tipo de recordatorio y por estado.

**Query params:**
| Param | Tipo | Opciones | Default |
|---|---|---|---|
| `reminder_type` | string | `deposit`, `event`, `lead_followup` | todos los tipos |
| `status` | string | `pending`, `done` | todos los estados |

**Tipos de recordatorio:**
| Valor | Descripción |
|---|---|
| `deposit` | Recordatorio de pago de seña pendiente |
| `event` | Recordatorio de evento próximo (día del evento) |
| `lead_followup` | Recordatorio para solicitar reserva a un lead |

**`scheduled_at` según tipo y estado:**
| Tipo | Estado | Valor de `scheduled_at` |
|---|---|---|
| `deposit` | `pending` | `COALESCE(reminded_at, confirmed_at) + 4h` |
| `deposit` | `done` | `reminded_at` (timestamp del último enviado) |
| `event` | `pending` / `done` | `ticket_date` combinado con `slot_time` |
| `lead_followup` | `pending` | último mensaje del usuario + 4h |
| `lead_followup` | `done` | timestamp del último follow-up enviado |

**Respuesta exitosa `200` — array de objetos `ReminderItem`:**
```json
[
  {
    "phone": "+59891234567",
    "name": "María García",
    "reminder_type": "deposit",
    "status": "pending",
    "scheduled_at": "2026-04-10T14:00:00",
    "ticket_id": "TKT-001"
  },
  {
    "phone": "+59898765432",
    "name": null,
    "reminder_type": "lead_followup",
    "status": "pending",
    "scheduled_at": "2026-04-10T18:30:00",
    "ticket_id": null
  }
]
```

**Campos del objeto `ReminderItem`:**
| Campo | Tipo | Nullable | Descripción |
|---|---|---|---|
| `phone` | string | ❌ | Teléfono del cliente |
| `name` | string | ✅ | Nombre del cliente (null si no está registrado) |
| `reminder_type` | string (enum) | ❌ | Tipo de recordatorio: `deposit`, `event` o `lead_followup` |
| `status` | string (enum) | ❌ | `pending` = aún no enviado / `done` = ya enviado o completado |
| `scheduled_at` | datetime (ISO 8601) | ✅ | Fecha y hora programada del recordatorio |
| `ticket_id` | string | ✅ | ID del ticket (solo para `deposit` y `event`, null para `lead_followup`) |

---

## Prefijo `/demo`

Endpoints para la web de marketing. Permiten a visitantes interactuar con una versión recortada del agente sin crear datos reales en el ERP. Las reservas se guardan en memoria y se pierden al reiniciar el servidor.

---

### `POST /demo/chat`
Ejecuta un turno de conversación con el agente demo y devuelve la respuesta directamente en el body.

**Limitaciones respecto al agente de producción:**
- No se crea ningún contacto, lead ni ticket en el ERP.
- Las herramientas de CRM, pagos, recordatorios y soporte post-venta están deshabilitadas.
- Las reservas se almacenan en memoria (se pierden al reiniciar el servidor).
- Solo admite texto; no procesa audio ni imágenes.

**Headers requeridos:**
```
X-API-Key: <ADMIN_API_KEY>
Content-Type: application/json
```

**Body (JSON):**
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `session_id` | string (UUID v4) | ❌ | Identificador de sesión. Si se omite, el servidor genera uno nuevo. Debe enviarse en turnos siguientes para mantener el historial. |
| `message` | string | ✅ | Mensaje del usuario |

**Respuesta exitosa `200`:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "response": "¡Hola! Tenemos las siguientes experiencias disponibles...",
  "tools_used": ["list_experiences", "get_availability"]
}
```

**Campos de la respuesta:**
| Campo | Tipo | Descripción |
|---|---|---|
| `session_id` | string (UUID v4) | ID de la sesión. Retornar en las siguientes peticiones para mantener el contexto. |
| `response` | string | Respuesta generada por el agente |
| `tools_used` | array de strings | Herramientas del agente invocadas durante el turno |

**IDs de reservas demo:**
Los IDs generados por las herramientas de reserva siguen el formato:
- Tickets individuales: `DEMO-TKT-{session_prefix}-{contador}` (ej: `DEMO-TKT-550E-0001`)
- Reservas de ruta: `DEMO-RB-{session_prefix}-{contador}` (ej: `DEMO-RB-550E-0001`)

**Errores posibles:**
| Código | Causa |
|---|---|
| `401` | Header `X-API-Key` ausente |
| `403` | API key inválida |
| `422` | Body inválido o campo `message` faltante |
| `500` | Error interno del servidor |

