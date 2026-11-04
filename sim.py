"""
Motor de simulación multiagente para un Puesto de Mando ferroviario (CTC).

Arquitectura
------------
- AgenteTren      : conoce su inercia (aceleración, frenado de servicio y de
                    emergencia), velocidad y posición. Recibe una autorización de
                    movimiento y aplica una curva de frenado tipo ETCS.
- AgenteCanton    : vigila su circuito de vía, temperatura de carril y vibración
                    (mantenimiento predictivo) y detecta obstáculos.
- AgenteAguja     : motor de aguja con comprobación de posición; puede averiarse.
- Simulacion      : el Orquestador / CTC. Escucha a todos los agentes por el bus
                    de mensajes, calcula autorizaciones, aplica limitaciones
                    temporales de velocidad (LTV) y establece rutas alternativas
                    por vía banalizada cuando hay un bloqueo.

Solo usa la biblioteca estándar de Python: se puede probar sin interfaz.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# Parámetros de la línea
# --------------------------------------------------------------------------- #
KMH = 1 / 3.6                       # km/h -> m/s
LONG_LINEA = 24_000.0               # m
LONG_CANTON = 2_000.0               # m
N_CANTONES = int(LONG_LINEA // LONG_CANTON)
ESCAPES = [2_000.0, 6_000.0, 10_000.0, 14_000.0, 18_000.0, 22_000.0]  # PK de los escapes
VIAS = (1, 2)
SENTIDO = {1: +1, 2: -1}            # vía 1: izquierda -> derecha; vía 2: derecha -> izquierda
MARGEN = 60.0                       # m de margen antes del punto de parada
PASO_SIM = 0.5                      # s de simulación por paso de integración
LOOKAHEAD = 7_000.0                 # m que el CTC examina por delante de cada tren
PASO_ESCANEO = 25.0                 # m
INICIO_RELOJ = 7 * 3600             # la simulación arranca a las 07:00:00

TIPOS_FALLO = {
    "obstaculo": "Invasión de vía (obstáculo)",
    "aguja": "Fallo en motor de aguja",
    "circuito": "Fallo de circuito de vía (ocupación fantasma)",
    "tren": "Avería de tren",
    "calor": "Sobrecalentamiento de carril",
}


def otra(via: int) -> int:
    return 2 if via == 1 else 1


def s_a_x(s: float, sentido: int) -> float:
    """Distancia recorrida en el sentido de marcha -> PK absoluto (m)."""
    return s if sentido > 0 else LONG_LINEA - s


x_a_s = s_a_x  # la transformación es su propia inversa


def canton_de(x: float) -> int:
    return min(N_CANTONES - 1, max(0, int(x // LONG_CANTON)))


def entrada_canton_s(idx: int, sentido: int) -> float:
    return idx * LONG_CANTON if sentido > 0 else LONG_LINEA - (idx + 1) * LONG_CANTON


def id_canton(via: int, idx: int) -> str:
    return f"C{via}-{idx + 1:02d}"


def reloj(t: float) -> str:
    total = int(INICIO_RELOJ + t)
    return f"{(total // 3600) % 24:02d}:{(total // 60) % 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------- #
# Bus de mensajes entre agentes
# --------------------------------------------------------------------------- #
@dataclass
class Mensaje:
    t: float
    emisor: str
    receptor: str
    tipo: str
    texto: str
    nivel: str = "info"   # info | telemetria | aviso | critico | accion


class BusMensajes:
    def __init__(self, maximo: int = 500):
        self.maximo = maximo
        self.mensajes: list[Mensaje] = []

    def enviar(self, t, emisor, receptor, tipo, texto, nivel="info"):
        self.mensajes.append(Mensaje(t, emisor, receptor, tipo, texto, nivel))
        if len(self.mensajes) > self.maximo:
            self.mensajes = self.mensajes[-self.maximo:]


# --------------------------------------------------------------------------- #
# Agentes de infraestructura
# --------------------------------------------------------------------------- #
@dataclass
class AgenteCanton:
    via: int
    idx: int
    temperatura: float = 32.0
    vibracion: float = 1.0
    obstaculo_x: Optional[float] = None
    obstaculo_hasta: float = 0.0
    fallo_cv: bool = False
    fallo_cv_hasta: float = 0.0
    calentamiento_hasta: float = 0.0
    ltv: Optional[float] = None           # límite de velocidad (m/s) fijado por el CTC
    ltv_motivo: str = ""
    nivel_alerta: str = "ok"              # ok | precaucion | critico

    @property
    def id(self) -> str:
        return id_canton(self.via, self.idx)

    @property
    def x_ini(self) -> float:
        return self.idx * LONG_CANTON

    def actualizar(self, t, dt, rng, bus: BusMensajes, ocupado: bool):
        calentando = t < self.calentamiento_hasta
        objetivo = 32.0 + (40.0 if calentando else 0.0)
        self.temperatura += (objetivo - self.temperatura) * dt / 90.0 + rng.gauss(0, 0.05) * math.sqrt(dt)
        vib_obj = 1.0 + (1.2 if ocupado else 0.0) + (1.8 if calentando else 0.0)
        self.vibracion += (vib_obj - self.vibracion) * dt / 10.0 + rng.gauss(0, 0.02) * math.sqrt(dt)

        nivel = "critico" if self.temperatura > 62 else "precaucion" if self.temperatura > 52 else "ok"
        if nivel != self.nivel_alerta:
            textos = {
                "precaucion": f"Temperatura de carril {self.temperatura:.0f} °C: riesgo de pandeo. Solicito LTV.",
                "critico": f"Temperatura de carril {self.temperatura:.0f} °C: nivel crítico. Solicito LTV severa.",
                "ok": f"Temperatura de carril normalizada ({self.temperatura:.0f} °C).",
            }
            bus.enviar(t, self.id, "CTC", "MANT. PREDICTIVO", textos[nivel],
                       "critico" if nivel == "critico" else "aviso" if nivel == "precaucion" else "info")
            self.nivel_alerta = nivel

        if self.obstaculo_x is not None and t >= self.obstaculo_hasta:
            self.obstaculo_x = None
            bus.enviar(t, self.id, "CTC", "VÍA LIBRE", "Obstáculo retirado. Gálibo despejado.", "info")
        if self.fallo_cv and t >= self.fallo_cv_hasta:
            self.fallo_cv = False
            bus.enviar(t, self.id, "CTC", "CIRCUITO VÍA", "Circuito de vía restablecido.", "info")


@dataclass
class AgenteAguja:
    nombre: str
    x: float
    posicion: str = "normal"
    averiada: bool = False
    averia_hasta: float = 0.0
    asignada_a: Optional[str] = None
    temperatura_motor: float = 28.0

    def actualizar(self, t, dt, rng, bus: BusMensajes):
        objetivo = 85.0 if self.averiada else 28.0 + (6.0 if self.asignada_a else 0.0)
        self.temperatura_motor += (objetivo - self.temperatura_motor) * dt / 40.0 + rng.gauss(0, 0.05) * math.sqrt(dt)
        if self.averiada and t >= self.averia_hasta:
            self.averiada = False
            bus.enviar(t, self.nombre, "CTC", "AGUJA", "Motor reparado. Comprobación de posición OK.", "info")


# --------------------------------------------------------------------------- #
# Agente tren
# --------------------------------------------------------------------------- #
@dataclass
class AgenteTren:
    id: str
    tipo: str
    via_base: int
    s: float                 # posición de cabeza medida en su sentido de marcha (m)
    vmax: float              # m/s
    acel: float              # m/s²
    fren: float              # frenado de servicio m/s²
    fren_emerg: float        # frenado de emergencia m/s²
    longitud: float          # m
    v: float = 0.0
    estado: str = "PARADO"
    v_permitida: float = 0.0
    ma_s: float = 0.0
    motivo: str = "Vía libre"
    plan: Optional[tuple] = None          # (s_escape_ida, s_escape_vuelta) por vía banalizada
    averiado_hasta: float = -1.0
    en_linea: bool = True
    espera_entrada: float = 0.0
    autorizaciones_cv: set = field(default_factory=set)
    espera_cv: float = 0.0
    tiempo_parado: float = 0.0
    prox_intento_ruta: float = 0.0
    ultimo_rechazo: Optional[str] = None
    estado_registrado: str = ""
    orden_cambio: bool = False

    @property
    def sentido(self) -> int:
        return SENTIDO[self.via_base]

    @property
    def x(self) -> float:
        return s_a_x(self.s, self.sentido)

    @property
    def x_cola(self) -> float:
        return s_a_x(self.s - self.longitud, self.sentido)

    def via_en(self, s: float) -> int:
        if self.plan and self.plan[0] <= s < self.plan[1]:
            return otra(self.via_base)
        return self.via_base

    @property
    def via_actual(self) -> int:
        return self.via_en(self.s)

    def averiado(self, t: float) -> bool:
        return t < self.averiado_hasta

    def distancia_frenado(self) -> float:
        return self.v ** 2 / (2 * self.fren)

    def celdas_ocupadas(self) -> set:
        celdas = set()
        puntos = [self.s - self.longitud + k * 100.0 for k in range(int(self.longitud // 100) + 1)] + [self.s]
        for sp in puntos:
            x = s_a_x(sp, self.sentido)
            if 0.0 <= x <= LONG_LINEA:
                celdas.add((self.via_en(sp), canton_de(x)))
        return celdas


# --------------------------------------------------------------------------- #
# Orquestador / CTC
# --------------------------------------------------------------------------- #
class Simulacion:
    def __init__(self, semilla: int = 7):
        self.rng = random.Random(semilla)
        self.t = 0.0
        self.bus = BusMensajes()
        self.cantones = {(v, i): AgenteCanton(v, i) for v in VIAS for i in range(N_CANTONES)}
        self.agujas = [AgenteAguja(f"A{k + 1}", x) for k, x in enumerate(ESCAPES)]
        self.reservas: dict = {}
        self.trenes = self._crear_flota()
        self.fallos_aleatorios = False
        self.tasa_fallos_hora = 20.0
        self.ultimo_fallo: Optional[tuple] = None
        self.rutas_banalizadas = 0
        self.incidentes = 0
        self.fallos_inyectados = 0
        self._prox_telemetria = 60.0
        self.bus.enviar(0, "CTC", "TODOS", "ARRANQUE",
                        f"Puesto de mando en servicio. {len(self.trenes)} trenes, "
                        f"{len(self.cantones)} cantones y {len(self.agujas)} agujas conectados.", "info")

    # ------------------------------------------------------------------ flota
    def _crear_flota(self) -> list[AgenteTren]:
        # id, tipo, vía, s cabeza, vmax km/h, acel, fren, fren emerg, longitud
        datos = [
            ("AV-101", "Alta velocidad", 1, 15_500, 220, 0.45, 0.60, 1.20, 200),
            ("R-103", "Regional", 1, 7_500, 160, 0.50, 0.70, 1.25, 150),
            ("MER-105", "Mercancías", 1, 1_500, 100, 0.20, 0.35, 0.80, 450),
            ("C-202", "Cercanías", 2, 14_500, 120, 0.70, 0.80, 1.30, 120),
            ("AV-204", "Alta velocidad", 2, 6_500, 220, 0.45, 0.60, 1.20, 200),
            ("R-206", "Regional", 2, 700, 160, 0.50, 0.70, 1.25, 150),
        ]
        flota = []
        for tid, tipo, via, s, vmax, a, b, be, lon in datos:
            flota.append(AgenteTren(tid, tipo, via, float(s), vmax * KMH, a, b, be, float(lon),
                                    v=0.55 * vmax * KMH))
        return flota

    def tren(self, tid: str) -> AgenteTren:
        return next(t for t in self.trenes if t.id == tid)

    def aguja(self, nombre: str) -> AgenteAguja:
        return next(a for a in self.agujas if a.nombre == nombre)

    def _aguja_cerca(self, x: float, tol: float = PASO_ESCANEO + 1) -> Optional[AgenteAguja]:
        for a in self.agujas:
            if abs(a.x - x) <= tol:
                return a
        return None

    # --------------------------------------------------------------- bucle
    def avanzar(self, segundos: float):
        for _ in range(max(1, int(round(segundos / PASO_SIM)))):
            self._paso(PASO_SIM)

    def _mapa_ocupacion(self) -> dict:
        ocup: dict = {}
        for tr in self.trenes:
            if tr.en_linea:
                for c in tr.celdas_ocupadas():
                    ocup.setdefault(c, []).append(tr.id)
        return ocup

    def _paso(self, dt: float):
        self.t += dt
        ocup = self._mapa_ocupacion()

        # 1) Los agentes de infraestructura se actualizan y reportan
        for clave, c in self.cantones.items():
            c.actualizar(self.t, dt, self.rng, self.bus, clave in ocup)
        for a in self.agujas:
            a.actualizar(self.t, dt, self.rng, self.bus)

        # 2) El orquestador aplica limitaciones temporales de velocidad
        self._orquestador_ltv()

        # 3) Cada tren recibe su autorización y se mueve
        for tr in self.trenes:
            self._mover_tren(tr, dt, ocup)

        self._vigilar_colisiones()

        # 4) Fallos aleatorios y telemetría consolidada
        if self.fallos_aleatorios and self.rng.random() < self.tasa_fallos_hora / 3600.0 * dt:
            texto = self.inyectar_fallo(self.rng.choice(list(TIPOS_FALLO)), None)
            self.ultimo_fallo = (self.t, "automático", texto)
        if self.t >= self._prox_telemetria:
            self._prox_telemetria += 60.0
            k = self.kpis()
            tmax = max(c.temperatura for c in self.cantones.values())
            self.bus.enviar(self.t, "Agentes", "CTC", "TELEMETRÍA",
                            f"{k['trenes_en_linea']} trenes en línea, velocidad media "
                            f"{k['velocidad_media']:.0f} km/h, carril máx. {tmax:.0f} °C.", "telemetria")

    # ------------------------------------------------------------- LTV
    def _orquestador_ltv(self):
        for c in self.cantones.values():
            limite, motivo = None, ""
            if c.nivel_alerta == "precaucion":
                limite, motivo = 80 * KMH, "temperatura de carril"
            elif c.nivel_alerta == "critico":
                limite, motivo = 30 * KMH, "temperatura crítica de carril"
            for a in self.agujas:
                if a.averiada and c.idx in (canton_de(a.x - 1), canton_de(a.x + 1)):
                    if limite is None or 40 * KMH < limite:
                        limite, motivo = 40 * KMH, f"aguja {a.nombre} averiada"
            if (limite is None) != (c.ltv is None) or (limite and c.ltv and abs(limite - c.ltv) > 0.1):
                if limite is None:
                    self.bus.enviar(self.t, "CTC", "Trenes", "LTV", f"Anulada la LTV en {c.id}.", "info")
                else:
                    self.bus.enviar(self.t, "CTC", "Trenes", "LTV",
                                    f"LTV {limite / KMH:.0f} km/h en {c.id} por {motivo}.", "accion")
                c.ltv, c.ltv_motivo = limite, motivo

    # -------------------------------------------------- autorización (MA)
    def _extension(self, otro: AgenteTren, tr: AgenteTren) -> tuple:
        a = x_a_s(otro.x, tr.sentido)
        b = x_a_s(otro.x_cola, tr.sentido)
        return min(a, b), max(a, b)

    def _autorizacion(self, tr: AgenteTren, ocup: dict):
        s0 = tr.s
        ma = s0 + LOOKAHEAD
        motivo = "Vía libre"
        restricciones: list = []
        bloqueo = None
        via_prev = tr.via_en(s0)
        celda_prev = None
        s = s0
        while s < s0 + LOOKAHEAD:
            x = s_a_x(s, tr.sentido)
            if x < 0 or x > LONG_LINEA:
                break
            via = tr.via_en(s)
            if via != via_prev:
                ag = self._aguja_cerca(x)
                if ag is None or ag.averiada or ag.asignada_a != tr.id:
                    nombre = ag.nombre if ag else "?"
                    ma = s - PASO_ESCANEO - MARGEN
                    motivo = f"Aguja {nombre} sin comprobación"
                    break
            via_prev = via
            idx = canton_de(x)
            celda = (via, idx)
            if celda != celda_prev:
                celda_prev = celda
                c = self.cantones[celda]
                ent = entrada_canton_s(idx, tr.sentido)
                delante = ent > s0 + 0.5

                otros = []
                for oid in ocup.get(celda, []):
                    if oid == tr.id:
                        continue
                    o = self.tren(oid)
                    lo, hi = self._extension(o, tr)
                    if delante or hi > s0:
                        otros.append((o, lo))
                if otros:
                    o, lo = min(otros, key=lambda p: p[1])
                    punto = ent if delante else max(s0, lo)
                    ma = punto - MARGEN
                    motivo = f"{c.id} ocupado por {o.id}"
                    if o.averiado(self.t) or (o.v < 0.5 and o.motivo != "Vía libre"):
                        bloqueo = ("tren_detenido", celda, ent)
                    break

                res = self.reservas.get(celda)
                # Si el tren ya está dentro del cantón, lo despeja: así no hay bloqueo mutuo.
                if res is not None and res != tr.id and delante:
                    ma = ent - MARGEN
                    motivo = f"{c.id} reservado para {res}"
                    break

                if c.obstaculo_x is not None:
                    obs_s = x_a_s(c.obstaculo_x, tr.sentido)
                    if obs_s > s0:
                        ma = (ent if delante else obs_s - 100) - MARGEN
                        motivo = f"Obstáculo en {c.id}"
                        bloqueo = ("obstaculo", celda, ent)
                        break

                if c.fallo_cv and celda not in tr.autorizaciones_cv and delante:
                    ma = ent - MARGEN
                    motivo = f"Fallo circuito de vía {c.id}"
                    bloqueo = ("fallo_cv", celda, ent)
                    break

                inicio = max(ent, s0)
                if c.ltv is not None:
                    restricciones.append((inicio, c.ltv))
                if c.fallo_cv:
                    restricciones.append((inicio, 30 * KMH))   # marcha a la vista
            s += PASO_ESCANEO
        return ma, restricciones, motivo, bloqueo

    # ------------------------------------------------------ movimiento
    def _mover_tren(self, tr: AgenteTren, dt: float, ocup: dict):
        t = self.t
        if not tr.en_linea:
            tr.espera_entrada -= dt
            if tr.espera_entrada <= 0:
                celda = (tr.via_base, canton_de(s_a_x(1.0, tr.sentido)))
                c = self.cantones[celda]
                ocup_actual = self._mapa_ocupacion()   # fresca: dos trenes no pueden entrar a la vez
                libre = (not ocup_actual.get(celda) and celda not in self.reservas
                         and c.obstaculo_x is None and not c.fallo_cv)
                if libre:
                    tr.en_linea, tr.s, tr.v = True, 0.0, 0.6 * tr.vmax
                    tr.autorizaciones_cv.clear()
                    self.bus.enviar(t, tr.id, "CTC", "ENTRADA", f"Entra en el tramo por vía {tr.via_base}.", "info")
                else:
                    tr.espera_entrada = 5.0
            return

        s_prev = tr.s
        if tr.averiado(t):
            a = -tr.fren_emerg
            tr.estado = "AVERIADO"
            tr.v_permitida = 0.0
        else:
            ma, restr, motivo, bloqueo = self._autorizacion(tr, ocup)
            tr.ma_s, tr.motivo = ma, motivo
            ant = tr.v * dt
            vp = math.sqrt(2 * tr.fren * max(0.0, ma - tr.s - ant))
            for sr, lim in restr:
                vp = min(vp, math.sqrt(lim ** 2 + 2 * tr.fren * max(0.0, sr - tr.s - ant)))
            vp = min(vp, tr.vmax)
            tr.v_permitida = vp
            if tr.v > vp + 1.5:
                a, tr.estado = -tr.fren_emerg, "EMERGENCIA"
            elif tr.v > vp:
                a, tr.estado = -tr.fren, "FRENANDO"
            elif tr.v < vp - 0.3:
                a, tr.estado = tr.acel, "TRACCIÓN"
            else:
                a, tr.estado = 0.0, "CRUCERO"

            if bloqueo:
                self._gestionar_bloqueo(tr, bloqueo, dt, ocup)
            elif tr.orden_cambio and tr.plan is None and t >= tr.prox_intento_ruta:
                self._intentar_orden_cambio(tr)

        v_nueva = max(0.0, tr.v + a * dt)
        tr.s += (tr.v + v_nueva) / 2 * dt
        tr.v = v_nueva
        if tr.v < 0.05:
            tr.tiempo_parado += dt
            if tr.estado not in ("AVERIADO",):
                tr.estado = "PARADO"

        self._registrar_estado(tr)
        self._comprobar_obstaculos(tr, s_prev)

        # Liberación de la ruta banalizada
        if tr.plan and tr.s - tr.longitud > tr.plan[1] + 10:
            self._liberar_ruta(tr, "Ruta banalizada completada")

        # Salida del tramo
        if tr.s - tr.longitud > LONG_LINEA:
            if tr.plan:
                self._liberar_ruta(tr, "Salida del tramo")
            tr.en_linea = False
            tr.orden_cambio = False
            tr.espera_entrada = 25.0 + self.rng.uniform(0, 30)
            self.bus.enviar(t, tr.id, "CTC", "SALIDA", "Sale del tramo controlado.", "info")

    def _registrar_estado(self, tr: AgenteTren):
        clave = tr.estado if tr.estado in ("EMERGENCIA", "PARADO", "AVERIADO") else "MARCHA"
        if clave == tr.estado_registrado:
            return
        anterior, tr.estado_registrado = tr.estado_registrado, clave
        if clave == "EMERGENCIA":
            self.bus.enviar(self.t, tr.id, "CTC", "FRENO EMERG.",
                            f"Frenado de emergencia a {tr.v / KMH:.0f} km/h. Motivo: {tr.motivo}.", "critico")
        elif clave == "PARADO":
            self.bus.enviar(self.t, tr.id, "CTC", "DETENIDO", f"Detenido. Motivo: {tr.motivo}.", "aviso")
        elif clave == "MARCHA" and anterior in ("PARADO", "AVERIADO"):
            self.bus.enviar(self.t, tr.id, "CTC", "REANUDA", "Reanuda la marcha.", "info")

    # --------------------------------------------- gestión de bloqueos
    def _gestionar_bloqueo(self, tr: AgenteTren, bloqueo, dt, ocup):
        tipo, celda, ent = bloqueo
        if tipo == "fallo_cv":
            if tr.v < 0.1 and tr.ma_s - tr.s < 200:
                tr.espera_cv += dt
                if tr.espera_cv >= 12:
                    tr.autorizaciones_cv.add(celda)
                    tr.espera_cv = 0.0
                    self.bus.enviar(self.t, "CTC", tr.id, "MARCHA A LA VISTA",
                                    f"Autorizado a rebasar {id_canton(*celda)} en marcha a la vista (≤ 30 km/h).",
                                    "accion")
            return
        if tr.plan is None and self.t >= tr.prox_intento_ruta:
            tr.prox_intento_ruta = self.t + 5.0
            self._intentar_banalizar(tr, celda, ent, ocup)

    def _celda_bloqueada(self, celda, ocup) -> bool:
        c = self.cantones[celda]
        if c.obstaculo_x is not None or c.fallo_cv:
            return True
        for oid in ocup.get(celda, []):
            o = self.tren(oid)
            if o.averiado(self.t) or o.v < 0.5:
                return True
        return False

    def _intentar_banalizar(self, tr: AgenteTren, celda, ent, ocup):
        ocup = self._mapa_ocupacion()   # estado actual, no el del inicio del paso
        sentido = tr.sentido
        via_alt = otra(tr.via_base)
        escapes_s = sorted(x_a_s(x, sentido) for x in ESCAPES)
        clave = f"{celda}"

        def rechazar(razon):
            if tr.ultimo_rechazo != clave + razon:
                tr.ultimo_rechazo = clave + razon
                self.bus.enviar(self.t, "CTC", tr.id, "RUTA ALT.",
                                f"Ruta alternativa no disponible ({razon}). Se mantiene la protección.", "aviso")
            return False

        margen_fren = tr.distancia_frenado() + 150
        candidatos = [e for e in escapes_s if tr.s + margen_fren <= e <= ent + 1]
        if not candidatos:
            return rechazar("sin escape alcanzable antes del bloqueo")
        c1 = max(candidatos)

        # Extensión del tramo bloqueado en la vía propia
        s_fin = ent + LONG_CANTON
        while s_fin < LONG_LINEA:
            x_sig = s_a_x(s_fin + 1, sentido)
            if not (0 <= x_sig <= LONG_LINEA):
                break
            if not self._celda_bloqueada((tr.via_base, canton_de(x_sig)), ocup):
                break
            s_fin += LONG_CANTON
        posteriores = [e for e in escapes_s if e >= s_fin - 1]
        if not posteriores:
            return rechazar("sin escape de retorno tras el bloqueo")
        c2 = min(posteriores)
        razon = self._establecer_ruta(tr, c1, c2, ocup)
        return True if razon is None else rechazar(razon)

    def _establecer_ruta(self, tr: AgenteTren, c1: float, c2: float, ocup: dict) -> Optional[str]:
        """Comprueba y, si es segura, establece la ruta por la vía contraria entre los escapes
        c1 y c2. Devuelve None si la ruta queda establecida o el motivo del rechazo."""
        sentido = tr.sentido
        via_alt = otra(tr.via_base)
        ag1, ag2 = self._aguja_cerca(s_a_x(c1, sentido)), self._aguja_cerca(s_a_x(c2, sentido))
        for ag in (ag1, ag2):
            if ag is None or ag.averiada:
                return f"aguja {ag.nombre if ag else '?'} averiada"
            if ag.asignada_a not in (None, tr.id):
                return f"aguja {ag.nombre} ocupada por otra ruta"

        celdas_alt = []
        s = c1 + LONG_CANTON / 2
        while s < c2:
            celda_alt = (via_alt, canton_de(s_a_x(s, sentido)))
            c = self.cantones[celda_alt]
            if ocup.get(celda_alt):
                return f"{c.id} ocupado"
            if celda_alt in self.reservas or c.obstaculo_x is not None or c.fallo_cv:
                return f"{c.id} no disponible"
            celdas_alt.append(celda_alt)
            s += LONG_CANTON

        # Enclavamiento: el cantón de reincorporación a la vía propia también queda reservado,
        # para que ningún tren de la vía propia pueda ocuparlo mientras dure la ruta.
        x_inc = s_a_x(c2 + LONG_CANTON / 2, sentido)
        if 0 <= x_inc <= LONG_LINEA:
            celda_inc = (tr.via_base, canton_de(x_inc))
            c = self.cantones[celda_inc]
            if ocup.get(celda_inc) or celda_inc in self.reservas:
                return f"cantón de reincorporación {c.id} ocupado"
            celdas_alt.append(celda_inc)

        # Enclavamiento de aproximación: ningún otro tren puede estar ya comprometido
        # (dentro de su distancia de frenado) a entrar en alguno de los cantones a reservar.
        xa, xb = sorted((s_a_x(c1, sentido), s_a_x(c2, sentido)))
        for o in self.trenes:
            if o is tr or not o.en_linea:
                continue
            for via_c, idx_c in celdas_alt:
                ent_o = entrada_canton_s(idx_c, o.sentido)
                d = ent_o - o.s
                if o.via_en(ent_o + 1) == via_c and -1 <= d < o.distancia_frenado() + 400:
                    return f"{o.id} aproximándose a {id_canton(via_c, idx_c)}"

        # Ruta factible: reservar y mover agujas
        for ce in celdas_alt:
            self.reservas[ce] = tr.id
        for ag in (ag1, ag2):
            ag.posicion, ag.asignada_a = "invertida", tr.id
        tr.plan = (c1, c2)
        tr.ultimo_rechazo = None
        self.rutas_banalizadas += 1
        self.bus.enviar(self.t, "CTC", f"{ag1.nombre}, {ag2.nombre}", "ORDEN AGUJAS",
                        "Mover a posición invertida.", "accion")
        self.bus.enviar(self.t, f"{ag1.nombre}, {ag2.nombre}", "CTC", "COMPROBACIÓN",
                        "Agujas movidas, acerrojadas y comprobadas.", "info")
        self.bus.enviar(self.t, "CTC", tr.id, "RUTA ALT.",
                        f"Ruta por vía {via_alt} banalizada entre PK {xa / 1000:.0f} y PK {xb / 1000:.0f} "
                        f"({', '.join(id_canton(*ce) for ce in celdas_alt)}).", "accion")
        return None

    # --------------------------------------------- orden manual de cambio de vía
    def ordenar_cambio_via(self, tid: str) -> str:
        """El operador del CTC pide pasar un tren a la vía contraria entre los dos próximos
        escapes. El CTC la ejecuta en cuanto sea segura (reintenta cada 5 s)."""
        tr = self.tren(tid)
        if not tr.en_linea:
            return f"{tid} está fuera del tramo; espera a que entre."
        if tr.plan:
            return f"{tid} ya circula por una ruta alternativa."
        tr.orden_cambio = True
        tr.prox_intento_ruta = 0.0
        self.bus.enviar(self.t, "Operador", "CTC", "ORDEN MANUAL",
                        f"Solicitado cambio de vía para {tid}.", "accion")
        resultado = self._intentar_orden_cambio(tr)
        return resultado

    def _intentar_orden_cambio(self, tr: AgenteTren) -> str:
        tr.prox_intento_ruta = self.t + 5.0
        escapes_s = sorted(x_a_s(x, tr.sentido) for x in ESCAPES)
        alcanzables = [e for e in escapes_s if e >= tr.s + tr.distancia_frenado() + 150]
        if len(alcanzables) < 2:
            tr.orden_cambio = False
            self.bus.enviar(self.t, "CTC", tr.id, "ORDEN MANUAL",
                            "Orden anulada: no quedan dos escapes por delante del tren.", "aviso")
            return f"No quedan escapes por delante de {tr.id}: orden anulada."
        c1, c2 = alcanzables[0], alcanzables[1]
        razon = self._establecer_ruta(tr, c1, c2, self._mapa_ocupacion())
        if razon is None:
            tr.orden_cambio = False
            return f"{tr.id}: ruta por la vía contraria establecida."
        clave = f"manual{razon}"
        if tr.ultimo_rechazo != clave:
            tr.ultimo_rechazo = clave
            self.bus.enviar(self.t, "CTC", tr.id, "ORDEN MANUAL",
                            f"En espera: {razon}. Reintento automático.", "aviso")
        return f"{tr.id}: orden en espera ({razon}). El CTC la ejecutará en cuanto sea segura."

    def _liberar_ruta(self, tr: AgenteTren, motivo: str):
        for ce in [k for k, v in self.reservas.items() if v == tr.id]:
            del self.reservas[ce]
        nombres = []
        for ag in self.agujas:
            if ag.asignada_a == tr.id:
                ag.asignada_a = None
                if not ag.averiada:
                    ag.posicion = "normal"
                nombres.append(ag.nombre)
        tr.plan = None
        self.bus.enviar(self.t, "CTC", tr.id, "RUTA LIBERADA",
                        f"{motivo}. Agujas {', '.join(nombres) or '-'} a normal.", "info")

    # ----------------------------------------------------- seguridad
    def _comprobar_obstaculos(self, tr: AgenteTren, s_prev: float):
        for c in self.cantones.values():
            if c.obstaculo_x is None:
                continue
            obs_s = x_a_s(c.obstaculo_x, tr.sentido)
            if s_prev < obs_s <= tr.s and tr.via_en(obs_s) == c.via:
                self.incidentes += 1
                self.bus.enviar(self.t, tr.id, "CTC", "IMPACTO",
                                f"Impacto con obstáculo en {c.id} a {tr.v / KMH:.0f} km/h: la distancia de "
                                f"frenado era insuficiente. Tren inmovilizado.", "critico")
                c.obstaculo_x = None
                tr.v = 0.0
                tr.averiado_hasta = self.t + 240

    @staticmethod
    def _tramos(tr: AgenteTren) -> list:
        """Tramos (vía, x_min, x_max) que ocupa físicamente el tren, partidos en los escapes."""
        cortes = [tr.s - tr.longitud, tr.s]
        if tr.plan:
            cortes += [c for c in tr.plan if cortes[0] < c < cortes[1]]
        cortes.sort()
        tramos = []
        for a, b in zip(cortes, cortes[1:]):
            xa, xb = sorted((s_a_x(a, tr.sentido), s_a_x(b, tr.sentido)))
            tramos.append((tr.via_en((a + b) / 2), xa, xb))
        return tramos

    def _vigilar_colisiones(self):
        activos = [t for t in self.trenes if t.en_linea]
        pares = set()
        for i, a in enumerate(activos):
            for b in activos[i + 1:]:
                for va, a0, a1 in self._tramos(a):
                    for vb, b0, b1 in self._tramos(b):
                        if va == vb and a0 < b1 and b0 < a1:
                            pares.add((a.id, b.id))
        for par in pares - getattr(self, "_colisiones_activas", set()):
            self.incidentes += 1
            self.bus.enviar(self.t, "CTC", "TODOS", "COLISIÓN",
                            f"Solapamiento entre {par[0]} y {par[1]}.", "critico")
        self._colisiones_activas = pares

    # ------------------------------------------------- inyección de fallos
    def objetivos(self, tipo: str) -> list[str]:
        if tipo in ("obstaculo", "circuito", "calor"):
            return [c.id for c in self.cantones.values()]
        if tipo == "aguja":
            return [a.nombre for a in self.agujas]
        if tipo == "tren":
            return [t.id for t in self.trenes]
        return []

    def _canton_por_id(self, cid: str) -> AgenteCanton:
        return next(c for c in self.cantones.values() if c.id == cid)

    def inyectar_fallo(self, tipo: str, objetivo: Optional[str] = None) -> str:
        rng, t = self.rng, self.t
        self.fallos_inyectados += 1
        ocup = self._mapa_ocupacion()

        if tipo == "obstaculo":
            if objetivo:
                c = self._canton_por_id(objetivo)
            else:
                libres = [c for k, c in self.cantones.items() if k not in ocup and c.obstaculo_x is None]
                c = rng.choice(libres or list(self.cantones.values()))
            x = c.x_ini + LONG_CANTON * rng.uniform(0.35, 0.65)
            for tr in self.trenes:     # que no aparezca bajo un tren
                lo, hi = sorted((tr.x, tr.x_cola))
                if tr.en_linea and tr.via_actual == c.via and lo - 50 <= x <= hi + 50:
                    x = c.x_ini + (LONG_CANTON * 0.9 if x - c.x_ini < LONG_CANTON / 2 else LONG_CANTON * 0.1)
            c.obstaculo_x, c.obstaculo_hasta = x, t + 240
            self.bus.enviar(t, c.id, "CTC", "DETECTOR", f"Obstáculo detectado en vía {c.via}, "
                            f"PK {x / 1000:.2f}. Cantón protegido.", "critico")
            return f"Obstáculo en {c.id} (PK {x / 1000:.2f})"

        if tipo == "circuito":
            c = self._canton_por_id(objetivo) if objetivo else rng.choice(list(self.cantones.values()))
            c.fallo_cv, c.fallo_cv_hasta = True, t + 300
            self.bus.enviar(t, c.id, "CTC", "CIRCUITO VÍA",
                            "Ocupación sin tren identificado: posible fallo de circuito de vía.", "critico")
            return f"Fallo de circuito de vía en {c.id}"

        if tipo == "calor":
            c = self._canton_por_id(objetivo) if objetivo else rng.choice(list(self.cantones.values()))
            c.calentamiento_hasta = t + 300
            self.bus.enviar(t, c.id, "CTC", "MANT. PREDICTIVO",
                            "Tendencia ascendente de temperatura y vibración en el carril.", "aviso")
            return f"Sobrecalentamiento iniciado en {c.id}"

        if tipo == "aguja":
            ag = self.aguja(objetivo) if objetivo else rng.choice(self.agujas)
            ag.averiada, ag.averia_hasta = True, t + 240
            self.bus.enviar(t, ag.nombre, "CTC", "AGUJA",
                            "Sobreintensidad en el motor: pérdida de comprobación de posición.", "critico")
            if ag.asignada_a:
                tr = self.tren(ag.asignada_a)
                if tr.plan and tr.s < tr.plan[0] - 1:
                    self._liberar_ruta(tr, f"Ruta anulada por avería de {ag.nombre}")
            return f"Avería en la aguja {ag.nombre}"

        if tipo == "tren":
            candidatos = [tr for tr in self.trenes if tr.en_linea and not tr.averiado(t)]
            if objetivo:
                tr = self.tren(objetivo)
            elif candidatos:
                tr = rng.choice(candidatos)
            else:
                return "No hay trenes disponibles para averiar"
            tr.averiado_hasta = t + 150
            self.bus.enviar(t, tr.id, "CTC", "AVERÍA",
                            "Pérdida de tracción: aplicado freno de emergencia.", "critico")
            return f"Avería en el tren {tr.id}"

        return "Tipo de fallo desconocido"

    def resolver_todo(self):
        for c in self.cantones.values():
            c.obstaculo_x, c.fallo_cv, c.calentamiento_hasta = None, False, 0.0
        for a in self.agujas:
            a.averiada = False
        for tr in self.trenes:
            tr.averiado_hasta = -1.0
        self.bus.enviar(self.t, "Mantenimiento", "CTC", "MANTENIMIENTO",
                        "Brigadas en campo: todas las incidencias resueltas.", "info")

    # --------------------------------------------------------- consultas
    def kpis(self) -> dict:
        en_linea = [t for t in self.trenes if t.en_linea]
        return {
            "reloj": reloj(self.t),
            "trenes_en_linea": len(en_linea),
            "velocidad_media": sum(t.v for t in en_linea) / KMH / max(1, len(en_linea)),
            "cantones_bloqueados": sum(1 for c in self.cantones.values() if c.obstaculo_x is not None or c.fallo_cv),
            "ltv_activas": sum(1 for c in self.cantones.values() if c.ltv is not None),
            "agujas_averiadas": sum(1 for a in self.agujas if a.averiada),
            "rutas_activas": sum(1 for t in self.trenes if t.plan),
            "rutas_banalizadas": self.rutas_banalizadas,
            "incidentes": self.incidentes,
            "minutos_detenidos": sum(t.tiempo_parado for t in self.trenes) / 60.0,
        }

    def tabla_trenes(self) -> list[dict]:
        filas = []
        for tr in self.trenes:
            filas.append({
                "Tren": tr.id,
                "Tipo": tr.tipo,
                "Vía": str(tr.via_actual) if tr.en_linea else "-",
                "PK (km)": round(tr.x / 1000, 2) if tr.en_linea else None,
                "Velocidad (km/h)": round(tr.v / KMH) if tr.en_linea else 0,
                "V. permitida (km/h)": round(tr.v_permitida / KMH) if tr.en_linea else 0,
                "Estado": tr.estado if tr.en_linea else "FUERA DE TRAMO",
                "Autorización (m)": round(max(0.0, tr.ma_s - tr.s)) if tr.en_linea and not tr.averiado(self.t) else 0,
                "Motivo": ("Ruta banalizada · " if tr.plan else "") + (tr.motivo if tr.en_linea else "Esperando entrada"),
            })
        return filas
