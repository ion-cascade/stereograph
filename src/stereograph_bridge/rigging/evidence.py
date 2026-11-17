"""Matched orthographic overlays for declared joints and reference landmarks."""

from html import escape
from math import isfinite


def overlay(rest, posed, view, frame):
    horizontal = 0 if view == "front" else 1
    points = [item[field] for table in (rest, posed) for item in table.values()
              for field in ("head", "tail", "landmark")
              if field in item and all(isfinite(x) for x in item[field])]
    if not points:
        return None
    lo, hi = ([function(p[axis] for p in points) for axis in (horizontal, 2)]
              for function in (min, max))
    centre = [(left + right) / 2 for left, right in zip(lo, hi)]
    scale = min(375 / max(hi[0] - lo[0], 1e-4), 420 / max(hi[1] - lo[1], 1e-4))
    result = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600">',
              '<rect width="1000" height="600" fill="#141a24"/>',
              '<g fill="#eff4f9" font-family="DejaVu Sans,sans-serif">',
              f'<text x="30" y="35" font-size="22">Rig inspection · {escape(view)} · frame {frame:g}</text>',
              '<text x="30" y="575" font-size="14">Shared projection and scale · gold crosses show authored landmarks</text>']
    for index, (label, data, colour) in enumerate((("Rest", rest, "#90bdff"), ("Posed", posed, "#83f5d0"))):
        shift = index * 490
        result += [f'<rect x="{20+shift}" y="61" width="470" height="485" fill="#1d2634"/>',
                   f'<text x="{35+shift}" y="88" font-size="16">{label}</text>']

        def project(point):
            return 250 + shift + (point[horizontal] - centre[0]) * scale, 323 - (point[2] - centre[1]) * scale

        labelled = []
        for role, item in data.items():
            x, y = project(item["head"])
            u, v = project(item["tail"])
            result.append(f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{u:.2f}" y2="{v:.2f}" stroke="{colour}" stroke-width="3"/>')
            result.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="{colour}"/>')
            tx, ty = x + 8, y - 5
            if len(data) <= 30 and not any(abs(px - tx) < 85 and abs(py - ty) < 16 for px, py in labelled):
                labelled.append((tx, ty))
                result.append(f'<text x="{tx:.2f}" y="{ty:.2f}" font-size="11">{escape(role[:24])}</text>')
            if "landmark" in item:
                a, b = project(item["landmark"])
                result.append(f'<path d="M{a-5:.2f} {b:.2f}h10m-5 -5v10" stroke="#f9cc80" stroke-width="2"/>')
    result.append("</g></svg>")
    return "".join(result)
