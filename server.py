import eventlet
eventlet.monkey_patch()

import os
import sys
import logging
import base64
import json
from datetime import datetime
from flask import Flask, request, session, redirect, url_for, render_template_string, send_file
from flask_socketio import SocketIO, emit
from io import BytesIO

# --- Logging Setup ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'default_secret_key_for_local_dev')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')

# --- WebRTC Configuration (Single Source of Truth) ---
ICE_SERVERS = [
    {'urls': 'stun:stun.l.google.com:19302'},
    {'urls': 'stun:stun1.l.google.com:19302'},
    {
        'urls': "turn:numb.viagenie.ca:443?transport=tcp",
        'username': "webrtc@live.com",
        'credential': "muazkh"
    },
    {
        'urls': "turn:relay.metered.ca:443?transport=tcp",
        'username': "85d4fae4a3569d1a11e09a7a",
        'credential': "JZEOEt2V3Qb0y27GRntt2u2PAYA="
    },
    {
        'urls': "turn:numb.viagenie.ca:3478",
        'username': "webrtc@live.com",
        'credential': "muazkh"
    },
]

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", max_http_buffer_size=100000000)  # 100MB max for ultra high quality screenshots

# --- Global State ---
client_pc_sid = None
# Store screenshots in memory (you could also save to disk)
screenshots_storage = {}

# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates ---
LOGIN_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Login</title><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-100 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm"><h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Login</h1>{% if error %}<div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4"><span>{{ error }}</span></div>{% endif %}<form method="POST" action="{{ url_for('index') }}"><div class="mb-4"><label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label><input type="password" id="password" name="password" required class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"></div><button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md">Login</button></form></div></body></html>
"""

INTERFACE_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Remote Control</title><script src="https://cdn.tailwindcss.com"></script><script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script><style>html, body { height: 100%; overflow: hidden; } .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; } .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; } #main-content { display: flex; flex-direction: column; height: calc(100vh - 3.5rem); } #screen-view-area { flex-grow: 1; display: flex; align-items: center; justify-content: center; background-color: #000; overflow: hidden; transition: height 0.3s ease; position: relative; } #screen-video { max-width: 100%; max-height: 100%; cursor: crosshair; object-fit: contain; } #text-input-container { height: 0; overflow: hidden; background-color: #f0f4f8; padding: 0; transition: all 0.3s ease; display: flex; flex-direction: column; } #injection-textarea { flex-grow: 1; resize: none; border-radius: 0.25rem; border: 1px solid #ccc; padding: 0.5rem; font-family: monospace; } .control-button { background-color: #3b82f6; color: white; padding: 0.5rem 1rem; border-radius: 0.25rem; transition: background-color 0.2s; } .control-button:hover { background-color: #2563eb; } .control-button.active { background-color: #16a34a; } .control-button.active:hover { background-color: #15803d; } body.text-mode #screen-view-area { height: 50%; } body.text-mode #text-input-container { height: 50%; padding: 1rem; } 
/* Screenshot gallery styles */
#screenshot-gallery { position: absolute; bottom: 10px; left: 10px; display: flex; gap: 10px; flex-wrap: wrap; max-width: 300px; background: rgba(0,0,0,0.7); padding: 10px; border-radius: 8px; max-height: 200px; overflow-y: auto; display: none; }
#screenshot-gallery.has-screenshots { display: flex; }
.screenshot-thumb { width: 60px; height: 40px; object-fit: cover; cursor: pointer; border: 2px solid transparent; border-radius: 4px; transition: all 0.2s; }
.screenshot-thumb:hover { border-color: #3b82f6; transform: scale(1.1); }
#screenshot-viewer { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); display: none; align-items: center; justify-content: center; z-index: 1000; }
#screenshot-viewer img { max-width: 90%; max-height: 90%; object-fit: contain; }
#screenshot-viewer-controls { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); display: flex; gap: 10px; }
.viewer-button { background-color: #3b82f6; color: white; padding: 0.5rem 1rem; border-radius: 0.25rem; transition: background-color 0.2s; cursor: pointer; border: none; }
.viewer-button:hover { background-color: #2563eb; }
#screenshot-status { position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.8); color: white; padding: 10px; border-radius: 8px; display: none; }
</style></head><body class="bg-gray-200" tabindex="0"><header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md h-14"><h1 class="text-lg font-semibold">Remote Desktop Control</h1><div class="flex items-center space-x-4"><button id="screenshot-btn" class="control-button">📸 Screenshot</button><button id="toggle-text-mode" class="control-button">Text Input</button><div id="connection-status" class="flex items-center text-sm"><span id="status-dot" class="status-dot status-connecting"></span><span id="status-text">Connecting...</span></div><a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md">Logout</a></div></header><main id="main-content"><div id="screen-view-area"><video id="screen-video" autoplay playsinline></video><div id="screenshot-gallery"></div><div id="screenshot-status"></div></div><div id="text-input-container"><textarea id="injection-textarea" placeholder="Paste or type text here. Client will type this on F2 press."></textarea><div class="flex justify-end items-center mt-2"><span id="injection-status" class="text-sm text-green-600 mr-4"></span><button id="send-text-button" class="control-button">Send Text to Client</button></div></div></main>
<div id="screenshot-viewer" onclick="closeScreenshotViewer(event)">
<img id="screenshot-full" src="" alt="Screenshot">
<div id="screenshot-viewer-controls">
<button class="viewer-button" onclick="downloadScreenshot(event)">Download</button>
<button class="viewer-button" onclick="closeScreenshotViewer(event)">Close</button>
</div>
</div>
<script>
document.addEventListener('DOMContentLoaded', () => {
    const socket = io(); const body = document.body; const screenViewArea = document.getElementById('screen-view-area'); const screenVideo = document.getElementById('screen-video'); const statusText = document.getElementById('status-text'); const statusDot = document.getElementById('status-dot'); const toggleTextModeBtn = document.getElementById('toggle-text-mode'); const injectionTextarea = document.getElementById('injection-textarea'); const sendTextBtn = document.getElementById('send-text-button'); const injectionStatus = document.getElementById('injection-status');
    const screenshotBtn = document.getElementById('screenshot-btn');
    const screenshotGallery = document.getElementById('screenshot-gallery');
    const screenshotStatus = document.getElementById('screenshot-status');
    const screenshotViewer = document.getElementById('screenshot-viewer');
    const screenshotFull = document.getElementById('screenshot-full');
    
    let pc = null; let iceServers = []; let remoteDimensions = { width: 1920, height: 1080 };
    let screenshots = [];
    let currentScreenshotData = null;
    
    function updateStatus(s, msg) { statusText.textContent = msg; statusDot.className = `status-dot ${s}`; }
    function closeConnection() { if (pc) { try { pc.close(); } catch(e) {} pc = null; } screenVideo.srcObject = null; }
    
    function showScreenshotStatus(msg, duration = 3000) {
        screenshotStatus.textContent = msg;
        screenshotStatus.style.display = 'block';
        setTimeout(() => { screenshotStatus.style.display = 'none'; }, duration);
    }
    
    window.closeScreenshotViewer = function(event) {
        if (event) event.stopPropagation();
        screenshotViewer.style.display = 'none';
        currentScreenshotData = null;
    };
    
    window.downloadScreenshot = function(event) {
        event.stopPropagation();
        if (!currentScreenshotData) return;
        
        const link = document.createElement('a');
        link.href = currentScreenshotData.data;
        link.download = `screenshot_${currentScreenshotData.timestamp}.png`;
        link.click();
    };
    
    function addScreenshotToGallery(screenshotData) {
        screenshots.push(screenshotData);
        
        const thumb = document.createElement('img');
        thumb.className = 'screenshot-thumb';
        thumb.src = screenshotData.data;
        thumb.title = `Screenshot taken at ${new Date(screenshotData.timestamp).toLocaleString()}`;
        thumb.onclick = () => {
            currentScreenshotData = screenshotData;
            screenshotFull.src = screenshotData.data;
            screenshotViewer.style.display = 'flex';
        };
        
        screenshotGallery.appendChild(thumb);
        screenshotGallery.classList.add('has-screenshots');
        
        // Keep only last 10 screenshots in gallery
        if (screenshots.length > 10) {
            screenshots.shift();
            screenshotGallery.removeChild(screenshotGallery.firstChild);
        }
    }
    
    screenshotBtn.addEventListener('click', () => {
        if (socket.connected) {
            socket.emit('request_screenshot');
            showScreenshotStatus('Requesting ultra high-quality screenshot...');
        }
    });
    
    socket.on('screenshot_received', (data) => {
        if (data.status === 'success' && data.screenshot) {
            addScreenshotToGallery({
                data: data.screenshot,
                timestamp: data.timestamp
            });
            showScreenshotStatus('Ultra high-quality screenshot captured!', 2000);
        } else {
            showScreenshotStatus('Failed to capture screenshot', 3000);
        }
    });
    
    async function createPeerConnection() {
        closeConnection();
        const pcConfig = { iceServers: iceServers };
        console.log("Creating PeerConnection with config:", pcConfig);
        pc = new RTCPeerConnection(pcConfig);
        pc.onicecandidate = e => { if (e.candidate) { console.log("Sending ICE candidate:", e.candidate); socket.emit('webrtc_ice_candidate', { candidate: e.candidate.toJSON() }); } };
        pc.ontrack = e => { if (screenVideo.srcObject !== e.streams[0]) { console.log("Received remote stream"); screenVideo.srcObject = e.streams[0]; } };
        pc.onconnectionstatechange = () => { console.log("Connection state:", pc.connectionState); if(pc.connectionState === 'connected') updateStatus('status-connected', 'Remote PC Connected'); else if (['disconnected', 'failed', 'closed'].includes(pc.connectionState)) { updateStatus('status-disconnected', 'Video Disconnected'); closeConnection(); } };
    }
    
    socket.on('connect', () => { 
        updateStatus('status-connecting', 'Server connected...');
        console.log("Connected to server, requesting config.");
        socket.emit('request_webrtc_config');
    });
    
    socket.on('webrtc_config', (config) => {
        iceServers = config.iceServers;
        console.log("Received WebRTC config:", iceServers);
        socket.emit('controller_ready');
    });

    socket.on('disconnect', () => { updateStatus('status-disconnected', 'Server disconnected'); closeConnection(); });
    socket.on('client_disconnected', () => { updateStatus('status-disconnected', 'Remote PC Disconnected'); closeConnection(); });
    
    socket.on('webrtc_offer', async (data) => {
        try {
            console.log("Received WebRTC offer:", data.offer);
            await createPeerConnection();
            await pc.setRemoteDescription(new RTCSessionDescription(data.offer));
            console.log("Set remote description successfully. Creating answer.");
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            console.log("Set local description successfully. Sending answer.");
            socket.emit('webrtc_answer', { answer: pc.localDescription.toJSON() });
        } catch (e) {
            console.error("Error handling WebRTC offer:", e);
            updateStatus('status-disconnected', 'WebRTC Error!');
        }
    });

    socket.on('webrtc_ice_candidate', (data) => {
        if (pc && data.candidate) {
            console.log("Received ICE candidate:", data.candidate);
            pc.addIceCandidate(new RTCIceCandidate(data.candidate)).catch(e => console.error("Error adding ICE candidate:", e));
        }
    });

    function sendControl(cmd) { if (socket.connected) socket.emit('control_command', cmd); }
    function getRemoteCoords(e) { const r = screenVideo.getBoundingClientRect(); if (r.width === 0 || r.height === 0) return null; return { x: Math.round((e.offsetX / r.width) * remoteDimensions.width), y: Math.round((e.offsetY / r.height) * remoteDimensions.height)}; }
    screenViewArea.addEventListener('mousemove', e => { const c = getRemoteCoords(e); if (c) sendControl({ action: 'move', ...c }); }); 
    screenViewArea.addEventListener('click', e => { const c = getRemoteCoords(e); if (c) sendControl({ action: 'click', button: 'left', ...c }); }); 
    screenViewArea.addEventListener('contextmenu', e => { e.preventDefault(); const c = getRemoteCoords(e); if (c) sendControl({ action: 'click', button: 'right', ...c }); }); 
    screenViewArea.addEventListener('wheel', e => { e.preventDefault(); const dY = e.deltaY > 0 ? 1 : (e.deltaY < 0 ? -1 : 0); const dX = e.deltaX > 0 ? 1 : (e.deltaX < 0 ? -1 : 0); if (dY || dX) sendControl({ action: 'scroll', dx: dX, dy: dY }); });
    document.body.addEventListener('keydown', e => { if(body.classList.contains('text-mode') && e.target === injectionTextarea) return; e.preventDefault(); sendControl({ action: 'keydown', key: e.key, code: e.code }); });
    document.body.addEventListener('keyup', e => { if(body.classList.contains('text-mode') && e.target === injectionTextarea) return; e.preventDefault(); sendControl({ action: 'keyup', key: e.key, code: e.code }); });
    toggleTextModeBtn.addEventListener('click', () => { body.classList.toggle('text-mode'); toggleTextModeBtn.classList.toggle('active'); if (body.classList.contains('text-mode')) injectionTextarea.focus(); else document.body.focus(); });
    sendTextBtn.addEventListener('click', () => { const text = injectionTextarea.value; socket.emit('set_injection_text', { text: text }); injectionStatus.textContent = "Sending..."; });
    socket.on('text_injection_ack', (data) => { if (data.status === 'success') { injectionStatus.textContent = "Text saved on client!"; } else { injectionStatus.textContent = `Error: ${data.message}`; } setTimeout(() => { injectionStatus.textContent = ''; }, 3000); });
});
</script></body></html>
"""

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if check_auth(request.form.get('password')):
            session['authenticated'] = True
            return redirect(url_for('interface'))
        else:
            return render_template_string(LOGIN_HTML, error="Invalid password")
    if session.get('authenticated'):
        return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    return render_template_string(INTERFACE_HTML)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('index'))

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    if session.get('authenticated'):
        logger.info(f"Controller connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    if request.sid == client_pc_sid:
        logger.warning("Remote PC disconnected.")
        client_pc_sid = None
        emit('client_disconnected', broadcast=True, include_self=False)
    else: # It's a controller
        logger.info(f"Controller {request.sid} disconnected.")
        if client_pc_sid:
            emit('controller_disconnected', {'sid': request.sid}, room=client_pc_sid)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    if data.get('token') == ACCESS_PASSWORD:
        client_pc_sid = request.sid
        logger.info(f"Remote PC registered: {client_pc_sid}")
    else:
        logger.warning(f"Failed registration attempt from SID {request.sid}")

@socketio.on('request_webrtc_config')
def handle_request_webrtc_config():
    if session.get('authenticated'):
        logger.info(f"Controller {request.sid} requested WebRTC config.")
        emit('webrtc_config', {'iceServers': ICE_SERVERS})

@socketio.on('controller_ready')
def handle_controller_ready():
    if session.get('authenticated') and client_pc_sid:
        logger.info(f"Controller {request.sid} is ready, telling PC to start connection.")
        emit('start_webrtc_for_controller', {'sid': request.sid}, room=client_pc_sid)

# --- Screenshot Handler ---
@socketio.on('request_screenshot')
def handle_request_screenshot():
    if session.get('authenticated') and client_pc_sid:
        logger.info(f"Controller {request.sid} requested screenshot")
        emit('capture_screenshot', {'requester_sid': request.sid}, room=client_pc_sid)

@socketio.on('screenshot_data')
def handle_screenshot_data(data):
    if request.sid == client_pc_sid:
        requester_sid = data.get('requester_sid')
        screenshot_data = data.get('screenshot')
        
        if requester_sid and screenshot_data:
            timestamp = datetime.now().isoformat()
            
            # Store screenshot (you could also save to disk here)
            screenshot_id = f"{requester_sid}_{timestamp}"
            screenshots_storage[screenshot_id] = {
                'data': screenshot_data,
                'timestamp': timestamp
            }
            
            logger.info(f"Received screenshot from client for {requester_sid}")
            
            # Send screenshot to the requester
            emit('screenshot_received', {
                'status': 'success',
                'screenshot': screenshot_data,
                'timestamp': timestamp
            }, room=requester_sid)

# --- Universal Relay Handlers ---
@socketio.on('webrtc_offer')
def handle_webrtc_offer(data):
    if request.sid == client_pc_sid and 'to_sid' in data:
        emit('webrtc_offer', {'offer': data['offer']}, room=data['to_sid'])

@socketio.on('webrtc_answer')
def handle_webrtc_answer(data):
    if client_pc_sid:
        emit('webrtc_answer', {'answer': data['answer'], 'from_sid': request.sid}, room=client_pc_sid)

@socketio.on('webrtc_ice_candidate')
def handle_webrtc_ice_candidate(data):
    if session.get('authenticated'): # From controller to client
        if client_pc_sid:
            emit('webrtc_ice_candidate', {'candidate': data['candidate'], 'from_sid': request.sid}, room=client_pc_sid)
    elif request.sid == client_pc_sid: # From client to controller
        if 'to_sid' in data:
            emit('webrtc_ice_candidate', {'candidate': data.get('candidate'), 'from_sid': request.sid}, room=data['to_sid'])

# --- Universal Command Handlers ---
@socketio.on('control_command')
def handle_control_command(data):
    if client_pc_sid:
        emit('command', data, room=client_pc_sid)

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if client_pc_sid:
        emit('receive_injection_text', data, room=client_pc_sid)
        emit('text_injection_ack', {'status': 'success'}, room=request.sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port)
