"""Catalog of destinations, location search, and geocoding helpers for trip booking without coordinates."""

import math
import urllib.parse
import urllib.request
import json
from decimal import Decimal
from django.db.models import Q


LANDMARKS = [
    {
        "label": "Hazrat Shahjalal International Airport",
        "address": "Kurmitola, Airport Road, Dhaka",
        "latitude": 23.8433,
        "longitude": 90.3978,
        "category": "Airport / Transit",
    },
    {
        "label": "Banani Road 11",
        "address": "Banani Commercial Area, Dhaka",
        "latitude": 23.7937,
        "longitude": 90.4043,
        "category": "Shopping & Dining",
    },
    {
        "label": "Banani Model Town",
        "address": "Block B/C/D, Banani, Dhaka",
        "latitude": 23.7905,
        "longitude": 90.4010,
        "category": "Residential Area",
    },
    {
        "label": "Gulshan 1 Circle",
        "address": "Gulshan Avenue, Dhaka",
        "latitude": 23.7783,
        "longitude": 90.4168,
        "category": "Commercial Hub",
    },
    {
        "label": "Gulshan 2 Circle",
        "address": "Gulshan Avenue / Madani Avenue, Dhaka",
        "latitude": 23.7925,
        "longitude": 90.4167,
        "category": "Commercial Hub",
    },
    {
        "label": "Dhanmondi 27 (Mirpur Road)",
        "address": "Road 27, Dhanmondi, Dhaka",
        "latitude": 23.7516,
        "longitude": 90.3756,
        "category": "Commercial & Dining",
    },
    {
        "label": "Dhanmondi 32 / Bangabandhu Museum",
        "address": "Road 32, Dhanmondi, Dhaka",
        "latitude": 23.7508,
        "longitude": 90.3804,
        "category": "Historic Landmark",
    },
    {
        "label": "Uttara Sector 3 (Rabindra Sarani)",
        "address": "Sector 3, Uttara Model Town, Dhaka",
        "latitude": 23.8683,
        "longitude": 90.3956,
        "category": "Hub & Dining",
    },
    {
        "label": "Uttara Sector 10",
        "address": "Sector 10, Uttara, Dhaka",
        "latitude": 23.8795,
        "longitude": 90.3860,
        "category": "Residential Area",
    },
    {
        "label": "Bashundhara R/A (Main Gate)",
        "address": "Pragati Sarani, Bashundhara, Dhaka",
        "latitude": 23.8180,
        "longitude": 90.4285,
        "category": "Residential Area",
    },
    {
        "label": "Jamuna Future Park",
        "address": "Ka-244, Kuril, Pragati Sarani, Dhaka",
        "latitude": 23.8135,
        "longitude": 90.4242,
        "category": "Shopping Mall",
    },
    {
        "label": "Bashundhara City Shopping Mall",
        "address": "Panthapath, Tejgaon, Dhaka",
        "latitude": 23.7510,
        "longitude": 90.3908,
        "category": "Shopping Mall",
    },
    {
        "label": "Motijheel Commercial Area / Shapla Chattar",
        "address": "Motijheel C/A, Dhaka",
        "latitude": 23.7289,
        "longitude": 90.4190,
        "category": "Financial District",
    },
    {
        "label": "Mirpur 10 Circle",
        "address": "Mirpur 10, Dhaka",
        "latitude": 23.8069,
        "longitude": 90.3687,
        "category": "Metro / Transit Hub",
    },
    {
        "label": "Mirpur 1 / Sony Cinema",
        "address": "Mirpur 1, Dhaka",
        "latitude": 23.7975,
        "longitude": 90.3541,
        "category": "Commercial Hub",
    },
    {
        "label": "Mohakhali Wireless Gate / DOHS",
        "address": "Mohakhali, Dhaka",
        "latitude": 23.7776,
        "longitude": 90.4012,
        "category": "Transit & Residential",
    },
    {
        "label": "Dhaka University / TSC / Curzon Hall",
        "address": "Shahbagh, University of Dhaka, Dhaka",
        "latitude": 23.7262,
        "longitude": 90.4011,
        "category": "University Campus",
    },
    {
        "label": "Shahbagh Circle / BSMMU",
        "address": "Shahbagh, Kazi Nazrul Islam Ave, Dhaka",
        "latitude": 23.7388,
        "longitude": 90.3958,
        "category": "Hospital & Hub",
    },
    {
        "label": "Kawran Bazar",
        "address": "Kazi Nazrul Islam Avenue, Dhaka",
        "latitude": 23.7514,
        "longitude": 90.3934,
        "category": "Business District",
    },
    {
        "label": "Kamalapur Railway Station",
        "address": "Station Road, Kamalapur, Dhaka",
        "latitude": 23.7335,
        "longitude": 90.4255,
        "category": "Railway Terminal",
    },
    {
        "label": "Lalbagh Fort",
        "address": "Lalbagh, Old Dhaka",
        "latitude": 23.7188,
        "longitude": 90.3882,
        "category": "Tourist Attraction",
    },
    {
        "label": "Baridhara Diplomatic Zone",
        "address": "Park Road, Baridhara, Dhaka",
        "latitude": 23.7997,
        "longitude": 90.4225,
        "category": "Diplomatic Zone",
    },
    {
        "label": "Mohammadpur Town Hall",
        "address": "Asad Avenue, Mohammadpur, Dhaka",
        "latitude": 23.7584,
        "longitude": 90.3634,
        "category": "Commercial Center",
    },
    {
        "label": "Farmgate",
        "address": "Farmgate, Tejgaon, Dhaka",
        "latitude": 23.7561,
        "longitude": 90.3872,
        "category": "Transit Hub",
    },
    {
        "label": "Agargaon / Bangladesh National Museum & Metro",
        "address": "Mirpur Road, Agargaon, Dhaka",
        "latitude": 23.7770,
        "longitude": 90.3800,
        "category": "Government & Metro",
    },
    {
        "label": "Rampura Bridge",
        "address": "DIT Road, Rampura, Dhaka",
        "latitude": 23.7628,
        "longitude": 90.4258,
        "category": "Transit Hub",
    },
    {
        "label": "Badda Link Road",
        "address": "Middle Badda, Pragati Sarani, Dhaka",
        "latitude": 23.7745,
        "longitude": 90.4260,
        "category": "Commercial Corridor",
    },
    {
        "label": "Tejgaon Industrial Area",
        "address": "Tejgaon, Dhaka",
        "latitude": 23.7644,
        "longitude": 90.3995,
        "category": "Commercial & Tech",
    },
    {
        "label": "Sadarghat Launch Terminal",
        "address": "Buriganga Riverfront, Sadarghat, Dhaka",
        "latitude": 23.7058,
        "longitude": 90.4121,
        "category": "Waterfront Terminal",
    },
    {
        "label": "Old Dhaka / Chawkbazar",
        "address": "Chawkbazar, Lalbagh, Old Dhaka",
        "latitude": 23.7165,
        "longitude": 90.3992,
        "category": "Historic Commercial",
    },
]


