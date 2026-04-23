from __future__ import annotations

import os
import sqlite3
import uuid
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

# =========================================================
# CONFIG
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads_cortes"
DB_PATH = DATA_DIR / "cortes_app.db"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("CORTES_APP_SECRET", "dev-secret-cambiar")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

from datetime import datetime, timedelta

TZ_MTY = ZoneInfo("America/Monterrey")
HOY_MTY = datetime.now(TZ_MTY).date()
AYER = (HOY_MTY - timedelta(days=1)).strftime("%Y-%m-%d")

# =========================================================
# CATÁLOGO DE SUCURSALES
# =========================================================
SUCURSALES = [
    {"id": 1, "nombre": "Generales Revolución", "alias": ["revolucion", "generales revolucion"]},
    {"id": 2, "nombre": "Generales Galerías", "alias": ["galerias", "generales galerias"]},
    {"id": 3, "nombre": "Generales Linda Vista", "alias": ["linda vista", "generales linda vista","GRLV"]},
    {"id": 4, "nombre": "Generales Barragán", "alias": ["barragan", "generales barragan"]},
    {"id": 5, "nombre": "Generales Santa Catarina", "alias": ["santa catarina", "generales santa catarina"]},
    {"id": 6, "nombre": "Casona Linda Vista", "alias": ["casona linda vista"]},
    {"id": 7, "nombre": "Casona Galerías", "alias": ["casona galerias","csg"]},
    {"id": 8, "nombre": "Generales Sendero la Fe", "alias": ["sendero la fe", "sendero fe", "sendero"]},
    {"id": 9, "nombre": "Buchakas Citadel", "alias": ["citadel", "buchakas citadel"]},
    {"id": 10, "nombre": "Buchakas Esfera", "alias": ["esfera", "buchakas esfera"]},
    {"id": 11, "nombre": "Buchakas Interplaza", "alias": ["interplaza", "buchakas interplaza"]},
    {"id": 12, "nombre": "Buchakas San Roque", "alias": ["san roque", "buchakas san roque"]},
    {"id": 13, "nombre": "Buchakas Apodaca", "alias": ["apodaca", "buchakas apodaca"]},
    {"id": 14, "nombre": "Buchakas Cumbres", "alias": ["cumbres", "buchakas cumbres"]},
    {"id": 15, "nombre": "Buchakas Lincoln", "alias": ["lincoln", "buchakas lincoln"]},
    {"id": 16, "nombre": "Buchakas Anahuac", "alias": ["anahuac", "buchakas anahuac"]},
    {"id": 17, "nombre": "Buchakas Valle Oriente", "alias": ["valle oriente", "buchakas valle oriente"]},
]

