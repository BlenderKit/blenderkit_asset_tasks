"""
This script is used to pack a material from TwinBru to a blenderkit asset.
It imports textures from the unzipped folder , creates a node tree and assigns the textures to the material.
"""

import sys
import os
import bpy
import json

# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)
from blenderkit_server_utils import paths


if __name__ == "__main__":
    datafile = sys.argv[-1]
    print(f"datafile: {datafile}")
    with open(datafile, "r", encoding="utf-8") as f:
        data = json.load(f)
    twinbru_asset = data["asset_data"]
    temp_folder = data["temp_folder"]
    result_filepath = data["result_filepath"]
    print(f"temp_folder: {temp_folder}")

    # convert name - remove _ and remove the number that comes last in name
    # readable_name = twinbru_asset["name"].split("_")
    # capitalize the first letter of each word
    # readable_name = " ".join(word.capitalize() for word in readable_name[:-1])
    readable_name = twinbru_asset["name"]

    # create a new material
    material = bpy.data.materials.new(name=readable_name)
    material.name = readable_name
    material.use_nodes = True
    material.blend_method = "BLEND"
    # material.shadow_method = "HASHED"
    material.diffuse_color = (1, 1, 1, 1)
    # ensure the material is saved
    material.use_fake_user = True
    # create the node tree
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    # set nodes spacing
    node_gap_x = 400
    node_gap_y = 300
    # find the output node
    output_node = nodes.get("Material Output")
    if not output_node:
        output_node = nodes.new(type="ShaderNodeOutputMaterial")
    output_node.location = (node_gap_x, 0)

    # find Principled BSDF node
    principled_bsdf = nodes.get("Principled BSDF")
    if not principled_bsdf:
        principled_bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    principled_bsdf.location = (0, 0)

    # Link the Principled BSDF to the Output Material node
    links.new(principled_bsdf.outputs[0], output_node.inputs[0])

    # Get the texture file names
    # texture_directory = os.path.join(temp_folder, "pbr-pol")

    # the final file TwinBru sent doesn't have subfolders
    texture_directory = temp_folder
    texture_files = os.listdir(texture_directory)
    mapping_substrings = {
        "BASE": "Base Color",
        "MTL": "Metallic",
        "ROUGH": "Roughness",
        "ALPHA": "Alpha",
        "NRM": "Normal",
    }
    mapping_substrings = {
        "col": "Base Color",
        "met": "Metallic",
        "rough": "Roughness",
        "alpha": "Alpha",
        "nrm": "Normal",
    }
    index = 0
    texture_nodes = []
    for substring, mapping in mapping_substrings.items():
        for texture_file in texture_files:
            if substring + "." in texture_file:
                print(f"texture_file: {texture_file}")
                texture_path = os.path.join(texture_directory, texture_file)
                texture_node = nodes.new(type="ShaderNodeTexImage")
                texture_node.location = (
                    -2 * node_gap_x,
                    node_gap_y * 2 - index * node_gap_y,
                )
                texture_node.image = bpy.data.images.load(texture_path)
                # set anything besides color to non color
                if mapping != "Base Color":
                    texture_node.image.colorspace_settings.name = "Non-Color"
                # normal maps need a normal map node
                if mapping == "Normal":
                    normal_map = nodes.new(type="ShaderNodeNormalMap")
                    normal_map.location = (
                        -node_gap_x,
                        texture_node.location[1],
                    )
                    links.new(texture_node.outputs[0], normal_map.inputs["Color"])
                    links.new(normal_map.outputs[0], principled_bsdf.inputs[mapping])
                else:
                    links.new(texture_node.outputs[0], principled_bsdf.inputs[mapping])
                index += 1
                texture_nodes.append(texture_node)

    # Mark the material as asset for Belnder's asset manager
    material.asset_mark()
    material.asset_generate_preview()
    material.name = twinbru_asset["name"] # not sure why but this works here but not before.
    print(f"processed material {material.name}")
    # Pack all .blend textures
    bpy.ops.file.pack_all()
    # save the material
    bpy.ops.wm.save_as_mainfile(filepath=result_filepath)
