"""Avisador de comandas.

Una pantalla «visor» muestra los números de pedido listos para recoger y los
empleados los marcan desde otro dispositivo. Otro software puede enviar las
mismas órdenes por la API /api/v1 con una clave.

Ejecutar con un solo proceso (uvicorn app:app), porque los avisos en tiempo
real se reparten desde la memoria de ese proceso.

Los textos que ve el usuario están en catalán.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
STATIC = BASE_DIR / "static"

VISOR_PASSWORD = os.environ.get("VISOR_PASSWORD", "")
EMPLEADO_PASSWORD = os.environ.get("EMPLEADO_PASSWORD", "")
API_KEY = os.environ.get("API_KEY", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "avisador.db"))
EXPIRA_MINUTOS = float(os.environ.get("EXPIRA_MINUTOS", "15"))
MAX_DIGITOS = int(os.environ.get("MAX_DIGITOS", "4"))
MAX_VISIBLES = int(os.environ.get("MAX_VISIBLES", "13"))
COOKIE_SEGURA = os.environ.get("COOKIE_SEGURA", "1") != "0"

if not VISOR_PASSWORD or not EMPLEADO_PASSWORD:
    raise RuntimeError("Define VISOR_PASSWORD y EMPLEADO_PASSWORD")
if secrets.compare_digest(VISOR_PASSWORD, EMPLEADO_PASSWORD):
    raise RuntimeError("VISOR_PASSWORD y EMPLEADO_PASSWORD deben ser distintas")
if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY debe tener al menos 32 caracteres")
if not 1 <= MAX_DIGITOS <= 9:
    raise RuntimeError("MAX_DIGITOS debe estar entre 1 y 9")

COOKIE = "avisador_sesion"
DURACION_SESION = {"visor": 365 * 86400, "empleado": 30 * 86400}
NUMERO_RE = re.compile(r"^\d{1,%d}$" % MAX_DIGITOS)
SEGUNDOS_ENTREGA = 2        # lo que dura el color de «entregada» antes de desaparecer
MAX_DESHACER = 20           # acciones que se pueden deshacer
DESHACER_MINUTOS = 10       # pasado este tiempo una acción ya no se deshace


# ---------------------------------------------------------------- sesiones

def _firma(datos: bytes) -> str:
    return hmac.new(SECRET_KEY.encode(), datos, hashlib.sha256).hexdigest()


def crear_token(rol: str) -> str:
    datos = json.dumps({"rol": rol, "exp": int(time.time()) + DURACION_SESION[rol]}).encode()
    return base64.urlsafe_b64encode(datos).decode().rstrip("=") + "." + _firma(datos)


def leer_token(token: str):
    try:
        cuerpo, firma = token.split(".", 1)
        datos = base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4))
        if not hmac.compare_digest(firma, _firma(datos)):
            return None
        info = json.loads(datos)
        if info.get("exp", 0) < time.time() or info.get("rol") not in DURACION_SESION:
            return None
        return info["rol"]
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def rol_actual(request: Request):
    """'empleado', 'visor', 'api' o None."""
    clave = request.headers.get("x-api-key", "")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        clave = auth[7:].strip()
    if clave:
        if API_KEY and secrets.compare_digest(clave, API_KEY):
            return "api"
        return None
    return leer_token(request.cookies.get(COOKIE, ""))


def puede_leer(request: Request):
    rol = rol_actual(request)
    if rol is None:
        raise HTTPException(401, "No autoritzat")
    return rol


def puede_avisar(request: Request):
    rol = rol_actual(request)
    if rol is None:
        raise HTTPException(401, "No autoritzat")
    if rol not in ("empleado", "api"):
        raise HTTPException(403, "La pantalla visor només pot mostrar comandes")
    return rol


# ---------------------------------------------------------------- estado

COLUMNAS = "numero, listo_en, avisado_en, avisos, origen, entregado_en"


class Avisador:
    """Pedidos listos, guardados en SQLite para no perderlos al reiniciar."""

    def __init__(self, ruta: str):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(ruta, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS pedidos (
            numero TEXT PRIMARY KEY,
            listo_en REAL NOT NULL,
            avisado_en REAL NOT NULL,
            avisos INTEGER NOT NULL DEFAULT 1,
            origen TEXT NOT NULL,
            entregado_en REAL)""")
        columnas = {fila[1] for fila in self.db.execute("PRAGMA table_info(pedidos)")}
        if "entregado_en" not in columnas:
            self.db.execute("ALTER TABLE pedidos ADD COLUMN entregado_en REAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
        self.db.commit()
        fila = self.db.execute("SELECT valor FROM meta WHERE clave='version'").fetchone()
        self.version = fila[0] if fila else 0
        # Cada entrada: (momento, descripción, [(numero, fila anterior o None), ...])
        self.deshacer_pila: list[tuple[float, str, list]] = []
        self.suscriptores: set[asyncio.Queue] = set()
        self.loop = None

    # -- lectura

    def _deshacer_disponible(self):
        limite = time.time() - DESHACER_MINUTOS * 60
        while self.deshacer_pila and self.deshacer_pila[0][0] < limite:
            self.deshacer_pila.pop(0)
        return self.deshacer_pila[-1][1] if self.deshacer_pila else None

    def estado(self) -> dict:
        with self.lock:
            filas = self.db.execute(f"SELECT {COLUMNAS} FROM pedidos ORDER BY avisado_en DESC").fetchall()
            version = self.version
            deshacer = self._deshacer_disponible()
        pedidos = [
            {"numero": n, "listo_en": l, "avisado_en": a, "avisos": c, "origen": o,
             "entregado": e is not None}
            for n, l, a, c, o, e in filas
        ]
        return {
            "version": version,
            "ultimo": pedidos[0] if pedidos else None,
            "pedidos": pedidos,
            "deshacer": deshacer,
            "max_visibles": MAX_VISIBLES,
            "max_digitos": MAX_DIGITOS,
            "ahora": time.time(),
        }

    def pedido(self, numero: str):
        return next((p for p in self.estado()["pedidos"] if p["numero"] == numero), None)

    # -- cambios (llamar con self.lock tomado)

    def _cambio(self):
        self.version += 1
        self.db.execute(
            "INSERT INTO meta(clave, valor) VALUES('version', ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
            (self.version,),
        )
        self.db.commit()

    def _fila(self, numero: str):
        return self.db.execute(f"SELECT {COLUMNAS} FROM pedidos WHERE numero=?", (numero,)).fetchone()

    def _apuntar(self, descripcion: str, anteriores: list):
        self.deshacer_pila.append((time.time(), descripcion, anteriores))
        del self.deshacer_pila[:-MAX_DESHACER]

    # -- acciones

    def avisar(self, numero: str, origen: str) -> dict:
        """Marca un pedido como listo. Si ya lo estaba, vuelve a avisar."""
        ahora = time.time()
        with self.lock:
            anterior = self._fila(numero)
            self.db.execute(
                """INSERT INTO pedidos(numero, listo_en, avisado_en, avisos, origen) VALUES(?,?,?,1,?)
                   ON CONFLICT(numero) DO UPDATE SET avisado_en=excluded.avisado_en,
                       avisos=avisos+1, entregado_en=NULL""",
                (numero, ahora, ahora, origen),
            )
            self._apuntar(f"comanda {numero} " + ("avisada de nou" if anterior else "avisada"),
                          [(numero, anterior)])
            self._cambio()
        self.publicar()
        return self.pedido(numero)

    def quitar(self, numero: str) -> bool:
        """Lo borra del visor sin más."""
        with self.lock:
            anterior = self._fila(numero)
            if anterior:
                self.db.execute("DELETE FROM pedidos WHERE numero=?", (numero,))
                self._apuntar(f"comanda {numero} eliminada", [(numero, anterior)])
                self._cambio()
        if anterior:
            self.publicar()
        return bool(anterior)

    def entregar(self, numero: str) -> bool:
        """Lo marca como entregado: cambia de color unos segundos y desaparece."""
        with self.lock:
            anterior = self._fila(numero)
            if anterior:
                self.db.execute("UPDATE pedidos SET entregado_en=? WHERE numero=?", (time.time(), numero))
                self._apuntar(f"comanda {numero} entregada", [(numero, anterior)])
                self._cambio()
        if anterior:
            self.publicar()
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.loop.call_later, SEGUNDOS_ENTREGA + 0.05, self.caducar)
        return bool(anterior)

    def vaciar(self) -> int:
        with self.lock:
            anteriores = self.db.execute(f"SELECT {COLUMNAS} FROM pedidos").fetchall()
            if anteriores:
                self.db.execute("DELETE FROM pedidos")
                self._apuntar("buidar el visor", [(fila[0], fila) for fila in anteriores])
                self._cambio()
        if anteriores:
            self.publicar()
        return len(anteriores)

    def deshacer(self):
        """Deshace la última acción (de cualquier empleado o de la API)."""
        with self.lock:
            if not self._deshacer_disponible():
                return None
            _, descripcion, anteriores = self.deshacer_pila.pop()
            for numero, fila in anteriores:
                self.db.execute("DELETE FROM pedidos WHERE numero=?", (numero,))
                if fila:
                    self.db.execute(
                        f"INSERT INTO pedidos({COLUMNAS}) VALUES(?,?,?,?,?,NULL)", fila[:5])
            self._cambio()
        self.publicar()
        return descripcion

    def caducar(self) -> int:
        """Quita los entregados tras su color y los que llevan demasiado en pantalla."""
        ahora = time.time()
        limite = ahora - EXPIRA_MINUTOS * 60 if EXPIRA_MINUTOS > 0 else 0
        with self.lock:
            borrados = self.db.execute(
                "DELETE FROM pedidos WHERE avisado_en < ? OR entregado_en <= ?",
                (limite, ahora - SEGUNDOS_ENTREGA),
            ).rowcount
            if borrados:
                self._cambio()
        if borrados:
            self.publicar()
        return borrados

    # -- tiempo real

    def publicar(self):
        if self.loop is None:
            return
        datos = self.estado()
        for cola in list(self.suscriptores):
            self.loop.call_soon_threadsafe(_poner, cola, datos)


