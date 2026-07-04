from .shareworks import ShareworksProvider
from .manual     import ManualProvider

# Add new provider types here — key must match "type" in config.json
REGISTRY = {
    "shareworks": ShareworksProvider,
    "manual":     ManualProvider,
}


def load_providers(config: dict) -> list:
    """Instantiate all configured providers from config['providers'] list."""
    providers = []
    for entry in config.get("providers", []):
        ptype = entry.get("type")
        cls = REGISTRY.get(ptype)
        if cls is None:
            raise ValueError(f"Unknown provider type '{ptype}'. "
                             f"Available: {list(REGISTRY)}")
        providers.append(cls(entry))
    return providers
