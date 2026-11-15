# Avisador de comandas

Muestra en una pantalla grande los números de pedido listos para recoger. Los empleados marcan los números desde otro dispositivo (móvil, tablet u ordenador) y la pantalla se actualiza al instante, con un aviso sonoro y un destello.

## Dos usuarios

| Usuario | Contraseña | Qué ve |
|---|---|---|
| **Visor** | `VISOR_PASSWORD` | `/visor`: pantalla en horizontal con el último pedido en grande y el resto de pedidos listos al lado. Solo puede mirar. La sesión dura un año, así que la pantalla no se desconecta. |
| **Empleado** | `EMPLEADO_PASSWORD` | `/empleado`: teclado numérico para avisar un pedido, y la lista de lo que está en el visor con *Avisar otra vez* y *Quitar*. También acepta el teclado físico (cifras, Intro, Retroceso, Esc). La sesión dura 30 días. |

Las dos entran por la misma página (`/`); la contraseña decide el papel.

En el visor, toca la pantalla una vez al encenderlo: el navegador exige un toque para permitir el sonido y la pantalla completa. La página también pide que la pantalla no se apague.

## Comportamiento

- Avisar un número que ya está en el visor lo vuelve a avisar: pasa a ser el principal y suena otra vez.
- Cada pedido desaparece solo `EXPIRA_MINUTOS` después de su último aviso (15 por defecto; `0` lo desactiva).
- Los números son solo cifras, de 1 a `MAX_DIGITOS` (4 por defecto). Los ceros a la izquierda se quitan: `042` es el pedido `42`.
- El estado se guarda en SQLite, de modo que un reinicio no vacía el visor.

## Puesta en marcha

```bash
cd avisador-comandes
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # rellena las contraseñas, SECRET_KEY y, si hace falta, API_KEY
set -a && . ./.env && set +a
uvicorn app:app --host 0.0.0.0 --port 8000
```

Para probar en local sin HTTPS pon `COOKIE_SEGURA=0`; en producción debe ir detrás de HTTPS.

Con Docker:

```bash
docker build -t avisador-comandas .
docker run -d -p 8000:8000 --env-file .env -v avisador-datos:/data avisador-comandas
```

Ejecútalo siempre con **un solo proceso** (sin `--workers`): los avisos en tiempo real se reparten desde la memoria del proceso.

## API para otro software

Todo lo que hace la pantalla de empleado está disponible en `/api/v1`. Otro programa (TPV, cocina, web de pedidos…) se identifica con la clave `API_KEY` en la cabecera `X-API-Key` o `Authorization: Bearer <clave>`. Si `API_KEY` está vacía, el acceso externo queda desactivado.

La documentación interactiva (OpenAPI) está en `/api/docs`.

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/api/v1/pedidos` | Avisa un pedido. Cuerpo: `{"numero": "42"}` (texto o número). Si ya estaba, lo vuelve a avisar. Responde `201` con el pedido. |
| `POST` | `/api/v1/pedidos/{numero}/avisar` | Vuelve a avisar un pedido. |
| `DELETE` | `/api/v1/pedidos/{numero}` | Quita un pedido (recogido). `404` si no estaba. |
| `DELETE` | `/api/v1/pedidos` | Vacía el visor. |
| `GET` | `/api/v1/pedidos` | Estado actual: `ultimo`, `pedidos` (el último avisado primero), `version`. |
| `GET` | `/api/v1/eventos` | [Server-Sent Events](https://developer.mozilla.org/es/docs/Web/API/Server-sent_events): manda el estado completo cada vez que cambia. |

Ejemplos:

```bash
curl -X POST https://tu-servidor/api/v1/pedidos \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"numero": 42}'
```

```bash
curl -X DELETE https://tu-servidor/api/v1/pedidos/42 -H "X-API-Key: $API_KEY"
```

Respuesta de un aviso:

```json
{"numero": "42", "listo_en": 1763222400.1, "avisado_en": 1763222400.1, "avisos": 1, "origen": "api"}
```

`origen` indica quién lo avisó (`empleado` o `api`). Las horas son segundos Unix.

Errores: `401` sin credenciales o clave incorrecta, `403` si lo intenta el visor, `422` si el número no es válido.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```
