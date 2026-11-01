# Paperless MCP Admin für Home Assistant

Diese App ist für die Verwaltung von Paperless-Konfigurationssammlungen vorgesehen.
Der Token wird nur über die App-Konfiguration gesetzt und nicht im Quellcode gespeichert.

## Installation

1. Den gesamten Ordner `paperless-mcp-admin` nach `/addons/local/` kopieren.
2. In Home Assistant **Einstellungen → Apps → App-Repositories aktualisieren** öffnen.
3. **Paperless MCP Admin** installieren.
4. In der App-Konfiguration `paperless_url`, den neuen Paperless-API-Token und einen separaten `mcp_token` eintragen.
5. Zuerst mit `read_only: true` starten und die Logs prüfen.
6. Vor Schreibtests ein aktuelles vollständiges Home-Assistant-Backup erstellen.
7. Erst danach `read_only: false` aktivieren.

Schreibwerkzeuge liefern ohne `confirm=true` nur eine Vorschau.
