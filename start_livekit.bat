@echo off
cd /d "%~dp0LiveKit"
livekit-server --config config.yaml --bind 0.0.0.0 --dev
pause