"""Road routes from an OSRM-compatible routing service (no straight-line fallback)."""

import hashlib
import json
import math
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import APIException, ValidationError


class RoutingUnavailable(APIException):
    status_code = 503
    default_detail = "Road routing is temporarily unavailable. Please try again."


def _request_json(url):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Nori-Rides/1.0"})
    try:
        response = urlopen(request, timeout=settings.ROUTING_TIMEOUT_SECONDS)
    except HTTPError as error:
        if error.code != 400:
            raise
        response = error
    with response:
        return json.load(response)


def get_pickup_distances(origins, destination):
    """Return road kilometres from each driver to pickup; None means unreachable."""
    distances = []
    # Keep each matrix request below the usual 100-coordinate OSRM limit.
    for start in range(0, len(origins), 50):
        batch = origins[start:start + 50]
        points = [*batch, destination]
        locations = ";".join(f"{float(lon):.6f},{float(lat):.6f}" for lat, lon in points)
        query = urlencode({"sources": ";".join(str(i) for i in range(len(batch))),
                           "destinations": len(batch), "annotations": "distance",
                           "radiuses": ";".join(["1000"] * len(points))})
        url = f"{settings.ROUTING_BASE_URL.rstrip('/')}/table/v1/driving/{locations}?{query}"
        key = "pickup-distances:" + hashlib.sha256(url.encode()).hexdigest()
        values = cache.get(key)
        if values is None:
            try:
                payload = _request_json(url)
                if payload.get("code") in {"NoSegment", "NoTable"}:
                    # An unsnappable driver must not prevent reachable drivers matching.
                    values = []
                    for origin in batch:
                        try:
                            values.append(get_road_route([origin, destination])["distance_meters"] / 1000)
                        except ValidationError:
                            values.append(None)
                else:
                    if payload.get("code") != "Ok" or len(payload["distances"]) != len(batch):
                        raise ValueError("Invalid distance matrix")
                    values = []
                    for row in payload["distances"]:
                        if len(row) != 1:
                            raise ValueError("Invalid distance matrix row")
                        meters = row[0]
                        if meters is not None:
                            meters = float(meters)
                            if not math.isfinite(meters) or meters < 0:
                                raise ValueError("Invalid pickup distance")
                        values.append(None if meters is None else meters / 1000)
            except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
                raise RoutingUnavailable() from error
            cache.set(key, values, timeout=60)
        distances.extend(values)
    return distances


def get_road_route(coordinates):
    """Accept ordered (latitude, longitude) pairs; return GeoJSON and SI totals."""
    try:
        points = [(float(lat), float(lon)) for lat, lon in coordinates]
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError("Choose valid route locations.") from error
    if not 2 <= len(points) <= 10 or any(
        not (-90 <= lat <= 90 and -180 <= lon <= 180) for lat, lon in points
    ):
        raise ValidationError("Choose between 2 and 10 valid route locations.")
    locations = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    query = urlencode({
        "overview": "full", "geometries": "geojson", "steps": "false",
        "alternatives": "false", "radiuses": ";".join(["1000"] * len(points)),
    })
    url = f"{settings.ROUTING_BASE_URL.rstrip('/')}/route/v1/driving/{locations}?{query}"
    key = "road-route:" + hashlib.sha256(url.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        payload = _request_json(url)
        if payload.get("code") in {"NoRoute", "NoSegment"}:
            raise ValidationError("No driving route connects these locations. Choose points closer to a road.")
        if payload.get("code") != "Ok":
            raise ValueError("Routing service rejected the request")
        route = payload["routes"][0]
        distance, duration = float(route["distance"]), float(route["duration"])
        geometry = route["geometry"]
        if not all(math.isfinite(n) and n >= 0 for n in (distance, duration)):
            raise ValueError("Invalid route totals")
        if geometry["type"] != "LineString" or len(geometry["coordinates"]) < 2:
            raise ValueError("Missing route geometry")
        for lon, lat in geometry["coordinates"]:
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError("Invalid route geometry")
        result = {"distance_meters": distance, "duration_seconds": duration, "geometry": geometry}
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
        raise RoutingUnavailable() from error
    cache.set(key, result, timeout=300)
    return result
