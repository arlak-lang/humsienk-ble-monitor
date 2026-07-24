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

## ⚠️ Network reachability

The CYD needs to reach the broker over WiFi. If you put IoT devices on a **separate / guest /
VLAN network**, make sure that network can actually **route to the broker**
(`homeassistant.local:1883`) — isolated IoT VLANs often can't, and MQTT will silently fail while
the screen and BLE keep working. The serial log shows `[mqtt] connecting…` but never connects if
it's blocked; put the CYD on a network that can reach the broker.
