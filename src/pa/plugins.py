from importlib.metadata import entry_points

GROUPS = (
    "pa.ingress",
    "pa.engines",
    "pa.tools",
    "pa.scm",
    "pa.notifiers",
    "pa.secrets",
    "pa.hooks",
    "pa.sandbox",
    "pa.effects",
)


def discover(group):
    if group not in GROUPS:
        raise ValueError(f"unknown plugin group: {group}")
    entries = entry_points(group=group)
    result = {}
    for entry in entries:
        if entry.name in result:
            raise ValueError(f"duplicate plugin: {group}/{entry.name}")
        result[entry.name] = entry
    return result
