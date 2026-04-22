from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    flash,
    g,
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
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB por request

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
            observaciones TEXT,
            imagen_1 TEXT,
            imagen_2 TEXT,
            status TEXT NOT NULL DEFAULT 'pendiente',
            created_at TEXT NOT NULL,
            processed_at TEXT
        )
        """
    )
    db.commit()
    db.close()


# =========================================================
# HELPERS
# =========================================================
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_file(file_storage, sucursal: str, fecha: str, turno: str, slot: int) -> Optional[str]:
    if not file_storage or not file_storage.filename:
        print(f"No viene archivo en slot {slot}", flush=True)
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError(f"Archivo no permitido: {file_storage.filename}")

    safe_name = secure_filename(file_storage.filename)
    unique_name = f"{fecha}_{sucursal}_{turno}_img{slot}_{uuid.uuid4().hex[:10]}_{safe_name}"
    target = UPLOAD_DIR / unique_name

    print(f"Guardando archivo slot {slot}: {file_storage.filename}", flush=True)
    print(f"Destino: {target}", flush=True)

    file_storage.save(target)

    print(f"Archivo guardado slot {slot}", flush=True)
    return unique_name


def fetch_all_cortes():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, sucursal, fecha, turno, cajera, observaciones,
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
        SELECT id, sucursal, fecha, turno, cajera, observaciones,
               imagen_1, imagen_2, status, created_at, processed_at
        FROM cortes_subidos
        WHERE status = 'pendiente'
        ORDER BY fecha ASC, created_at ASC
        """
    ).fetchall()
    return rows


def update_status(corte_id: int, status: str) -> None:
    db = get_db()
    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status != "pendiente" else None
    db.execute(
        "UPDATE cortes_subidos SET status = ?, processed_at = ? WHERE id = ?",
        (status, processed_at, corte_id),
    )
    db.commit()


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    rows = fetch_all_cortes()
    return render_template_string(INDEX_HTML, rows=rows)

