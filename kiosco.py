import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk


# ---------- Base de datos (kiosco.db queda al lado del programa) ----------
BASE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
con = sqlite3.connect(os.path.join(BASE, "kiosco.db"))
con.executescript("""
CREATE TABLE IF NOT EXISTS productos(
  codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL, precio REAL NOT NULL,
  promo TEXT DEFAULT 'ninguna', valor REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ventas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, total REAL,
  metodo TEXT, pago REAL, vuelto REAL);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, venta_id INTEGER, codigo TEXT, nombre TEXT,
  cantidad INTEGER, precio REAL, descuento REAL, subtotal REAL);
CREATE TABLE IF NOT EXISTS cierres(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, desde TEXT, inicial REAL,
  efectivo REAL, retiros REAL, esperado REAL, contado REAL, diferencia REAL, ganancia REAL);
CREATE TABLE IF NOT EXISTS config(clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS cola_ingresos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, codigo TEXT, cantidad REAL, nota TEXT, subida INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cola_borrados(
  id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, subida INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_items_codigo ON items(codigo);
CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas(fecha);
""")


def addcol(tabla, col, ddl):
    if col not in [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]:
        con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {ddl}")


addcol("productos", "costo", "REAL DEFAULT 0")
addcol("productos", "stock", "REAL DEFAULT 0")
addcol("productos", "stock_min", "REAL DEFAULT 0")
addcol("items", "costo", "REAL DEFAULT 0")
addcol("ventas", "anulada", "INTEGER DEFAULT 0")
addcol("productos", "creado", "TEXT")
addcol("productos", "sucio", "INTEGER DEFAULT 0")
addcol("ventas", "subida", "INTEGER DEFAULT 0")
addcol("ventas", "anul_subida", "INTEGER DEFAULT 0")
addcol("productos", "promo_n", "INTEGER DEFAULT 2")
addcol("productos", "en_hoja", "INTEGER DEFAULT 0")
con.execute("UPDATE productos SET creado=? WHERE creado IS NULL", (datetime.now().strftime("%Y-%m-%d"),))
con.commit()

# ---------- Configuración guardada en la base ----------
DEFAULTS = {"nombre": "MI KIOSCO", "t_nombre": "", "t_l2": "", "t_l3": "", "t_num": "Ticket Nro",
            "t_total": "TOTAL", "t_pie": "Gracias por su compra!", "t_pie2": "", "ancho": "40", "auto_ticket": "0",
            "tema": "claro", "dias_sin_venta": "21", "pin_admin": "", "pin_empleado": "", "pin_rec": "",
            "sheets_on": "0", "sheets_id": "", "caja_id": "C1", "sheets_seg": "60", "sheets_preparada": "0"}
_cfg = dict(con.execute("SELECT clave,valor FROM config"))


def cfg(k):
    return _cfg.get(k, DEFAULTS[k])


def set_cfg(k, v):
    _cfg[k] = v
    con.execute("INSERT INTO config(clave,valor) VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (k, v))
    con.commit()


def ancho():
    try:
        return int(cfg("ancho"))
    except ValueError:
        return 40


def nombre():
    return cfg("nombre").strip() or "MI KIOSCO"


def hpin(p):
    return hashlib.sha256(("kiosco|" + p).encode("utf-8")).hexdigest()


def rol_de(pin):
    h = hpin(pin)
    if cfg("pin_admin") and h == cfg("pin_admin"):
        return "admin"
    if cfg("pin_empleado") and h == cfg("pin_empleado"):
        return "empleado"
    return None


PROMOS = {"ninguna": "Sin promo", "2da50": "Segunda unidad al 50%", "2x1": "2x1",
          "3x2": "3x2", "pct": "Descuento %", "nesima": "Unidad N con % de descuento",
          "pack": "Desde N unidades, % de descuento"}
AYUDA_PROMO = {
    "ninguna": "Sin promoción.",
    "2da50": "La segunda unidad sale al 50%.",
    "2x1": "Llevando 2 pagás 1.",
    "3x2": "Llevando 3 pagás 2.",
    "pct": "Descuento fijo: completá el % de descuento.",
    "nesima": "Completá N y el %. La unidad N lleva ese descuento. Ej: N=2 y 30% = la segunda con 30% OFF; "
              "N=3 y 100% = la tercera gratis.",
    "pack": "Completá N y el %. Desde N unidades, TODAS llevan ese descuento. Ej: N=3 y 15%.",
}
REV = {v: k for k, v in PROMOS.items()}
METODOS = ["Efectivo", "Transferencia", "Débito", "Crédito"]


def num(s):
    """'1.500,50' o '1500,50' o '1500' -> float"""
    try:
        return float(str(s).strip().replace("$", "").replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def fmt(n):
    n = round(n, 2)
    txt = f"{n:,.0f}" if n == int(n) else f"{n:,.2f}"
    return "$" + txt.replace(",", "X").replace(".", ",").replace("X", ".")


def ordinal(n):
    return {2: "2da", 3: "3ra", 4: "4ta", 5: "5ta", 6: "6ta"}.get(n, f"{n}ª")


def descuento(precio, cant, promo, valor, n=2):
    """Plata que se descuenta por la promo del producto (valor = % de descuento, n = unidad / cantidad)."""
    n = max(2, int(n or 2))
    valor = max(0, min(100, valor or 0))
    if promo == "2da50":
        return (cant // 2) * precio * 0.5
    if promo == "2x1":
        return (cant // 2) * precio
    if promo == "3x2":
        return (cant // 3) * precio
    if promo == "pct":
        return cant * precio * valor / 100
    if promo == "nesima":              # cada N unidades, la N-ésima lleva el % de descuento
        return (cant // n) * precio * valor / 100
    if promo == "pack":                # desde N unidades, todas llevan el % de descuento
        return cant * precio * valor / 100 if cant >= n else 0
    return 0


def etiqueta_promo(promo, valor, n=2):
    n = max(2, int(n or 2))
    return {"2da50": "2da al 50% OFF", "2x1": "2x1", "3x2": "3x2", "pct": f"{valor:g}% OFF",
            "nesima": f"{ordinal(n)} al {valor:g}% OFF",
            "pack": f"Desde {n}u: {valor:g}% OFF"}.get(promo, "")


def info_promo(promo, valor, n, cant):
    """(texto para la columna Promo, estado). estado: '' sin promo | 'pend' (se puede ofrecer más) | 'ok' (aplicada)"""
    if not promo or promo == "ninguna":
        return "", ""
    n = max(2, int(n or 2))
    minimo = {"2da50": 2, "2x1": 2, "3x2": 3, "pct": 1, "nesima": n, "pack": n}.get(promo, 1)
    et = etiqueta_promo(promo, valor, n)
    return (f"✓ {et}", "ok") if cant >= minimo else (f"OFERTA: {et}", "pend")


def bajos():
    return con.execute("SELECT codigo,nombre,stock,stock_min FROM productos "
                       "WHERE stock_min>0 AND stock<=stock_min ORDER BY nombre").fetchall()


def sin_movimiento():
    """Productos que no se venden hace X días (o que se cargaron hace X días y nunca se vendieron)."""
    try:
        dias = int(cfg("dias_sin_venta"))
    except ValueError:
        dias = 21
    if dias <= 0:
        return []
    hoy = datetime.now().date()
    out = []
    for cod, nom, stock, creado, ult in con.execute(
            "SELECT p.codigo,p.nombre,p.stock,p.creado,"
            "(SELECT MAX(substr(v.fecha,1,10)) FROM items i JOIN ventas v ON v.id=i.venta_id "
            " WHERE i.codigo=p.codigo AND v.anulada=0) FROM productos p"):
        ref = ult or creado
        try:
            d = (hoy - datetime.strptime(ref, "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            continue
        if d >= dias:
            out.append((cod, nom, stock, d, ult))
    return sorted(out, key=lambda x: -x[3])


# ---------- Sincronización con Google Sheets ----------
# Cómo funciona el stock: en la planilla, la columna H de cada producto es una FÓRMULA:
#     stock = suma de la hoja "Ingresos"  -  suma de la hoja "Movimientos" (ventas)
# Cada caja solo AGREGA filas (nunca pisa números), así varias cajas pueden vender a la vez sin perder nada.
RUTA_JSON = os.path.join(BASE, "credenciales.json")
H_PROD = ["Código", "Nombre", "Precio", "Costo", "Promo", "% Promo", "Stock mínimo", "Stock actual (no tocar)",
          "N (para promos)"]
H_ING = ["Fecha", "Código", "Cantidad", "Nota", "Caja"]
H_MOV = ["ID", "Fecha", "Código", "Cantidad", "Producto", "Precio", "Descuento", "Subtotal", "Medio de pago",
         "Caja", "Venta N°", "Tipo"]
_SEP = [","]   # separador de argumentos que acepta tu planilla ("," o ";"): se detecta solo


def f_stock(n):
    s = _SEP[0]
    return f"=SUMIF(Ingresos!B:B{s}A{n}{s}Ingresos!C:C)-SUMIF(Movimientos!C:C{s}A{n}{s}Movimientos!D:D)"


def norm_cod(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def a_num(v, defecto=0.0):
    if isinstance(v, (int, float)):
        return float(v)
    return num(v) if str(v).strip() else defecto


def clave_promo(t):
    t = str(t).strip().lower()
    for k, v in PROMOS.items():
        if t in (k.lower(), v.lower()):
            return k
    return "ninguna"


def extraer_id(txt):
    m = re.search(r"/d/([A-Za-z0-9_-]+)", txt or "")
    return m.group(1) if m else (txt or "").strip()


def abrir_db():
    d = sqlite3.connect(os.path.join(BASE, "kiosco.db"), timeout=20)
    d.execute("PRAGMA busy_timeout=20000")
    return d


def mail_del_json():
    try:
        with open(RUTA_JSON, encoding="utf-8") as f:
            return json.load(f).get("client_email")
    except (OSError, ValueError):
        return None


def registrar_ingreso(codigo, cantidad, nota):
    """Anota una entrada/salida de stock para subirla a la planilla (hoja Ingresos)."""
    if cantidad:
        con.execute("INSERT INTO cola_ingresos(fecha,codigo,cantidad,nota) VALUES(?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), codigo, cantidad, nota))
        con.commit()


def explicar_error(e):
    t, n = str(e), type(e).__name__
    if isinstance(e, FileNotFoundError):
        return "No encuentro el archivo credenciales.json. Elegilo con el botón de abajo."
    if "No module named" in t:
        return "Falta la librería gspread. Generá el .exe de nuevo con el crear_exe.bat actualizado."
    if n == "SpreadsheetNotFound" or "404" in t:
        return ("No encuentro la planilla. Revisá el link y que esté compartida como Editor con el mail "
                "de la cuenta de servicio.")
    if "SERVICE_DISABLED" in t or "has not been used" in t or "is disabled" in t:
        return "Falta habilitar Google Sheets API en tu proyecto de Google Cloud."
    if "PERMISSION_DENIED" in t or "403" in t:
        return "Google no deja entrar: compartí la planilla con el mail de la cuenta de servicio como Editor."
    if "invalid_grant" in t or "Invalid JWT" in t:
        return "El archivo de claves no sirve, o la fecha/hora de esta PC está mal."
    if "RESOURCE_EXHAUSTED" in t or "429" in t:
        return "Google pidió ir más despacio. Se reintenta solo."
    if any(x in n for x in ("Connection", "Timeout")) or "Max retries" in t or "getaddrinfo" in t:
        return "Sin conexión a internet. Se sube todo cuando vuelva."
    return "Error: " + t[:200]


class HojaGoogle:
    """Habla con la planilla real usando una cuenta de servicio (librería gspread)."""

    def __init__(self, archivo, sheet_id):
        import gspread
        if not os.path.exists(archivo):
            raise FileNotFoundError(archivo)
        self.sh = gspread.service_account(filename=archivo).open_by_key(sheet_id)

    def titulo(self):
        return self.sh.title

    @staticmethod
    def _r(hoja, rango):
        return f"'{hoja}'!{rango}"

    def _leer(self, hoja, rango):
        d = self.sh.values_get(self._r(hoja, rango), params={"valueRenderOption": "UNFORMATTED_VALUE"})
        return d.get("values", [])

    def leer_productos(self):
        return self._leer("Productos", "A1:I")

    def escribir_productos(self, updates, nuevos):
        """Escribe A:G (datos) e I (N de la promo). La columna H (stock) es fórmula y no se toca.
        Devuelve los números de fila de los productos nuevos."""
        data, nuevas = [], []
        filas = list(updates)
        if nuevos:
            col = self._leer("Productos", "A1:A")
            ultima = max([i + 1 for i, f in enumerate(col) if f and norm_cod(f[0])] or [1])
            for k, v in enumerate(nuevos):
                filas.append((ultima + 1 + k, v))
                nuevas.append(ultima + 1 + k)
        for n, v in filas:
            data.append({"range": self._r("Productos", f"A{n}:G{n}"), "values": [v[:7]]})
            data.append({"range": self._r("Productos", f"I{n}"), "values": [[v[7]]]})
        if data:
            self.sh.values_batch_update({"valueInputOption": "RAW", "data": data})
        return nuevas

    def escribir_formulas(self, filas):
        """Pone la fórmula de stock en las filas indicadas. Detecta solo si tu planilla usa , o ; """
        if not filas:
            return
        for _ in range(2):
            data = [{"range": self._r("Productos", f"H{n}"), "values": [[f_stock(n)]]} for n in filas]
            self.sh.values_batch_update({"valueInputOption": "USER_ENTERED", "data": data})
            m = self._leer("Productos", f"H{filas[0]}:H{filas[0]}")
            v = m[0][0] if m and m[0] else ""
            if not (isinstance(v, str) and v.startswith("#")):
                return
            _SEP[0] = ";" if _SEP[0] == "," else ","
        raise RuntimeError("Google rechazó la fórmula de stock. Probá el botón Reparar planilla.")

    def limpiar_stock(self):
        self.sh.values_clear(self._r("Productos", "H2:H"))

    def borrar_productos(self, codigos):
        if not codigos:
            return
        col = self._leer("Productos", "A1:A")
        filas = sorted({i + 1 for i, f in enumerate(col) if i > 0 and f and norm_cod(f[0]) in codigos}, reverse=True)
        if filas:
            sid = self.sh.worksheet("Productos").id
            self.sh.batch_update({"requests": [{"deleteDimension": {"range": {
                "sheetId": sid, "dimension": "ROWS", "startIndex": n - 1, "endIndex": n}}} for n in filas]})

    def _agregar(self, hoja, filas):
        if filas:
            self.sh.values_append(self._r(hoja, "A1"),
                                  params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                                  body={"values": filas})

    def agregar_ingresos(self, filas):
        self._agregar("Ingresos", filas)

    def agregar_movimientos(self, filas):
        self._agregar("Movimientos", filas)

    def preparar(self):
        """Crea las hojas Productos / Ingresos / Movimientos (si faltan), con títulos y formato."""
        ex = {w.title: w for w in self.sh.worksheets()}
        if "Productos" not in ex and len(ex) == 1:
            w0 = list(ex.values())[0]
            if not any(any(c for c in fila) for fila in w0.get_all_values()):
                w0.update_title("Productos")      # planilla nueva y vacía: reutiliza la primera hoja
                ex = {"Productos": w0}
        for nombre, cab, filas in (("Productos", H_PROD, 2000), ("Ingresos", H_ING, 2000), ("Movimientos", H_MOV, 5000)):
            if nombre not in ex:
                ex[nombre] = self.sh.add_worksheet(title=nombre, rows=filas, cols=max(len(cab), 9))
            self.sh.values_update(self._r(nombre, "A1"), params={"valueInputOption": "RAW"}, body={"values": [cab]})
        try:   # formato lindo (si falla, no pasa nada)
            reqs = []
            for nombre, col in (("Productos", 0), ("Ingresos", 1), ("Movimientos", 2)):
                sid = ex[nombre].id
                reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": col,
                                                      "endColumnIndex": col + 1},
                                            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
                                            "fields": "userEnteredFormat.numberFormat"}})
                reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                     "backgroundColor": {"red": 0.88, "green": 0.91, "blue": 0.99}}},
                                            "fields": "userEnteredFormat(textFormat,backgroundColor)"}})
                reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid,
                             "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}})
            reqs.append({"setDataValidation": {
                "range": {"sheetId": ex["Productos"].id, "startRowIndex": 1, "endRowIndex": 5000,
                          "startColumnIndex": 4, "endColumnIndex": 5},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                                       "values": [{"userEnteredValue": v} for v in PROMOS.values()]},
                         "showCustomUi": True, "strict": False}}})
            self.sh.batch_update({"requests": reqs})
        except Exception:
            pass


