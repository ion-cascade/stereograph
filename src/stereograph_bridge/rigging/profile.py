"""Versioned semantic rig maps and regional check definitions."""

from dataclasses import dataclass

from ..review.profile import FRAME_LIMIT, interval, mapping, name, number

METRICS = {
    "arm": ("shoulder", "elbow", "wrist"),
    "finger": ("base", "middle", "tip"),
    "thumb": ("palm", "cmc", "mcp", "ip"),
    "leg": ("hip", "knee", "foot"),
    "plant": ("foot",),
}
METRIC_VALUES = {
    "arm": {"reach_ratio", "elbow_bend", "wrist_bend"},
    "finger": {"base_bend", "tip_bend", "curl"},
    "thumb": {"basal_rotation", "basal_sweep", "basal_elevation", "mcp_bend", "ip_bend", "tip_to_palm"},
    "leg": {"tracking", "knee_bend", "upper_length", "lower_length"},
    "plant": {"position_drift", "rotation_drift"},
}
MAX_FRAMES = 64
MAX_ROLES = 80


def vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} needs three coordinates.")
    return tuple(number(item, label, -1e6, 1e6) for item in value)


def axis(value, label):
    result = vector(value, label)
    if sum(x * x for x in result) < 1e-12:
        raise ValueError(f"{label} needs a nonzero direction.")
    return result


def identifier_map(data, label, maximum, parse, minimum=0):
    if not isinstance(data, dict) or not minimum <= len(data) <= maximum:
        raise ValueError(f"{label} needs {minimum} to {maximum} named entries.")
    return {name(key, f"{label} ID"): parse(value) for key, value in data.items()}


def text_list(values, label, maximum, minimum=0):
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise ValueError(f"Provide {minimum} to {maximum} names for {label}.")
    parsed = tuple(name(value, label) for value in values)
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{label} names must be distinct.")
    return parsed


@dataclass(frozen=True)
class Role:
    object: str
    bone: str
    control: str | None
    expected_axis: tuple | None
    local_axis: tuple
    axis_tolerance: float
    landmark: tuple | None
    landmark_tolerance: float

    @classmethod
    def parse(cls, data):
        item = mapping(data, "Bone role", {"object", "bone", "control", "expected_axis",
                                           "local_axis", "axis_tolerance", "landmark", "landmark_tolerance"})
        return cls(name(item.get("object"), "armature object"), name(item.get("bone"), "bone name"),
                   name(item["control"], "control name") if "control" in item else None,
                   axis(item["expected_axis"], "Expected rig-local rest axis") if "expected_axis" in item else None,
                   axis(item.get("local_axis", [0, 1, 0]), "Bone-local axis"),
                   number(item.get("axis_tolerance", 10), "Axis tolerance", 0, 180),
                   vector(item["landmark"], "Rig-local landmark") if "landmark" in item else None,
                   number(item.get("landmark_tolerance", .002), "Landmark tolerance", 0, 10))


@dataclass(frozen=True)
class Switch:
    object: str
    bone: str
    property: str
    ik: float
    fk: float
    ik_controls: tuple
    fk_controls: tuple

    @classmethod
    def parse(cls, data):
        item = mapping(data, "Limb switch", {"object", "bone", "property", "ik", "fk", "ik_controls", "fk_controls"})
        ik = number(item.get("ik", 0), "IK value", -1e3, 1e3)
        fk = number(item.get("fk", 1), "FK value", -1e3, 1e3)
        if abs(ik - fk) <= 1e-6:
            raise ValueError("IK and FK switch values must be distinct.")
        return cls(name(item.get("object"), "armature object"), name(item.get("bone"), "switch bone"),
                   name(item.get("property"), "switch property"), ik, fk,
                   text_list(item.get("ik_controls", []), "IK control", 12),
                   text_list(item.get("fk_controls", []), "FK control", 12))


@dataclass(frozen=True)
class Binding:
    mesh: str
    rig: str
    region: str | None
    expected: tuple
    tolerance: float

    @classmethod
    def parse(cls, data):
        item = mapping(data, "Binding", {"mesh", "rig", "region", "expected_bones", "weight_tolerance"})
        return cls(name(item.get("mesh"), "mesh object"), name(item.get("rig"), "armature object"),
                   name(item["region"], "vertex group") if "region" in item else None,
                   text_list(item.get("expected_bones", []), "expected deform bone", 64),
                   number(item.get("weight_tolerance", .01), "Weight tolerance", 1e-6, 1))


@dataclass(frozen=True)
class Check:
    kind: str
    roles: tuple
    forward: tuple
    anchor: str | None
    frames: tuple | None
    limits: dict
    contact_rule: str | None

    @classmethod
    def parse(cls, data, roles):
        item = mapping(data, "Pose check", {"kind", "roles", "forward", "anchor", "frames", "limits", "contact_rule"})
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in METRICS:
            raise ValueError("Pose check kind must be arm, finger, thumb, leg or plant.")
        raw = mapping(item.get("roles"), "Check role map", set(METRICS[kind]))
        if set(raw) != set(METRICS[kind]) or any(not isinstance(key, str) or key not in roles for key in raw.values()):
            raise ValueError(f"{kind} check requires these mapped roles: {', '.join(METRICS[kind])}.")
        references = tuple(raw[key] for key in METRICS[kind])
        if len(set(references)) != len(references):
            raise ValueError("Each measured joint needs a distinct bone role.")
        limits = mapping(item.get("limits", {}), "Check limits", METRIC_VALUES[kind])
        limits = {field: _bounds(value) for field, value in limits.items()}
        return cls(kind, references, axis(item.get("forward", [0, 1, 0]), "Reference forward"),
                   name(item["anchor"], "tracking or planting anchor") if "anchor" in item else None,
                   interval(item["frames"], "Check frame range") if "frames" in item else None, limits,
                   name(item["contact_rule"], "motion-contact rule ID") if "contact_rule" in item else None)


def _bounds(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("A metric limit needs lower and upper bounds.")
    low, high = (number(x, "Metric bound", -1e6, 1e6) for x in value)
    if low > high:
        raise ValueError("Metric limits must be in ascending order.")
    return low, high


@dataclass(frozen=True)
class InspectionProfile:
    name: str
    roles: dict
    switches: dict
    bindings: dict
    checks: dict

    @classmethod
    def parse(cls, data):
        item = mapping(data, "Inspection profile", {"version", "name", "roles", "switches", "bindings", "checks"})
        if type(item.get("version")) is not int or item["version"] != 1:
            raise ValueError("Use rig inspection profile version 1.")
        roles = identifier_map(item.get("roles"), "Joint roles", MAX_ROLES, Role.parse, 1)
        switches = identifier_map(item.get("switches", {}), "Limb switches", 16, Switch.parse)
        bindings = identifier_map(item.get("bindings", {}), "Skin bindings", 16, Binding.parse)
        checks = identifier_map(item.get("checks", {}), "Pose checks", 32, lambda value: Check.parse(value, roles))
        return cls(name(item.get("name"), "profile name"), roles, switches, bindings, checks)


def frames(values, start):
    if values is None:
        return [start]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_FRAMES:
        raise ValueError(f"Provide between 1 and {MAX_FRAMES} sample frames.")
    return sorted({round(number(x, "Frame", -FRAME_LIMIT, FRAME_LIMIT), 6) for x in values})
