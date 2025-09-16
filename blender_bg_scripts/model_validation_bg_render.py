"""Render model validation animations and export helper visualizations.

This background Blender script loads a model asset, renders validation scenes
(render, mesh checker, and mesh checker without modifiers), and exports helper
visualizations such as node graphs using internal utilities.
"""

# isort: skip_file
from __future__ import annotations

import json
import logging
import math
import os
import sys
from typing import Any
from collections.abc import Sequence

import bpy

# Import utils - add path for Blender background execution
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import render_nodes_graph, utils  # noqa: E402


logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


MAX_FACE_COUNT_FOR_GN = 300_000


def link_collection(
    file_name: str,
    obnames: Sequence[str] | None = None,
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    link: bool = False,
    parent: str | None = None,
    **kwargs: Any,
) -> tuple[bpy.types.Object, list[bpy.types.Object]]:
    """Link an instanced collection from a .blend file.

    Args:
        file_name: Path to the .blend file.
        obnames: Unused; kept for API compatibility.
        location: World location for the instance empty.
        link: Whether to link instead of appending.
        parent: Optional parent object name to parent the instance to.
        **kwargs: Expected to include 'name' of the collection to instance and optional 'rotation'.

    Returns:
        A tuple of (instance empty object, list of newly linked objects [unused, empty]).
    """
    _ = obnames  # kept for API compatibility
    sel = utils.selection_get()

    with bpy.data.libraries.load(file_name, link=link, relative=True) as (
        data_from,
        data_to,
    ):
        name = kwargs.get("name")
        for col in data_from.collections:
            if name and col == name:
                data_to.collections = [col]

    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    if kwargs.get("rotation") is not None:
        rotation = kwargs["rotation"]

    bpy.ops.object.empty_add(type="PLAIN_AXES", location=location, rotation=rotation)
    main_object = bpy.context.view_layer.objects.active
    main_object.instance_type = "COLLECTION"

    if parent is not None:
        main_object.parent = bpy.data.objects.get(parent)

    main_object.matrix_world.translation = location

    for col in bpy.data.collections:
        if col.library is not None:
            fp = bpy.path.abspath(col.library.filepath)
            fp1 = bpy.path.abspath(file_name)
            if fp == fp1:
                main_object.instance_collection = col
                break

    # sometimes, the lib might already  be without the actual link.
    if not main_object.instance_collection and kwargs.get("name"):
        col = bpy.data.collections.get(kwargs.get("name"))
        if col:
            main_object.instance_collection = col

    main_object.name = main_object.instance_collection.name

    utils.selection_set(sel)
    return main_object, []


def add_text_line(strip: str, text: str) -> None:
    """Append a text line to a text strip in the Composite scene."""
    bpy.data.scenes["Composite"].sequence_editor.sequences_all[strip].text += text + 10 * "    "


def write_out_param(asset_data: dict[str, Any], param_name: str) -> None:
    """Write out a parameter to the overlay if present."""
    pl = utils.get_param(asset_data, param_name)
    if pl is not None:
        add_text_line("asset", f"{param_name}:{pl}")


def set_text(strip: str, text: str) -> None:
    """Set the text on a given text strip in the Composite scene."""
    bpy.data.scenes["Composite"].sequence_editor.sequences_all[strip].text = text


def scale_cameras(asset_data: dict[str, Any]) -> None:
    """Scale helper objects and set camera ortho scale based on asset bounds."""
    params = asset_data["dictParameters"]
    minx = params["boundBoxMinX"]
    miny = params["boundBoxMinY"]
    minz = params["boundBoxMinZ"]
    maxx = params["boundBoxMaxX"]
    maxy = params["boundBoxMaxY"]
    maxz = params["boundBoxMaxZ"]

    dx = maxx - minx
    dy = maxy - miny
    dz = maxz - minz

    logger.debug("asset bounds dx=%s dy=%s dz=%s", dx, dy, dz)

    r = math.sqrt(dx * dx + dy * dy + dz * dz)
    r *= 1.2
    scaler = bpy.data.objects["scaler"]
    scaler.scale = (r, r, r)
    scaler.location.z = (maxz + minz) / 2

    # get scene camera
    cam = bpy.data.objects["Camera"]
    # Set ortho scale to max of dimensions
    cam.data.ortho_scale = max(dx, dy, dz) * 1.1

    bpy.context.view_layer.update()


def check_for_flat_faces() -> bool:
    """Return True if any mesh polygon in the scene is not smooth shaded."""
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH":
            for f in ob.data.polygons:
                if not f.use_smooth:
                    return True
    return False


def mark_freestyle_edges() -> None:
    """Mark all mesh edges for Freestyle rendering (unused; kept for reference)."""
    for m in bpy.data.meshes:
        for e in m.edges:
            e.use_freestyle_mark = True


