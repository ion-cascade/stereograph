"""Matched shoulder–elbow–wrist diagrams for the two evaluated poses."""

from html import escape
from math import sqrt


def _vector_length(v):
    return sqrt(sum(item * item for item in v))


def compare(before, after, frame, view):
    x = 0 if view == "front" else 1
    states = [before, after]
    names = ("shoulder", "elbow", "wrist", "palm")
    points = []
    for state in states:
        chain = [state[key] for key in names[:-1]]
        wrist = chain[-1]
        palm = [value + axis * state["forearm_length"] * .45
                for value, axis in zip(wrist, state["palm_direction"])]
        points.append([*chain, palm])
    all_points = points[0] + points[1]
    low_x, high_x = min(p[x] for p in all_points), max(p[x] for p in all_points)
    low_y, high_y = min(p[2] for p in all_points), max(p[2] for p in all_points)
    extent = max(high_x - low_x, high_y - low_y, .00001)
    scale = 262 / extent
    center_x, center_y = (low_x + high_x) / 2, (low_y + high_y) / 2
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="536" viewBox="0 0 980 536">',
        '<title>Planted hand elbow comparison</title><rect width="980" height="536" fill="#141a24"/>',
        '<g font-family="DejaVu Sans,sans-serif" fill="#e1e7f1">',
        f'<text x="28" y="37" font-size="24" font-weight="700">Planted hand · {escape(view)} view</text>',
        f'<text x="28" y="62" font-size="15">Frame {frame:g} · matched world-space projection</text>',
    ]
    for i, chain in enumerate(points):
        left = 18 + 480 * i
        cx = left + 232
        color = ("#83bdf4", "#6de4b2")[i]
        coords = [(cx + (p[x] - center_x) * scale, 280 - (p[2] - center_y) * scale) for p in chain]
        title = ("Before", "Candidate")[i]
        parts.extend([
            f'<rect x="{left}" y="80" width="464" height="390" fill="#1d2634" stroke="#354354"/>',
            f'<text x="{left+16}" y="110" font-size="18" font-weight="700">{title}</text>',
            f'<text x="{left+16}" y="443" font-size="16">Wrist bend {states[i]["wrist_bend_degrees"]:.2f}°</text>',
            f'<polyline points="{" ".join(f"{a:.2f},{b:.2f}" for a,b in coords)}" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>',
        ])
        for label, (a, b) in zip(names, coords):
            parts.extend([
                f'<circle cx="{a:.2f}" cy="{b:.2f}" r="7" fill="{color}" stroke="#101722" stroke-width="2"/>',
                f'<text x="{a+12:.2f}" y="{b-9:.2f}" font-size="12">{label}</text>',
            ])
    offset = _vector_length([b - a for a, b in zip(before["elbow"], after["elbow"])])
    parts.extend([
        f'<text x="28" y="506" font-size="15">Elbow displacement {offset:.6g} BU · wrist and palm orientation are the anchor</text>',
        '</g></svg>',
    ])
    return "".join(parts)
