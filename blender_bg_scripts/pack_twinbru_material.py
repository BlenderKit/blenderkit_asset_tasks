"""TwinBru material packer for Blender background use.

This module reads a JSON instruction file, builds a Blender material from TwinBru
textures located in a temp folder, links textures into a Principled BSDF setup,
marks the material as an asset, packs all external data, and saves the .blend.

The expected JSON structure is:
{
  "asset_data": {"name": "Material Name"},
  "temp_folder": "C:/path/to/textures",
  "result_filepath": "C:/path/to/output.blend"
}
"""

# ruff: noqa: I001

import json
import logging
import os
import sys

import bpy


# Layout constants to avoid magic numbers
NODE_GAP_X: int = 400
NODE_GAP_Y: int = 300

# Mapping from filename token to Principled BSDF input name
TEXTURE_MAPPING: dict[str, str] = {
    "col": "Base Color",
    "met": "Metallic",
    "rough": "Roughness",
    "alpha": "Alpha",
    "nrm": "Normal",
}


logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")


def _ensure_principled_setup(material: bpy.types.Material) -> tuple[bpy.types.Node, bpy.types.Node, bpy.types.NodeTree]:
    """Ensure a material has a Principled BSDF connected to a Material Output.

    Args:
        material: The Blender material to prepare.

    Returns:
        A tuple of (principled_bsdf_node, output_node, node_tree) for convenience.
    """
    material.use_nodes = True
    node_tree = material.node_tree
    nodes = node_tree.nodes
    links = node_tree.links

    output_node = nodes.get("Material Output") or nodes.new(type="ShaderNodeOutputMaterial")
    output_node.location = (NODE_GAP_X, 0)

    principled_bsdf = nodes.get("Principled BSDF") or nodes.new(type="ShaderNodeBsdfPrincipled")
    principled_bsdf.location = (0, 0)

    # Link the Principled BSDF to the Output Material node
    links.new(principled_bsdf.outputs[0], output_node.inputs[0])
    return principled_bsdf, output_node, node_tree


def _link_normal_map(
    node_tree: bpy.types.NodeTree,
    texture_node: bpy.types.Node,
    principled_bsdf: bpy.types.Node,
) -> None:
    """Insert nodes to convert DirectX normals to OpenGL and feed a Normal Map node.

    Args:
        node_tree: The material node tree.
        texture_node: The image texture node providing the normal map.
        principled_bsdf: The Principled BSDF node to connect the normal to.
    """
    nodes = node_tree.nodes
    links = node_tree.links

    normal_map = nodes.new(type="ShaderNodeNormalMap")
    normal_map.location = (-1 * NODE_GAP_X, texture_node.location[1])
    normal_map.space = "TANGENT"

    # Convert DX normal map to OpenGL by inverting the green channel
    separate_xyz = nodes.new(type="ShaderNodeSeparateXYZ")
    separate_xyz.location = (-2.5 * NODE_GAP_X, texture_node.location[1])

    invert_y = nodes.new(type="ShaderNodeMath")
    invert_y.operation = "SUBTRACT"
    invert_y.inputs[0].default_value = 1.0
    invert_y.location = (-2 * NODE_GAP_X, texture_node.location[1] - 50)

    combine_xyz = nodes.new(type="ShaderNodeCombineXYZ")
    combine_xyz.location = (-1.5 * NODE_GAP_X, texture_node.location[1] - 100)

    # Link nodes: texture -> separate -> (invert Y) -> combine -> normal map -> principled
    links.new(texture_node.outputs[0], separate_xyz.inputs[0])
    links.new(separate_xyz.outputs[0], combine_xyz.inputs[0])  # X
    links.new(separate_xyz.outputs[1], invert_y.inputs[1])  # Y
    links.new(invert_y.outputs[0], combine_xyz.inputs[1])  # Inverted Y
    links.new(separate_xyz.outputs[2], combine_xyz.inputs[2])  # Z
    links.new(combine_xyz.outputs[0], normal_map.inputs["Color"])
    links.new(normal_map.outputs[0], principled_bsdf.inputs["Normal"])


