"""Sample evaluated motion and retain explicitly navigable surface witnesses."""

from collections import defaultdict
from math import ceil, floor
from time import monotonic

from ..spatial.distance import InspectionLimit
from ..spatial.evidence import VIEWS, diagram, witness_framing
from ..spatial.query import InvalidScene, _evaluated_surface, _measure
from ..wire import failure, success
from .profile import FRAME_LIMIT, MAX_SAMPLES, Profile, number
from .results import classify, findings, needs_refinement, profile_signature, severity, summary


def identity(bpy, action_object):
    layer = bpy.context.view_layer
    obj = layer.objects.get(action_object) if action_object else None
    animation = obj.animation_data if obj else None
    action = animation.action if animation else None
    slot = animation.action_slot if action else None
    return dict(file=bpy.data.filepath or None, scene=bpy.context.scene.name, view_layer=layer.name,
                action_object=action_object, action=action.name if action else None,
                action_slot=slot.identifier if slot else None)


def set_frame(scene, time):
    whole = floor(time)
    if (scene.frame_current, round(scene.frame_subframe, 6)) != (whole, round(time - whole, 6)):
        scene.frame_set(whole, subframe=time - whole)


def _unresolved(code, message):
    return dict(status="unresolved", code=code, message=message)


class Review:
    def __init__(self, bpy, profile, deadline):
        self.bpy = bpy
        self.scene = bpy.context.scene
        self.profile = profile
        self.deadline = deadline
        self.rows = defaultdict(dict)
        self.errors = {}
        self.frame = None
        self.graph = None
        self.cache = {}

    def at(self, frame):
        if monotonic() >= self.deadline:
            raise InspectionLimit("The review reached its deadline.")
        if self.frame != frame:
            set_frame(self.scene, frame)
            self.graph = self.bpy.context.evaluated_depsgraph_get()
            self.cache.clear()
            self.errors.clear()
            self.frame = frame

    def surface(self, key):
        region = self.profile.surfaces[key]
        if region not in self.cache:
            obj = self.bpy.context.view_layer.objects.get(region.object)
            if obj is None or obj.type not in {"MESH", "CURVE"}:
                self.errors[key] = _unresolved(
                    "surface_unresolved", f"{region.object!r} is not an available mesh or bevelled curve."
                )
                return None
            try:
                self.cache[region] = _evaluated_surface(obj, self.graph, region)
            except InvalidScene as exc:
                self.errors[key] = _unresolved(exc.code, str(exc))
                return None
        return self.cache[region]

    def measure(self, rule):
        first, second = [self.surface(key) for key in rule.surfaces]
        if first is None or second is None:
            unresolved = next(key for key in rule.surfaces if key in self.errors)
            return dict(surface=unresolved, **self.errors[unresolved]), None, None, None
        result, witness = _measure(first, second, rule.surfaces, self.frame, self.scene.frame_current_final,
                                   rule.gap, self.deadline, check_containment=rule.containment)
        result["status"], result["code"] = classify(rule, result)
        return result, first, second, witness

    def sample(self, tasks):
        for frame, rules in sorted(tasks.items()):
            self.at(frame)
            for rule in rules:
                self.at(frame)
                measured, _, _, _ = self.measure(rule)
                self.rows[rule.id][frame] = {**measured, "rule": rule.id, "frame": frame}

    def refine(self, remaining):
        tasks = defaultdict(list)
        # Prioritise the widest eligible intervals, so a budget is shared across the range.
        intervals = []
        for rule in self.profile.rules:
            rows = sorted(self.rows[rule.id].values(), key=lambda item: item["frame"])
            for left, right in zip(rows, rows[1:]):
                width = round(right["frame"] - left["frame"], 6)
                if width > self.profile.refine_step + 1e-6 and needs_refinement(
                    rule, left, right, self.profile.near_gap
                ):
                    intervals.append((width, left["frame"], rule.id, rule))
        exhausted = False
        for width, start, _, rule in sorted(intervals, key=lambda item: (-item[0], item[1], item[2])):
            count = ceil(width / self.profile.refine_step - 1e-8)
            for step in range(1, count):
                if remaining <= 0:
                    exhausted = True
                    break
                tasks[round(start + width * step / count, 6)].append(rule)
                remaining -= 1
            if exhausted:
                break
        self.sample(tasks)
        return exhausted

    def evidence(self, views, rule_id, state, session, matching):
        if not views:
            return []
        ranked = [row for rows in self.rows.values() for row in rows.values()
                  if "distance" in row and (not rule_id or row["rule"] == rule_id)]
        if matching:
            selected = [self.rows[rule][frame] for rule, frame in matching if frame in self.rows[rule]]
        else:
            selected = _representatives(ranked)
        lookup = {rule.id: rule for rule in self.profile.rules}
        reports = []
        for choice in selected:
            self.at(choice["frame"])
            rule = lookup[choice["rule"]]
            row, a, b, witness = self.measure(rule)
            if witness is None:
                continue
            center, radius = matching.get((rule.id, self.frame)) or witness_framing(a, b, witness)
            reports.append(dict(rule=rule.id, frame=self.frame, relation=row["surface_relation"],
                                bookmark=dict(identity=state, session_id=session, frame=self.frame,
                                              center=center.tolist(), radius=radius),
                                diagrams={view: diagram(a, b, witness, row, view, self.deadline,
                                                        (center, radius), rule.id) for view in views}))
        return reports


