"""WebSocket client: streams audio + metadata to server."""

import websockets
import asyncio
import json
import threading
import queue
from typing import Dict, Any, Optional
import numpy as np
import config

try:
    from .message_dispatcher import QtMessageDispatcher
except Exception:
    QtMessageDispatcher = None


class AudioStreamer:
    """WebSocket streamer for audio + metadata."""
    
    def __init__(self, server_url: str, agent_id: str, dispatcher: Optional["QtMessageDispatcher"] = None):
        self.server_url = server_url
        self.agent_id = agent_id
        self.dispatcher = dispatcher
        self.websocket = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5.0  # seconds
        self._backoff_delay = self.reconnect_delay
        
        self.loop = None
        self.loop_thread = None
        self._stop_event = threading.Event()
        
        self.audio_queue = None
        self.streaming_task = None
        
        self.metadata_queue = queue.Queue()
        self.metadata_task = None
        
        self.message_queue = queue.Queue()
        self.message_task = None
        self.message_callbacks = {}
    
    def _run_event_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._async_init())
    
    async def _async_init(self):
        self.metadata_task = asyncio.create_task(self._metadata_sender_loop())
        self.message_task = asyncio.create_task(self._message_receiver_loop())
        
        while not self._stop_event.is_set():
            await asyncio.sleep(0.1)
    
    def start(self):
        if self.loop_thread and self.loop_thread.is_alive():
            return
        
        self._stop_event.clear()
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()
        
        import time
        time.sleep(0.5)
    
    def stop(self):
        self._stop_event.set()
        
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._async_cleanup(), self.loop)
        
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2.0)
    
    async def _async_cleanup(self):
        if self.streaming_task:
            self.streaming_task.cancel()
            try:
                await self.streaming_task
            except asyncio.CancelledError:
                pass
        
        if self.metadata_task:
            self.metadata_task.cancel()
            try:
                await self.metadata_task
            except asyncio.CancelledError:
                pass
        
        if self.message_task:
            self.message_task.cancel()
            try:
                await self.message_task
            except asyncio.CancelledError:
                pass
        
        await self.disconnect()
    
    async def connect(self) -> bool:
        """
        Establish WebSocket connection and handle reconnections.
        
        Returns:
            bool: True if connection successful
        """
        # Idempotent: If already connected, return True immediately
        if self.is_connected and self.websocket is not None:
            try:
                # Quick check: try to ping the connection to verify it's still alive
                pong_waiter = await self.websocket.ping()
                await asyncio.wait_for(pong_waiter, timeout=2)
                return True
            except Exception:
                # Connection is dead, mark as disconnected and reconnect
                self.is_connected = False
                self.websocket = None
        
        # Application-level reconnect loop with exponential backoff so we
        # don't create reconnect storms when the server or network is flaky.
        while not self._stop_event.is_set():
            try:
                # Use agent_id as path parameter (e.g., ws://localhost:8000/ws/agent_001)
                # Extract base URL and append agent_id
                if self.server_url.endswith('/'):
                    url_with_id = f"{self.server_url}{self.agent_id}"
                else:
                    url_with_id = f"{self.server_url}/{self.agent_id}"
                
                self.websocket = await websockets.connect(
                    url_with_id,
                    # Keep the connection alive through typical 60s firewall/load-balancer idle timeouts
                    ping_interval=20,
                    ping_timeout=20,
                )
                
                self.is_connected = True
                self.reconnect_attempts = 0
                self._backoff_delay = self.reconnect_delay  # Reset backoff on success
                print(f"Connected to server: {self.server_url}")
                return True
            
            except Exception as e:
                self.is_connected = False
                self.reconnect_attempts += 1
                
                if self.reconnect_attempts >= self.max_reconnect_attempts:
                    print(f"Max reconnection attempts reached. Failed to connect: {e}")
                    return False
                
                print(f"Connection failed (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts}): {e}")
                print(f"Retrying in {self._backoff_delay:.1f} seconds...")
                await asyncio.sleep(self._backoff_delay)
                # Exponential backoff capped at 30 seconds
                self._backoff_delay = min(self._backoff_delay * 2, 30.0)
        
        return False
    
    async def disconnect(self):
        """Disconnect from server"""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                print(f"Error closing websocket: {e}")
            finally:
                self.websocket = None
                self.is_connected = False
    
    async def _reconnect(self):
        """Handle reconnection logic"""
        await self.disconnect()
        # Use the same exponential backoff strategy as connect()
        await asyncio.sleep(self._backoff_delay)
        return await self.connect()
    
    async def stream_audio(self, audio_queue: queue.Queue):
        """
        Async loop that pulls audio chunks from a queue and sends them as bytes to the server.
        
        Args:
            audio_queue: Queue containing audio chunks (numpy arrays or bytes)
        """
        self.audio_queue = audio_queue
        
        # Ensure connected
        if not self.is_connected:
            if not await self.connect():
                print("Failed to connect, cannot stream audio")
                return
        
        print("Starting audio stream...")
        
        while not self._stop_event.is_set():
            try:
                # Check connection
                if not self.is_connected or self.websocket is None:
                    print("Connection lost, attempting reconnect...")
                    if not await self._reconnect():
                        await asyncio.sleep(self.reconnect_delay)
                        continue
                
                # Get audio chunk from queue (non-blocking)
                try:
                    audio_chunk = audio_queue.get_nowait()
                except queue.Empty:
                    # No audio available, wait a bit
                    await asyncio.sleep(0.01)
                    continue
                
                # Convert numpy array to bytes if needed
                if isinstance(audio_chunk, np.ndarray):
                    # Ensure it's int16 and convert to bytes
                    if audio_chunk.dtype != np.int16:
                        audio_chunk = audio_chunk.astype(np.int16)
                    audio_bytes = audio_chunk.tobytes()
                elif isinstance(audio_chunk, bytes):
                    audio_bytes = audio_chunk
                else:
                    print(f"Unknown audio chunk type: {type(audio_chunk)}")
                    continue
                
                # Send audio as binary message
                try:
                    await self.websocket.send(audio_bytes)
                    # Debug: log occasionally
                    if hasattr(self, '_packet_sent_counter'):
                        self._packet_sent_counter += 1
                        if self._packet_sent_counter % 50 == 0:
                            print(f"[Streamer] Sent {self._packet_sent_counter} audio packets to server")
                    else:
                        self._packet_sent_counter = 1
                except websockets.exceptions.ConnectionClosed:
                    print("Connection closed during audio send, reconnecting...")
                    if not await self._reconnect():
                        continue
                except Exception as e:
                    print(f"Error sending audio: {e}")
                    # Try to reconnect
                    if not await self._reconnect():
                        continue
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in audio streaming loop: {e}")
                await asyncio.sleep(0.1)
        
        print("Audio streaming stopped")
    
    async def _metadata_sender_loop(self):
        """Async loop that sends metadata from queue"""
        while not self._stop_event.is_set():
            try:
                # Check for metadata in queue (non-blocking)
                try:
                    metadata = self.metadata_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue
                
                # Ensure connected (connect() is idempotent, so safe to call)
                if not self.is_connected:
                    if not await self.connect():
                        await asyncio.sleep(self.reconnect_delay)
                        continue
                
                # Send metadata as JSON
                try:
                    message = {
                        "type": "metadata",
                        "agent_id": self.agent_id,
                        "data": metadata
                    }
                    await self.websocket.send(json.dumps(message))
                except websockets.exceptions.ConnectionClosed:
                    print("Connection closed during metadata send, reconnecting...")
                    if not await self._reconnect():
                        continue
                except Exception as e:
                    print(f"Error sending metadata: {e}")
                    # Try to reconnect
                    if not await self._reconnect():
                        continue
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in metadata sender loop: {e}")
                await asyncio.sleep(0.1)
    
    def send_metadata(self, status_dict: Dict[str, Any]):
        """
        Send JSON metadata updates (non-blocking, runs asynchronously).
        This method does not block the main GUI thread.
        
        Args:
            status_dict: Dictionary containing status information
                (e.g., {'status': 'Calling', 'lead': '555-0199'})
        """
        try:
            import time
            # Add timestamp
            status_dict['timestamp'] = time.time()
            
            # Put in queue (non-blocking)
            self.metadata_queue.put_nowait(status_dict)
        except queue.Full:
            print("Metadata queue full, dropping message")
        except Exception as e:
            print(f"Error queuing metadata: {e}")
    
    def start_audio_streaming(self, audio_queue: queue.Queue):
        """
        Start audio streaming in the async event loop.
        
        Args:
            audio_queue: Queue containing audio chunks
        """
        if not self.loop:
            print("Event loop not started. Call start() first.")
            return
        
        if self.streaming_task and not self.streaming_task.done():
            print("Audio streaming already started")
            return
        
        # Schedule streaming task
        self.streaming_task = asyncio.run_coroutine_threadsafe(
            self.stream_audio(audio_queue),
            self.loop
        )
    
    def stop_audio_streaming(self):
        """Stop audio streaming"""
        if self.streaming_task:
            self.streaming_task.cancel()
            self.streaming_task = None
    
    async def _message_receiver_loop(self):
        """Async loop that receives messages from server"""
        while not self._stop_event.is_set():
            try:
                # Ensure connected (connect() is idempotent, so safe to call)
                if not self.is_connected:
                    if not await self.connect():
                        await asyncio.sleep(self.reconnect_delay)
                        continue
                
                # Wait for messages indefinitely
                try:
                    message = await self.websocket.recv()
                    
                    # Parse JSON message
                    try:
                        message_dict = json.loads(message)
                        message_type = message_dict.get("type")
                        
                        # Put message in queue for compatibility with older callers.
                        self.message_queue.put_nowait(message_dict)
                        if self.dispatcher is not None:
                            self.dispatcher.dispatch(message_dict)
                    
                    except json.JSONDecodeError:
                        # Not JSON, ignore
                        pass
                    
                except websockets.exceptions.ConnectionClosed:
                    print("Connection closed during message receive, reconnecting...")
                    if not await self._reconnect():
                        continue
                except Exception as e:
                    print(f"Error receiving message: {e}")
                    if not await self._reconnect():
                        continue
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in message receiver loop: {e}")
                await asyncio.sleep(0.1)
    
    def register_message_handler(self, message_type: str, callback):
        """
        Register a callback function for a specific message type.
        
        Args:
            message_type: Type of message (e.g., "show_callback_prompt")
            callback: Callback function that takes message_dict as parameter
        """
        self.message_callbacks[message_type] = callback

    def set_dispatcher(self, dispatcher: Optional["QtMessageDispatcher"]):
        """Attach or replace the Qt-side inbound message dispatcher."""
        self.dispatcher = dispatcher
    
    def send_json_message(self, message_dict: Dict[str, Any]):
        """
        Send a JSON message to the server (non-blocking).
        
        Args:
            message_dict: Dictionary to send as JSON
        """
        try:
            if not self.loop:
                print("Event loop not started. Cannot send message.")
                return
            
            # Schedule send in async loop
            asyncio.run_coroutine_threadsafe(
                self._send_json_async(message_dict),
                self.loop
            )
        except Exception as e:
            print(f"Error sending JSON message: {e}")
    
    async def _send_json_async(self, message_dict: Dict[str, Any]):
        """Async helper to send JSON message"""
        try:
            # Ensure connected (connect() is idempotent, so safe to call)
            if not self.is_connected:
                if not await self.connect():
                    print(f"[Streamer] Failed to connect, cannot send message: {message_dict.get('type', 'unknown')}")
                    return
            
            await self.websocket.send(json.dumps(message_dict))
        except Exception as e:
            print(f"Error sending JSON message async: {e}")
            # Mark as disconnected and let reconnect logic handle it
            self.is_connected = False
    
    def get_message(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """
        Get a message from the message queue (non-blocking).
        
        Args:
            timeout: Timeout in seconds (default 0.1)
        
        Returns:
            Message dictionary or None if no message available
        """
        try:
            return self.message_queue.get(timeout=timeout)
        except queue.Empty:
            return None


# Alias for backward compatibility
Streamer = AudioStreamer
