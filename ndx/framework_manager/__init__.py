"""Framework registry: frameworks/libraries arrive through plugins of category
'framework'/'library'. This module lists what installed plugins provide."""

def installed_frameworks(package_manager):
    return {pid: e for pid, e in package_manager.list_installed().items()
            if e.get("category") in ("framework", "library", "gui-toolkit")}
