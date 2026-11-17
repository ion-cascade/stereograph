"""Evaluate contact rules and compare only measured outcomes."""

from dataclasses import asdict
import hashlib
import json
from math import isfinite

from .profile import MAX_SAMPLES


def profile_signature(profile):
    return hashlib.sha256(json.dumps(asdict(profile), sort_keys=True).encode()).hexdigest()


def classify(rule, measure):
    relation = measure["surface_relation"]
    if relation == "crossing":
        return "fail", "surface_crossing"
    containment = measure["containment"] or {}
    if any(value["state"] == "contained" for value in containment.values()):
        return "fail", "contained_surface"
    if rule.mode == "clear":
        if relation == "touching":
            return "fail", "unexpected_contact"
        if measure["distance"] < rule.gap:
            return "fail", "insufficient_clearance"
    if rule.mode == "required" and measure["distance"] > max(rule.gap, measure["numerical_epsilon"]):
        return "fail", "required_contact_missing"
    if any(value["state"] == "indeterminate" and value["reason"] != "boundaries_meet"
           for value in containment.values()):
        return "inconclusive", "containment_indeterminate"
    return "pass", None


def needs_refinement(rule, left, right, near):
    if any(row["status"] in {"inconclusive", "unresolved"} for row in (left, right)):
        return False
    threshold = rule.gap if rule.mode != "allowed" else 0
    return (
        left["status"] != right["status"]
        or left["code"] != right["code"]
        or left["surface_relation"] != right["surface_relation"]
        or any(row["status"] == "fail" or abs(row["distance"] - threshold) <= near
               for row in (left, right))
    )


def severity(sample):
    prior = {"surface_crossing": 4, "contained_surface": 3, "unexpected_contact": 2,
             "insufficient_clearance": 1, "required_contact_missing": 1}
    distance = sample.get("distance", 0)
    direction = distance if sample["code"] == "required_contact_missing" else -distance
    return prior.get(sample["code"], 0), direction


def findings(rows):
    """Summarise consecutive measured failures sharing a diagnostic."""
    groups = []
    active = None
    for sample in rows:
        if sample["status"] != "fail":
            active = None
            continue
        if active is None or active["code"] != sample["code"]:
            active = dict(rule=sample["rule"], code=sample["code"], first=sample["frame"],
                          last=sample["frame"], worst=sample["frame"], samples=0)
            groups.append(active)
            worst = sample
        active["last"] = sample["frame"]
        active["samples"] += 1
        if severity(sample) > severity(worst):
            active["worst"] = sample["frame"]
            worst = sample
    return groups


def summary(rule, rows):
    measured = [row for row in rows if "distance" in row]
    failed = [row["frame"] for row in rows if row["status"] == "fail"]
    known = [row for row in rows if row["status"] in {"pass", "fail"}]
    if len(known) < len(rows) or not rows:
        status = "incomplete"
    else:
        status = "fail" if failed else "pass"
    return dict(rule=rule.id, mode=rule.mode, pair=list(rule.surfaces), range=list(rule.interval),
                status=status, sample_times=[row["frame"] for row in rows],
                largest_sample_interval=max((b["frame"] - a["frame"] for a, b in zip(rows, rows[1:])), default=0),
                first_failure=min(failed, default=None), last_failure=max(failed, default=None),
                closest_distance=min((row["distance"] for row in measured), default=None),
                missed_all_required_samples=rule.mode == "required" and bool(rows)
                and all(row["code"] == "required_contact_missing" for row in rows))


def compare(before, after):
    if (before.get("version") != 1 or after.get("version") != 1
            or not before.get("profile_signature") or before["profile_signature"] != after.get("profile_signature")
            or before.get("range") != after.get("range")):
        raise ValueError("Use two version 1 reviews with the same complete inspection profile and range.")
    reports = (before, after)
    by_key = [_sample_index(report["samples"]) for report in reports]
    keys = sorted(set(by_key[0]) | set(by_key[1]))
    classifications = dict(fixed=[], persistent=[], new=[], unverified=[])
    for rule, frame in keys:
        old, new = (lookup.get((rule, frame), {}) for lookup in by_key)
        start, end = old.get("status"), new.get("status")
        if "fail" not in (start, end):
            continue
        entry = dict(rule=rule, frame=frame, before=old.get("code"), after=new.get("code"))
        key = {("fail", "pass"): "fixed", ("fail", "fail"): "persistent",
               ("pass", "fail"): "new"}.get((start, end), "unverified")
        classifications[key].append(entry)
    return dict(before=before["identity"], after=after["identity"], range=before["range"],
                **classifications)


def _sample_index(rows):
    if not isinstance(rows, list) or len(rows) > MAX_SAMPLES:
        raise ValueError("Supply a motion review sample list within the report limit.")
    result = {}
    for row in rows:
        rule, frame = row["rule"], row["frame"]
        if (not isinstance(rule, str) or type(frame) not in (int, float) or not isfinite(frame)
                or row["status"] not in ("pass", "fail", "unresolved", "inconclusive")
                or (rule, frame) in result):
            raise ValueError("Reports need distinct rule and frame samples with valid statuses.")
        result[rule, frame] = row
    return result
