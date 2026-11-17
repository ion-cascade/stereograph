"""Bounded profile validation shared by native motion review and fixtures."""

from dataclasses import dataclass
from math import ceil, isfinite

MAX_RULES = 16
MAX_SAMPLES = 384
FRAME_LIMIT = 1048574
MODES = ("clear", "allowed", "required")
VIEWS = ("front", "side", "top", "isometric")


def mapping(value, label, allowed):
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError(f"{label} must be an object with keys from {', '.join(sorted(allowed))}.")
    return value


def number(value, label, low, high):
    if type(value) not in (int, float) or not isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number from {low:g} to {high:g}.")
    return float(value)


def name(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"Provide a non-empty {label} of at most 256 characters.")
    return value


def interval(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} must contain start and end frames.")
    start, end = (number(item, label, -FRAME_LIMIT, FRAME_LIMIT) for item in value)
    if end < start:
        raise ValueError(f"{label} must be in ascending order.")
    return round(start, 6), round(end, 6)


@dataclass(frozen=True)
class Region:
    object: str
    group: str | None
    weight: float
    material: str | None

    @classmethod
    def parse(cls, value):
        data = mapping(value, "Surface", {"object", "vertex_group", "minimum_weight", "material"})
        group = name(data["vertex_group"], "vertex group") if "vertex_group" in data else None
        if "minimum_weight" in data and group is None:
            raise ValueError("minimum_weight requires a vertex_group.")
        return cls(
            name(data.get("object"), "mesh object name"), group,
            number(data.get("minimum_weight", .5), "minimum_weight", .001, 1),
            name(data["material"], "material name") if "material" in data else None,
        )


@dataclass(frozen=True)
class Rule:
    id: str
    surfaces: tuple
    interval: tuple[float, float]
    mode: str
    gap: float
    containment: bool

    @classmethod
    def parse(cls, value, span, surfaces):
        data = mapping(value, "Rule", {"id", "pair", "frames", "mode", "gap", "check_containment"})
        key = name(data.get("id"), "rule ID")
        pair = data.get("pair")
        if (not isinstance(pair, list) or len(pair) != 2 or any(
            not isinstance(item, str) or item not in surfaces for item in pair
        ) or pair[0] == pair[1]):
            raise ValueError(f"{key!r} needs two different named surfaces.")
        if surfaces[pair[0]].object == surfaces[pair[1]].object:
            raise ValueError(f"{key!r} needs surfaces on different mesh objects.")
        frames = interval(data.get("frames", list(span)), "Rule frames")
        if frames[0] < span[0] or frames[1] > span[1]:
            raise ValueError(f"{key!r} extends outside the review range.")
        mode = data.get("mode")
        if mode not in MODES:
            raise ValueError(f"{key!r} needs a clear, allowed or required mode.")
        if mode == "allowed" and "gap" in data:
            raise ValueError(f"{key!r} allows contact at any distance, so it takes no gap.")
        check = data.get("check_containment", False)
        if type(check) is not bool:
            raise ValueError("check_containment must be true or false.")
        return cls(key, tuple(pair), frames, mode,
                   number(data.get("gap", 0), "Gap", 0, 1e6), check)

    def base_frames(self, step):
        start, end = self.interval
        return sorted({round(start + step * i, 6) for i in range(ceil((end - start) / step))} | {end})


@dataclass(frozen=True)
class Profile:
    name: str
    frames: tuple
    step: float
    refine_step: float
    near_gap: float
    action_object: str | None
    action_name: str | None
    surfaces: dict
    rules: tuple

    @classmethod
    def parse(cls, value):
        data = mapping(value, "Profile", {
            "version", "name", "frames", "sampling", "action", "surfaces", "rules",
        })
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Use motion review profile version 1.")
        span = interval(data.get("frames"), "Review range")
        cfg = mapping(data.get("sampling", {}), "Sampling", {"step", "refine_step", "near_gap"})
        step = number(cfg.get("step", 1), "Step", .001, 1)
        refine_step = number(cfg.get("refine_step", min(.25, step)), "Refinement step", .001, step)
        near_gap = number(cfg.get("near_gap", .001), "Near gap", 0, 1e6)
        action = mapping(data.get("action", {}), "Action", {"object", "name"})
        action_object = name(action.get("object"), "Action object") if "action" in data else None
        action_name = name(action.get("name"), "Action name") if "action" in data else None
        refs = data.get("surfaces")
        if not isinstance(refs, dict) or not 2 <= len(refs) <= MAX_RULES * 2:
            raise ValueError("Supply between 2 and 32 named surfaces.")
        surfaces = {name(key, "surface ID"): Region.parse(spec) for key, spec in refs.items()}
        terms = data.get("rules")
        if not isinstance(terms, list) or not 1 <= len(terms) <= MAX_RULES:
            raise ValueError(f"Supply between 1 and {MAX_RULES} rules.")
        rules = tuple(Rule.parse(item, span, surfaces) for item in terms)
        if len({rule.id for rule in rules}) != len(rules):
            raise ValueError("Rule IDs must be unique.")
        if sum(ceil((rule.interval[1] - rule.interval[0]) / step) + 1 for rule in rules) > MAX_SAMPLES:
            raise ValueError(f"Base sampling exceeds {MAX_SAMPLES} rule samples. Split the review range.")
        return cls(name(data.get("name"), "profile name"), span, step, refine_step, near_gap,
                   action_object, action_name, surfaces, rules)
