"""Central server: FastAPI + WebSocket for audio streaming."""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import json
import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import io
import numpy as np
from typing import Dict, Set, List, Optional, Any
from datetime import datetime
from contextlib import asynccontextmanager
import database
from outlook_manager import OutlookManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
ANNOUNCEMENT_IMAGES_DIR = os.path.join(BASE_DIR, "announcement_images")


_app_loop = None


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    """Init before accepting connections."""
    global outlook_manager, _app_loop
    _app_loop = asyncio.get_running_loop()
    print("Initializing Outlook Manager...")
    outlook_manager = OutlookManager()
    threading.Thread(target=_preload_amd_background, daemon=True).start()
    try:
        from livekit_worker import run_worker_thread
        threading.Thread(target=run_worker_thread, daemon=True).start()
        print("[LiveKit] AMD worker thread started")
    except ImportError as e:
        print(f"[LiveKit] Worker not started (missing deps): {e}")
    scheduler_thread = threading.Thread(target=callback_scheduler, daemon=True)
    scheduler_thread.start()
    print("Callback scheduler thread started")
    hostname = socket.gethostname()
    try:
        all_ips = socket.gethostbyname_ex(hostname)[2]
        local_ips = [ip for ip in all_ips if ip.startswith(("192.168.", "10.", "172."))]
    except socket.gaierror:
        local_ips = []
    print("-" * 60)
    print("TeamsPowerDialer Server Starting...")
    print(f"   Host Name: {hostname}")
    print("   Uvicorn running directly on port 80")
    if local_ips:
        print("   Access at:")
        for ip in local_ips:
            print(f"   - http://{ip} (Dashboard)")
            print(f"   - ws://{ip}/ws (WebSocket for agents)")
    else:
        print("   Access: http://localhost")
    print("-" * 60)
    yield
    print("Server shutting down...")


app = FastAPI(title="TeamsPowerDialer Central Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.isdir(ANNOUNCEMENT_IMAGES_DIR):
    app.mount("/static/announcements", StaticFiles(directory=ANNOUNCEMENT_IMAGES_DIR), name="announcement_images")

_amd_detector = None
_amd_load_error = None
_ai_deps_ensured = False

def _ensure_ai_dependencies():
    """Install AMD deps (torch, faster-whisper) if missing."""
    global _ai_deps_ensured
    if _ai_deps_ensured:
        return
    _ai_deps_ensured = True
    missing = []
    try:
        import torch  # noqa: F401
    except ImportError:
        missing.append("torch")
    try:
        import torchaudio  # noqa: F401
    except ImportError:
        missing.append("torchaudio")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        missing.append("faster-whisper")
    if missing:
        print(f"[AI] Installing missing dependencies: {missing}")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
                capture_output=True,
                timeout=300,
                cwd=BASE_DIR,
            )
            print(f"[AI] Installed: {missing}")
        except Exception as e:
            print(f"[AI] pip install failed: {e}")

def get_amd_detector():
    """Lazy-load AMD detector. Returns (detector, error)."""
    global _amd_detector, _amd_load_error
    if _amd_detector is not None:
        return _amd_detector, None
    if _amd_load_error is not None:
        return None, _amd_load_error
    _ensure_ai_dependencies()
    try:
        from ai_core import AMDDetector
        _amd_detector = AMDDetector()
        print("[AI] AMD detector (Silero VAD + faster-whisper) ready.")
        return _amd_detector, None
    except Exception as e:
        _amd_load_error = str(e)
        print(f"[AI] AMD detector failed to load (monitoring disabled): {e}")
        return None, _amd_load_error

# Global Outlook manager - initialized in lifespan before accepting connections
outlook_manager: Optional[OutlookManager] = None


def _preload_amd_background():
    """Pre-load AMD detector in background so first call does not block."""
    get_amd_detector()


async def _send_callback_prompts():
    """Async helper to send callback prompts to connected agents"""
    due_callbacks = database.get_due_callbacks()
    
    for callback in due_callbacks:
        agent_id = callback.get("agent_id")
        if agent_id in manager.agent_connections:
            websocket = manager.agent_connections[agent_id]
            try:
                # Extract contact info from JSON contact_data if available
                contact_name = ""
                contact_phone = ""
                contact_data_str = callback.get("contact_data", "{}")
                if contact_data_str:
                    try:
                        contact_data = json.loads(contact_data_str)
                        first_name = contact_data.get('First Name', '')
                        last_name = contact_data.get('Last Name', '')
                        contact_name = f"{first_name} {last_name}".strip()
                        phones = contact_data.get('Phones', [])
                        contact_phone = phones[0] if phones else ""
                    except:
                        pass
                
                await websocket.send_json({
                    "type": "show_callback_prompt",
                    "callback_id": callback.get("id"),
                    "contact_name": contact_name,
                    "contact_phone": contact_phone,
                    "notes": callback.get("notes", ""),
                    "scheduled_time": callback.get("scheduled_time")
                })
                print(f"Sent callback prompt to agent {agent_id} for callback {callback.get('id')}")
            except Exception as e:
                print(f"Error sending callback prompt to agent {agent_id}: {e}")

