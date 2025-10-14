"""Render material and geometry node graphs as annotated images in Blender.

This module creates a visualization scene, draws node boxes with labels,
connects links between nodes, frames the camera to include all content,
and renders a WEBP image for quick review/debugging.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

import bmesh  # type: ignore
import bpy
from mathutils import Vector  # type: ignore

from . import log, render_UVs

logger = log.create_logger(__name__)

# Layout constants
LINE_HEIGHT: float = 0.3
TEXT_SCALE: float = 0.2
MARGIN: float = 0.1
# Export constants
MAX_EXPORT_SIZE: int = 2048


def setup_scene(material_name: str) -> tuple[bpy.types.Scene, bpy.types.Object]:
    """Create a dedicated scene and orthographic camera for node visualization.

    Args:
        material_name: The material name used to label created objects.

    Returns:
        A tuple of (new_scene, camera_object).
    """
    new_scene: bpy.types.Scene = bpy.data.scenes.new(name=f"{material_name}_Node_Visualization")
    bpy.context.window.scene = new_scene

    bpy.ops.object.camera_add(location=(0, 0, 10))
    camera: bpy.types.Object = bpy.context.object
    camera.name = f"{material_name}_Visualization_Camera"
    camera.data.name = f"{material_name}_Visualization_Camera_Data"
    camera.rotation_euler = (0, 0, 0)
    camera.data.type = "ORTHO"
    new_scene.camera = camera

    return new_scene, camera


@dataclass
class NodeRow:
    """Visual row metadata for a node label/value pair."""

    textobject: bpy.types.Object
    text: str
    position: Vector
    role: str = "input"  # input or output


class DrawNode:
    """Helper that creates a labeled plane representing a node and its sockets."""

    def __init__(self, node: bpy.types.Node, scene: bpy.types.Scene, scale: float = 0.01) -> None:
        self.node: bpy.types.Node = node
        self.scene: bpy.types.Scene = scene
        self.scale: float = scale
        self.offs_x: float = 0.0
        self.offs_y: float = 0.0
        self.rows: list[NodeRow] = []
        self.node_width: float = 0.0
        self.node_height: float = 0.0
        self.mesh: bpy.types.Mesh | None = None
        self.plane_obj: bpy.types.Object | None = None
        self.position: Vector  # set in _compute_position
        self._init_offsets()
        self._compute_position()
        if self._handle_reroute():
            return
        self._estimate_dimensions()
        self._add_header()
        self._add_image_details()
        self._add_outputs()
        self._add_inputs()
        self._finalize()

    def _init_offsets(self) -> None:
        """Initialize parent offsets for nodes inside frames."""
        if self.node.parent is not None:
            self.offs_x = self.node.parent.location.x
            self.offs_y = self.node.parent.location.y

    def _compute_position(self) -> None:
        """Compute world-space position from node location and parent offsets."""
        node_pos_x = (self.offs_x + self.node.location.x) * self.scale
        node_pos_y = (self.offs_y + self.node.location.y) * self.scale
        self.position = Vector((node_pos_x, node_pos_y, 0.0))

    def _handle_reroute(self) -> bool:
        """Special-case for reroute nodes that have no geometry to draw."""
        if self.node.type == "REROUTE":
            self.node_width = 0.0
            self.node_height = 0.0
            self.input_pos = Vector((0.0, 0.0, 0.0))
            self.output_pos = Vector((0.0, 0.0, 0.0))
            return True
        return False

    def _estimate_dimensions(self) -> None:
        """Estimate on-screen dimensions from Blender's node width."""
        self.node_width = float(self.node.width) * self.scale

    def _add_header(self) -> None:
        """Add a header label for the node name."""
        self.add_text(self.node.name, align=("LEFT", "TOP"), size=TEXT_SCALE, color="LightBlue", role="input")

    def _add_image_details(self) -> None:
        """If this is an image texture node, append image and colorspace info."""
        if self.node.type != "TEX_IMAGE":
            return
        file_name = os.path.basename(self.node.image.filepath)
        self.add_text(file_name, align=("LEFT", "TOP"), size=TEXT_SCALE)
        colorspace_name = self.node.image.colorspace_settings.name
        self.add_text("Color space: ", value=colorspace_name, align=("LEFT", "TOP"), size=TEXT_SCALE)

    def _add_outputs(self) -> None:
        """List output socket identifiers that have outgoing links."""
        for output in self.node.outputs:
            if len(output.links) > 0:
                self.add_text(
                    output.identifier,
                    align=("RIGHT", "TOP"),
                    size=TEXT_SCALE,
                    color="White",
                    role="output",
                )

    def _add_inputs(self) -> None:
        """List selected input sockets and show values for certain identifiers."""
        filter_values = {
            "Alpha",
            "Subsurface Weight",  # Principled
            "Emission Strength",
            "Scale",  # Displacement and others
            "Midlevel",
            "Strength",  # Bump and others
            "Distance",
        }
        for node_input in self.node.inputs:
            if len(node_input.links) > 0:
                self.add_text(node_input.identifier, align=("LEFT", "TOP"), size=TEXT_SCALE)
            elif node_input.identifier in filter_values:
                self.add_text(
                    node_input.identifier,
                    self.node_value_to_text(node_input),
                    align=("LEFT", "TOP"),
                    size=TEXT_SCALE,
                )

    def _finalize(self) -> None:
        """Create the plane mesh and parent text rows to it."""
        self.node_height = len(self.rows) * LINE_HEIGHT
        self.create_node_mesh()
        if self.plane_obj is not None:
            for row in self.rows:
                row.textobject.parent = self.plane_obj

    def node_value_to_text(self, node_input: bpy.types.NodeSocket) -> str:
        """Format default value of a node input as a short string."""
        if node_input.type == "VALUE":
            return str(round(float(node_input.default_value), 2))
        if node_input.type == "RGBA":
            rgba = node_input.default_value
            return f"{round(rgba[0], 1)}, {round(rgba[1], 1)}, {round(rgba[2], 1)}, {round(rgba[3], 1)}"
        if node_input.type == "VECTOR":
            vec = node_input.default_value
            return f"{round(vec[0], 1)}, {round(vec[1], 1)}, {round(vec[2], 1)}"
        return ""

    def create_node_mesh(self) -> None:
        """Create the node backing plane, bevel its corners, and assign materials."""
        self.mesh = bpy.data.meshes.new(name=f"{self.node.name}_Plane")
        self.plane_obj = bpy.data.objects.new(name=f"{self.node.name}_Plane_Obj", object_data=self.mesh)
        self.scene.collection.objects.link(self.plane_obj)
        bpy.context.view_layer.objects.active = self.plane_obj

        self.plane_obj.location = self.position
        bm = bmesh.new()
        try:
            bm.verts.new((0.0, 0.0, 0.0))
            bm.verts.new((0.0, -self.node_height, 0.0))
            bm.verts.new((self.node_width, -self.node_height, 0.0))
            bm.verts.new((self.node_width, 0.0, 0.0))
            bm.faces.new(bm.verts)
            bm.to_mesh(self.mesh)
        finally:
            bm.free()

        # Bevel modifier for rounded corners
        bpy.ops.object.modifier_add(type="BEVEL")
        bpy.context.object.modifiers["Bevel"].width = 0.2
        bpy.context.object.modifiers["Bevel"].segments = 10
        bpy.context.object.modifiers["Bevel"].affect = "VERTICES"

        # Assign base material
        if "DarkGrey" in bpy.data.materials:
            self.plane_obj.data.materials.append(bpy.data.materials["DarkGrey"])

        # Apply bevel modifier, go to edit mode, select all, inset by 0.1, invert selection
        bpy.ops.object.modifier_apply(modifier="Bevel")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.inset(thickness=0.02, depth=0.0)
        bpy.ops.mesh.select_all(action="INVERT")

        # assign a new orange material slot
        bpy.ops.object.material_slot_add()
        bpy.context.object.active_material_index = 1
        if "Orange" in bpy.data.materials:
            bpy.context.object.active_material = bpy.data.materials["Orange"]
        bpy.ops.object.material_slot_assign()

        # back to object mode
        bpy.ops.object.mode_set(mode="OBJECT")

    def count_used_inputs(self):
        """Count input sockets that have incoming links."""
        i = 0
        for in_node in self.node.inputs:
            if len(in_node.links) > 0:
                i += 1
        return i

    def add_text(  # noqa: PLR0913
        self,
        text: str,
        value: str | None = None,
        *,
        align: tuple[str, str] = ("LEFT", "TOP"),
        size: float = 0.3,
        color: str = "White",
        role: str = "input",
    ) -> NodeRow | None:
        """Add a label (and optional value) row to this node.

        Args:
            text: Main label text.
            value: Optional value text to be displayed on the right.
            align: Tuple of (horizontal alignment, vertical alignment).
            size: Font size for the text.
            color: Material name to use for the label text (e.g., "White").
            role: Row role, typically "input" or "output".

        Returns:
            Created NodeRow or None for REROUTE nodes.
        """
        if self.node.type == "REROUTE":
            return None

        alignment_x, alignment_y = align

        if alignment_x == "LEFT":
            x = MARGIN
            link_x = 0.0
        else:
            x = self.node_width - MARGIN
            link_x = self.node_width

        y = -len(self.rows) * LINE_HEIGHT
        link_y = y - LINE_HEIGHT * 0.35

        bpy.ops.object.text_add(location=(x, y, 0.05))
        text_obj = bpy.context.object
        safe_text = text.replace(" ", "_")
        text_obj.name = f"{self.node.name}_{safe_text}"
        text_obj.data.name = f"{self.node.name}_{safe_text}_Data"
        text_obj.data.body = text
        text_obj.data.align_x = alignment_x
        text_obj.data.align_y = alignment_y
        text_obj.data.size = size

        if value is not None:
            bpy.ops.object.text_add(location=(self.node_width - MARGIN, y, 0.05))
            value_text_obj = bpy.context.object
            value_text_obj.name = f"{self.node.name}_{safe_text}_Value"
            value_text_obj.data.name = f"{self.node.name}_{safe_text}_Value_Data"
            value_text_obj.data.body = value
            value_text_obj.data.align_x = "RIGHT"
            value_text_obj.data.align_y = alignment_y
            value_text_obj.data.size = size
            value_text_obj.parent = text_obj
            value_text_obj.location = (self.node_width - 2 * MARGIN, 0.0, 0.05)
            if "Red" in bpy.data.materials:
                value_text_obj.data.materials.append(bpy.data.materials["Red"])

        if color in bpy.data.materials:
            text_obj.data.materials.append(bpy.data.materials[color])

        node_row = NodeRow(
            textobject=text_obj,
            text=text,
            position=Vector((link_x, link_y, -0.05)),
            role=role,
        )
        self.rows.append(node_row)
        return node_row


