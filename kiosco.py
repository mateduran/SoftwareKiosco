import os
import sqlite3
import sys
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta
from tkinter import messagebox, ttk


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
""")


def addcol(tabla, col, ddl):
    if col not in [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]:
        con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {ddl}")


addcol("productos", "costo", "REAL DEFAULT 0")
addcol("productos", "stock", "REAL DEFAULT 0")
addcol("productos", "stock_min", "REAL DEFAULT 0")
addcol("items", "costo", "REAL DEFAULT 0")
addcol("ventas", "anulada", "INTEGER DEFAULT 0")
con.commit()

# ---------- Configuración guardada en la base ----------
DEFAULTS = {"nombre": "MI KIOSCO", "t_nombre": "", "t_l2": "", "t_l3": "", "t_num": "Ticket Nro",
            "t_total": "TOTAL", "t_pie": "Gracias por su compra!", "t_pie2": "", "ancho": "40", "auto_ticket": "0"}
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


PROMOS = {"ninguna": "Sin promo", "2da50": "Segunda unidad al 50%", "2x1": "2x1",
          "3x2": "3x2", "pct": "Descuento %"}
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


def descuento(precio, cant, promo, valor):
    if promo == "2da50":
        return (cant // 2) * precio * 0.5
    if promo == "2x1":
        return (cant // 2) * precio
    if promo == "3x2":
        return (cant // 3) * precio
    if promo == "pct":
        return cant * precio * valor / 100
    return 0


def bajos():
    return con.execute("SELECT codigo,nombre,stock,stock_min FROM productos "
                       "WHERE stock_min>0 AND stock<=stock_min ORDER BY nombre").fetchall()


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
BG, CARD, HEAD = "#EEF1F6", "#FFFFFF", "#1E293B"
PRIMARY, PRIMARY_H = "#2563EB", "#1D4ED8"
OK, OK_H = "#16A34A", "#15803D"
DANGER, DANGER_L, DANGER_LH = "#DC2626", "#FEE2E2", "#FECACA"
TEXT, MUTED, LINE = "#1F2937", "#6B7280", "#E5E9F0"
SOMBRA, SOMBRA2 = "#CDD4DF", "#E1E6EE"
GRIS, GRIS_H = "#EEF1F6", "#E2E7EF"
SUAVE, SUAVE_H, SUAVE_T = "#E0E7FF", "#C7D2FE", "#3730A3"
AMBAR, AMBAR_H = "#D97706", "#B45309"


def aplicar_tema(root):
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=TEXT, font=(FONT, 11))
    st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=38,
                 font=(FONT, 12), borderwidth=0, relief="flat", bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
    st.configure("Treeview.Heading", background="#F3F5F9", foreground=MUTED, font=(FONT, 10, "bold"),
                 relief="flat", padding=(8, 8))
    st.map("Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", TEXT)])
    st.map("Treeview.Heading", background=[("active", "#E8ECF3")])
    st.configure("TButton", background=PRIMARY, foreground="white", font=(FONT, 11, "bold"),
                 padding=(14, 8), borderwidth=0, focusthickness=0, focuscolor=PRIMARY)
    st.map("TButton", background=[("active", PRIMARY_H), ("pressed", PRIMARY_H)])
    st.configure("Peligro.TButton", background=DANGER)
    st.map("Peligro.TButton", background=[("active", "#B91C1C"), ("pressed", "#B91C1C")])
    st.configure("TEntry", fieldbackground="#F3F5F9", padding=6)
    st.configure("TCombobox", padding=6)
    st.configure("TLabelframe", background=BG, bordercolor=LINE)
    st.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=(FONT, 10, "bold"))


def rrect(c, x1, y1, x2, y2, r, **kw):
    """Rectángulo con esquinas redondeadas dibujado en un Canvas."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Boton(tk.Canvas):
    """Botón redondeado con sombra, efecto al pasar el mouse y al apretar."""

    def __init__(self, master, text, command, color=PRIMARY, hover=PRIMARY_H, fg="white",
                 h=44, size=12, r=12, bgc=CARD, sombra=True, w=10):
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
    t.tag_configure("par", background="#F8FAFC")
    sb.pack(side="right", fill="y")
    t.pack(side="left", fill="both", expand=True)
    return f, t


