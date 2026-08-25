from __future__ import annotations

# Scan UI rendering remains intentionally separate from scanner logic.
# Rejection details are supplied by scan_diagnostics and rendered by feature_handlers.


def install() -> None:
    """Compatibility hook used by app.__init__ before main imports handlers.

    The scan UI is now rendered directly by the feature handlers, so there is
    no monkey-patching to install. Keeping this explicit no-op preserves the
    package import contract and prevents startup failures in existing images.
    """
    return None
