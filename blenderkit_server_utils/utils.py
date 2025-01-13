import platform

bpy = None
try:
    import bpy
    from mathutils import Vector

except:
    print('bpy not present')


def get_headers(api_key):
    headers = {
        "accept": "application/json",
        "Platform-Version": platform.platform(),
    }
    if api_key != '':
        headers["Authorization"] = "Bearer %s" % api_key
    return headers

def activate_object(aob):
    # this deselects everything, selects the object and makes it active
    for obj in bpy.context.visible_objects:
        obj.select_set(False)
    aob.select_set(True)
    bpy.context.view_layer.objects.active = aob

def selection_get():
    aob = bpy.context.view_layer.objects.active
    selobs = bpy.context.view_layer.objects.selected[:]
    return (aob, selobs)


def selection_set(sel):
    bpy.ops.object.select_all(action="DESELECT")
    try:
        bpy.context.view_layer.objects.active = sel[0]
        for ob in sel[1]:
            ob.select_set(True)
    except Exception as e:
        print("Selectible objects not found")
        print(e)


def get_param(asset_data, parameter_name, default=None):
    if not asset_data.get("dictParameters"):
        # this can appear in older version files.
        return default

    return asset_data["dictParameters"].get(parameter_name, default)

    # for p in asset_data['parameters']:
    #     if p.get('parameterType') == parameter_name:
    #         return p['value']
    # return default


def dict_to_params(inputs, parameters=None):
    if parameters == None:
        parameters = []
    for k in inputs.keys():
        if type(inputs[k]) == list:
            strlist = ""
            for idx, s in enumerate(inputs[k]):
                strlist += s
                if idx < len(inputs[k]) - 1:
                    strlist += ','

            value = "%s" % strlist
        elif type(inputs[k]) != bool:
            value = inputs[k]
        else:
            value = str(inputs[k])
        parameters.append(
            {
                "parameterType": k,
                "value": value
            })
    return parameters

def enable_cycles_CUDA():
  preferences = bpy.context.preferences
  cycles_preferences = preferences.addons['cycles'].preferences

  cycles_preferences.compute_device_type = 'CUDA'
  if cycles_preferences.compute_device_type == 'CUDA':
      print("CUDA is enabled for rendering.")
      # Additional code for GPU rendering
  elif cycles_preferences.compute_device_type == 'OPTIX':
      print("OPTIX is enabled for rendering.")
      # Additional code for GPU rendering
  else:
      print("GPU rendering is not enabled.")
      # Additional code for CPU rendering

### moved all from blenderkit/utils.py to here
# only if bpy is present
if bpy:
    def scale_2d(v, s, p):
        """scale a 2d vector with a pivot"""
        return (p[0] + s[0] * (v[0] - p[0]), p[1] + s[1] * (v[1] - p[1]))


    def scale_uvs(ob, scale=1.0, pivot=Vector((0.5, 0.5))):
        mesh = ob.data
        if len(mesh.uv_layers) > 0:
            uv = mesh.uv_layers[mesh.uv_layers.active_index]

            # Scale a UV map iterating over its coordinates to a given scale and with a pivot point
            for uvindex in range(len(uv.data)):
                uv.data[uvindex].uv = scale_2d(uv.data[uvindex].uv, scale, pivot)


    # map uv cubic and switch of auto tex space and set it to 1,1,1
    def automap(
        target_object=None,
        target_slot=None,
        tex_size=1,
        bg_exception=False,
        just_scale=False,
    ):
        tob = bpy.data.objects[target_object]
        # only automap mesh models
        if tob.type == "MESH" and len(tob.data.polygons) > 0:
            # check polycount for a rare case where no polys are in editmesh
            actob = bpy.context.active_object
            bpy.context.view_layer.objects.active = tob

            # auto tex space
            if tob.data.use_auto_texspace:
                tob.data.use_auto_texspace = False

            if not just_scale:
                tob.data.texspace_size = (1, 1, 1)

            if "automap" not in tob.data.uv_layers:
                bpy.ops.mesh.uv_texture_add()
                uvl = tob.data.uv_layers[-1]
                uvl.name = "automap"

            tob.data.uv_layers.active = tob.data.uv_layers["automap"]
            tob.data.uv_layers["automap"].active_render = True

            # TODO limit this to active material
            # tob.data.uv_textures['automap'].active = True

            scale = tob.scale.copy()

            if target_slot is not None:
                tob.active_material_index = target_slot
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="DESELECT")

            # this exception is just for a 2.8 background thunmbnailer crash, can be removed when material slot select works...
            if bg_exception or len(tob.material_slots) == 0:
                bpy.ops.mesh.select_all(action="SELECT")
            else:
                bpy.ops.object.material_slot_select()

            scale = (scale.x + scale.y + scale.z) / 3.0

            if (
                tex_size == 0
            ):  # prevent division by zero, it's possible to have 0 in tex size by unskilled uploaders
                tex_size = 1

            if not just_scale:
                # compensate for the undocumented operator change in blender 3.2
                if bpy.app.version >= (3, 2, 0):
                    cube_size = (tex_size) / scale
                else:
                    cube_size = (
                        scale * 2.0 / (tex_size)
                    )  # it's * 2.0 because blender can't tell size of a unit cube :)

                bpy.ops.uv.cube_project(cube_size=cube_size, correct_aspect=False)

            bpy.ops.object.editmode_toggle()
            # this by now works only for thumbnail preview, but should be extended to work on arbitrary objects.
            # by now, it takes the basic uv map = 1 meter. also, it now doeasn't respect more materials on one object,
            # it just scales whole UV.
            if just_scale:
                scale_uvs(tob, scale=Vector((1 / tex_size, 1 / tex_size)))
            bpy.context.view_layer.objects.active = actob

def get_bounds_worldspace(objects):
    """Get the bounding box of objects in world space.
    
    Args:
        objects: List of Blender objects
        
    Returns:
        tuple: (minx, miny, minz, maxx, maxy, maxz)
    """
    minx = miny = minz = float('inf')
    maxx = maxy = maxz = -float('inf')
    
    for obj in objects:
        # Skip objects that shouldn't be included in bounds
        if obj.type == 'EMPTY' and not obj.instance_collection:
            continue
            
        # Get object's world matrix
        matrix_world = obj.matrix_world
        
        if obj.type == 'MESH':
            # For mesh objects, use all vertices
            for v in obj.data.vertices:
                world_coord = matrix_world @ v.co
                minx = min(minx, world_coord.x)
                miny = min(miny, world_coord.y)
                minz = min(minz, world_coord.z)
                maxx = max(maxx, world_coord.x)
                maxy = max(maxy, world_coord.y)
                maxz = max(maxz, world_coord.z)
        else:
            # For non-mesh objects, use object location
            world_coord = matrix_world.translation
            minx = min(minx, world_coord.x)
            miny = min(miny, world_coord.y)
            minz = min(minz, world_coord.z)
            maxx = max(maxx, world_coord.x)
            maxy = max(maxy, world_coord.y)
            maxz = max(maxz, world_coord.z)
            
    if minx == float('inf'):
        # No valid objects found, return zero bounds
        return 0, 0, 0, 0, 0, 0
        
    return minx, miny, minz, maxx, maxy, maxz
