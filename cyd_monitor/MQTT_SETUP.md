# MQTT credential for the CYD battery monitor

The firmware authenticates to Home Assistant's **Mosquitto broker** with a
dedicated account (better than anonymous — scoped, revocable):

| | |
|---|---|
| **Broker** | `homeassistant.local:1883` (HA Mosquitto add-on) |
| **Username** | `humsienk_cyd` |
| **Password** | `CHANGE_ME_MQTT_PASSWORD` |

These are set in `src/config.h`.

## Create this user in Mosquitto (one-time)

In Home Assistant → **Settings → Add-ons → Mosquitto broker → Configuration**,
add a dedicated login and restart the add-on:

```yaml
logins:
  - username: humsienk_cyd
    password: CHANGE_ME_MQTT_PASSWORD
```

(Alternatively, create a normal HA user with the same name/password — the add-on
authenticates HA users too. The `logins:` block above is the cleaner, dedicated way.)

## Prefer anonymous instead?

If you'd rather not create a user right now, set both `MQTT_USER` and
`MQTT_PASSWORD` to `""` in `config.h` and enable anonymous access in the add-on
(`customize: active: true` + an ACL, or the add-on's anonymous option). Not
recommended for anything long-term.

## ⚠️ Network reachability (worth reading)

The CYD has to reach the broker over WiFi, and the **ESP32 is 2.4 GHz only**. Two things trip
people up:

- **2.4 vs 5 GHz.** Some routers — UniFi especially — won't let a device on 2.4 GHz talk to one on
  5 GHz. If your broker / Home Assistant is only on the 5 GHz network, a 2.4 GHz-only ESP32 can't
  reach it. The easy fix (and good practice anyway) is a **dedicated 2.4 GHz WiFi network — its own
  SSID — for your IoT gear**.
- **Locked-down networks.** If that IoT WiFi is walled off from the rest of your house, make sure it
  can still **reach the broker** (`homeassistant.local:1883`). A guest/isolated WiFi often can't, and
  MQTT then fails silently while the screen and BLE keep working.

If MQTT never connects, the serial log sits on `[mqtt] connecting…`. Put the CYD on a WiFi network
(and band) that can actually reach the broker.
