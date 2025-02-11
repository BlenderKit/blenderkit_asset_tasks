# blenderkit_asset_tasks
Scripts to do automatic processing of Blender assets in the database.

## Structure

- `.github` - contains definitions of Github Actions Workflows.
- `blend_files` - contains template .blend projects through which some tasks are made.
- `blender_bg_scripts` - contains scripts which gets executed inside the Blender instance.
- `blender_server_utils` - python module containing code shared between multiple scripts in root dir.
- `./` - root of the project contains standalone scripts to do the job.

Scripts in the root are standalone scripts which does, prefferably one, task.
They can import from `blender_server_utils`, but should not import from one another.
If some code is to be shared, it should be placed in `blender_server_utils`.
Standalone scripts in root often need do some stuff right inside Blender.
For this they should start Blender with some script from `blender_bg_utils`.
All code which has to be run inside Blender should be in `blender_bg_utils`.

### Requirements for Blender

Assets tasks have to start a Blender to make the job and the questions is which version?
Basically there are 2 types of scripts in this repo:
- requiring multiple versions of Blender (we want to target closest to original)
- requiring one target version of Blender (we know the version in advance, or we are ok with just one version, preferebly latest)

#### Multiple versions
There are tasks where we need the asset to be processed in the same Blender version as original to keep compatibility with as old Blender as possible.
Like in case of generating resolutions which will be later imported by users into their Blenders.
Script automatically detects closest Blender to use.

We define the path `BLENDERS_PATH` to directory containing multiple installations of Blender.
Each version of the Blender should be placed in directory named `X.Y` so the scripts can detect it automatically:
![image](https://user-images.githubusercontent.com/6907354/203579508-952ba12e-6a83-49dd-bca2-b3d33dd1ad36.png)

For example:

```
BLENDERS_PATH="/Users/ag/blenders"
ls /Users/ag/blenders
2.93
3.0
3.1
4.0
4.1
```

NOTE: On MacOS you will need to create a symbolic links.

For multiple versions scripts you can use docker image `blenderkit/headless-blender:multi-version`.
It has the blenders directory at: `/home/headless/blenders`.

#### Single versions
There are tasks in which we do not care about compatibility with older Blenders.
Latest Blender brings better stability and performance in these cases.
Like in case of generating GLTFs or renders which does not get imported back to users' Blenders.
Scripts will just use the specified path directly to Blender executable defined by `BLENDER_PATH`.

For example: `BLENDER_PATH=/Applications/Blender420/Contents/MacOS/Blender`

For single versions scripts you can use docker image `blenderkit/headless-blender:blender-x.y`.
It has the blender executable placed at: `/home/headless/blender/blender`.

## CI

## Developing

### Trigger job via webhook

Webhook can be tested with this curl command:

```
curl -X POST -H "Accept: application/vnd.github.v3+json" \
     -H "Authorization: token <TOKEN>" \
     https://api.github.com/repos/blenderkit/blenderkit_asset_tasks/actions/workflows/webhook_process_asset.yml/dispatches \
     -d '{
       "ref": "main",
       "inputs": {
         "asset_base_id": "eda7948f-a0a5-457a-b0a0-c4031bed093d",
         "asset_type": "model",
         "verification_status": "validated",
         "is_private": true,
         "source_app_version_xy": "4.3",
       }
     }'
```
