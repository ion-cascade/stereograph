# Rig inspection and knee repair

Inspect a Blender character's joint centres, control frames, authored skin weights and sampled motion. Use the measurements to distinguish a rest fit error from a control, binding or contact issue.

## Map the character

Start with the [rig inspection profile](rig-inspection-profile.json). Name the actual Blender object and bone for each role. The role may point to a deform bone on the body rig or a bone on a separate hand rig. Use the optional `control` to show the associated control alongside it, and `switches` to declare which controls are active at your rig's IK and FK settings.

- `landmark` is an authored joint centre in armature coordinates. `landmark_tolerance` uses those same units. `expected_axis` is the expected rest direction in armature coordinates, and `local_axis` is the bone-local direction tested against it. The default is local +Y. Axis tolerances are in degrees.
- `bindings` describes an active Armature modifier, optionally restricted to a named vertex group. The tool audits authored weights on the source mesh. At least 0.5 membership selects a vertex in the optional region. The report counts unweighted points, non-unit deform weights, and weights outside the declared expected bones. Reported totals describe the authored groups and may differ from Blender's effective normalized values.
- `checks` declares a region and its mapped roles, optional frame interval, and explicit limits. A measurement outside a limit is a finding. Use limits from the model's intended movement and references.

| Check | Required roles | Measurements |
| --- | --- | --- |
| `arm` | shoulder, elbow, wrist | Reach ratio, elbow bend and wrist bend |
| `thumb` | palm, cmc, mcp, ip | Basal rotation, sweep and elevation relative to the palm, MCP and IP bend, and tip-to-palm distance |
| `finger` | base, middle, tip | Two joint direction changes and total curl |
| `leg` | hip, knee, foot | Signed tracking in the plane perpendicular to the hip-to-ankle line, bend and segment lengths |
| `plant` | foot | Translation and rotation drift relative to the first sample in the declared interval |

The thumb check uses the palm's local XY plane for sweep and local Z for elevation. This requires a calibrated palm frame and mapped metacarpal, proximal and distal segments. Tip-to-palm distance is a landmark measurement. Use actual pad and target surfaces when you need a contact test.

For a leg check, `forward` is local to the optional anchor object, or to the armature when omitted. Measure foot drift in world coordinates by default or choose a floor or prop anchor. Name the intended planted interval explicitly.

Call `inspect_rig` with `frames` and optional `views: ["front", "side"]` and `evidence_frame`. Pass the data from a same-scene `review_motion_contacts` call as `contact_report`. Use each check's `contact_rule` to link its geometry report. The output separates rest fit, active controls, binding, and contact evidence. Scene, Action and scope mismatches are reported. Inspection restores the starting frame. The geometry and motion checks apply at the sampled times.

## Preview and repair a knee

Use the [knee profile example](knee-repair-profile.json) with a full-influence two-bone IK constraint and an editable pole. Map the effective hip, knee and foot bones separately from the IK constraint owner. Use the foot bone that drives the geometry you need to keep planted.

1. Call `blender_health`. Pass its session ID into `preview_knee_repair` at the intended frame. The tool uses profile forward by default. Give it an explicit `tracking_degrees` or reachable world-space `knee_target` when the intended bend differs from forward.
2. Read the before and candidate tracking angles, foot and anchor drift, segment lengths, clearance witnesses and exact planned keys. The target and four adjacent whole/half-frame samples are tested. A straight leg with an undefined bend plane, an unreachable target, or a solve that fails the configured invariants blocks apply.
3. Apply the issued candidate ID with `apply_knee_repair`, or discard it with `cancel_knee_repair`. Apply rechecks the active pose, Action and clearance, then writes three pole location channels at the edit frame in an Action variant. The original Action is retained. Blender Global Undo and an open 3D viewport are required. Use normal Blender Undo to restore the original Action.

Knee preview and apply accept an explicit timeout of up to 600 seconds. Match the client deadline for densely layered scenes. Live evaluation occupies Blender during the check. Use `blend_file` for background inspection or non-applicable previews. Scenes whose drivers need trust can be tested through a trusted live session. Bake history-dependent simulations or use a controlled disposable reproduction before interpreting their contacts. The repair targets native rigs with live pole controls. Baked object and mesh playback carries no editable knee pole.

## Reproduce

```sh
uv run --locked python tests/check_rig.py --live --evidence build/rig/reproduction
```

The fixture verifies a known rest-axis error, rest landmark offset, unweighted and unexpected influences, inward knee tracking, a fixed foot, an unreachable target, cancellation, stale state, a new clearance conflict, and native Undo. Use `--server` and `--addon` to qualify an installed release pair.
