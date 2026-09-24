# Test the function definition in context
_toolkit = None

def tool_catalog_watch(
    interval: float = 1.0,
    debounce: float = 0.5,
    max_iterations: int = 0,
) -> dict[str, Any]:
    """Watch for catalog changes and auto-refresh.
    
    Uses filesystem watching to detect changes in tool directories and
    automatically re-index the catalog.
    """
    if not _toolkit:
        return {"error": "Toolkit not available"}

    watch_paths = getattr(_toolkit, 'projects', None)
    watch_paths = watch_paths.roots if watch_paths else []
    if not watch_paths:
        return {"error": "No watch paths configured"}
    
    try:
        import watchfiles
        return _watch_watchfiles(watch_paths, debounce, max_iterations)
    except ImportError:
        return _watch_polling(watch_paths, interval, debounce, max_iterations)

print("Exec OK")