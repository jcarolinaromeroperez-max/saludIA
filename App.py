import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader


# -------------------------------------------------
# CONFIGURACIÓN GENERAL
# -------------------------------------------------

st.set_page_config(
    page_title="SaludIA · Estado Diario Nutricional y Clínico",
    page_icon="🍽️",
    layout="wide",
)

st.title("🍽️🩺 Panel Clínico-Nutricional SaludIA")
st.caption("Dashboard diario del paciente · soporte al cuidador")

st.markdown(
    """
    Este panel usa directamente tu archivo `Plan nutricional.xlsm` y muestra:
    - Estado actual (día de hoy)
    - Evolución peso / IMC
    - Signos vitales registrados
    - Eventos clínicos / notas del cuidador
    - Menú y carga del cuidador por día
    - Informe PDF descargable con gráfica incluida
    """
)

# -------------------------------------------------
# SIDEBAR: SUBIDA DE ARCHIVO
# -------------------------------------------------

st.sidebar.header("📂 Cargar datos del paciente")
file_main = st.sidebar.file_uploader(
    "📤 Sube el fichero 'Plan nutricional.xlsm'",
    type=["xlsm", "xlsx"],
    key="plan_file"
)

if file_main is None:
    st.warning("Sube el fichero para ver el panel.")
    st.stop()

# -------------------------------------------------
# LECTURA DE HOJAS
# -------------------------------------------------

@st.cache_data(show_spinner=False)
def load_all_sheets(xls_file):
    sheets_needed = [
        "INTERFAZ_HOY",
        "MENUS_BASE_SEMANALES",
        "REGISTRO_EVENTOS",
        "REGISTRO_SIGNOS",
        "ANALISIS_FEEDBACK",
    ]
    dfs_local = {}
    for sh in sheets_needed:
        try:
            dfs_local[sh] = pd.read_excel(xls_file, sheet_name=sh)
        except Exception:
            dfs_local[sh] = pd.DataFrame()
    return dfs_local

dfs = load_all_sheets(file_main)

df_hoy = dfs["INTERFAZ_HOY"].copy()
df_menus = dfs["MENUS_BASE_SEMANALES"].copy()
df_eventos = dfs["REGISTRO_EVENTOS"].copy()
df_signos = dfs["REGISTRO_SIGNOS"].copy()
df_feedback = dfs["ANALISIS_FEEDBACK"].copy()

# -------------------------------------------------
# LIMPIEZAS Y CAMPOS CLAVE
# -------------------------------------------------

# REGISTRO_SIGNOS → crear columnas estándar peso_kg, talla_m, imc si existen
for col in df_signos.columns:
    if "Peso" in col:
        df_signos["peso_kg"] = pd.to_numeric(df_signos[col], errors="coerce")
    if "Altura" in col:
        df_signos["talla_m"] = pd.to_numeric(df_signos[col], errors="coerce")
    if "IMC" in col:
        df_signos["imc"] = pd.to_numeric(df_signos[col], errors="coerce")

# detectar columna fecha en REGISTRO_SIGNOS
col_fecha_signos = None
for c in df_signos.columns:
    if "Fecha" in c:
        col_fecha_signos = c
        break

# detectar columna día/semana en REGISTRO_SIGNOS (ej. "Día de la semana")
col_semana_signos = None
for c in df_signos.columns:
    if "Día de la semana" in c or "semana" in c.lower():
        col_semana_signos = c
        break

# -------------------------------------------------
# FUNCIÓN PARA EXTRAER "HOY" DE INTERFAZ_HOY
# -------------------------------------------------

def extraer_estado_hoy(df_hoy_raw: pd.DataFrame):
    """
    Intentamos detectar:
    - 'Fecha de hoy'
    - 'Día'
    - algún campo tipo SI / PARCIAL / NO (adherencia)
    """
    fecha_hoy_local = None
    dia_semana_local = None
    adherencia_texto = None

    if not df_hoy_raw.empty:
        for row in df_hoy_raw.itertuples(index=False):
            cells = [str(x) for x in row if pd.notna(x)]
            if len(cells) >= 2:
                if "fecha" in cells[0].lower() and "hoy" in cells[0].lower():
                    fecha_hoy_local = cells[1]
                if cells[0].strip().lower() in ["día", "dia"]:
                    dia_semana_local = cells[1]

            if len(cells) >= 2:
                posibles = [x.strip().upper() for x in cells if isinstance(x, str)]
                match = [x for x in posibles if x in ["SI","SÍ","SÍ","PARCIAL","NO"]]
                if match:
                    adherencia_texto = ", ".join(match)

    return fecha_hoy_local, dia_semana_local, adherencia_texto

