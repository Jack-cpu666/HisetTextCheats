import asyncio
import socketio
import os
import logging
import time
import random
from threading import Thread
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCIceCandidate
from aiortc.sdp import candidate_from_sdp
from aiortc.contrib.media import MediaStreamTrack
from av import VideoFrame
import numpy as np
from mss import mss
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController, Listener as KeyboardListener
import sys

# --- Basic Setup & Config ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
SERVER_URL = os.environ.get('REMOTE_SERVER_URL', 'https://spotifycoolmusic.onrender.com')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')

# IMPROVED ICE SERVERS - Multiple TURN servers for better reliability
ICE_SERVERS = [
    RTCIceServer(urls=['stun:stun.l.google.com:19302']),
    RTCIceServer(urls=['stun:stun1.l.google.com:19302']),
    RTCIceServer(urls=['stun:stun2.l.google.com:19302']),
    # Primary TURN server
    RTCIceServer(
        urls=["turn:numb.viagenie.ca"],
        username="webrtc@live.com",
        credential="muazkh"
    ),
    # Backup TURN servers
    RTCIceServer(
        urls=["turn:relay.metered.ca:80"],
        username="85d4fae4a3569d1a11e09a7a",
        credential="JZEOEt2V3Qb0y27GRntt2u2PAYA="
    ),
    RTCIceServer(
        urls=["turn:relay.metered.ca:443"],
        username="85d4fae4a3569d1a11e09a7a",
        credential="JZEOEt2V3Qb0y27GRntt2u2PAYA="
    )
]

PC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)

# --- Global State ---
sio = socketio.AsyncClient(logger=True, engineio_logger=True)
peer_connections = {}
mouse = MouseController()
keyboard = KeyboardController()
sct = mss()
monitor = sct.monitors[1]  # Changed from monitor 3 to 1 (primary monitor)
text_to_inject = ""
is_typing = False

KEY_MAP = {'Enter': Key.enter, 'Escape': Key.esc, 'ArrowUp': Key.up, 'ArrowDown': Key.down, 'ArrowLeft': Key.left, 'ArrowRight': Key.right, 'Tab': Key.tab, ' ': Key.space, 'Backspace': Key.backspace, 'Delete': Key.delete, 'Shift': Key.shift, 'Control': Key.ctrl, 'Alt': Key.alt, 'Meta': Key.cmd, 'CapsLock': Key.caps_lock, 'F1': Key.f1, 'F2': Key.f2, 'F3': Key.f3, 'F4': Key.f4, 'F5': Key.f5, 'F6': Key.f6, 'F7': Key.f7, 'F8': Key.f8, 'F9': Key.f9, 'F10': Key.f10, 'F11': Key.f11, 'F12': Key.f12}

try: import cv2
except ImportError: logging.error("OpenCV not installed! Please run: pip install opencv-python"); sys.exit(1)

class ScreenShareTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self): 
        super().__init__()
        self.start_time = time.time()
        # Add frame counter for debugging
        self.frame_count = 0
    
    async def recv(self):
        try:
            sct_img = sct.grab(monitor)
            img = np.array(sct_img)
            
            # Ensure we have a valid image
            if img.size == 0:
                logging.error("Empty screen capture!")
                return None
            
            # Convert color format
            img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            # Scale down for better performance (optional)
            height, width = img_bgr.shape[:2]
            if width > 1920 or height > 1080:
                scale_factor = min(1920/width, 1080/height)
                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)
                img_bgr = cv2.resize(img_bgr, (new_width, new_height))
            
            frame = VideoFrame.from_ndarray(img_bgr, format="bgr24")
            current_time = time.time()
            frame.pts = int((current_time - self.start_time) * 90000)
            frame.time_base = 90000
            
            self.frame_count += 1
            if self.frame_count % 30 == 0:  # Log every 30 frames
                logging.info(f"Sent frame {self.frame_count}, size: {img_bgr.shape}")
            
            return frame
        except Exception as e:
            logging.error(f"Error in ScreenShareTrack.recv: {e}")
            return None

async def type_like_human(text_to_type):
    global is_typing
    if not text_to_type or is_typing: return
    is_typing = True; typo_chars = "qazwsxedcrfvtgb "
    for char in text_to_type:
        if random.random() < 0.04:
            keyboard.type(random.choice(typo_chars)); await asyncio.sleep(random.uniform(0.1, 0.3))
            keyboard.press(Key.backspace); keyboard.release(Key.backspace); await asyncio.sleep(random.uniform(0.05, 0.15))
        keyboard.type(char); await asyncio.sleep(random.uniform(0.04, 0.12))
    is_typing = False

# --- Socket.IO Handlers ---
@sio.event
async def connect():
    dimensions = {'width': monitor['width'], 'height': monitor['height']}
    logging.info(f"Connecting with monitor dimensions: {dimensions}")
    await sio.emit('register_client', {'token': ACCESS_PASSWORD, 'dimensions': dimensions})

