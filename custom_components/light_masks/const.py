"""Light Masks constants."""

DOMAIN = "light_masks"
VERSION = "0.1.0"
STORE_VERSION = 1
PLATFORMS = ["light", "switch", "sensor", "button"]
SIGNAL = f"{DOMAIN}_updated"
SUPPORTED_MODES = {"onoff", "brightness", "color_temp", "xy", "hs", "rgb"}