def build_material_from_textures(name: str, texture_dir: str) -> bpy.types.Material:
    """Create a Blender material by wiring TwinBru textures into Principled BSDF.

    Args:
        name: Name of the material to create.
        texture_dir: Directory containing TwinBru texture files.

    Returns:
        The newly created Blender material.
    """
    logger.info("Building material '%s' from textures in %s", name, texture_dir)

    material = bpy.data.materials.new(name=name)
    material.name = name
    material.blend_method = "BLEND"
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    material.use_fake_user = True  # ensure it stays saved

    principled_bsdf, _output_node, node_tree = _ensure_principled_setup(material)
    nodes = node_tree.nodes
    links = node_tree.links

    try:
        texture_files: list[str] = os.listdir(texture_dir)
    except OSError:
        logger.exception("Failed to list textures in %s", texture_dir)
        texture_files = []

    index = 0
    for token, principled_input in TEXTURE_MAPPING.items():
        for texture_file in texture_files:
            texture_file_lower = texture_file.lower()
            # Match token followed by dot before extension, e.g., *_col.png
            if f"{token}." not in texture_file_lower:
                continue

            texture_path = os.path.join(texture_dir, texture_file)
            logger.info("Using texture: %s -> %s", texture_file, principled_input)

            texture_node = nodes.new(type="ShaderNodeTexImage")
            texture_node.location = (-4 * NODE_GAP_X, NODE_GAP_Y * 2 - index * NODE_GAP_Y)
            try:
                texture_node.image = bpy.data.images.load(texture_path)
            except (RuntimeError, OSError) as exc:  # file missing or unreadable
                logger.warning("Couldn't load image %s: %s", texture_path, exc)
                # Remove unused node to keep the tree clean
                nodes.remove(texture_node)
                continue

            # Set color space for non-color data
            if principled_input != "Base Color":
                texture_node.image.colorspace_settings.name = "Non-Color"

            if principled_input == "Normal":
                _link_normal_map(node_tree, texture_node, principled_bsdf)
            else:
                links.new(texture_node.outputs[0], principled_bsdf.inputs[principled_input])

            index += 1

    # Mark as asset (if available in this Blender version)
    try:
        material.asset_mark()
        material.asset_generate_preview()
    except AttributeError:
        logger.debug("Asset marking APIs not available in this Blender version.")

    return material


def main(argv: list[str]) -> int:
    """Entry point for Blender background execution.

    Args:
        argv: Command-line arguments (sys.argv).

    Returns:
        Process exit code (0 for success, non-zero for failure).
    """
    # Blender passes our argument after a '--' separator; fallback to last item
    datafile = argv[-1]
    logger.info("Datafile: %s", datafile)

    try:
        with open(datafile, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read JSON input %s", datafile)
        return 2

    twinbru_asset = data.get("asset_data", {})
    temp_folder = data.get("temp_folder")
    result_filepath = data.get("result_filepath")

    if not temp_folder or not result_filepath:
        logger.error("Missing required keys 'temp_folder' or 'result_filepath' in input JSON")
        return 3

    readable_name = str(twinbru_asset.get("name") or "TwinBru Material")
    logger.info("Temp folder: %s", temp_folder)

    material = build_material_from_textures(readable_name, temp_folder)
    material.name = readable_name  # ensure final name
    logger.info("Processed material: %s", material.name)

    # Ensure output directory exists
    outdir = os.path.dirname(result_filepath)
    if outdir and not os.path.isdir(outdir):
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError:
            logger.exception("Failed to create output directory %s", outdir)
            return 4

    # Pack external files into the .blend and save
    bpy.ops.file.pack_all()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=result_filepath)
    except RuntimeError:
        logger.exception("Failed to save blend file to %s", result_filepath)
        return 5

    logger.info("Saved blend file to %s", result_filepath)
    return 0


if __name__ == "__main__":
    exit_code = main(sys.argv)
    sys.exit(exit_code)