def set_asset_data_texts(asset_data: dict[str, Any]) -> None:
    """Populate overlay text with key asset properties."""
    set_text("asset", "")
    add_text_line("asset", asset_data["name"])
    dx = utils.get_param(asset_data, "dimensionX")
    dy = utils.get_param(asset_data, "dimensionY")
    dz = utils.get_param(asset_data, "dimensionZ")
    dim_text = f"Dimensions:{dx}x{dy}x{dz}m"
    add_text_line("asset", dim_text)
    fc = utils.get_param(asset_data, "faceCount", 1)
    fcr = utils.get_param(asset_data, "faceCountRender", 1)

    add_text_line("asset", f"fcount {fc} render {fcr}")

    if check_for_flat_faces():
        add_text_line("asset", "Flat faces detected")

    write_out_param(asset_data, "productionLevel")
    write_out_param(asset_data, "shaders")
    write_out_param(asset_data, "modifiers")
    write_out_param(asset_data, "meshPolyType")
    write_out_param(asset_data, "manifold")
    write_out_param(asset_data, "objectCount")
    write_out_param(asset_data, "nodeCount")
    write_out_param(asset_data, "textureCount")
    write_out_param(asset_data, "textureResolutionMax")


def set_scene(name: str = "") -> None:
    """Switch active window to a scene by name."""
    logger.info("setting scene %s", name)
    bpy.context.window.scene = bpy.data.scenes[name]
    c = bpy.context.scene.objects.get("Camera")
    if c is not None:
        bpy.context.scene.camera = c
    bpy.context.view_layer.update()


def set_view_shading(
    shading_type: str = "RENDERED",
    *,
    face_orientation: bool = False,
    wireframe: bool = False,
) -> None:
    """Set viewport shading and overlays across workspaces.

    Args:
        shading_type: Target shading mode (e.g., 'RENDERED', 'MATERIAL').
        face_orientation: Whether to show face orientation overlay.
        wireframe: Whether to show wireframes overlay.
    """
    for w in bpy.data.workspaces:
        for a in w.screens[0].areas:
            if a.type == "VIEW_3D":
                for s in a.spaces:
                    if s.type == "VIEW_3D":
                        s.shading.type = shading_type
                        s.overlay.show_wireframes = wireframe
                        s.overlay.show_face_orientation = face_orientation
    bpy.context.scene.display.shading.type = shading_type


def set_workspace(name: str = "Layout") -> None:
    """Switch workspace a couple times to encourage UI refresh in background."""
    for _ in range(2):
        bpy.context.window.workspace = bpy.data.workspaces[name]
        bpy.context.workspace.update_tag()
        bpy.context.view_layer.update()


def _unlink_all_collection_instances() -> None:
    """Unlink all collection instance empties from all scenes and collections."""
    for scn in bpy.data.scenes:
        coll = scn.collection
        for ob in list(coll.objects):
            if ob.instance_collection:
                coll.objects.unlink(ob)
    for coll in bpy.data.collections:
        for ob in list(coll.objects):
            if ob.instance_collection:
                coll.objects.unlink(ob)


def switch_off_all_modifiers() -> list[tuple[bpy.types.Object, bpy.types.Modifier, bool]]:
    """Disable all mesh modifiers for render and record original states."""
    original_states: list[tuple[bpy.types.Object, bpy.types.Modifier, bool]] = []
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH":
            for m in ob.modifiers:
                original_states.append((ob, m, m.show_render))
                m.show_render = False
    return original_states


def switch_on_all_modifiers(original_states: list[tuple[bpy.types.Object, bpy.types.Modifier, bool]]) -> None:
    """Restore modifiers' render state from a recorded list."""
    for _ob, m, state in original_states:
        m.show_render = state


def add_geometry_nodes_to_all_objects(group: str = "wireNodes", dimensions: float = 1.0) -> None:
    """Add a Geometry Nodes modifier to visible meshes under a face-count threshold."""
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH" and ob.visible_get() and len(ob.data.polygons) < MAX_FACE_COUNT_FOR_GN:
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.modifier_add(type="NODES")
            m = bpy.context.object.modifiers[-1]
            m.node_group = bpy.data.node_groups[group]
            # asset dimensions needed
            m["Socket_0"] = float(dimensions)


def remove_geometry_nodes_from_all_objects(group: str = "wireNodes") -> None:
    """Remove the Geometry Nodes modifier with the given group from visible meshes."""
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH" and ob.visible_get() and len(ob.data.polygons) < MAX_FACE_COUNT_FOR_GN:
            bpy.context.view_layer.objects.active = ob
            # check if the modifier is there
            for m in ob.modifiers:
                if m.type == "NODES" and m.node_group.name == group:
                    bpy.context.object.modifiers.remove(m)


def render_model_validation(asset_data: dict[str, Any], filepath: str) -> None:
    """Render validation passes for the model into scene-defined outputs."""
    logger.debug("Render target filepath: %s", filepath)

    # render basic render
    set_scene("Render")
    bpy.ops.render.render(animation=True)

    # render the Mesh checker
    # now in render
    set_scene("Mesh_checker")
    bpy.ops.render.render(animation=True)

    # switch off modifiers for this one
    set_scene("Mesh_checker_no_modif")
    original_states = switch_off_all_modifiers()
    dimension_x = utils.get_param(asset_data, "dimensionX")
    dimension_y = utils.get_param(asset_data, "dimensionY")
    dimension_z = utils.get_param(asset_data, "dimensionZ")
    # Max length is taken as the dimension of the asset
    dimensions = max(dimension_x, dimension_y, dimension_z)
    add_geometry_nodes_to_all_objects(group="wireNodes", dimensions=dimensions)
    bpy.ops.render.render(animation=True)
    remove_geometry_nodes_from_all_objects(group="wireNodes")
    switch_on_all_modifiers(original_states)


