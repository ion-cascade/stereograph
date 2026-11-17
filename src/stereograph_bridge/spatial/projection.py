"""Small NumPy depth rasterizer for headless and live witness views."""

import base64
import struct
from time import monotonic
import zlib

import numpy as np

from .distance import InspectionLimit

WIDTH, HEIGHT = 600, 306
PIXELS = WIDTH * HEIGHT
BATCH_PIXELS = 350_000
COLOR_A = np.array([76, 168, 230])
COLOR_B = np.array([230, 174, 86])
BACKGROUND = np.array([29, 38, 52])


def _cross(a, b):
    return a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]


def _paint(triangles, shades, low, high, zbuffer, colors, deadline):
    sizes = high - low + 1
    areas = sizes[:, 0] * sizes[:, 1]
    boundaries = np.concatenate(([0], areas.cumsum()))
    start = 0
    while start < len(triangles):
        if monotonic() >= deadline:
            raise InspectionLimit("The evidence projection reached its deadline.")
        stop = min(len(triangles), int(np.searchsorted(boundaries, boundaries[start] + BATCH_PIXELS, side="right") - 1))
        stop = max(start + 1, stop)
        counts = areas[start:stop]
        ids = np.repeat(np.arange(start, stop), counts)
        local = np.arange(len(ids)) - np.repeat(boundaries[start:stop] - boundaries[start], counts)
        x = low[ids, 0] + local % sizes[ids, 0]
        y = low[ids, 1] + local // sizes[ids, 0]
        point = np.column_stack((x + .5, y + .5))
        faces = triangles[ids]
        base = faces[:, 0, :2]
        u, v = faces[:, 1, :2] - base, faces[:, 2, :2] - base
        w = point - base
        denom = _cross(u, v)
        a = _cross(w, v) / denom
        b = _cross(u, w) / denom
        inside = (a >= -1e-9) & (b >= -1e-9) & (a + b <= 1 + 1e-9)
        z = faces[:, 0, 2] + a * (faces[:, 1, 2] - faces[:, 0, 2]) + b * (faces[:, 2, 2] - faces[:, 0, 2])
        pixels = (y * WIDTH + x)[inside]
        depths = z[inside]
        order = np.lexsort((depths, pixels))
        if len(order):
            unique = order[np.concatenate((np.diff(pixels[order]) != 0, [True]))]
            best = unique[depths[unique] >= zbuffer[pixels[unique]]]
            zbuffer[pixels[best]] = depths[best]
            colors[pixels[best]] = shades[ids[inside][best]]
        start = stop


def _raster(surface, center, radius, view, color, deadline):
    right, up, depth = view
    close = np.all((surface.lower <= center + 1.8 * radius) & (surface.upper >= center - 1.8 * radius), axis=1)
    points = surface.triangles[close] - center
    plane = np.stack((points @ right, -(points @ up)), axis=-1)
    plane = plane * np.array([WIDTH / 924, HEIGHT / 472]) * (240 / radius) + [WIDTH / 2, HEIGHT / 2 + 10 * HEIGHT / 472]
    xy_min, xy_max = plane.min(axis=1), plane.max(axis=1)
    area = _cross(plane[:, 1] - plane[:, 0], plane[:, 2] - plane[:, 0])
    visible = np.all((xy_max > 0) & (xy_min < [WIDTH, HEIGHT]), axis=1) & (np.abs(area) > 1e-12)
    plane, points = plane[visible], points[visible]
    lower = np.maximum(np.floor(xy_min[visible]), 0).astype(int)
    upper = np.minimum(np.floor(xy_max[visible]), [WIDTH - 1, HEIGHT - 1]).astype(int)
    normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    front = np.abs(normals @ depth) / np.linalg.norm(normals, axis=1)
    directional = np.abs(normals @ (.62 * depth + .65 * up - .32 * right)) / np.linalg.norm(normals, axis=1)
    brightness = .48 + .29 * front + .22 * directional
    shades = brightness[:, None] * color
    projected = np.concatenate((plane, (points @ depth)[:, :, None]), axis=2)
    zbuffer = np.full(PIXELS, -np.inf)
    colors = np.zeros((PIXELS, 3))
    _paint(projected, shades, lower, upper, zbuffer, colors, deadline)
    return zbuffer, colors


def _chunk(kind, payload):
    return struct.pack("!I", len(payload)) + kind + payload + struct.pack("!I", zlib.crc32(kind + payload))


def embedded_context(a, b, center, radius, view, deadline):
    za, colors_a = _raster(a, center, radius, view, COLOR_A, deadline)
    zb, colors_b = _raster(b, center, radius, view, COLOR_B, deadline)
    behind = np.where((za > zb)[:, None], colors_b, colors_a)
    front = np.where((za > zb)[:, None], colors_a, colors_b)
    result = np.tile(BACKGROUND, (PIXELS, 1)).astype(float)
    both = np.isfinite(za) & np.isfinite(zb)
    either = np.isfinite(za) | np.isfinite(zb)
    result[both] = behind[both] * .68 + result[both] * .32
    result[either] = front[either] * .70 + result[either] * .30
    pixels = result.astype(np.uint8).reshape((HEIGHT, WIDTH * 3))
    scanlines = np.pad(pixels, ((0, 0), (1, 0))).tobytes()
    header = struct.pack("!IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(scanlines, level=6)) + _chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")
