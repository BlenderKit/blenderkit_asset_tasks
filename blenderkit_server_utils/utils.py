import platform

try:
    import bpy
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