def _poner(cola: asyncio.Queue, datos: dict):
    if cola.full():
        try:
            cola.get_nowait()  # solo interesa el estado más reciente
        except asyncio.QueueEmpty:
            pass
    cola.put_nowait(datos)


avisador = Avisador(DB_PATH)


async def _caducar_periodicamente():
    while True:
        await asyncio.to_thread(avisador.caducar)
        await asyncio.sleep(20)


@asynccontextmanager
async def ciclo(app: FastAPI):
    avisador.loop = asyncio.get_running_loop()
    tarea = asyncio.create_task(_caducar_periodicamente())
    yield
    tarea.cancel()


app = FastAPI(title="Avisador de comandas", version="1.1.0", lifespan=ciclo,
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")


def normalizar(numero) -> str:
    texto = str(numero).strip()
    if not NUMERO_RE.match(texto):
        raise HTTPException(422, f"El número ha de tenir entre 1 i {MAX_DIGITOS} xifres")
    return str(int(texto))


# ---------------------------------------------------------------- sesión web

class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(datos: Login, response: Response):
    if secrets.compare_digest(datos.password, EMPLEADO_PASSWORD):
        rol = "empleado"
    elif secrets.compare_digest(datos.password, VISOR_PASSWORD):
        rol = "visor"
    else:
        time.sleep(0.5)
        raise HTTPException(401, "Contrasenya incorrecta")
    response.set_cookie(COOKIE, crear_token(rol), max_age=DURACION_SESION[rol],
                        httponly=True, samesite="strict", secure=COOKIE_SEGURA)
    return {"rol": rol}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/sesion")
def sesion(request: Request):
    return {"rol": rol_actual(request)}


# ---------------------------------------------------------------- API v1

class NuevoPedido(BaseModel):
    numero: str | int


@app.get("/api/v1/pedidos", tags=["pedidos"])
def listar(rol=Depends(puede_leer)):
    """Pedidos listos, el último avisado primero."""
    return avisador.estado()


@app.post("/api/v1/pedidos", status_code=201, tags=["pedidos"])
def avisar(datos: NuevoPedido, rol=Depends(puede_avisar)):
    """Muestra un pedido en el visor. Si ya estaba, vuelve a avisar."""
    return avisador.avisar(normalizar(datos.numero), rol)


@app.post("/api/v1/pedidos/{numero}/avisar", tags=["pedidos"])
def volver_a_avisar(numero: str, rol=Depends(puede_avisar)):
    """Vuelve a avisar un pedido (sonido y resaltado en el visor)."""
    return avisador.avisar(normalizar(numero), rol)


@app.post("/api/v1/pedidos/{numero}/entregar", tags=["pedidos"])
def entregar(numero: str, rol=Depends(puede_avisar)):
    """Pedido entregado: cambia de color 2 segundos en el visor y desaparece."""
    if not avisador.entregar(normalizar(numero)):
        raise HTTPException(404, "Aquesta comanda no és al visor")
    return {"ok": True}


@app.delete("/api/v1/pedidos/{numero}", tags=["pedidos"])
def quitar(numero: str, rol=Depends(puede_avisar)):
    """Elimina un pedido del visor al momento, sin marcarlo como entregado."""
    if not avisador.quitar(normalizar(numero)):
        raise HTTPException(404, "Aquesta comanda no és al visor")
    return {"ok": True}


@app.delete("/api/v1/pedidos", tags=["pedidos"])
def vaciar(rol=Depends(puede_avisar)):
    """Quita todos los pedidos del visor."""
    return {"quitados": avisador.vaciar()}


@app.post("/api/v1/deshacer", tags=["pedidos"])
def deshacer(rol=Depends(puede_avisar)):
    """Deshace la última acción (avisar, entregar, eliminar o vaciar) de los últimos 10 minutos."""
    descripcion = avisador.deshacer()
    if descripcion is None:
        raise HTTPException(404, "No hi ha res per desfer")
    return {"deshecho": descripcion}


@app.get("/api/v1/eventos", tags=["pedidos"])
async def eventos(request: Request, rol=Depends(puede_leer)):
    """Server-Sent Events: envía el estado completo cada vez que cambia."""
    cola: asyncio.Queue = asyncio.Queue(maxsize=1)
    avisador.suscriptores.add(cola)

    async def flujo():
        try:
            yield "retry: 3000\n\n"
            yield f"event: estado\ndata: {json.dumps(avisador.estado())}\n\n"
            while not await request.is_disconnected():
                try:
                    datos = await asyncio.wait_for(cola.get(), timeout=15)
                    yield f"event: estado\ndata: {json.dumps(datos)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            avisador.suscriptores.discard(cola)

    return StreamingResponse(flujo(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/salud", include_in_schema=False)
def salud():
    return {"ok": True}


# ---------------------------------------------------------------- páginas

def _pagina(nombre: str):
    return FileResponse(STATIC / nombre, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def inicio(request: Request):
    rol = rol_actual(request)
    destino = {"visor": "/visor", "empleado": "/empleado"}.get(rol)
    if destino:
        return Response(status_code=303, headers={"Location": destino})
    return _pagina("login.html")


@app.get("/visor", include_in_schema=False)
def visor(request: Request):
    if rol_actual(request) not in ("visor", "empleado"):
        return Response(status_code=303, headers={"Location": "/"})
    return _pagina("visor.html")


@app.get("/empleado", include_in_schema=False)
def empleado(request: Request):
    if rol_actual(request) != "empleado":
        return Response(status_code=303, headers={"Location": "/"})
    return _pagina("empleado.html")


@app.get("/static/{nombre}", include_in_schema=False)
def estatico(nombre: str):
    ruta = (STATIC / nombre).resolve()
    if ruta.parent != STATIC or not ruta.is_file():
        raise HTTPException(404)
    return FileResponse(ruta)