def _representatives(ranked):
    if not ranked:
        return []
    failed = [row for row in ranked if row["status"] == "fail"]
    first = min(failed or ranked, key=lambda row: (row["frame"], row["rule"]))
    worst = max(failed, key=severity) if failed else min(ranked, key=lambda row: row["distance"])
    # Equal-distance crossings still benefit from a second sample later in the move.
    if first == worst and len(failed) > 1:
        worst = max((row for row in failed if row != first), key=lambda row: (severity(row), row["frame"]))
    return [first] + ([worst] if first != worst else [])


def _matching_evidence(entries, rules, views):
    from numpy import asarray

    if not isinstance(entries, list) or len(entries) > 2 or (entries and not views):
        raise ValueError("Use up to two prior evidence entries with two requested views.")
    spans = {rule.id: rule.interval for rule in rules}
    matching = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("rule") not in spans:
            raise ValueError("Match evidence against a rule in this review.")
        rule = entry["rule"]
        bookmark = entry.get("bookmark", {})
        if not isinstance(bookmark, dict):
            raise ValueError("Prior evidence needs its original bookmark.")
        frame = round(number(entry.get("frame"), "Evidence frame", *spans[rule]), 6)
        center, radius = _framing(bookmark)
        matching[rule, frame] = (asarray(center), radius)
    return matching


def _framing(bookmark):
    center = bookmark.get("center")
    if not isinstance(center, list) or len(center) != 3:
        raise ValueError("Provide a three-coordinate world-space center.")
    return ([number(item, "Coordinate", -1e8, 1e8) for item in center],
            number(bookmark.get("radius"), "Radius", 1e-8, 1e8))


