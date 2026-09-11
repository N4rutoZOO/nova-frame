import app_v6_ui_runtime as ui

app = ui.app
core = ui.core
worker = ui.worker
stable = worker.stable

_original_authish = stable._authish
_original_friendly_error = core.friendly_error


def _authish(text: str) -> bool:
    low = (text or "").lower()
    legacy_markers = (
        "authentification youtube requise",
        "youtube-cookies",
        "mets à jour le secret youtube-cookies",
        "mets a jour le secret youtube-cookies",
        "mets à jour les cookies youtube",
        "mets a jour les cookies youtube",
        "cookies youtube",
        "cette vidéo nécessite une session youtube valide",
        "cette video necessite une session youtube valide",
    )
    return _original_authish(text) or any(marker in low for marker in legacy_markers)


def _friendly_error(exc):
    text = str(exc)
    if _authish(text):
        # Never instruct the UI to refresh the legacy Cloud Run cookie secret.
        # V6.7+ must fall back to the authenticated Chromium worker instead.
        return "Session YouTube directe indisponible · bascule automatique vers le worker authentifié."
    return _original_friendly_error(exc)


stable._authish = _authish
core.friendly_error = _friendly_error

# Keep the version visible in /health and make it explicit that the legacy cookie
# error path has been disabled.
worker.VERSION = "6.8-ui-authfix"
