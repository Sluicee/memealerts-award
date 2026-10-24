MemeAlerts Award
===============

Windows: extract the entire ZIP and run MemeAlertsAward/MemeAlertsAward.exe.
Linux: extract the entire archive and run MemeAlertsAward/MemeAlertsAward.
macOS: extract the archive and move MemeAlertsAward.app to Applications.

Keep the bundle contents together. Python does not need to be installed.
Linux needs a graphical desktop and system OpenGL, X11/Wayland and D-Bus libraries.
Tray availability depends on your desktop environment.

This build is not code-signed. Windows/macOS may ask you to confirm its origin.

Connect the broadcaster's Twitch account and paste the MemeAlerts accessToken
on the Connections screen. Tokens may be saved to the operating system keychain.
Closing the window hides it in the tray. Exit from the app or tray menu to stop
collection and disable the Twitch reward. An unexpected shutdown or network
failure may leave the reward enabled: check it in the Twitch dashboard.

The --self-test argument runs an offline check using temporary data.