@app.route("/subir", methods=["GET", "POST"])
def subir_corte():
    if request.method == "POST":
        print("POST /subir recibido", flush=True)

        sucursal = (request.form.get("sucursal") or "").strip().upper()
        fecha = (request.form.get("fecha") or "").strip()
        turno = (request.form.get("turno") or "").strip()
        cajera = (request.form.get("cajera") or "").strip()
        observaciones = (request.form.get("observaciones") or "").strip()

        print("Datos leídos:", sucursal, fecha, turno, cajera, flush=True)

        if not sucursal or not fecha or not turno:
            print("Faltan campos obligatorios", flush=True)
            flash("Sucursal, fecha y turno son obligatorios.", "danger")
            return redirect(url_for("subir_corte"))

        try:
            print("Antes de guardar img1", flush=True)
            img1 = save_uploaded_file(request.files.get("imagen_1"), sucursal, fecha, turno, 1)
            print("Después de guardar img1:", img1, flush=True)

            print("Antes de guardar img2", flush=True)
            img2 = save_uploaded_file(request.files.get("imagen_2"), sucursal, fecha, turno, 2)
            print("Después de guardar img2:", img2, flush=True)
        except ValueError as e:
            print("Error de validación archivo:", str(e), flush=True)
            flash(str(e), "danger")
            return redirect(url_for("subir_corte"))
        except Exception as e:
            print("Error guardando archivos:", repr(e), flush=True)
            raise

        if not img1 and not img2:
            print("No se subió ninguna imagen", flush=True)
            flash("Debes subir al menos una imagen.", "danger")
            return redirect(url_for("subir_corte"))

        print("Antes de get_db()", flush=True)
        db = get_db()
        print("Después de get_db()", flush=True)

        print("Antes de INSERT", flush=True)
        db.execute(
            """
            INSERT INTO cortes_subidos (
                sucursal, fecha, turno, cajera, observaciones,
                imagen_1, imagen_2, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)
            """,
            (
                sucursal,
                fecha,
                turno,
                cajera,
                observaciones,
                img1,
                img2,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        print("Después de INSERT", flush=True)

        print("Antes de COMMIT", flush=True)
        db.commit()
        print("Después de COMMIT", flush=True)

        flash("Corte guardado correctamente.", "success")
        return redirect(url_for("index"))

    return render_template_string(SUBIR_HTML)


@app.route("/pendientes")
def pendientes():
    rows = fetch_pending_cortes()
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
      <div class="muted">Obs: {{ row['observaciones'] or '-' }}</div>
      <div class="muted">Subido: {{ row['created_at'] }}{% if row['processed_at'] %} | Procesado: {{ row['processed_at'] }}{% endif %}</div>

      <div class="thumbs">
        {% if row['imagen_1'] %}<a href="{{ url_for('uploaded_file', filename=row['imagen_1']) }}" target="_blank"><img class="thumb" src="{{ url_for('uploaded_file', filename=row['imagen_1']) }}"></a>{% endif %}
        {% if row['imagen_2'] %}<a href="{{ url_for('uploaded_file', filename=row['imagen_2']) }}" target="_blank"><img class="thumb" src="{{ url_for('uploaded_file', filename=row['imagen_2']) }}"></a>{% endif %}
      </div>

      <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
        <form class="inline" method="post" action="{{ url_for('marcar_status', corte_id=row['id'], status='procesado') }}"><button class="btn">Marcar procesado</button></form>
        <form class="inline" method="post" action="{{ url_for('marcar_status', corte_id=row['id'], status='revisar') }}"><button class="btn">Marcar revisar</button></form>
        <form class="inline" method="post" action="{{ url_for('marcar_status', corte_id=row['id'], status='auditado') }}"><button class="btn">Marcar auditado</button></form>
      </div>
    </div>
  {% else %}
    <div class="card">No hay cortes registrados.</div>
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
    .box { background:white; padding:16px; border-radius:12px; max-width:680px; margin:auto; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    label { display:block; margin-top:12px; font-weight:bold; }
    input, textarea, select { width:100%; padding:10px; margin-top:6px; border-radius:8px; border:1px solid #ccc; box-sizing:border-box; }
    button { margin-top:16px; background:#111827; color:white; border:none; padding:12px 14px; border-radius:8px; width:100%; }
    a { text-decoration:none; }
  </style>
</head>
<body>
  <div class="box">
    <h2>Subir corte</h2>
    <form method="post" enctype="multipart/form-data">
      <label>Sucursal</label>
      <input type="text" name="sucursal" placeholder="Ej. GRLV" required>

      <label>Fecha</label>
      <input type="date" name="fecha" required>

      <label>Turno</label>
      <select name="turno" required>
        <option value="">Selecciona...</option>
        <option value="1">1</option>
        <option value="2">2</option>
        <option value="3">3</option>
      </select>

      <label>Cajera</label>
      <input type="text" name="cajera" placeholder="Nombre de la cajera">

      <label>Observaciones</label>
      <textarea name="observaciones" rows="3" placeholder="Opcional"></textarea>

      <label>Imagen 1</label>
      <input type="file" name="imagen_1" accept="image/*">

      <label>Imagen 2 (opcional)</label>
      <input type="file" name="imagen_2" accept="image/*">

      <button type="submit">Guardar corte</button>
    </form>
    <p style="margin-top:14px;"><a href="{{ url_for('index') }}">Volver</a></p>
  </div>
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
    .card { background:white; border-radius:12px; padding:14px; margin-bottom:12px; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    a.btn { background:#111827; color:white; padding:10px 14px; border-radius:8px; text-decoration:none; }
    img.thumb { width:140px; border-radius:10px; border:1px solid #ddd; }
  </style>
</head>
<body>
  <p><a class="btn" href="{{ url_for('index') }}">Volver</a></p>
  <h2>Cortes pendientes</h2>
  {% for row in rows %}
    <div class="card">
      <div><strong>#{{ row['id'] }}</strong> · {{ row['sucursal'] }} · {{ row['fecha'] }} · Turno {{ row['turno'] }}</div>
      <div>Cajera: {{ row['cajera'] or '-' }}</div>
      <div>Obs: {{ row['observaciones'] or '-' }}</div>
      <div style="display:flex; gap:10px; margin-top:10px; flex-wrap:wrap;">
        {% if row['imagen_1'] %}<a href="{{ url_for('uploaded_file', filename=row['imagen_1']) }}" target="_blank"><img class="thumb" src="{{ url_for('uploaded_file', filename=row['imagen_1']) }}"></a>{% endif %}
        {% if row['imagen_2'] %}<a href="{{ url_for('uploaded_file', filename=row['imagen_2']) }}" target="_blank"><img class="thumb" src="{{ url_for('uploaded_file', filename=row['imagen_2']) }}"></a>{% endif %}
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