def callback_scheduler():
    """Background thread that checks for due callbacks every 60 seconds"""
    while True:
        try:
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Run the async function
            loop.run_until_complete(_send_callback_prompts())
            
            loop.close()
            
            # Sleep for 60 seconds
            time.sleep(60)
        except Exception as e:
            print(f"Error in callback scheduler: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)


class ConnectionManager:
    """Manages WebSocket connections for agents and admins"""
    
    def __init__(self):
        # Store agent connections: {agent_id: websocket}
        self.agent_connections: Dict[str, WebSocket] = {}
        
        # Store admin connections: {admin_id: websocket}
        self.admin_connections: Dict[str, WebSocket] = {}
        
        # Store which admin is listening to which agent: {admin_id: agent_id}
        self.admin_listening: Dict[str, str] = {}
        
        # Store agent metadata: {agent_id: {status, last_seen, etc}}
        self.agent_metadata: Dict[str, Dict] = {}
    
    async def connect_agent(self, agent_id: str, websocket: WebSocket):
        """Register an agent connection"""
        await websocket.accept()
        self.agent_connections[agent_id] = websocket
        self.agent_metadata[agent_id] = {
            "status": "connected",
            "last_seen": datetime.now().isoformat(),
            "connected_at": datetime.now().isoformat()
        }
        print(f"[CONN] Agent connected: {agent_id}")
        
        # Notify all admins of new agent
        await self.broadcast_to_admins({
            "type": "agent_connected",
            "agent_id": agent_id,
            "metadata": self.agent_metadata[agent_id]
        })
    
    async def disconnect_agent(self, agent_id: str):
        """Remove an agent connection"""
        if agent_id in self.agent_connections:
            del self.agent_connections[agent_id]
        
        if agent_id in self.agent_metadata:
            self.agent_metadata[agent_id]["status"] = "disconnected"
            self.agent_metadata[agent_id]["last_seen"] = datetime.now().isoformat()
        
        # Notify admins
        await self.broadcast_to_admins({
            "type": "agent_disconnected",
            "agent_id": agent_id
        })
        
        # Remove from admin listening if any admin was listening
        for admin_id, listening_agent in list(self.admin_listening.items()):
            if listening_agent == agent_id:
                del self.admin_listening[admin_id]
        
        print(f"[CONN] Agent disconnected: {agent_id}")
    
    async def connect_admin(self, admin_id: str, websocket: WebSocket):
        """Register an admin connection"""
        await websocket.accept()
        self.admin_connections[admin_id] = websocket
        print(f"Admin connected: {admin_id}")
        
        # Send current agent list to new admin
        await websocket.send_json({
            "type": "agent_list",
            "agents": self.get_agent_list()
        })
    
    async def disconnect_admin(self, admin_id: str):
        """Remove an admin connection"""
        if admin_id in self.admin_connections:
            del self.admin_connections[admin_id]
        
        if admin_id in self.admin_listening:
            del self.admin_listening[admin_id]
        
        print(f"Admin disconnected: {admin_id}")
    
    async def broadcast_audio_to_admin(self, agent_id: str, audio_bytes: bytes):
        """Broadcast audio from an agent to the admin listening to that agent"""
        # Iterate over COPY of items to prevent runtime errors during disconnects
        for admin_id, listening_agent in list(self.admin_listening.items()):
            if listening_agent == agent_id:
                if admin_id in self.admin_connections:
                    try:
                        await self.admin_connections[admin_id].send_bytes(audio_bytes)
                    except Exception:
                        # Silently handle disconnects here to avoid crashing the agent loop
                        await self.disconnect_admin(admin_id)
    
    async def broadcast_to_admins(self, message: dict):
        """Broadcast a JSON message to all connected admins"""
        disconnected = []
        for admin_id, websocket in self.admin_connections.items():
            try:
                await websocket.send_json(message)
            except Exception as e:
                print(f"Error broadcasting to admin {admin_id}: {e}")
                disconnected.append(admin_id)
        
        # Clean up disconnected admins
        for admin_id in disconnected:
            await self.disconnect_admin(admin_id)

    async def broadcast_to_agents(self, message: dict):
        """Broadcast a JSON message to all connected agents (Installer clients)"""
        disconnected = []
        for agent_id, websocket in list(self.agent_connections.items()):
            try:
                await websocket.send_json(message)
            except Exception as e:
                print(f"Error broadcasting to agent {agent_id}: {e}")
                disconnected.append(agent_id)
        for agent_id in disconnected:
            await self.disconnect_agent(agent_id)
    
    async def update_agent_metadata(self, agent_id: str, metadata: dict):
        """Update agent metadata"""
        if agent_id in self.agent_metadata:
            self.agent_metadata[agent_id].update(metadata)
            self.agent_metadata[agent_id]["last_seen"] = datetime.now().isoformat()
        else:
            self.agent_metadata[agent_id] = metadata
            self.agent_metadata[agent_id]["last_seen"] = datetime.now().isoformat()
        
        # Notify admins
        await self.broadcast_to_admins({
            "type": "agent_metadata_update",
            "agent_id": agent_id,
            "metadata": self.agent_metadata[agent_id]
        })
    
    def get_agent_list(self) -> List[Dict]:
        """Get list of all agents with their metadata"""
        agents = []
        for agent_id, metadata in self.agent_metadata.items():
            agent_info = {
                "agent_id": agent_id,
                "status": metadata.get("status", "unknown"),
                "last_seen": metadata.get("last_seen"),
                "connected_at": metadata.get("connected_at")
            }
            agents.append(agent_info)
        return agents
    
    def set_admin_listening(self, admin_id: str, agent_id: str):
        """Set which agent an admin is listening to"""
        self.admin_listening[admin_id] = agent_id


# Global connection manager
manager = ConnectionManager()


class AnnouncementRequest(BaseModel):
    message: str
    image: Optional[str] = None  # Filename from announcement_images/ or null for none


@app.post("/api/control/shutdown")
async def shutdown_endpoint(request: Request):
    """Graceful shutdown: localhost-only. Sends SIGTERM so uvicorn drains connections."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    os.kill(os.getpid(), signal.SIGTERM)
    return {"ok": True}


@app.post("/api/control/announcement")
async def announcement_endpoint(request: Request, body: AnnouncementRequest):
    """Send announcement to all connected agents. Localhost-only."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    await manager.broadcast_to_agents({
        "type": "announcement",
        "message": body.message,
        "image": body.image,
    })
    return {"ok": True, "sent_to": len(manager.agent_connections)}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Admin monitor dashboard"""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.websocket("/ws/{agent_id}")
async def websocket_agent(websocket: WebSocket, agent_id: str):
    """WebSocket endpoint for agents to send audio and metadata"""
    import database  # Import at function level to ensure availability
    await manager.connect_agent(agent_id, websocket)
    
    amd_state = None
    call_start_time = 0
    is_ringing_state = False
    ai_available_for_call = False
    amd_last_log_elapsed = -1  # For periodic "still listening" logs
    RING_TIMEOUT = 30
    audio_packets_received = 0  # Diagnostic: track audio reception
    
    try:
        while True:
            # Use 1s timeout so we can send force_hangup even when no audio arrives
            try:
                data = await asyncio.wait_for(websocket.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                # No message in 1s - check no-audio timeout (server-driven hangup)
                if is_ringing_state and call_start_time > 0:
                    elapsed = time.time() - call_start_time
                    if elapsed > RING_TIMEOUT:
                        if ai_available_for_call:
                            print(f"[AI] Agent {agent_id}: No-audio timeout ({RING_TIMEOUT}s). Sending hangup.")
                            try:
                                await websocket.send_json({"type": "force_hangup", "reason": "timeout"})
                            except Exception:
                                pass
                        # When AMD unavailable, do NOT send force_hangup—cannot verify no-answer, would risk hanging up on live person
                        is_ringing_state = False
                        amd_state = None
                        amd_last_log_elapsed = -1
                continue
            
            # Handle Mixed Types: Use receive() to get both text and bytes
            try:
                
                # Check for disconnect event explicitly
                if data["type"] == "websocket.disconnect":
                    code = data.get("code", 1000)
                    # Only log non-normal disconnects (1000/1001 are normal)
                    if code not in [1000, 1001]:
                        print(f"Agent {agent_id} disconnected. Code: {code}")
                    break
                
                if "text" in data:
                    try:
                        raw_text = data["text"]
                        message = json.loads(raw_text)
                        
                        if message.get("type") == "call_started":
                            print(f"[RECV] Agent {agent_id}: call_started received")
                            detector, load_err = get_amd_detector()
                            ai_available_for_call = detector is not None
                            if not ai_available_for_call:
                                await websocket.send_json({"type": "ai_unavailable", "reason": load_err or "AI not loaded"})
                                print(f"[AI] Agent {agent_id}: Call started. AMD unavailable - no server-driven hangup (client manual mode).")
                            else:
                                from ai_core import AMDState
                                amd_state = AMDState(detector)
                                print(f"[AI] Agent {agent_id}: Call started. AMD monitoring active (VAD + Whisper).")
                            is_ringing_state = True
                            call_start_time = time.time()
                        
                        elif message.get("status") == "Call Ended":
                            is_ringing_state = False
                            amd_state = None
                            amd_last_log_elapsed = -1
                            if audio_packets_received > 0:
                                print(f"[RECV] Agent {agent_id}: call ended (total {audio_packets_received} audio packets received)")
                            audio_packets_received = 0
                            print(f"[AI] Agent {agent_id}: Call ended")
                        
                        # Handle metadata updates
                        elif message.get("type") == "metadata":
                            metadata_data = message.get("data", {})
                            await manager.update_agent_metadata(agent_id, metadata_data)
                            
                            # If metadata includes call status, broadcast stats update to admins
                            if "status" in metadata_data and metadata_data.get("status") == "Calling":
                                # Get fresh stats when call starts
                                new_stats = database.get_agent_stats(agent_id)
                                await manager.broadcast_to_admins({
                                    "type": "stats_update",
                                    "agent_id": agent_id,
                                    "stats": new_stats,
                                })
                        
                        # Handle callback operations
                        elif message.get("type") == "create_callback":
                            scheduled_time = message.get("scheduled_time")
                            notes = message.get("notes", "")
                            source_workbook_path = message.get("source_workbook_path")
                            source_sheet_name = message.get("source_sheet_name")
                            source_row_index = message.get("source_row_index")
                            contact_data = message.get("contact_data", "{}")
                            agent_email = message.get("agent_email")
                            
                            # Save to database
                            callback_id = database.create_callback(
                                agent_id=agent_id,
                                scheduled_time=scheduled_time,
                                notes=notes,
                                source_workbook_path=source_workbook_path,
                                source_sheet_name=source_sheet_name,
                                source_row_index=source_row_index,
                                contact_data=contact_data
                            )
                            
                            await websocket.send_json({
                                "type": "callback_created",
                                "callback_id": callback_id
                            })
                            
                            if agent_email:
                                print(f"Creating Outlook event for {agent_id} at {agent_email}...")
                                # Parse contact details for the email body
                                try:
                                    c_data = json.loads(contact_data)
                                    first = c_data.get('First Name', '')
                                    last = c_data.get('Last Name', '')
                                    company = c_data.get('Company', '')
                                    phones = ", ".join([str(p) for p in c_data.get('Phones', [])])
                                    emails = c_data.get('Emails', [])
                                    contact_emails = ", ".join([str(e) for e in emails]) if emails else "None"
                                    
                                    subject = f"Callback: {first} {last} ({company})"
                                    body = f"""
                                    <h3>Callback Reminder</h3>
                                    <p><b>Contact:</b> {first} {last}</p>
                                    <p><b>Company:</b> {company}</p>
                                    <p><b>Phone(s):</b> {phones}</p>
                                    <p><b>Email(s):</b> {contact_emails}</p>
                                    <p><b>Notes:</b> {notes}</p>
                                    """
                                    
                                    # Run in background thread to not block WebSocket
                                    threading.Thread(
                                        target=outlook_manager.create_event,
                                        args=(subject, body, scheduled_time, agent_email, message.get("timezone", "ET"), emails),
                                        daemon=True
                                    ).start()
                                    print(f"✅ Outlook invite thread started for {agent_id}")
                                    
                                except json.JSONDecodeError as json_e:
                                    print(f"❌ ERROR: Failed to parse contact_data JSON for Outlook invite: {json_e}")
                                    print(f"   Raw contact_data: {contact_data[:200]}...")  # First 200 chars
                                except Exception as e:
                                    print(f"❌ ERROR: Failed to prepare Outlook invite: {e}")
                                    import traceback
                                    traceback.print_exc()
                            else:
                                print("Outlook invite skipped: No agent_email provided.")
                            
                            # Log callback creation
                            contact_data_str = message.get("contact_data", "{}")
                            try:
                                contact_data_dict = json.loads(contact_data_str)
                            except Exception:
                                contact_data_dict = {}
                            first_name = contact_data_dict.get("First Name", "")
                            last_name = contact_data_dict.get("Last Name", "")
                            contact_name_for_log = f"{first_name} {last_name}".strip() or "Unknown Contact"
                            print(f"[DISPO] Agent {agent_id} created callback {callback_id} for contact {contact_name_for_log}")
                        
                        elif message.get("type") == "update_callback":
                            callback_id = message.get("callback_id")
                            new_scheduled_time = message.get("scheduled_time")
                            database.update_callback_time(callback_id, new_scheduled_time)
                            
                            # Send new reminder email if agent_email is provided
                            agent_email = message.get("agent_email")
                            if agent_email:
                                print(f"Rescheduling Outlook event for {agent_id} at {agent_email}...")
                                try:
                                    # Get contact_data from message or fetch from callback record
                                    contact_data = message.get("contact_data")
                                    callback_record = None
                                    if not contact_data:
                                        # Try to get from callback record if available
                                        callback_record = database.get_callback(callback_id)
                                        if callback_record:
                                            contact_data = callback_record.get("contact_data", "{}")
                                    
                                    if contact_data:
                                        c_data = json.loads(contact_data) if isinstance(contact_data, str) else contact_data
                                        first = c_data.get('First Name', '')
                                        last = c_data.get('Last Name', '')
                                        company = c_data.get('Company', '')
                                        phones = ", ".join([str(p) for p in c_data.get('Phones', [])])
                                        emails = c_data.get('Emails', [])
                                        contact_emails = ", ".join([str(e) for e in emails]) if emails else "None"
                                        notes = message.get("notes") or (callback_record.get("notes") if callback_record else "")
                                        
                                        subject = f"Callback: {first} {last} ({company})"
                                        body = f"""
                                        <h3>Callback Reminder (Rescheduled)</h3>
                                        <p><b>Contact:</b> {first} {last}</p>
                                        <p><b>Company:</b> {company}</p>
                                        <p><b>Phone(s):</b> {phones}</p>
                                        <p><b>Email(s):</b> {contact_emails}</p>
                                        <p><b>Notes:</b> {notes}</p>
                                        """
                                        
                                        # Run in background thread to not block WebSocket
                                        threading.Thread(
                                            target=outlook_manager.create_event,
                                            args=(subject, body, new_scheduled_time, agent_email, message.get("timezone", "ET"), emails),
                                            daemon=True
                                        ).start()
                                except Exception as e:
                                    print(f"Error preparing Outlook invite for rescheduled callback: {e}")
                            
                            await websocket.send_json({
                                "type": "callback_updated",
                                "callback_id": callback_id
                            })
                            print(f"Agent {agent_id} updated callback {callback_id}")
                        
                        elif message.get("type") == "cancel_callback":
                            callback_id = message.get("callback_id")
                            database.cancel_callback(callback_id)
                            await websocket.send_json({
                                "type": "callback_cancelled",
                                "callback_id": callback_id
                            })
                            print(f"Agent {agent_id} cancelled callback {callback_id}")
                        
                        elif message.get("type") == "request_overdue_callbacks":
                            overdue = database.get_overdue_callbacks(agent_id)
                            await websocket.send_json({
                                "type": "overdue_callbacks_list",
                                "callbacks": overdue
                            })
                            print(f"Sent {len(overdue)} overdue callbacks to agent {agent_id}")
                        
                        elif message.get("type") == "log_manual_callback":
                            callback_id = message.get("callback_id")
                            disposition_id = message.get("disposition_id", "Manual Callback")
                            # Mark callback as completed
                            database.complete_callback(callback_id)
                            # Log the call (get callback details first)
                            callback = database.get_callback(callback_id)
                            if callback:
                                # Extract phone from contact_data JSON if available
                                phone = ""
                                contact_data_str = callback.get("contact_data", "{}")
                                if contact_data_str:
                                    try:
                                        contact_data = json.loads(contact_data_str)
                                        phones = contact_data.get('Phones', [])
                                        phone = phones[0] if phones else ""
                                    except:
                                        pass
                                
                                database.log_call({
                                    "agent": agent_id,
                                    "phone": phone,
                                    "disposition": disposition_id,
                                    "duration": 0
                                })
                            await websocket.send_json({
                                "type": "callback_logged",
                                "callback_id": callback_id
                            })
                            print(f"[DISPO] Agent {agent_id} logged manual callback {callback_id}")

                        elif message.get("type") == "log_disposition":
                            try:
                                # Create new call record (e.g. when call ends with "No Disposition")
                                disposition = message.get("disposition", "Unknown")
                                phone = message.get("phone", "")
                                contact_name = message.get("contact_name", "")
                                contact_data = message.get("contact_data")
                                
                                database.log_disposition(
                                    {
                                        "agent_id": agent_id,
                                        "phone": phone,
                                        "contact_name": contact_name,
                                        "disposition": disposition,
                                        "duration": message.get("duration", 0),
                                        "notes": message.get("notes", ""),
                                        "campaign_mode": message.get("campaign_mode", "General"),
                                        "contact_data": contact_data,
                                    }
                                )

                                # 2. Get Updated Stats for this agent
                                new_stats = database.get_agent_stats(agent_id)

                                # 3. Broadcast update to Admins immediately
                                await manager.broadcast_to_admins(
                                    {
                                        "type": "stats_update",
                                        "agent_id": agent_id,
                                        "stats": new_stats,
                                    }
                                )
                                print(
                                    f"[DISPO] Logged call for {agent_id}: {disposition} | Phone: {phone} | Contact: {contact_name} | "
                                    f"Stats: Today={new_stats.get('calls_today', 0)}, Month={new_stats.get('calls_month', 0)}"
                                )
                            except Exception as db_err:
                                print(f"CRITICAL DB ERROR for {agent_id}: {db_err}")
                                import traceback
                                traceback.print_exc()

                        elif message.get("type") == "update_disposition":
                            try:
                                # Update existing call's disposition (user clicked disposition after call ended)
                                # Only sent when client actually made a call; no fallback INSERT to avoid phantom calls
                                disposition = message.get("disposition", "Unknown")
                                phone = message.get("phone", "")
                                contact_name = message.get("contact_name", "")
                                notes = message.get("notes", "")
                                contact_data = message.get("contact_data")
                                updated = database.update_call_disposition(
                                    {
                                        "agent_id": agent_id,
                                        "phone": phone,
                                        "contact_name": contact_name,
                                        "disposition": disposition,
                                        "notes": notes,
                                        "contact_data": contact_data,
                                    }
                                )

                                # 2. Get Updated Stats for this agent
                                new_stats = database.get_agent_stats(agent_id)

                                # 3. Broadcast update to Admins immediately
                                await manager.broadcast_to_admins(
                                    {
                                        "type": "stats_update",
                                        "agent_id": agent_id,
                                        "stats": new_stats,
                                    }
                                )
                                action = "Updated disposition" if updated else "No matching call (skipped - no call was made)"
                                print(
                                    f"[DISPO] {action} for {agent_id}: {disposition} | Phone: {phone} | "
                                    f"Stats: Today={new_stats.get('calls_today', 0)}, Month={new_stats.get('calls_month', 0)}"
                                )
                            except Exception as db_err:
                                print(f"CRITICAL DB ERROR for {agent_id}: {db_err}")
                                import traceback
                                traceback.print_exc()
                    
                    except json.JSONDecodeError as e:
                        print(f"Error parsing JSON from agent {agent_id}: {e}")
                    except Exception as e:
                        print(f"Logic error processing text from {agent_id}: {e}")
                
                elif "bytes" in data:
                    try:
                        audio_bytes = data["bytes"]
                        audio_packets_received += 1
                        # Diagnostic: log first packet and every 100th packet
                        if audio_packets_received == 1 or audio_packets_received % 100 == 0:
                            print(f"[RECV] Agent {agent_id}: audio received (packet #{audio_packets_received}, {len(audio_bytes)} bytes)")
                        # Ping back to client every 50 packets so client can confirm server is receiving in real-time
                        if audio_packets_received % 50 == 0:
                            try:
                                await websocket.send_json({"type": "audio_ack", "packets_received": audio_packets_received})
                            except Exception:
                                pass
                        
                        # 1. Validate Audio Data
                        audio_chunk_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
                        
                        system_channel = None
                        dashboard_audio = audio_bytes 
                        
                        # 2. Process Stereo
                        if len(audio_chunk_int16) % 2 == 0:
                            stereo = audio_chunk_int16.reshape(-1, 2)
                            system_channel = stereo[:, 1]
                            mono_mix = np.mean(stereo, axis=1).astype(np.int16)
                            dashboard_audio = mono_mix.tobytes()
                        else:
                            system_channel = None
                        
                        # 3. Broadcast to Admin
                        await manager.broadcast_audio_to_admin(agent_id, dashboard_audio)
                        
                        # 4. AMD Processing (VAD + Whisper, non-blocking, fail-safe to human)
                        if is_ringing_state and amd_state is not None:
                            elapsed = time.time() - call_start_time
                            elapsed_int = int(elapsed)
                            if system_channel is None:
                                if elapsed_int != amd_last_log_elapsed and elapsed_int > 0 and elapsed_int % 5 == 0:
                                    amd_last_log_elapsed = elapsed_int
                                    print(f"[AI] Agent {agent_id}: AMD waiting - no stereo audio ({elapsed_int}s). Is loopback streaming?")
                            else:
                                try:
                                    system_float = system_channel.astype(np.float32) / 32768.0
                                    # Diagnostic: RMS of both channels - channel0=mic, channel1=loopback (used for AMD)
                                    # If loopback RMS ~0 but mic RMS > 0, possible channel swap or wrong loopback device
                                    rms_ch0 = float(np.sqrt(np.mean((stereo[:, 0].astype(np.float32) / 32768.0) ** 2)))
                                    rms_ch1 = float(np.sqrt(np.mean(system_float ** 2)))
                                    if not hasattr(amd_state, "_rms_log_ctr"):
                                        amd_state._rms_log_ctr = 0
                                    amd_state._rms_log_ctr += 1
                                    if amd_state._rms_log_ctr % 50 == 0:
                                        print(f"[AI] AMD DEBUG: ch0(mic) RMS={rms_ch0:.4f}, ch1(loopback) RMS={rms_ch1:.4f}")

                                    # Log first audio + periodic "still listening" every 5s
                                    if amd_last_log_elapsed == -1 and elapsed_int >= 0:
                                        amd_last_log_elapsed = 0
                                        print(f"[AI] Agent {agent_id}: AMD receiving stereo audio, listening for speech...")
                                    elif elapsed_int != amd_last_log_elapsed and elapsed_int > 0 and elapsed_int % 5 == 0:
                                        amd_last_log_elapsed = elapsed_int
                                        print(f"[AI] Agent {agent_id}: AMD listening... {elapsed_int}s elapsed, waiting for speech")

                                    if elapsed > RING_TIMEOUT:
                                        if ai_available_for_call:
                                            print(f"[AI] Agent {agent_id}: Timeout ({RING_TIMEOUT}s). Sending hangup.")
                                            await websocket.send_json({"type": "force_hangup", "reason": "timeout"})
                                        # When AMD unavailable, do NOT send force_hangup—cannot verify no-answer, would risk hanging up on live person
                                        is_ringing_state = False
                                        amd_state = None
                                        amd_last_log_elapsed = -1
                                    else:
                                        result = amd_state.process_chunk(system_float, elapsed + call_start_time)
                                        if result is not None:
                                            if result[0] == "human_detected":
                                                print(f"[AI] Agent {agent_id}: Human detected (VAD).")
                                                is_ringing_state = False
                                                amd_state = None
                                                amd_last_log_elapsed = -1
                                                await websocket.send_json({"type": "human_detected"})
                                            elif result[0] == "force_hangup":
                                                reason = result[1]
                                                print(f"[AI] Agent {agent_id}: {reason}. Sending hangup.")
                                                is_ringing_state = False
                                                amd_state = None
                                                amd_last_log_elapsed = -1
                                                await websocket.send_json({"type": "force_hangup", "reason": reason})
                                            elif result[0] == "transcribe":
                                                _, buf, dur = result
                                                print(f"[AI] Agent {agent_id}: Speech buffered (5s), transcribing...")
                                                try:
                                                    from ai_core import analyze_buffered_audio
                                                    detector = amd_state.detector
                                                    action, reason = await asyncio.wait_for(
                                                        asyncio.to_thread(analyze_buffered_audio, detector, buf, dur),
                                                        timeout=10.0
                                                    )
                                                    if action == "human_detected":
                                                        print(f"[AI] Agent {agent_id}: Human detected (greeting).")
                                                        is_ringing_state = False
                                                        amd_state = None
                                                        amd_last_log_elapsed = -1
                                                        await websocket.send_json({"type": "human_detected"})
                                                    elif action == "continue":
                                                        # Non-speech/unintelligible: reset AMD, keep waiting for real voice
                                                        print(f"[AI] Agent {agent_id}: {reason}. Resetting, will keep waiting up to timeout.")
                                                        amd_state.reset()
                                                    else:
                                                        print(f"[AI] Agent {agent_id}: {reason}. Sending hangup.")
                                                        is_ringing_state = False
                                                        amd_state = None
                                                        amd_last_log_elapsed = -1
                                                        await websocket.send_json({"type": "force_hangup", "reason": reason})
                                                except asyncio.TimeoutError:
                                                    print(f"[AI] Agent {agent_id}: Transcription timeout (fail-safe: keep call)")
                                                except Exception as ai_err:
                                                    print(f"[AI] Agent {agent_id}: AMD analysis error (fail-safe: keep call): {ai_err}")
                                except Exception as buf_err:
                                    print(f"[AI] Agent {agent_id}: AMD buffer error (skipped): {buf_err}")
                        
                        await manager.update_agent_metadata(agent_id, {"last_audio": datetime.now().isoformat()})
                    except Exception as audio_err:
                        print(f"[AUDIO] Agent {agent_id}: Audio processing error (continuing): {audio_err}")
                        
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error processing frame from {agent_id}: {e}")
                continue
    
    except WebSocketDisconnect:
        await manager.disconnect_agent(agent_id)
    except OSError as e:
        winerr = getattr(e, "winerror", None)
        if winerr == 121:
            print(f"[NET] Agent {agent_id}: WinError 121 (semaphore timeout) - data transfer failed, connection closed")
        print(f"Critical WebSocket error for agent {agent_id}: {e}")
        await manager.disconnect_agent(agent_id)
    except Exception as e:
        print(f"Critical WebSocket error for agent {agent_id}: {e}")
        await manager.disconnect_agent(agent_id)


@app.websocket("/ws/admin/{admin_id}")
async def websocket_admin(websocket: WebSocket, admin_id: str):
    """WebSocket endpoint for admin clients to receive audio streams"""
    await manager.connect_admin(admin_id, websocket)
    print(f"Admin {admin_id} connected to WebSocket")
    
    try:
        while True:
            # Receive commands from admin (e.g., start/stop listening)
            data = await websocket.receive()

            if "text" in data:
                try:
                    message = json.loads(data["text"])
                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON from admin {admin_id}: {e}")
                    continue

                cmd_type = message.get("type")
                print(f"Admin {admin_id} sent command: {cmd_type}")
            
                if cmd_type == "listen_to_agent":
                    agent_id = message.get("agent_id")
                    print(f"Admin {admin_id} wants to listen to agent {agent_id}")
                    manager.set_admin_listening(admin_id, agent_id)
                    await websocket.send_json({
                        "type": "listening_started",
                        "agent_id": agent_id
                    })
                    print(f"Admin {admin_id} is now listening to agent {agent_id}")
            
                elif cmd_type == "stop_listening":
                    if admin_id in manager.admin_listening:
                        del manager.admin_listening[admin_id]
                    await websocket.send_json({
                        "type": "listening_stopped"
                    })
                    print(f"Admin {admin_id} stopped listening")
                
                elif cmd_type == "get_full_list":
                    agent_list = manager.get_agent_list()
                    await websocket.send_json({
                        "type": "agent_list",
                        "agents": agent_list
                    })
                    print(f"Sent agent list to admin {admin_id}: {len(agent_list)} agents")

            elif "bytes" in data:
                # Admins should not send binary data, log and ignore
                print(f"Admin {admin_id} sent unexpected binary data.")
    
    except WebSocketDisconnect:
        print(f"Admin {admin_id} disconnected")
        await manager.disconnect_admin(admin_id)
    except Exception as e:
        print(f"WebSocket error for admin {admin_id}: {e}")
        await manager.disconnect_admin(admin_id)


@app.get("/api/agents")
async def get_agents():
    """Get list of all active agents"""
    return {
        "agents": manager.get_agent_list(),
        "total": len(manager.agent_connections)
    }


@app.get("/api/status")
async def get_status():
    """Get current system status"""
    return {
        "status": "ok",
        "active_agents": len(manager.agent_connections),
        "active_admins": len(manager.admin_connections),
        "timestamp": datetime.now().isoformat()
    }


def _introspect_task(task) -> dict:
    """Safely extract task info for debugging. Returns dict with name, coro, done, etc."""
    out = {"done": getattr(task, "done", lambda: False)()}
    try:
        out["name"] = task.get_name() or "(unnamed)"
    except Exception:
        out["name"] = "(unknown)"
    out["file"] = None
    out["line"] = None
    try:
        coro = task.get_coro()
        if coro:
            out["coro"] = getattr(coro, "__qualname__", None) or getattr(coro, "__name__", None)
            if not out["coro"]:
                out["coro"] = getattr(getattr(coro, "cr_code", None), "co_name", None) or repr(coro)[:60]
            # Safe file/line from code object (no frame access)
            code = getattr(coro, "cr_code", None)
            if code:
                fn = getattr(code, "co_filename", None)
                out["file"] = fn.split("\\")[-1].split("/")[-1] if fn else None
                out["line"] = getattr(code, "co_firstlineno", None)
        else:
            out["coro"] = "(no coro)"
    except Exception:
        out["coro"] = "(error)"
    return out


@app.get("/api/debug/status")
async def debug_status():
    """Debug: agents, connections, asyncio task count and task list for visibility"""
    task_list = []
    task_count = -1
    try:
        loop = asyncio.get_running_loop()
        tasks = list(asyncio.all_tasks(loop))
        task_count = len(tasks)
        for i, t in enumerate(tasks):
            info = _introspect_task(t)
            info["index"] = i + 1
            task_list.append(info)
    except Exception:
        pass
    agents = []
    for agent_id in list(manager.agent_connections.keys()):
        meta = manager.agent_metadata.get(agent_id, {})
        agents.append({
            "agent_id": agent_id,
            "status": meta.get("status", "connected"),
            "last_seen": meta.get("last_seen"),
            "last_audio": meta.get("last_audio"),
            "connected_at": meta.get("connected_at"),
        })
    return {
        "agents": agents,
        "active_agents": len(manager.agent_connections),
        "active_admins": len(manager.admin_connections),
        "asyncio_task_count": task_count,
        "asyncio_tasks": task_list,
        "timestamp": datetime.now().isoformat(),
        "database_path": str(database.DB_PATH.resolve()),
    }


@app.post("/api/debug/disconnect_agent/{agent_id}")
async def debug_disconnect_agent(agent_id: str):
    """Debug: force-disconnect an agent's WebSocket (clears their connection/queue)"""
    if agent_id not in manager.agent_connections:
        return {"ok": False, "error": "Agent not connected"}
    ws = manager.agent_connections[agent_id]
    try:
        await ws.close(code=1000)
    except Exception:
        pass
    await manager.disconnect_agent(agent_id)
    print(f"[DEBUG] Force-disconnected agent {agent_id}")
    return {"ok": True}


