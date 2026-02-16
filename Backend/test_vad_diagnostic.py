"""
Diagnostic script to isolate why Silero VAD returns prob=0 despite audio with speech.
Run from Backend dir: python test_vad_diagnostic.py

This will:
1. Load Silero VAD
2. Generate test audio (sine wave "speech-like" + silence)
3. Run VAD and print raw output
4. Optionally load a real chunk from server (if you save one)
"""
import numpy as np
import torch

def test_silero_basic():
    """Test if Silero VAD returns non-zero for obvious speech-like audio."""
    print("Loading Silero VAD...")
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
    )
    print("Model loaded. Utils:", utils if utils else "None")

    # Create 512 samples at 16kHz - simple tone (speech-like frequency ~300Hz)
    t = np.linspace(0, 512/16000, 512, dtype=np.float32)
    audio = (np.sin(2 * np.pi * 300 * t) * 0.5).astype(np.float32)  # Amplitude 0.5
    print(f"Test audio: shape={audio.shape}, range=[{audio.min():.3f}, {audio.max():.3f}]")

    # Call model - match get_speech_timestamps style (1D tensor)
    model.reset_states()
    chunk_t = torch.from_numpy(audio)
    print(f"Input tensor: shape={chunk_t.shape}, dim={chunk_t.dim()}")

    out = model(chunk_t, 16000)
    print(f"Raw output: {out}, type={type(out)}")
    if torch.is_tensor(out):
        print(f"  shape={out.shape}, dtype={out.dtype}")
        print(f"  values: min={out.min().item():.4f}, max={out.max().item():.4f}, mean={out.mean().item():.4f}")
        prob = out.item() if out.numel() == 1 else out.flatten()[-1].item()
        print(f"  prob (extracted)={prob:.4f}")

    # Also try with batch dimension (1, 512)
    model.reset_states()
    chunk_2d = chunk_t.unsqueeze(0)
    out2 = model(chunk_2d, 16000)
    print(f"With batch dim: out={out2}, prob={out2.item() if out2.numel()==1 else out2.flatten()[-1].item():.4f}")

    # Try get_speech_timestamps from utils (official way)
    if utils:
        get_speech_timestamps = utils[0]
        model.reset_states()
        long_audio = np.tile(audio, 10)  # 10x longer
        ts = get_speech_timestamps(torch.from_numpy(long_audio), model, sampling_rate=16000)
        print(f"get_speech_timestamps result: {ts}")

if __name__ == "__main__":
    test_silero_basic()
