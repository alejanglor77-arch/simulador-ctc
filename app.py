"""
Simulador CTC multiagente · interfaz Streamlit + Plotly
Ejecutar:  streamlit run app.py
"""
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sim import (ESCAPES, KMH, LONG_CANTON, LONG_LINEA, N_CANTONES, TIPOS_FALLO,
                 Simulacion, otra, reloj, s_a_x)

st.set_page_config(page_title="CTC Multiagente", page_icon="🚆", layout="wide")

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
        st.toast(sim.inyectar_fallo(tipo, None if objetivo == "Aleatorio" else objetivo), icon="⚡")

    sim.fallos_aleatorios = st.toggle("Fallos aleatorios automáticos", value=sim.fallos_aleatorios)
    if sim.fallos_aleatorios:
        sim.tasa_fallos_hora = st.slider("Fallos por hora simulada", 1, 40, int(sim.tasa_fallos_hora))
    if st.button("🔧 Enviar brigada de mantenimiento", width="stretch"):
        sim.resolver_todo()
        st.toast("Incidencias resueltas", icon="🔧")

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


def esquema(sim: Simulacion) -> go.Figure:
    fig = go.Figure()
    ocup = sim._mapa_ocupacion()

    # Cantones agrupados por color
    grupos: dict = {}
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
        xs, ys = grupos.setdefault(estado, ([], []))
        x0, x1 = idx * LONG_CANTON / 1000 + 0.05, (idx + 1) * LONG_CANTON / 1000 - 0.05
        xs += [x0, x1, None]
        ys += [Y[via], Y[via], None]
    for estado, (xs, ys) in grupos.items():
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", hoverinfo="skip", showlegend=False,
                                 line=dict(color=COLOR_CANTON[estado], width=7)))

    # LTV
    for c in sim.cantones.values():
        if c.ltv is not None:
            dy = 0.11 if c.via == 1 else -0.11
            x0, x1 = c.idx * 2 + 0.1, c.idx * 2 + 1.9
            fig.add_trace(go.Scatter(x=[x0, x1], y=[Y[c.via] + dy] * 2, mode="lines", showlegend=False,
                                     hovertext=f"LTV {c.ltv / KMH:.0f} km/h · {c.ltv_motivo}", hoverinfo="text",
                                     line=dict(color="#eab308", width=2, dash="dot")))
            fig.add_annotation(x=(x0 + x1) / 2, y=Y[c.via] + dy * 3.2, text=f"LTV {c.ltv / KMH:.0f}",
                               showarrow=False, font=dict(color="#eab308", size=10))

    # Escapes (agujas)
    for ag in sim.agujas:
        xk = ag.x / 1000
        if ag.averiada:
            color, ancho, trazo = "#ef4444", 3, "dash"
        elif ag.asignada_a:
            color, ancho, trazo = "#22c55e", 4, "solid"
        else:
            color, ancho, trazo = "#334155", 2, "solid"
        for ya, yb in ((1, 0), (0, 1)):
            fig.add_trace(go.Scatter(x=[xk - 0.35, xk + 0.35], y=[ya, yb], mode="lines", showlegend=False,
                                     hovertext=f"{ag.nombre} · {ag.posicion} · motor {ag.temperatura_motor:.0f} °C"
                                               + (" · AVERIADA" if ag.averiada else ""),
                                     hoverinfo="text", line=dict(color=color, width=ancho, dash=trazo)))
        fig.add_annotation(x=xk, y=0.5, text=f"<b>{ag.nombre}</b>", showarrow=False,
                           font=dict(size=10, color=color if ag.averiada or ag.asignada_a else "#94a3b8"),
                           bgcolor="#0b1220")

    # Obstáculos
    for c in sim.cantones.values():
        if c.obstaculo_x is not None:
            fig.add_trace(go.Scatter(x=[c.obstaculo_x / 1000], y=[Y[c.via]], mode="markers+text",
                                     marker=dict(symbol="x", size=16, color="#d946ef", line=dict(width=2)),
                                     text=["obstáculo"], textposition="bottom center" if c.via == 2 else "top center",
                                     textfont=dict(color="#d946ef", size=10), showlegend=False, hoverinfo="skip"))

    # Trenes
    for tr in sim.trenes:
        if not tr.en_linea:
            continue
        color = COLOR_ESTADO.get(tr.estado, "#e2e8f0")
        pasos = max(2, int(tr.longitud // 40))
        ss = [tr.s - tr.longitud + k * tr.longitud / pasos for k in range(pasos + 1)]
        xs = [s_a_x(s, tr.sentido) / 1000 for s in ss]
        ys = [y_tren(tr, s) for s in ss]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=color, width=11),
                                 showlegend=False, hoverinfo="skip"))
        arriba = tr.via_base == 1
        hover = (f"<b>{tr.id}</b> · {tr.tipo}<br>{tr.v / KMH:.0f} km/h (permitida {tr.v_permitida / KMH:.0f})"
                 f"<br>Estado: {tr.estado}<br>Autorización: {max(0, tr.ma_s - tr.s):.0f} m<br>{tr.motivo}")
        fig.add_trace(go.Scatter(
            x=[xs[-1]], y=[ys[-1]], mode="markers+text",
            marker=dict(symbol="triangle-right" if tr.sentido > 0 else "triangle-left", size=17, color=color,
                        line=dict(color="#38bdf8" if tr.plan else "#0b1220", width=3 if tr.plan else 1)),
            text=[f"<b>{tr.id}</b> {tr.v / KMH:.0f}"], textposition="top center" if arriba else "bottom center",
            textfont=dict(color="#e2e8f0", size=11), hovertext=hover, hoverinfo="text", showlegend=False))

    fig.add_annotation(x=-0.3, y=1, text="Vía 1 ▶", showarrow=False, xanchor="right", font=dict(color="#94a3b8"))
    fig.add_annotation(x=-0.3, y=0, text="◀ Vía 2", showarrow=False, xanchor="right", font=dict(color="#94a3b8"))
    for i in range(N_CANTONES):
        fig.add_annotation(x=i * 2 + 1, y=-0.47, text=f"{i + 1:02d}", showarrow=False,
                           font=dict(size=9, color="#64748b"))

    fig.update_layout(
        height=380, margin=dict(l=70, r=20, t=10, b=30), paper_bgcolor="#0b1220", plot_bgcolor="#0b1220",
        xaxis=dict(range=[-2.2, LONG_LINEA / 1000 + 0.4], title="PK (km)", color="#94a3b8", showgrid=False,
                   zeroline=False, tickvals=list(range(0, 25, 2))),
        yaxis=dict(range=[-0.6, 1.6], visible=False, fixedrange=True),
        uirevision="ctc", hoverlabel=dict(bgcolor="#1e293b"),
    )
    return fig