@sio.event
async def disconnect():
    for sid, pc in peer_connections.items():
        if pc and pc.connectionState != 'closed': await pc.close()
    peer_connections.clear()

@sio.on('receive_injection_text')
async def on_receive_injection_text(data):
    global text_to_inject
    text_to_inject = data.get('text', '')

# --- IMPROVED MULTI-CONTROLLER WEBRTC HANDLERS ---
@sio.on('start_webrtc_for_controller')
async def on_start_webrtc(data):
    controller_sid = data['sid']
    if controller_sid in peer_connections:
        logging.warning(f"Connection for {controller_sid} already exists. Closing old one.")
        await peer_connections[controller_sid].close()
        del peer_connections[controller_sid]

    logging.info(f"Creating new peer connection for controller: {controller_sid}")
    pc = RTCPeerConnection(configuration=PC_CONFIG)
    peer_connections[controller_sid] = pc

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if candidate:
            logging.info(f"Sending ICE candidate to {controller_sid}: {candidate.sdp}")
            await sio.emit('webrtc_ice_candidate', {
                'candidate': {
                    'candidate': candidate.sdp, 
                    'sdpMid': candidate.sdpMid, 
                    'sdpMLineIndex': candidate.sdpMLineIndex
                }, 
                'to_sid': controller_sid
            })
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logging.info(f"Connection state for {controller_sid} is: {pc.connectionState}")
        if pc.connectionState in ["failed", "disconnected", "closed"]:
            if controller_sid in peer_connections:
                await peer_connections[controller_sid].close()
                del peer_connections[controller_sid]
        elif pc.connectionState == "connected":
            logging.info(f"Successfully connected to controller {controller_sid}")

    # Add the screen share track BEFORE creating offer
    screen_track = ScreenShareTrack()
    pc.addTrack(screen_track)
    logging.info(f"Added screen track to peer connection for {controller_sid}")
    
    # Create offer with better constraints
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    logging.info(f"Sending offer to {controller_sid}")
    await sio.emit('webrtc_offer', {
        'offer': {
            'sdp': pc.localDescription.sdp, 
            'type': pc.localDescription.type
        }, 
        'to_sid': controller_sid
    })

@sio.on('webrtc_answer')
async def on_webrtc_answer(data):
    from_sid = data.get('from_sid')
    pc = peer_connections.get(from_sid)
    if pc and data.get('answer'):
        logging.info(f"Received answer from {from_sid}")
        await pc.setRemoteDescription(RTCSessionDescription(**data['answer']))

@sio.on('webrtc_ice_candidate')
async def on_webrtc_ice_candidate(data):
    from_sid = data.get('from_sid')
    pc = peer_connections.get(from_sid)
    if pc and data.get('candidate') and data['candidate'].get('candidate'):
        try:
            candidate = candidate_from_sdp(data['candidate']['candidate'])
            candidate.sdpMid = data['candidate']['sdpMid']
            candidate.sdpMLineIndex = data['candidate']['sdpMLineIndex']
            await pc.addIceCandidate(candidate)
            logging.info(f"Added ICE candidate from {from_sid}")
        except Exception as e:
            logging.warning(f"Could not add ICE candidate from {from_sid}: {e}")

@sio.on('controller_disconnected')
async def on_controller_disconnected(data):
    sid = data['sid']
    if sid in peer_connections:
        logging.info(f"Controller {sid} disconnected, closing their connection.")
        await peer_connections[sid].close()
        del peer_connections[sid]

# --- Command Handler ---
@sio.on('command')
async def on_command(data):
    action, key_str = data.get('action'), data.get('key', '')
    if action == 'move': mouse.position = (data['x'], data['y'])
    elif action == 'click': mouse.position = (data['x'], data['y']); mouse.click(Button.left if data['button'] == 'left' else Button.right, 1)
    elif action == 'scroll': mouse.scroll(data['dx'], data['dy'])
    elif action == 'keydown': keyboard.press(KEY_MAP.get(key_str, key_str))
    elif action == 'keyup': keyboard.release(KEY_MAP.get(key_str, key_str))

# --- Keyboard Listener for F2 ---
def setup_keyboard_listener(loop):
    def on_press(key):
        if key == Key.f2: asyncio.run_coroutine_threadsafe(type_like_human(text_to_inject), loop)
    listener = KeyboardListener(on_press=on_press)
    listener.start()

async def main():
    # Print available monitors for debugging
    logging.info(f"Available monitors: {sct.monitors}")
    logging.info(f"Using monitor: {monitor}")
    
    setup_keyboard_listener(asyncio.get_running_loop())
    try:
        await sio.connect(SERVER_URL)
        await sio.wait()
    except Exception as e: 
        logging.error(f"Could not run client: {e}")
    finally:
        for sid, pc in peer_connections.items():
            if pc and pc.connectionState != 'closed': await pc.close()
        peer_connections.clear()

if __name__ == '__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