def draw_link(start_pos: Vector, end_pos: Vector, scene: bpy.types.Scene) -> bpy.types.Object:
    """Create a beveled curve and end-point spheres to visualize a link."""
    curve_data = bpy.data.curves.new("link_curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.fill_mode = "FULL"
    curve_data.bevel_depth = 0.02

    spline = curve_data.splines.new(type="BEZIER")
    spline.bezier_points.add(1)

    spline.bezier_points[0].co = start_pos
    spline.bezier_points[0].handle_right = start_pos + Vector((0.5, 0.0, 0.0))
    spline.bezier_points[0].handle_left = start_pos - Vector((0.5, 0.0, 0.0))
    spline.bezier_points[1].co = end_pos
    spline.bezier_points[1].handle_left = end_pos - Vector((0.5, 0.0, 0.0))
    spline.bezier_points[1].handle_right = end_pos + Vector((0.5, 0.0, 0.0))

    curve_obj = bpy.data.objects.new("Link", curve_data)
    scene.collection.objects.link(curve_obj)
    curve_obj.data.resolution_u = 25

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.08, location=start_pos + Vector((0.0, 0.0, 0.1)))

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.08, location=end_pos + Vector((0.0, 0.0, 0.1)))

    if "Orange" in bpy.data.materials:
        curve_obj.data.materials.append(bpy.data.materials["Orange"])
        bpy.context.object.data.materials.append(bpy.data.materials["Orange"])
        bpy.context.object.data.materials.append(bpy.data.materials["Orange"])

    return curve_obj


