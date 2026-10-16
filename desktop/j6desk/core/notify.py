"""Windows toasts, with no packaged dependency.

Windows' own notification API is reachable from PowerShell, so the toast is built as a scrap of
XML and handed to a one-shot `powershell.exe`. It costs about 200 ms, needs nothing installed,
and the same call works whether the message arrived in the window or in the terminal.

Everything here fails quietly: a missing notification is a nuisance, not a reason for the chat
to stop working.
"""
import subprocess
import sys
from xml.sax.saxutils import escape

APP_ID = 'Microsoft.Windows.Explorer'      # a registered AppID, so the toast is allowed to show

SCRIPT = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
{payload}
"@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}").Show($toast)
"""


def _toast_xml(title, body):
    return (
        '<toast activationType="protocol"><visual><binding template="ToastGeneric">'
        f'<text>{escape(title)}</text><text>{escape(body)}</text>'
        '</binding></visual></toast>'
    )


def available():
    return sys.platform == 'win32'


def toast(title, body, app_id=APP_ID):
    """Raise one notification. Returns True if Windows took it."""
    if not available():
        return False
    script = SCRIPT.format(payload=_toast_xml(title, body), app_id=app_id)
    try:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, timeout=10, creationflags=flags)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def bell():
    """The console's own nudge, for a terminal with no desktop to toast onto."""
    try:
        sys.stdout.write('\a')
        sys.stdout.flush()
    except Exception:
        pass