@app.post("/api/debug/disconnect_all_agents")
async def debug_disconnect_all_agents():
    """Debug: force-disconnect all agent WebSockets"""
    agent_ids = list(manager.agent_connections.keys())
    for aid in agent_ids:
        ws = manager.agent_connections.get(aid)
        if ws:
            try:
                await ws.close(code=1000)
            except Exception:
                pass
            await manager.disconnect_agent(aid)
            print(f"[DEBUG] Force-disconnected agent {aid}")
    return {"ok": True, "disconnected": len(agent_ids)}


@app.get("/api/agent_stats/{agent_id}")
async def get_stats(agent_id: str):
    """API for dashboard to fetch stats on load."""
    return database.get_agent_stats(agent_id)


@app.get("/api/db_agents")
async def get_db_agents():
    """Get list of all unique agents from the database"""
    agents = database.get_all_unique_agents()
    return {"agents": agents}


# Models for call-related APIs
class ManualCallRequest(BaseModel):
    agent_id: str
    count: int


class CallLogCreate(BaseModel):
    """
    DTO for creating a call record.
    - `lead_id` is optional to support manual calls.
    - `phone_number` is always required.
    """
    agent_id: str
    phone_number: str
    lead_id: Optional[str] = None
    campaign_mode: str
    is_manual: bool = False
    contact_name: Optional[str] = None
    notes: Optional[str] = None
    disposition: Optional[str] = None
    duration: Optional[int] = 0