def _find_socket_position(viz: DrawNode, socket_id: str, want_role: str) -> Vector | None:
    """Return the world-space position of a socket label row if found."""
    if viz.node.type == "REROUTE":
        return viz.position
    for row in viz.rows:
        if row.text == socket_id and row.role == want_role:
            return row.position + viz.position
    return None


def visualize_links(
    node_tree: bpy.types.NodeTree,
    viz_nodes: list[DrawNode],
    scene: bpy.types.Scene,
) -> list[bpy.types.Object]:
    """Draw link curves between already created DrawNode instances."""
    link_objects: list[bpy.types.Object] = []

    for link in node_tree.links:
        from_viz = next((n for n in viz_nodes if n.node == link.from_node), None)
        to_viz = next((n for n in viz_nodes if n.node == link.to_node), None)
        if not from_viz or not to_viz:
            continue

        start_pos = _find_socket_position(from_viz, link.from_socket.identifier, "output")
        if start_pos is None:
            continue
        end_pos = _find_socket_position(to_viz, link.to_socket.identifier, "input")
        if end_pos is None:
            continue

        link_obj = draw_link(start_pos, end_pos, scene)
        link_objects.append(link_obj)

    return link_objects


def create_emit_material(
    name: str,
    color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
) -> bpy.types.Material:
    """Create a simple emissive material, mixed with transparency by alpha channel."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    for n in list(nodes):
        nodes.remove(n)

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (200, 0)

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    emission.inputs[0].default_value = color  # type: ignore[index]

    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, 0)

    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (0, -200)
    mix.inputs[0].default_value = color[3]  # type: ignore[index]

    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs[0])

    return mat


def _ensure_base_materials() -> None:
    """Create base emissive/transparent materials used by the visualizer if missing."""
    create_emit_material("Black", (0, 0, 0, 1))
    create_emit_material("Grey", (0.5, 0.5, 0.5, 1))
    create_emit_material("White", (0.9, 0.9, 0.9, 1))
    create_emit_material("Orange", (0.7, 0.35, 0.0, 1))
    create_emit_material("LightBlue", (0.2, 1.0, 0.8, 1))
    create_emit_material("DarkGrey", (0.04, 0.04, 0.04, 0.8))
    create_emit_material("Red", (1.0, 0.8, 0.8, 1))


def visualize_nodes(tempfolder: str | None, name: str, node_tree: bpy.types.NodeTree) -> None:
    """Visualize a given node tree and render an image to tempfolder."""
    _ensure_base_materials()
    white = bpy.data.materials.get("White") or create_emit_material("White", (0.9, 0.9, 0.9, 1))

    new_scene, camera = setup_scene(name)

    min_x, max_x, min_y, max_y = (float("inf"), float("-inf"), float("inf"), float("-inf"))

    nodes: list[DrawNode] = []
    for node in node_tree.nodes:
        if node.type == "FRAME":
            continue

        viz_node = DrawNode(node, new_scene)
        nodes.append(viz_node)

        min_x = min(min_x, viz_node.position.x)
        max_x = max(max_x, viz_node.position.x + viz_node.node_width)
        min_y = min(min_y, viz_node.position.y)
        max_y = max(max_y, viz_node.position.y - viz_node.node_height)

    visualize_links(node_tree, nodes, new_scene)

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    width = max_x - min_x
    height = max_y - min_y

    camera.location.x = center_x
    camera.location.y = center_y
    camera.data.ortho_scale = max(width, height) * 1.1

    max_corner = max(width, height)
    bpy.ops.object.text_add(location=(center_x - max_corner / 2.0, center_y + max_corner / 2.0, 0.0))
    text_obj = bpy.context.object
    text_obj.name = f"{name}_Name"
    text_obj.data.name = f"{name}_Name_Data"
    text_obj.data.body = f"Material: {name}"
    text_obj.data.align_x = "LEFT"
    text_obj.data.align_y = "TOP"
    text_obj.data.size = 0.6
    if "White" in bpy.data.materials:
        text_obj.data.materials.append(white)

    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.device = "GPU"
    bpy.context.scene.cycles.samples = 20
    bpy.context.scene.cycles.use_denoising = False

    bpy.context.scene.render.resolution_x = MAX_EXPORT_SIZE
    bpy.context.scene.render.resolution_y = MAX_EXPORT_SIZE
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = "WEBP"
    bpy.context.scene.render.image_settings.quality = 20

    out_base = os.path.join(tempfolder or "", f"Nodes_{name}")
    bpy.context.scene.render.filepath = out_base

    bpy.ops.render.render(write_still=True)

    bpy.data.scenes.remove(new_scene)


def visualize_material_nodes(material_name: str, tempfolder: str | None = None) -> None:
    """Visualize a material node tree by material name."""
    if material_name not in bpy.data.materials:
        logger.warning("Material '%s' not found.", material_name)
        return

    material = bpy.data.materials[material_name]
    visualize_nodes(tempfolder, material_name, material.node_tree)


def visualize_all_nodes(tempfolder: str | None = None, objects: Iterable[bpy.types.Object] | None = None) -> None:
    """Visualize all materials and geometry node groups used by the given objects."""
    if not objects:
        # Fallback to visible objects to preserve previous behavior
        objects = getattr(bpy.context, "visible_objects", [])
        if not objects:
            logger.info("No objects provided and no visible objects; skipping node visualization.")
            return

    for material in _collect_materials(objects):
        if material.use_nodes and material.node_tree is not None:
            visualize_nodes(tempfolder, material.name, material.node_tree)

    for geometry_nodes in _collect_geo_groups(objects):
        if geometry_nodes.bl_idname == "GeometryNodeTree":
            visualize_nodes(tempfolder, geometry_nodes.name, geometry_nodes)


def _collect_materials(objects: Iterable[bpy.types.Object]) -> list[bpy.types.Material]:
    """Collect unique materials used by the provided objects."""
    materials: list[bpy.types.Material] = []
    for ob in objects:
        if ob.type in {"MESH", "CURVE", "SURFACE", "FONT", "META", "VOLUME"}:
            for slot in ob.material_slots:
                if slot.material is not None and slot.material not in materials:
                    materials.append(slot.material)
    return materials


def _collect_geo_groups(objects: Iterable[bpy.types.Object]) -> list[bpy.types.NodeTree]:
    """Collect unique geometry node groups from object modifiers."""
    groups: list[bpy.types.NodeTree] = []
    for ob in objects:
        for modifier in ob.modifiers:
            if modifier.type == "NODES" and modifier.node_group not in groups:
                groups.append(modifier.node_group)
    return groups


def activate_object(aob: bpy.types.Object) -> None:
    """Deselect all objects and make the given object active."""
    for obj in bpy.context.visible_objects:
        obj.select_set(state=False)
    aob.select_set(state=True)
    bpy.context.view_layer.objects.active = aob


def _has_valid_uv(obj: bpy.types.Object) -> bool:
    """Return True if the object has a non-empty active UV layer.

    Prefer direct access for Blender mesh data; guard for version differences.
    """
    if obj.type != "MESH":
        return False
    mesh = obj.data  # type: ignore[assignment]
    if not hasattr(mesh, "uv_layers"):
        return False
    uv_layers = mesh.uv_layers
    if not uv_layers:
        return False
    active = uv_layers.active

    def _layer_has_data(layer: object) -> bool:
        try:
            # type: ignore[attr-defined]
            return len(layer.data) > 0  # type: ignore[arg-type]
        except (TypeError, AttributeError, KeyError):
            return bool(getattr(layer, "data", []))

    if active is not None:
        return _layer_has_data(active)

    # If no active layer, consider any UV layer with data as valid
    return any(_layer_has_data(layer) for layer in uv_layers)


def _unique_mesh_objects_with_uv(objects: Iterable[bpy.types.Object]) -> list[bpy.types.Object]:
    """Deduplicate mesh objects by data and keep only those with valid UVs."""
    unique_meshes_obs: list[bpy.types.Object] = []
    for obj in objects:
        if _has_valid_uv(obj) and obj.data not in [o.data for o in unique_meshes_obs]:
            unique_meshes_obs.append(obj)
    return unique_meshes_obs


def save_uv_layouts(tempfolder: str, objects: Iterable[bpy.types.Object]) -> None:
    """Export combined and per-object UV layout webp images for given objects."""
    for ob in objects:
        ob.select_set(state=True)

    for obj in objects:
        if obj.type == "MESH":
            bpy.context.view_layer.objects.active = obj
            break

    for ob in objects:
        ob.select_set(state=True)

    unique_meshes_obs = _unique_mesh_objects_with_uv(objects)
    if not unique_meshes_obs:
        return

    filepath = os.path.join(tempfolder, "UV_Map_Complete_")

    activate_object(unique_meshes_obs[0])
    for obj in unique_meshes_obs:
        obj.select_set(state=True)
    bpy.context.scene.update_tag()

    render_UVs.export_uvs_as_webps(unique_meshes_obs, filepath)

    if len(unique_meshes_obs) == 1:
        return

    for obj in unique_meshes_obs:
        activate_object(obj)
        logger.info("Export UV layout for %s (%s)", obj.name, obj.data.name)
        filepath = os.path.join(tempfolder, f"UV_Map_{obj.name}")
        render_UVs.export_uvs_as_webps([obj], filepath)


def export_all_textures(tempfolder: str, objects: Iterable[bpy.types.Object]) -> None:
    """Export all image textures used by provided objects as WEBP images."""
    unique_textures: list[bpy.types.Image] = []
    for ob in objects:
        if ob.type in {"MESH", "CURVE", "SURFACE", "FONT", "META", "VOLUME"}:
            for slot in ob.material_slots:
                if slot.material and slot.material.node_tree:
                    for node in slot.material.node_tree.nodes:
                        if node.type == "TEX_IMAGE" and node.image and node.image not in unique_textures:
                            unique_textures.append(node.image)

    for img in unique_textures:
        bpy.context.scene.render.image_settings.file_format = "WEBP"
        quality = 60 if "normal" in img.name.lower() else 20

        filepath = os.path.join(tempfolder, f"TEXTURE_{img.name}")
        if img.size[0] > MAX_EXPORT_SIZE or img.size[1] > MAX_EXPORT_SIZE:
            scale = MAX_EXPORT_SIZE / max(img.size[0], img.size[1])
            img.scale(round(img.size[0] * scale), round(img.size[1] * scale))
        img.save_render(filepath=bpy.path.ensure_ext(filepath, ".webp"), quality=quality)


def export_all_material_textures(tempfolder: str, material: bpy.types.Material) -> None:
    """Export all image textures used by a single material as WEBP images."""
    if not material.node_tree:
        return

    unique_textures: list[bpy.types.Image] = []
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image and node.image not in unique_textures:
            unique_textures.append(node.image)

    for img in unique_textures:
        bpy.context.scene.render.image_settings.file_format = "WEBP"
        quality = 60 if "normal" in img.name.lower() else 20

        filepath = os.path.join(tempfolder, f"TEXTURE_{img.name}")
        if img.size[0] > MAX_EXPORT_SIZE or img.size[1] > MAX_EXPORT_SIZE:
            scale = MAX_EXPORT_SIZE / max(img.size[0], img.size[1])
            img.scale(round(img.size[0] * scale), round(img.size[1] * scale))
        img.save_render(filepath=bpy.path.ensure_ext(filepath, ".webp"), quality=quality)


def visualize_and_save_all(tempfolder: str, objects: Iterable[bpy.types.Object]) -> None:
    """Export all textures, UVs, and visualize all nodes for provided objects."""
    export_all_textures(tempfolder, objects)
    save_uv_layouts(tempfolder, objects)
    visualize_all_nodes(tempfolder, objects)
