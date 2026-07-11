import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor

from . import CONF_TEXECOM_ID, TexecomComponent, texecom_ns

DEPENDENCIES = ["texecom"]

CONF_ZONE = "zone"
CONF_TYPE = "type"

TexecomZone = texecom_ns.class_("TexecomZone", binary_sensor.BinarySensor)


def _exactly_one(config):
    if (CONF_ZONE in config) == (CONF_TYPE in config):
        raise cv.Invalid("specify exactly one of 'zone:' or 'type: armed'")
    return config


CONFIG_SCHEMA = cv.All(
    binary_sensor.binary_sensor_schema(TexecomZone).extend(
        {
            cv.GenerateID(CONF_TEXECOM_ID): cv.use_id(TexecomComponent),
            # a panel zone number, OR the system arm state
            cv.Optional(CONF_ZONE): cv.int_range(min=1, max=240),
            cv.Optional(CONF_TYPE): cv.one_of("armed", lower=True),
        }
    ),
    _exactly_one,
)


async def to_code(config):
    var = await binary_sensor.new_binary_sensor(config)
    hub = await cg.get_variable(config[CONF_TEXECOM_ID])
    if CONF_ZONE in config:
        cg.add(var.set_zone_number(config[CONF_ZONE]))
        cg.add(hub.register_zone(var))
    else:
        cg.add(hub.set_armed_sensor(var))