@app.post("/api/admin/add_calls")
async def add_manual_calls(request: ManualCallRequest):
    """
    API endpoint to manually add generic call records for an agent.
    This ensures the server handles the DB write and cache invalidation.
    """
    try:
        # Sanitize ID to match client behavior
        clean_agent_id = request.agent_id.strip()
        now = datetime.now().isoformat()
        
        # We can loop and reuse log_call directly
        for i in range(request.count):
            database.log_call({
                "agent": clean_agent_id,
                "phone": "Manual Entry",
                "contact_name": "Manual Entry",
                "disposition": "Manual Entry",
                "duration": 0,
                "notes": f"Manually added call #{i+1}",
                "timestamp": now,
                "campaign_mode": "General"
            })
            
        # Get updated stats to confirm
        new_stats = database.get_agent_stats(clean_agent_id)
        
        # Broadcast update to admins so dashboard updates live
        await manager.broadcast_to_admins({
            "type": "stats_update",
            "agent_id": clean_agent_id,
            "stats": new_stats,
        })
        
        return {"status": "success", "added": request.count, "new_stats": new_stats}
        
    except Exception as e:
        print(f"Error adding manual calls: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


@app.post("/api/calls", status_code=201)
async def create_call(call: CallLogCreate):
    """Create a call record in the central database."""
    try:
        payload = call.dict()

        # Map DTO to database schema; ensure minimal required fields
        db_data: Dict[str, Any] = {
            "agent_id": call.agent_id,
            "phone": call.phone_number,
            "contact_name": payload.get("contact_name") or "",
            "disposition": payload.get("disposition") or "Pending",
            "duration": int(payload.get("duration") or 0),
            "notes": payload.get("notes") or "",
            "campaign_mode": call.campaign_mode or "General",
        }

        # Persist call log FIRST and return its ID
        call_id = database.create_call_record(db_data)

        return {"id": call_id, "status": "created"}
    except Exception as e:
        print(f"CRITICAL: Failed to create call record: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to create call record"}
        )


