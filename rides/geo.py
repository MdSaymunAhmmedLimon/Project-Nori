import math
from decimal import Decimal, ROUND_HALF_UP


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(lambda n: math.radians(float(n)), (lat1, lon1, lat2, lon2))
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(value)))


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
