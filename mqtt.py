import subprocess
import threading
from threading import Timer
import logging
import paho.mqtt.client as mqtt
import base64
import json
from datetime import datetime
# import picamera
# import cv2
from time import sleep
from config import USERNAME, PASSWORD, BROKER, PORT, KEEP_ALIVE_INTERVAL, BASE_TOPIC, IDENTIFIER, MODEL, VERSION, WATER_LOW_CM, UPPER_CAMERA_DEVICE, LOWER_CAMERA_DEVICE, UPPER_IMAGE_PATH, LOWER_IMAGE_PATH, CAMERA_RESOLUTION, IMAGE_INTERVAL_SECONDS, AUTO_PUMP_ENABLED, AUTO_PUMP_DAY_ON_TIME, AUTO_PUMP_DAY_OFF_TIME, AUTO_PUMP_NIGHT_ON_TIME, AUTO_PUMP_NIGHT_OFF_TIME, AUTO_PUMP_DAY_START_HOUR, AUTO_PUMP_DAY_END_HOUR

from gpiozero import Button  # Import gpiozero Button
from gpiozero.pins.pigpio import PiGPIOFactory

from app.sensors.light.light import Light
from app.sensors.pump.pump import Pump
from app.sensors.pcb_temp.pcb_temp import get_pcb_temperature
from app.sensors.temperature.temperature import temperature_sensor
from app.sensors.humidity.humidity import humidity_sensor
from app.sensors.distance.distance import Distance, MeasurementError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("gardyn.log"),  # Log to a file
        logging.StreamHandler()  # Log to the console (stdout)
    ]
)

logger = logging.getLogger(__name__)

# set to INFO, for to capture mqtt messages at info-level messages.
logger.setLevel(logging.INFO)

# logger.debug("This is a debug message")
# logger.info("This is an info message")
# logger.warning("This is a warning message")
# logger.error("This is an error message")

# Initialize devices
pin_factory = PiGPIOFactory()

pump = Pump(pin_factory=pin_factory)
light = Light(pin_factory=pin_factory)
distance_sensor = Distance(pin_factory=pin_factory)

# default on brightness
brightness  = 50
speed       = 100
sec_per_min = 60
min_per_hr  = 60

# publish twice an hour
publish_frequency = sec_per_min * min_per_hr / 2

# Button GPIO setup using gpiozero
button_pin = 13
button = Button(button_pin, pin_factory=pin_factory, bounce_time=0.2, hold_time=2)  # hold_time = 2 seconds for long press detection

# Variables to track the state of the light and pump
light_state = False
pump_state = False
double_press_time = 1  # Time to detect a double press (in seconds)
press_count = 0
double_press_timer = None

# Variables for automatic pump cycling
auto_pump_enabled = AUTO_PUMP_ENABLED
auto_pump_timer = None

# Day/Night schedule configuration (loaded from environment)
day_pump_on_time = AUTO_PUMP_DAY_ON_TIME
day_pump_off_time = AUTO_PUMP_DAY_OFF_TIME
night_pump_on_time = AUTO_PUMP_NIGHT_ON_TIME
night_pump_off_time = AUTO_PUMP_NIGHT_OFF_TIME

# Day/Night time boundaries (24-hour format)
day_start_hour = AUTO_PUMP_DAY_START_HOUR
day_end_hour = AUTO_PUMP_DAY_END_HOUR

# Current pump state tracking
pump_is_on = False
current_cycle_remaining = 0

# Button press callbacks
def toggle_light():
    global light_state
    light_state = not light_state
    if light_state:
        logger.info("Toggling Light ON")
        light.set_duty_cycle(brightness)
        client.publish(BASE_TOPIC + "/light/state", "ON")
    else:
        logger.info("Toggling Light OFF")
        light.off()
        client.publish(BASE_TOPIC + "/light/state", "OFF")

def toggle_pump():
    global pump_state
    pump_state = not pump_state
    if pump_state:
        logger.info("Toggling Pump ON")
        pump.set_speed(speed)
        client.publish(BASE_TOPIC + "/pump/state", "ON")
    else:
        logger.info("Toggling Pump OFF")
        pump.off()
        client.publish(BASE_TOPIC + "/pump/state", "OFF")

