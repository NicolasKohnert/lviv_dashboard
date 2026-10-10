import geopandas as gpd
import pandas as pd
import networkx as nx
from shapely.geometry import LineString
from itertools import combinations
import math

# --- 1. Daten einlesen ---
print("Lese Daten ein...")
streets = gpd.read_file(r"C:\Users\PC\Desktop\Hackathon\Python\Lviv_hackathon.gpkg", layer="lviv_hackathon - streets_lviv")
sites = gpd.read_file(r"C:\Users\PC\Desktop\Hackathon\Python\Lviv_hackathon.gpkg", layer="lviv_hackathon - mul_objects")

# WICHTIG: Koordinatensysteme synchronisieren
sites = sites.to_crs(streets.crs)


# --- 2. Graph aus Straßen-Layer bauen ---
def build_graph(streets_gdf):
    G = nx.Graph()
    for idx, row in streets_gdf.iterrows():
        coords = [(c[0], c[1]) for c in row.geometry.coords]
        oneway = row.get("oneway", None)
        
        bridge_val = str(row.get("bridge", "")).strip().lower()
        is_bridge = (bridge_val == "yes")
        b_id = str(row.get("full_id")) if is_bridge and pd.notna(row.get("full_id")) else None
        
        for i in range(len(coords) - 1):
            u, v = coords[i], coords[i+1]
            if u != v:
                # Sauber und sicher als float via math.hypot (verhindert komplexe Zahlen)
                seg_len = float(math.hypot(u[0] - v[0], u[1] - v[1]))
                
                G.add_edge(u, v, weight=seg_len, bridge_id=b_id)
                if oneway != "yes":
                    G.add_edge(v, u, weight=seg_len, bridge_id=b_id)
                    
    return G


# --- 3. Standort-Koordinaten robust mit Netz verbinden ---
def snap_to_graph(G, point):
    nodes = list(G.nodes)
    if not nodes:
        return None
    
    p_x = point.x
    p_y = point.y
    
    best_node = None
    min_dist_sq = float('inf')
    
    for n in nodes:
        dist_sq = (n[0] - p_x) * 2 + (n[1] - p_y) * 2
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best_node = n
            
    return best_node


# --- 4. Standorte ins Netz snappen ---
site_nodes = {}
print("----DEBUG: Standorte und ihr Snapping----")

G_base = build_graph(streets)

for idx, row in sites.iterrows():
    name = row.get("name_eng", f"Site_{idx}")
    node = snap_to_graph(G_base, row.geometry)
    
    if node:
        site_nodes[name] = node
        print(f"Standort: {name} | Punkt (X: {row.geometry.x:.1f}, Y: {row.geometry.y:.1f}) -> gesnappt auf Knoten: {node}")
    else:
        print(f"Standort: {name} | Kein Knoten gesnappt")

print(f"Anzahl gültiger site_nodes: {len(site_nodes)}")
print(f"Anzahl Straßen-Zeilen: {len(streets)}")
print(f"Anzahl Sites-Zeilen: {len(sites)}")
print(f"Graph Knoten: {G_base.number_of_nodes()} | Kanten: {G_base.number_of_edges()}")
print(f"CRS Streets: {streets.crs} | CRS Sites: {sites.crs}")


# --- 5. Basis-Matrix & Routen berechnen ---
print("Berechne Basis-Matrix und gültige Routen...")
base_matrix = {}
valid_routes = {}

for a, b in combinations(site_nodes.keys(), 2):
    node_a = site_nodes[a]
    node_b = site_nodes[b]
    if node_a == node_b:
        continue
    try:
        length = nx.shortest_path_length(G_base, source=node_a, target=node_b, weight="weight")
        path = nx.shortest_path(G_base, source=node_a, target=node_b, weight="weight")
        base_matrix[(a, b)] = length
        valid_routes[(a, b)] = path
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass

print(f"Erfolgreich berechnete Basis-Routen: {len(valid_routes)}")


# --- 6. Eindeutige Brücken gruppieren ---
unique_bridges = {}
bridge_streets = streets[streets["bridge"].astype(str).str.lower().str.strip() == "yes"]
print(f"DEBUG: Anzahl Brücken-Zeilen im DataFrame gefunden: {len(bridge_streets)}")

for idx, row in bridge_streets.iterrows():
    b_name = row.get("name_eng")
    if pd.isna(b_name):
        b_name = row.get("name")
    b_id = str(row.get("full_id"))
    
    key = b_name if pd.notna(b_name) else b_id
    if key not in unique_bridges:
        unique_bridges[key] = {
            "name": b_name if pd.notna(b_name) else "Unbenannt",
            "full_ids": []
        }
    if b_id not in unique_bridges[key]["full_ids"]:
        unique_bridges[key]["full_ids"].append(b_id)

print(f"DEBUG: Gruppierte physische Brücken: {len(unique_bridges)}")