class Pestana(tk.Canvas):
    """Pestaña estilo navegador (todas en la misma ventana)."""

    def __init__(self, master, text, command, bgc=HEAD):
        self.fnt = tkfont.Font(family=FONT, size=11, weight="bold")
        super().__init__(master, width=self.fnt.measure(text) + 40, height=44, bg=bgc, highlightthickness=0, cursor="hand2")
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
        self.config(width=self.fnt.measure(t) + 40)
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

    def __init__(self, master, bgc=BG, auto=True, r=16, pad=14, **kw):
        super().__init__(master, bg=bgc, highlightthickness=0, **kw)
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
    return tk.Entry(master, font=(FONT, size), bg="#F3F5F9", fg=TEXT, relief="flat", insertbackground=PRIMARY,
                    highlightthickness=2, highlightbackground="#DDE3EC", highlightcolor=PRIMARY, **kw)




# ---------- Ventana principal: pestañas + caja ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(nombre() + " - Caja")
        self.geometry("1240x760")
        self.minsize(1100, 680)
        self.configure(bg=BG)
        aplicar_tema(self)
        self.cart = {}  # codigo -> [nombre, precio, cant, promo, valor, costo]
        self.total = 0.0
        self.ticket = tk.BooleanVar(value=cfg("auto_ticket") == "1")
        self.metodo = tk.StringVar(value="Efectivo")
        self.ultimo = None       # vuelto de la última venta (se muestra hasta el próximo producto)
        self.cobrando = False
        self.t_fin = None
        self._toast = None
        self.actual = "Caja"
        self.paginas, self.tabs = {}, {}

        head = tk.Frame(self, bg=HEAD, height=62)
        head.pack(fill="x")
        head.pack_propagate(False)
        self.lbl_nombre = tk.Label(head, text=nombre(), bg=HEAD, fg="white", font=(FONT, 18, "bold"))
        self.lbl_nombre.pack(side="left", padx=(22, 10))
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
        self.agregar("Cierre de caja", Cierre(self.cont, self), "<F6>")
        self.agregar("Configuración", Configuracion(self.cont, self), "<F7>")

        self.bind("<F12>", lambda e: self.actual == "Caja" and self.cobrar())
        self.bind("<Escape>", lambda e: self.actual == "Caja" and self.cancelar())
        self.bind("<FocusIn>", lambda e: self.scan.focus_set() if (e.widget is self and self.actual == "Caja") else None)
        self.elegir("Efectivo")
        self.refresh()
        self.ir("Caja")
        self.upd_stock_btn()
        self.after(600, self.aviso_stock)

    # ----- pestañas -----
    def agregar(self, nombre_tab, pagina, tecla):
        pagina.grid(row=0, column=0, sticky="nsew")
        self.paginas[nombre_tab] = pagina
        t = Pestana(self.barra, nombre_tab, lambda n=nombre_tab: self.ir(n))
        t.pack(side="left", padx=2, pady=(18, 0))
        self.tabs[nombre_tab] = t
        self.bind(tecla, lambda e, n=nombre_tab: self.ir(n))

    def ir(self, n):
        if n not in self.paginas:
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
        Boton(bar, "Cancelar venta  (Esc)", self.cancelar, color=DANGER_L, hover=DANGER_LH, fg="#B91C1C", h=44,
              size=10).grid(row=0, column=2, sticky="ew", padx=(5, 0))
        fr, self.tree = lista(cart.inner, (("nombre", "Producto", 250, "w", True), ("cant", "Cant", 55, "e", False),
                                           ("precio", "Precio", 95, "e", False), ("desc", "Descuento", 105, "e", False),
                                           ("sub", "Subtotal", 110, "e", False)))
        fr.pack(fill="both", expand=True)
        self.tree.tag_configure("hecho", background="#DCFCE7", foreground="#15803D")
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
                       activebackground=CARD, fg=TEXT, font=(FONT, 10), selectcolor="white",
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

    def aviso_stock(self):
        b = bajos()
        if b:
            messagebox.showwarning("Poco stock", "Productos con poco stock:\n\n" +
                                   "\n".join(f"• {n}: quedan {s:g}" for _, n, s, _ in b[:15]))

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
        r = con.execute("SELECT nombre,precio,promo,valor,costo FROM productos WHERE codigo=?", (txt,)).fetchone()
        if not r:
            # producto nuevo: te lleva a la pestaña Productos con el código ya cargado
            self.paginas["Productos"].nuevo_con_codigo(txt)
            self.ir("Productos")
            self.toast("Código nuevo: completá nombre y precio y tocá Guardar", AMBAR, 5000)
            return
        self.ultimo = None
        if txt in self.cart:
            self.cart[txt][2] += cant
        else:
            self.cart[txt] = [r[0], r[1], cant, r[2], r[3], r[4]]
        self.refresh()
        self.tree.selection_set(txt)
        self.tree.see(txt)
        self.scan.focus_set()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.total, ahorro = 0.0, 0.0
        for i, (cod, (nom, pre, cant, promo, val, _)) in enumerate(self.cart.items()):
            d = descuento(pre, cant, promo, val)
            sub = pre * cant - d
            self.total += sub
            ahorro += d
            self.tree.insert("", "end", iid=cod, tags=("par",) if i % 2 else (),
                             values=(nom, cant, fmt(pre), "-" + fmt(d) if d else "", fmt(sub)))
        self.lbl_total.config(text=fmt(self.total))
        self.lbl_ahorro.config(text=f"Ahorro por promos: {fmt(ahorro)}" if ahorro else " ")
        if self.cart:
            self.vacio.place_forget()
        else:
            self.vacio.place(relx=0.5, rely=0.45, anchor="center")
        self.upd_vuelto()

    def upd_vuelto(self):
        txt = self.pago.get().strip()
        big, small = (FONT, 32, "bold"), (FONT, 22, "bold")
        if not self.cart and self.ultimo is not None:
            face, fg, tit, val, f = "#DCFCE7", "#15803D", "VUELTO A ENTREGAR", fmt(self.ultimo), big
        elif self.metodo.get() != "Efectivo":
            face, fg, tit, val, f = "#F3F5F9", MUTED, "VUELTO", "No aplica", small
        elif not txt or not self.cart:
            face, fg, tit, val, f = "#F3F5F9", MUTED, "VUELTO", "—", big
        else:
            v = num(txt) - self.total
            if v >= 0:
                face, fg, tit, val, f = "#DCFCE7", "#15803D", "VUELTO", fmt(v), big
            else:
                face, fg, tit, val, f = "#FEE2E2", "#B91C1C", "FALTA", fmt(-v), big
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
        for cod, (nom, pre, cant, promo, val, costo) in self.cart.items():
            d = descuento(pre, cant, promo, val)
            cur.execute("INSERT INTO items(venta_id,codigo,nombre,cantidad,precio,descuento,subtotal,costo) VALUES(?,?,?,?,?,?,?,?)",
                        (vid, cod, nom, cant, pre, d, pre * cant - d, costo))
            cur.execute("UPDATE productos SET stock=stock-? WHERE codigo=?", (cant, cod))  # descuenta stock
        con.commit()
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