@app.get("/api/livekit/token")
async def livekit_token(agent_id: str):
    """
    Generate a LiveKit access token for an agent to join the dispatch room.
    Registers the agent in manager so they appear on the dashboard for Listen.
    """
    try:
        from livekit.api import AccessToken, VideoGrants
        token = AccessToken(
            api_key=os.getenv("LIVEKIT_API_KEY", "devkey"),
            api_secret=os.getenv("LIVEKIT_API_SECRET", "secret"),
        )
        token.with_identity(agent_id).with_grants(
            VideoGrants(room_join=True, room="dispatch-room")
        )
        # Register LiveKit agent for dashboard (so Listen works)
        manager.agent_metadata[agent_id] = {
            "status": "connected",
            "last_seen": datetime.now().isoformat(),
            "connected_at": datetime.now().isoformat(),
        }
        await manager.broadcast_to_admins({
            "type": "agent_connected",
            "agent_id": agent_id,
            "metadata": manager.agent_metadata[agent_id],
        })
        return {"token": token.to_jwt()}
    except ImportError as e:
        return JSONResponse(
            status_code=500,
            content={"error": "LiveKit API not installed", "detail": str(e)},
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to generate token", "detail": str(e)},
        )


@app.get("/api/export/{disposition_type}")
async def export_csv(disposition_type: str, agent: str = None, format: str = None):
    """
    Download export of dispositions (xlsx with bold matching phone, or CSV if format=csv).
    Usage: /api/export/No Response or /api/export/ALL
    Optional: ?agent=AgentName or ?agent=ALL  and  ?format=csv for CSV instead of xlsx.
    """
    from urllib.parse import unquote

    clean_type = unquote(disposition_type)
    agent_filter = unquote(agent) if agent else None
    use_xlsx = (format or "").lower() != "csv"

    if use_xlsx:
        try:
            xlsx_io = database.export_dispositions_to_xlsx(clean_type, agent_id=agent_filter)
            data = xlsx_io.getvalue()
        except Exception:
            data = database.export_dispositions_to_csv(clean_type, agent_id=agent_filter).getvalue().encode("utf-8")
            use_xlsx = False
    else:
        data = database.export_dispositions_to_csv(clean_type, agent_id=agent_filter).getvalue().encode("utf-8")

    filename = f"export_{clean_type}"
    if agent_filter and agent_filter != "ALL":
        safe_agent_name = agent_filter.replace(" ", "_").replace("/", "_")
        filename = f"export_{clean_type}_{safe_agent_name}"
    filename += ".xlsx" if use_xlsx else ".csv"

    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if use_xlsx else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
# BASE_DIR is 'Backend', so we go up one level ('..') then into 'webupdates'
WEB_UPDATES_DIR = os.path.join(BASE_DIR, "..", "webupdates")

@app.get("/version.json")
async def get_version_file():
    """Serve the version file from the webupdates folder (for backwards compatibility with old launchers)."""
    file_path = os.path.join(WEB_UPDATES_DIR, "version.json")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "Version file not found", "version": 0.0}


@app.get("/newversion.json")
async def get_newversion_file():
    """Serve the new version file; updated launchers use this for update checks."""
    file_path = os.path.join(WEB_UPDATES_DIR, "newversion.json")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "Version file not found", "version": "0.0.0"}


@app.get("/client_package.zip")
async def get_update_package():
    """Serve the update zip from the webupdates folder"""
    # Note: launcher.py asks for 'client_package.zip'
    file_path = os.path.join(WEB_UPDATES_DIR, "client_package.zip")
    
    # Fallback: check if you named it 'installer.zip' instead
    if not os.path.exists(file_path):
        file_path = os.path.join(WEB_UPDATES_DIR, "installer.zip")
        
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/zip", filename="client_package.zip")
    return {"error": "Update package not found"}
