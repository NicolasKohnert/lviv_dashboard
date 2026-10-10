import geopandas as gpd
import networkx as nx
from itertools import combinations
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

GPKG_PATH = "lviv_hackathon_master.gpkg"
BUFFER_STAGES = {1: 100, 2: 250, 3: 500}  # Meidungszone 1/2/3 in Metern

# Rynok Square / Lviv Rathaus (WGS84) — Zentrum der Innenstadt-Zone
CITY_CENTER_WGS84 = Point(24.03194, 49.84167)  # (lon, lat)
CITY_CENTER_RADIUS_M = 1200  # anpassbar

# ============================================================
# MODUL 1: Grundlagen einlesen
# ============================================================
def load_base_data(gpkg_path):
    streets = gpd.read_file(gpkg_path, layer="streets_routing")
    sites = gpd.read_file(gpkg_path, layer="hospitals")
    if streets and sites is not None:
        print("Daten geladen: streets_routing, hospitals")

    return streets, sites

    

# ============================================================
# MODUL 2: Kritikalitäts-Objekte einlesen
# ============================================================
def load_criticality_layers(gpkg_path):
    names = {"schools": "schools", "kindergartens": "kindergartens", "residential": "landuse_residential"}
    layers = {}
    for key, name in names.items():
        try:
            layers[key] = gpd.read_file(gpkg_path, layer=name)
        except Exception as e:
            print(f"Warnung: Layer '{name}' fehlt ({e})")
            layers[key] = None
    return layers

def get_city_center_point(target_crs):
    """Rynok Square von WGS84 ins Ziel-KBS (z.B. EPSG:32635) umrechnen."""
    center_gdf = gpd.GeoDataFrame(geometry=[CITY_CENTER_WGS84], crs="EPSG:4326")
    center_proj = center_gdf.to_crs(target_crs)
    return center_proj.geometry.iloc[0]

# ============================================================
# MODUL 3: Meidungszonen je Pufferstufe erzeugen
# ============================================================
def build_meidungszonen(criticality_layers, buffer_m, target_crs,
                         include_city_center=True, city_center_radius=CITY_CENTER_RADIUS_M):
    geoms = []
    for key in ["schools", "kindergartens", "residential"]:
        layer = criticality_layers.get(key)
        if layer is not None and len(layer) > 0:
            geoms.extend(layer.geometry.buffer(buffer_m).tolist())

    if include_city_center:
        center_point = get_city_center_point(target_crs)
        geoms.append(center_point.buffer(city_center_radius))

    if not geoms:
        return None

    merged = unary_union(geoms)
    return gpd.GeoDataFrame(geometry=[merged], crs=target_crs)

def compute_weights(streets, meidungszone_gdf, factor=2.5):
    streets = streets.copy()
    streets["base_length"] = streets.geometry.length
    if meidungszone_gdf is not None:
        zone = meidungszone_gdf.geometry.iloc[0]
        mask = streets.geometry.intersects(zone)
        streets["in_zone"] = mask
        streets["routing_cost"] = streets["base_length"] * mask.map({True: factor, False: 1.0})
    else:
        streets["in_zone"] = False
        streets["routing_cost"] = streets["base_length"]
    return streets

# ============================================================
# MODUL 4: Graph + Streckenberechnung
# ============================================================
DEFAULT_SPEEDS_KMH = {
    "motorway": 100, "trunk": 90, "primary": 70, "secondary": 60,
    "tertiary": 50, "unclassified": 40, "residential": 40,
    "service": 20, "motorway_link": 60, "trunk_link": 50,
    "primary_link": 40, "secondary_link": 40, "tertiary_link": 30,
}
DEFAULT_SPEED_FALLBACK = 30

def get_speed_kmh(row):
    maxspeed = row.get("maxspeed")
    if maxspeed and str(maxspeed).isdigit():
        return float(maxspeed)
    return DEFAULT_SPEEDS_KMH.get(row.get("highway"), DEFAULT_SPEED_FALLBACK)

