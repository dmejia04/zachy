from garmin_auth import GarminAuth

def get_client():
    auth = GarminAuth()
    client = auth.login()
    return client

if __name__ == "__main__":
    client = get_client()
    print("Connected to Garmin!")

    activities = client.get_activities(0, 5)  # start=0, limit=5
    print(f"\nFound {len(activities)} activities\n")

    for act in activities:
        print(f"- {act.get('activityName')} | {act.get('startTimeLocal')} | "
              f"{act.get('distance', 0)/1000:.2f} km | "
              f"{act.get('duration', 0)/60:.1f} min")