fecha_hoy, dia_semana_hoy, adherencia_hoy = extraer_estado_hoy(df_hoy)

# -------------------------------------------------
# FUNCIÓN PARA ÚLTIMOS SIGNOS
# -------------------------------------------------

def extraer_signos_actuales(df_signos_raw: pd.DataFrame):
    if df_signos_raw.empty or "peso_kg" not in df_signos_raw:
        return None
    df_valid = df_signos_raw.dropna(subset=["peso_kg"])
    if len(df_valid) == 0:
        return None
    ult = df_valid.iloc[-1]
    return {
        "peso": ult.get("peso_kg", None),
        "imc":  ult.get("imc", None),
        "talla": ult.get("talla_m", None),
        "fecha": ult.get(col_fecha_signos, None) if col_fecha_signos else None,
        "semana": ult.get(col_semana_signos, None) if col_semana_signos else None,
    }

signos_actuales = extraer_signos_actuales(df_signos)

# -------------------------------------------------
# FUNCIÓN PARA EVENTOS RECIENTES
# -------------------------------------------------

def extraer_eventos_recientes(df_evt: pd.DataFrame, n=5):
    if df_evt.empty:
        return pd.DataFrame()
    fecha_col = None
    for c in df_evt.columns:
        if "Fecha" in c:
            fecha_col = c
            break
    if fecha_col:
        df_evt = df_evt.sort_values(by=fecha_col)
    return df_evt.tail(n)

ult_eventos_df = extraer_eventos_recientes(df_eventos, n=5)

# -------------------------------------------------
# FUNCIÓN PARA MENÚ DEL DÍA
# -------------------------------------------------

def preparar_menu_por_dia(df_menu_raw: pd.DataFrame):
    if df_menu_raw.empty:
        return [], lambda d: pd.DataFrame(), None

    col_dia_menu_local = None
    for c in df_menu_raw.columns:
        if "día" in c.lower() or "dia" in c.lower():
            col_dia_menu_local = c
            break

    if not col_dia_menu_local:
        def menu_all(_):
            return df_menu_raw.copy()
        return [], menu_all, col_dia_menu_local

    dias = [
        d for d in df_menu_raw[col_dia_menu_local].dropna().unique()
        if isinstance(d, str)
    ]

    def menu_de(dia_sel):
        return df_menu_raw[df_menu_raw[col_dia_menu_local] == dia_sel].copy()

    return dias, menu_de, col_dia_menu_local

dias_disponibles, menu_de, col_dia_menu = preparar_menu_por_dia(df_menus)

# -------------------------------------------------
# SECCIÓN 1 · ESTADO ACTUAL
# -------------------------------------------------

st.subheader("📌 Estado actual del paciente (hoy)")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Fecha",
        value=str(fecha_hoy) if fecha_hoy else "—"
    )
    st.caption("Fecha de referencia del seguimiento clínico.")

with col2:
    st.metric(
        "Día de la semana",
        value=str(dia_semana_hoy) if dia_semana_hoy else "—"
    )
    st.caption("Ayuda a alinear el menú base previsto.")

with col3:
    st.metric(
        "Adherencia al plan",
        value=adherencia_hoy if adherencia_hoy else "—"
    )
    st.caption("Ej. SI / PARCIAL / NO según INTERFAZ_HOY.")

with st.expander("🔎 Ver hoja INTERFAZ_HOY completa"):
    st.dataframe(df_hoy, use_container_width=True)
    st.caption("Observaciones del día, tolerancia digestiva, incidencias, etc.")

st.markdown("---")

# -------------------------------------------------
# SECCIÓN 2 · EVOLUCIÓN DEL PESO / IMC
# -------------------------------------------------

st.subheader("📈 Evolución del peso e IMC")

fig_weight = None

