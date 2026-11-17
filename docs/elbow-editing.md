# Elbow editing

Stereograph can swivel an FK elbow while preserving the hand’s position and orientation. It checks that the shoulder and arm lengths hold, the wrist improves, and the edited pose clears the named surfaces.

## Profile version 1

Start with the [calibration profile](fk-elbow-profile.json). Its names and distances belong to the included synthetic fixture. Set them for your own rig and scene.

| Field | Meaning |
| --- | --- |
| `rig` and `controls` | Editable armature and the upper-arm, forearm, and hand FK control names. Use unlocked quaternion rotation, a direct upper-to-forearm chain, and a descendant hand. |
| `palm_axis` | Hand-local direction from the wrist toward the palm. The wrist bend is the angle from the forearm direction to this axis. |
| `anchor_object` | Optional prop to use as the hand’s coordinate reference. Omit it to hold the hand in world space. |
| `hand_witness_object` | Optional attached object, such as a separate hand rig. Its evaluated transform independently checks the planted hand. |
| `fk_switch` | Optional `bone`, `property`, and numeric `value` that identify the rig’s FK state. |
| `clearance` | One to four pairs of evaluated meshes and their `minimum_gap` in Blender units. Crossings also block the candidate. The first pair appears in the matched surface views. |
| `limits` | `max_swivel`, `min_improvement`, `elbow_bend` and `wrist_bend` in degrees. `position_tolerance` and `neighbour_position` are in Blender units. `rotation_tolerance`, `neighbour_rotation` and `neighbour_step` are in degrees. |

Use Object or Pose Mode with an evaluated view layer, a uniformly scaled rig, and one directly assigned layered Action with one keyframe strip. The FK controls must be directly editable. For scenes whose authored drivers require trusted execution, preview in a trusted live copy once Blender is evaluating those drivers.

## Preview, inspect, apply

1. Call `blender_health` and pass its session ID as `expected_session` to `preview_elbow_edit`. Request `views: ["front", "side"]` for matched joint diagrams and surface outlines.
2. Omit the target to preview the wrist-alignment optimum within the swivel limit. For a specific candidate, pass signed `swivel_degrees` or an `elbow_target` in world space. The elbow travels on the circle defined by the fixed shoulder, wrist, and arm lengths. An unreachable target or a blocked candidate returns the reason. Automatic mode evaluates one candidate. If it clashes, a different swivel may still be feasible.
3. Read `sample`, `neighbours`, `planned_keys` and `conflicts`. The tool stages the actual keys and samples the current frame plus offsets of −1, −0.5, +0.5 and +1 frame before restoring the original Action. Clearance claims cover the declared mesh pairs at those samples. Headless previews take `blend_file` and return measurements and views for inspection.
4. Use `apply_elbow_edit` with the live candidate ID and the same session ID to commit, or `cancel_elbow_edit` to discard it. Apply requires the same live frame, pose, rig, and Action. It stages and measures the candidate again. Successful apply assigns a named Action variant to the edited rig’s slot and keys the upper-arm and hand quaternions at the target frame. The source Action is retained and other rigs using it keep their assignments. Use Blender’s normal Undo to restore the original. Apply requires a live 3D viewport and Global Undo. Save from Blender when you want the edit on disk.

The native qualification runner generates its own disposable Blender fixture. With the released wheel installed and the matching extension ZIP downloaded, run it from the source checkout.

```sh
python tests/check_elbow.py --live --addon /path/to/stereograph_bridge-0.5.0.zip --evidence ./elbow-evidence
```