# =========================================================
# DB
# =========================================================
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cortes_subidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sucursal TEXT NOT NULL,
            fecha TEXT NOT NULL,
            turno TEXT NOT NULL,
            cajera TEXT,
            total_corte REAL,
            observaciones TEXT,
            imagen_1 TEXT,
            imagen_2 TEXT,
            status TEXT NOT NULL DEFAULT 'pendiente',
            created_at TEXT NOT NULL,
            processed_at TEXT
        )
        """
    )

    # por si la tabla ya existía sin la columna
    columnas = [row[1] for row in db.execute("PRAGMA table_info(cortes_subidos)").fetchall()]
    if "total_corte" not in columnas:
        db.execute("ALTER TABLE cortes_subidos ADD COLUMN total_corte REAL")

    db.commit()
    db.close()


ZETUS_SUCURSALES = {
    "Generales Linda Vista": {
        "abr": "GRLV",
        "id_suc_api": 4,
    },
    "Casona Galerías": {
        "abr": "CSG",
        "id_suc_api": 9,  # prueba este
    },
}


# =========================================================
# HELPERS
# =========================================================
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_text(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split())


def mapear_sucursal(nombre_entrada: str) -> Optional[dict]:
    entrada = normalize_text(nombre_entrada)

    if not entrada:
        return None

    for suc in SUCURSALES:
        if entrada == normalize_text(suc["nombre"]):
            return suc

        for alias in suc["alias"]:
            if entrada == normalize_text(alias):
                return suc

    for suc in SUCURSALES:
        for alias in suc["alias"]:
            if normalize_text(alias) in entrada or entrada in normalize_text(alias):
                return suc

    return None


def enrich_corte(row) -> dict:
    corte = dict(row) if not isinstance(row, dict) else row.copy()

    sucursal_original = corte.get("sucursal", "")
    suc_map = mapear_sucursal(sucursal_original)

    corte["sucursal_original"] = sucursal_original
    corte["sucursal_id"] = suc_map["id"] if suc_map else None
    corte["sucursal_nombre"] = suc_map["nombre"] if suc_map else "NO IDENTIFICADA"

    return corte


def save_uploaded_file(file_storage, sucursal: str, fecha: str, turno: str, slot: int) -> Optional[str]:
    try:
        if not file_storage:
            print(f"[img{slot}] file_storage viene vacío", flush=True)
            return None

        if not file_storage.filename:
            print(f"[img{slot}] no se seleccionó archivo", flush=True)
            return None

        print(f"[img{slot}] filename original: {file_storage.filename}", flush=True)

        if not allowed_file(file_storage.filename):
            raise ValueError(f"Archivo no permitido en slot {slot}: {file_storage.filename}")

        safe_name = secure_filename(file_storage.filename)
        if not safe_name:
            safe_name = f"imagen_{slot}.jpg"

        sucursal_safe = secure_filename(sucursal) or "sucursal"
        turno_safe = secure_filename(turno) or "turno"

        unique_name = f"{fecha}_{sucursal_safe}_{turno_safe}_img{slot}_{uuid.uuid4().hex[:10]}_{safe_name}"
        target = UPLOAD_DIR / unique_name

        print(f"[img{slot}] guardando en: {target}", flush=True)

        file_storage.save(target)

        if not target.exists():
            raise RuntimeError(f"[img{slot}] el archivo no se guardó en disco")

        print(f"[img{slot}] archivo guardado correctamente", flush=True)
        return unique_name

    except Exception as e:
        print(f"ERROR guardando imagen {slot}: {repr(e)}", flush=True)
        raise


def fetch_all_cortes():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, sucursal, fecha, turno, cajera, total_corte, observaciones,
               imagen_1, imagen_2, status, created_at, processed_at
        FROM cortes_subidos
        ORDER BY fecha DESC, created_at DESC
        """
    ).fetchall()
    return rows


