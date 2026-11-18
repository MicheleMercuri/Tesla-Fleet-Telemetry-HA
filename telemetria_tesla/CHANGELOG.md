# Changelog

## 0.10.0-5 (beta)

- Battery power: new entities `tensione_batteria` (V), `corrente_batteria` (A) and the derived `potenza_batteria` (kW,
  voltage times current: driving, regenerative braking and charging, with the sign of the current sent by the car).
  They need `PackVoltage` and `PackCurrent` in the car configuration: run `tools/configura_auto.py` again.
- `tools/configura_auto.py`: `Location` every 15 s instead of 60 (arrival home recognised sooner), `PackVoltage` and
  `PackCurrent` every 10 s.
- New option `registro` (default on): on-screen alerts, returns and telemetry errors in the Home Assistant logbook.
  Turn it off only if another part of Home Assistant already writes them.

## 0.10.0-4 (beta)

- "At home" / "away from home" now comes first from the GPS position inside Home Assistant's home zone. The car's own
  flag (`LocatedAtHome`) is used only when the position or the zone is not known: it can stay `true` for hours with the
  car kilometres away, and then Sentry alerts away from home were recorded as "at home" and not notified.
- A null or 0,0 position from the car no longer hides the last valid one.
- Charging losses with a wallbox meter: the session counts as "at home" if the car was at home at the start or at the
  end (the position arrives every 60 s, so plugging in right after arriving could still see the old position).
- Dashboard: the position row uses the GPS zone first, then the car's flag. The legacy YAML package rule does the same.

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
