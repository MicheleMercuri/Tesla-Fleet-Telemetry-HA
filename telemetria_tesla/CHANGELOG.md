# Changelog

## 0.10.0-3 (beta)

- Entities created by the add-on itself (MQTT discovery): no YAML package and no AppDaemon app to install.
- Derived values computed in the add-on: doors and windows open, navigation home, distance of the destination from home
  (always in km, from Home Assistant's home zone), last Sentry alarm and alert, last on-screen alert, 12 V battery, last
  telemetry error. They become unavailable if the scanner stops.
- Notifications through any Home Assistant service: Sentry alarm and alert away from home, new key paired, translated
  on-screen alerts, configuration expiry, telemetry stopped. On-screen alerts and telemetry errors in the logbook.
- Battery capacity measured at each charge and Sentry count since parking (options).
- Server runs as `nobody`; its log drops normal disconnections and shows only the last 4 digits of the VIN.
- Requires Home Assistant 2025.10 or later.

## 0.10.0-2 (draft)

- Tesla's fleet-telemetry v0.10.0 server, MQTT package generated on the PC, optional AppDaemon app for the alerts.