def export_gltf(filepath: str = "") -> None:
    """Export selected objects to GLB using built-in exporter."""
    # Log all selected object names first
    for ob in bpy.context.selected_objects:
        logger.info("Exporting object: %s", ob.name)
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        export_copyright="",
        export_image_format="WEBP",
        export_image_add_webp=True,
        export_image_webp_fallback=False,
        export_texture_dir="",
        export_jpeg_quality=50,
        export_image_quality=50,
        export_keep_originals=False,
        export_texcoords=True,
        export_normals=True,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
        export_draco_color_quantization=10,
        export_draco_generic_quantization=12,
        export_tangents=False,
        export_materials="EXPORT",
        export_colors=True,
        export_attributes=False,
        use_mesh_edges=False,
        use_mesh_vertices=False,
        export_cameras=False,
        use_selection=True,
        use_visible=False,
        use_renderable=False,
        use_active_collection_with_nested=True,
        use_active_collection=False,
        use_active_scene=False,
        export_extras=False,
        export_yup=True,
        export_apply=False,
        export_animations=True,
        export_frame_range=False,
        export_frame_step=1,
        export_force_sampling=True,
        export_animation_mode="ACTIONS",
        export_nla_strips_merged_animation_name="Animation",
        export_def_bones=False,
        export_hierarchy_flatten_bones=False,
        export_optimize_animation_size=True,
        export_optimize_animation_keep_anim_armature=True,
        export_optimize_animation_keep_anim_object=False,
        export_negative_frame="SLIDE",
        export_anim_slide_to_zero=False,
        export_bake_animation=False,
        export_anim_single_armature=True,
        export_reset_pose_bones=True,
        export_current_frame=False,
        export_rest_position_armature=True,
        export_anim_scene_split_object=True,
        export_skins=True,
        export_influence_nb=4,
        export_all_influences=False,
        export_morph=True,
        export_morph_normal=True,
        export_morph_tangent=False,
        export_morph_animation=True,
        export_morph_reset_sk_data=True,
        export_lights=False,
        export_try_sparse_sk=True,
        export_try_omit_sparse_sk=False,
        export_gpu_instances=False,
        export_nla_strips=True,
        export_original_specular=False,
        will_save_settings=False,
        filter_glob="*.glb",
    )


def render_asset_bg(data: dict[str, Any]) -> None:
    """Entry point: load model, render validation animations, and export helpers."""
    asset_data = data["asset_data"]
    set_scene("Empty_start")

    try:
        utils.enable_cycles_CUDA()
    except Exception:
        logger.exception("Failed to configure GPU devices for Cycles")

    # Clean up any existing collection instances
    _unlink_all_collection_instances()
    bpy.ops.outliner.orphans_purge()

    fpath = data.get("file_path")
    if not fpath:
        logger.error("Missing file_path in input data")
        return

    try:
        parent, _ = link_collection(
            fpath,
            location=(0, 0, 0),
            rotation=(0, 0, 0),
            link=True,
            name=asset_data["name"],
            parent=None,
        )

        # Realize instances for UV, texture, and node graph exports
        utils.activate_object(parent)
        bpy.ops.object.duplicates_make_real(
            use_base_parent=True,
            use_hierarchy=True,
        )
        all_obs = bpy.context.selected_objects[:]
        bpy.ops.object.make_local(type="ALL")
    except (OSError, RuntimeError, ValueError):
        logger.exception("Failed to link/realize asset from file: %s", fpath)
        return

    # Link realized objects into other scenes
    for scn in bpy.data.scenes:
        if scn != bpy.context.scene:
            for ob in all_obs:
                scn.collection.objects.link(ob)

    set_asset_data_texts(asset_data)
    scale_cameras(asset_data)

    # Save to temp folder so all auxiliary files go there
    blend_file_path = os.path.join(
        data["temp_folder"],
        f"{asset_data['name']}.blend",
    )
    try:
        bpy.ops.wm.save_as_mainfile(
            filepath=blend_file_path,
            compress=False,
            copy=False,
            relative_remap=False,
        )
    except Exception:
        logger.exception("Failed to save temp blend file: %s", blend_file_path)

    # Render and export helpers
    render_model_validation(asset_data, data["result_filepath"])
    try:
        render_nodes_graph.visualize_and_save_all(
            tempfolder=data["result_folder"],
            objects=all_obs,
        )
    except Exception:
        logger.exception("Failed to export node graphs/auxiliary outputs")


if __name__ == "__main__":
    logger.info("Background model validation generator started")
    datafile = sys.argv[-1]
    try:
        with open(datafile, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse input JSON: %s", datafile)
        sys.exit(1)

    render_asset_bg(data)
