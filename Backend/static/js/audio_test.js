function getHost() {
    return window.location.host;
}

const adminId = "audio_test_" + Date.now();
const wsUrl = "ws://" + getHost() + "/ws/admin/" + adminId;
const ws = new WebSocket(wsUrl);
const SAMPLE_RATE = 48000;

let audioContext = null;
let listening = false;

const logEl = document.getElementById("log");
const statusEl = document.getElementById("status");
const btnListen = document.getElementById("btn-listen");

function log(msg) {
    if (!logEl) {
        return;
    }
    const line = document.createElement("div");
    line.textContent = new Date().toLocaleTimeString() + " " + msg;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
}

ws.onopen = function () {
    if (statusEl) {
        statusEl.textContent = "🟢 Connected";
    }
    if (btnListen) {
        btnListen.disabled = false;
    }
    ws.send(JSON.stringify({ type: "get_full_list" }));
    log("Connected. Click Start listening to hear TesterAgent (Opus test client).");
};

ws.onclose = function () {
    if (statusEl) {
        statusEl.textContent = "🔴 Disconnected";
    }
    if (btnListen) {
        btnListen.disabled = true;
    }
};

ws.onmessage = async function (event) {
    if (event.data instanceof Blob) {
        if (!listening || !audioContext) return;
        if (audioContext.state === "suspended") audioContext.resume();
        const buffer = await event.data.arrayBuffer();
        if (Math.random() < 0.02) log("🔊 RX " + buffer.byteLength + " bytes");
        const int16 = new Int16Array(buffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;
        const audioBuffer = audioContext.createBuffer(1, float32.length, SAMPLE_RATE);
        audioBuffer.copyToChannel(float32, 0);
        const source = audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(audioContext.destination);
        source.start();
        return;
    }
    try {
        const msg = JSON.parse(event.data);
        if (msg.type === "agent_list") {
            const list =
                (msg.agents || [])
                    .map(a => a.agent_id)
                    .join(", ") || "none";
            log("Agents: " + list);
        }
        if (msg.type === "listening_started") {
            log("✅ Listening to " + (msg.agent_id || "agent"));
        }
    } catch (_e) {
        // ignore parse errors
    }
};

if (btnListen) {
    btnListen.onclick = function () {
        if (listening) {
            ws.send(JSON.stringify({ type: "stop_listening" }));
            listening = false;
            btnListen.textContent = "🎧 Start listening to TesterAgent";
            if (audioContext) audioContext.suspend();
            log("Stopped listening.");
        } else {
            if (!audioContext) {
                audioContext = new (window.AudioContext || window.webkitAudioContext)({
                    sampleRate: SAMPLE_RATE
                });
            }
            audioContext.resume().then(function () {
                ws.send(JSON.stringify({ type: "listen_to_agent", agent_id: "TesterAgent" }));
                listening = true;
                btnListen.textContent = "🛑 Stop listening";
                log("Listening to TesterAgent (run opus_tester.py to send audio).");
            });
        }
    };
}

