# Avisador de comandas

Muestra en una pantalla grande los números de pedido listos para recoger. Los empleados marcan los números desde otro dispositivo (móvil, tablet u ordenador) y la pantalla se actualiza al instante, con un aviso sonoro y un destello.

## Dos usuarios

| Usuario | Contraseña | Qué ve |
|---|---|---|
| **Visor** | `VISOR_PASSWORD` | `/visor`: pantalla en horizontal con el último pedido en grande y el resto de pedidos listos al lado. Solo puede mirar. La sesión dura un año, así que la pantalla no se desconecta. |
| **Empleado** | `EMPLEADO_PASSWORD` | `/empleado`: teclado numérico para avisar un pedido, y la lista de lo que está en el visor en cuadros: se tocan para seleccionar una o varias y se aplica *Remarcar* (volver a avisar), *Entregada* o *Eliminar*. Además, un botón *Desfer*. También acepta el teclado físico (cifras, Intro, Retroceso, Esc). Puede abrir el visor y volver con *← Tornar*. La sesión dura 30 días. |

Las dos entran por la misma página (`/`); la contraseña decide el papel. Los textos de las pantallas están en catalán.

En el visor, toca la pantalla una vez al encenderlo: el navegador exige un toque para permitir el sonido y la pantalla completa. La página también pide que la pantalla no se apague.

## Comportamiento

- Avisar un número que ya está en el visor lo vuelve a avisar: pasa a ser el principal y suena otra vez.
- *Entregada*: la comanda cambia de color (azul) durante 2 segundos en el visor y desaparece.
- *Eliminar*: la comanda desaparece del visor al momento, sin cambio de color.
- *Remarcar*: vuelve a avisar, la comanda vuelve a salir en grande y suena, pero en la lista se queda en su sitio (la lista va por orden de llegada) y se marca en amarillo. Con varias seleccionadas, la última elegida queda en grande.
- En la pantalla de empleado, la comanda que lleva más de *alerta_minutos* sin entregar (desde el primer aviso) se marca en rojo.
- *Desfer*: deshace la última acción (avisar, entregar, eliminar o vaciar) de cualquier empleado o de la API, de los últimos 10 minutos. Se puede pulsar varias veces para ir hacia atrás.
- Cada pedido desaparece solo pasados unos minutos desde su último aviso (15 por defecto; `0` lo desactiva).

## Configuración (⚙)

El botón ⚙ de la pantalla de empleado abre la configuración, compartida por todos los dispositivos:

| Campo | Qué es | Por defecto |
|---|---|---|
| Minutos para rojo | Minutos sin entregar tras los que la comanda se marca en rojo (0 = nunca) | `ALERTA_MINUTOS`, 10 |
| Minutos para quitarla | Minutos desde el último aviso tras los que se quita sola del visor (0 = nunca) | `EXPIRA_MINUTOS`, 15 |

Se guarda en la base de datos. En el plan gratuito de Render no hay disco: tras cada reinicio vuelve a los valores de `ALERTA_MINUTOS` y `EXPIRA_MINUTOS` del servicio, así que si quieres que un valor se mantenga, cámbialo también allí (Environment).
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

## Publicar en Render

El archivo `render.yaml` deja el servicio configurado:

1. En Render: **New → Blueprint**, elige este repositorio y pulsa **Apply**.
2. Render pide `VISOR_PASSWORD` y `EMPLEADO_PASSWORD` (distintas). `SECRET_KEY` y `API_KEY` las genera solo; la `API_KEY` se consulta en **Environment** del servicio.
3. Al terminar, abre la dirección `https://….onrender.com` que muestra Render.

Está configurado en el **plan gratuito**, pensado para pruebas:

- No hay disco: los pedidos del visor se pierden cada vez que el servicio se reinicia o se publica un cambio.
- Render duerme el servicio tras 15 minutos sin visitas. Mientras la pantalla visor esté abierta, ella misma hace una visita cada 5 minutos para mantenerlo despierto. Si nadie lo tiene abierto, la primera visita después tarda alrededor de un minuto en cargar.
- El plan gratuito da 750 horas al mes para todos tus servicios gratuitos juntos; un servicio despierto todo el mes gasta unas 730.

Para usarlo de verdad, cambia en `render.yaml` a `plan: starter` y descomenta el disco y `DB_PATH`.

Cada vez que se sube un cambio a `main`, Render vuelve a publicar.

## API para otro software

Todo lo que hace la pantalla de empleado está disponible en `/api/v1`. Otro programa (TPV, cocina, web de pedidos…) se identifica con la clave `API_KEY` en la cabecera `X-API-Key` o `Authorization: Bearer <clave>`. Si `API_KEY` está vacía, el acceso externo queda desactivado.

La documentación interactiva (OpenAPI) está en `/api/docs`.

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/api/v1/pedidos` | Avisa un pedido. Cuerpo: `{"numero": "42"}` (texto o número). Si ya estaba, lo vuelve a avisar. Responde `201` con el pedido. |
| `POST` | `/api/v1/pedidos/{numero}/avisar` | Vuelve a avisar un pedido. |
| `POST` | `/api/v1/pedidos/{numero}/entregar` | Pedido entregado: cambia de color 2 segundos y desaparece. `404` si no estaba. |
| `DELETE` | `/api/v1/pedidos/{numero}` | Elimina un pedido al momento. `404` si no estaba. |
| `POST` | `/api/v1/pedidos/entregar` | Entrega varios a la vez. Cuerpo: `{"numeros": ["12", "37"]}`. Responde `{"entregados": [...]}` con los que estaban. |
| `POST` | `/api/v1/pedidos/eliminar` | Elimina varios a la vez. Cuerpo: `{"numeros": ["12", "37"]}`. Responde `{"eliminados": [...]}`. |
| `DELETE` | `/api/v1/pedidos` | Vacía el visor. |
| `POST` | `/api/v1/deshacer` | Deshace la última acción. Responde `{"deshecho": "comanda 42 entregada"}`, o `404` si no hay nada. |
| `GET` / `PUT` | `/api/v1/config` | Lee o cambia la configuración: `{"alerta_minutos": 10, "expira_minutos": 15}` (solo los campos que cambian). |
| `GET` | `/api/v1/pedidos` | Estado actual: `ultimo` (la que sale en grande), `config`, `pedidos` (por orden de llegada, la más reciente primero; `entregado: true` mientras dura el color), `deshacer` (descripción de lo que se desharía) y `version`. |
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
{"numero": "42", "listo_en": 1763222400.1, "avisado_en": 1763222400.1, "avisos": 1, "origen": "api", "entregado": false}
```

`origen` indica quién lo avisó (`empleado` o `api`). Las horas son segundos Unix.

Los mensajes de error (`detail`) están en catalán, porque se muestran en la pantalla de empleado. Errores: `401` sin credenciales o clave incorrecta, `403` si lo intenta el visor, `422` si el número no es válido.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```