def handle_button_press():
    global press_count, double_press_timer

    press_count += 1

    if press_count == 1:
        # Start a timer to detect if a second press occurs within the double press time window
        double_press_timer = Timer(double_press_time, handle_single_press)
        double_press_timer.start()
    elif press_count == 2:
        # If a second press occurs, cancel the single press action and trigger the double press action
        if double_press_timer:
            double_press_timer.cancel()
        handle_double_press()
        press_count = 0

def handle_single_press():
    global press_count
    toggle_light()  # Single press toggles the light
    press_count = 0

def handle_double_press():
    toggle_pump()  # Double press toggles the pump

# Set button event for press detection
button.when_pressed = handle_button_press

# helpers
def flash_lights(times=3, delay=0.3):
    original_brightness = light.get_brightness()  # Save the brightness (0–100 scale)
    was_on = original_brightness > 0  # If >0%, we consider it "on"

    logger.info(f"Flashing lights {times} times. Original brightness: {original_brightness}%")

    for _ in range(times):
        light.off()
        sleep(delay)
        light.set_brightness(100)  # Flash full brightness for maximum visibility
        sleep(delay)
    # Restore original state
    if was_on:
        light.set_brightness(original_brightness)
    else:
        light.off()

def safe_distance_measure():
    global distance_sensor
    try:
        return distance_sensor.measure_once()
    except MeasurementError as e:
        logger.warning(f"Distance measure failed: {e}, trying recovery")
        try:
            distance_sensor = Distance(pin_factory=pin_factory)
            return distance_sensor.measure_once()
        except Exception as e2:
            logger.error(f"Distance full recovery failed: {e2}")
            return None

def publish_water_low_mode(client):
    if WATER_LOW_CM not in (None, 0):
        mode = "Enabled"
    else:
        mode = "Disabled"
    logger.info(f"Publishing water low mode: {mode}")
    client.publish(BASE_TOPIC + "/water/low/mode", mode, retain=True)


def update_water_low_state(client):
    if WATER_LOW_CM not in (None, 0):
        distance = safe_distance_measure()
        if distance is not None:
            if distance > WATER_LOW_CM:
                client.publish(BASE_TOPIC + "/water/low/state", "ON", retain=True)
                logger.info(f"Updated water low state to ON (distance {distance:.2f}cm > {WATER_LOW_CM:.2f}cm)")
            else:
                client.publish(BASE_TOPIC + "/water/low/state", "OFF", retain=True)
                logger.info(f"Updated water low state to OFF (distance {distance:.2f}cm <= {WATER_LOW_CM:.2f}cm)")
        else:
            logger.warning("Could not update water low state because distance reading failed")
    else:
        # If checking is disabled, maybe set it to OFF by default
        client.publish(BASE_TOPIC + "/water/low/state", "OFF", retain=True)
        logger.info("Water low checking disabled, setting water low state to OFF")

def is_daytime():
    """Determine if current time is day or night based on configured hours"""
    current_hour = datetime.now().hour
    return day_start_hour <= current_hour < day_end_hour

def get_pump_schedule():
    """Get the current pump schedule based on day/night time"""
    if is_daytime():
        return day_pump_on_time, day_pump_off_time
    else:
        return night_pump_on_time, night_pump_off_time

