import geopandas as gpd
import pandas as pd

try:
    #read locations
    print("read geodata from Geopackage...")
    print(f"Layer:{gpd.list_layers(r"C:\Users\PC\Desktop\Hackathon\Python\lviv_hackathon.gpkg")}")
    locations = gpd.read_file(r"C:\Users\PC\Desktop\Hackathon\Python\lviv_hackathon.gpkg", layer="medical_union_lviv__amenity_hospital_lviv")
    print("Geodata successfully read")

    #read CSV with waste values
    print("read CSV...")
    waste = pd.read_csv(r"C:\Users\PC\Desktop\Hackathon\Python\waste.csv", sep=";", encoding="utf-8")
    print("CSV successfully read")

    #join over full_id
    print("Join data...")
    locations = locations.drop(columns=[c for c in waste.columns if c != "full_id"], errors="ignore")
    locations = locations.merge(waste, on="full_id", how="left")
    print("Data successfully joined")

    #write in gpkg
    print("Write data back in the Geopackage...")
    locations.to_file(r"C:\Users\PC\Desktop\Hackathon\Python\lviv_hackathon.gpkg", layer="medical_union_lviv__amenity_hospital_lviv")
    print("Data successfully writen")

except Exception as e:
    print(f"Attention! Error: {e}")