def codigos_con_fila(filas):
    return [i + 1 for i, f in enumerate(filas) if i > 0 and f and norm_cod(f[0])]


def sincronizar(db, hoja, caja):
    """Una pasada completa. Si algo falla (sin internet, etc.) levanta la excepción y NADA se marca como subido."""
    r = {"ventas": 0, "ingresos": 0, "subidos": 0, "bajados": 0, "ignoradas": 0, "borrados": 0,
         "borrados_hoja": 0, "bloqueado": 0}

    # 1) ventas (y anulaciones) -> hoja Movimientos
    mov, marcas = [], []
    pend = db.execute("SELECT id,fecha,metodo,anulada,subida,anul_subida FROM ventas "
                      "WHERE subida=0 OR (subida=1 AND anulada=1 AND anul_subida=0)").fetchall()
    for vid, fecha, metodo, anulada, subida, anul_sub in pend:
        if subida == 0 and anulada == 1:       # se anuló antes de subirse: no hace falta subir nada
            marcas.append(("UPDATE ventas SET subida=2 WHERE id=?", vid))
            continue
        items = db.execute("SELECT id,codigo,nombre,cantidad,precio,descuento,subtotal FROM items WHERE venta_id=?",
                           (vid,)).fetchall()
        if subida == 0:
            for iid, cod, nom, cant, pre, desc, sub in items:
                mov.append([f"{caja}-{vid}-{iid}", fecha, cod, cant, nom, pre, desc, sub, metodo, caja, vid, "VENTA"])
            marcas.append(("UPDATE ventas SET subida=1 WHERE id=?", vid))
            r["ventas"] += 1
        else:                                  # venta ya subida y luego anulada: filas en negativo
            for iid, cod, nom, cant, pre, desc, sub in items:
                mov.append([f"{caja}-{vid}-{iid}-A", fecha, cod, -cant, nom, pre, -desc, -sub, metodo, caja, vid,
                            "ANULACION"])
            marcas.append(("UPDATE ventas SET anul_subida=1 WHERE id=?", vid))
    if mov:
        hoja.agregar_movimientos(mov)
    for sql, vid in marcas:
        db.execute(sql, (vid,))
    db.commit()

    # 2) ingresos de stock hechos en el programa -> hoja Ingresos
    ing = db.execute("SELECT id,fecha,codigo,cantidad,nota FROM cola_ingresos WHERE subida=0 ORDER BY id").fetchall()
    if ing:
        hoja.agregar_ingresos([[f, c, q, n, caja] for _, f, c, q, n in ing])
        db.executemany("UPDATE cola_ingresos SET subida=1 WHERE id=?", [(i[0],) for i in ing])
        db.commit()
        r["ingresos"] = len(ing)

    # 3a) productos borrados en el programa -> se borra la fila de la planilla
    bor = db.execute("SELECT id,codigo FROM cola_borrados WHERE subida=0").fetchall()
    if bor:
        hoja.borrar_productos({c for _, c in bor})
        db.executemany("UPDATE cola_borrados SET subida=1 WHERE id=?", [(i,) for i, _ in bor])
        db.commit()
        r["borrados_hoja"] = len(bor)

    # 3b) productos creados/editados en el programa -> hoja Productos
    sucios = db.execute("SELECT codigo,nombre,precio,costo,promo,valor,stock_min,promo_n FROM productos "
                        "WHERE sucio=1").fetchall()
    if sucios:
        filas = hoja.leer_productos()
        pos = {norm_cod(f[0]): i + 1 for i, f in enumerate(filas) if i > 0 and f and norm_cod(f[0])}
        upd, nuevos = [], []
        for cod, nom, pre, co, pr, val, sm, pn in sucios:
            vals = [cod, nom, pre, co, PROMOS.get(pr, PROMOS["ninguna"]), val, sm, pn]
            if cod in pos:
                upd.append((pos[cod], vals))
            else:
                nuevos.append(vals)
        nuevas = hoja.escribir_productos(upd, nuevos)
        hoja.escribir_formulas(nuevas)
        db.executemany("UPDATE productos SET sucio=0, en_hoja=1 WHERE codigo=?", [(s[0],) for s in sucios])
        db.commit()
        r["subidos"] = len(sucios)

    # 4) catálogo y stock de la planilla -> programa
    filas = hoja.leer_productos()
    faltan = [i + 1 for i, f in enumerate(filas) if i > 0 and f and norm_cod(f[0]) and
              (len(f) < 8 or f[7] in ("", None) or (isinstance(f[7], str) and f[7].startswith("#")))]
    if faltan:                                  # filas nuevas escritas a mano en la planilla: les pone la fórmula
        hoja.escribir_formulas(faltan)
        filas = hoja.leer_productos()
    hoy = datetime.now().strftime("%Y-%m-%d")
    db.execute("BEGIN IMMEDIATE")      # mientras se actualiza, una venta nueva espera unos milisegundos
    try:
        pv = dict(db.execute("SELECT i.codigo,SUM(i.cantidad) FROM items i JOIN ventas v ON v.id=i.venta_id "
                             "WHERE v.anulada=0 AND v.subida=0 GROUP BY i.codigo"))
        pa = dict(db.execute("SELECT i.codigo,SUM(i.cantidad) FROM items i JOIN ventas v ON v.id=i.venta_id "
                             "WHERE v.anulada=1 AND v.subida=1 AND v.anul_subida=0 GROUP BY i.codigo"))
        pi = dict(db.execute("SELECT codigo,SUM(cantidad) FROM cola_ingresos WHERE subida=0 GROUP BY codigo"))
        locales = {x[0] for x in db.execute("SELECT codigo FROM productos WHERE sucio=1")}
        borrando = {x[0] for x in db.execute("SELECT codigo FROM cola_borrados WHERE subida=0")}
        en_sheet = set()
        for i, f in enumerate(filas):
            if i == 0 or not f:
                continue
            f = list(f) + [""] * (9 - len(f))
            cod, nom, pre = norm_cod(f[0]), str(f[1]).strip(), a_num(f[2])
            if not cod:
                continue
            en_sheet.add(cod)
            if not nom or pre <= 0:
                r["ignoradas"] += 1
                continue
            if cod in locales or cod in borrando:
                continue
            costo, promo, val, smin = a_num(f[3]), clave_promo(f[4]), a_num(f[5]), a_num(f[6])
            pn = max(2, int(a_num(f[8], 2) or 2))
            stock = None if f[7] in ("", None) or (isinstance(f[7], str) and f[7].startswith("#")) else a_num(f[7])
            viejo = db.execute("SELECT nombre,precio,costo,promo,valor,stock_min,promo_n FROM productos "
                               "WHERE codigo=?", (cod,)).fetchone()
            nuevo = (nom, pre, costo, promo, val, smin, pn)
            if viejo is None:
                db.execute("INSERT INTO productos(codigo,nombre,precio,costo,promo,valor,stock_min,promo_n,stock,"
                           "creado,en_hoja) VALUES(?,?,?,?,?,?,?,?,0,?,1)", (cod,) + nuevo + (hoy,))
                r["bajados"] += 1
            elif tuple(viejo) != nuevo:
                db.execute("UPDATE productos SET nombre=?,precio=?,costo=?,promo=?,valor=?,stock_min=?,promo_n=?,"
                           "en_hoja=1 WHERE codigo=?", nuevo + (cod,))
                r["bajados"] += 1
            else:
                db.execute("UPDATE productos SET en_hoja=1 WHERE codigo=? AND en_hoja=0", (cod,))
            if stock is not None:
                db.execute("UPDATE productos SET stock=? WHERE codigo=?",
                           (stock - pv.get(cod, 0) + pa.get(cod, 0) + pi.get(cod, 0), cod))
        # productos que ya estaban en la planilla y ahora no están: se borraron allá -> se borran acá
        if en_sheet:
            cand = [x[0] for x in db.execute("SELECT codigo FROM productos WHERE en_hoja=1 AND sucio=0")
                    if x[0] not in en_sheet and x[0] not in borrando]
            total = db.execute("SELECT COUNT(*) FROM productos WHERE en_hoja=1").fetchone()[0]
            if len(cand) >= 6 and len(cand) * 2 >= total:
                r["bloqueado"] = len(cand)     # desaparecieron muchísimos de golpe: seguro fue un error
            else:
                db.executemany("DELETE FROM productos WHERE codigo=?", [(c,) for c in cand])
                r["borrados"] = len(cand)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return r


def resumen_sync(r):
    partes = []
    if r["ventas"]:
        partes.append(f"{r['ventas']} venta(s) subida(s)")
    if r["ingresos"]:
        partes.append(f"{r['ingresos']} ingreso(s) de stock subido(s)")
    if r["subidos"]:
        partes.append(f"{r['subidos']} producto(s) enviados")
    if r["bajados"]:
        partes.append(f"{r['bajados']} producto(s) actualizados desde la planilla")
    if r["borrados"]:
        partes.append(f"{r['borrados']} producto(s) borrados (los quitaste de la planilla)")
    if r["borrados_hoja"]:
        partes.append(f"{r['borrados_hoja']} producto(s) quitados de la planilla")
    if r["bloqueado"]:
        partes.append(f"ATENCIÓN: faltan {r['bloqueado']} productos en la planilla; por seguridad NO se borraron "
                      "del programa. Revisá que no hayas borrado filas sin querer")
    if r["ignoradas"]:
        partes.append(f"{r['ignoradas']} fila(s) ignoradas (falta nombre o precio)")
    return ", ".join(partes) or "Todo al día"


def reparar_planilla(hoja):
    """Vuelve a poner títulos, formato y la fórmula de stock en cada producto (arregla planillas viejas o rotas)."""
    hoja.preparar()
    hoja.limpiar_stock()
    filas = hoja.leer_productos()
    rows = codigos_con_fila(filas)
    hoja.escribir_formulas(rows)
    return f"Planilla reparada: fórmula de stock puesta en {len(rows)} producto(s) (separador '{_SEP[0]}')."


def preparar_planilla(db, hoja, caja):
    """Primera vez: crea las hojas y sube el catálogo y el stock actual. Si la planilla ya tiene productos
    (otra caja ya la preparó), solo se une a ella."""
    hoja.preparar()
    hoja.limpiar_stock()
    filas = hoja.leer_productos()
    hay = any(norm_cod(f[0]) for f in filas[1:] if f)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not hay:
        prods = db.execute("SELECT codigo,nombre,precio,costo,promo,valor,stock_min,promo_n,stock FROM productos "
                           "ORDER BY nombre").fetchall()
        nuevas = hoja.escribir_productos([], [[c, n, p, co, PROMOS.get(pr, PROMOS["ninguna"]), v, sm, pn]
                                              for c, n, p, co, pr, v, sm, pn, st in prods])
        hoja.escribir_formulas(nuevas)
        hoja.agregar_ingresos([[ahora, c, st, "Stock inicial", caja] for c, n, p, co, pr, v, sm, pn, st in prods if st])
        db.execute("UPDATE productos SET sucio=0, en_hoja=1")
        msg = f"Planilla lista: {len(prods)} producto(s) enviados con su stock actual."
    else:
        db.execute("UPDATE productos SET sucio=0")
        hoja.escribir_formulas(codigos_con_fila(filas))
        en_hoja = {norm_cod(f[0]) for f in filas[1:] if f}
        for (cod,) in db.execute("SELECT codigo FROM productos").fetchall():
            db.execute("UPDATE productos SET sucio=?, en_hoja=? WHERE codigo=?",
                       (0, 1, cod) if cod in en_hoja else (1, 0, cod))
        msg = "Te uniste a una planilla que ya tenía productos. El stock se toma de la planilla."
    # lo vendido/ingresado hasta hoy ya está reflejado en el stock que se subió: no se vuelve a subir
    db.execute("UPDATE ventas SET subida=CASE WHEN anulada=1 THEN 2 ELSE 1 END WHERE subida=0")
    db.execute("UPDATE cola_ingresos SET subida=1 WHERE subida=0")
    db.commit()
    return msg


# ---------- Tickets e impresión ----------
def linea(i, d="", a=None):
    a = a or ancho()
    return i[:max(0, a - len(d) - 1)].ljust(a - len(d)) + d


def armar_ticket(num_ticket, fecha, items, total, metodo, pago, vuelto, conf=None):
    """items: lista de (nombre, cantidad, precio, descuento). conf: textos sin guardar (vista previa)."""
    def g(k):
        return conf[k] if conf and k in conf else cfg(k)
    try:
        a = int(g("ancho"))
    except ValueError:
        a = 40
    L = [(g("t_nombre").strip() or g("nombre").strip() or "MI KIOSCO").center(a)]
    for k in ("t_l2", "t_l3"):
        if g(k).strip():
            L.append(g(k).strip().center(a))
    L += [f"{g('t_num')} {num_ticket}".center(a), fecha[:16].center(a), "-" * a]
    for nom, cant, pre, desc in items:
        L.append(linea(f"{cant} x {nom}", fmt(cant * pre), a))
        if desc:
            L.append(linea("   descuento promo", "-" + fmt(desc), a))
    L += ["-" * a, linea(g("t_total"), fmt(total), a), linea("Medio de pago", metodo, a)]
    if metodo == "Efectivo":
        L += [linea("Pago con", fmt(pago), a), linea("Vuelto", fmt(vuelto), a)]
    pies = [p.strip() for p in (g("t_pie"), g("t_pie2")) if p.strip()]
    if pies:
        L += [""] + [p.center(a) for p in pies]
    return "\n".join(L)


