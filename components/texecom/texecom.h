#pragma once

#include <string>
#include <vector>

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/alarm_control_panel/alarm_control_panel.h"

namespace esphome {
namespace texecom {

class TexecomZone;
class TexecomAlarmPanel;

// Talks the Texecom Wintex/UDL protocol to a Premier 412 (International) over
// its COM port set to "PC-Com". Two non-obvious things for this firmware:
//  - the UDL passcode is sent as RAW DIGIT VALUES ("1234" -> 01 02 03 04);
//  - arm/disarm are short commands 'A'(0x41)/'D'(0x44) + area number.
class TexecomComponent : public Component, public uart::UARTDevice {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_udl(const std::string &udl) { this->udl_ = udl; }
  void set_zone_status_address(uint32_t addr) { this->zone_status_address_ = addr; }
  void set_zone_count(uint8_t count) { this->zone_count_ = count; }
  void set_arm_state_address(uint32_t addr) { this->arm_state_address_ = addr; }
  void set_disarmed_value(uint8_t v) { this->disarmed_value_ = v; }
  void set_poll_interval(uint32_t ms) { this->poll_interval_ = ms; }

  void register_zone(TexecomZone *zone) { this->zones_.push_back(zone); }
  void set_armed_sensor(binary_sensor::BinarySensor *s) { this->armed_sensor_ = s; }
  void set_ready_sensor(binary_sensor::BinarySensor *s) { this->ready_sensor_ = s; }
  void set_alarm_panel(TexecomAlarmPanel *p) { this->alarm_panel_ = p; }

  // Called by the alarm_control_panel platform.
  void arm_area(uint8_t area) { this->queue_action_(0x41, area); }
  void disarm_area(uint8_t area) { this->queue_action_(0x44, area); }

 protected:
  enum class State { INIT, HELLO_SENT, HELLO_ACKED, PASS_SENT, LOGGED_IN };
  enum class Poll { IDLE, ZONES, SEND_ARM, ARM, ACTION };

  void read_serial_();
  void process_packet_(const std::vector<uint8_t> &pkt);
  void send_command_(uint8_t command, const std::vector<uint8_t> &payload);
  void send_read_(uint32_t address, uint8_t size);
  void handle_zone_data_(const std::vector<uint8_t> &pkt);
  void handle_arm_data_(const std::vector<uint8_t> &pkt);
  void queue_action_(uint8_t command, uint8_t area);
  std::vector<uint8_t> passcode_bytes_() const;
  static uint8_t checksum_(const std::vector<uint8_t> &bytes);
  static std::string to_hex_(const std::vector<uint8_t> &bytes);

  std::vector<TexecomZone *> zones_;
  binary_sensor::BinarySensor *armed_sensor_{nullptr};
  binary_sensor::BinarySensor *ready_sensor_{nullptr};
  TexecomAlarmPanel *alarm_panel_{nullptr};

  std::string udl_{"1234"};
  uint32_t zone_status_address_{0x0004F8};
  uint8_t zone_count_{16};
  uint32_t arm_state_address_{0x0000E4};
  uint8_t disarmed_value_{0xE0};
  uint32_t poll_interval_{1000};

  std::vector<uint8_t> rx_buffer_;
  State state_{State::INIT};
  Poll poll_{Poll::IDLE};
  bool awaiting_response_{false};
  bool have_action_{false};
  uint8_t action_command_{0};
  uint8_t action_area_{1};
  uint32_t last_tx_{0};
  uint32_t last_poll_{0};
  uint32_t command_delay_{120};
  uint32_t response_timeout_{2500};
};

class TexecomZone : public binary_sensor::BinarySensor {
 public:
  void set_zone_number(uint8_t zone) { this->zone_number_ = zone; }
  uint8_t get_zone_number() const { return this->zone_number_; }

 protected:
  uint8_t zone_number_{0};
};

class TexecomAlarmPanel : public alarm_control_panel::AlarmControlPanel, public Component {
 public:
  void dump_config() override;
  void set_parent(TexecomComponent *parent) { this->parent_ = parent; }
  void set_area(uint8_t area) { this->area_ = area; }

  uint32_t get_supported_features() const override {
    return (uint32_t) alarm_control_panel::ACP_FEAT_ARM_AWAY;
  }
  bool get_requires_code() const override { return false; }
  bool get_requires_code_to_arm() const override { return false; }

 protected:
  void control(const alarm_control_panel::AlarmControlPanelCall &call) override;
  TexecomComponent *parent_{nullptr};
  uint8_t area_{1};
};

}  // namespace texecom
}  // namespace esphome