if not df_signos.empty and "peso_kg" in df_signos and "imc" in df_signos:
    df_peso_plot = df_signos.dropna(subset=["peso_kg"]).copy()

    if col_fecha_signos and col_fecha_signos in df_peso_plot.columns:
        x_axis = df_peso_plot[col_fecha_signos].astype(str)
        x_label = "Fecha"
    elif col_semana_signos and col_semana_signos in df_peso_plot.columns:
        x_axis = df_peso_plot[col_semana_signos].astype(str)
        x_label = "Día / Semana"
    else:
        x_axis = df_peso_plot.index.astype(str)
        x_label = "Registro"

    if len(df_peso_plot) > 1:
        fig_weight, ax = plt.subplots(figsize=(6,3))
        ax.plot(
            x_axis,
            df_peso_plot["peso_kg"],
            marker="o",
            linewidth=2
        )
        ax.set_xlabel(x_label)
        ax.set_ylabel("Peso (kg)")
        ax.set_title("Evolución del peso")
        ax.grid(True, linestyle="--", alpha=0.4)

        ax.axhspan(
            55, 62,
            color="#D9F2E6",
            alpha=0.4,
            label="Rango objetivo ~55–62 kg"
        )
        ax.legend()
        st.pyplot(fig_weight)
    else:
        st.info("No hay suficientes mediciones de peso para trazar tendencia.")

    if signos_actuales:
        colA, colB, colC = st.columns(3)
        with colA:
            st.metric(
                "Peso actual (kg)",
                value=f"{signos_actuales['peso']:.1f}" if signos_actuales["peso"] else "—"
            )
            st.caption("Evitar saltos bruscos >2 kg / 2 semanas.")
        with colB:
            st.metric(
                "IMC actual",
                value=f"{signos_actuales['imc']:.1f}" if signos_actuales["imc"] else "—",
                delta="Objetivo 22–25"
            )
            st.caption("Mantener masa muscular sin sobrepeso.")
        with colC:
            st.metric(
                "Talla (m)",
                value=f"{signos_actuales['talla']:.2f}" if signos_actuales["talla"] else "—"
            )
            st.caption("Referencia para el IMC.")
else:
    st.warning("No se pudieron extraer peso / IMC de REGISTRO_SIGNOS.")

st.markdown("---")

# -------------------------------------------------
# SECCIÓN 3 · SIGNOS VITALES REGISTRADOS
# -------------------------------------------------

st.subheader("🩺 Registro de signos vitales")
if not df_signos.empty:
    st.dataframe(df_signos, use_container_width=True)
    st.caption("Peso, presión, IMC, etc. según las tomas registradas.")
else:
    st.info("No hay datos en REGISTRO_SIGNOS.")

st.markdown("---")

# -------------------------------------------------
# SECCIÓN 4 · EVENTOS CLÍNICOS / OBSERVACIONES
# -------------------------------------------------

st.subheader("📒 Eventos clínicos y observaciones del cuidador")
if not df_eventos.empty:
    ult_eventos_df = extraer_eventos_recientes(df_eventos, n=5)
    st.dataframe(ult_eventos_df, use_container_width=True)
    st.caption("Últimos eventos / incidencias (rechazo de comida, diarrea, dolor, somnolencia, etc.).")
else:
    st.info("No hay datos en REGISTRO_EVENTOS.")

st.markdown("---")

# -------------------------------------------------
# SECCIÓN 5 · PLAN NUTRICIONAL BASE Y CARGA
# -------------------------------------------------

st.subheader("🥗 Plan nutricional base y carga del cuidador")

if not df_menus.empty:
    if len(dias_disponibles) == 0:
        st.info("No pude detectar los días en MENUS_BASE_SEMANALES, mostrando tabla completa.")
        st.dataframe(df_menus, use_container_width=True)
        dia_sel = None
        df_dia = df_menus.copy()
    else:
        default_index = 0
        if dia_semana_hoy and dia_semana_hoy in dias_disponibles:
            default_index = dias_disponibles.index(dia_semana_hoy)

        dia_sel = st.selectbox(
            "Selecciona un día para ver el menú:",
            dias_disponibles,
            index=default_index
        )

        df_dia = menu_de(dia_sel)

        st.markdown(f"### 🍽 Menú previsto para {dia_sel}")
        st.dataframe(df_dia, use_container_width=True)

    col_kcal = None
    col_esfuerzo = None
    for c in df_dia.columns:
        if "kcal" in c.lower():
            col_kcal = c
        if "esfuerzo" in c.lower() or "carga" in c.lower():
            col_esfuerzo = c

    kcal_total_txt = "—"
    carga_txt = "—"

    if col_kcal and df_dia[col_kcal].notna().any():
        kcal_vals = pd.to_numeric(df_dia[col_kcal], errors="coerce").dropna()
        if len(kcal_vals) > 0:
            kcal_total_txt = f"{kcal_vals.sum():.0f} kcal aprox."

    if col_esfuerzo and df_dia[col_esfuerzo].notna().any():
        mapa_esf = {"Muy bajo":1, "Bajo":2, "Medio":3}
        vals = [mapa_esf.get(str(x), None) for x in df_dia[col_esfuerzo]]
        vals = [v for v in vals if v is not None]
        if len(vals):
            media = sum(vals)/len(vals)
            if media <= 1.5:
                carga_txt = "Muy baja"
            elif media <= 2.5:
                carga_txt = "Baja"
            else:
                carga_txt = "Media"

    colX, colY = st.columns(2)
    with colX:
        st.success(f"🔢 kcal totales estimadas: {kcal_total_txt}")
    with colY:
        st.info(f"💪 Carga estimada del cuidador: {carga_txt}")

