"""
Test Client configuration – COPY TO config.py AND FILL IN YOUR VALUES.
"""
from pathlib import Path

BASE_DIR = Path(__file__).parent

# REPLACE with your server URLs
SERVER_WS_URL = "ws://YOUR_SERVER_IP_OR_HOSTNAME/ws"
SERVER_API_URL = "http://YOUR_SERVER_IP_OR_HOSTNAME"
LIVEKIT_URL = "ws://YOUR_SERVER_IP_OR_HOSTNAME:7880"
DEFAULT_AGENT_ID = "test_agent"

# Audio
SAMPLE_RATE = 48000
CHANNELS = 2
SYSTEM_AUDIO_MULTIPLIER = 1.0