def ticket_texto(vid):
    fecha, total, metodo, pago, vuelto = con.execute(
        "SELECT fecha,total,metodo,pago,vuelto FROM ventas WHERE id=?", (vid,)).fetchone()
    items = con.execute("SELECT nombre,cantidad,precio,descuento FROM items WHERE venta_id=?", (vid,)).fetchall()
    return armar_ticket(vid, fecha, items, total, metodo, pago, vuelto)


def imprimir(texto, nombre_archivo):
    """Guarda el texto en la carpeta 'tickets' y lo manda a la impresora predeterminada de Windows."""
    carpeta = os.path.join(BASE, "tickets")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre_archivo + ".txt")
    with open(ruta, "w", encoding="utf-8-sig") as f:
        f.write(texto + "\n\n\n\n")
    try:
        if hasattr(os, "startfile"):
            os.startfile(ruta, "print")
        else:
            messagebox.showinfo("Ticket", f"Guardado en:\n{ruta}")
    except OSError as e:
        messagebox.showerror("Impresora", f"No se pudo imprimir: {e}\nEl ticket quedó en:\n{ruta}")


# ---------- Tema visual ----------
FONT = "Segoe UI"
PALETAS = {
    "claro": dict(BG="#EEF1F6", CARD="#FFFFFF", HEAD="#1E293B", PRIMARY="#2563EB", PRIMARY_H="#1D4ED8",
                  OK="#16A34A", OK_H="#15803D", DANGER="#DC2626", DANGER_L="#FEE2E2", DANGER_LH="#FECACA",
                  ROJO_T="#B91C1C", TEXT="#1F2937", MUTED="#6B7280", LINE="#E5E9F0", SOMBRA="#CDD4DF",
                  SOMBRA2="#E1E6EE", GRIS="#EEF1F6", GRIS_H="#E2E7EF", SUAVE="#E0E7FF", SUAVE_H="#C7D2FE",
                  SUAVE_T="#3730A3", AMBAR="#D97706", AMBAR_H="#B45309", INPUT="#F3F5F9", INPUT_B="#DDE3EC",
                  STRIPE="#F8FAFC", HEADBG="#F3F5F9", SELBG="#DBEAFE", VERDE_BG="#DCFCE7", VERDE_T="#15803D",
                  ROJO_BG="#FEE2E2", NEUTRO="#F3F5F9", AMB_BG="#FEF3C7", AMB_T="#92400E"),
    "oscuro": dict(BG="#0F172A", CARD="#1E293B", HEAD="#020617", PRIMARY="#3B82F6", PRIMARY_H="#2563EB",
                   OK="#16A34A", OK_H="#15803D", DANGER="#EF4444", DANGER_L="#4C1D1D", DANGER_LH="#6B2525",
                   ROJO_T="#FCA5A5", TEXT="#F1F5F9", MUTED="#94A3B8", LINE="#334155", SOMBRA="#0A0F1C",
                   SOMBRA2="#0C1322", GRIS="#334155", GRIS_H="#475569", SUAVE="#312E81", SUAVE_H="#3F3A9E",
                   SUAVE_T="#C7D2FE", AMBAR="#D97706", AMBAR_H="#B45309", INPUT="#0F172A", INPUT_B="#334155",
                   STRIPE="#223048", HEADBG="#273449", SELBG="#1E3A8A", VERDE_BG="#14532D", VERDE_T="#86EFAC",
                   ROJO_BG="#4C1D1D", NEUTRO="#273449", AMB_BG="#4A3B12", AMB_T="#FCD34D"),
}


def set_paleta(modo):
    globals().update(PALETAS[modo if modo in PALETAS else "claro"])


set_paleta(cfg("tema"))


def aplicar_tema(root):
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=TEXT, font=(FONT, 11))
    st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=38,
                 font=(FONT, 12), borderwidth=0, relief="flat", bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
    st.configure("Treeview.Heading", background=HEADBG, foreground=MUTED, font=(FONT, 10, "bold"),
                 relief="flat", padding=(8, 8))
    st.map("Treeview", background=[("selected", SELBG)], foreground=[("selected", TEXT)])
    st.map("Treeview.Heading", background=[("active", GRIS_H)])
    st.configure("TButton", background=PRIMARY, foreground="white", font=(FONT, 11, "bold"),
                 padding=(14, 8), borderwidth=0, focusthickness=0, focuscolor=PRIMARY)
    st.map("TButton", background=[("active", PRIMARY_H), ("pressed", PRIMARY_H)])
    st.configure("Peligro.TButton", background=DANGER)
    st.map("Peligro.TButton", background=[("active", "#B91C1C"), ("pressed", "#B91C1C")])
    st.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, padding=6)
    st.configure("TCombobox", padding=6, fieldbackground=INPUT, foreground=TEXT, background=GRIS, arrowcolor=TEXT)
    st.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)],
           selectbackground=[("readonly", INPUT)], selectforeground=[("readonly", TEXT)])
    st.configure("Vertical.TScrollbar", background=GRIS_H, troughcolor=CARD, bordercolor=CARD, arrowcolor=TEXT,
                 lightcolor=GRIS_H, darkcolor=GRIS_H)
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", PRIMARY)
    st.configure("TLabelframe", background=BG, bordercolor=LINE)
    st.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=(FONT, 10, "bold"))


