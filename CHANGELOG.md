# Changelog

## 0.5.0 · 2026-11-17

- Preview an FK elbow swivel with a planted hand in world space or relative to an animated prop.
- Validate rig profiles, arm lengths, wrist alignment, evaluated surface clearance and neighbouring poses. Return matched front and side evidence and conflicts with witnesses.
- Apply a live candidate as an undoable Action variant or cancel it. Recheck the active pose and clearance before changing the Action.

## 0.4.0 · 2026-11-17

- Added live and background spatial inspection of evaluated meshes at frames and subframes, with distances, contact states, directional containment and world-space witnesses.
- Added matching annotated orthographic evidence in both modes, and disposable regression fixtures for geometry, deformation and animation.

## 0.3.0 · 2026-11-17

- Added a reproducible benchmark against Blender Lab MCP 1.0.3 with two disposable fixtures, paired timing runs, recovery scenarios and raw evidence.
- Reduced headless response delay by waiting for Blender's exit notification while retaining on-disk diagnostics and process deadlines.

## 0.2.0 · 2026-11-17

- Added native live and headless file summaries, plus collection and object inspection in the live session.
- Added workspace and object navigation, window and area screenshots, and camera renders with optional headless execution.
- Added version-labelled offline API and manual search and a command to install the pinned documentation.
- Qualified the tools against Blender Lab MCP 1.0.3 on the same disposable scene and recorded compatibility differences.

## 0.1.0 · 2026-11-17

- Established the Stereograph project, the Blender Lab MCP parity checklist and the initial release plan.
- Added a loopback Blender extension and three stdio MCP tools for session health and trusted Python execution.
- Added native disposable-scene checks for live and background Blender.
