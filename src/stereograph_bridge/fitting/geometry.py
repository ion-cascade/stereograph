"""Animation of a feathered, post-deformation Geometry Nodes displacement."""

from contextlib import contextmanager

from ..spatial.query import InvalidScene

LABEL = "Stereograph cloth corrective"


def _group(group, plan):
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    source = nodes.new("NodeGroupInput")
    source.location = (-540, 180)
    sink = nodes.new("NodeGroupOutput")
    sink.location = (540, 180)
    weights = nodes.new("GeometryNodeInputNamedAttribute")
    weights.location = (-540, -40)
    weights.data_type = "FLOAT"
    weights.inputs["Name"].default_value = plan.mask
    amplitude = nodes.new("ShaderNodeValue")
    amplitude.label = "Pose envelope"
    amplitude.location = (-540, -260)
    multiplier = nodes.new("ShaderNodeMath")
    multiplier.operation = "MULTIPLY"
    multiplier.use_clamp = True
    multiplier.location = (-280, -40)
    offset = nodes.new("ShaderNodeVectorMath")
    offset.operation = "SCALE"
    offset.location = (0, -40)
    offset.inputs[0].default_value = [x*plan.distance for x in plan.direction]
    position = nodes.new("GeometryNodeSetPosition")
    position.location = (280, 180)
    links.new(weights.outputs["Attribute"], multiplier.inputs[0])
    links.new(amplitude.outputs[0], multiplier.inputs[1])
    links.new(multiplier.outputs[0], offset.inputs["Scale"])
    links.new(source.outputs["Geometry"], position.inputs["Geometry"])
    links.new(offset.outputs[0], position.inputs["Offset"])
    links.new(position.outputs["Geometry"], sink.inputs["Geometry"])
    socket = amplitude.outputs[0]
    for frame, value in ((plan.frame-plan.span, 0), (plan.frame, 1), (plan.frame+plan.span, 0)):
        socket.default_value = value
        if not socket.keyframe_insert(data_path="default_value", frame=frame):
            raise InvalidScene("corrective_key_failed", "Blender could not key the correction envelope.")
    animation = group.animation_data
    action = animation.action
    action.name = LABEL + " envelope"
    channel = action.layers[0].strips[0].channelbag(animation.action_slot)
    for fcurve in channel.fcurves:
        fcurve.extrapolation = "CONSTANT"
        for key in fcurve.keyframe_points:
            key.interpolation = "BEZIER"
            key.handle_left_type = key.handle_right_type = "AUTO_CLAMPED"
        fcurve.update()


@contextmanager
def staged(bpy, obj, plan, *, label=LABEL):
    group = bpy.data.node_groups.new(label, "GeometryNodeTree")
    modifier = None
    transaction = {"keep": False}
    try:
        _group(group, plan)
        modifier = obj.modifiers.new(label, "NODES")
        modifier.node_group = group
        transaction.update(modifier=modifier, group=group)
        yield transaction
    finally:
        if not transaction["keep"]:
            action = group.animation_data.action if group.animation_data else None
            if modifier is not None:
                obj.modifiers.remove(modifier)
            bpy.data.node_groups.remove(group)
            if action is not None and action.users == 0:
                bpy.data.actions.remove(action)