else:
    st.info("No hay datos en MENUS_BASE_SEMANALES.")
    dia_sel = None
    df_dia = pd.DataFrame()
    kcal_total_txt = "—"
    carga_txt = "—"

st.markdown("---")

# -------------------------------------------------
# SECCIÓN 6 · ALERTAS AUTOMÁTICAS
# -------------------------------------------------

st.subheader("🤖 Señales / Alertas automáticas simples")

alertas = []

if not df_signos.empty and "peso_kg" in df_signos:
    df_valid_peso = df_signos.dropna(subset=["peso_kg"])
    if len(df_valid_peso) >= 2:
        peso_ult = df_valid_peso["peso_kg"].iloc[-1]
        peso_prev = df_valid_peso["peso_kg"].iloc[-2]
        cambio = peso_ult - peso_prev
        if cambio > 1:
            alertas.append("⚠ Aumento de peso rápido. Vigilar retención de líquidos, hinchazón en piernas, sal en la cena.")
        elif cambio < -1:
            alertas.append("⚠ Pérdida de peso rápida. Vigilar apetito, dolor al tragar, apatía o diarrea.")

if len(alertas) == 0:
    st.success("✅ Sin alertas críticas automáticas detectadas con los últimos datos.")
else:
    for a in alertas:
        st.warning(a)

st.caption("Estas recomendaciones son orientativas y NO sustituyen valoración médica.")

st.markdown("---")

# -------------------------------------------------
# SECCIÓN 7 · PDF DESCARGABLE
# -------------------------------------------------

st.subheader("📄 Informe clínico descargable (PDF)")
st.markdown(
    """
    Este informe resume la situación actual para enviarla a enfermería / médico:
    - Fecha y día
    - Peso / IMC actuales
    - Últimos eventos clínicos
    - Alertas automáticas
    - Menú previsto y carga del cuidador
    - Gráfica de evolución del peso
    """
)