def campo(parent, texto, var, row, col, span=1, size=13):
    f = tk.Frame(parent, bg=CARD)
    f.grid(row=row, column=col, columnspan=span, sticky="ew", padx=4, pady=3)
    tk.Label(f, text=texto, bg=CARD, fg=MUTED, font=(FONT, 9, "bold")).pack(anchor="w")
    e = entrada(f, size, textvariable=var)
    e.pack(fill="x", ipady=4)
    return e


def etiqueta(parent, texto):
    return tk.Label(parent, text=texto, bg=CARD, fg=MUTED, font=(FONT, 9, "bold"))


def gris(parent, texto, cmd, **kw):
    return Boton(parent, texto, cmd, color=GRIS, hover=GRIS_H, fg=TEXT, **kw)


def rojo(parent, texto, cmd, **kw):
    return Boton(parent, texto, cmd, color=DANGER_L, hover=DANGER_LH, fg="#B91C1C", **kw)


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

        fr, self.t = lista(izq.inner, (("codigo", "Código", 130, "w", False), ("nombre", "Nombre", 250, "w", True),
                                       ("precio", "Precio", 90, "e", False), ("costo", "Costo", 90, "e", False),
                                       ("stock", "Stock", 70, "e", False), ("promo", "Promo", 180, "w", False)))
        fr.pack(fill="both", expand=True)
        self.t.bind("<<TreeviewSelect>>", self.sel)

        titulo(der.inner, "Producto")
        f = tk.Frame(der.inner, bg=CARD)
        f.pack(fill="x")
        f.columnconfigure((0, 1), weight=1, uniform="c")
        self.v = {k: tk.StringVar() for k in ("codigo", "nombre", "precio", "costo", "stock", "stock_min", "valor")}
        self.promo = tk.StringVar(value=PROMOS["ninguna"])
        self.e_codigo = campo(f, "Código de barras", self.v["codigo"], 0, 0, 2)
        self.e_nombre = campo(f, "Nombre", self.v["nombre"], 1, 0, 2)
        campo(f, "Precio de venta", self.v["precio"], 2, 0)
        campo(f, "Precio de compra (costo)", self.v["costo"], 2, 1)
        campo(f, "Stock actual", self.v["stock"], 3, 0)
        campo(f, "Stock mínimo (aviso)", self.v["stock_min"], 3, 1)
        pf = tk.Frame(f, bg=CARD)
        pf.grid(row=4, column=0, sticky="ew", padx=4, pady=3)
        etiqueta(pf, "Promo").pack(anchor="w")
        ttk.Combobox(pf, textvariable=self.promo, values=list(PROMOS.values()), state="readonly",
                     font=(FONT, 11)).pack(fill="x", ipady=3)
        campo(f, "% (si es Descuento %)", self.v["valor"], 4, 1)
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
        for i, (cod, nom, pre, costo, stock, promo, val) in enumerate(con.execute(
                "SELECT codigo,nombre,precio,costo,stock,promo,valor FROM productos "
                "WHERE codigo LIKE ? OR nombre LIKE ? ORDER BY nombre", (q, q))):
            p = PROMOS.get(promo, "") + (f" {val:g}%" if promo == "pct" else "")
            self.t.insert("", "end", iid=cod, tags=("par",) if i % 2 else (),
                          values=(cod, nom, fmt(pre), fmt(costo) if costo else "-", f"{stock:g}",
                                  "" if promo == "ninguna" else p))

    def sel(self, _=None):
        s = self.t.selection()
        if not s:
            return
        cod, nom, pre, costo, stock, smin, promo, val = con.execute(
            "SELECT codigo,nombre,precio,costo,stock,stock_min,promo,valor FROM productos WHERE codigo=?", (s[0],)).fetchone()
        for k, x in (("codigo", cod), ("nombre", nom), ("precio", f"{pre:g}".replace(".", ",")),
                     ("costo", f"{costo:g}".replace(".", ",")), ("stock", f"{stock:g}"),
                     ("stock_min", f"{smin:g}"), ("valor", f"{val:g}")):
            self.v[k].set(x)
        self.promo.set(PROMOS.get(promo, PROMOS["ninguna"]))

    def nuevo(self):
        for v in self.v.values():
            v.set("")
        self.promo.set(PROMOS["ninguna"])
        self.volver = False

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
        con.execute("""INSERT INTO productos(codigo,nombre,precio,costo,stock,stock_min,promo,valor) VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, precio=excluded.precio,
                       costo=excluded.costo, stock=excluded.stock, stock_min=excluded.stock_min,
                       promo=excluded.promo, valor=excluded.valor""",
                    (cod, nom, pre, num(self.v["costo"].get()), num(self.v["stock"].get()),
                     num(self.v["stock_min"].get()), REV[self.promo.get()], num(self.v["valor"].get())))
        con.commit()
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
            con.commit()
            self.load()
            self.nuevo()
            self.app.upd_stock_btn()
            self.app.toast("Producto eliminado", HEAD)

    def masivo(self):
        txt = self.pct.get().strip()
        if not txt:
            self.app.toast("Escribí el porcentaje (ej: 10 sube 10%, -5 baja 5%)", DANGER)
            return
        p = num(txt)
        if not messagebox.askyesno("Cambio masivo", f"¿Cambiar TODOS los precios {p:+g}%?"):
            return
        con.execute("UPDATE productos SET precio=ROUND(precio*(1+?/100.0))", (p,))
        con.commit()
        self.pct.set("")
        self.load()
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
                       activebackground=CARD, font=(FONT, 10), highlightthickness=0).pack(side="left", padx=10)
        fr, self.t = lista(izq.inner, (("codigo", "Código", 130, "w", False), ("nombre", "Nombre", 260, "w", True),
                                       ("stock", "Stock", 80, "e", False), ("minimo", "Mínimo", 80, "e", False),
                                       ("estado", "Estado", 110, "w", False)))
        fr.pack(fill="both", expand=True)
        self.t.tag_configure("bajo", background="#FFE0E0")
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
        elif tipo == "fijar":
            con.execute("UPDATE productos SET stock=? WHERE codigo=?", (n, s[0]))
        else:
            con.execute("UPDATE productos SET stock_min=? WHERE codigo=?", (n, s[0]))
        con.commit()
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
        L += ["-" * a, linea("Ganancia estimada", fmt(self.gan))]
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