# --- 7. Prüfen, welche Brücken auf den Routen liegen ---
relevant_keys = set()
edges_with_bridge_id_count = 0

for pair, path in valid_routes.items():
    for i in range(len(path) - 1):
        u, v = path[i], path[i+1]
        edge_data = G_base.get_edge_data(u, v)
        if edge_data and "bridge_id" in edge_data:
            b_id = edge_data["bridge_id"]
            if b_id is not None:
                edges_with_bridge_id_count += 1
                for key, info in unique_bridges.items():
                    if b_id in info["full_ids"]:
                        relevant_keys.add(key)

print(f"DEBUG: Auf kürzesten Pfaden gefundene Brücken-Kanten insgesamt: {edges_with_bridge_id_count}")
print(f"DEBUG: Davon als relevant identifizierte Brücken-Gruppen: {len(relevant_keys)}")


# --- 8. Jede relevante Brücke testen und bewerten ---
report = []

for key in relevant_keys:
    bridge_info = unique_bridges[key]
    bridge_name = bridge_info["name"]
    target_ids = bridge_info["full_ids"]
    
    streets_wo_bridge = streets[~streets["full_id"].astype(str).isin(target_ids)]
    G_test = build_graph(streets_wo_bridge)
    
    affected = []
    for pair, base_len in base_matrix.items():
        if base_len is not None:
            node_a = site_nodes[pair[0]]
            node_b = site_nodes[pair[1]]
            try:
                test_len = nx.shortest_path_length(G_test, source=node_a, target=node_b, weight="weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                test_len = None
                
            if test_len is None:
                affected.append((pair, "abgeschnitten (vollständig zerstört)"))
            elif test_len > base_len * 1.05:
                umweg_m = test_len - base_len
                affected.append((pair, f"{umweg_m:.0f} m Umweg"))
    
    if affected:
        report.append({
            "bridge_id": target_ids[0],
            "name": bridge_name,
            "betroffene_verbindungen": len(affected),
            "details": affected
        })

print(f"Final ermittelte kritische Brücken: {len(report)}")


# --- 9. Export der Ergebnisse (GPKG mit zwei Layern) ---
report_sorted = sorted(report, key=lambda r: r["betroffene_verbindungen"], reverse=True)
output_filename = "crit_bridges_result.gpkg"

if report_sorted:
    report_df = pd.DataFrame(report_sorted)
    bridge_geoms = bridge_streets.drop_duplicates(subset=["full_id"])
    bridge_geoms["full_id"] = bridge_geoms["full_id"].astype(str)
    report_df["bridge_id"] = report_df["bridge_id"].astype(str)
    
    bridges_result = bridge_geoms.merge(report_df, left_on="full_id", right_on="bridge_id")
    
    # Layer 1: Absolut kritische Brücken
    bridges_result.to_file(output_filename, layer="critical_bridges", driver="GPKG")
    print(f"Erfolgreich als Layer 'critical_bridges' gespeichert unter: {output_filename}")
    
    # Layer 2: Andere relevante Brücken (die nicht im kritischen Layer sind)
    crit_ids = list(bridges_result["full_id"].astype(str)) if not bridges_result.empty else []
    other_relevant_keys = [k for k in relevant_keys if str(unique_bridges[k].get("full_ids", [""])[0]) not in crit_ids]
    
    other_rows = []
    for k in other_relevant_keys:
        t_ids = unique_bridges[k]["full_ids"]
        sub_df = bridge_streets[bridge_streets["full_id"].astype(str).isin([str(x) for x in t_ids])]
        for idx, row in sub_df.iterrows():
            other_rows.append({
                "bridge_id": str(row.get("full_id")),
                "name": unique_bridges[k]["name"],
                "geometry": row.geometry
            })
            
    if other_rows:
        other_gdf = gpd.GeoDataFrame(other_rows, crs=streets.crs)
        other_gdf = other_gdf.drop_duplicates(subset=["bridge_id"])
        other_gdf.to_file(output_filename, layer="other_relevant_bridges", driver="GPKG")
        print(f"Erfolgreich als Layer 'other_relevant_bridges' in {output_filename} gespeichert!")


# --- 10. Kontroll-Routen exportieren ---
route_records = []
for (a, b), path in valid_routes.items():
    if path and len(path) >= 2:
        try:
            line = LineString(path)
            route_records.append({
                "source": str(a),
                "target": str(b),
                "length_m": base_matrix.get((a, b), 0),
                "geometry": line
            })
        except Exception:
            pass
            
if route_records:
    routes_gdf = gpd.GeoDataFrame(route_records, crs=streets.crs)
    routes_gdf.to_file("calc_routes.gpkg", layer="calculated_routes", driver="GPKG")
    print("Kontroll-Routen erfolgreich unter 'calc_routes.gpkg' gespeichert.")