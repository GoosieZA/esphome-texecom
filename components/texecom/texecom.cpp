#include "texecom.h"

#include "esphome/core/log.h"

namespace esphome {
namespace texecom {

static const char *const TAG = "texecom";

static const uint8_t CMD_LOGIN = 0x5A;          // 'Z'
static const uint8_t CMD_READ_VOLATILE = 0x52;  // 'R'  live/RAM read
static const uint8_t CMD_NAK = 0x0F;

void TexecomComponent::setup() {
  this->state_ = State::INIT;
  this->awaiting_response_ = false;
}

void TexecomComponent::loop() {
  this->read_serial_();
  const uint32_t now = millis();

  if (this->awaiting_response_) {
    if (now - this->last_tx_ > this->response_timeout_) {
      ESP_LOGW(TAG, "No response in %ums, restarting session", this->response_timeout_);
      this->state_ = State::INIT;
      this->poll_ = Poll::IDLE;
      this->awaiting_response_ = false;
    }
    return;
  }
  if (now - this->last_tx_ < this->command_delay_)
    return;

  switch (this->state_) {
    case State::INIT:
      this->send_command_(CMD_LOGIN, {});  // hello -> panel serial
      this->state_ = State::HELLO_SENT;
      break;
    case State::HELLO_ACKED:
      this->send_command_(CMD_LOGIN, this->passcode_bytes_());  // passcode -> banner
      this->state_ = State::PASS_SENT;
      break;
    case State::LOGGED_IN:
      if (this->poll_ == Poll::IDLE && this->have_action_) {
        // arm/disarm takes priority over the status poll
        ESP_LOGD(TAG, "Sending action cmd=0x%02X area=%u", this->action_command_, this->action_area_);
        this->send_command_(this->action_command_, {this->action_area_});
        this->poll_ = Poll::ACTION;
      } else if (this->poll_ == Poll::IDLE && now - this->last_poll_ >= this->poll_interval_) {
        this->send_read_(this->zone_status_address_, this->zone_count_);
        this->poll_ = Poll::ZONES;
      } else if (this->poll_ == Poll::SEND_ARM) {
        this->send_read_(this->arm_state_address_, 1);
        this->poll_ = Poll::ARM;
      }
      break;
    default:
      break;  // HELLO_SENT / PASS_SENT / ZONES / ARM wait for a reply
  }
}

void TexecomComponent::read_serial_() {
  while (this->available()) {
    uint8_t c;
    this->read_byte(&c);
    this->rx_buffer_.push_back(c);
    uint8_t len = this->rx_buffer_[0];
    if (len < 3 || len > 250) {  // resync on a bad length byte
      this->rx_buffer_.erase(this->rx_buffer_.begin());
      continue;
    }
    if (this->rx_buffer_.size() >= len) {
      std::vector<uint8_t> pkt(this->rx_buffer_.begin(), this->rx_buffer_.begin() + len);
      this->rx_buffer_.erase(this->rx_buffer_.begin(), this->rx_buffer_.begin() + len);
      this->process_packet_(pkt);
    }
  }
}

void TexecomComponent::process_packet_(const std::vector<uint8_t> &pkt) {
  ESP_LOGV(TAG, "RX: %s", to_hex_(pkt).c_str());
  std::vector<uint8_t> body(pkt.begin(), pkt.end() - 1);
  if (checksum_(body) != pkt.back())
    return;  // bad checksum, drop

  this->awaiting_response_ = false;
  uint8_t cmd = pkt[1];

  switch (this->state_) {
    case State::HELLO_SENT:
      this->state_ = State::HELLO_ACKED;
      break;
    case State::PASS_SENT:
      // A real login returns the panel model as a 0x5A banner; a short reply
      // means the passcode was not accepted (wrong digits / not raw values).
      if (cmd != CMD_LOGIN || pkt[0] <= 3) {
        ESP_LOGW(TAG, "Login not accepted - check the UDL passcode digits");
        this->state_ = State::INIT;
        break;
      }
      ESP_LOGI(TAG, "Logged in: %s",
               std::string(pkt.begin() + 2, pkt.end() - 1).c_str());
      this->state_ = State::LOGGED_IN;
      this->poll_ = Poll::IDLE;
      this->last_poll_ = 0;  // poll immediately
      break;
    case State::LOGGED_IN: {
      if (this->poll_ == Poll::ACTION) {
        // arm/disarm command was acknowledged; re-poll immediately to reflect it
        ESP_LOGV(TAG, "Action reply: %s", to_hex_(pkt).c_str());
        this->have_action_ = false;
        this->poll_ = Poll::IDLE;
        this->last_poll_ = 0;
        break;
      }
      // Dispatch strictly by the address the panel echoes back, not by poll
      // order. Zone and arm reads share the same command/response bytes, so a
      // mis-paired reply would otherwise be read as the wrong thing - a zone's
      // 0x00 "secure" byte landing in the arm slot reads as "armed", which is
      // what made the panel flap armed<->disarmed on every poll.
      const bool usable = pkt.size() >= 7 && pkt[1] != CMD_NAK;
      const uint32_t addr = ((uint32_t) pkt[2] << 16) | ((uint32_t) pkt[3] << 8) | pkt[4];
      if (usable && addr == this->zone_status_address_) {
        this->handle_zone_data_(pkt);
        this->poll_ = Poll::SEND_ARM;
      } else if (usable && addr == this->arm_state_address_) {
        this->handle_arm_data_(pkt);
        this->poll_ = Poll::IDLE;
        this->last_poll_ = millis();
      } else {
        // Unexpected, NAK or unsolicited frame: publish nothing, but keep the
        // poll cycle moving so we never wedge waiting on a reply that matched.
        ESP_LOGD(TAG, "Ignoring reply @0x%06X (poll=%d)", addr, (int) this->poll_);
        if (this->poll_ == Poll::ZONES) {
          this->poll_ = Poll::SEND_ARM;
        } else if (this->poll_ == Poll::ARM) {
          this->poll_ = Poll::IDLE;
          this->last_poll_ = millis();
        }
      }
      break;
    }
    default:
      break;
  }
}

void TexecomComponent::handle_zone_data_(const std::vector<uint8_t> &pkt) {
  // reply: [len][cmd][addr_hi addr_mid addr_lo][size][data...][chk]
  if (pkt.size() < 7 || pkt[1] == CMD_NAK)
    return;
  const uint8_t *data = &pkt[6];
  uint8_t count = (uint8_t) (pkt.size() - 7);
  for (auto *z : this->zones_) {
    uint8_t idx = z->get_zone_number() - 1;
    if (idx < count)
      z->publish_state(data[idx] != 0x00);  // 0x00 secure, 0x01 active
  }
}

void TexecomComponent::handle_arm_data_(const std::vector<uint8_t> &pkt) {
  if (pkt.size() < 7 || pkt[1] == CMD_NAK)
    return;
  // The panel toggles bit 7 of this byte as a ~1 Hz poll heartbeat (observed
  // flipping 0xE0<->0x60 while disarmed). The real arm state is in the lower
  // bits, so mask the heartbeat off - comparing the raw byte made the panel
  // flap armed<->disarmed every second.
  const uint8_t masked = pkt[6] & 0x7F;
  bool armed = masked != (this->disarmed_value_ & 0x7F);
  ESP_LOGV(TAG, "Arm byte=0x%02X (masked 0x%02X) -> %s", pkt[6], masked, armed ? "armed" : "disarmed");
  if (this->armed_sensor_ != nullptr)
    this->armed_sensor_->publish_state(armed);
  if (this->alarm_panel_ != nullptr)
    this->alarm_panel_->publish_state(armed ? alarm_control_panel::ACP_STATE_ARMED_AWAY
                                            : alarm_control_panel::ACP_STATE_DISARMED);
}

void TexecomComponent::queue_action_(uint8_t command, uint8_t area) {
  this->action_command_ = command;
  this->action_area_ = area;
  this->have_action_ = true;
}

void TexecomAlarmPanel::control(const alarm_control_panel::AlarmControlPanelCall &call) {
  if (!call.get_state().has_value() || this->parent_ == nullptr)
    return;
  switch (*call.get_state()) {
    case alarm_control_panel::ACP_STATE_ARMED_AWAY:
      ESP_LOGI(TAG, "Arming area %u (away)", this->area_);
      this->parent_->arm_area(this->area_);
      break;
    case alarm_control_panel::ACP_STATE_DISARMED:
      ESP_LOGI(TAG, "Disarming area %u", this->area_);
      this->parent_->disarm_area(this->area_);
      break;
    default:
      break;
  }
}

void TexecomAlarmPanel::dump_config() {
  ESP_LOGCONFIG(TAG, "Texecom Alarm Panel (area %u)", this->area_);
}

void TexecomComponent::send_read_(uint32_t address, uint8_t size) {
  std::vector<uint8_t> payload = {
      (uint8_t) ((address >> 16) & 0xFF),
      (uint8_t) ((address >> 8) & 0xFF),
      (uint8_t) (address & 0xFF),
      size,
  };
  this->send_command_(CMD_READ_VOLATILE, payload);
}

void TexecomComponent::send_command_(uint8_t command, const std::vector<uint8_t> &payload) {
  std::vector<uint8_t> pkt;
  pkt.push_back((uint8_t) (payload.size() + 3));
  pkt.push_back(command);
  for (auto b : payload)
    pkt.push_back(b);
  pkt.push_back(checksum_(pkt));
  this->write_array(pkt.data(), pkt.size());
  this->last_tx_ = millis();
  this->awaiting_response_ = true;
  ESP_LOGV(TAG, "TX: %s", to_hex_(pkt).c_str());
}

std::vector<uint8_t> TexecomComponent::passcode_bytes_() const {
  // Raw digit values: '1' -> 0x01, '0' -> 0x00.
  std::vector<uint8_t> out;
  for (char c : this->udl_) {
    if (c >= '0' && c <= '9')
      out.push_back((uint8_t) (c - '0'));
  }
  return out;
}

uint8_t TexecomComponent::checksum_(const std::vector<uint8_t> &bytes) {
  uint8_t sum = 0;
  for (auto b : bytes)
    sum += b;
  return (uint8_t) (0xFF - sum);
}

std::string TexecomComponent::to_hex_(const std::vector<uint8_t> &bytes) {
  static const char *const HEX = "0123456789ABCDEF";
  std::string out;
  for (auto b : bytes) {
    out += HEX[b >> 4];
    out += HEX[b & 0x0F];
    out += ' ';
  }
  return out;
}

void TexecomComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Texecom Premier (Wintex/UDL, raw-digit auth)");
  ESP_LOGCONFIG(TAG, "  Zones registered: %u", (unsigned) this->zones_.size());
  ESP_LOGCONFIG(TAG, "  Zone status @0x%06X (%u bytes)", this->zone_status_address_, this->zone_count_);
  ESP_LOGCONFIG(TAG, "  Arm state @0x%06X (disarmed=0x%02X)", this->arm_state_address_, this->disarmed_value_);
  ESP_LOGCONFIG(TAG, "  Poll interval: %ums", this->poll_interval_);
  this->check_uart_settings(19200, 2, uart::UART_CONFIG_PARITY_NONE, 8);
}

}  // namespace texecom
}  // namespace esphome
