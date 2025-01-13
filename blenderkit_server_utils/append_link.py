def append_material(file_name, matname=None, link=False, fake_user=True):
    """Append a material type asset
    
    Args:
        file_name (str): Path to the .blend file containing the material
        matname (str, optional): Name of material to append. If None, appends first found. 
        link (bool, optional): Link the material instead of appending. Defaults to False.
        fake_user (bool, optional): Set fake user on appended material. Defaults to True.
    
    Returns:
        bpy.types.Material: The appended/linked material or None if failed
    """
    import bpy
    
    mats_before = bpy.data.materials[:]
    try:
        with bpy.data.libraries.load(file_name, link=link, relative=True) as (
                data_from,
                data_to,
        ):
            found = False
            for m in data_from.materials:
                if matname is None or m == matname:
                    data_to.materials = [m]
                    found = True
                    break
            
            if not found:
                return None

        # Get the newly added material
        mats_after = bpy.data.materials[:]
        new_mats = [m for m in mats_after if m not in mats_before]
        
        if not new_mats:
            return None
            
        mat = new_mats[0]
        if fake_user:
            mat.use_fake_user = True
            
        return mat
        
    except Exception as e:
        print(f"Failed to append material: {e}")
        return None 

def link_collection(
    file_name: str,
    location=(0, 0, 0),
    rotation=(0, 0, 0),
    link=False,
    name=None,
    parent=None,
) -> tuple:
    """Link/append a collection from a blend file.
    
    Args:
        file_name: Path to the blend file
        location: Location for the collection (default: origin)
        rotation: Rotation for the collection (default: no rotation)
        link: True to link, False to append
        name: Name of collection to find (if None, uses first)
        parent: Parent object to parent collection to
        
    Returns:
        tuple: (main_object, all_objects)
            - main_object: The parent/main object of the collection
            - all_objects: List of all objects in the collection
    """
    import bpy

    # Store existing collections to find new ones
    collections_before = bpy.data.collections[:]
    objects_before = bpy.data.objects[:]

    # Link/append the collection
    with bpy.data.libraries.load(file_name, link=link) as (data_from, data_to):
        found = False
        for cname in data_from.collections:
            if name is None or cname == name:
                data_to.collections = [cname]
                found = True
                break
                
        if not found:
            print(f"Collection {name} not found in file {file_name}")
            return None, []

    # Find the newly added collection
    collections_after = bpy.data.collections[:]
    new_collections = [c for c in collections_after if c not in collections_before]
    if not new_collections:
        print("No new collections found after linking/appending")
        return None, []
        
    new_collection = new_collections[0]

    # Link the collection to the scene
    if new_collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(new_collection)

    # Get all objects from the collection
    all_objects = []
    for obj in new_collection.all_objects:
        all_objects.append(obj)
        if obj.parent is None:
            obj.location = location
            obj.rotation_euler = rotation
            if parent is not None:
                obj.parent = parent

    # Find main/parent object (first object without parent)
    main_object = None
    for obj in all_objects:
        if obj.parent is None:
            main_object = obj
            break

    return main_object, all_objects 