def construir_pdf_buffer(
    fecha_hoy_val,
    dia_hoy_val,
    adherencia_val,
    signos,
    eventos_df,
    alertas_list,
    dia_menu,
    kcal_txt,
    carga_txt,
    menu_df,
    fig_weight_obj
):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    x_left = 2*cm
    y = height - 2*cm
    line_height = 0.6*cm

    def writeln(text, bold=False):
        nonlocal y
        if bold:
            c.setFont("Helvetica-Bold", 10)
        else:
            c.setFont("Helvetica", 10)
        for line in str(text).split("\n"):
            c.drawString(x_left, y, line)
            y -= line_height

    # Portada / info clínica
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x_left, y, "Informe clínico-nutricional SaludIA")
    y -= line_height*1.5

    writeln(f"Fecha de referencia: {fecha_hoy_val if fecha_hoy_val else '—'}", bold=True)
    writeln(f"Día de la semana: {dia_hoy_val if dia_hoy_val else '—'}")
    writeln(f"Adherencia al plan: {adherencia_val if adherencia_val else '—'}")
    y -= line_height*0.5

    writeln("Signos actuales:", bold=True)
    if signos:
        writeln(f"- Peso actual: {signos['peso']:.1f} kg" if signos.get('peso') else "- Peso actual: —")
        writeln(f"- IMC actual: {signos['imc']:.1f}" if signos.get('imc') else "- IMC actual: —")
        writeln(f"- Talla: {signos['talla']:.2f} m" if signos.get('talla') else "- Talla: —")
        writeln(f"- Fecha última toma: {signos.get('fecha','—')}")
    else:
        writeln("No hay datos recientes de peso/IMC.")
    y -= line_height*0.5

    writeln("Alertas automáticas:", bold=True)
    if alertas_list:
        for a in alertas_list:
            writeln(f"- {a}")
    else:
        writeln("Sin alertas relevantes detectadas.")
    y -= line_height*0.5

    writeln("Eventos clínicos recientes:", bold=True)
    if eventos_df is not None and not eventos_df.empty:
        max_rows = 5
        rows = eventos_df.tail(max_rows).fillna("").astype(str).values.tolist()
        cols = list(eventos_df.columns)
        writeln("(Últimos registros)")
        for row_vals in rows:
            fila_txt = " | ".join(
                f"{cols[i]}: {row_vals[i]}" for i in range(min(len(cols), 3))
            )
            writeln(f"- {fila_txt}")
            if y < 4*cm:
                c.showPage()
                y = height - 2*cm
    else:
        writeln("No hay eventos registrados.")
    y -= line_height*0.5

    writeln("Plan nutricional previsto:", bold=True)
    writeln(f"Día menú: {dia_menu if dia_menu else '—'}")
    writeln(f"Kcal estimadas del día: {kcal_txt}")
    writeln(f"Carga cuidador estimada: {carga_txt}")
    writeln("Detalle comidas:")

    if menu_df is not None and not menu_df.empty:
        cols_lower = {c.lower(): c for c in menu_df.columns}
        col_comida = None
        col_menu = None
        for key in cols_lower:
            if ("comida" in key and "del" in key) or key.strip() == "comida":
                col_comida = cols_lower[key]
            if "menú" in key or "menu" in key:
                col_menu = cols_lower[key]

        preview_rows = menu_df.fillna("").astype(str).values.tolist()
        header_cols = list(menu_df.columns)

        if col_comida or col_menu:
            for _, row in menu_df.fillna("").iterrows():
                comida_txt = row[col_comida] if col_comida else ""
                menu_txt = row[col_menu] if col_menu else ""
                if comida_txt or menu_txt:
                    writeln(f"- {comida_txt}: {menu_txt}")
                else:
                    writeln(f"- {row.to_dict()}")
                if y < 4*cm:
                    c.showPage()
                    y = height - 2*cm
        else:
            for row_vals in preview_rows:
                fila_txt = " | ".join(
                    f"{header_cols[i]}: {row_vals[i]}" for i in range(min(len(header_cols), 2))
                )
                writeln(f"- {fila_txt}")
                if y < 4*cm:
                    c.showPage()
                    y = height - 2*cm
    else:
        writeln("- No se pudo extraer el menú del día.")

    # Página nueva para gráfica
    c.showPage()
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x_left, y, "Evolución de peso (kg)")
    y -= line_height*1.5

    if fig_weight_obj is not None:
        img_buffer = BytesIO()
        fig_weight_obj.savefig(img_buffer, format="png", bbox_inches="tight", dpi=200)
        img_buffer.seek(0)
        img_reader = ImageReader(img_buffer)

        img_width = width - 4*cm
        img_height = img_width * 0.5
        c.drawImage(
            img_reader,
            x_left,
            y - img_height,
            width=img_width,
            height=img_height,
            preserveAspectRatio=True,
            mask="auto"
        )
        y -= img_height + line_height
    else:
        c.setFont("Helvetica", 10)
        c.drawString(x_left, y, "No hay gráfica de peso disponible.")
        y -= line_height

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

pdf_buffer = construir_pdf_buffer(
    fecha_hoy_val = fecha_hoy,
    dia_hoy_val = dia_semana_hoy,
    adherencia_val = adherencia_hoy,
    signos = signos_actuales,
    eventos_df = ult_eventos_df,
    alertas_list = alertas,
    dia_menu = (dia_semana_hoy if (dia_semana_hoy and dia_semana_hoy in dias_disponibles)
                else (dias_disponibles[0] if len(dias_disponibles)>0 else None)),
    kcal_txt = kcal_total_txt,
    carga_txt = carga_txt,
    menu_df = df_dia,
    fig_weight_obj = fig_weight
)

st.download_button(
    label="📥 Descargar informe PDF",
    data=pdf_buffer,
    file_name="informe_clinico_saludia.pdf",
    mime="application/pdf"
)

st.caption("Este PDF está pensado para imprimir o enviar al equipo clínico.")