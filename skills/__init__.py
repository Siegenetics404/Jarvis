def tool(name, description, params=None, optional=(), enums=None):
    """Build the schema the model sees.
    params = {argument name: description}, enums = {argument name: [allowed values]}."""
    params = params or {}
    enums = enums or {}
    properties = {}
    for key, text in params.items():
        properties[key] = {"type": "string", "description": text}
        if key in enums:
            properties[key]["enum"] = enums[key]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": [k for k in params if k not in optional],
            },
        },
    }