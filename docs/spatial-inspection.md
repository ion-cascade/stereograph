# Inspect evaluated surfaces

`inspect_spatial_pairs` measures the actual triangles evaluated by Blender in the active view layer. Use it on named mesh pairs at the current pose, at specified frames, or across a range. The tool is available in a live session and against a specified `.blend` in background Blender. It works with Python execution disabled. Use Object Mode and ensure both objects are available in the view layer.

## Example

Pass the following arguments to the MCP tool. Replace the example object names and frame range with the ones you want to inspect.

```json
{
  "pairs": [["Glove", "Handle"]],
  "frame_range": [33, 39, 0.5],
  "tolerance": 0.001,
  "views": ["front", "side"],
  "evidence_frame": 37
}
```

Omit both frame options for the current frame. Supply `frames` for a list of samples or `frame_range` as start, end and step. The range includes its end. A result records both the requested frame and Blender's evaluated frame after time remapping. Supply `blend_file` to open a saved file in a fresh background process. The request allows up to 128 pair/frame combinations and eight annotated views. Set `timeout` to adjust the deadline for larger meshes.

## Read the results

- **Distance** is the finite triangle-to-triangle surface minimum in Blender units. It uses evaluated vertex coordinates and each object's evaluated world matrix, including scale. When scene units are set, `meters_per_unit` gives the conversion factor. `witness` gives the two world points and triangulated polygon indices from that evaluated sample.
- **Touching** means a common surface witness within the reported numerical epsilon. **Crossing** certifies a transverse intersection segment through the interiors of contributing triangles. At coplanar, edge-aligned, or tangential contacts, the surfaces can meet without a transverse witness. **Within tolerance** is a positive gap at or below the requested contact tolerance. A crossing has zero surface distance.
- **Containment** is directional. `contained` means every connected component of the subject surface lies inside the other object's validated boundary. Out-of-bounds surfaces and exterior components give `not_contained`. Open, inconsistently oriented, nonmanifold, or self-intersecting containers give `indeterminate` when the bounds alone cannot decide the query. A boundary meeting or an inconclusive topology check can also make the result indeterminate.
- **Evidence** contains SVG documents with a depth-rasterized local projection of the evaluated geometry, highlighted witness triangles and world-space scale. Save the `svg` value as an SVG file and open it in a browser or image viewer. Front looks along +Y, side looks along −X, and top looks along −Z. Front, side, top and isometric evidence work in either mode. A Blender UI screenshot uses the existing live screenshot tools.

The tool restores the starting frame and preserves selection and authored scene data. Evaluating another frame can run frame handlers and refresh simulation caches in Blender. For history-dependent deformation, inspect baked data or a disposable reproduction. An instance-only geometry node output needs a realized evaluated mesh. Frame samples show the selected times, so increase sampling around fast motion. The distances are surface distances, and crossing reports give witnesses rather than penetration depth. Numerical work uses Blender's evaluated mesh precision and double-precision triangle calculations.

## Reproduce

```sh
uv run --locked python -m unittest discover -s tests
uv run --locked python tests/check_spatial.py --live --evidence build/spatial/reproduction
```

The harness creates its own calibration file. It checks known gaps, contact and crossing, closed and ambiguous containment, an armature-deformed object, a subframe-only crossing, and a synthetic glove and handle. Live and headless results and diagrams must match. The test checks the source hash and confirms that the live frame and selection are restored. Use `--server` and `--addon` with an installed wheel and prebuilt extension to qualify the exact release.

The minimum search bounds triangle candidates with an AABB hierarchy and checks finite vertex–face and edge–edge features on the remaining pairs. For the evaluated mesh API and the background to hierarchical proximity queries, see the primary sources. [Blender dependency graph API](https://docs.blender.org/api/5.3/bpy.types.Depsgraph.html) · [UNC Proximity Query Package](https://github.com/GammaUNC/PQP)
