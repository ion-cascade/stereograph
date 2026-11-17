# Contact and motion review

Review phase-specific contact rules against Blender's evaluated triangles and reuse the same rules after an edit. The report identifies the scene, linked Action slot when specified, sampling range, and the exact time and position of each surface witness.

## Build a profile

Use the [fixture example](motion-review-profile.json) and [profile schema](motion-review.schema.json). The schema checks structure. The bridge also checks frame ordering and coverage, unique rule IDs, pair references, sample limits, evaluated surfaces, and Action assignment.

- Name each surface as a mesh object available in the active view layer. Narrow it by evaluated material, vertex group, or both. A group selects a triangle when all three of its evaluated vertices meet `minimum_weight`. Make an explicit region group or patch object when a bone weight covers more anatomy than the intended contact area. A rule uses two different mesh objects.
- Set inclusive rule intervals, in scene frames. For a transfer, give the approach and the landing separate rules. `clear` rejects crossings, touching and gaps under the configured minimum. `allowed` accepts separation and touching while rejecting transverse crossings. `required` demands a surface distance at or below `gap` and rejects transverse crossings. Distances are in Blender units.
- Set `check_containment` on a rule that also needs closed-volume containment checks. Inconclusive containment appears explicitly. Ordinary surface-only rules work with open regions. Tangency and coincident contact can produce a touching witness. A crossing witness identifies the intersection rather than penetration depth.
- Use the optional `action` object and name to verify the intended assignment. Only the scene state actually evaluated by Blender is measured. For authored drivers that need trust, use a trusted live session. For history-dependent simulation use a baked cache or a disposable file with a controlled evaluation history.

## Review and compare

1. Call `review_motion_contacts` in Object Mode with the profile. Specify `blend_file` for background review. The default base step is one frame and the default refinement step is a quarter frame. Eligible intervals are subdivided near sampled changes, failures and threshold proximity. Use `extra_frames` or a finer base step for fast movement that could happen wholly between ordinary samples.
2. Read each rule's sampled times, biggest time gap, closest distance, and first and last observed failures. Time and count limits are explicit. An incomplete rule cannot establish clearance. All claims apply at measured samples.
3. Request two views from `front`, `side`, `top` and `isometric`. The report returns SVG diagrams of two representative samples at most. Use `evidence_rule` to focus the selection. Pass a live result's bookmark to `jump_to_motion_witness` to set that frame and centre the current viewport. The jump preserves selection. The review itself restores the starting frame.
4. Replay the prior sample times with `extra_frames`. To reuse the same frame, centre, and scale in the diagrams, pass up to two previous evidence entries as `match_evidence`. Use `compare_motion_reviews` with the two report data objects and the same profile. The comparison reports fixed, persistent and new findings at shared samples. Failures with unresolved or missing counterparts remain unverified. Omit embedded SVGs from comparison inputs to reduce the payload.

The limit is 384 rule samples, including refinement, and four diagrams per request. Shorten the requested range or focus on fewer rules for dense scenes. A deadline can return a partial report and the measured samples collected before it.

## Reproduce

```sh
uv run --locked python tests/check_motion_review.py --live --evidence build/review/reproduction
```

The fixture includes a crossing between clear whole frames, permitted and required contact, missed contact, a missing object and inconclusive containment. The live check replays the same sampling after an edit and jumps to the exact subframe without changing selection. Pass the release wheel's executable with `--server` and the matching extension ZIP with `--addon` to qualify installed packages.