def grafico_velocidades(sim: Simulacion) -> go.Figure:
    trenes = [t for t in sim.trenes if t.en_linea]
    ids = [t.id for t in trenes]
    fig = go.Figure([
        go.Bar(name="Velocidad real", x=ids, y=[t.v / KMH for t in trenes],
               marker_color=[COLOR_ESTADO.get(t.estado, "#94a3b8") for t in trenes]),
        go.Scatter(name="Curva de frenado (v. permitida)", x=ids, y=[t.v_permitida / KMH for t in trenes],
                   mode="markers", marker=dict(symbol="line-ew", size=34, color="#e2e8f0",
                                               line=dict(width=3, color="#e2e8f0"))),
    ])
    fig.update_layout(height=260, margin=dict(l=40, r=10, t=10, b=30), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", yaxis=dict(title="km/h", range=[0, 240], gridcolor="#1e293b"),
                      legend=dict(orientation="h", y=1.15), uirevision="vel")
    return fig


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

    st.plotly_chart(esquema(sim), width="stretch", config={"displayModeBar": False}, key="mapa")

    izq, der = st.columns([1.15, 1])
    with izq:
        st.markdown("**📡 Telemetría de los agentes tren**")
        st.dataframe(pd.DataFrame(sim.tabla_trenes()), hide_index=True, width="stretch", height=250)
        st.markdown("**🛑 Supervisión de la curva de frenado**")
        st.plotly_chart(grafico_velocidades(sim), width="stretch",
                        config={"displayModeBar": False}, key="vel")
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
