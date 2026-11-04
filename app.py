"""
Simulador CTC multiagente · interfaz Streamlit
Ejecutar:  streamlit run app.py
"""
import time

import pandas as pd
from html import escape
import streamlit as st

from sim import (ESCAPES, KMH, LONG_CANTON, LONG_LINEA, N_CANTONES, TIPOS_FALLO,
                 Simulacion, otra, reloj, s_a_x)

st.set_page_config(page_title="CTC Multiagente", page_icon="🚆", layout="wide")

# Sin parpadeo: Streamlit atenúa los elementos mientras refresca; aquí se desactiva ese efecto.
st.markdown("""
<style>
[data-stale="true"], .stale-element, [data-testid="stElementContainer"][data-stale="true"] {
    opacity: 1 !important; transition: none !important; filter: none !important;
}
[data-testid="stStatusWidget"] { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# Estado de la sesión
# --------------------------------------------------------------------------- #
if "sim" not in st.session_state:
    st.session_state.sim = Simulacion(semilla=7)
    st.session_state.play = False
    st.session_state.ultimo = time.time()
sim: Simulacion = st.session_state.sim

Y = {1: 1.0, 2: 0.0}
COLOR_ESTADO = {
    "TRACCIÓN": "#22c55e", "CRUCERO": "#22c55e", "FRENANDO": "#f59e0b",
    "EMERGENCIA": "#ef4444", "PARADO": "#94a3b8", "AVERIADO": "#a855f7",
}
COLOR_CANTON = {
    "libre": "#475569", "ocupado": "#ef4444", "reservado": "#22c55e",
    "obstaculo": "#d946ef", "fallo_cv": "#f97316",
}
ICONO_NIVEL = {"info": "ℹ️", "telemetria": "📡", "aviso": "⚠️", "critico": "🚨", "accion": "🧠"}

# --------------------------------------------------------------------------- #
# Barra lateral: control y fallos
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.title("🎛️ Puesto de Mando")
    c1, c2 = st.columns(2)
    if c1.button("⏸ Pausa" if st.session_state.play else "▶ Play", type="primary", width="stretch"):
        st.session_state.play = not st.session_state.play
        st.session_state.ultimo = time.time()
        st.rerun()
    if c2.button("↺ Reiniciar", width="stretch"):
        st.session_state.sim = Simulacion(semilla=int(time.time()) % 10_000)
        st.session_state.play = False
        st.rerun()

    st.select_slider("Velocidad de la simulación", options=[1, 2, 5, 10, 20, 40], value=10,
                     format_func=lambda v: f"×{v}", key="factor")
    if not st.session_state.play and st.button("⏭ Avanzar 30 s", width="stretch"):
        sim.avanzar(30)

    st.divider()
    st.subheader("⚡ Inyección de fallos")
    tipo = st.selectbox("Tipo de fallo", list(TIPOS_FALLO), format_func=TIPOS_FALLO.get)
    objetivo = st.selectbox("Elemento afectado", ["Aleatorio"] + sim.objetivos(tipo))
    if st.button("Inyectar fallo ahora", type="primary", width="stretch"):
        texto = sim.inyectar_fallo(tipo, None if objetivo == "Aleatorio" else objetivo)
        sim.ultimo_fallo = (sim.t, "manual", texto)
        st.toast(texto, icon="⚡")

    sim.fallos_aleatorios = st.toggle("Fallos aleatorios automáticos", value=sim.fallos_aleatorios)
    if sim.fallos_aleatorios:
        sim.tasa_fallos_hora = st.slider("Fallos por hora simulada", 1, 40, int(sim.tasa_fallos_hora))
    if st.button("🔧 Enviar brigada de mantenimiento", width="stretch"):
        sim.resolver_todo()
        st.toast("Incidencias resueltas", icon="🔧")

    st.divider()
    st.subheader("🔀 Maniobra de agujas")
    st.caption("Pide al CTC que pase un tren a la vía contraria entre los dos próximos escapes "
               "y lo devuelva a su vía. Solo lo hará cuando sea seguro.")
    tren_maniobra = st.selectbox("Tren", [t.id for t in sim.trenes])
    if st.button("Ordenar cambio de vía", width="stretch"):
        st.toast(sim.ordenar_cambio_via(tren_maniobra), icon="🔀")

    st.divider()
    st.checkbox("Mostrar telemetría periódica en el bus", value=False, key="ver_telemetria")
    st.caption("Leyenda de cantones: 🟥 ocupado · 🟩 ruta reservada · 🟪 obstáculo · "
               "🟧 fallo de circuito · ┅ amarillo: LTV")


# --------------------------------------------------------------------------- #
# Dibujo del esquema de vías
# --------------------------------------------------------------------------- #
def y_tren(tr, s):
    yh, ya = Y[tr.via_base], Y[otra(tr.via_base)]
    if not tr.plan:
        return yh
    c1, c2 = tr.plan
    rampa = lambda c: min(1.0, max(0.0, (s - (c - 350)) / 700))
    return yh + (ya - yh) * (rampa(c1) - rampa(c2))


# Dibujo en SVG: el navegador sustituye la imagen de golpe en cada refresco, sin parpadeo.
ANCHO, ALTO = 1400, 360
X0, X1 = 95, 1375                    # píxeles del PK 0 y del PK 24
YV = {1: 125.0, 2: 255.0}            # píxeles de cada vía


def px(km: float) -> float:
    return X0 + (X1 - X0) * km / (LONG_LINEA / 1000)


def py(y_logico: float) -> float:    # 1 = vía 1, 0 = vía 2
    return YV[2] + (YV[1] - YV[2]) * y_logico


def esquema(sim: Simulacion) -> str:
    ocup = sim._mapa_ocupacion()
    e = []
    e.append(f'<rect x="0" y="0" width="{ANCHO}" height="{ALTO}" rx="10" fill="#0b1220"/>')

    # Escapes (debajo de las vías)
    for ag in sim.agujas:
        xk = ag.x / 1000
        if ag.averiada:
            color, ancho, trazo = "#ef4444", 3, ' stroke-dasharray="7 5"'
        elif ag.asignada_a:
            color, ancho, trazo = "#22c55e", 4, ""
        else:
            color, ancho, trazo = "#334155", 2, ""
        tip = f"{ag.nombre} · {ag.posicion} · motor {ag.temperatura_motor:.0f} °C" + (" · AVERIADA" if ag.averiada else "")
        e.append(f'<g><title>{escape(tip)}</title>'
                 f'<line x1="{px(xk - 0.35):.1f}" y1="{YV[1]}" x2="{px(xk + 0.35):.1f}" y2="{YV[2]}" '
                 f'stroke="{color}" stroke-width="{ancho}"{trazo}/>'
                 f'<line x1="{px(xk - 0.35):.1f}" y1="{YV[2]}" x2="{px(xk + 0.35):.1f}" y2="{YV[1]}" '
                 f'stroke="{color}" stroke-width="{ancho}"{trazo}/>'
                 f'<rect x="{px(xk) - 15:.1f}" y="{(YV[1] + YV[2]) / 2 - 10}" width="30" height="20" rx="4" fill="#0b1220"/>'
                 f'<text x="{px(xk):.1f}" y="{(YV[1] + YV[2]) / 2 + 4}" text-anchor="middle" font-size="12" '
                 f'font-weight="700" fill="{color if ag.averiada or ag.asignada_a else "#94a3b8"}">{ag.nombre}</text></g>')

    # Cantones
    for (via, idx), c in sim.cantones.items():
        if c.obstaculo_x is not None:
            estado = "obstaculo"
        elif c.fallo_cv:
            estado = "fallo_cv"
        elif (via, idx) in ocup:
            estado = "ocupado"
        elif (via, idx) in sim.reservas:
            estado = "reservado"
        else:
            estado = "libre"
        x0, x1 = px(idx * 2 + 0.05), px(idx * 2 + 1.95)
        tip = f"{c.id} · carril {c.temperatura:.0f} °C · vibración {c.vibracion:.2f}"
        e.append(f'<line x1="{x0:.1f}" y1="{YV[via]}" x2="{x1:.1f}" y2="{YV[via]}" stroke="{COLOR_CANTON[estado]}" '
                 f'stroke-width="7" stroke-linecap="round"><title>{escape(tip)}</title></line>')
        if c.ltv is not None:
            dy = -16 if via == 1 else 16
            e.append(f'<line x1="{x0 + 6:.1f}" y1="{YV[via] + dy}" x2="{x1 - 6:.1f}" y2="{YV[via] + dy}" '
                     f'stroke="#eab308" stroke-width="2" stroke-dasharray="3 4"/>'
                     f'<text x="{(x0 + x1) / 2:.1f}" y="{YV[via] + dy * 2.6 + 4:.1f}" text-anchor="middle" '
                     f'font-size="11" fill="#eab308">LTV {c.ltv / KMH:.0f} · {escape(c.ltv_motivo)}</text>')

    # Obstáculos
    for c in sim.cantones.values():
        if c.obstaculo_x is not None:
            xo, yo = px(c.obstaculo_x / 1000), YV[c.via]
            e.append(f'<g stroke="#d946ef" stroke-width="4" stroke-linecap="round">'
                     f'<line x1="{xo - 9:.1f}" y1="{yo - 9}" x2="{xo + 9:.1f}" y2="{yo + 9}"/>'
                     f'<line x1="{xo - 9:.1f}" y1="{yo + 9}" x2="{xo + 9:.1f}" y2="{yo - 9}"/></g>'
                     f'<text x="{xo:.1f}" y="{yo + (-18 if c.via == 1 else 28)}" text-anchor="middle" '
                     f'font-size="11" fill="#d946ef">obstáculo</text>')

    # Trenes
    for tr in sim.trenes:
        if not tr.en_linea:
            continue
        color = COLOR_ESTADO.get(tr.estado, "#e2e8f0")
        pasos = max(2, int(tr.longitud // 40))
        ss = [tr.s - tr.longitud + k * tr.longitud / pasos for k in range(pasos + 1)]
        puntos = [(px(s_a_x(sv, tr.sentido) / 1000), py(y_tren(tr, sv))) for sv in ss]
        hx, hy = puntos[-1]
        d = 13 if tr.sentido > 0 else -13
        tri = f"{hx + d:.1f},{hy:.1f} {hx - d * 0.3:.1f},{hy - 9:.1f} {hx - d * 0.3:.1f},{hy + 9:.1f}"
        borde = 'stroke="#38bdf8" stroke-width="3"' if tr.plan else 'stroke="#0b1220" stroke-width="1"'
        tip = (f"{tr.id} · {tr.tipo}\n{tr.v / KMH:.0f} km/h (permitida {tr.v_permitida / KMH:.0f})\n"
               f"Estado: {tr.estado}\nAutorización: {max(0, tr.ma_s - tr.s):.0f} m\n{tr.motivo}")
        ty = hy - 22 if tr.via_base == 1 else hy + 32
        e.append(f'<g><title>{escape(tip)}</title>'
                 f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in puntos)}" fill="none" '
                 f'stroke="{color}" stroke-width="11" stroke-linecap="round"/>'
                 f'<polygon points="{tri}" fill="{color}" {borde}/>'
                 f'<text x="{hx:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="13" fill="#e2e8f0">'
                 f'<tspan font-weight="700">{tr.id}</tspan> {tr.v / KMH:.0f} km/h</text></g>')

    # Rótulos y eje
    e.append(f'<text x="{X0 - 18}" y="{YV[1] + 5}" text-anchor="end" font-size="14" fill="#94a3b8">Vía 1 ▶</text>')
    e.append(f'<text x="{X0 - 18}" y="{YV[2] + 5}" text-anchor="end" font-size="14" fill="#94a3b8">◀ Vía 2</text>')
    for i in range(N_CANTONES):
        e.append(f'<text x="{px(i * 2 + 1):.1f}" y="300" text-anchor="middle" font-size="11" fill="#475569">'
                 f'{i + 1:02d}</text>')
    for km in range(0, 25, 2):
        e.append(f'<text x="{px(km):.1f}" y="328" text-anchor="middle" font-size="12" fill="#94a3b8">{km}</text>')
    e.append(f'<text x="{(X0 + X1) / 2}" y="350" text-anchor="middle" font-size="12" fill="#64748b">'
             f'PK (km) · los números pequeños son los cantones · pasa el ratón por encima para ver detalles</text>')
    return (f'<svg viewBox="0 0 {ANCHO} {ALTO}" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui, -apple-system, sans-serif">{"".join(e)}</svg>')


def grafico_velocidades(sim: Simulacion) -> str:
    trenes = [t for t in sim.trenes if t.en_linea]
    W, H, izq, abajo, arriba = 640, 250, 46, 215, 20
    vmax = 240.0
    yv = lambda v: abajo - (abajo - arriba) * min(v, vmax) / vmax
    e = [f'<rect x="0" y="0" width="{W}" height="{H}" rx="10" fill="#0b1220"/>']
    for v in range(0, 241, 60):
        e.append(f'<line x1="{izq}" y1="{yv(v):.1f}" x2="{W - 10}" y2="{yv(v):.1f}" stroke="#1e293b"/>'
                 f'<text x="{izq - 6}" y="{yv(v) + 4:.1f}" text-anchor="end" font-size="11" fill="#64748b">{v}</text>')
    if trenes:
        hueco = (W - izq - 10) / len(trenes)
        for i, t in enumerate(trenes):
            cx = izq + hueco * (i + 0.5)
            v, vp = t.v / KMH, t.v_permitida / KMH
            color = COLOR_ESTADO.get(t.estado, "#94a3b8")
            e.append(f'<g><title>{escape(t.id)}: {v:.0f} km/h · permitida {vp:.0f} km/h · {escape(t.estado)}</title>'
                     f'<rect x="{cx - 22:.1f}" y="{yv(v):.1f}" width="44" height="{abajo - yv(v):.1f}" rx="3" fill="{color}"/>'
                     f'<line x1="{cx - 32:.1f}" y1="{yv(vp):.1f}" x2="{cx + 32:.1f}" y2="{yv(vp):.1f}" '
                     f'stroke="#e2e8f0" stroke-width="3"/>'
                     f'<text x="{cx:.1f}" y="{abajo + 18}" text-anchor="middle" font-size="12" fill="#cbd5e1">'
                     f'{escape(t.id)}</text></g>')
    e.append(f'<text x="{izq}" y="14" font-size="11" fill="#94a3b8">km/h · barra = velocidad real · '
             f'raya blanca = velocidad permitida por la curva de frenado</text>')
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui, -apple-system, sans-serif">{"".join(e)}</svg>')


# --------------------------------------------------------------------------- #
# Panel principal (se refresca solo mientras está en Play)
# --------------------------------------------------------------------------- #
st.markdown("### 🚆 Simulador CTC multiagente · supervisión en tiempo real")
st.caption("Cada tren, cantón y aguja es un agente que reporta al orquestador del Puesto de Mando. "
           "Pulsa **Play**, inyecta fallos desde la barra lateral y observa cómo se protege la red.")

intervalo = 0.5 if st.session_state.play else None


@st.fragment(run_every=intervalo)
def panel():
    sim: Simulacion = st.session_state.sim
    if st.session_state.play:
        ahora = time.time()
        dt_real = min(1.0, max(0.0, ahora - st.session_state.ultimo))
        st.session_state.ultimo = ahora
        sim.avanzar(dt_real * st.session_state.factor)

    k = sim.kpis()
    cols = st.columns(7)
    cols[0].metric("Hora", k["reloj"], "▶ en marcha" if st.session_state.play else "⏸ en pausa",
                   delta_color="off")
    cols[1].metric("Velocidad media", f"{k['velocidad_media']:.0f} km/h")
    cols[2].metric("Cantones bloqueados", k["cantones_bloqueados"])
    cols[3].metric("LTV activas", k["ltv_activas"])
    cols[4].metric("Agujas averiadas", k["agujas_averiadas"])
    cols[5].metric("Rutas alternativas", k["rutas_banalizadas"], f"{k['rutas_activas']} activas", delta_color="off")
    cols[6].metric("Incidentes", k["incidentes"], help="Impactos o solapamientos (debería ser 0 salvo "
                   "obstáculos que aparecen dentro de la distancia de frenado).")

    if sim.ultimo_fallo:
        t_f, origen, texto = sim.ultimo_fallo
        st.info(f"⚡ Último fallo ({origen}, {reloj(t_f)}): {texto}")
    st.markdown(esquema(sim), unsafe_allow_html=True)

    izq, der = st.columns([1.15, 1])
    with izq:
        st.markdown("**📡 Telemetría de los agentes tren**")
        st.dataframe(pd.DataFrame(sim.tabla_trenes()), hide_index=True, width="stretch", height=250)
        st.markdown("**🛑 Supervisión de la curva de frenado**")
        st.markdown(grafico_velocidades(sim), unsafe_allow_html=True)
    with der:
        st.markdown("**🧠 Bus de mensajes entre agentes**")
        ver_tel = st.session_state.get("ver_telemetria", False)
        filas = [{"Hora": reloj(m.t), "": ICONO_NIVEL.get(m.nivel, ""), "De": m.emisor, "Para": m.receptor,
                  "Tipo": m.tipo, "Mensaje": m.texto}
                 for m in reversed(sim.bus.mensajes) if ver_tel or m.nivel != "telemetria"][:60]
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height=330)

        st.markdown("**🔧 Salud de la infraestructura (mantenimiento predictivo)**")
        infra = [{"Elemento": c.id, "Temp. carril (°C)": round(c.temperatura, 1),
                  "Vibración": round(c.vibracion, 2), "Alerta": c.nivel_alerta}
                 for c in sorted(sim.cantones.values(), key=lambda c: -c.temperatura)[:4]]
        infra += [{"Elemento": a.nombre, "Temp. carril (°C)": None, "Vibración": None,
                   "Alerta": f"motor {a.temperatura_motor:.0f} °C · " + ("AVERIADA" if a.averiada else a.posicion)}
                  for a in sim.agujas]
        st.dataframe(pd.DataFrame(infra), hide_index=True, width="stretch", height=250)


panel()

with st.expander("ℹ️ Cómo funciona la arquitectura multiagente"):
    st.markdown(f"""
- **Línea**: {LONG_LINEA / 1000:.0f} km de doble vía, {N_CANTONES} cantones de {LONG_CANTON / 1000:.0f} km por vía
  y {len(ESCAPES)} escapes (A1–A{len(ESCAPES)}) que permiten pasar de una vía a otra.
- **Agente tren**: conoce su masa equivalente (aceleración, frenado de servicio y de emergencia). En cada paso
  recibe una *autorización de movimiento* y calcula su velocidad permitida con la curva v = √(2·b·d),
  igual que la supervisión de ETCS. Si la supera, frena; si la supera mucho, freno de emergencia.
- **Agente cantón**: vigila ocupación, temperatura del carril y vibración. Si la tendencia es mala, pide al CTC una
  limitación temporal de velocidad (LTV) *antes* de que haya avería: mantenimiento predictivo.
- **Agente aguja**: mueve, acerroja y comprueba su posición. Si pierde la comprobación, el CTC no le asigna rutas.
- **Orquestador (CTC)**: escucha a todos por el bus, calcula autorizaciones por bloqueo fijo, aplica LTV,
  autoriza *marcha a la vista* ante fallos de circuito y, ante un obstáculo o tren averiado, busca una
  **ruta por vía banalizada**: reserva cantones, enclava agujas y comprueba que ningún tren contrario esté ya
  comprometido a entrar.
""")
