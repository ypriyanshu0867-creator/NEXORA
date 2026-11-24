"""Server-side geofence verification (Haversine).

Terminology: ``geo_min_accuracy_m`` in the database is the WORST accuracy
the teacher will accept: a reading whose reported error radius is larger
than this is rejected. Distance is compared strictly against the radius;
the reported accuracy is not subtracted from it.

Only the measured distance and accuracy are returned for storage. The
student's raw coordinates are never persisted.
"""
import math

from app.services.errors import ServiceError

EARTH_RADIUS_M = 6_371_008.8
MAX_REASONABLE_ACCURACY_M = 10_000


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres between two WGS-84 points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def parse_reading(payload):
    """Validate (latitude, longitude, accuracy) from a request body."""
    raw = [payload.get("latitude"), payload.get("longitude"), payload.get("accuracy")]
    if all(v is None for v in raw):
        if payload.get("location_error") == "denied":
            raise ServiceError("location_denied", "Location permission was denied. Allow location "
                               "access for this site in your browser settings and scan again.", 422)
        raise ServiceError("location_missing", "Your location was not received. Turn on location "
                           "services and scan again.", 422)
    lat, lng, acc = (_number(v) for v in raw)
    if lat is None or lng is None or acc is None \
            or not -90 <= lat <= 90 or not -180 <= lng <= 180 \
            or not 0 < acc <= MAX_REASONABLE_ACCURACY_M:
        raise ServiceError("location_invalid", "The location sent by your device is not valid.", 422)
    return lat, lng, acc


def verify_for_session(session, payload):
    """Apply the session's geofence snapshot to a student's reading.

    Returns {"distance_m", "accuracy_m"} or None when the session has the
    geofence disabled. Raises ServiceError on any failed requirement.
    """
    if not session["geofence_enabled"]:
        return None
    lat, lng, acc = parse_reading(payload)
    max_accuracy = float(session["geo_min_accuracy_m"])
    if acc > max_accuracy:
        raise ServiceError("location_inaccurate",
                           f"Your GPS accuracy (about {acc:.0f} m) is worse than the allowed "
                           f"{max_accuracy:.0f} m. Move near a window or outdoors and try again.", 422)
    distance = haversine_m(lat, lng, float(session["geo_latitude"]), float(session["geo_longitude"]))
    radius = float(session["geo_radius_m"])
    if distance > radius:
        raise ServiceError("outside_radius",
                           f"You are about {distance:.0f} m from the classroom; the allowed "
                           f"distance is {radius:.0f} m.", 403)
    return {"distance_m": round(distance, 2), "accuracy_m": round(acc, 2)}