# ---------- Pestaña Configuración ----------
CFG_CAMPOS = ("nombre", "t_nombre", "t_l2", "t_l3", "t_num", "t_total", "t_pie", "t_pie2")
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

        a1 = Tarjeta(A, width=320)
        a1.pack(fill="x")
        titulo(a1.inner, "Tu kiosco")
        f = tk.Frame(a1.inner, bg=CARD)
        f.pack(fill="x")
        f.columnconfigure(0, weight=1)
        campo(f, "Nombre del kiosco", self.v["nombre"], 0, 0)

        a2 = Tarjeta(A, width=320)
        a2.pack(fill="x", pady=(14, 0))
        titulo(a2.inner, "Impresión")
        etiqueta(a2.inner, "Ancho del papel de la impresora").pack(anchor="w")
        self.ancho = tk.StringVar(value=ANCHOS[1] if ancho() == 32 else ANCHOS[0])
        ttk.Combobox(a2.inner, textvariable=self.ancho, values=ANCHOS, state="readonly",
                     font=(FONT, 11)).pack(fill="x", ipady=3, pady=(4, 10))
        self.auto = tk.BooleanVar(value=cfg("auto_ticket") == "1")
        tk.Checkbutton(a2.inner, text="Imprimir ticket automáticamente al cobrar", variable=self.auto, bg=CARD,
                       activebackground=CARD, font=(FONT, 10), selectcolor="white", highlightthickness=0,
                       wraplength=280, justify="left").pack(anchor="w")
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

        titulo(C.inner, "Vista previa del ticket")
        self.prev = tk.Label(C.inner, font=("Consolas", 10), justify="left", anchor="nw", bg="white", fg=TEXT,
                             relief="solid", bd=1, padx=12, pady=12)
        self.prev.pack(anchor="nw")
        gris(C.inner, "Imprimir ticket de prueba", self.prueba, h=44, size=10).pack(anchor="nw", pady=(12, 0), fill="x")
        for v in self.v.values():
            v.trace_add("write", lambda *a: self.preview())
        self.ancho.trace_add("write", lambda *a: self.preview())
        self.preview()

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

    def guardar(self):
        for k, v in self.conf().items():
            set_cfg(k, v.strip() if k != "ancho" else v)
        set_cfg("auto_ticket", "1" if self.auto.get() else "0")
        self.app.lbl_nombre.config(text=nombre())
        self.app.title(nombre() + " - Caja")
        self.app.ticket.set(self.auto.get())
        self.app.toast("Configuración guardada ✓", OK)


if __name__ == "__main__":
    App().mainloop()
