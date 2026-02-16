"""
Test Client configuration - server URLs, agent ID, audio settings.
"""
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Server
SERVER_WS_URL = "ws://192.168.1.46/ws"
SERVER_API_URL = "http://192.168.1.46"
LIVEKIT_URL = "ws://192.168.1.46:7880"
DEFAULT_AGENT_ID = "test_agent"

# Audio (for audio_core when used)
SAMPLE_RATE = 48000
CHANNELS = 2
SYSTEM_AUDIO_MULTIPLIER = 1.0
