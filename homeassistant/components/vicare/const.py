"""Constants for the ViCare integration."""

from homeassistant.const import Platform

DOMAIN = "vicare"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

UNSUPPORTED_DEVICES = [
    "Heatbox1",
    "Heatbox2_SRC",
    "E3_TCU10_x07",
    "E3_TCU41_x04",
    "E3_RoomControl_One_522",
]

VICARE_NAME = "ViCare"
VICARE_TOKEN_FILENAME = "vicare_token.save"

VIESSMANN_DEVELOPER_PORTAL = "https://app.developer.viessmann-climatesolutions.com"

CONF_CIRCUIT = "circuit"

DEFAULT_CACHE_DURATION = 60

VICARE_BAR = "bar"
VICARE_CELSIUS = "celsius"
VICARE_CUBIC_METER = "cubicMeter"
VICARE_KW = "kilowatt"
VICARE_KWH = "kilowattHour"
VICARE_PERCENT = "percent"
VICARE_W = "watt"
VICARE_WH = "wattHour"

CONF_CIRCULATION_BOOST_DURATION = "circulation_boost_duration"
DEFAULT_CIRCULATION_BOOST_DURATION = 10  # minutes

# Internal config-entry options used to persist boost state across restarts.
# These are not user-configurable.
CONF_BOOST_END_TIME = "_boost_end_time"
CONF_BOOST_ORIGINAL_SCHEDULE = "_boost_original_schedule"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
