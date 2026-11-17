# Clothing and armour fit

Use `inspect_clothing_fit` to see how a garment, its details and adjacent
armour evaluate in the loaded view layer. Live review uses the open scene.
Set `blend_file` to inspect a saved file in a disposable background process.
Both restore the starting frame. If Blender blocks the file's drivers, open
the trusted file in Blender and review that live session.

## Map the assembly

Start with the [version 1 fit profile](clothing-fit-profile.json).
Each layer resolves an object by name and identifies its role as `cloth`,
`trim`, `mail`, `glove`, `plate`, `body` or `support`. Optional
`weight_groups` report authored weight counts and values for selected
groups. An optional `representation` label records the intended authoring
mode, `editable` or `baked_playback`. The report shows the actual Action,
shape-key stack, visible or disabled modifiers, parent and source links,
and native cloth cache status separately. Expand the profile with each
required part if a dependency is beyond three levels of traced links.

Set `frames` to the poses you want to inspect. A seam or an attachment
uses a pair of evaluated vertex indices or object-local points. Choose
landmarks you can identify on the actual assembly. At each sample the
tool reports separation and drift against the first resolved frame, in
the first object's local axes. A changed topology makes a vertex-index
comparison unresolved. Local deformation compares world-space edge
lengths against the first resolved sample. A vertex group limits that
measurement to the panel or edge in question.

Put an existing [motion contact profile](motion-review.md) under
`contacts` to measure crossings and separation for named surfaces.
Its frame interval and refinement are independent of the landmark and
strain frames. Call with `views: ["front", "side"]` for orthographic
witnesses. The report distinguishes an unresolved surface from measured
clearance. Instanced mail needs real evaluated mesh output for numeric
ring contact. A collision envelope can be mapped as a separate support
layer with its own result. Compare wrist bend or other motion separately
with [rig inspection](rig-inspection.md).

## Preview a local correction

Use the [version 1 corrective profile](cloth-corrective-profile.json).
Author a feathered vertex group on the garment, away from the pin or
waist groups and from any sewn edge that must stay put. List the
protected groups and distinct trim objects explicitly. Put the pair to
repair first in `pairs` and add adjacent armour or underlayers as guard
pairs. A pair can filter either surface by group or material, using the
same convention as motion contact review. Leave the filters out to
cover the entire surface.

Set `frame`, `span` (half the envelope width in frames), a garment-local
`direction` and a `distance` in Blender units. The corrective appends a
Geometry Nodes modifier and keys a smooth envelope that is zero at
both endpoints and outside the envelope. It leaves prior modifiers,
Actions, shape keys and the cloth bake in place. Supported stacks
evaluate authored geometry or a current internal cloth bake that covers
all sampled frames. For a retained, outdated internal bake, deliberately
choose `"cache_policy": "frozen_bake"` to evaluate the recorded playback
and preserve the existing outdated flag. This makes no claim about a fresh
solve. Unbaked, external and out-of-range caches are rejected. Review rebaked
cloth separately.

1. Call `preview_cloth_corrective`. Live previews use the session ID from
   `blender_health`. A `blend_file` preview returns measurements for a
   saved scene. It cannot return a live candidate ID.
2. Inspect the reported before and after surface witnesses, the
   unchanged endpoints, and the amount of local displacement and edge
   deformation. Add `extra_frames` when an important adjacent pose falls
   between the nine evenly spaced automatic samples. Measure outside
   mask and protected trim motion, and look at the whole garment in
   Blender before accepting the shape.
3. Apply a feasible live candidate with `apply_cloth_corrective`, using its
   `candidate_id` and `expected_session`, or discard it with
   `cancel_cloth_corrective`. Apply rechecks the current scene, frame,
   sampled baseline and candidate. Blender records native Undo. Save
   the resulting file through Blender after inspection.

The checks describe the declared surfaces, sampled frames and named
regions. They do not guarantee global absence of intersections or detect
all cloth bulges. A guard crossing that already exists is reported in
both phases. Exact mesh extraction can take several minutes on large
scenes. Clothing review, preview and apply support a deadline up to
600 seconds.
