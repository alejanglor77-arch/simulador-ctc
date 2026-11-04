# 🚆 Simulador CTC multiagente

Simulador en tiempo real de un Puesto de Mando ferroviario (CTC) en el que cada tren, cantón y aguja es un **agente autónomo** que se comunica con un **orquestador central**. Tiene botón de **Play**, animación del tráfico e **inyección de fallos** en cualquier momento.

## Puesta en marcha

```bash
pip install -r requirements.txt
streamlit run app.py
```

Para publicarlo: sube la carpeta a un repositorio de GitHub y despliégalo en Streamlit Community Cloud indicando `app.py` como archivo principal.

## Estructura

| Archivo | Qué contiene |
|---|---|
| `sim.py` | Motor de simulación y agentes. Python puro, sin dependencias: se puede probar o reutilizar sin interfaz. |
| `app.py` | Interfaz Streamlit + Plotly: esquema de vías animado, KPIs, telemetría, bus de mensajes y panel de fallos. |
| `.streamlit/config.toml` | Tema oscuro tipo pantalla de CTC. |

## Agentes

- **Tren**: conoce su inercia (aceleración, frenado de servicio y de emergencia), velocidad y posición. Recibe una autorización de movimiento y calcula su velocidad permitida con la curva `v = √(2·b·d)`, como la supervisión de ETCS. Si la supera, frena; si la supera claramente, freno de emergencia.
- **Cantón**: vigila ocupación, temperatura de carril y vibración. Cuando la tendencia empeora pide una limitación temporal de velocidad (LTV) antes de que haya avería: mantenimiento predictivo.
- **Aguja**: mueve, acerroja y comprueba posición. Si pierde la comprobación, no se le asignan rutas.
- **Orquestador (CTC)**: escucha a todos por el bus de mensajes y decide: autorizaciones por bloqueo fijo, LTV, marcha a la vista ante fallos de circuito y **rutas alternativas por vía banalizada** ante un obstáculo o un tren averiado (reserva cantones, enclava agujas y comprueba que ningún tren esté ya comprometido a entrar).

## Fallos que se pueden inyectar

| Fallo | Respuesta del sistema |
|---|---|
| Invasión de vía (obstáculo) | Protege el cantón, frena los trenes y busca ruta por la otra vía |
| Fallo en motor de aguja | Anula rutas que la usen y aplica LTV 40 km/h en su entorno |
| Fallo de circuito de vía | Detiene al tren ante el cantón y autoriza marcha a la vista (≤ 30 km/h) |
| Avería de tren | Freno de emergencia; los trenes de detrás lo rodean si hay ruta libre |
| Sobrecalentamiento de carril | LTV 80 km/h al pasar de 52 °C y 30 km/h al pasar de 62 °C |

## Validación

Se simularon 48 horas (16 semillas × 3 h) con unos 30 fallos aleatorios por hora: ninguna colisión entre trenes y ningún bloqueo permanente de la red. Los únicos incidentes posibles son impactos con un obstáculo que aparece dentro de la distancia de frenado del tren, algo físicamente inevitable y que el simulador registra para mostrarlo.

## Alcance y siguientes pasos

La "inteligencia" actual es un sistema multiagente con reglas expertas de explotación ferroviaria y física de frenado; no hay aprendizaje automático. El motor está separado de la interfaz precisamente para dar el siguiente paso:

1. Envolver `Simulacion` como entorno de Gymnasium / PettingZoo (observación = telemetría, acción = decisiones del CTC).
2. Entrenar agentes con aprendizaje por refuerzo multiagente (MARL) para minimizar retrasos sin perder seguridad, comparándolos con el orquestador de reglas como referencia.
3. Sustituir el detector de sobrecalentamiento por un modelo predictivo entrenado con series de temperatura y vibración.
