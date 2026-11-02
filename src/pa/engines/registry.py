from pa.common import PAError
from pa.plugins import discover


def get_engine(config, engine_id):
    entry = config.engines.engines.get(engine_id)
    if not entry or not entry.enabled:
        raise PAError("engine is missing or disabled", "not_found", 4)
    adapters = discover("pa.engines")
    if entry.adapter not in adapters:
        raise PAError("GUI execution driver is not available for unattended dispatch")
    return adapters[entry.adapter].load()(engine_id, entry)
