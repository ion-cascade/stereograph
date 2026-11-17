"""Orthographic witness diagrams from the exact evaluated triangles."""

from html import escape

import numpy as np

from .projection import embedded_context

WIDTH = 960
HEIGHT = 640
CENTER = np.array([480, 326])
SCALE = 240
VIEWS = {
    "front": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "side": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "top": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "isometric": ((.70710678, .70710678, 0), (-.40824829, .40824829, .81649658),
                  (.57735027, -.57735027, .57735027)),
}
COLORS = ("#4da8e6", "#e6ad54")


def _projection(points, center, right, up, radius):
    shifted = points - center
    return np.stack((shifted @ right, -(shifted @ up)), axis=-1) * (SCALE / radius) + CENTER


def _outline(surface, index, center, radius, right, up, color):
    projected = _projection(surface.triangles[index], center, right, up, radius)
    coordinates = " ".join(f"{x:.1f},{y:.1f}" for x, y in projected)
    return f'<polygon points="{coordinates}" fill="none" stroke="{color}" stroke-width="2.5"/>'


def _text(x, y, text, size, color="#e1e7f1", bold=False):
    weight = 700 if bold else 400
    return f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}">{escape(text)}</text>'


def diagram(a, b, witness, sample, orientation, deadline):
    right, up, depth = map(np.asarray, VIEWS[orientation])
    landmarks = np.stack((witness.point_a, witness.point_b))
    center = landmarks.mean(axis=0)
    shortest_object = min(np.linalg.norm(a.root.high - a.root.low), np.linalg.norm(b.root.high - b.root.low))
    radius = max(float(np.linalg.norm(landmarks[0] - landmarks[1])) * .85, float(shortest_object) * .29, 1e-8)
    background = embedded_context(a, b, center, radius, (right, up, depth), deadline)
    outlines = [_outline(surface, index, center, radius, right, up, color)
                for surface, index, color in zip((a, b), witness.indices, COLORS)]
    pa, pb = _projection(landmarks, center, right, up, radius)
    x1, y1 = pa
    x2, y2 = pb
    midpoint = (pa + pb) / 2
    label = f"{sample['surface_relation'].replace('_', ' ')} · {sample['distance']:.6g} BU"
    names = [name if len(name) <= 56 else name[:53] + "…" for name in sample["pair"]]
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<title>Evaluated surface witness</title><rect width="960" height="640" fill="#141a24"/>',
        '<g font-family="DejaVu Sans,sans-serif">',
        _text(28, 34, "Evaluated surface witness", 22, bold=True),
        _text(28, 60, f"Frame {sample['frame']:g} · {orientation} orthographic · {label}", 15),
        '<clipPath id="geometry"><rect x="18" y="80" width="924" height="472"/></clipPath>',
        '<rect x="18" y="80" width="924" height="472" fill="#1d2634" stroke="#354354"/>',
        '<g clip-path="url(#geometry)">',
        f'<image x="18" y="80" width="924" height="472" preserveAspectRatio="none" xlink:href="data:image/png;base64,{background}"/>',
        *outlines,
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#ffffff" stroke-width="3"/>',
        f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="10" fill="none" stroke="#71c7ff" stroke-width="3"/>',
        f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="6" fill="#ffcd7a" stroke="#171a21" stroke-width="2"/>',
        f'<line x1="{midpoint[0]:.1f}" y1="{midpoint[1]-12:.1f}" x2="{midpoint[0]+48:.1f}" y2="{midpoint[1]-45:.1f}" stroke="#ffffff" stroke-width="2"/>',
        f'<rect x="{midpoint[0]+45:.1f}" y="{midpoint[1]-64:.1f}" width="154" height="28" rx="4" fill="#141a24"/>',
        _text(midpoint[0] + 55, midpoint[1] - 45, "Surface witness", 14, "#ffffff"),
        '</g>',
        _text(28, 585, f"A · {names[0]}", 15, COLORS[0], bold=True),
        _text(485, 585, f"B · {names[1]}", 15, COLORS[1], bold=True),
        '<line x1="28" y1="613" x2="148" y2="613" stroke="#a8b5c6" stroke-width="2"/>',
        _text(155, 618, f"{radius / 2:.5g} BU", 13, "#a8b5c6"),
        _text(485, 618, "Local mesh context · outlined witness triangles", 13, "#a8b5c6"),
        '</g></svg>',
    ]
    return "".join(body)
