import geopandas as gpd
import pandas as pd
import networkx as nx
from itertools import combinations
from shapely.geometry import LineString
import math

# --- Daten einlesen ---
print("Lese Daten ein...")
streets = gpd.read_file(r"C:\Users\PC\Desktop\Hackathon\Python\Lviv_hackathon.gpkg", layer="lviv_hackathon — streets_lviv")
sites = gpd.read_file(r"C:\Users\PC\Desktop\Hackathon\Python\Lviv_hackathon.gpkg", layer="lviv_hackathon — mul_objects")

# --- Graph aus Straßen-Layer bauen ---
def build_graph(streets_gdf):
    G = nx.Graph()
    for idx, row in streets_gdf.iterrows():
        # Auf ganze Meter runden (0 Nachkommastellen) für saubere Verbindungen
        coords = [(c[0], c[1]) for c in row.geometry.coords]
        oneway = row.get("oneway", None)
        
        bridge_val = str(row.get("bridge", "")).strip().lower()
        is_bridge = (bridge_val == "yes")
        b_id = str(row.get("full_id")) if is_bridge and pd.notna(row.get("full_id")) else None
        
        for i in range(len(coords) - 1):
            u, v = coords[i], coords[i+1]
            if u != v:
                seg_len = float(math.hypot(u[0] - v[0], u[1] - v[1]))
                G.add_edge(u, v, weight=seg_len, bridge_id=b_id)
                if oneway != "yes":
                    G.add_edge(v, u, weight=seg_len, bridge_id=b_id)
                    #print(f"Graph ist vollständigverbunden: {nx.is_connected(G)}")
                    #print(f"Anzahl isolierter Netzwerke: {nx.number_connected_components(G)}")
    return G



# --- Standort-Koordinaten mit Netz verbinden ---
def snap_to_graph(G, point):
    nodes = list(G.nodes)
    if not nodes:
        return None

    p_x = point.x
    p_y = point.y

    best_node = None
    min_dist_sq = float('inf')

    for n in nodes:
        dist_sq = (n[0] - p_x) **2 + (n[1] - p_y) **2
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best_node = n

    return best_node


site_nodes = {}
print(f"----DEBUG: Standort und ihr snapping----")

G_base = build_graph(streets)
for idx, row in sites.iterrows():
    name = row.get("name:en", f"Site_{idx}")
    node = snap_to_graph(G_base, row.geometry)
    if node:
        site_nodes[name] = node
        print(f"Standort: {name} | Punkt: (X: {row.geometry.x:.1f}, Y: {row.geometry.y:.1f})-->gesnappt auf knoten: {node}")
    else:
        print(f"Standort: {name} | kein Knoten gesnappt")
print(f"Anzahl gültiger side_nodes: {len(site_nodes)}")

print(f"Anzahl Straßen-Zeilen: {len(streets)}")
print(f"Anzahl Sites-Zeilen: {len(sites)}")
print(f"Graph Knoten: {G_base.number_of_nodes()} | Kanten: {G_base.number_of_edges()}")
print(f"CRS Streets: {streets.crs} | CRS Sites: {sites.crs}")

# --- Basis-Matrix & Routen robust berechnen (Fehlertolerant bei OSM-Lücken) ---
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
        base_matrix[(a, b)] = None

print(f"Erfolgreich berechnete Basis-Routen: {len(valid_routes)}")

# --- 1. Eindeutige Brücken gruppieren ---
unique_bridges = {}
bridge_streets = streets[streets["bridge"].astype(str).str.lower().str.strip() == "yes"]
print(f"DEBUG: Anzahl Brücken-Zeilen im DataFrame gefunden: {len(bridge_streets)}")

for idx, row in bridge_streets.iterrows():
    b_name = row.get("name:en")
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

# --- 2. Prüfen, welche dieser Brücken tatsächlich auf den Routen liegen ---
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

# --- 3. Jede relevante Brücke testen und bewerten ---
report = []

for key in relevant_keys:
    bridge_info = unique_bridges[key]
    bridge_name = bridge_info["name"]
    target_ids = bridge_info["full_ids"]
    
    # Brücke(n) aus dem Straßennetz entfernen
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

# --- 4. Ergebnis sortieren und abspeichern ---
report_sorted = sorted(report, key=lambda r: r["betroffene_verbindungen"], reverse=True)

if report_sorted:
    report_df = pd.DataFrame(report_sorted)
    bridge_geoms = bridge_streets.drop_duplicates(subset=["full_id"])
    bridge_geoms["full_id"] = bridge_geoms["full_id"].astype(str)
    report_df["bridge_id"] = report_df["bridge_id"].astype(str)
    
    bridges_result = bridge_geoms.merge(report_df, left_on="full_id", right_on="bridge_id")
    output_filename = "crit_bridges_result.gpkg"
    bridges_result.to_file(output_filename, layer="critical_bridges", driver="GPKG")
    print(f"Erfolgreich gespeichert unter: {output_filename}")

# --- Zusätzlicher Layer für die anderen relevanten Brücken ---
# Wir holen uns alle relevanten Brücken-Keys und filtern die final kritischen heraus
crit_ids = list(bridges_result["full_id"].astype(str)) if 'bridges_result' in locals() and not bridges_result.empty else []

