"""Editable garment and acceptance limits for a local corrective."""

from dataclasses import dataclass
from math import sqrt

from ..review.profile import FRAME_LIMIT, Region, mapping, name, number
from ..rigging.profile import text_list, vector


@dataclass(frozen=True)
class Pair:
    surface: Region
    obstacle: Region
    minimum_gap: float

    @classmethod
    def parse(cls, raw, garment):
        data = mapping(raw, "Corrective pair", {"garment_region", "obstacle", "minimum_gap"})
        region = data.get("garment_region", {})
        if not isinstance(region, dict) or "object" in region:
            raise ValueError("garment_region takes optional surface filters for the corrective garment.")
        surface = Region.parse(dict(object=garment, **region))
        obstacle = Region.parse(data.get("obstacle"))
        if obstacle.object == garment:
            raise ValueError("The corrective obstacle must be a different object.")
        return cls(surface, obstacle, number(data.get("minimum_gap", 0), "Minimum gap", 0, 10))


@dataclass(frozen=True)
class Corrective:
    garment: str
    mask: str
    protected_groups: tuple
    protected_objects: tuple
    direction: tuple
    distance: float
    frame: float
    span: float
    times: tuple
    min_ratio: float
    max_ratio: float
    max_displacement: float
    protected_tolerance: float
    pairs: tuple
    cache_policy: str

    @classmethod
    def parse(cls, raw):
        data = mapping(raw, "Cloth corrective", {"version", "garment", "mask", "protected_groups",
                       "protected_objects", "direction", "distance", "frame", "span", "extra_frames",
                       "min_edge_ratio", "max_edge_ratio", "max_displacement", "protected_tolerance", "pairs", "cache_policy"})
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Use cloth corrective version 1.")
        garment = name(data.get("garment"), "garment object")
        mask = name(data.get("mask"), "editable corrective mask group")
        groups = text_list(data.get("protected_groups"), "Protected vertex groups", 8, 1)
        objects = text_list(data.get("protected_objects", []), "Protected detail objects", 8)
        if mask in groups or garment in objects:
            raise ValueError("Use separate corrective and protected regions.")
        direction = vector(data.get("direction"), "Garment-local direction")
        length = sqrt(sum(x*x for x in direction))
        if length < 1e-8:
            raise ValueError("Give a nonzero direction.")
        frame = round(number(data.get("frame"), "Correction frame", -FRAME_LIMIT+12, FRAME_LIMIT-12), 6)
        span = round(number(data.get("span", 2), "Correction half-width", .25, 12), 6)
        times = {round(frame + step*span, 6) for step in (-1, -.75, -.5, -.25, 0, .25, .5, .75, 1)}
        extra = data.get("extra_frames", [])
        if not isinstance(extra, list) or len(extra) > 8:
            raise ValueError("Provide at most eight additional sample times.")
        times.update(round(number(x, "Additional sample", -FRAME_LIMIT, FRAME_LIMIT), 6) for x in extra)
        pairs = data.get("pairs")
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 6:
            raise ValueError("Supply one repair pair followed by up to five guard pairs.")
        cache_policy = data.get("cache_policy", "current")
        if cache_policy not in ("current", "frozen_bake"):
            raise ValueError("Use current or frozen_bake as the cache policy.")
        return cls(garment, mask, tuple(groups), tuple(objects), tuple(x/length for x in direction),
                   number(data.get("distance"), "Local correction distance", 1e-6, .15), frame, span,
                   tuple(sorted(times)), number(data.get("min_edge_ratio", .9), "Minimum edge ratio", .01, 1),
                   number(data.get("max_edge_ratio", 1.1), "Maximum edge ratio", 1, 3),
                   number(data.get("max_displacement", .03), "Maximum world displacement", 1e-6, .3),
                   number(data.get("protected_tolerance", 1e-6), "Protected detail tolerance", 1e-8, .005),
                   tuple(Pair.parse(pair, garment) for pair in pairs), cache_policy)