def start_auto_pump_cycle(client):
    """Start the automatic pump cycling with day/night schedules"""
    global auto_pump_enabled, auto_pump_timer, pump_state, pump_is_on, current_cycle_remaining
    
    if auto_pump_enabled:
        logger.warning("Auto pump cycle already running - ignoring start command")
        return
    
    auto_pump_enabled = True
    pump_is_on = False
    current_cycle_remaining = 0
    
    # Get current schedule
    on_time, off_time = get_pump_schedule()
    time_period = "day" if is_daytime() else "night"
    
    logger.info("=" * 50)
    logger.info("🚀 AUTO PUMP CYCLE STARTED")
    logger.info(f"📅 Schedule: {time_period.upper()} mode")
    logger.info(f"⏰ ON time: {on_time} minutes")
    logger.info(f"⏰ OFF time: {off_time} minutes")
    logger.info(f"🔄 Total cycle: {on_time + off_time} minutes")
    logger.info("=" * 50)
    
    client.publish(BASE_TOPIC + "/pump/auto/state", "ON", retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/schedule", time_period, retain=True)
    
    # Start the cycle immediately
    cycle_auto_pump(client)

def stop_auto_pump_cycle(client):
    """Stop the automatic pump cycling"""
    global auto_pump_enabled, auto_pump_timer, pump_state, pump_is_on, current_cycle_remaining
    
    if not auto_pump_enabled:
        logger.warning("Auto pump cycle not running - ignoring stop command")
        return
    
    auto_pump_enabled = False
    pump_is_on = False
    current_cycle_remaining = 0
    
    if auto_pump_timer:
        auto_pump_timer.cancel()
        auto_pump_timer = None
    
    # Turn off pump if it's running
    if pump_state:
        pump.off()
        pump_state = False
        client.publish(BASE_TOPIC + "/pump/state", "OFF")
        logger.info("🔴 Pump turned OFF due to auto cycle stop")
    
    logger.info("=" * 50)
    logger.info("🛑 AUTO PUMP CYCLE STOPPED")
    logger.info("⏹️  All timers cancelled")
    logger.info("🔴 Pump state: OFF")
    logger.info("=" * 50)
    
    client.publish(BASE_TOPIC + "/pump/auto/state", "OFF", retain=True)

def cycle_auto_pump(client):
    """Cycle the pump on/off for automatic operation with day/night schedules"""
    global auto_pump_enabled, auto_pump_timer, pump_state, speed, pump_is_on, current_cycle_remaining
    
    if not auto_pump_enabled:
        logger.warning("Auto pump cycle disabled - stopping cycle function")
        return
    
    # Get current schedule (may have changed if day/night transition occurred)
    on_time, off_time = get_pump_schedule()
    time_period = "day" if is_daytime() else "night"
    
    logger.info(f"🔄 Auto cycle check - {time_period.upper()} mode")
    
    # Check water level before turning on pump
    if WATER_LOW_CM not in (None, 0):
        distance = safe_distance_measure()
        if distance is not None and distance > WATER_LOW_CM:
            logger.warning("⚠️  WATER LEVEL CHECK FAILED")
            logger.warning(f"💧 Distance: {distance:.2f}cm (threshold: {WATER_LOW_CM:.2f}cm)")
            logger.warning("🚫 Skipping pump cycle - water too low")
            client.publish(BASE_TOPIC + "/water/low/state", "ON", retain=True)
            flash_lights()
            # Schedule next cycle with current schedule
            next_duration = off_time * 60  # Convert to seconds
            logger.info(f"⏰ Next check in {off_time} minutes (OFF period)")
            auto_pump_timer = Timer(next_duration, cycle_auto_pump, args=(client,))
            auto_pump_timer.start()
            return
        else:
            logger.info(f"✅ Water level OK: {distance:.2f}cm")
            client.publish(BASE_TOPIC + "/water/low/state", "OFF", retain=True)
    
    # Determine next action based on current pump state
    if pump_is_on:
        # Currently ON, so turn OFF and schedule OFF duration
        pump.off()
        pump_state = False
        pump_is_on = False
        next_duration = off_time * 60  # Convert to seconds
        logger.info("🔴 PUMP TURNED OFF")
        logger.info(f"⏰ OFF period: {off_time} minutes ({time_period} schedule)")
        logger.info(f"⏰ Next cycle in: {off_time} minutes")
        client.publish(BASE_TOPIC + "/pump/state", "OFF")
    else:
        # Currently OFF, so turn ON and schedule ON duration
        pump.set_speed(speed)
        pump_state = True
        pump_is_on = True
        next_duration = on_time * 60  # Convert to seconds
        logger.info("🟢 PUMP TURNED ON")
        logger.info(f"⚡ Speed: {speed}%")
        logger.info(f"⏰ ON period: {on_time} minutes ({time_period} schedule)")
        logger.info(f"⏰ Next cycle in: {on_time} minutes")
        client.publish(BASE_TOPIC + "/pump/state", "ON")
    
    # Update schedule info
    client.publish(BASE_TOPIC + "/pump/auto/schedule", time_period, retain=True)
    
    # Schedule next cycle
    auto_pump_timer = Timer(next_duration, cycle_auto_pump, args=(client,))
    auto_pump_timer.start()
    logger.info("⏰ Timer scheduled for next cycle")

# https://www.home-assistant.io/integrations/mqtt/#discovery-messages
#  Note: homeassistant/<component>/[<node_id>/]<object_id>/config.
#  User device_class for auto suggestion on HA card picks
def send_discovery_messages(client):
    device_info = {
        "identifiers": [IDENTIFIER],
        "name": BASE_TOPIC,
        "manufacturer": "gardyn-of-eden",
        "model": MODEL,
        "sw_version": VERSION,
    }

    # Config for Light
    TEMP_CONFIG_TOPIC = "homeassistant/light/gardyn/"+IDENTIFIER+"_light/config"
    temp_config_payload = {
        "name": "Light",
        "unique_id": IDENTIFIER + "_light",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/light/state",
        "command_topic": BASE_TOPIC + "/light/command",
        "brightness_state_topic": BASE_TOPIC + "/light/brightness/state",
        "brightness_command_topic": BASE_TOPIC + "/light/brightness/set",
        "brightness_scale": 100,
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    #Config for Pump (as a light with speed control, for example)
    # todo: maybe use fan instead....
    TEMP_CONFIG_TOPIC = "homeassistant/light/gardyn/"+IDENTIFIER+"_pump/config"
    temp_config_payload = {
        "name": "Pump",
        "unique_id": IDENTIFIER + "_pump",
        "platform": "mqtt",
	"device_class": "fan",
        "state_topic": BASE_TOPIC + "/pump/state",
        "command_topic": BASE_TOPIC + "/pump/command",

        "brightness_state_topic": BASE_TOPIC + "/pump/speed/state",
        "brightness_command_topic": BASE_TOPIC + "/pump/speed/set",
        "brightness_scale": 100,

        # if using fan....
	# "percentage_state_topic": BASE_TOPIC + "/pump/speed/state",
	# "percentage_command_topic": BASE_TOPIC + "/pump/speed/set",
	# "speed_range_min": 1,
	# "speed_range_max": 100,
        "icon": "mdi:water-pump",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Automatic Pump Cycling Switch
    TEMP_CONFIG_TOPIC = f"homeassistant/switch/gardyn/{IDENTIFIER}_auto_pump/config"
    temp_config_payload = {
        "name": "Auto Pump Cycle",
        "unique_id": IDENTIFIER + "_auto_pump",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/state",
        "command_topic": BASE_TOPIC + "/pump/auto/command",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:water-pump",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Day Pump ON Time
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_day_pump_on/config"
    temp_config_payload = {
        "name": "Day Pump ON Time",
        "unique_id": IDENTIFIER + "_day_pump_on",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/day/on_time",
        "command_topic": BASE_TOPIC + "/pump/auto/day/on_time/set",
        "min": 1,
        "max": 60,
        "step": 1,
        "unit_of_measurement": "min",
        "icon": "mdi:timer",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Day Pump OFF Time
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_day_pump_off/config"
    temp_config_payload = {
        "name": "Day Pump OFF Time",
        "unique_id": IDENTIFIER + "_day_pump_off",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/day/off_time",
        "command_topic": BASE_TOPIC + "/pump/auto/day/off_time/set",
        "min": 1,
        "max": 60,
        "step": 1,
        "unit_of_measurement": "min",
        "icon": "mdi:timer-off",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Night Pump ON Time
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_night_pump_on/config"
    temp_config_payload = {
        "name": "Night Pump ON Time",
        "unique_id": IDENTIFIER + "_night_pump_on",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/night/on_time",
        "command_topic": BASE_TOPIC + "/pump/auto/night/on_time/set",
        "min": 1,
        "max": 60,
        "step": 1,
        "unit_of_measurement": "min",
        "icon": "mdi:timer",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Night Pump OFF Time
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_night_pump_off/config"
    temp_config_payload = {
        "name": "Night Pump OFF Time",
        "unique_id": IDENTIFIER + "_night_pump_off",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/night/off_time",
        "command_topic": BASE_TOPIC + "/pump/auto/night/off_time/set",
        "min": 1,
        "max": 60,
        "step": 1,
        "unit_of_measurement": "min",
        "icon": "mdi:timer-off",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Day Start Hour
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_day_start/config"
    temp_config_payload = {
        "name": "Day Start Hour",
        "unique_id": IDENTIFIER + "_day_start",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/day_start",
        "command_topic": BASE_TOPIC + "/pump/auto/day_start/set",
        "min": 0,
        "max": 23,
        "step": 1,
        "unit_of_measurement": "h",
        "icon": "mdi:sun-clock",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Day End Hour
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_day_end/config"
    temp_config_payload = {
        "name": "Day End Hour",
        "unique_id": IDENTIFIER + "_day_end",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/day_end",
        "command_topic": BASE_TOPIC + "/pump/auto/day_end/set",
        "min": 0,
        "max": 23,
        "step": 1,
        "unit_of_measurement": "h",
        "icon": "mdi:moon-clock",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Current Schedule Status
    TEMP_CONFIG_TOPIC = f"homeassistant/sensor/gardyn/{IDENTIFIER}_pump_schedule/config"
    temp_config_payload = {
        "name": "Pump Schedule",
        "unique_id": IDENTIFIER + "_pump_schedule",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/pump/auto/schedule",
        "icon": "mdi:schedule",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    #Config for Temperature from PCB
    TEMP_CONFIG_TOPIC = "homeassistant/sensor/gardyn/"+IDENTIFIER+"_pcb_temp/config"
    temp_config_payload = {
        "name": "PCB Temperature",
        "unique_id": IDENTIFIER + "_pcb_temp",
        "state_topic": BASE_TOPIC + "/pcb/temperature",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    #Config for Temperature Sensor
    TEMP_CONFIG_TOPIC = "homeassistant/sensor/gardyn/"+IDENTIFIER+"_temperature/config"
    temp_config_payload = {
        "name": "Temperature",
        "unique_id": IDENTIFIER + "_temperature",
        "state_topic": BASE_TOPIC + "/temperature",
        "command_topic": BASE_TOPIC + "/temperature/get",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    #Config for Humidity Sensor
    TEMP_CONFIG_TOPIC = "homeassistant/sensor/gardyn/"+IDENTIFIER+"_humidity/config"
    temp_config_payload = {
        "name": "Humidity",
        "unique_id": IDENTIFIER + "_humidity",
        "state_topic": BASE_TOPIC + "/humidity",
        "command_topic": BASE_TOPIC + "/humidity/get",
        "unit_of_measurement": "%",
        "device_class": "humidity",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)


    #Config for Water Level Sensor
    TEMP_CONFIG_TOPIC = "homeassistant/sensor/gardyn/"+IDENTIFIER+"_water_level/config"

    temp_config_payload = {
        "name": "Water Level",
        "unique_id": IDENTIFIER + "_water_level",
        "state_topic": BASE_TOPIC + "/water/level",
        "command_topic": BASE_TOPIC + "/water/level/get",
        "unit_of_measurement": "cm",
        "device_class": "distance",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Water Low Binary Sensor
    TEMP_CONFIG_TOPIC = f"homeassistant/binary_sensor/gardyn/{IDENTIFIER}_water_low/config"
    temp_config_payload = {
        "name": "Water Low",
        "unique_id": IDENTIFIER + "_water_low",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/water/low/state",
        "device_class": "problem",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Water Low Threshold (current value)
        # Config for Water Low CM Set Number
    TEMP_CONFIG_TOPIC = f"homeassistant/number/gardyn/{IDENTIFIER}_water_low_cm/config"
    temp_config_payload = {
        "name": "Set Water Low Threshold",
        "unique_id": IDENTIFIER + "_water_low_cm",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/water/low/cm",
        "command_topic": BASE_TOPIC + "/water/low/cm/set",
        "min": 0,
        "max": 15,
        "step": 0.5,
        "unit_of_measurement": "cm",
        "device_class": "distance",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Config for Water Low Mode (Enabled/Disabled)
    TEMP_CONFIG_TOPIC = f"homeassistant/sensor/gardyn/{IDENTIFIER}_water_low_mode/config"
    temp_config_payload = {
        "name": "Water Low Mode",
        "unique_id": IDENTIFIER + "_water_low_mode",
        "platform": "mqtt",
        "state_topic": BASE_TOPIC + "/water/low/mode",
        "icon": "mdi:toggle-switch",  # Optional: or use mdi:alert for dramatic effect
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Discovery configuration for Camera A (image entity)
    TEMP_CONFIG_TOPIC = "homeassistant/image/gardyn/" + IDENTIFIER + "_upper_camera/config"
    temp_config_payload = {
        "name": "Upper Camera",
        "unique_id": IDENTIFIER + "_upper_camera",
        "image_topic": BASE_TOPIC + "/image/upper_camera",
        "encoding": "b64",
        "content_type": "image/jpeg",
        "object_id": IDENTIFIER + "_upper_camera",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

    # Discovery configuration for Camera B (image entity)
    TEMP_CONFIG_TOPIC = "homeassistant/image/gardyn/" + IDENTIFIER + "_lower_camera/config"
    temp_config_payload = {
        "name": "Lower Camera",
        "unique_id": IDENTIFIER + "_lower_camera",
        "image_topic": BASE_TOPIC + "/image/lower_camera",
        "encoding": "b64",
        "content_type": "image/jpeg",
        "object_id": IDENTIFIER + "_lower_camera",
        "device": device_info
    }
    client.publish(TEMP_CONFIG_TOPIC, json.dumps(temp_config_payload), retain=True)

def on_connect(client, userdata, flags, rc, properties=None):
    logger.info(f"Connected with result code {rc}")
    client.subscribe(BASE_TOPIC + "/#")
    # client.subscribe(BASE_TOPIC + "/light/brightness/set")
    send_discovery_messages(client)
    publish_water_low_mode(client)
    
    # Publish initial auto pump state and configuration
    if auto_pump_enabled:
        client.publish(BASE_TOPIC + "/pump/auto/state", "ON", retain=True)
        logger.info("🚀 Auto pump enabled on startup - starting cycle")
        start_auto_pump_cycle(client)
    else:
        client.publish(BASE_TOPIC + "/pump/auto/state", "OFF", retain=True)
        logger.info("⏹️  Auto pump disabled on startup")
    
    client.publish(BASE_TOPIC + "/pump/auto/schedule", "night", retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/day/on_time", str(day_pump_on_time), retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/day/off_time", str(day_pump_off_time), retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/night/on_time", str(night_pump_on_time), retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/night/off_time", str(night_pump_off_time), retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/day_start", str(day_start_hour), retain=True)
    client.publish(BASE_TOPIC + "/pump/auto/day_end", str(day_end_hour), retain=True)

def on_message(client, userdata, msg):
    global brightness, speed, WATER_LOW_CM

    # Handle binary payloads (like image topics) — skip decoding
    if msg.topic.endswith("/image/upper_camera") or msg.topic.endswith("/image/lower_camera"):
        logger.debug(f"Received binary image on topic {msg.topic}, skipping decode.")
        return

    try:
        payload = msg.payload.decode("utf-8").strip()
        logger.debug(f"Decoded payload on {msg.topic}: '{payload}'")
    except UnicodeDecodeError:
        logger.error(f"Failed to decode message on topic {msg.topic}. Likely binary.")
        return

    topic_suffix = msg.topic.replace(BASE_TOPIC + "/", "")

    try:
        # === Pump Logic ===
        if topic_suffix == "pump/command":
            if payload.upper() == "ON":
                if WATER_LOW_CM not in (None, 0):
                    distance = safe_distance_measure()
                    if distance is not None and distance > WATER_LOW_CM:
                        logger.warning(f"Water too low ({distance:.2f}cm > {WATER_LOW_CM:.2f}cm), aborting pump")
                        flash_lights()
                        client.publish(BASE_TOPIC + "/water/low/state", "ON", retain=True)
                        return
                    else:
                        client.publish(BASE_TOPIC + "/water/low/state", "OFF", retain=True)
                pump.set_speed(speed)
                client.publish(BASE_TOPIC + "/pump/state", "ON")
            elif payload.upper() == "OFF":
                pump.off()
                client.publish(BASE_TOPIC + "/pump/state", "OFF")

        elif topic_suffix == "pump/speed/set" and payload.isdigit():
            speed = int(payload)
            pump.set_speed(speed)
            client.publish(BASE_TOPIC + "/pump/speed/state", str(speed))

        # === Light Logic ===
        elif topic_suffix == "light/command":
            if payload.upper() == "ON":
                light.set_duty_cycle(brightness)
                client.publish(BASE_TOPIC + "/light/state", "ON")
            elif payload.upper() == "OFF":
                light.off()
                client.publish(BASE_TOPIC + "/light/state", "OFF")

        elif topic_suffix == "light/brightness/set" and payload.isdigit():
            brightness = int(payload)
            light.set_duty_cycle(brightness)
            client.publish(BASE_TOPIC + "/light/brightness/state", str(brightness))

        # === Automatic Pump Cycling ===
        elif topic_suffix == "pump/auto/command":
            logger.info(f"📨 Received MQTT command: {payload}")
            if payload.upper() == "ON":
                logger.info("🚀 Starting auto pump cycle via MQTT command")
                start_auto_pump_cycle(client)
            elif payload.upper() == "OFF":
                logger.info("🛑 Stopping auto pump cycle via MQTT command")
                stop_auto_pump_cycle(client)
            else:
                logger.warning(f"❌ Invalid auto pump command: {payload} (expected ON/OFF)")

        # === Day/Night Schedule Configuration ===
        elif topic_suffix == "pump/auto/day/on_time/set":
            try:
                day_pump_on_time = int(payload)
                client.publish(BASE_TOPIC + "/pump/auto/day/on_time", str(day_pump_on_time), retain=True)
                logger.info(f"📅 Day pump ON time updated: {day_pump_on_time} minutes")
            except ValueError:
                logger.error(f"❌ Invalid day pump ON time value: {payload}")

        elif topic_suffix == "pump/auto/day/off_time/set":
            try:
                day_pump_off_time = int(payload)
                client.publish(BASE_TOPIC + "/pump/auto/day/off_time", str(day_pump_off_time), retain=True)
                logger.info(f"📅 Day pump OFF time updated: {day_pump_off_time} minutes")
            except ValueError:
                logger.error(f"❌ Invalid day pump OFF time value: {payload}")

        elif topic_suffix == "pump/auto/night/on_time/set":
            try:
                night_pump_on_time = int(payload)
                client.publish(BASE_TOPIC + "/pump/auto/night/on_time", str(night_pump_on_time), retain=True)
                logger.info(f"🌙 Night pump ON time updated: {night_pump_on_time} minutes")
            except ValueError:
                logger.error(f"❌ Invalid night pump ON time value: {payload}")

        elif topic_suffix == "pump/auto/night/off_time/set":
            try:
                night_pump_off_time = int(payload)
                client.publish(BASE_TOPIC + "/pump/auto/night/off_time", str(night_pump_off_time), retain=True)
                logger.info(f"🌙 Night pump OFF time updated: {night_pump_off_time} minutes")
            except ValueError:
                logger.error(f"❌ Invalid night pump OFF time value: {payload}")

        elif topic_suffix == "pump/auto/day_start/set":
            try:
                day_start_hour = int(payload)
                if 0 <= day_start_hour <= 23:
                    client.publish(BASE_TOPIC + "/pump/auto/day_start", str(day_start_hour), retain=True)
                    logger.info(f"🌅 Day start hour updated: {day_start_hour}:00")
                else:
                    logger.error(f"❌ Day start hour must be between 0-23: {payload}")
            except ValueError:
                logger.error(f"❌ Invalid day start hour value: {payload}")

        elif topic_suffix == "pump/auto/day_end/set":
            try:
                day_end_hour = int(payload)
                if 0 <= day_end_hour <= 23:
                    client.publish(BASE_TOPIC + "/pump/auto/day_end", str(day_end_hour), retain=True)
                    logger.info(f"🌇 Day end hour updated: {day_end_hour}:00")
                else:
                    logger.error(f"❌ Day end hour must be between 0-23: {payload}")
            except ValueError:
                logger.error(f"❌ Invalid day end hour value: {payload}")

        # === Water Level ===
        elif topic_suffix == "water/level/get":
            distance = safe_distance_measure()
            if distance is not None:
                client.publish(BASE_TOPIC + "/water/level", f"{distance:.2f}")

        elif topic_suffix == "water/low/cm/set":
            try:
                WATER_LOW_CM = float(payload)
                client.publish(BASE_TOPIC + "/water/low/cm", f"{WATER_LOW_CM:.2f}", retain=True)
                publish_water_low_mode(client)
                update_water_low_state(client)
            except ValueError:
                logger.error(f"Invalid water low cm value: {payload}")

        # === Sensor Data on Request ===
        elif topic_suffix == "pcb/temperature/get":
            pcb_temp = get_pcb_temperature()
            client.publish(BASE_TOPIC + "/pcb/temperature", f"{pcb_temp:.2f}")

        elif topic_suffix == "temperature/get":
            temperature = temperature_sensor.read()
            client.publish(BASE_TOPIC + "/temperature", f"{temperature:.2f}")

        elif topic_suffix == "humidity/get":
            humidity = humidity_sensor.read()
            client.publish(BASE_TOPIC + "/humidity", f"{humidity:.2f}")

    except Exception as e:
        logger.exception(f"Error handling message on topic {msg.topic}: {e}")

def publish_pcb_temperature(client):
    while True:
        try:
            pcb_temp = get_pcb_temperature()
            logger.info(f"Publishing PCB Temperature: {pcb_temp:.2f}°C")
            client.publish(BASE_TOPIC + "/pcb/temperature", f"{pcb_temp:.2f}")
        except Exception as e:
            logger.error(f"Failed to read or publish PCB temperature: {e}")
        sleep(30*60)  # Publish frequency, every x seconds

def publish_temperature(client):
    while True:
        try:
            temperature = temperature_sensor.read()
            logger.info(f"Publishing Temperature: {temperature:.2f}°C")
            client.publish(BASE_TOPIC + "/temperature", f"{temperature:.2f}")
        except Exception as e:
            logger.error(f"Failed to read or publish ambient temperature: {e}")
        sleep(30*60)  # Publish frequency, every x seconds

def publish_humidity(client):
    while True:
        try:
            humidity = humidity_sensor.read()
            logger.info(f"Publishing Humidity: {humidity:.2f}%")
            client.publish(BASE_TOPIC + "/humidity", f"{humidity:.2f}")
        except Exception as e:
            logger.error(f"Failed to read or publish ambient humidity: {e}")
        sleep(30*60)  # Publish frequency, every x seconds

def publish_water_level(client):
    while True:
        distance = safe_distance_measure()
        if distance is not None:
            logger.info(f"💧 Water Level Check: {distance:.2f}cm")
            client.publish(BASE_TOPIC + "/water/level", f"{distance:.2f}")
        else:
            logger.warning("⚠️  Water level measurement failed")
        sleep(5 * 60)  # Every 5 minutes

def publish_images(client):
    while True:
        try:
            # Capture upper camera image
            subprocess.check_call([
                'fswebcam', '-d', UPPER_CAMERA_DEVICE, '-r', CAMERA_RESOLUTION,
                '-S', '2', '-F', '2', '--no-banner', UPPER_IMAGE_PATH
            ])
            logger.info(f"Captured image from upper camera ({UPPER_CAMERA_DEVICE})")

            # Capture lower camera image
            subprocess.check_call([
                'fswebcam', '-d', LOWER_CAMERA_DEVICE, '-r', CAMERA_RESOLUTION,
                '-S', '2', '-F', '2', '--no-banner', LOWER_IMAGE_PATH
            ])
            logger.info(f"Captured image from lower camera ({LOWER_CAMERA_DEVICE})")

            # Publish upper camera image
            with open(UPPER_IMAGE_PATH, 'rb') as f:
                upper_cam_jpeg_data = f.read()  # Read as raw binary
                client.publish(BASE_TOPIC + "/image/upper_camera", payload=upper_cam_jpeg_data, qos=0, retain=False)
                logger.info("Published image to /image/upper_camera")

            # Publish lower camera image
            with open(LOWER_IMAGE_PATH, 'rb') as f:
                lower_cam_jpeg_data = f.read()  # Read as raw binary
                client.publish(BASE_TOPIC + "/image/lower_camera", payload=lower_cam_jpeg_data, qos=0, retain=False)
                logger.info("Published image to /image/lower_camera")

        except subprocess.CalledProcessError as e:
            logger.error(f"Camera capture failed: {e}")
        except Exception as e:
            logger.exception("Unexpected error during image capture/publish")

        sleep(IMAGE_INTERVAL_SECONDS)


if __name__ == "__main__":
    logger.info(f"Connecting to {BROKER} on port {PORT} with keep alive {KEEP_ALIVE_INTERVAL}")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(USERNAME, PASSWORD)
    client.connect(BROKER, PORT, KEEP_ALIVE_INTERVAL)

    pcb_temp_thread = threading.Thread(target=publish_pcb_temperature, args=(client,))
    pcb_temp_thread.daemon = True
    pcb_temp_thread.start()

    temperature_thread = threading.Thread(target=publish_temperature, args=(client,))
    temperature_thread.daemon = True
    temperature_thread.start()

    humidity_thread = threading.Thread(target=publish_humidity, args=(client,))
    humidity_thread.daemon = True
    humidity_thread.start()

    water_level_thread = threading.Thread(target=publish_water_level, args=(client,))
    water_level_thread.daemon = True
    water_level_thread.start()


    # publish_images_thread = threading.Thread(target=publish_images, args=(client,))
    # publish_images_thread.daemon = True
    # publish_images_thread.start()

    client.loop_forever()
