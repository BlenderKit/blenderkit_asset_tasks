import platform

def get_headers(api_key):
  headers = {
    "accept": "application/json",
    "Platform-Version": platform.platform(),
  }
  if api_key != '':
    headers["Authorization"] = "Bearer %s" % api_key
  return headers