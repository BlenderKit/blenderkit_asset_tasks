# blenderkit_asset_tasks
Scripts to do automatic processing of assets in the database

by now needs a directory with all possible blender versions, look like this:  
![image](https://user-images.githubusercontent.com/6907354/203579508-952ba12e-6a83-49dd-bca2-b3d33dd1ad36.png)

generate resolutions is currently run from a .bat file with a .bat command like this:

:: server to use  
set BLENDERKIT_SERVER https://www.blenderkit.com  
:: api key  
set BLENDERKIT_API_KEY avcdasdfaienlksdfoasih  
:: blenders folder, see above  
set BLENDERS_PATH=F:\blender_processors  
:: asset id, if not submitted, uses default search for resolutions  
set BLENDERKIT_RESOLUTIONS_SEARCH_ID=3e34afef-31e6-4729-af9c-c181950640ad  
:: check if asset needs resolutions - doesn't reupload for assets that already have resolutions files.  
set BLENDERKIT_CHECK_NEEDS_RESOLUTION=0  
%~dp0blender_processors\2.93.8\2.93\python\bin\python.exe %~dp0blenderkit_server_tools\generate_resolutions_update.py