def inspect_motion(bpy, parameters, seconds, session=None):
    scene = bpy.context.scene
    if scene is None or bpy.context.view_layer is None:
        return failure("no_scene", "An active scene and view layer are required.")
    if bpy.context.mode != "OBJECT":
        return failure("object_mode_required", "Use Object Mode for motion review.")
    if bpy.app.autoexec_fail:
        return failure("drivers_not_evaluated", "Blender blocked authored drivers. Inspect an evaluated, trusted live session.")
    try:
        profile = Profile.parse(parameters.get("profile"))
        views = parameters.get("views", [])
        if (not isinstance(views, list) or len(views) not in (0, 2)
                or any(view not in VIEWS for view in views) or (views and views[0] == views[1])):
            raise ValueError("Choose two distinct views from front, side, top and isometric.")
        rule_id = parameters.get("evidence_rule")
        if rule_id is not None and rule_id not in {rule.id for rule in profile.rules}:
            raise ValueError("evidence_rule must identify a rule in the profile.")
        matching = _matching_evidence(parameters.get("match_evidence", []), profile.rules, views)
        if matching and rule_id:
            raise ValueError("Choose either matched evidence or a single evidence rule.")
        extra = parameters.get("extra_frames", [])
        if not isinstance(extra, list) or len(extra) > MAX_SAMPLES:
            raise ValueError("extra_frames must be a bounded list of frame times.")
        extra = {round(number(time, "Extra frame", *profile.frames), 6) for time in extra}
        base = defaultdict(list)
        for rule in profile.rules:
            lo, hi = rule.interval
            requested = {time for time in extra if lo <= time <= hi}
            requested.update(time for key, time in matching if key == rule.id)
            for time in set(rule.base_frames(profile.step)) | requested:
                base[time].append(rule)
        count = sum(map(len, base.values()))
        if count > MAX_SAMPLES:
            raise ValueError(f"Base and explicit samples exceed {MAX_SAMPLES} rule samples.")
    except (TypeError, ValueError, OverflowError) as exc:
        return failure("bad_motion_profile", str(exc))
    state = identity(bpy, profile.action_object)
    if profile.action_name and state["action"] != profile.action_name:
        return failure("action_unresolved", f"The configured Action {profile.action_name!r} is not assigned to its object.")
    start = scene.frame_current, scene.frame_subframe
    review = Review(bpy, profile, monotonic() + max(.01, seconds - .5))
    limits, evidence = [], []
    sampling_finished = False
    try:
        review.sample(base)
        if review.refine(MAX_SAMPLES - count):
            limits.append("refinement_sample_limit")
        sampling_finished = not limits
        evidence = review.evidence(views, rule_id, state, session, matching)
    except InspectionLimit:
        limits.append("review_deadline")
    finally:
        if (scene.frame_current, scene.frame_subframe) != start:
            scene.frame_set(start[0], subframe=start[1])
    lists = [sorted(review.rows[rule.id].values(), key=lambda row: row["frame"]) for rule in profile.rules]
    rules = [summary(rule, rows) for rule, rows in zip(profile.rules, lists)]
    for report in rules:
        expected = {time for time, terms in base.items() if any(rule.id == report["rule"] for rule in terms)}
        report["missing_base_samples"] = sorted(expected - set(report["sample_times"]))
        if report["missing_base_samples"]:
            report["status"] = "incomplete"
    return success(dict(
        version=1, profile=profile.name, profile_signature=profile_signature(profile), identity=state,
        range=list(profile.frames), base_step=profile.step, refinement_step=profile.refine_step,
        starting_frame=sum(start), units="blender_units", limits=limits, sampling_finished=sampling_finished,
        rules=rules,
        samples=[row for rows in lists for row in rows], findings=[issue for rows in lists for issue in findings(rows)],
        evidence=evidence,
    ))


def jump(bpy, parameters, session):
    from mathutils import Matrix
    from ..viewport import _largest

    window = bpy.context.window
    if not session or window is None:
        return failure("live_session_required", "Use the live Blender session that generated the witness.")
    bookmark = parameters.get("bookmark")
    if not isinstance(bookmark, dict) or bookmark.get("session_id") != session:
        return failure("stale_motion_witness", "Generate the witness in the current live session.")
    origin = bookmark.get("identity")
    if (not isinstance(origin, dict) or not isinstance(origin.get("action_object"), (str, type(None)))
            or origin != identity(bpy, origin.get("action_object"))):
        return failure("stale_motion_witness", "The loaded file, scene, view layer or Action changed.")
    view = parameters.get("view", "front")
    if not isinstance(view, str) or view not in VIEWS:
        return failure("bad_motion_witness", "Choose a supported orthographic view.")
    area = _largest(area for area in window.screen.areas if area.type == "VIEW_3D")
    if area is None or area.spaces.active.region_3d is None:
        return failure("area_not_found", "Open a 3D viewport before jumping to a witness.")
    try:
        time = number(bookmark.get("frame"), "Frame", -FRAME_LIMIT, FRAME_LIMIT)
        center, radius = _framing(bookmark)
    except ValueError as exc:
        return failure("bad_motion_witness", str(exc))
    set_frame(bpy.context.scene, time)
    region = area.spaces.active.region_3d
    region.view_location = center
    region.view_rotation = Matrix(VIEWS[view]).transposed().to_quaternion()
    region.view_distance = radius * 4
    region.view_perspective = "ORTHO"
    area.tag_redraw()
    return success(dict(frame=time, center=center, view=view, distance=region.view_distance))
