"""Version 1 clothing layers, attachment landmarks, and local strain checks."""

from dataclasses import dataclass

from ..review.profile import mapping, name, number
from ..rigging.profile import identifier_map, text_list, vector

KINDS = ("cloth", "trim", "mail", "glove", "plate", "body", "support")


@dataclass(frozen=True)
class Layer:
    object: str
    kind: str
    representation: str | None
    weight_groups: tuple

    @classmethod
    def parse(cls, raw):
        values = mapping(raw, "Fit layer", {"object", "kind", "representation", "weight_groups"})
        kind = values.get("kind")
        if kind not in KINDS:
            raise ValueError(f"Choose a layer kind from {', '.join(KINDS)}.")
        representation = values.get("representation")
        if representation is not None and representation not in ("editable", "baked_playback"):
            raise ValueError("Layer representation must be editable or baked_playback.")
        return cls(name(values.get("object"), "layer object"), kind, representation,
                   tuple(text_list(values.get("weight_groups", []), "Weight groups", 8)))


@dataclass(frozen=True)
class Landmark:
    layer: str
    vertex: int | None
    point: tuple | None

    @classmethod
    def parse(cls, raw, layers):
        values = mapping(raw, "Attachment landmark", {"layer", "vertex", "point"})
        layer = _layer(values, layers)
        if ("vertex" in values) == ("point" in values):
            raise ValueError("Supply an evaluated vertex index or an object-local point for each landmark.")
        index = values.get("vertex")
        if "vertex" in values and (type(index) is not int or not 0 <= index < 20_000_000):
            raise ValueError("Use a nonnegative evaluated vertex index.")
        return cls(layer, index, vector(values["point"], "Object-local landmark") if "point" in values else None)


def _layer(values, layers):
    key = values.get("layer")
    if not isinstance(key, str) or key not in layers:
        raise ValueError("A diagnostic references an undeclared layer.")
    return key


@dataclass(frozen=True)
class Attachment:
    a: Landmark
    b: Landmark
    kind: str
    max_gap: float
    max_drift: float

    @classmethod
    def parse(cls, raw, layers):
        values = mapping(raw, "Attachment", {"a", "b", "kind", "max_gap", "max_drift"})
        a, b = (Landmark.parse(values.get(key), layers) for key in ("a", "b"))
        if a == b:
            raise ValueError("Use distinct attachment landmarks.")
        kind = values.get("kind")
        if kind not in ("seam", "attachment"):
            raise ValueError("Choose seam or attachment as the landmark relationship.")
        return cls(a, b, kind, number(values.get("max_gap"), "Maximum gap", 0, 1e6),
                   number(values.get("max_drift"), "Maximum drift", 0, 1e6))


@dataclass(frozen=True)
class Strain:
    layer: str
    group: str | None
    min_ratio: float
    max_ratio: float

    @classmethod
    def parse(cls, raw, layers):
        values = mapping(raw, "Local deformation", {"layer", "vertex_group", "min_ratio", "max_ratio"})
        return cls(_layer(values, layers),
                   name(values["vertex_group"], "strain vertex group") if "vertex_group" in values else None,
                   number(values.get("min_ratio", .9), "Minimum edge ratio", .01, 1),
                   number(values.get("max_ratio", 1.1), "Maximum edge ratio", 1, 10))


@dataclass(frozen=True)
class FitProfile:
    name: str
    layers: dict
    attachments: dict
    strain: dict
    contacts: dict | None

    @classmethod
    def parse(cls, raw):
        values = mapping(raw, "Clothing fit profile", {"version", "name", "layers", "attachments", "strain", "contacts"})
        if type(values.get("version")) is not int or values["version"] != 1:
            raise ValueError("Use clothing fit profile version 1.")
        layers = identifier_map(values.get("layers"), "Layers", 32, Layer.parse, 1)
        attachments = identifier_map(values.get("attachments", {}), "Attachment checks", 16,
                                     lambda item: Attachment.parse(item, layers))
        strain = identifier_map(values.get("strain", {}), "Local deformation checks", 12,
                                lambda item: Strain.parse(item, layers))
        contacts = values.get("contacts")
        if "contacts" in values and not isinstance(contacts, dict):
            raise ValueError("contacts must be an existing motion-contact profile.")
        return cls(name(values.get("name"), "clothing fit name"), layers, attachments, strain, contacts)
