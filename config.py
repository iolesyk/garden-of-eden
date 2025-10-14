import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# MQTT configurations
BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
KEEP_ALIVE_INTERVAL = int(os.getenv("MQTT_KEEPALIVE_INTERVAL", "60"))

# Topic configurations
VERSION = os.getenv("MQTT_VERSION", "1.0.0")
IDENTIFIER = os.getenv("MQTT_IDENTIFIER", "gardyn-xx")
MODEL= os.getenv("MQTT_DEVICE_MODEL", "gardyn 3.0")
BASE_TOPIC = os.getenv("MQTT_BASETOPIC", "gardyn")

USERNAME = os.getenv("MQTT_USERNAME")
PASSWORD = os.getenv("MQTT_PASSWORD")

SENSOR_TYPE = os.getenv('SENSOR_TYPE')

WATER_LOW_CM = float(os.getenv("WATER_LOW_CM", 0)) or None

# Auto Pump Configuration
AUTO_PUMP_ENABLED = os.getenv("AUTO_PUMP_ENABLED", "false").lower() in ("true", "1", "yes", "on")
AUTO_PUMP_DAY_ON_TIME = int(os.getenv("AUTO_PUMP_DAY_ON_TIME", "20"))
AUTO_PUMP_DAY_OFF_TIME = int(os.getenv("AUTO_PUMP_DAY_OFF_TIME", "10"))
AUTO_PUMP_NIGHT_ON_TIME = int(os.getenv("AUTO_PUMP_NIGHT_ON_TIME", "10"))
AUTO_PUMP_NIGHT_OFF_TIME = int(os.getenv("AUTO_PUMP_NIGHT_OFF_TIME", "30"))
AUTO_PUMP_DAY_START_HOUR = int(os.getenv("AUTO_PUMP_DAY_START_HOUR", "6"))
AUTO_PUMP_DAY_END_HOUR = int(os.getenv("AUTO_PUMP_DAY_END_HOUR", "18"))

UPPER_CAMERA_DEVICE = os.getenv("UPPER_CAMERA_DEVICE", "/dev/video0")
LOWER_CAMERA_DEVICE = os.getenv("LOWER_CAMERA_DEVICE", "/dev/video2")
UPPER_IMAGE_PATH = os.getenv("UPPER_IMAGE_PATH", "/tmp/upper_camera.jpg")
LOWER_IMAGE_PATH = os.getenv("LOWER_IMAGE_PATH", "/tmp/lower_camera.jpg")
CAMERA_RESOLUTION = os.getenv("CAMERA_RESOLUTION", "640x480")
IMAGE_INTERVAL_SECONDS = int(os.getenv("IMAGE_INTERVAL_SECONDS", "3600"))
