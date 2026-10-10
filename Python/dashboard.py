import folium
import geopandas as gpd
import streamlit as st
from streamlit_folium import st_folium
import sqlite3

# Page configuration
st.set_page_config(page_title="Lviv GIS & BIM Dashboard", layout="wide")
st.title("Lviv Hackathon Master Dashboard")

# Path to the GeoPackage
gpkg_path = "lviv_hackathon_master.gpkg"

conn = sqlite3.connect(gpkg_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
layers = cursor.fetchall()
print("Available layers in the GeoPackage:")
for layer in layers:
    print(layer[0])
conn.close()


# Load data efficiently using caching
@st.cache_data
def load_geospatial_data():
  hospitals_layer = gpd.read_file(
      gpkg_path, layer="hospitals"
  )
  beds_layer = gpd.read_file(gpkg_path, layer="beds")
  waste_layer = gpd.read_file(gpkg_path, layer="waste_amount")
  return hospitals_layer, beds_layer, waste_layer


# Load data safely
try:
  hospitals, beds, waste = load_geospatial_data()
except Exception as e:
  st.error(f"Error loading GeoPackage layers. Details: {e}")
  st.stop()

# WICHTIG: Koordinatensystem für alle Layer auf GPS (WGS 84 / EPSG:4326) umstellen
hospitals = hospitals.to_crs(epsg=4326)
beds = beds.to_crs(epsg=4326)
waste = waste.to_crs(epsg=4326)

# Sidebar mit Tabs für Steuerung
with st.sidebar:
  st.header("Dashboard controll")
  tab_hospitals, tab_analysis = st.tabs(["Hospitals", "Analysis"])

  with tab_hospitals:
    st.markdown("""
        <style>
        span[data-baseweb="tag"][aria-label*="rehabilitation center"] {background-color: #2ea832 !important; color: white !important;}
        span[data-baseweb="tag"][aria-label*="children's hospital"] {background-color: #800080 !important; color: white !important;}
        span[data-baseweb="tag"][aria-label*="outpatient clinic"] {background-color: #007bff !important; color: white !important;}
        span[data-baseweb="tag"][aria-label*="adult hospital"] {background-color: #d9534f !important; color: white !important;}
        span[data-baseweb="tag"][aria-label*="Maternity/Gynecology hospital"] {background-color: #f8ad4e!important; color: black !important;}
        </style>
    """, unsafe_allow_html=True)


    show_hospitals = st.checkbox("show Hospitals", value=True)
    selected_categories = []
    if show_hospitals and "type" in hospitals.columns:
      unique_types = hospitals["type"].dropna().unique().tolist()
      selected_categories = st.multiselect(
          "Hospital Categories", unique_types, default=unique_types
      )

      st.markdown("""
      <div style="font-size: 13px; line-height: 1.6;">
      <b>Legende:</b><br>
      🟢 Rehabilitation<br>
      🟣 Children's Hospital<br>
      🔵 Outpatient Clinic<br>
      🔴 Adult Hospital<br>
      🟡 Maternity/Gynecology
      </div>
      """, unsafe_allow_html=True)
      
  with tab_analysis:
    show_beds = st.checkbox("Beds Analysis", value=True)
    show_waste = st.checkbox("Waste Management", value=True)

# Hauptbereich: Karte
st.subheader("Map of Lviv")

# Folium-Karte zentrieren
m = folium.Map(location=[49.8397, 24.0297], zoom_start=13, control_scale=True)

# Esri Topo Basemap hinzufügen
folium.TileLayer(
    tiles="https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Esri Topo",
    name="Esri Topo Basemap",
    overlay=False,
    control=True,
).add_to(m)

# Hospitals hinzufügen
if show_hospitals:
  filtered_hospitals = hospitals
  if selected_categories and "type" in hospitals.columns:
    filtered_hospitals = hospitals[hospitals["type"].isin(selected_categories)]

color_map = {
  'rehabilitation center': 'green',
  "children's hospital": 'purple',
  'outpatient clinic': 'blue',
  'adult hospital': 'red',
  'Maternity/Gynecology hospital': 'yellow',
}
 
for _, row in filtered_hospitals.iterrows():
    if row.geometry and row.geometry.geom_type == "Point":
        hosp_type = row.get("type", "Unknown")
        marker_color = color_map.get(hosp_type, "gray")
        
        # HTML-Struktur für ein sauberes Pop-up mit allen gewünschten Infos
        popup_html = f"""
        <div style="font-size: 14px; line-height: 1.5; min-width: 200px;">
            <b>Name:</b> {row.get('name', 'N/A')}<br>
            <b>Name (eng):</b> {row.get('name_eng', 'N/A')}<br>
            <b>Type:</b> {hosp_type}<br>
            <b>Beds:</b> {row.get('beds', 'N/A')}<br>
            <b>Waste:</b> {row.get('waste[kg/day]', 'N/A')} kg/day<br>
            <b>Lab:</b> {row.get('Lab', 'N/A')}<br>
            <b>OP:</b> {row.get('OP', 'N/A')}
        </div>
        """
        
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"Hospital: {row.get('name', 'Unknown')}",
            icon=folium.Icon(color=marker_color, icon="plus", prefix="fa")
        ).add_to(m)

# Beds hinzufügen
if show_beds:
    for _, row in beds.iterrows():
        if row.geometry and row.geometry.geom_type == "Point":
            bed_val = row.get("beds")
            try:
                radius_beds = max(3, float(bed_val) / 20)
            except (TypeError, ValueError):
                radius_beds = 5
            
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=radius_beds,
                color="blue",
                weight=1,
                fill=True,
                fill_color="blue",
                fill_opacity=0.5,
                popup=f"Beds Data Point<br>Beds: {bed_val}",
            ).add_to(m)

# Waste hinzufügen
if show_waste:
    for _, row in waste.iterrows():
        if row.geometry and row.geometry.geom_type == "Point":
            waste_val = row.get("waste[kg/day]")
            try:
                radius_waste = max(3, float(waste_val) / 50)
            except (TypeError, ValueError):
                radius_waste = 5
                
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=radius_waste,
                color="green",
                weight=1,
                fill=True,
                fill_color="green",
                fill_opacity=0.5,
                popup=f"Waste Data Point<br>Waste: {waste_val} kg/day",
            ).add_to(m)

# Karte in Streamlit rendern
st_folium(m, width=1300, height=650)