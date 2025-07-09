import eventlet
eventlet.monkey_patch()

import os
import sys
import logging
from flask import Flask, request, session, redirect, url_for, render_template_string
from flask_socketio import SocketIO, emit

# --- Logging Setup ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'default_secret_key_for_local_dev')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1') # Password is '1' by default

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# --- Global State ---
client_pc_sid = None

# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control - Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm">
        <h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Remote Access Login</h1>
        {% if error %}
            <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-4" role="alert">
                <span>{{ error }}</span>
            </div>
        {% endif %}
        <form method="POST" action="{{ url_for('index') }}">
            <div class="mb-4">
                <label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label>
                <input type="password" id="password" name="password" required
                       class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>
            <button type="submit"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md">
                Login
            </button>
        </form>
    </div>
</body>
</html>
"""

INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control Interface</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <style>
        html, body { height: 100%; overflow: hidden; }
        #screen-view-area { display: flex; align-items: center; justify-content: center; background-color: #000; overflow: hidden; height: calc(100vh - 3.5rem); }
        #screen-video { max-width: 100%; max-height: 100%; cursor: crosshair; object-fit: contain; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; }
    </style>
</head>
<body class="bg-gray-200" tabindex="0">
    <header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md h-14">
        <h1 class="text-lg font-semibold">Remote Desktop Control</h1>
        <div class="flex items-center space-x-3">
            <div id="connection-status" class="flex items-center text-sm">
                <span id="status-dot" class="status-dot status-connecting"></span>
                <span id="status-text">Connecting...</span>
            </div>
            <a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md">Logout</a>
        </div>
    </header>

    <main id="screen-view-area">
        <video id="screen-video" autoplay playsinline>Your browser does not support the video tag.</video>
    </main>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const socket = io();
            const screenViewArea = document.getElementById('screen-view-area');
            const screenVideo = document.getElementById('screen-video');
            const statusDot = document.getElementById('status-dot');
            const statusText = document.getElementById('status-text');

            let pc = null;
            let remoteDimensions = { width: 1920, height: 1080 };

            function updateStatus(status, message) {
                statusText.textContent = message;
                statusDot.className = `status-dot ${status}`;
            }

            // --- WebRTC Setup with STUN/TURN for Reliability ---
            async function createPeerConnection() {
                const pcConfig = {
                    iceServers: [
                        { urls: 'stun:stun.l.google.com:19302' },
                        { urls: 'stun:stun1.l.google.com:19302' },
                        {
                            urls: "turn:numb.viagenie.ca",
                            username: "webrtc@live.com",
                            credential: "muazkh"
                        }
                    ]
                };
                const newPc = new RTCPeerConnection(pcConfig);
                newPc.onicecandidate = event => {
                    if (event.candidate) {
                        socket.emit('webrtc_ice_candidate', { candidate: event.candidate.toJSON() });
                    }
                };
                newPc.ontrack = event => {
                    if (screenVideo.srcObject !== event.streams[0]) {
                        screenVideo.srcObject = event.streams[0];
                        console.log('Remote stream attached!');
                    }
                };
                newPc.onconnectionstatechange = () => {
                    console.log('Browser Connection State:', newPc.connectionState);
                    if (newPc.connectionState === 'failed') {
                        updateStatus('status-disconnected', 'Video connection failed');
                    } else if (newPc.connectionState === 'connected') {
                         updateStatus('status-connected', 'Remote PC Connected');
                    }
                };
                return newPc;
            }

            socket.on('connect', () => updateStatus('status-connecting', 'Server connected...'));
            socket.on('disconnect', () => { updateStatus('status-disconnected', 'Server disconnected'); if(pc) pc.close(); });
            socket.on('client_connected', (data) => {
                updateStatus('status-connecting', 'PC found, starting video...');
                remoteDimensions = data.dimensions;
                socket.emit('controller_ready');
            });
            socket.on('client_disconnected', () => { updateStatus('status-disconnected', 'Remote PC Disconnected'); if(pc) pc.close(); screenVideo.srcObject = null; });

            socket.on('webrtc_offer', async (data) => {
                console.log('Received WebRTC offer from remote PC');
                pc = await createPeerConnection();
                await pc.setRemoteDescription(new RTCSessionDescription(data.offer));
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                socket.emit('webrtc_answer', { answer: pc.localDescription.toJSON() });
            });

            socket.on('webrtc_ice_candidate', (data) => {
                if (pc && data.candidate) {
                    pc.addIceCandidate(new RTCIceCandidate(data.candidate)).catch(e => console.error('Error adding remote ICE candidate:', e));
                }
            });

            function sendControl(command) {
                if (socket.connected) socket.emit('control_command', command);
            }
            function getRemoteCoords(event) {
                const rect = screenVideo.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0 || !remoteDimensions) return null;
                const x = Math.round((event.offsetX / rect.width) * remoteDimensions.width);
                const y = Math.round((event.offsetY / rect.height) * remoteDimensions.height);
                return { x, y };
            }

            screenViewArea.addEventListener('mousemove', e => { const coords = getRemoteCoords(e); if (coords) sendControl({ action: 'move', ...coords }); });
            screenViewArea.addEventListener('click', e => { const coords = getRemoteCoords(e); if (coords) sendControl({ action: 'click', button: 'left', ...coords }); });
            screenViewArea.addEventListener('contextmenu', e => { e.preventDefault(); const coords = getRemoteCoords(e); if (coords) sendControl({ action: 'click', button: 'right', ...coords }); });
            screenViewArea.addEventListener('wheel', e => { e.preventDefault(); const dY = e.deltaY > 0 ? 1 : (e.deltaY < 0 ? -1 : 0); const dX = e.deltaX > 0 ? 1 : (e.deltaX < 0 ? -1 : 0); if (dY || dX) sendControl({ action: 'scroll', dx: dX, dy: dY }); });
            document.body.addEventListener('keydown', e => { e.preventDefault(); sendControl({ action: 'keydown', key: e.key, code: e.code }); });
            document.body.addEventListener('keyup', e => { e.preventDefault(); sendControl({ action: 'keyup', key: e.key, code: e.code }); });

            updateStatus('status-connecting', 'Initializing...');
        });
    </script>
</body>
</html>
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
    return render_template_string(LOGIN_HTML) if not session.get('authenticated') else redirect(url_for('interface'))

@app.route('/interface')
def interface():
    return render_template_string(INTERFACE_HTML) if session.get('authenticated') else redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('index'))

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    if session.get('authenticated') and client_pc_sid:
        emit('client_connected', {'dimensions': {'width': 1920, 'height': 1080}}, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    if request.sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected.")
        client_pc_sid = None
        emit('client_disconnected', broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    if data.get('token') == ACCESS_PASSWORD:
        client_pc_sid = request.sid
        logger.info(f"Remote PC registered: SID {client_pc_sid}")
        emit('client_connected', data, broadcast=True, include_self=False)
    else:
        logger.warning(f"Failed registration attempt from SID {request.sid}")

@socketio.on('control_command')
def handle_control_command(data):
    if session.get('authenticated') and client_pc_sid:
        emit('command', data, room=client_pc_sid)

# --- WebRTC Signaling ---
@socketio.on('controller_ready')
def handle_controller_ready():
    if session.get('authenticated') and client_pc_sid:
        logger.info(f"Controller {request.sid} is ready. Notifying client PC.")
        emit('start_webrtc', room=client_pc_sid)

@socketio.on('webrtc_offer')
def handle_webrtc_offer(data):
    if request.sid == client_pc_sid:
        emit('webrtc_offer', data, broadcast=True, include_self=False)

@socketio.on('webrtc_answer')
def handle_webrtc_answer(data):
    if session.get('authenticated') and client_pc_sid:
        emit('webrtc_answer', data, room=client_pc_sid)

@socketio.on('webrtc_ice_candidate')
def handle_webrtc_ice_candidate(data):
    if request.sid == client_pc_sid:
        emit('webrtc_ice_candidate', data, broadcast=True, include_self=False)
    elif session.get('authenticated') and client_pc_sid:
        emit('webrtc_ice_candidate', data, room=client_pc_sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