def rrect(c, x1, y1, x2, y2, r, **kw):
    """Rectángulo con esquinas redondeadas dibujado en un Canvas."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Boton(tk.Canvas):
    """Botón redondeado con sombra, efecto al pasar el mouse y al apretar."""

    def __init__(self, master, text, command, color=None, hover=None, fg="white",
                 h=44, size=12, r=12, bgc=None, sombra=True, w=10):
        color, hover, bgc = color or PRIMARY, hover or PRIMARY_H, bgc or CARD
        super().__init__(master, width=w, height=h, bg=bgc, highlightthickness=0, cursor="hand2")
        self.text, self.command, self.color, self.hcolor = text, command, color, hover
        self.fg, self.size, self.r, self.sombra, self.estado = fg, size, r, sombra, "n"
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self.set("h"))
        self.bind("<Leave>", lambda e: self.set("n"))
        self.bind("<ButtonPress-1>", lambda e: self.set("p"))
        self.bind("<ButtonRelease-1>", self.release)

    def set(self, s):
        self.estado = s
        self.draw()

    def release(self, e):
        dentro = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        self.set("h" if dentro else "n")
        if dentro and self.command:
            self.command()

    def texto(self, t):
        self.text = t
        self.draw()

    def pulso(self, texto, color, ms=900):
        """Animación: el botón cambia de texto, se agranda un instante y vuelve a su color."""
        base = (self.text, self.color, self.hcolor, self.size)
        pasos = 20

        def paso(i):
            if not self.winfo_exists():
                return
            if i >= pasos:
                self.text, self.color, self.hcolor, self.size = base
                self.draw()
                return
            col = mezclar(color, base[1], max(0, (i - 6) / (pasos - 6)))
            self.text, self.color, self.hcolor = texto, col, col
            self.size = base[3] + (3 if i < 3 else 0)
            self.draw()
            self.after(ms // pasos, lambda: paso(i + 1))
        paso(0)

    def recolor(self, color, hover, fg="white"):
        self.color, self.hcolor, self.fg = color, hover, fg
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        if w < 20:
            return
        col = self.hcolor if self.estado in ("h", "p") else self.color
        if self.sombra:
            p = 2 if self.estado == "p" else 0
            rrect(self, 1, 5, w - 1, h - 1, self.r + 2, fill=SOMBRA2, outline="")
            rrect(self, 3, 6, w - 3, h - 2, self.r, fill=SOMBRA, outline="")
            top, bot = 2 + p, h - 6 + p
        else:
            top, bot = 1, h - 1
        rrect(self, 3, top, w - 3, bot, self.r, fill=col, outline="")
        self.create_text(w / 2, (top + bot) / 2, text=self.text, fill=self.fg, font=(FONT, self.size, "bold"))


def mezclar(a, b, t):
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))


def lista(parent, columnas, height=None):
    """Tabla con scroll. columnas = (clave, titulo, ancho, alineacion, estira)"""
    f = tk.Frame(parent, bg=CARD)
    kw = {"height": height} if height else {}
    t = ttk.Treeview(f, columns=[c[0] for c in columnas], show="headings", selectmode="browse", **kw)
    sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=sb.set)
    for k, tx, w, anc, st in columnas:
        t.heading(k, text=tx, anchor=anc)
        t.column(k, width=w, anchor=anc, stretch=st)
    t.tag_configure("par", background=STRIPE)
    sb.pack(side="right", fill="y")
    t.pack(side="left", fill="both", expand=True)
    return f, t


class Pestana(tk.Canvas):
    """Pestaña estilo navegador (todas en la misma ventana)."""

    def __init__(self, master, text, command, bgc=None):
        self.fnt = tkfont.Font(family=FONT, size=11, weight="bold")
        super().__init__(master, width=self.fnt.measure(text) + 28, height=44, bg=bgc or HEAD, highlightthickness=0, cursor="hand2")
        self.text, self.command, self.sel, self.hov, self.alerta = text, command, False, False, False
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: self.hover(True))
        self.bind("<Leave>", lambda e: self.hover(False))
        self.draw()

    def hover(self, v):
        self.hov = v
        self.draw()

    def seleccionar(self, v):
        self.sel = v
        self.draw()

    def texto(self, t, alerta=False):
        self.text, self.alerta = t, alerta
        self.config(width=self.fnt.measure(t) + 28)
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = int(self.cget("width")), 44
        if self.sel:
            rrect(self, 0, 2, w, h + 20, 12, fill=BG, outline="")
            fg, y = (AMBAR if self.alerta else TEXT), 24
        elif self.hov:
            rrect(self, 0, 8, w, h + 20, 12, fill="#334155", outline="")
            fg, y = ("#FBBF24" if self.alerta else "white"), 27
        else:
            fg, y = ("#FBBF24" if self.alerta else "#CBD5E1"), 27
        self.create_text(w / 2, y, text=self.text, fill=fg, font=(FONT, 11, "bold"))


class Tarjeta(tk.Canvas):
    """Panel blanco con esquinas redondeadas y sombra. Los widgets van en .inner"""

    def __init__(self, master, bgc=None, auto=True, r=16, pad=14, **kw):
        super().__init__(master, bg=bgc or BG, highlightthickness=0, **kw)
        self.r, self.pad, self.auto, self.face = r, pad, auto, CARD
        self.inner = tk.Frame(self, bg=CARD)
        self.win = self.create_window(pad, pad, window=self.inner, anchor="nw")
        self.bind("<Configure>", self.redraw)
        if auto:
            self.inner.bind("<Configure>", lambda e: self.config(height=e.height + 2 * pad + 8))

    def redraw(self, e=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("fondo")
        rrect(self, 1, 4, w - 1, h - 1, self.r + 2, fill=SOMBRA2, outline="", tags="fondo")
        rrect(self, 3, 6, w - 3, h - 3, self.r, fill=SOMBRA, outline="", tags="fondo")
        rrect(self, 2, 2, w - 2, h - 6, self.r, fill=self.face, outline="", tags="fondo")
        self.tag_lower("fondo")
        self.itemconfigure(self.win, width=max(10, w - 2 * self.pad))
        if not self.auto:
            self.itemconfigure(self.win, height=max(10, h - 2 * self.pad - 6))

    def pintar(self, color, *labels):
        self.face = color
        self.inner.config(bg=color)
        for lb in labels:
            lb.config(bg=color)
        self.redraw()


def entrada(master, size=20, **kw):
    return tk.Entry(master, font=(FONT, size), bg=INPUT, fg=TEXT, relief="flat", insertbackground=PRIMARY,
                    highlightthickness=2, highlightbackground=INPUT_B, highlightcolor=PRIMARY, **kw)




# ---------- Pantalla de PIN ----------
class Login(tk.Frame):
    """Tapa toda la ventana hasta que se ingresa un PIN válido (administrador o empleado)."""

    def __init__(self, app):
        super().__init__(app, bg=BG)
        self.app = app
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        card = Tarjeta(self, width=390, pad=24)
        card.place(relx=0.5, rely=0.5, anchor="center")
        i = card.inner
        tk.Label(i, text=nombre(), bg=CARD, fg=TEXT, font=(FONT, 22, "bold")).pack()
        self.msg = tk.Label(i, text="Ingresá tu PIN", bg=CARD, fg=MUTED, font=(FONT, 12))
        self.msg.pack(pady=(2, 10))

        self.f_pin = tk.Frame(i, bg=CARD)
        self.f_pin.pack(fill="x")
        self.pin = entrada(self.f_pin, 26, show="•", justify="center")
        self.pin.pack(fill="x", ipady=8)
        self.pin.bind("<Return>", lambda e: self.entrar())
        pad = tk.Frame(self.f_pin, bg=CARD)
        pad.pack(fill="x", pady=(10, 0))
        pad.columnconfigure((0, 1, 2), weight=1, uniform="k")
        for n, t in enumerate(("1", "2", "3", "4", "5", "6", "7", "8", "9", "Borrar", "0", "Entrar")):
            if t == "Entrar":
                b = Boton(pad, t, self.entrar, color=OK, hover=OK_H, h=50, size=12)
            elif t == "Borrar":
                b = Boton(pad, t, lambda: self.pin.delete(len(self.pin.get()) - 1), color=GRIS, hover=GRIS_H,
                          fg=TEXT, h=50, size=11)
            else:
                b = Boton(pad, t, lambda t=t: self.pin.insert("end", t), color=GRIS, hover=GRIS_H, fg=TEXT,
                          h=50, size=16)
            b.grid(row=n // 3, column=n % 3, sticky="ew", padx=3, pady=3)
        self.olv = tk.Label(i, text="Olvidé mi PIN", bg=CARD, fg=PRIMARY, font=(FONT, 10, "underline"), cursor="hand2")
        self.olv.pack(pady=(10, 0))
        self.olv.bind("<Button-1>", lambda e: self.ver_recuperar(True))

        self.f_rec = tk.Frame(i, bg=CARD)
        self.rec = entrada(self.f_rec, 20, justify="center")
        self.rec.pack(fill="x", ipady=8)
        self.rec.bind("<Return>", lambda e: self.recuperar())
        Boton(self.f_rec, "Recuperar acceso", self.recuperar, h=46, size=12).pack(fill="x", pady=(10, 0))
        Boton(self.f_rec, "Volver", lambda: self.ver_recuperar(False), color=GRIS, hover=GRIS_H, fg=TEXT,
              h=40, size=10).pack(fill="x", pady=(8, 0))
        self.pin.focus_set()

    def ver_recuperar(self, si):
        if si:
            self.f_pin.pack_forget()
            self.olv.pack_forget()
            self.f_rec.pack(fill="x")
            self.msg.config(text="Escribí tu código de recuperación", fg=MUTED)
            self.rec.focus_set()
        else:
            self.f_rec.pack_forget()
            self.f_pin.pack(fill="x")
            self.olv.pack(pady=(10, 0))
            self.msg.config(text="Ingresá tu PIN", fg=MUTED)
            self.pin.focus_set()

    def entrar(self):
        rol = rol_de(self.pin.get().strip())
        if rol:
            self.destroy()
            self.app.iniciar_sesion(rol)
        else:
            self.pin.delete(0, "end")
            self.msg.config(text="PIN incorrecto, probá de nuevo", fg=DANGER)

    def recuperar(self):
        code = self.rec.get().strip().upper()
        if cfg("pin_rec") and hpin(code) == cfg("pin_rec"):
            for k in ("pin_admin", "pin_empleado", "pin_rec"):
                set_cfg(k, "")
            self.destroy()
            self.app.iniciar_sesion("admin")
            self.app.toast("Se borraron los PIN. Creá nuevos en Configuración", AMBAR, 6000)
        else:
            self.rec.delete(0, "end")
            self.msg.config(text="Código incorrecto", fg=DANGER)


# ---------- Ventana principal: pestañas + caja ----------
class App(tk.Tk):
    # None = todas las pestañas. El empleado no ve precios de compra, ganancias ni configuración.
    PERMISOS = {"admin": None, "empleado": ("Caja", "Stock", "Ventas", "Cierre")}

    def __init__(self):
        super().__init__()
        self.geometry("1280x740")
        self.minsize(1280, 660)
        self.cart = {}  # codigo -> [nombre, precio, cant, promo, valor, costo]
        self.total = 0.0
        self.ticket = tk.BooleanVar(value=cfg("auto_ticket") == "1")
        self.metodo = tk.StringVar(value="Efectivo")
        self.ultimo = None       # vuelto de la última venta (se muestra hasta el próximo producto)
        self.cobrando = False
        self.t_fin = None
        self._toast = None
        self.actual = "Caja"
        self.rol = None
        self.sync = {"corriendo": False, "ultimo": "", "error": ""}
        self.sync_log, self.sync_avisar, self._t_pronto = [], True, None
        self.q_sync = queue.Queue()
        self.construir()
        if cfg("pin_admin"):
            self.bloquear()
        else:
            self.iniciar_sesion("admin")
        self.after(4000, self._ciclo_sync)

    def construir(self):
        """Arma toda la ventana (se vuelve a llamar al cambiar entre modo claro y oscuro)."""
        self.configure(bg=BG)
        aplicar_tema(self)
        self.paginas, self.tabs = {}, {}
        self._toast = None
        head = tk.Frame(self, bg=HEAD, height=62)
        head.pack(fill="x")
        head.pack_propagate(False)
        self.lbl_nombre = tk.Label(head, text=nombre(), bg=HEAD, fg="white", font=(FONT, 18, "bold"))
        self.lbl_nombre.pack(side="left", padx=(22, 10))
        Boton(head, "Bloquear", self.bloquear, color="#334155", hover="#475569", h=40, size=10, bgc=HEAD,
              sombra=False, w=92).pack(side="right", padx=(4, 16), pady=11)
        Boton(head, "Claro" if cfg("tema") == "oscuro" else "Oscuro", self.cambiar_tema, color="#334155",
              hover="#475569", h=40, size=10, bgc=HEAD, sombra=False, w=78).pack(side="right", padx=4, pady=11)
        self.barra = tk.Frame(head, bg=HEAD)
        self.barra.pack(side="left", fill="y", padx=(14, 0))
        self.cont = tk.Frame(self, bg=BG)
        self.cont.pack(fill="both", expand=True)
        self.cont.grid_rowconfigure(0, weight=1)
        self.cont.grid_columnconfigure(0, weight=1)

        caja = tk.Frame(self.cont, bg=BG)
        self.construir_caja(caja)
        self.agregar("Caja", caja, "<F1>")
        self.agregar("Productos", Productos(self.cont, self), "<F2>")
        self.agregar("Stock", Stock(self.cont, self), "<F3>")
        self.agregar("Ventas", Ventas(self.cont, self), "<F4>")
        self.agregar("Reportes", Reportes(self.cont, self), "<F5>")
        self.agregar("Cierre", Cierre(self.cont, self), "<F6>")
        self.agregar("Configuración", Configuracion(self.cont, self), "<F7>")
        self.agregar("Sheets", Sheets(self.cont, self), "<F8>")

        self.bind("<F12>", lambda e: self.rol and self.actual == "Caja" and self.cobrar())
        self.bind("<Escape>", lambda e: self.rol and self.actual == "Caja" and self.cancelar())
        self.bind("<FocusIn>", lambda e: self.scan.focus_set()
                  if (e.widget is self and self.actual == "Caja" and self.rol) else None)
        self.elegir("Efectivo")
        self.refresh()
        self.aplicar_rol()

    # ----- usuarios y PIN -----
    def permitidas(self):
        p = self.PERMISOS.get(self.rol, ())
        return list(self.paginas) if p is None else [n for n in self.paginas if n in p]

    def aplicar_rol(self):
        ok = self.permitidas()
        for t in self.tabs.values():
            t.pack_forget()
        for n in self.paginas:
            if n in ok:
                self.tabs[n].pack(side="left", padx=2, pady=(18, 0))
        r = {"admin": "Administrador", "empleado": "Empleado"}.get(self.rol, "")
        self.title(nombre() + " - Caja" + (f"  ({r})" if r and cfg("pin_admin") else ""))
        self.upd_stock_btn()

    def bloquear(self):
        if not cfg("pin_admin"):
            self.toast("Primero creá un PIN en Configuración > Seguridad", AMBAR, 4500)
            return
        if self.cobrando:
            self._fin_cobro()
        self.rol = None
        Login(self)

    def iniciar_sesion(self, rol):
        self.rol = rol
        self.aplicar_rol()
        self.ir("Caja")
        self.after(500, self.avisos_inicio)

    def avisos_inicio(self):
        partes = []
        n = len(bajos())
        if n:
            partes.append(f"⚠ {n} producto(s) con poco stock")
        if self.rol == "admin":
            m = len(sin_movimiento())
            if m:
                partes.append(f"{m} sin ventas hace {cfg('dias_sin_venta')}+ días (ver Reportes)")
        if partes:
            self.toast("      ".join(partes), AMBAR, 6500)

    # ----- modo oscuro -----
    def cambiar_tema(self):
        nuevo = "claro" if cfg("tema") == "oscuro" else "oscuro"
        set_cfg("tema", nuevo)
        set_paleta(nuevo)
        self.after(10, self.reconstruir)

    def reconstruir(self):
        if self.cobrando:
            self._fin_cobro()
        actual = self.actual
        for w in self.winfo_children():
            w.destroy()
        self.construir()
        self.ir(actual if actual in self.permitidas() else "Caja")

    # ----- sincronización con Google Sheets (en segundo plano, sin trabar la caja) -----
    def sheets_activo(self):
        return (cfg("sheets_on") == "1" and cfg("sheets_preparada") == "1" and bool(cfg("sheets_id"))
                and os.path.exists(RUTA_JSON))

    def pedir_sync(self, seg=6):
        """Después de una venta o un cambio: sincroniza en unos segundos (junta varios cambios en una sola vez)."""
        if not self.sheets_activo():
            return
        if self._t_pronto:
            self.after_cancel(self._t_pronto)
        self._t_pronto = self.after(seg * 1000, self.lanzar_sync)

    def _ciclo_sync(self):
        self.lanzar_sync()
        try:
            seg = max(15, int(cfg("sheets_seg")))
        except ValueError:
            seg = 60
        self.after(seg * 1000, self._ciclo_sync)

    def lanzar_sync(self, modo="sync", manual=False):
        if self.sync["corriendo"]:
            if manual:
                self.toast("Ya se está sincronizando...", HEAD)
            return
        if modo == "sync" and not self.sheets_activo():
            return
        self.sync["corriendo"] = True
        caja, sid = cfg("caja_id") or "C1", extraer_id(cfg("sheets_id"))

        def trabajo():
            db = None
            try:
                hoja = HojaGoogle(RUTA_JSON, sid)
                if modo == "probar":
                    msg = f"Conectado a «{hoja.titulo()}»"
                elif modo == "reparar":
                    msg = reparar_planilla(hoja)
                else:
                    db = abrir_db()
                    msg = preparar_planilla(db, hoja, caja) if modo == "preparar" else resumen_sync(sincronizar(db, hoja, caja))
                self.q_sync.put(("ok", msg, modo, manual))
            except Exception as e:
                self.q_sync.put(("error", explicar_error(e), modo, manual))
            finally:
                if db:
                    db.close()
        threading.Thread(target=trabajo, daemon=True).start()
        self.after(400, self._poll_sync)

    def _poll_sync(self):
        try:
            tipo, msg, modo, manual = self.q_sync.get_nowait()
        except queue.Empty:
            self.after(400, self._poll_sync)
            return
        hora = datetime.now().strftime("%H:%M:%S")
        self.sync["corriendo"] = False
        if tipo == "ok":
            if modo == "preparar":
                set_cfg("sheets_preparada", "1")
                set_cfg("sheets_on", "1")
            if modo in ("sync", "preparar"):
                self.sync["ultimo"] = hora
            self.sync["error"] = ""
            if msg != "Todo al día" or manual:
                self.sync_log.append(f"{hora}  {msg}")
        else:
            self.sync["error"] = msg
            self.sync_log.append(f"{hora}  ERROR: {msg}")
        for n in ("Productos", "Stock"):
            if n in self.paginas:
                self.paginas[n].load()
        self.upd_stock_btn()
        if "Sheets" in self.paginas:
            self.paginas["Sheets"].al_mostrar()
        if manual or (tipo == "error" and self.sync_avisar):
            self.toast(("✓ " if tipo == "ok" else "") + msg, OK if tipo == "ok" else DANGER, 5000)
        self.sync_avisar = tipo != "error"      # avisa el primer error, no cada vez que reintenta

    # ----- pestañas -----
    def agregar(self, nombre_tab, pagina, tecla):
        pagina.grid(row=0, column=0, sticky="nsew")
        self.paginas[nombre_tab] = pagina
        t = Pestana(self.barra, nombre_tab, lambda n=nombre_tab: self.ir(n))
        self.tabs[nombre_tab] = t
        self.bind(tecla, lambda e, n=nombre_tab: self.ir(n))

    def ir(self, n):
        if n not in self.paginas or self.rol is None:
            return
        if n not in self.permitidas():
            self.toast("Esa sección es solo para el administrador", AMBAR)
            return
        if self.cobrando:
            self._fin_cobro()
        self.actual = n
        pg = self.paginas[n]
        pg.tkraise()
        for k, t in self.tabs.items():
            t.seleccionar(k == n)
        if hasattr(pg, "al_mostrar"):
            pg.al_mostrar()
        if n == "Caja":
            self.scan.focus_set()

    def toast(self, texto, color=HEAD, ms=3500):
        """Aviso flotante que se va solo (no tapa nada ni abre ventanas)."""
        if self._toast is not None:
            try:
                self._toast.destroy()
            except tk.TclError:
                pass
        lb = tk.Label(self, text=texto, bg=color, fg="white", font=(FONT, 12, "bold"), padx=22, pady=12)
        lb.place(relx=0.5, y=72, anchor="n")
        lb.lift()
        self._toast = lb

        def quitar():
            try:
                lb.destroy()
            except tk.TclError:
                pass
        self.after(ms, quitar)

    # ----- pantalla de caja -----
    def construir_caja(self, page):
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        c = Tarjeta(left)
        c.pack(fill="x")
        tk.Label(c.inner, text="Escaneá o escribí el código y presioná Enter", bg=CARD, fg=MUTED,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        self.scan = entrada(c.inner, 22)
        self.scan.pack(fill="x", pady=(6, 2), ipady=8)
        tk.Label(c.inner, text="Varias unidades: 3*código     |     Supr o Borrar = sacar 1 unidad", bg=CARD,
                 fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.lbl_promo = tk.Label(c.inner, text=" ", bg=CARD, fg=MUTED, font=(FONT, 12, "bold"), anchor="w",
                                  justify="left", padx=10, pady=5)
        self.lbl_promo.pack(fill="x", pady=(8, 0))
        self.scan.bind("<Return>", self.on_scan)
        self.scan.bind("<Delete>", self.borrar_item)
        self.scan.bind("<BackSpace>", self.borrar_item)

        cart = Tarjeta(left, auto=False)
        cart.pack(fill="both", expand=True, pady=(14, 0))
        bar = tk.Frame(cart.inner, bg=CARD)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        bar.columnconfigure((0, 1, 2), weight=1, uniform="b")
        Boton(bar, "Sacar 1 unidad  (Supr)", self.menos, color=GRIS, hover=GRIS_H, fg=TEXT, h=44, size=10).grid(
            row=0, column=0, sticky="ew", padx=(0, 5))
        Boton(bar, "Sacar producto completo", self.quitar, color=GRIS, hover=GRIS_H, fg=TEXT, h=44,
              size=10).grid(row=0, column=1, sticky="ew", padx=5)
        Boton(bar, "Cancelar venta  (Esc)", self.cancelar, color=DANGER_L, hover=DANGER_LH, fg=ROJO_T, h=44,
              size=10).grid(row=0, column=2, sticky="ew", padx=(5, 0))
        fr, self.tree = lista(cart.inner, (("nombre", "Producto", 180, "w", True), ("promo", "Promo", 235, "w", False),
                                           ("cant", "Cant", 58, "e", False), ("precio", "Precio", 90, "e", False),
                                           ("desc", "Desc.", 90, "e", False), ("sub", "Subtotal", 100, "e", False)))
        fr.pack(fill="both", expand=True)
        self.tree.tag_configure("hecho", background=VERDE_BG, foreground=VERDE_T)
        self.tree.tag_configure("promo_pend", background=AMB_BG, foreground=AMB_T)
        self.tree.tag_configure("promo_ok", background=VERDE_BG, foreground=VERDE_T)
        self.tree.bind("<Delete>", lambda e: self.menos())
        self.tree.bind("<BackSpace>", lambda e: self.menos())
        self.vacio = tk.Label(self.tree, text="Escaneá un producto para empezar", bg=CARD, fg="#9CA3AF",
                              font=(FONT, 16))

        right = tk.Frame(body, bg=BG)
        right.pack(side="right", fill="y", padx=(16, 0))
        self.btn_cobrar = Boton(right, "COBRAR   F12", self.cobrar, color=OK, hover=OK_H, h=66, size=19, bgc=BG)
        self.btn_cobrar.pack(side="bottom", fill="x", pady=(10, 0))
        t1 = Tarjeta(right, width=410, pad=12)
        t1.pack(fill="x")
        tk.Label(t1.inner, text="TOTAL A COBRAR", bg=CARD, fg=MUTED, font=(FONT, 10, "bold")).pack(anchor="w")
        self.lbl_total = tk.Label(t1.inner, text="$0", bg=CARD, fg=TEXT, font=(FONT, 40, "bold"))
        self.lbl_total.pack(anchor="w")
        self.lbl_ahorro = tk.Label(t1.inner, text=" ", bg=CARD, fg=OK, font=(FONT, 11, "bold"))
        self.lbl_ahorro.pack(anchor="w")

        t2 = Tarjeta(right, width=410, pad=12)
        t2.pack(fill="x", pady=(10, 0))
        tk.Label(t2.inner, text="Medio de pago", bg=CARD, fg=MUTED, font=(FONT, 10, "bold")).pack(anchor="w")
        pf = tk.Frame(t2.inner, bg=CARD)
        pf.pack(fill="x", pady=(6, 10))
        pf.columnconfigure((0, 1, 2, 3), weight=1, uniform="p")
        self.pills = {}
        for i, (m, txt) in enumerate(zip(METODOS, ("Efectivo", "Transf.", "Débito", "Crédito"))):
            b = Boton(pf, txt, lambda m=m: self.elegir(m), h=38, size=10, r=10)
            b.grid(row=0, column=i, sticky="ew", padx=3)
            self.pills[m] = b
        tk.Label(t2.inner, text="El cliente pagó con", bg=CARD, fg=MUTED, font=(FONT, 10, "bold")).pack(anchor="w")
        self.pago = entrada(t2.inner, 24)
        self.pago.pack(fill="x", pady=(6, 8), ipady=4)
        self.pago.bind("<KeyRelease>", lambda e: self.upd_vuelto())
        self.pago.bind("<Return>", lambda e: self.cobrar())
        qf = tk.Frame(t2.inner, bg=CARD)
        qf.pack(fill="x")
        qf.columnconfigure(tuple(range(6)), weight=1, uniform="q")
        for i, v in enumerate((1000, 2000, 5000, 10000, 20000, None)):
            txt = "Exacto" if v is None else f"{v // 1000}.000"
            Boton(qf, txt, lambda v=v: self.set_pago(v), color=SUAVE, hover=SUAVE_H, fg=SUAVE_T, h=38, size=9,
                  r=9).grid(row=0, column=i, sticky="ew", padx=2)
        tk.Checkbutton(t2.inner, text="Imprimir ticket al cobrar", variable=self.ticket, bg=CARD,
                       activebackground=CARD, fg=TEXT, activeforeground=TEXT, font=(FONT, 10), selectcolor=INPUT,
                       highlightthickness=0).pack(anchor="w", pady=(10, 0))

        self.cv = Tarjeta(right, width=410, pad=12)
        self.cv.pack(fill="x", pady=(10, 0))
        self.vl_t = tk.Label(self.cv.inner, text="VUELTO", bg=CARD, fg=MUTED, font=(FONT, 10, "bold"))
        self.vl_t.pack(anchor="w")
        self.lbl_vuelto = tk.Label(self.cv.inner, text="—", bg=CARD, fg=MUTED, font=(FONT, 32, "bold"))
        self.lbl_vuelto.pack(anchor="w")

    def borrar_item(self, e):
        if self.scan.get():
            return None          # si hay texto escrito, Supr/Borrar funcionan normal
        self.menos()
        return "break"

    def elegir(self, m):
        self.metodo.set(m)
        for k, b in self.pills.items():
            if k == m:
                b.recolor(PRIMARY, PRIMARY_H, "white")
            else:
                b.recolor(GRIS, GRIS_H, TEXT)
        self.upd_vuelto()
        if self.actual == "Caja":
            self.scan.focus_set()

    def upd_stock_btn(self):
        n = len(bajos())
        if "Stock" in self.tabs:
            self.tabs["Stock"].texto(f"Stock  ⚠ {n}" if n else "Stock", alerta=bool(n))
        if "Reportes" in self.tabs:
            m = len(sin_movimiento()) if self.rol == "admin" else 0
            self.tabs["Reportes"].texto(f"Reportes  ⚠ {m}" if m else "Reportes", alerta=bool(m))
        if "Sheets" in self.tabs:
            self.tabs["Sheets"].texto("Sheets  ⚠" if self.sync["error"] else "Sheets", alerta=bool(self.sync["error"]))

    def set_pago(self, v):
        self.elegir("Efectivo")
        self.pago.delete(0, "end")
        self.pago.insert(0, f"{(self.total if v is None else v):g}")
        self.upd_vuelto()
        self.scan.focus_set()

    def on_scan(self, _=None):
        if self.cobrando:
            self._fin_cobro()
        txt = self.scan.get().strip()
        self.scan.delete(0, "end")
        if not txt:
            return
        cant = 1
        if "*" in txt:
            a, txt = txt.split("*", 1)
            cant = max(1, int(num(a)))
        r = con.execute("SELECT nombre,precio,promo,valor,costo,promo_n FROM productos WHERE codigo=?", (txt,)).fetchone()
        if not r:
            if self.rol != "admin":
                self.toast("Producto no cargado. Avisale al administrador", DANGER, 4500)
                return
            # producto nuevo: te lleva a la pestaña Productos con el código ya cargado
            self.paginas["Productos"].nuevo_con_codigo(txt)
            self.ir("Productos")
            self.toast("Código nuevo: completá nombre y precio y tocá Guardar", AMBAR, 5000)
            return
        self.ultimo = None
        if txt in self.cart:
            self.cart[txt][2] += cant
        else:
            self.cart[txt] = [r[0], r[1], cant, r[2], r[3], r[4], r[5]]
        self.refresh()
        self.tree.selection_set(txt)
        self.tree.see(txt)
        self.mostrar_promo(txt)
        self.scan.focus_set()

    def mostrar_promo(self, cod):
        """Cartel arriba: le avisa a quien cobra que el producto tiene promo, para que se la ofrezca al cliente."""
        nom, pre, cant, promo, val, costo, n = self.cart[cod]
        _, estado = info_promo(promo, val, n, cant)
        et = etiqueta_promo(promo, val, n)
        if estado == "pend":
            self.lbl_promo.config(text=f"OFERTA en {nom}: {et}  →  ofrecele llevar más", bg=AMB_BG, fg=AMB_T)
        elif estado == "ok":
            self.lbl_promo.config(text=f"✓ Promo aplicada en {nom}: {et}", bg=VERDE_BG, fg=VERDE_T)
        else:
            self.lbl_promo.config(text=" ", bg=CARD, fg=MUTED)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.total, ahorro = 0.0, 0.0
        for i, (cod, (nom, pre, cant, promo, val, _, pn)) in enumerate(self.cart.items()):
            d = descuento(pre, cant, promo, val, pn)
            sub = pre * cant - d
            self.total += sub
            ahorro += d
            texto, estado = info_promo(promo, val, pn, cant)
            tag = ("promo_" + estado,) if estado else (("par",) if i % 2 else ())
            self.tree.insert("", "end", iid=cod, tags=tag,
                             values=(nom, texto, cant, fmt(pre), "-" + fmt(d) if d else "", fmt(sub)))
        self.lbl_total.config(text=fmt(self.total))
        self.lbl_ahorro.config(text=f"Ahorro por promos: {fmt(ahorro)}" if ahorro else " ")
        if self.cart:
            self.vacio.place_forget()
        else:
            self.vacio.place(relx=0.5, rely=0.45, anchor="center")
            self.lbl_promo.config(text=" ", bg=CARD, fg=MUTED)
        self.upd_vuelto()

    def upd_vuelto(self):
        txt = self.pago.get().strip()
        big, small = (FONT, 32, "bold"), (FONT, 22, "bold")
        if not self.cart and self.ultimo is not None:
            face, fg, tit, val, f = VERDE_BG, VERDE_T, "VUELTO A ENTREGAR", fmt(self.ultimo), big
        elif self.metodo.get() != "Efectivo":
            face, fg, tit, val, f = NEUTRO, MUTED, "VUELTO", "No aplica", small
        elif not txt or not self.cart:
            face, fg, tit, val, f = NEUTRO, MUTED, "VUELTO", "—", big
        else:
            v = num(txt) - self.total
            if v >= 0:
                face, fg, tit, val, f = VERDE_BG, VERDE_T, "VUELTO", fmt(v), big
            else:
                face, fg, tit, val, f = ROJO_BG, ROJO_T, "FALTA", fmt(-v), big
        self.cv.pintar(face, self.vl_t, self.lbl_vuelto)
        self.vl_t.config(text=tit, fg=fg)
        self.lbl_vuelto.config(text=val, fg=fg, font=f)

    def menos(self):
        """Saca 1 unidad del producto seleccionado (o del último escaneado)."""
        if not self.cart or self.cobrando:
            return
        sel = self.tree.selection()
        cod = sel[0] if sel else list(self.cart)[-1]
        self.cart[cod][2] -= 1
        if self.cart[cod][2] <= 0:
            del self.cart[cod]
        self.refresh()
        if self.cart:
            self.tree.selection_set(cod if cod in self.cart else list(self.cart)[-1])
        self.scan.focus_set()

    def quitar(self):
        if self.cobrando:
            return
        sel = self.tree.selection()
        cod = sel[0] if sel else (list(self.cart)[-1] if self.cart else None)
        if cod:
            self.cart.pop(cod, None)
            self.refresh()
        self.scan.focus_set()

    def cancelar(self):
        if self.cobrando:
            return
        if self.cart and not messagebox.askyesno("Cancelar", "¿Cancelar toda la venta actual?"):
            return
        self.cart.clear()
        self.ultimo = None
        self.pago.delete(0, "end")
        self.refresh()
        self.scan.focus_set()

    def cobrar(self):
        if not self.cart or self.cobrando:
            return
        metodo = self.metodo.get()
        txt = self.pago.get().strip()
        pago = num(txt) if (metodo == "Efectivo" and txt) else self.total
        if pago < self.total - 0.001:
            self.toast(f"Faltan {fmt(self.total - pago)}", DANGER)
            return
        vuelto = pago - self.total
        cur = con.cursor()
        cur.execute("INSERT INTO ventas(fecha,total,metodo,pago,vuelto) VALUES(?,?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.total, metodo, pago, vuelto))
        vid = cur.lastrowid
        for cod, (nom, pre, cant, promo, val, costo, pn) in self.cart.items():
            d = descuento(pre, cant, promo, val, pn)
            cur.execute("INSERT INTO items(venta_id,codigo,nombre,cantidad,precio,descuento,subtotal,costo) VALUES(?,?,?,?,?,?,?,?)",
                        (vid, cod, nom, cant, pre, d, pre * cant - d, costo))
            cur.execute("UPDATE productos SET stock=stock-? WHERE codigo=?", (cant, cod))  # descuenta stock
        con.commit()
        self.pedir_sync()
        if self.ticket.get():
            imprimir(ticket_texto(vid), f"ticket_{vid}")
        poco = []
        for cod in self.cart:
            r = con.execute("SELECT nombre,stock,stock_min FROM productos WHERE codigo=?", (cod,)).fetchone()
            if r and r[2] > 0 and r[1] <= r[2]:
                poco.append(f"{r[0]} ({r[1]:g})")
        # --- animación de cobro ---
        self.ultimo = vuelto if (metodo == "Efectivo" and vuelto > 0) else None
        self.cobrando = True
        self.cart.clear()
        self.tree.selection_set(())
        for iid in self.tree.get_children():
            self.tree.item(iid, tags=("hecho",))
        self.lbl_total.config(fg=OK)
        self.btn_cobrar.pulso("✓  COBRADO", "#4ADE80", 900)
        self.upd_vuelto()
        if poco:
            self.toast(f"✓ Venta {vid} cobrada     ⚠ Poco stock: " + ", ".join(poco), AMBAR, 5000)
        else:
            self.toast(f"✓ Venta {vid} cobrada", OK, 2500)
        self.t_fin = self.after(900, self._fin_cobro)

    def _fin_cobro(self):
        if not self.cobrando:
            return
        if self.t_fin:
            self.after_cancel(self.t_fin)
            self.t_fin = None
        self.cobrando = False
        self.lbl_total.config(fg=TEXT)
        self.pago.delete(0, "end")
        self.refresh()
        self.upd_stock_btn()
        if self.actual == "Caja":
            self.scan.focus_set()


# ---------- Piezas comunes de las pestañas ----------
def envoltorio(frame):
    w = tk.Frame(frame, bg=BG)
    w.pack(fill="both", expand=True, padx=16, pady=14)
    return w


def titulo(parent, texto):
    tk.Label(parent, text=texto, bg=CARD, fg=TEXT, font=(FONT, 14, "bold")).pack(anchor="w", pady=(0, 6))


def campo(parent, texto, var, row, col, span=1, size=13, show=""):
    f = tk.Frame(parent, bg=CARD)
    f.grid(row=row, column=col, columnspan=span, sticky="ew", padx=4, pady=3)
    tk.Label(f, text=texto, bg=CARD, fg=MUTED, font=(FONT, 9, "bold")).pack(anchor="w")
    e = entrada(f, size, textvariable=var, show=show)
    e.pack(fill="x", ipady=4)
    return e


def etiqueta(parent, texto):
    return tk.Label(parent, text=texto, bg=CARD, fg=MUTED, font=(FONT, 9, "bold"))


def gris(parent, texto, cmd, **kw):
    return Boton(parent, texto, cmd, color=GRIS, hover=GRIS_H, fg=TEXT, **kw)


def rojo(parent, texto, cmd, **kw):
    return Boton(parent, texto, cmd, color=DANGER_L, hover=DANGER_LH, fg=ROJO_T, **kw)


# ---------- Pestaña Productos ----------
class Productos(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app, self.volver = app, False
        wrap = envoltorio(self)
        izq = Tarjeta(wrap, auto=False)
        izq.pack(side="left", fill="both", expand=True)
        der = Tarjeta(wrap, width=440)
        der.pack(side="right", anchor="n", padx=(16, 0))

        top = tk.Frame(izq.inner, bg=CARD)
        top.pack(fill="x", pady=(0, 8))
        etiqueta(top, "Buscar").pack(side="left")
        self.q = tk.StringVar()
        entrada(top, 12, textvariable=self.q, width=16).pack(side="left", padx=8, ipady=3)
        self.q.trace_add("write", lambda *a: self.load())
        self.pct = tk.StringVar()
        Boton(top, "Aplicar", self.masivo, h=36, size=10, w=84).pack(side="right")
        entrada(top, 12, textvariable=self.pct, width=6).pack(side="right", padx=6, ipady=3)
        etiqueta(top, "Ajustar TODOS los precios en %").pack(side="right")

        fr, self.t = lista(izq.inner, (("codigo", "Código", 110, "w", False), ("nombre", "Nombre", 190, "w", True),
                                       ("precio", "Precio", 85, "e", False), ("costo", "Costo", 85, "e", False),
                                       ("stock", "Stock", 60, "e", False), ("promo", "Promo", 200, "w", False)))
        fr.pack(fill="both", expand=True)
        self.t.bind("<<TreeviewSelect>>", self.sel)

        titulo(der.inner, "Producto")
        f = tk.Frame(der.inner, bg=CARD)
        f.pack(fill="x")
        f.columnconfigure((0, 1), weight=1, uniform="c")
        self.v = {k: tk.StringVar() for k in ("codigo", "nombre", "precio", "costo", "stock", "stock_min", "valor", "promo_n")}
        self.promo = tk.StringVar(value=PROMOS["ninguna"])
        self.e_codigo = campo(f, "Código de barras", self.v["codigo"], 0, 0, 2)
        self.e_nombre = campo(f, "Nombre", self.v["nombre"], 1, 0, 2)
        campo(f, "Precio de venta", self.v["precio"], 2, 0)
        campo(f, "Precio de compra (costo)", self.v["costo"], 2, 1)
        campo(f, "Stock actual", self.v["stock"], 3, 0)
        campo(f, "Stock mínimo (aviso)", self.v["stock_min"], 3, 1)
        pf = tk.Frame(f, bg=CARD)
        pf.grid(row=4, column=0, columnspan=2, sticky="ew", padx=4, pady=3)
        etiqueta(pf, "Promo").pack(anchor="w")
        ttk.Combobox(pf, textvariable=self.promo, values=list(PROMOS.values()), state="readonly",
                     font=(FONT, 11)).pack(fill="x", ipady=3)
        campo(f, "% de descuento", self.v["valor"], 5, 0)
        campo(f, "N (unidad o cantidad)", self.v["promo_n"], 5, 1)
        self.lbl_ayuda = tk.Label(f, text="", bg=CARD, fg=MUTED, font=(FONT, 9), wraplength=390, justify="left",
                                  anchor="w")
        self.lbl_ayuda.grid(row=6, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 2))
        self.promo.trace_add("write", lambda *a: self.ayuda_promo())
        self.v["promo_n"].set("2")
        tk.Label(der.inner, text="Decimales con coma: 1500,50", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(4, 0))
        b = tk.Frame(der.inner, bg=CARD)
        b.pack(fill="x", pady=(10, 0))
        b.columnconfigure((0, 1, 2), weight=1, uniform="b")
        Boton(b, "Guardar", self.guardar, color=OK, hover=OK_H, h=44, size=11).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        gris(b, "Nuevo", self.nuevo, h=44, size=11).grid(row=0, column=1, sticky="ew", padx=4)
        rojo(b, "Eliminar", self.eliminar, h=44, size=11).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        self.load()

    def al_mostrar(self):
        self.load()

    def load(self):
        self.t.delete(*self.t.get_children())
        q = f"%{self.q.get()}%"
        for i, (cod, nom, pre, costo, stock, promo, val, pn) in enumerate(con.execute(
                "SELECT codigo,nombre,precio,costo,stock,promo,valor,promo_n FROM productos "
                "WHERE codigo LIKE ? OR nombre LIKE ? ORDER BY nombre", (q, q))):
            self.t.insert("", "end", iid=cod, tags=("par",) if i % 2 else (),
                          values=(cod, nom, fmt(pre), fmt(costo) if costo else "-", f"{stock:g}",
                                  etiqueta_promo(promo, val, pn)))

    def sel(self, _=None):
        s = self.t.selection()
        if not s:
            return
        cod, nom, pre, costo, stock, smin, promo, val, pn = con.execute(
            "SELECT codigo,nombre,precio,costo,stock,stock_min,promo,valor,promo_n FROM productos WHERE codigo=?",
            (s[0],)).fetchone()
        for k, x in (("codigo", cod), ("nombre", nom), ("precio", f"{pre:g}".replace(".", ",")),
                     ("costo", f"{costo:g}".replace(".", ",")), ("stock", f"{stock:g}"),
                     ("stock_min", f"{smin:g}"), ("valor", f"{val:g}"), ("promo_n", f"{pn or 2}")):
            self.v[k].set(x)
        self.promo.set(PROMOS.get(promo, PROMOS["ninguna"]))

    def nuevo(self):
        for v in self.v.values():
            v.set("")
        self.v["promo_n"].set("2")
        self.promo.set(PROMOS["ninguna"])
        self.volver = False

    def ayuda_promo(self):
        self.lbl_ayuda.config(text=AYUDA_PROMO.get(REV.get(self.promo.get(), "ninguna"), ""))

    def nuevo_con_codigo(self, cod):
        """Viene de escanear un código que no existe: lo deja cargado para completar."""
        self.nuevo()
        self.v["codigo"].set(cod)
        self.volver = True
        self.e_nombre.focus_set()

    def guardar(self):
        cod, nom, pre = self.v["codigo"].get().strip(), self.v["nombre"].get().strip(), num(self.v["precio"].get())
        if not cod or not nom or pre <= 0:
            self.app.toast("Completá código, nombre y precio de venta", DANGER)
            return
        promo, val, pn = REV[self.promo.get()], num(self.v["valor"].get()), int(num(self.v["promo_n"].get()) or 2)
        if promo in ("pct", "nesima", "pack") and not (0 < val <= 100):
            self.app.toast("Poné el % de descuento (entre 1 y 100)", DANGER)
            return
        if promo in ("nesima", "pack") and pn < 2:
            self.app.toast("N tiene que ser 2 o más", DANGER)
            return
        viejo = con.execute("SELECT stock FROM productos WHERE codigo=?", (cod,)).fetchone()
        con.execute("""INSERT INTO productos(codigo,nombre,precio,costo,stock,stock_min,promo,valor,promo_n,creado)
                       VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, precio=excluded.precio,
                       costo=excluded.costo, stock=excluded.stock, stock_min=excluded.stock_min,
                       promo=excluded.promo, valor=excluded.valor, promo_n=excluded.promo_n""",
                    (cod, nom, pre, num(self.v["costo"].get()), num(self.v["stock"].get()),
                     num(self.v["stock_min"].get()), promo, val, pn, datetime.now().strftime("%Y-%m-%d")))
        con.execute("DELETE FROM cola_borrados WHERE codigo=? AND subida=0", (cod,))
        con.commit()
        registrar_ingreso(cod, num(self.v["stock"].get()) - (viejo[0] if viejo else 0),
                          "Ajuste desde Productos" if viejo else "Alta de producto")
        con.execute("UPDATE productos SET sucio=1 WHERE codigo=?", (cod,))
        con.commit()
        self.app.pedir_sync()
        volvio = self.volver
        self.load()
        self.nuevo()
        self.app.upd_stock_btn()
        if volvio:
            self.app.ir("Caja")
            self.app.toast("Producto guardado. Escanealo de nuevo para sumarlo", OK, 4000)
        else:
            self.app.toast("Producto guardado ✓", OK)

    def eliminar(self):
        cod = self.v["codigo"].get().strip()
        if cod and messagebox.askyesno("Eliminar", f"¿Eliminar el producto {cod} de la base?"):
            con.execute("DELETE FROM productos WHERE codigo=?", (cod,))
            con.execute("INSERT INTO cola_borrados(codigo) VALUES(?)", (cod,))   # también se borra de la planilla
            con.commit()
            self.load()
            self.nuevo()
            self.app.upd_stock_btn()
            self.app.pedir_sync()
            self.app.toast("Producto eliminado", HEAD)

    def masivo(self):
        txt = self.pct.get().strip()
        if not txt:
            self.app.toast("Escribí el porcentaje (ej: 10 sube 10%, -5 baja 5%)", DANGER)
            return
        p = num(txt)
        if not messagebox.askyesno("Cambio masivo", f"¿Cambiar TODOS los precios {p:+g}%?"):
            return
        con.execute("UPDATE productos SET precio=ROUND(precio*(1+?/100.0)), sucio=1", (p,))
        con.commit()
        self.pct.set("")
        self.load()
        self.app.pedir_sync()
        self.app.toast(f"Precios actualizados {p:+g}%", OK)


# ---------- Pestaña Stock ----------
class Stock(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = envoltorio(self)
        izq = Tarjeta(wrap, auto=False)
        izq.pack(side="left", fill="both", expand=True)
        der = Tarjeta(wrap, width=400)
        der.pack(side="right", anchor="n", padx=(16, 0))
        top = tk.Frame(izq.inner, bg=CARD)
        top.pack(fill="x", pady=(0, 8))
        etiqueta(top, "Buscar / escanear").pack(side="left")
        self.q = tk.StringVar()
        self.e_q = entrada(top, 12, textvariable=self.q, width=18)
        self.e_q.pack(side="left", padx=8, ipady=3)
        self.e_q.bind("<Return>", self.enter)
        self.q.trace_add("write", lambda *a: self.load())
        self.solo = tk.BooleanVar()
        tk.Checkbutton(top, text="Solo poco stock", variable=self.solo, command=self.load, bg=CARD,
                       activebackground=CARD, fg=TEXT, activeforeground=TEXT, selectcolor=INPUT, font=(FONT, 10),
                       highlightthickness=0).pack(side="left", padx=10)
        fr, self.t = lista(izq.inner, (("codigo", "Código", 130, "w", False), ("nombre", "Nombre", 260, "w", True),
                                       ("stock", "Stock", 80, "e", False), ("minimo", "Mínimo", 80, "e", False),
                                       ("estado", "Estado", 110, "w", False)))
        fr.pack(fill="both", expand=True)
        self.t.tag_configure("bajo", background=ROJO_BG)
        self.t.bind("<<TreeviewSelect>>", self.sel)

        titulo(der.inner, "Reponer mercadería")
        self.lbl_sel = tk.Label(der.inner, text="Elegí un producto de la lista", bg=CARD, fg=TEXT,
                                font=(FONT, 15, "bold"), wraplength=350, justify="left")
        self.lbl_sel.pack(anchor="w")
        self.lbl_cant = tk.Label(der.inner, text=" ", bg=CARD, fg=MUTED, font=(FONT, 12))
        self.lbl_cant.pack(anchor="w", pady=(2, 10))
        etiqueta(der.inner, "Cantidad").pack(anchor="w")
        self.c = tk.StringVar()
        self.ent = entrada(der.inner, 22, textvariable=self.c)
        self.ent.pack(fill="x", ipady=6, pady=(4, 10))
        self.ent.bind("<Return>", lambda e: self.accion("sumar"))
        Boton(der.inner, "+  Sumar al stock", lambda: self.accion("sumar"), color=OK, hover=OK_H, h=48, size=12).pack(fill="x")
        gris(der.inner, "Fijar stock exacto (inventario)", lambda: self.accion("fijar"), h=44, size=10).pack(fill="x", pady=(8, 0))
        gris(der.inner, "Fijar mínimo de aviso", lambda: self.accion("min"), h=44, size=10).pack(fill="x", pady=(8, 0))
        self.load()

    def al_mostrar(self):
        self.load()
        self.e_q.focus_set()

    def load(self):
        keep = self.t.selection()
        self.t.delete(*self.t.get_children())
        q = f"%{self.q.get()}%"
        for i, (cod, nom, st, mn) in enumerate(con.execute(
                "SELECT codigo,nombre,stock,stock_min FROM productos WHERE codigo LIKE ? OR nombre LIKE ? ORDER BY nombre", (q, q))):
            bajo = mn > 0 and st <= mn
            if self.solo.get() and not bajo:
                continue
            estado = "NEGATIVO" if st < 0 else ("POCO STOCK" if bajo else "OK")
            tag = ("bajo",) if (bajo or st < 0) else (("par",) if i % 2 else ())
            self.t.insert("", "end", iid=cod, values=(cod, nom, f"{st:g}", f"{mn:g}", estado), tags=tag)
        if keep and self.t.exists(keep[0]):
            self.t.selection_set(keep[0])

    def sel(self, _=None):
        s = self.t.selection()
        if not s:
            return
        nom, st, mn = con.execute("SELECT nombre,stock,stock_min FROM productos WHERE codigo=?", (s[0],)).fetchone()
        self.lbl_sel.config(text=nom)
        self.lbl_cant.config(text=f"Stock actual: {st:g}     |     Aviso con: {mn:g}")

    def enter(self, _=None):
        kids = self.t.get_children()
        cod = self.q.get().strip()
        if cod in kids:
            self.t.selection_set(cod)
        elif len(kids) == 1:
            self.t.selection_set(kids[0])
        else:
            return
        self.ent.focus_set()

    def accion(self, tipo):
        s = self.t.selection()
        if not s or not self.c.get().strip():
            self.app.toast("Elegí un producto y escribí una cantidad", DANGER)
            return
        n = num(self.c.get())
        if tipo == "sumar":
            con.execute("UPDATE productos SET stock=stock+? WHERE codigo=?", (n, s[0]))
            registrar_ingreso(s[0], n, "Ingreso de mercadería")
        elif tipo == "fijar":
            actual = con.execute("SELECT stock FROM productos WHERE codigo=?", (s[0],)).fetchone()[0]
            con.execute("UPDATE productos SET stock=? WHERE codigo=?", (n, s[0]))
            registrar_ingreso(s[0], n - actual, "Inventario (stock contado)")
        else:
            con.execute("UPDATE productos SET stock_min=?, sucio=1 WHERE codigo=?", (n, s[0]))
        con.commit()
        self.app.pedir_sync()
        self.c.set("")
        self.load()
        self.sel()
        self.app.upd_stock_btn()
        self.app.toast("Stock actualizado ✓", OK)


# ---------- Pestaña Ventas (con el detalle en la misma pantalla) ----------
class Ventas(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app, self.sel_id = app, None
        wrap = envoltorio(self)
        izq = Tarjeta(wrap, auto=False)
        izq.pack(side="left", fill="both", expand=True)
        der = Tarjeta(wrap, auto=False, width=430)
        der.pack(side="right", fill="y", padx=(16, 0))

        top = tk.Frame(izq.inner, bg=CARD)
        top.pack(fill="x", pady=(0, 8))
        etiqueta(top, "Fecha (AAAA-MM-DD)").pack(side="left")
        self.f = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        entrada(top, 12, textvariable=self.f, width=11).pack(side="left", padx=8, ipady=3)
        Boton(top, "Ver", self.load, h=36, size=10, w=64).pack(side="left", padx=3)
        gris(top, "Hoy", lambda: self.dia(0), h=36, size=10, w=64).pack(side="left", padx=3)
        gris(top, "Ayer", lambda: self.dia(1), h=36, size=10, w=64).pack(side="left", padx=3)
        self.res = tk.Label(izq.inner, bg=CARD, fg=TEXT, font=(FONT, 12, "bold"), justify="left", anchor="w")
        self.res.pack(side="bottom", fill="x", pady=(8, 0))
        fr, self.t = lista(izq.inner, (("id", "N°", 60, "w", False), ("hora", "Hora", 80, "w", False),
                                       ("metodo", "Medio de pago", 200, "w", True), ("total", "Total", 120, "e", False)))
        fr.pack(fill="both", expand=True)
        self.t.tag_configure("anulada", foreground="#9CA3AF")
        self.t.bind("<<TreeviewSelect>>", self.elegida)

        # panel de detalle (misma pantalla)
        self.d_tit = tk.Label(der.inner, text="Elegí una venta de la lista", bg=CARD, fg=TEXT,
                              font=(FONT, 15, "bold"), anchor="w")
        self.d_tit.pack(fill="x")
        self.d_sub = tk.Label(der.inner, text="Acá vas a ver qué se vendió en cada una.", bg=CARD, fg=MUTED,
                              font=(FONT, 11), anchor="w")
        self.d_sub.pack(fill="x", pady=(0, 8))
        bb = tk.Frame(der.inner, bg=CARD)
        bb.pack(side="bottom", fill="x", pady=(10, 0))
        bb.columnconfigure((0, 1), weight=1, uniform="b")
        gris(bb, "Reimprimir ticket", self.reimprimir, h=44, size=10).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        rojo(bb, "Anular venta", self.anular, h=44, size=10).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.d_res = tk.Label(der.inner, text="", bg=CARD, fg=TEXT, font=(FONT, 13, "bold"), justify="left", anchor="w")
        self.d_res.pack(side="bottom", fill="x", pady=(8, 0))
        fr2, self.dt = lista(der.inner, (("p", "Producto", 220, "w", True), ("c", "Cant", 50, "e", False),
                                         ("s", "Subtotal", 100, "e", False)), height=6)
        fr2.pack(fill="both", expand=True)
        self.load()

    def al_mostrar(self):
        self.load(self.sel_id)

    def dia(self, atras):
        self.f.set((datetime.now() - timedelta(days=atras)).strftime("%Y-%m-%d"))
        self.load()

    def load(self, keep=None):
        self.t.delete(*self.t.get_children())
        tot, por = 0.0, {}
        for vid, hora, met, total, anu in con.execute(
                "SELECT id,substr(fecha,12,5),metodo,total,anulada FROM ventas WHERE substr(fecha,1,10)=? ORDER BY id DESC",
                (self.f.get().strip(),)):
            self.t.insert("", "end", iid=str(vid), values=(vid, hora, met + (" (ANULADA)" if anu else ""), fmt(total)),
                          tags=("anulada",) if anu else ())
            if not anu:
                tot += total
                por[met] = por.get(met, 0) + total
        det = "".join(f"\n   {m}: {fmt(v)}" for m, v in por.items())
        self.res.config(text=f"TOTAL DEL DÍA: {fmt(tot)}{det}")
        if keep and self.t.exists(str(keep)):
            self.t.selection_set(str(keep))
        else:
            self.sel_id = None
            self.detalle(None)

    def elegida(self, _=None):
        s = self.t.selection()
        if s:
            self.sel_id = int(s[0])
            self.detalle(self.sel_id)

    def detalle(self, vid):
        self.dt.delete(*self.dt.get_children())
        if vid is None:
            self.d_tit.config(text="Elegí una venta de la lista", fg=TEXT)
            self.d_sub.config(text="Acá vas a ver qué se vendió en cada una.")
            self.d_res.config(text="")
            return
        fecha, total, metodo, pago, vuelto, anu = con.execute(
            "SELECT fecha,total,metodo,pago,vuelto,anulada FROM ventas WHERE id=?", (vid,)).fetchone()
        self.d_tit.config(text=f"Venta N° {vid}" + ("  (ANULADA)" if anu else ""), fg=DANGER if anu else TEXT)
        self.d_sub.config(text=f"{fecha[:16]}   ·   {metodo}")
        for i, (nom, cant, sub) in enumerate(con.execute(
                "SELECT nombre,cantidad,subtotal FROM items WHERE venta_id=?", (vid,))):
            self.dt.insert("", "end", values=(nom, cant, fmt(sub)), tags=("par",) if i % 2 else ())
        txt = f"TOTAL: {fmt(total)}"
        if metodo == "Efectivo":
            txt += f"\nPagó con: {fmt(pago)}     Vuelto: {fmt(vuelto)}"
        self.d_res.config(text=txt)

    def reimprimir(self):
        if self.sel_id:
            imprimir(ticket_texto(self.sel_id), f"ticket_{self.sel_id}")
            self.app.toast("Ticket enviado a la impresora", HEAD)

    def anular(self):
        if self.app.rol != "admin":
            self.app.toast("Solo el administrador puede anular ventas", AMBAR)
            return
        vid = self.sel_id
        if not vid:
            self.app.toast("Elegí una venta primero", DANGER)
            return
        if con.execute("SELECT anulada FROM ventas WHERE id=?", (vid,)).fetchone()[0]:
            self.app.toast("Esa venta ya está anulada", DANGER)
            return
        if not messagebox.askyesno("Anular", f"¿Anular la venta {vid}? Se devuelve el stock y no cuenta en la caja."):
            return
        for cod, cant in con.execute("SELECT codigo,cantidad FROM items WHERE venta_id=?", (vid,)).fetchall():
            con.execute("UPDATE productos SET stock=stock+? WHERE codigo=?", (cant, cod))
        con.execute("UPDATE ventas SET anulada=1 WHERE id=?", (vid,))
        con.commit()
        self.load(vid)
        self.app.upd_stock_btn()
        self.app.pedir_sync()
        self.app.toast(f"Venta {vid} anulada", HEAD)


# ---------- Pestaña Reportes ----------
class Reportes(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = envoltorio(self)
        bar = Tarjeta(wrap)
        bar.pack(fill="x")
        f = bar.inner
        hoy = datetime.now().strftime("%Y-%m-%d")
        self.d, self.h = tk.StringVar(value=hoy), tk.StringVar(value=hoy)
        etiqueta(f, "Desde").pack(side="left")
        entrada(f, 12, textvariable=self.d, width=11).pack(side="left", padx=6, ipady=3)
        etiqueta(f, "Hasta").pack(side="left", padx=(8, 0))
        entrada(f, 12, textvariable=self.h, width=11).pack(side="left", padx=6, ipady=3)
        Boton(f, "Ver", self.load, h=38, size=10, w=70).pack(side="left", padx=6)
        for txt, dias in (("Hoy", 0), ("7 días", 6), ("30 días", 29)):
            gris(f, txt, lambda x=dias: self.rango(x), h=38, size=10, w=80).pack(side="left", padx=3)

        stats = tk.Frame(wrap, bg=BG)
        stats.pack(fill="x", pady=(14, 0))
        stats.columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.st = {}
        for i, (k, tit) in enumerate((("n", "VENTAS REALIZADAS"), ("tot", "TOTAL VENDIDO"), ("gan", "GANANCIA"))):
            c = Tarjeta(stats)
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 7, 0 if i == 2 else 7))
            tk.Label(c.inner, text=tit, bg=CARD, fg=MUTED, font=(FONT, 9, "bold")).pack(anchor="w")
            self.st[k] = tk.Label(c.inner, text="—", bg=CARD, fg=OK if k == "gan" else TEXT, font=(FONT, 26, "bold"))
            self.st[k].pack(anchor="w")
        card = Tarjeta(wrap, auto=False)
        card.pack(fill="both", expand=True, pady=(14, 0))
        self.txt = tk.Text(card.inner, font=("Consolas", 12), wrap="none", bg=CARD, fg=TEXT, relief="flat",
                           highlightthickness=0)
        self.txt.pack(fill="both", expand=True)
        self.load()

    def al_mostrar(self):
        self.load()

    def rango(self, dias):
        h = datetime.now()
        self.h.set(h.strftime("%Y-%m-%d"))
        self.d.set((h - timedelta(days=dias)).strftime("%Y-%m-%d"))
        self.load()

    def load(self):
        a, b = self.d.get().strip(), self.h.get().strip()
        W = "v.anulada=0 AND substr(v.fecha,1,10) BETWEEN ? AND ?"
        n, tot = con.execute(f"SELECT COUNT(*),COALESCE(SUM(total),0) FROM ventas v WHERE {W}", (a, b)).fetchone()
        J = "FROM items i JOIN ventas v ON v.id=i.venta_id"
        gan = con.execute(f"SELECT COALESCE(SUM(i.subtotal-i.costo*i.cantidad),0) {J} WHERE {W}", (a, b)).fetchone()[0]
        sin = con.execute(f"SELECT COUNT(DISTINCT i.codigo) {J} WHERE {W} AND i.costo=0", (a, b)).fetchone()[0]
        base = f"SELECT MAX(i.nombre),SUM(i.cantidad),SUM(i.subtotal),SUM(i.subtotal-i.costo*i.cantidad) {J} WHERE {W} GROUP BY i.codigo"
        top = con.execute(base + " ORDER BY 2 DESC LIMIT 10", (a, b)).fetchall()
        topg = con.execute(base + " ORDER BY 4 DESC LIMIT 10", (a, b)).fetchall()
        self.st["n"].config(text=str(n))
        self.st["tot"].config(text=fmt(tot))
        self.st["gan"].config(text=fmt(gan))
        L = [f"Período: {a} al {b}" + (f"     Margen: {gan / tot * 100:.0f}%" if tot else ""), ""]
        if sin:
            L += [f"AVISO: {sin} producto(s) vendidos sin precio de compra;", "la ganancia real puede ser menor.", ""]
        L += ["MAS VENDIDOS (por cantidad)", "-" * 46]
        L += [f"{i}. {nom[:24]:<24} {int(c):>4} u.  {fmt(t):>9}" for i, (nom, c, t, g) in enumerate(top, 1)] or ["(sin ventas)"]
        L += ["", "QUE MAS GANANCIA DEJA", "-" * 46]
        L += [f"{i}. {nom[:24]:<24} {fmt(g):>12}" for i, (nom, c, t, g) in enumerate(topg, 1)] or ["(sin ventas)"]
        sm = sin_movimiento()
        L += ["", f"SIN VENTAS HACE {cfg('dias_sin_venta')}+ DIAS ({len(sm)})", "-" * 46]
        for cod, nom, st, d, ult in sm[:30]:
            L.append(f"{nom[:24]:<24} stock {st:g}".ljust(36) + (f"hace {d} dias" if ult else f"sin ventas (cargado hace {d} dias)"))
        if len(sm) > 30:
            L.append(f"... y {len(sm) - 30} mas")
        if not sm:
            L.append("(todo se esta moviendo bien)")
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(L))
        self.txt.config(state="disabled")


# ---------- Pestaña Cierre de caja ----------
class Cierre(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app, self.ult_id, self.desde, self.por, self.gan = app, None, "", {}, 0
        wrap = envoltorio(self)
        izq = Tarjeta(wrap, width=400)
        izq.pack(side="left", anchor="n")
        der = Tarjeta(wrap, auto=False)
        der.pack(side="right", fill="both", expand=True, padx=(16, 0))
        titulo(izq.inner, "Cierre de caja")
        self.lbl_desde = tk.Label(izq.inner, text="", bg=CARD, fg=MUTED, font=(FONT, 10), wraplength=360, justify="left")
        self.lbl_desde.pack(anchor="w")
        f = tk.Frame(izq.inner, bg=CARD)
        f.pack(fill="x", pady=(6, 0))
        f.columnconfigure(0, weight=1)
        self.ini, self.ret, self.cont = tk.StringVar(), tk.StringVar(value="0"), tk.StringVar()
        for i, (lbl, var) in enumerate((("Efectivo inicial (lo que había en caja)", self.ini),
                                        ("Retiros / gastos del turno", self.ret),
                                        ("Efectivo CONTADO ahora", self.cont))):
            e = campo(f, lbl, var, i, 0)
            e.bind("<KeyRelease>", lambda ev: self.mostrar())
        Boton(izq.inner, "CERRAR CAJA e imprimir", self.cerrar, color=OK, hover=OK_H, h=56, size=14).pack(fill="x", pady=(14, 0))
        self.out = tk.Label(der.inner, font=("Consolas", 11), justify="left", anchor="nw", bg=CARD, fg=TEXT)
        self.out.pack(fill="both", expand=True)
        self.al_mostrar()

    def al_mostrar(self):
        ult = con.execute("SELECT id,fecha,contado FROM cierres ORDER BY id DESC LIMIT 1").fetchone()
        self.desde = ult[1] if ult else datetime.now().strftime("%Y-%m-%d 00:00:00")
        self.por = dict(con.execute("SELECT metodo,SUM(total) FROM ventas WHERE anulada=0 AND fecha>? GROUP BY metodo", (self.desde,)))
        self.gan = con.execute("SELECT COALESCE(SUM(i.subtotal-i.costo*i.cantidad),0) FROM items i JOIN ventas v ON v.id=i.venta_id "
                               "WHERE v.anulada=0 AND v.fecha>?", (self.desde,)).fetchone()[0]
        nid = ult[0] if ult else 0
        if nid != self.ult_id:
            self.ult_id = nid
            self.ini.set(f"{ult[2]:g}".replace(".", ",") if ult else "0")
            self.ret.set("0")
            self.cont.set("")
        self.lbl_desde.config(text=f"Ventas desde el último cierre ({self.desde[:16]})")
        self.mostrar()

    def calc(self):
        ini, ret = num(self.ini.get()), num(self.ret.get())
        efe = self.por.get("Efectivo", 0)
        return ini, ret, efe, ini + efe - ret

    def armar(self):
        a = ancho()
        ini, ret, efe, esp = self.calc()
        L = [nombre().center(a), "CIERRE DE CAJA".center(a), "-" * a,
             linea("Desde", self.desde[:16]), linea("Hasta", datetime.now().strftime("%Y-%m-%d %H:%M")), "-" * a]
        L += [linea(m, fmt(v)) for m, v in self.por.items()] or [linea("(sin ventas)")]
        L += [linea("TOTAL VENDIDO", fmt(sum(self.por.values()))), "-" * a,
              linea("Efectivo inicial", fmt(ini)), linea("+ Ventas en efectivo", fmt(efe)),
              linea("- Retiros/gastos", fmt(ret)), linea("Efectivo esperado", fmt(esp))]
        if self.cont.get().strip():
            dif = num(self.cont.get()) - esp
            L += [linea("Efectivo contado", fmt(num(self.cont.get()))),
                  linea("SOBRA" if dif >= 0 else "FALTA", fmt(abs(dif)))]
        L += ["-" * a]
        if self.app.rol == "admin":
            L += [linea("Ganancia estimada", fmt(self.gan))]
        return "\n".join(L)

    def mostrar(self):
        self.out.config(text=self.armar())

    def cerrar(self):
        if not self.cont.get().strip():
            self.app.toast("Escribí cuánto efectivo contaste", DANGER)
            return
        ini, ret, efe, esp = self.calc()
        cont = num(self.cont.get())
        texto = self.armar()
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        con.execute("INSERT INTO cierres(fecha,desde,inicial,efectivo,retiros,esperado,contado,diferencia,ganancia) VALUES(?,?,?,?,?,?,?,?,?)",
                    (ahora, self.desde, ini, efe, ret, esp, cont, cont - esp, self.gan))
        con.commit()
        imprimir(texto, "cierre_" + ahora.replace(":", "").replace(" ", "_").replace("-", ""))
        self.al_mostrar()
        self.app.toast("Caja cerrada ✓", OK)


# ---------- Pestaña Sheets (conexión con Google Sheets) ----------
class Sheets(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = envoltorio(self)
        izq = Tarjeta(wrap, width=470)
        izq.pack(side="left", anchor="n")
        der = Tarjeta(wrap, auto=False)
        der.pack(side="right", fill="both", expand=True, padx=(16, 0))

        titulo(izq.inner, "Conexión con Google Sheets")
        self.lbl_estado = tk.Label(izq.inner, text="", bg=CARD, font=(FONT, 11, "bold"), anchor="w", justify="left",
                                   wraplength=430)
        self.lbl_estado.pack(fill="x", pady=(0, 6))
        f = tk.Frame(izq.inner, bg=CARD)
        f.pack(fill="x")
        f.columnconfigure((0, 1), weight=1, uniform="c")
        self.v_id = tk.StringVar(value=cfg("sheets_id"))
        self.v_caja = tk.StringVar(value=cfg("caja_id"))
        self.v_seg = tk.StringVar(value=cfg("sheets_seg"))
        campo(f, "Link o ID de la planilla", self.v_id, 0, 0, 2, size=11)
        campo(f, "Nombre de esta caja (ej: C1)", self.v_caja, 1, 0, size=11)
        campo(f, "Sincronizar cada (segundos)", self.v_seg, 1, 1, size=11)
        self.lbl_json = tk.Label(izq.inner, text="", bg=CARD, fg=MUTED, font=(FONT, 10), anchor="w", justify="left",
                                 wraplength=430)
        self.lbl_json.pack(fill="x", pady=(8, 0))
        gris(izq.inner, "Elegir archivo de claves (.json)", self.elegir_json, h=42, size=10).pack(fill="x", pady=(6, 0))
        self.on = tk.BooleanVar(value=cfg("sheets_on") == "1")
        tk.Checkbutton(izq.inner, text="Sincronizar automáticamente", variable=self.on, command=self.guardar_campos,
                       bg=CARD, activebackground=CARD, fg=TEXT, activeforeground=TEXT, selectcolor=INPUT,
                       font=(FONT, 10), highlightthickness=0).pack(anchor="w", pady=(10, 4))
        Boton(izq.inner, "1) Guardar y probar conexión", self.probar, h=46, size=11).pack(fill="x", pady=(4, 0))
        Boton(izq.inner, "2) Preparar planilla (primera vez)", self.preparar, color=SUAVE, hover=SUAVE_H, fg=SUAVE_T,
              h=46, size=11).pack(fill="x", pady=(8, 0))
        Boton(izq.inner, "Reparar planilla (fórmulas y formato)", self.reparar, color=GRIS, hover=GRIS_H, fg=TEXT,
              h=42, size=10).pack(fill="x", pady=(8, 0))
        Boton(izq.inner, "Sincronizar ahora", self.ahora, color=OK, hover=OK_H, h=46, size=11).pack(fill="x", pady=(8, 0))

        titulo(der.inner, "Estado")
        self.lbl_pend = tk.Label(der.inner, text="", bg=CARD, fg=TEXT, font=(FONT, 11), anchor="w", justify="left")
        self.lbl_pend.pack(fill="x")
        tk.Label(der.inner, text=("Cómo se usa la planilla:\n"
                                  "• Hoja Productos: precios, costos, promos y stock mínimo. Editá ahí o en el programa.\n"
                                  "• Si borrás una fila de Productos, el producto se borra también del programa (y al revés).\n"
                                  "• Hoja Ingresos: para cargar mercadería, agregá una fila (Código y Cantidad).\n"
                                  "• Stock actual (columna H): es una fórmula. No la toques. Si no anda, tocá Reparar planilla.\n"
                                  "• Hoja Movimientos: cada venta se agrega sola."),
                 bg=CARD, fg=MUTED, font=(FONT, 10), anchor="w", justify="left", wraplength=520).pack(fill="x", pady=(10, 8))
        etiqueta(der.inner, "Últimos eventos").pack(anchor="w")
        self.log = tk.Text(der.inner, font=("Consolas", 10), wrap="word", bg=INPUT, fg=TEXT, relief="flat",
                           highlightthickness=0, height=8, padx=8, pady=6)
        self.log.pack(fill="both", expand=True, pady=(4, 0))
        self.al_mostrar()

    def al_mostrar(self):
        self.on.set(cfg("sheets_on") == "1")
        mail = mail_del_json()
        self.lbl_json.config(text=(f"Archivo de claves: OK\nCompartí la planilla (como Editor) con: {mail}" if mail else
                                   "Falta el archivo de claves. Tocá el botón de abajo y elegí el .json que descargaste "
                                   "de Google Cloud."))
        s = self.app.sync
        if s["corriendo"]:
            t, c = "● Sincronizando...", PRIMARY
        elif s["error"]:
            t, c = "● " + s["error"], DANGER
        elif cfg("sheets_preparada") != "1":
            t, c = "● Falta preparar la planilla (paso 2).", AMBAR
        elif cfg("sheets_on") != "1":
            t, c = "● Sincronización apagada.", MUTED
        elif s["ultimo"]:
            t, c = f"● Conectado. Última sincronización: {s['ultimo']}", OK
        else:
            t, c = "● Conectado. Esperando la primera sincronización...", MUTED
        self.lbl_estado.config(text=t, fg=c)
        v = con.execute("SELECT COUNT(*) FROM ventas WHERE subida=0 AND anulada=0").fetchone()[0]
        i = con.execute("SELECT COUNT(*) FROM cola_ingresos WHERE subida=0").fetchone()[0]
        p = con.execute("SELECT COUNT(*) FROM productos WHERE sucio=1").fetchone()[0]
        self.lbl_pend.config(text=f"Pendiente de subir:  {v} venta(s)   ·   {i} ingreso(s) de stock   ·   {p} producto(s)")
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", "\n".join(self.app.sync_log[-30:][::-1]) or "(todavía no pasó nada)")
        self.log.config(state="disabled")

    def guardar_campos(self):
        set_cfg("sheets_id", extraer_id(self.v_id.get()))
        set_cfg("caja_id", self.v_caja.get().strip() or "C1")
        m = self.v_seg.get().strip()
        set_cfg("sheets_seg", m if m.isdigit() and int(m) >= 15 else "60")
        set_cfg("sheets_on", "1" if self.on.get() else "0")

    def elegir_json(self):
        ruta = filedialog.askopenfilename(title="Archivo de claves de Google", filetypes=[("JSON", "*.json")])
        if not ruta:
            return
        try:
            with open(ruta, encoding="utf-8") as f:
                d = json.load(f)
            if "client_email" not in d or "private_key" not in d:
                raise ValueError
        except (OSError, ValueError):
            self.app.toast("Ese archivo no parece ser una clave de cuenta de servicio", DANGER, 5000)
            return
        shutil.copy(ruta, RUTA_JSON)
        self.al_mostrar()
        self.app.toast("Archivo de claves guardado ✓", OK)

    def probar(self):
        self.guardar_campos()
        if not cfg("sheets_id"):
            self.app.toast("Pegá el link de la planilla", DANGER)
            return
        self.app.lanzar_sync("probar", manual=True)
        self.al_mostrar()

    def reparar(self):
        self.guardar_campos()
        if not cfg("sheets_id"):
            self.app.toast("Pegá el link de la planilla", DANGER)
            return
        self.app.lanzar_sync("reparar", manual=True)
        self.al_mostrar()

    def preparar(self):
        self.guardar_campos()
        if not cfg("sheets_id"):
            self.app.toast("Pegá el link de la planilla", DANGER)
            return
        if cfg("sheets_preparada") == "1":
            self.app.toast("La planilla ya está preparada. Si algo no anda, usá Reparar planilla", AMBAR, 5000)
            return
        if messagebox.askyesno("Preparar planilla",
                               "Se van a crear las hojas Productos, Ingresos y Movimientos (si no existen) y se "
                               "subirán tus productos con su stock actual.\n\n¿Continuar?"):
            self.app.lanzar_sync("preparar", manual=True)
            self.al_mostrar()

    def ahora(self):
        self.guardar_campos()
        if cfg("sheets_preparada") != "1":
            self.app.toast("Primero hacé los pasos 1 y 2", AMBAR)
            return
        self.app.lanzar_sync("sync", manual=True)
        self.al_mostrar()


# ---------- Pestaña Configuración ----------
CFG_CAMPOS = ("nombre", "t_nombre", "t_l2", "t_l3", "t_num", "t_total", "t_pie", "t_pie2", "dias_sin_venta")
ANCHOS = ("80 mm (40 caracteres)", "58 mm (32 caracteres)")


class Configuracion(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = envoltorio(self)
        A = tk.Frame(wrap, bg=BG)
        A.pack(side="left", fill="y")
        B = tk.Frame(wrap, bg=BG)
        B.pack(side="left", fill="y", padx=16)
        C = Tarjeta(wrap, auto=False)
        C.pack(side="left", fill="both", expand=True)
        self.v = {k: tk.StringVar(value=cfg(k)) for k in CFG_CAMPOS}

        a1 = Tarjeta(A, width=330)
        a1.pack(fill="x")
        titulo(a1.inner, "Tu kiosco")
        f = tk.Frame(a1.inner, bg=CARD)
        f.pack(fill="x")
        f.columnconfigure(0, weight=1)
        campo(f, "Nombre del kiosco", self.v["nombre"], 0, 0)
        campo(f, "Aviso: sin ventas hace (días)", self.v["dias_sin_venta"], 1, 0)

        a2 = Tarjeta(A, width=330)
        a2.pack(fill="x", pady=(14, 0))
        titulo(a2.inner, "Seguridad (PIN)")
        self.lbl_pin = tk.Label(a2.inner, text="", bg=CARD, fg=MUTED, font=(FONT, 10), wraplength=290, justify="left")
        self.lbl_pin.pack(anchor="w")
        f3 = tk.Frame(a2.inner, bg=CARD)
        f3.pack(fill="x")
        f3.columnconfigure(0, weight=1)
        self.pin_a, self.pin_e = tk.StringVar(), tk.StringVar()
        campo(f3, "PIN administrador (4 a 8 números)", self.pin_a, 0, 0, show="•")
        campo(f3, "PIN empleado (4 a 8 números)", self.pin_e, 1, 0, show="•")
        rojo(a2.inner, "Quitar protección con PIN", self.quitar_pins, h=40, size=10).pack(fill="x", pady=(8, 0))
        Boton(A, "Guardar cambios", self.guardar, color=OK, hover=OK_H, h=56, size=14, bgc=BG).pack(fill="x", pady=(14, 0))

        b1 = Tarjeta(B, width=380)
        b1.pack(fill="x")
        titulo(b1.inner, "Textos del ticket")
        f2 = tk.Frame(b1.inner, bg=CARD)
        f2.pack(fill="x")
        f2.columnconfigure((0, 1), weight=1, uniform="c")
        campo(f2, "Nombre en el ticket (vacío = nombre del kiosco)", self.v["t_nombre"], 0, 0, 2)
        campo(f2, "Línea 2 (ej: dirección)", self.v["t_l2"], 1, 0, 2)
        campo(f2, "Línea 3 (ej: teléfono o CUIT)", self.v["t_l3"], 2, 0, 2)
        campo(f2, 'Texto de "Ticket Nro"', self.v["t_num"], 3, 0)
        campo(f2, 'Texto de "TOTAL"', self.v["t_total"], 3, 1)
        campo(f2, "Mensaje final", self.v["t_pie"], 4, 0, 2)
        campo(f2, "Mensaje final 2 (opcional)", self.v["t_pie2"], 5, 0, 2)

        titulo(C.inner, "Impresión")
        etiqueta(C.inner, "Ancho del papel de la impresora").pack(anchor="w")
        self.ancho = tk.StringVar(value=ANCHOS[1] if ancho() == 32 else ANCHOS[0])
        ttk.Combobox(C.inner, textvariable=self.ancho, values=ANCHOS, state="readonly",
                     font=(FONT, 11)).pack(fill="x", ipady=3, pady=(4, 8))
        self.auto = tk.BooleanVar(value=cfg("auto_ticket") == "1")
        tk.Checkbutton(C.inner, text="Imprimir ticket automáticamente al cobrar", variable=self.auto, bg=CARD,
                       activebackground=CARD, fg=TEXT, activeforeground=TEXT, font=(FONT, 10), selectcolor=INPUT,
                       highlightthickness=0).pack(anchor="w", pady=(0, 10))
        etiqueta(C.inner, "Vista previa del ticket").pack(anchor="w", pady=(0, 4))
        self.prev = tk.Label(C.inner, font=("Consolas", 10), justify="left", anchor="nw", bg="white", fg="#111827",
                             relief="solid", bd=1, padx=12, pady=12)
        self.prev.pack(anchor="nw")
        gris(C.inner, "Imprimir ticket de prueba", self.prueba, h=44, size=10).pack(anchor="nw", pady=(12, 0), fill="x")
        for v in self.v.values():
            v.trace_add("write", lambda *a: self.preview())
        self.ancho.trace_add("write", lambda *a: self.preview())
        self.preview()
        self.estado_pin()

    def estado_pin(self):
        if cfg("pin_admin"):
            t = ("Protección ACTIVA: se pide PIN al abrir y al tocar Bloquear. "
                 + ("El empleado solo ve Caja, Stock, Ventas y Cierre." if cfg("pin_empleado")
                    else "Todavía no hay PIN de empleado."))
        else:
            t = "Sin PIN: cualquiera que abra el programa entra como administrador."
        self.lbl_pin.config(text=t)

    def conf(self):
        d = {k: v.get() for k, v in self.v.items()}
        d["ancho"] = "32" if self.ancho.get().startswith("58") else "40"
        return d

    def texto_prueba(self):
        items = [("Alfajor", 2, 1200, 600), ("Gaseosa 500ml", 1, 2500, 0)]
        return armar_ticket(1, datetime.now().strftime("%Y-%m-%d %H:%M"), items, 4300, "Efectivo", 5000, 700, self.conf())

    def preview(self):
        self.prev.config(text=self.texto_prueba())

    def prueba(self):
        imprimir(self.texto_prueba(), "prueba")
        self.app.toast("Ticket de prueba enviado", HEAD)

    def guardar_pins(self):
        a, e = self.pin_a.get().strip(), self.pin_e.get().strip()
        if not a and not e:
            return True
        for p in (a, e):
            if p and not (p.isdigit() and 4 <= len(p) <= 8):
                self.app.toast("El PIN debe tener entre 4 y 8 números", DANGER)
                return False
        adm = hpin(a) if a else cfg("pin_admin")
        emp = hpin(e) if e else cfg("pin_empleado")
        if not adm:
            self.app.toast("Primero definí el PIN del administrador", DANGER)
            return False
        if emp and emp == adm:
            self.app.toast("El PIN del empleado tiene que ser distinto al del administrador", DANGER)
            return False
        if a:
            set_cfg("pin_admin", adm)
            codigo = secrets.token_hex(3).upper()
            set_cfg("pin_rec", hpin(codigo))
            messagebox.showinfo("Código de recuperación",
                                f"Tu código de recuperación es:\n\n{codigo}\n\nAnotalo y guardalo en un lugar seguro. "
                                "Si te olvidás el PIN del administrador es la única forma de volver a entrar.")
        if e:
            set_cfg("pin_empleado", emp)
        self.pin_a.set("")
        self.pin_e.set("")
        return True

    def quitar_pins(self):
        if not cfg("pin_admin"):
            self.app.toast("No hay PIN configurado", HEAD)
            return
        if messagebox.askyesno("Quitar PIN", "¿Quitar la protección con PIN? Cualquiera podrá entrar como administrador."):
            for k in ("pin_admin", "pin_empleado", "pin_rec"):
                set_cfg(k, "")
            self.estado_pin()
            self.app.aplicar_rol()
            self.app.toast("Protección con PIN desactivada", HEAD)

    def guardar(self):
        if not self.guardar_pins():
            return
        for k, v in self.conf().items():
            set_cfg(k, v.strip() if k != "ancho" else v)
        if not cfg("dias_sin_venta").isdigit():
            set_cfg("dias_sin_venta", "21")
            self.v["dias_sin_venta"].set("21")
        set_cfg("auto_ticket", "1" if self.auto.get() else "0")
        self.app.lbl_nombre.config(text=nombre())
        self.app.ticket.set(self.auto.get())
        self.app.aplicar_rol()
        self.estado_pin()
        self.app.toast("Configuración guardada ✓", OK)


if __name__ == "__main__":
    App().mainloop()