def fetch_pending_cortes():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, sucursal, fecha, turno, cajera, total_corte, observaciones,
               imagen_1, imagen_2, status, created_at, processed_at
        FROM cortes_subidos
        WHERE status = 'pendiente'
        ORDER BY fecha DESC, created_at DESC
        """
    ).fetchall()

    return [enrich_corte(row) for row in rows]


def agrupar_cortes(cortes: list[dict]) -> list[dict]:
    grupos = defaultdict(list)

    for corte in cortes:
        key = (corte["sucursal_nombre"], corte["fecha"])
        grupos[key].append(corte)

    resultado = []
    for (sucursal, fecha), items in grupos.items():
        resultado.append(
            {
                "sucursal": sucursal,
                "fecha": fecha,
                "cantidad_cortes": len(items),
                "cortes": items,
            }
        )

    return resultado


def enviar_a_auditoria(grupo: dict) -> bool:
    print("Enviando a auditoría:", grupo["sucursal"], grupo["fecha"], flush=True)
    # Aquí luego conectamos el flujo real a Odoo / auditoría
    return True


def marcar_como_procesado(ids: list[int]) -> bool:
    if not ids:
        return True

    db = get_db()
    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ",".join("?" for _ in ids)
    db.execute(
        f"""
        UPDATE cortes_subidos
        SET status = 'procesado', processed_at = ?
        WHERE id IN ({placeholders})
        """,
        [processed_at, *ids],
    )
    db.commit()

    print("Marcados como procesados:", ids, flush=True)
    return True


def update_status(corte_id: int, status: str) -> None:
    db = get_db()
    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status != "pendiente" else None
    db.execute(
        "UPDATE cortes_subidos SET status = ?, processed_at = ? WHERE id = ?",
        (status, processed_at, corte_id),
    )
    db.commit()


# =========================================================
# Extraccion de Zetus
# =========================================================

import requests

ZETUS_API_URL = "https://e1.zetus.app/generales/genos/catalogos/consulta_cuenta"
ZETUS_ID = os.getenv("ZETUS_INTEGRATION_ID")
ZETUS_TOKEN = os.getenv("ZETUS_INTEGRATION_TOKEN")



from itertools import combinations

def resolver_pagos_ticket(pagos: list, total_ticket: float, tolerancia: float = 0.01) -> list:
    if not pagos:
        return []

    montos = [float(p.get("monto", 0) or 0) for p in pagos]
    suma_total = round(sum(montos), 2)
    total_ticket = round(float(total_ticket or 0), 2)

    if abs(suma_total - total_ticket) <= tolerancia:
        return pagos

    n = len(pagos)

    for r in range(1, n + 1):
        for idxs in combinations(range(n), r):
            suma_subset = round(sum(montos[i] for i in idxs), 2)
            if abs(suma_subset - total_ticket) <= tolerancia:
                return [pagos[i] for i in idxs]

    return pagos


def consultar_api_ventas_por_sucursal(fecha: str, id_suc: int) -> dict:
    payload = {
        "aut": {
            "id": ZETUS_ID,
            "tkn": ZETUS_TOKEN,
        },
        "dob": fecha,
        "id_suc": id_suc,
    }

    response = requests.post(
        ZETUS_API_URL,
        json=payload,
        timeout=60,
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok", False):
        raise ValueError(f"API Zetus respondió ok=False: {data}")

    return data

def extraer_pagos_api(data: dict, abr_suc_web: str, id_suc_api: int) -> list:
    contenido = data.get("contenido", [])
    pagos_extraidos = []

    for ticket in contenido:
        total_ticket = float(ticket.get("total", 0) or 0)
        pagos_ticket = ticket.get("pagos", [])

        pagos_validos = resolver_pagos_ticket(pagos_ticket, total_ticket)

        for pago in pagos_validos:
            pagos_extraidos.append({
                "monto_pago": float(pago.get("monto", 0) or 0),
                "propina": float(pago.get("propina", 0) or 0),
            })

    return pagos_extraidos

def fetch_zetus_por_sucursal(nombre_sucursal: str):
    try:
        cfg = ZETUS_SUCURSALES.get(nombre_sucursal)
        if not cfg:
            raise ValueError(f"No hay configuración Zetus para {nombre_sucursal}")

        fecha_zetus = AYER.replace("-", "/")
        data = consultar_api_ventas_por_sucursal(fecha_zetus, cfg["id_suc_api"])
        pagos = extraer_pagos_api(data, cfg["abr"], cfg["id_suc_api"])

        total = round(sum(p["monto_pago"] for p in pagos), 2)

        return [
            {
                "sucursal": nombre_sucursal,
                "fecha": AYER,
                "total": total
            }
        ]
    except Exception as e:
        print(f"ERROR ZETUS {nombre_sucursal}: {e}", flush=True)
        return []


def comparar_cortes(cortes_app, cortes_zetus):
    resultado = []

    for corte in cortes_app:
        encontrado = None

        for z in cortes_zetus:
            if (
                corte["sucursal_nombre"] == z["sucursal"]
                and corte["fecha"] == z["fecha"]
            ):
                encontrado = z
                break

        if encontrado:
            total_corte = float(corte.get("total_corte") or 0)
            total_zetus = float(encontrado.get("total") or 0)
            diferencia = round(total_corte - total_zetus, 2)

            status = "OK" if abs(diferencia) < 1 else "DIFERENCIA"

            resultado.append({
                "id": corte["id"],
                "sucursal": corte["sucursal_nombre"],
                "fecha": corte["fecha"],
                "total_corte": total_corte,
                "total_zetus": total_zetus,
                "diferencia": diferencia,
                "status": status,
            })
        else:
            resultado.append({
                "id": corte["id"],
                "sucursal": corte["sucursal_nombre"],
                "fecha": corte["fecha"],
                "total_corte": corte.get("total_corte"),
                "status": "NO EN ZETUS"
            })

    return resultado

def enriquecer_pendientes_con_comparacion(rows: list[dict]) -> list[dict]:
    resultado = []

    for row in rows:
        item = row.copy()
        nombre_sucursal = item.get("sucursal_nombre")
        fecha = item.get("fecha")

        item["comparacion_status"] = "SIN CONFIG"
        item["total_zetus"] = None
        item["diferencia"] = None

        if not nombre_sucursal or nombre_sucursal == "NO IDENTIFICADA":
            resultado.append(item)
            continue

        if nombre_sucursal not in ZETUS_SUCURSALES:
            resultado.append(item)
            continue

        if fecha != AYER:
            item["comparacion_status"] = "FUERA_DE_FECHA"
            resultado.append(item)
            continue

        cortes_zetus = fetch_zetus_por_sucursal(nombre_sucursal)
        comparacion = comparar_cortes([item], cortes_zetus)

        if comparacion:
            comp = comparacion[0]
            item["comparacion_status"] = comp.get("status")
            item["total_zetus"] = comp.get("total_zetus")
            item["diferencia"] = comp.get("diferencia")

        resultado.append(item)

    return resultado



# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    rows = fetch_all_cortes()
    return render_template_string(INDEX_HTML, rows=rows)


@app.route("/subir", methods=["GET"])
def subir_corte():
    return render_template_string(SUBIR_HTML)

@app.route("/pendientes")
def pendientes():
    rows = fetch_pending_cortes()
    rows = enriquecer_pendientes_con_comparacion(rows)
    return render_template_string(PENDIENTES_HTML, rows=rows)


@app.route("/marcar/<int:corte_id>/<status>", methods=["POST"])
def marcar_status(corte_id: int, status: str):
    valid_status = {"pendiente", "procesado", "revisar", "auditado"}
    if status not in valid_status:
        flash("Status inválido.", "danger")
        return redirect(url_for("index"))

    update_status(corte_id, status)
    flash(f"Corte {corte_id} actualizado a {status}.", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


# =========================================================
# API
# =========================================================
@app.route("/api/cortes_pendientes", methods=["GET"])
def api_cortes_pendientes():
    cortes = fetch_pending_cortes()
    return jsonify(cortes)


@app.route("/api/cortes_agrupados", methods=["GET"])
def api_cortes_agrupados():
    cortes = fetch_pending_cortes()
    agrupados = agrupar_cortes(cortes)
    return jsonify(agrupados)

@app.route("/api/comparar_linda_vista", methods=["GET"])
def api_comparar_linda_vista():
    cortes_app = fetch_pending_cortes()

    cortes_app = [
        c for c in cortes_app
        if c["sucursal_nombre"] == "Generales Linda Vista"
        and c["fecha"] == AYER
    ]

    cortes_zetus = fetch_zetus_por_sucursal("Generales Linda Vista")
    comparacion = comparar_cortes(cortes_app, cortes_zetus)

    return jsonify({
        "ayer": AYER,
        "cortes_filtrados": cortes_app,
        "zetus": cortes_zetus,
        "comparacion": comparacion
    })

@app.route("/api/comparar_casona_galerias", methods=["GET"])
def api_comparar_casona_galerias():
    cortes_app = fetch_pending_cortes()

    cortes_app = [
        c for c in cortes_app
        if c["sucursal_nombre"] == "Casona Galerías"
        and c["fecha"] == AYER
    ]

    cortes_zetus = fetch_zetus_por_sucursal("Casona Galerías")
    comparacion = comparar_cortes(cortes_app, cortes_zetus)

    return jsonify({
        "ayer": AYER,
        "cortes_filtrados": cortes_app,
        "zetus": cortes_zetus,
        "comparacion": comparacion
    })


@app.route("/api/procesar_cortes", methods=["POST"])
def api_procesar_cortes():
    cortes = fetch_pending_cortes()
    agrupados = agrupar_cortes(cortes)

    resultados = []

    for grupo in agrupados:
        enviado = enviar_a_auditoria(grupo)

        ids = [c["id"] for c in grupo["cortes"]]
        if enviado:
            marcar_como_procesado(ids)

        resultados.append(
            {
                "sucursal": grupo["sucursal"],
                "fecha": grupo["fecha"],
                "ids": ids,
                "enviado": enviado,
            }
        )

    return jsonify({"status": "ok", "resultados": resultados})

@app.route("/api/crear_corte", methods=["POST"])
def api_crear_corte():
    try:
        data = request.get_json(force=True)

        sucursal = (data.get("sucursal") or "").strip()
        fecha = (data.get("fecha") or "").strip()
        turno = (data.get("turno") or "").strip()
        cajera = (data.get("cajera") or "").strip()
        observaciones = (data.get("observaciones") or "").strip()
        total_corte = float(data.get("total_corte") or 0)

        if not sucursal or not fecha or not turno:
            return jsonify({"ok": False, "error": "Sucursal, fecha y turno son obligatorios"}), 400

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO cortes_subidos (
                sucursal, fecha, turno, cajera, total_corte, observaciones,
                imagen_1, imagen_2, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pendiente', ?)
            """,
            (
                sucursal,
                fecha,
                turno,
                cajera,
                total_corte,
                observaciones,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        db.commit()

        return jsonify({"ok": True, "corte_id": cursor.lastrowid})

    except Exception as e:
        print("ERROR api_crear_corte:", repr(e), flush=True)
        return jsonify({"ok": False, "error": repr(e)}), 500


@app.route("/api/subir_imagen_corte/<int:corte_id>", methods=["POST"])
def api_subir_imagen_corte(corte_id):
    try:
        db = get_db()
        row = db.execute(
            "SELECT id, sucursal, fecha, turno FROM cortes_subidos WHERE id = ?",
            (corte_id,),
        ).fetchone()

        if not row:
            return jsonify({"ok": False, "error": "Corte no encontrado"}), 404

        img1 = save_uploaded_file(request.files.get("imagen_1"), row["sucursal"], row["fecha"], row["turno"], 1)
        img2 = save_uploaded_file(request.files.get("imagen_2"), row["sucursal"], row["fecha"], row["turno"], 2)

        db.execute(
            """
            UPDATE cortes_subidos
            SET imagen_1 = COALESCE(?, imagen_1),
                imagen_2 = COALESCE(?, imagen_2)
            WHERE id = ?
            """,
            (img1, img2, corte_id),
        )
        db.commit()

        return jsonify({"ok": True, "imagen_1": img1, "imagen_2": img2})

    except Exception as e:
        print("ERROR api_subir_imagen_corte:", repr(e), flush=True)
        return jsonify({"ok": False, "error": repr(e)}), 500

# =========================================================
# HTML
# =========================================================
INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cortes subidos</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 18px; background: #f6f7fb; }
    .topbar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
    a.btn, button.btn { background:#111827; color:white; padding:10px 14px; border-radius:8px; text-decoration:none; border:none; cursor:pointer; }
    .card { background:white; border-radius:12px; padding:14px; margin-bottom:12px; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    .muted { color:#666; font-size:14px; }
    .tag { display:inline-block; padding:4px 8px; border-radius:999px; background:#eef2ff; font-size:12px; }
    .thumbs { display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }
    img.thumb { width:140px; border-radius:10px; border:1px solid #ddd; }
    .flash { padding:10px 12px; border-radius:8px; margin-bottom:12px; }
    .success { background:#dcfce7; }
    .danger { background:#fee2e2; }
    form.inline { display:inline; }
  </style>
</head>
<body>
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
        <div class="flash {{ category }}">{{ message }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <div class="topbar">
    <a class="btn" href="{{ url_for('subir_corte') }}">Subir corte</a>
    <a class="btn" href="{{ url_for('pendientes') }}">Ver pendientes</a>
  </div>

  <h2>Cortes subidos</h2>

  {% for row in rows %}
    <div class="card">
      <div><strong>#{{ row['id'] }}</strong> · {{ row['sucursal'] }} · {{ row['fecha'] }} · Turno {{ row['turno'] }}</div>
      <div class="muted">Cajera: {{ row['cajera'] or '-' }} | Estado: <span class="tag">{{ row['status'] }}</span></div>
      <div class="muted">Total corte: {{ row['total_corte'] if row['total_corte'] is not none else '-' }}</div>
      <div class="muted">Obs: {{ row['observaciones'] or '-' }}</div>
      <div class="muted">Subido: {{ row['created_at'] }}{% if row['processed_at'] %} | Procesado: {{ row['processed_at'] }}{% endif %}</div>

      <div class="thumbs">
        {% if row['imagen_1'] %}<a href="{{ url_for('uploaded_file', filename=row['imagen_1']) }}" target="_blank"><img class="thumb" src="{{ url_for('uploaded_file', filename=row['imagen_1']) }}"></a>{% endif %}
        {% if row['imagen_2'] %}<a href="{{ url_for('uploaded_file', filename=row['imagen_2']) }}" target="_blank"><img class="thumb" src="{{ url_for('uploaded_file', filename=row['imagen_2']) }}"></a>{% endif %}
      </div>
    </div>
  {% else %}
    <div class="card">No hay cortes todavía.</div>
  {% endfor %}
</body>
</html>
"""

SUBIR_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Subir corte</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 18px; background: #f6f7fb; }
    .wrap { max-width: 760px; margin: 0 auto; }
    .card { background:white; border-radius:12px; padding:18px; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    label { display:block; margin-top:12px; margin-bottom:6px; font-weight:bold; }
    input, textarea, select { width:100%; padding:10px; border:1px solid #ddd; border-radius:8px; box-sizing:border-box; }
    button { margin-top:16px; background:#111827; color:#fff; border:none; padding:10px 14px; border-radius:8px; cursor:pointer; }
    button:disabled { opacity:.6; cursor:not-allowed; }
    a { text-decoration:none; }
    .msg { margin-top:12px; padding:10px; border-radius:8px; display:none; }
    .ok { background:#dcfce7; color:#166534; }
    .err { background:#fee2e2; color:#991b1b; }
  </style>
</head>
<body>
  <div class="wrap">
    <div style="margin-bottom:14px;">
      <a href="{{ url_for('index') }}">← Volver</a>
    </div>

    <div class="card">
      <h2>Subir corte</h2>

      <form id="formCorte">
        <label>Sucursal</label>
        <input type="text" name="sucursal" placeholder="Ej. Linda Vista" required>

        <label>Fecha</label>
        <input type="date" name="fecha" required>

        <label>Turno</label>
        <select name="turno" required>
          <option value="">Selecciona...</option>
          <option>Mañana</option>
          <option>Tarde</option>
          <option>Noche</option>
        </select>

        <label>Cajera</label>
        <input type="text" name="cajera" placeholder="Nombre de cajera">

        <label>Total del corte</label>
        <input type="number" step="0.01" name="total_corte" placeholder="Ej. 65551.00" required>

        <label>Observaciones</label>
        <textarea name="observaciones" rows="3" placeholder="Comentarios..."></textarea>

        <label>Imagen 1</label>
        <input type="file" name="imagen_1" accept="image/*">

        <label>Imagen 2</label>
        <input type="file" name="imagen_2" accept="image/*">

        <button id="btnGuardar" type="submit">Guardar corte</button>
      </form>

      <div id="msg" class="msg"></div>
    </div>
  </div>

<script>
async function comprimirImagen(file, maxWidth = 1200, quality = 0.7) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const reader = new FileReader();

    reader.onload = e => {
      img.onload = () => {
        const scale = Math.min(1, maxWidth / img.width);
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);

        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(
          blob => {
            if (!blob) return reject(new Error("No se pudo comprimir imagen"));
            resolve(new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), {
              type: "image/jpeg"
            }));
          },
          "image/jpeg",
          quality
        );
      };

      img.onerror = reject;
      img.src = e.target.result;
    };

    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

document.getElementById("formCorte").addEventListener("submit", async function(e) {
  e.preventDefault();

  const form = e.target;
  const btn = document.getElementById("btnGuardar");
  const msg = document.getElementById("msg");

  btn.disabled = true;
  btn.textContent = "Guardando...";
  msg.style.display = "none";

  try {
    const payload = {
      sucursal: form.sucursal.value,
      fecha: form.fecha.value,
      turno: form.turno.value,
      cajera: form.cajera.value,
      total_corte: form.total_corte.value,
      observaciones: form.observaciones.value
    };

    const resCorte = await fetch("/api/crear_corte", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });

    const dataCorte = await resCorte.json();

    if (!dataCorte.ok) {
      throw new Error(dataCorte.error || "No se pudo crear el corte");
    }

    const corteId = dataCorte.corte_id;

    const imgData = new FormData();
    if (form.imagen_1.files.length > 0) {
      const img1 = await comprimirImagen(form.imagen_1.files[0]);
      imgData.append("imagen_1", img1);
    }

    if (form.imagen_2.files.length > 0) {
      const img2 = await comprimirImagen(form.imagen_2.files[0]);
      imgData.append("imagen_2", img2);
    }

    if (imgData.has("imagen_1") || imgData.has("imagen_2")) {
      btn.textContent = "Subiendo imágenes...";

      const resImg = await fetch(`/api/subir_imagen_corte/${corteId}`, {
        method: "POST",
        body: imgData
      });

      const dataImg = await resImg.json();

      if (!dataImg.ok) {
        throw new Error(dataImg.error || "El corte se guardó, pero falló la imagen");
      }
    }

    msg.className = "msg ok";
    msg.textContent = "Corte guardado correctamente.";
    msg.style.display = "block";

    setTimeout(() => {
      window.location.href = "/";
    }, 800);

  } catch (err) {
    msg.className = "msg err";
    msg.textContent = err.message;
    msg.style.display = "block";
    btn.disabled = false;
    btn.textContent = "Guardar corte";
  }
});
</script>
</body>
</html>
"""

PENDIENTES_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cortes pendientes</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 18px; background: #f6f7fb; }
    .topbar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
    a.btn, button.btn { background:#111827; color:white; padding:10px 14px; border-radius:8px; text-decoration:none; border:none; cursor:pointer; }
    .card { background:white; border-radius:12px; padding:14px; margin-bottom:12px; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    .muted { color:#666; font-size:14px; margin-top:4px; }
    .tag { display:inline-block; padding:4px 8px; border-radius:999px; background:#eef2ff; font-size:12px; }
    .flash { padding:10px 12px; border-radius:8px; margin-bottom:12px; }
    .success { background:#dcfce7; }
    .danger { background:#fee2e2; }
    form.inline { display:inline; }

    .status-ok { background:#dcfce7; color:#166534; }
    .status-diff { background:#fee2e2; color:#991b1b; }
    .status-miss { background:#e5e7eb; color:#374151; }
  </style>
</head>
<body>
  <div class="topbar">
    <a class="btn" href="{{ url_for('index') }}">Todos</a>
    <a class="btn" href="{{ url_for('subir_corte') }}">Subir corte</a>
  </div>

  <h2>Cortes pendientes</h2>

  {% for row in rows %}
    <div class="card">
      <div><strong>#{{ row['id'] }}</strong> · {{ row['sucursal_nombre'] }} · {{ row['fecha'] }} · Turno {{ row['turno'] }}</div>

      <div class="muted">Sucursal original: {{ row['sucursal_original'] }}</div>
      <div class="muted">Cajera: {{ row['cajera'] or '-' }} | Estado: <span class="tag">{{ row['status'] }}</span></div>
      <div class="muted">Total corte: {{ row['total_corte'] if row['total_corte'] is not none else '-' }}</div>
      <div class="muted">Total Zetus: {{ row['total_zetus'] if row['total_zetus'] is not none else '-' }}</div>
      <div class="muted">Diferencia: {{ row['diferencia'] if row['diferencia'] is not none else '-' }}</div>

      <div class="muted">
        {% set cls = 'status-miss' %}
        {% if row['comparacion_status'] == 'OK' %}
          {% set cls = 'status-ok' %}
        {% elif row['comparacion_status'] == 'DIFERENCIA' %}
          {% set cls = 'status-diff' %}
        {% endif %}
        Comparación: <span class="tag {{ cls }}">{{ row['comparacion_status'] }}</span>
      </div>

      <div class="muted">Obs: {{ row['observaciones'] or '-' }}</div>

      <div style="margin-top:10px;">
        <form class="inline" method="post" action="{{ url_for('marcar_status', corte_id=row['id'], status='revisar') }}">
          <button class="btn" type="submit">Marcar revisar</button>
        </form>
        <form class="inline" method="post" action="{{ url_for('marcar_status', corte_id=row['id'], status='auditado') }}">
          <button class="btn" type="submit">Marcar auditado</button>
        </form>
        <form class="inline" method="post" action="{{ url_for('marcar_status', corte_id=row['id'], status='procesado') }}">
          <button class="btn" type="submit">Marcar procesado</button>
        </form>
      </div>
    </div>
  {% else %}
    <div class="card">No hay pendientes.</div>
  {% endfor %}
</body>
</html>
"""

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)