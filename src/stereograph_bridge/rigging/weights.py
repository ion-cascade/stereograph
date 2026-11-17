"""Audit authored vertex groups participating in an actual Armature modifier."""

from time import monotonic

from ..spatial.distance import InspectionLimit


def vertex_bindings(layer, term, deadline):
    mesh = layer.objects.get(term.mesh)
    rig = layer.objects.get(term.rig)
    if mesh is None or mesh.type != "MESH" or rig is None or rig.type != "ARMATURE":
        return dict(status="unresolved", code="binding_object_missing")
    groups = {group.index: group.name for group in mesh.vertex_groups}
    keys = {bone.name for bone in rig.data.bones if bone.use_deform}
    missing = sorted(set(term.expected) - keys)
    if missing:
        return dict(status="unresolved", code="expected_deform_bone_missing", bones=missing)
    modifiers = [mod.name for mod in mesh.modifiers
                 if mod.type == "ARMATURE" and mod.object == rig and mod.show_viewport and mod.use_vertex_groups]
    if not modifiers:
        return dict(status="unresolved", code="active_vertex_group_binding_missing")
    region = mesh.vertex_groups.get(term.region) if term.region else None
    if term.region and region is None:
        return dict(status="unresolved", code="weight_region_missing")
    stats = dict(vertices=0, unweighted=0, non_normalized=0, unexpected=0)
    influences = {}
    witnesses = []
    for vertex in mesh.data.vertices:
        if vertex.index % 1024 == 0 and monotonic() >= deadline:
            raise InspectionLimit("The authored weight inspection reached its deadline.")
        weights = {entry.group: entry.weight for entry in vertex.groups if entry.weight > 0}
        if region is not None and weights.get(region.index, 0) < .5:
            continue
        chosen = {groups[i]: value for i, value in weights.items() if groups.get(i) in keys}
        amount = sum(chosen.values())
        stats["vertices"] += 1
        for key, value in chosen.items():
            count, maximum, total = influences.get(key, (0, 0., 0.))
            influences[key] = (count + 1, max(value, maximum), total + value)
        flags = []
        if amount <= 1e-8:
            flags.append("unweighted")
        elif abs(amount - 1) > term.tolerance:
            flags.append("non_normalized")
        if term.expected and any(key not in term.expected and value > term.tolerance
                                 for key, value in chosen.items()):
            flags.append("unexpected")
        for flag in flags:
            stats[flag] += 1
        if flags and len(witnesses) < 12:
            witnesses.append(dict(vertex=vertex.index, world=list(mesh.matrix_world @ vertex.co),
                                  weights=chosen, flags=flags))
    if not stats["vertices"]:
        return dict(status="unresolved", code="empty_weight_region")
    status = "finding" if any(stats[key] for key in ("unweighted", "non_normalized", "unexpected")) else "pass"
    return dict(status=status, scope="authored_vertex_groups", mesh=mesh.name, rig=rig.name,
                modifiers=modifiers, region=term.region, region_minimum_weight=.5 if region else None,
                counts=stats, witnesses=witnesses,
                influences={key: dict(vertices=count, max_weight=maximum, mean_on_assigned=total / count)
                            for key, (count, maximum, total) in sorted(influences.items())})
