## Home Assistant alert blueprints

Requires `vps-lightsail-agent` publishing over MQTT discovery (entities `sensor.vps_lightsail_*`).

| Blueprint | Import |
|---|---|
| Server unreachable (+ recovery) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fguicheffer%2Fvps-lightsail%2Fblob%2Fmain%2Fhomeassistant%2Fblueprints%2Fautomation%2Fvps_lightsail%2Funreachable.yaml) |
| Data allowance threshold | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fguicheffer%2Fvps-lightsail%2Fblob%2Fmain%2Fhomeassistant%2Fblueprints%2Fautomation%2Fvps_lightsail%2Fdata_allowance.yaml) |
| Agent offline (optional) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fguicheffer%2Fvps-lightsail%2Fblob%2Fmain%2Fhomeassistant%2Fblueprints%2Fautomation%2Fvps_lightsail%2Fagent_offline.yaml) |

Manual install: copy `automation/vps_lightsail/` into `<config>/blueprints/automation/` and reload automations.

- Targets: pick mobile-app devices, notify entities, and/or a legacy notify action. All filled
  targets are notified.
- Data allowance: create **one automation per threshold** (e.g. 80 and 100). Each fires at most
  once per UTC month.
- Recovery notices are sent only if the alert was sent. A Home Assistant restart during an
  outage loses the pending recovery notice and may repeat the alert.
