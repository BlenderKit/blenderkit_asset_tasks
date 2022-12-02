import platform

def get_headers(api_key):
  headers = {
    "accept": "application/json",
    "Platform-Version": platform.platform(),
  }
  if api_key != '':
    headers["Authorization"] = "Bearer %s" % api_key
  return headers


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