def build_graph(streets_weighted):
    G = nx.Graph()
    for idx, row in streets_weighted.iterrows():
        coords = [(round(x, 1), round(y, 1)) for x, y in row.geometry.coords]
        oneway = row.get("oneway", None)
        total_len = row.geometry.length
        total_cost = row["routing_cost"]
        speed_ms = get_speed_kmh(row) / 3.6
        for i in range(len(coords) - 1):
            u, v = coords[i], coords[i+1]
            seg_len = ((u[0]-v[0])**2 + (u[1]-v[1])**2) * 0.5    
            share = seg_len / total_len if total_len > 0 else 0
            seg_cost = total_cost * share
            seg_duration_s = seg_len / speed_ms if speed_ms > 0 else None
            G.add_edge(u, v, weight=seg_cost, length=seg_len, duration_s=seg_duration_s)
            if oneway != "yes":
                G.add_edge(v, u, weight=seg_cost, length=seg_len, duration_s=seg_duration_s)
    return G

def snap_to_graph(G, point):
    nodes = list(G.nodes)
    return min(nodes, key=lambda n: (n[0]-point.x)*2 + (n[1]-point.y)*2)

def compute_routes(G, site_nodes):
    results = []
    for a, b in combinations(site_nodes.keys(), 2):
        try:
            path = nx.shortest_path(G, site_nodes[a], site_nodes[b], weight="weight")
            length_m, duration_s, cost_sum = 0, 0, 0
            for i in range(len(path) - 1):
                ed = G.get_edge_data(path[i], path[i+1])
                length_m += ed["length"]
                duration_s += ed["duration_s"] or 0
                cost_sum += ed["weight"]
            results.append({
                "from": a, "to": b, "path": path,
                "length_m": round(length_m, 1),
                "duration_min": round(duration_s / 60, 1),
                "cost_raw": cost_sum
            })
        except nx.NetworkXNoPath:
            results.append({"from": a, "to": b, "path": None,
                             "length_m": None, "duration_min": None, "cost_raw": None})
    return results

def normalize_cost(results):
    """cost = relativer Umweg-/Meidungsfaktor, 0 = kein Umweg, bis 1 = starker Umweg."""
    for r in results:
        if r["cost_raw"] is not None and r["length_m"]:
            ratio = r["cost_raw"] / r["length_m"]
            r["cost"] = round(min(max(ratio - 1.0, 0.0), 1.0), 3)
        else:
            r["cost"] = None
    return results

# ============================================================
# MODUL 5: Export
# ============================================================
def export_zone_and_routes(meidungszone_gdf, results, gpkg_path, stage_id):
    if meidungszone_gdf is not None:
        meidungszone_gdf.to_file(gpkg_path, layer=f"meidungszone_{stage_id}", driver="GPKG")

    rows = []
    for r in results:
        if r["path"]:
            rows.append({
                "from": r["from"], "to": r["to"],
                "length_m": r["length_m"],
                "duration_min": r["duration_min"],
                "cost": r["cost"],
                "geometry": LineString(r["path"])
            })
    gdf_out = gpd.GeoDataFrame(rows, crs="EPSG:32635")
    gdf_out.to_file(gpkg_path, layer=f"routen_{stage_id}", driver="GPKG")
    print(f"Stufe {stage_id}: {len(gdf_out)} Routen exportiert (meidungszone_{stage_id}, routen_{stage_id})")
    return gdf_out


# ============================================================
# HAUPTABLAUF
# ============================================================
if __name__ == "_main_":
    streets, sites = load_base_data(GPKG_PATH)
    criticality = load_criticality_layers(GPKG_PATH)
    target_crs = streets.crs

    for stage_id, buffer_m in BUFFER_STAGES.items():
        print(f"\n--- Meidungszone {stage_id} ({buffer_m} m, + Innenstadt {CITY_CENTER_RADIUS_M} m) ---")
        zone = build_meidungszonen(criticality, buffer_m, target_crs)
        streets_weighted = compute_weights(streets, zone)
        G = build_graph(streets_weighted)
        site_nodes = {row["name_eng"]: snap_to_graph(G, row.geometry) for _, row in sites.iterrows()}
        results = compute_routes(G, site_nodes)
        results = normalize_cost(results)
        export_zone_and_routes(zone, results, GPKG_PATH, stage_id)