def search_places(query="", user=None, limit=12):
    """
    Search places by name or address.
    Searches user's saved places first, then popular city landmarks.
    Falls back to external geocoding if query yields few results.
    """
    results = []
    seen = set()

    clean_q = (query or "").strip().lower()

    # 1. Search user's Saved Places if authenticated
    if user and getattr(user, "is_authenticated", False):
        try:
            from .models import SavedPlace
            saved_qs = SavedPlace.objects.filter(user=user)
            if clean_q:
                saved_qs = saved_qs.filter(Q(label__icontains=clean_q) | Q(address__icontains=clean_q))
            for sp in saved_qs[:5]:
                key = (round(float(sp.latitude), 4), round(float(sp.longitude), 4))
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "label": sp.label,
                        "address": sp.address or sp.label,
                        "latitude": float(sp.latitude),
                        "longitude": float(sp.longitude),
                        "category": "Saved Place",
                    })
        except Exception:
            pass

    # 2. Search predefined landmarks
    if not clean_q:
        # Default top picks
        for lm in LANDMARKS[:limit]:
            key = (round(lm["latitude"], 4), round(lm["longitude"], 4))
            if key not in seen:
                seen.add(key)
                results.append(lm)
    else:
        # Match query in label, address, or category
        q_words = clean_q.split()
        for lm in LANDMARKS:
            target_str = f"{lm['label']} {lm['address']} {lm['category']}".lower()
            if clean_q in target_str or all(word in target_str for word in q_words):
                key = (round(lm["latitude"], 4), round(lm["longitude"], 4))
                if key not in seen:
                    seen.add(key)
                    results.append(lm)

    # 3. If query has at least 3 characters and we have few results, try Nominatim fallback
    if clean_q and len(clean_q) >= 3 and len(results) < 3:
        try:
            url = f"https://nominatim.openstreetmap.org/search?format=json&limit=3&q={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"User-Agent": "NoriRideSharing/1.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data:
                    lat = float(item["lat"])
                    lon = float(item["lon"])
                    key = (round(lat, 4), round(lon, 4))
                    if key not in seen:
                        seen.add(key)
                        name = item.get("display_name", query).split(",")[0].strip()
                        results.append({
                            "label": name,
                            "address": item.get("display_name", name),
                            "latitude": lat,
                            "longitude": lon,
                            "category": "Search Result",
                        })
        except Exception:
            # Offline or timeout, safely ignore
            pass

    return results[:limit]


def resolve_location_name(name, user=None):
    """
    Resolve a place name into (latitude, longitude, label).
    Returns (lat, lon, label) tuple, or None if not found.
    """
    if not name:
        return None
    matches = search_places(name, user=user, limit=5)
    if matches:
        best = matches[0]
        return Decimal(str(best["latitude"])), Decimal(str(best["longitude"])), best["label"]
    return None


def reverse_geocode_name(latitude, longitude):
    """
    Find nearest known landmark or neighborhood label for coordinates.
    """
    lat = float(latitude)
    lon = float(longitude)
    nearest = None
    min_dist = float("inf")

    for lm in LANDMARKS:
        dlat = (lat - lm["latitude"]) * 111.0
        dlon = (lon - lm["longitude"]) * 111.0 * math.cos(math.radians(lat))
        dist_km = math.sqrt(dlat ** 2 + dlon ** 2)
        if dist_km < min_dist:
            min_dist = dist_km
            nearest = lm

    if nearest and min_dist <= 1.8:
        return f"{nearest['label']}, {nearest['address']}"
    return f"Pinned Location ({round(lat, 4)}, {round(lon, 4)})"
