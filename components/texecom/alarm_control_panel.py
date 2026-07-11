import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import alarm_control_panel
from esphome.const import CONF_ID

from . import CONF_TEXECOM_ID, TexecomComponent, texecom_ns

DEPENDENCIES = ["texecom"]

CONF_AREA = "area"

TexecomAlarmPanel = texecom_ns.class_(
    "TexecomAlarmPanel", alarm_control_panel.AlarmControlPanel, cg.Component
)

CONFIG_SCHEMA = alarm_control_panel.alarm_control_panel_schema(TexecomAlarmPanel).extend(
    {
        cv.GenerateID(CONF_TEXECOM_ID): cv.use_id(TexecomComponent),
        cv.Optional(CONF_AREA, default=1): cv.int_range(min=1, max=8),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await alarm_control_panel.register_alarm_control_panel(var, config)
    hub = await cg.get_variable(config[CONF_TEXECOM_ID])
    cg.add(var.set_parent(hub))
    cg.add(var.set_area(config[CONF_AREA]))
    cg.add(hub.set_alarm_panel(var))