other_relevant_keys = [k for k in relevant_keys if str(unique_bridges[k].get("full_ids", [""])[0]) not in crit_ids]

if other_relevant_keys:
    # Geometrien für die anderen relevanten Brücken sammeln
    other_geoms = []
    for k in other_relevant_keys:
        t_ids = unique_bridges[k]["full_ids"]
        sub_df = bridge_streets[bridge_streets["full_id"].astype(str).isin([str(x) for x in t_ids])]
        if not sub_df.empty:
            # Wir nehmen die Linestring-Geometrien dieser Brücken
            for geom in sub_df.geometry:
                other_relevant_records = {
                    "bridge_id": str(t_ids[0]),
                    "name": unique_bridges[k]["name"],
                    "geometry": geom
                }
                # Als GeoDataFrame direkt in eine Liste packen oder einzeln anhängen
    
    # Eleganter Weg über ein separates GeoDataFrame:
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
        # Doppelte IDs bereinigen
        other_gdf = other_gdf.drop_duplicates(subset=["bridge_id"])
        
        # In dieselbe GPKG-Datei, aber als neuen Layer speichern!
        output_filename = "crit_bridges_result.gpkg"
        other_gdf.to_file(output_filename, layer="other_relevant_bridges", driver="GPKG")
        print(f"Erfolgreich als Layer 'other_relevant_bridges' in {output_filename} gespeichert!")

# --- Berechnete Routen für die Kontrolle als GPKG exportieren ---
print(f"DEBUG: Anzahl Einträge in valid_routes: {len(valid_routes)}")
route_records = []
for (a, b), path in valid_routes.items():
    print(f"Prüfe Route {a} -> {b} (Pfad-länge:{len(path) if path else 'leer'})")
    if path and len(path) >= 2:
        try:
            line = LineString(path)
            route_records.append({
                "source": str(a),
                "target": str(b),
                "length_m": base_matrix.get((a, b), 0),
                "geometry": line
            })
        except Exception as e:
            print(f"Fehler beim ertellen der Geometrie: {e}")
print(f"DEBUG: Anzahl erfolgreich erstellter route_records: {len(route_records)}")

if route_records:
    routes_gdf = gpd.GeoDataFrame(route_records, crs=streets.crs)
    output_filename = "calc_routes.gpkg"
    routes_gdf.to_file(output_filename, layer="shortest_path", driver="GPKG")
    print("Kontroll-Routen erfolgreich unter 'calculated_routes.gpkg' gespeichert.")
else:
    print("Keine gültigen Routen zum Exportieren gefunden.")



# --- Vollständiger Stresstest für die kritischen Brücken ---
print("\n--- STRESSTEST: Simulation von Brückensperrungen ---")

# Wir nehmen eine Kopie des Graphen, um das Original nicht zu verändern
G_test = G_base.copy()

# Beispielhaft testen wir den Ausfall zwischen zwei deiner Standorte
# (z.B. dem ersten und zweiten Standort in deinem site_nodes-Dictionary)
site_names = list(site_nodes.keys())
if len(site_names) >= 2:
    s1, s2 = site_names[2], site_names[5]
    node_a = site_nodes[s1]
    node_b = site_nodes[s2]
    
    try:
        # Originaler Weg vor der Sperrung
        path_before = nx.shortest_path(G_test, source=node_a, target=node_b, weight="weight")
        length_before = nx.shortest_path_length(G_test, source=node_a, target=node_b, weight="weight")
        print(f"Test-Route von {s1} nach {s2}:")
        print(f"  - Länge vor Sperrung: {length_before:.1f} Meter ({len(path_before)} Knoten)")
        
        # Wir suchen alle Kanten auf diesem Pfad, die eine Brücke sind, und sperren sie testweise
        edges_in_path = list(zip(path_before[:-1], path_before[1:]))
        bridges_on_path = []
        
        for u, v in edges_in_path:
            edge_data = G_test.get_edge_data(u, v)
            if edge_data and edge_data.get('bridge_id') is not None:
                bridges_on_path.append((u, v, edge_data['bridge_id']))
        
        print(f"  - Auf dieser Route gefundene Brücken-Kanten: {len(bridges_on_path)}")
        
        if bridges_on_path:
            # Test: Wir entfernen die erste Brücke auf diesem Pfad aus dem Test-Graphen
            u_b, v_b, b_id = bridges_on_path[0]
            G_test.remove_edge(u_b, v_b)
            
            # Versuchen, den Pfad neu zu berechnen
            try:
                length_after = nx.shortest_path_length(G_test, source=node_a, target=node_b, weight="weight")
                umweg = length_after - length_before
                print(f"  -> Nach Sperrung von Brücke {b_id}: Neuer Weg gefunden!")
                print(f"  -> Neue Länge: {length_after:.1f} Meter (Umweg von +{umweg:.1f} Metern)")
            except nx.NetworkXNoPath:
                print(f"  -> KRITISCH: Nach Sperrung von Brücke {b_id} gibt es KEINEN Weg mehr zwischen diesen Standorten!")
                
    except Exception as e:
        print(f"Fehler beim Stresstest: {e}")