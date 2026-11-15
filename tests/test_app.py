import importlib
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def modulo(tmp_path, monkeypatch):
    monkeypatch.setenv("VISOR_PASSWORD", "visor-123")
    monkeypatch.setenv("EMPLEADO_PASSWORD", "empleado-123")
    monkeypatch.setenv("API_KEY", "clave-api-externa")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("COOKIE_SEGURA", "0")
    monkeypatch.setenv("EXPIRA_MINUTOS", "15")
    sys.modules.pop("app", None)
    return importlib.import_module("app")


@pytest.fixture
def anonimo(modulo):
    with TestClient(modulo.app) as c:
        yield c


def cliente(modulo, password):
    c = TestClient(modulo.app)
    assert c.post("/api/login", json={"password": password}).status_code == 200
    return c


@pytest.fixture
def empleado(modulo):
    with cliente(modulo, "empleado-123") as c:
        yield c


@pytest.fixture
def visor(modulo):
    with cliente(modulo, "visor-123") as c:
        yield c


API = {"X-API-Key": "clave-api-externa"}


def test_login_incorrecto(anonimo):
    assert anonimo.post("/api/login", json={"password": "mal"}).status_code == 401
    assert anonimo.get("/api/v1/pedidos").status_code == 401


def test_roles_en_login(anonimo):
    assert anonimo.post("/api/login", json={"password": "visor-123"}).json() == {"rol": "visor"}
    assert anonimo.get("/api/sesion").json() == {"rol": "visor"}


def test_empleado_avisa_y_visor_lo_ve(empleado, visor):
    r = empleado.post("/api/v1/pedidos", json={"numero": "042"})
    assert r.status_code == 201
    assert r.json()["numero"] == "42"
    estado = visor.get("/api/v1/pedidos").json()
    assert estado["ultimo"]["numero"] == "42"
    assert [p["numero"] for p in estado["pedidos"]] == ["42"]


def test_visor_no_puede_avisar(visor):
    assert visor.post("/api/v1/pedidos", json={"numero": 1}).status_code == 403
    assert visor.delete("/api/v1/pedidos").status_code == 403


def test_numero_invalido(empleado):
    for malo in ["", "abc", "12345", "-1", "1.5"]:
        assert empleado.post("/api/v1/pedidos", json={"numero": malo}).status_code == 422, malo


def test_volver_a_avisar_lo_pone_el_primero(empleado):
    empleado.post("/api/v1/pedidos", json={"numero": 1})
    time.sleep(0.01)
    empleado.post("/api/v1/pedidos", json={"numero": 2})
    time.sleep(0.01)
    r = empleado.post("/api/v1/pedidos/1/avisar")
    assert r.json()["avisos"] == 2
    estado = empleado.get("/api/v1/pedidos").json()
    assert [p["numero"] for p in estado["pedidos"]] == ["1", "2"]


def test_quitar_y_vaciar(empleado):
    for n in (5, 6, 7):
        empleado.post("/api/v1/pedidos", json={"numero": n})
    assert empleado.delete("/api/v1/pedidos/6").status_code == 200
    assert empleado.delete("/api/v1/pedidos/6").status_code == 404
    assert empleado.delete("/api/v1/pedidos").json() == {"quitados": 2}
    assert empleado.get("/api/v1/pedidos").json()["pedidos"] == []


def test_api_externa_con_clave(anonimo):
    r = anonimo.post("/api/v1/pedidos", json={"numero": 77}, headers=API)
    assert r.status_code == 201
    assert r.json()["origen"] == "api"
    bearer = {"Authorization": "Bearer clave-api-externa"}
    assert anonimo.delete("/api/v1/pedidos/77", headers=bearer).status_code == 200


def test_api_clave_incorrecta(anonimo):
    r = anonimo.post("/api/v1/pedidos", json={"numero": 1}, headers={"X-API-Key": "otra"})
    assert r.status_code == 401


def test_caducidad(modulo, empleado):
    empleado.post("/api/v1/pedidos", json={"numero": 9})
    modulo.avisador.db.execute("UPDATE pedidos SET avisado_en = avisado_en - 3600")
    assert modulo.avisador.caducar() == 1
    assert empleado.get("/api/v1/pedidos").json()["pedidos"] == []


def test_persiste_al_reiniciar(modulo, empleado):
    empleado.post("/api/v1/pedidos", json={"numero": 3})
    nuevo = modulo.Avisador(modulo.DB_PATH)
    assert [p["numero"] for p in nuevo.estado()["pedidos"]] == ["3"]
    assert nuevo.version == modulo.avisador.version


def test_token_manipulado(modulo, anonimo):
    token = modulo.crear_token("visor")
    cuerpo, firma = token.split(".")
    falso = modulo.base64.urlsafe_b64encode(b'{"rol":"empleado","exp":9999999999}').decode().rstrip("=")
    anonimo.cookies.set(modulo.COOKIE, falso + "." + firma)
    assert anonimo.get("/api/sesion").json() == {"rol": None}


def test_paginas_segun_rol(anonimo, visor, empleado):
    assert anonimo.get("/empleado", follow_redirects=False).status_code == 303
    assert visor.get("/empleado", follow_redirects=False).status_code == 303
    assert visor.get("/visor").status_code == 200
    assert empleado.get("/empleado").status_code == 200
    assert visor.get("/", follow_redirects=False).headers["location"] == "/visor"


def test_eventos_publica_cambios(modulo, empleado):
    import asyncio

    async def prueba():
        modulo.avisador.loop = asyncio.get_running_loop()
        cola = asyncio.Queue(maxsize=1)
        modulo.avisador.suscriptores.add(cola)
        await asyncio.to_thread(modulo.avisador.avisar, "15", "empleado")
        datos = await asyncio.wait_for(cola.get(), 2)
        modulo.avisador.suscriptores.discard(cola)
        return datos

    datos = asyncio.run(prueba())
    assert datos["ultimo"]["numero"] == "15"
