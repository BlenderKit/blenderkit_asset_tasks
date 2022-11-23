# blenderkit_asset_tasks
Scripts to do automatic processing of assets in the database

by now needs a directory with all possible blender versions, look like this:
![image](https://user-images.githubusercontent.com/6907354/203579508-952ba12e-6a83-49dd-bca2-b3d33dd1ad36.png)

and is currently run from a .bat file with a .bat command like this:


:: set BLENDERKIT_SERVER https://www.blenderkit.com
:: set BLENDERKIT_API_KEY avcdasdfaienlksdfoasih

%~dp0blender_processors\2.93.8\2.93\python\bin\python.exe %~dp0blenderkit_server_tools\generate_resolutions_update.py
