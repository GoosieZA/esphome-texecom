import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import uart
from esphome.const import CONF_ID

CODEOWNERS = ["@GoosieZA"]
DEPENDENCIES = ["uart"]
AUTO_LOAD = ["binary_sensor", "alarm_control_panel"]
MULTI_CONF = True

texecom_ns = cg.esphome_ns.namespace("texecom")
TexecomComponent = texecom_ns.class_("TexecomComponent", cg.Component, uart.UARTDevice)

CONF_TEXECOM_ID = "texecom_id"
CONF_UDL = "udl"
CONF_ZONE_STATUS_ADDRESS = "zone_status_address"
CONF_ZONE_COUNT = "zone_count"
CONF_ARM_STATE_ADDRESS = "arm_state_address"
CONF_DISARMED_VALUE = "disarmed_value"
CONF_POLL_INTERVAL = "poll_interval"

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(TexecomComponent),
            # UDL passcode as plain digits (e.g. "1234"); sent as raw values.
            cv.Required(CONF_UDL): cv.string,
            # Verified for this Premier 412 v11.13; adjust if yours differs.
            cv.Optional(CONF_ZONE_STATUS_ADDRESS, default=0x0004F8): cv.hex_int,
            cv.Optional(CONF_ZONE_COUNT, default=16): cv.int_range(min=1, max=240),
            cv.Optional(CONF_ARM_STATE_ADDRESS, default=0x0000E4): cv.hex_int,
            cv.Optional(CONF_DISARMED_VALUE, default=0xE0): cv.hex_uint8_t,
            cv.Optional(
                CONF_POLL_INTERVAL, default="1s"
            ): cv.positive_time_period_milliseconds,
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(uart.UART_DEVICE_SCHEMA)
)

FINAL_VALIDATE_SCHEMA = uart.final_validate_device_schema(
    "texecom",
    baud_rate=19200,
    require_tx=True,
    require_rx=True,
    stop_bits=2,
    data_bits=8,
    parity="NONE",
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)
    cg.add(var.set_udl(config[CONF_UDL]))
    cg.add(var.set_zone_status_address(config[CONF_ZONE_STATUS_ADDRESS]))
    cg.add(var.set_zone_count(config[CONF_ZONE_COUNT]))
    cg.add(var.set_arm_state_address(config[CONF_ARM_STATE_ADDRESS]))
    cg.add(var.set_disarmed_value(config[CONF_DISARMED_VALUE]))
    cg.add(var.set_poll_interval(config[CONF_POLL_INTERVAL]))
