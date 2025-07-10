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

log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'default_secret_key_for_local_dev')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')

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

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", max_http_buffer_size=100000000, ping_timeout=120, ping_interval=30)

client_pc_sid = None
screenshots_storage = {}
control_modes = {
    'screenshot_only': False,
    'keyboard_disabled': False,
    'mouse_disabled': False
}
connection_quality = {
    'latency': 0,
    'quality_mode': 'high'
}

def check_auth(password):
    return password == ACCESS_PASSWORD

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm">
        <h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Login</h1>
        {% if error %}
        <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
            <span>{{ error }}</span>
        </div>
        {% endif %}
        <form method="POST" action="{{ url_for('index') }}">
            <div class="mb-4">
                <label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label>
                <input type="password" id="password" name="password" required class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>
            <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md">Login</button>
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
    <title>Remote Control</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <style>
        html, body { height: 100%; overflow: hidden; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-connected { background-color: #4ade80; }
        .status-disconnected { background-color: #f87171; }
        .status-connecting { background-color: #fbbf24; }
        .status-poor { background-color: #f59e0b; }
        #main-content { display: flex; flex-direction: column; height: calc(100vh - 3.5rem); }
        #screen-view-area { flex-grow: 1; display: flex; align-items: center; justify-content: center; background-color: #000; overflow: hidden; transition: height 0.3s ease; position: relative; }
        #screen-video { max-width: 100%; max-height: 100%; cursor: crosshair; object-fit: contain; }
        #text-input-container { height: 0; overflow: hidden; background-color: #f0f4f8; padding: 0; transition: all 0.3s ease; display: flex; flex-direction: column; }
        #injection-textarea { flex-grow: 1; resize: none; border-radius: 0.25rem; border: 1px solid #ccc; padding: 0.5rem; font-family: monospace; }
        .control-button { background-color: #3b82f6; color: white; padding: 0.5rem 1rem; border-radius: 0.25rem; transition: background-color 0.2s; white-space: nowrap; font-size: 0.875rem; border: none; cursor: pointer; }
        .control-button:hover { background-color: #2563eb; }
        .control-button.active { background-color: #16a34a; }
        .control-button.active:hover { background-color: #15803d; }
        .control-button.danger { background-color: #ef4444; }
        .control-button.danger:hover { background-color: #dc2626; }
        .control-button.danger.active { background-color: #f97316; }
        body.text-mode #screen-view-area { height: 50%; }
        body.text-mode #text-input-container { height: 50%; padding: 1rem; }
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
        #quality-indicator { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.8); color: white; padding: 8px 12px; border-radius: 8px; font-size: 0.75rem; display: none; }
        #controls-bar { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
        .control-toggle { display: flex; align-items: center; gap: 0.25rem; font-size: 0.75rem; }
        .disabled-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; color: white; font-size: 1.5rem; pointer-events: none; z-index: 10; }
    </style>
</head>
<body class="bg-gray-200" tabindex="0">
    <header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md h-14">
        <h1 class="text-lg font-semibold">Remote Desktop Control</h1>
        <div id="controls-bar">
            <button id="screenshot-btn" class="control-button">📸 Ultra</button>
            <button id="regular-screenshot-btn" class="control-button">📷 Regular</button>
            <button id="screenshot-only-toggle" class="control-button danger">Screenshot Only</button>
            <button id="keyboard-toggle" class="control-button danger">🎹 Disable</button>
            <button id="mouse-toggle" class="control-button danger">🖱️ Disable</button>
            <button id="toggle-text-mode" class="control-button">Text Input</button>
            <div id="connection-status" class="flex items-center text-sm">
                <span id="status-dot" class="status-dot status-connecting"></span>
                <span id="status-text">Connecting...</span>
            </div>
            <a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md">Logout</a>
        </div>
    </header>
    
    <main id="main-content">
        <div id="screen-view-area">
            <video id="screen-video" autoplay playsinline></video>
            <div class="disabled-overlay" id="control-disabled-overlay">Controls Disabled</div>
            <div id="screenshot-gallery"></div>
            <div id="screenshot-status"></div>
            <div id="quality-indicator"></div>
        </div>
        <div id="text-input-container">
            <textarea id="injection-textarea" placeholder="Paste or type text here. Client will type this on F2 press. New text will completely override any previous text."></textarea>
            <div class="flex justify-end items-center mt-2">
                <span id="injection-status" class="text-sm text-green-600 mr-4"></span>
                <button id="send-text-button" class="control-button">Send Text to Client (Overrides Previous)</button>
            </div>
        </div>
    </main>

    <div id="screenshot-viewer" onclick="closeScreenshotViewer(event)">
        <img id="screenshot-full" src="" alt="Screenshot">
        <div id="screenshot-viewer-controls">
            <button class="viewer-button" onclick="downloadScreenshot(event)">Download</button>
            <button class="viewer-button" onclick="closeScreenshotViewer(event)">Close</button>
        </div>
    </div>

    <script>
    document.addEventListener('DOMContentLoaded', () => {
        const socket = io({
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
            reconnectionAttempts: Infinity,
            timeout: 20000,
            transports: ['websocket']
        });
        
        const body = document.body;
        const screenViewArea = document.getElementById('screen-view-area');
        const screenVideo = document.getElementById('screen-video');
        const statusText = document.getElementById('status-text');
        const statusDot = document.getElementById('status-dot');
        const toggleTextModeBtn = document.getElementById('toggle-text-mode');
        const injectionTextarea = document.getElementById('injection-textarea');
        const sendTextBtn = document.getElementById('send-text-button');
        const injectionStatus = document.getElementById('injection-status');
        const screenshotBtn = document.getElementById('screenshot-btn');
        const regularScreenshotBtn = document.getElementById('regular-screenshot-btn');
        const screenshotGallery = document.getElementById('screenshot-gallery');
        const screenshotStatus = document.getElementById('screenshot-status');
        const screenshotViewer = document.getElementById('screenshot-viewer');
        const screenshotFull = document.getElementById('screenshot-full');
        const qualityIndicator = document.getElementById('quality-indicator');
        const controlDisabledOverlay = document.getElementById('control-disabled-overlay');
        const screenshotOnlyToggle = document.getElementById('screenshot-only-toggle');
        const keyboardToggle = document.getElementById('keyboard-toggle');
        const mouseToggle = document.getElementById('mouse-toggle');
        
        let pc = null;
        let iceServers = [];
        let remoteDimensions = { width: 1920, height: 1080 };
        let screenshots = [];
        let currentScreenshotData = null;
        let controlsDisabled = { screenshot_only: false, keyboard: false, mouse: false };
        let hasRemoteClient = false;
        let lastPingTime = Date.now();
        let connectionCheckInterval = null;
        
        function updateStatus(statusClass, msg) { 
            statusText.textContent = msg; 
            statusDot.className = `status-dot ${statusClass}`; 
        }
        
        function updateQualityIndicator(quality, latency) {
            qualityIndicator.style.display = 'block';
            const fps = quality === 'high' ? '10fps' : quality === 'medium' ? '7fps' : '5fps';
            qualityIndicator.textContent = `Quality: ${quality} | Latency: ${latency}ms`;
            qualityIndicator.style.backgroundColor = quality === 'high' ? 'rgba(0,128,0,0.8)' : quality === 'medium' ? 'rgba(255,165,0,0.8)' : 'rgba(255,0,0,0.8)';
        }
        
        function updateControlOverlay() {
            if (controlsDisabled.screenshot_only) {
                controlDisabledOverlay.textContent = 'Screenshot Only Mode';
                controlDisabledOverlay.style.display = 'flex';
                screenVideo.style.cursor = 'not-allowed';
            } else if (controlsDisabled.keyboard && controlsDisabled.mouse) {
                controlDisabledOverlay.textContent = 'Controls Disabled';
                controlDisabledOverlay.style.display = 'flex';
                screenVideo.style.cursor = 'not-allowed';
            } else if (controlsDisabled.mouse) {
                controlDisabledOverlay.style.display = 'none';
                screenVideo.style.cursor = 'not-allowed';
            } else {
                controlDisabledOverlay.style.display = 'none';
                screenVideo.style.cursor = 'crosshair';
            }
        }
        
        function closeConnection() { 
            if (pc) { 
                try { pc.close(); } catch(e) {} 
                pc = null; 
            } 
            screenVideo.srcObject = null; 
            screenViewArea.style.backgroundColor = '#000';
        }
        
        function showScreenshotStatus(msg, duration = 3000) {
            screenshotStatus.textContent = msg;
            screenshotStatus.style.display = 'block';
            setTimeout(() => { screenshotStatus.style.display = 'none'; }, duration);
        }
        
        screenshotOnlyToggle.addEventListener('click', () => {
            controlsDisabled.screenshot_only = !controlsDisabled.screenshot_only;
            socket.emit('set_control_mode', { mode: 'screenshot_only', value: controlsDisabled.screenshot_only });
        });
        
        keyboardToggle.addEventListener('click', () => {
            controlsDisabled.keyboard = !controlsDisabled.keyboard;
            socket.emit('set_control_mode', { mode: 'keyboard_disabled', value: controlsDisabled.keyboard });
        });
        
        mouseToggle.addEventListener('click', () => {
            controlsDisabled.mouse = !controlsDisabled.mouse;
            socket.emit('set_control_mode', { mode: 'mouse_disabled', value: controlsDisabled.mouse });
        });
        
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
            if (screenshots.length > 10) {
                screenshots.shift();
                screenshotGallery.removeChild(screenshotGallery.firstChild);
            }
        }
        
        screenshotBtn.addEventListener('click', () => {
            if (socket.connected) {
                socket.emit('request_screenshot', { quality: 'ultra' });
                showScreenshotStatus('Requesting ultra high-quality screenshot...');
            }
        });
        
        regularScreenshotBtn.addEventListener('click', () => {
            if (socket.connected) {
                socket.emit('request_screenshot', { quality: 'regular' });
                showScreenshotStatus('Requesting regular screenshot...');
            }
        });
        
        socket.on('screenshot_received', (data) => {
            if (data.status === 'success' && data.screenshot) {
                addScreenshotToGallery({
                    data: data.screenshot,
                    timestamp: data.timestamp,
                    quality: data.quality || 'ultra'
                });
                showScreenshotStatus(`${data.quality || 'Ultra'} quality screenshot captured!`, 2000);
            } else {
                showScreenshotStatus('Failed to capture screenshot', 3000);
            }
        });
        
        socket.on('connection_quality', (data) => {
            updateQualityIndicator(data.quality_mode, data.latency);
        });
        
        async function createPeerConnection() {
            closeConnection();
            const pcConfig = { 
                iceServers: iceServers,
                iceCandidatePoolSize: 10,
                bundlePolicy: 'max-bundle',
                rtcpMuxPolicy: 'require'
            };
            pc = new RTCPeerConnection(pcConfig);
            pc.onicecandidate = e => { 
                if (e.candidate) { 
                    socket.emit('webrtc_ice_candidate', { candidate: e.candidate.toJSON() }); 
                } 
            };
            pc.ontrack = e => { 
                if (screenVideo.srcObject !== e.streams[0]) { 
                    screenVideo.srcObject = e.streams[0]; 
                    screenViewArea.style.backgroundColor = 'transparent';
                } 
            };
            pc.onconnectionstatechange = () => { 
                if(pc.connectionState === 'connected') {
                    updateStatus('status-connected', 'Remote PC Connected');
                    startConnectionMonitoring();
                } else if (['disconnected', 'failed'].includes(pc.connectionState)) { 
                    updateStatus('status-poor', 'Connection Poor - Reconnecting...');
                    setTimeout(() => {
                        if (pc && pc.connectionState === 'failed') {
                            updateStatus('status-disconnected', 'Video Disconnected');
                            closeConnection();
                        }
                    }, 5000);
                } else if (pc.connectionState === 'closed') {
                    updateStatus('status-disconnected', 'Video Closed');
                    closeConnection();
                }
            };
        }
        
        function startConnectionMonitoring() {
            if (connectionCheckInterval) clearInterval(connectionCheckInterval);
            connectionCheckInterval = setInterval(() => {
                if (pc && pc.connectionState === 'connected') {
                    pc.getStats().then(stats => {
                        stats.forEach(report => {
                            if (report.type === 'candidate-pair' && report.state === 'succeeded') {
                                const latency = report.currentRoundTripTime * 1000;
                                socket.emit('report_latency', { latency: Math.round(latency) });
                            }
                        });
                    });
                }
            }, 2000);
        }
        
        socket.on('control_mode_update', (data) => {
            if (data.mode in controlsDisabled) {
                controlsDisabled[data.mode] = data.value;
                if (data.mode === 'screenshot_only') {
                    screenshotOnlyToggle.classList.toggle('active', data.value);
                    if (data.value) {
                        closeConnection();
                        updateStatus('status-connected', 'Screenshot Only Mode');
                    } else if (socket.connected && hasRemoteClient) {
                         updateStatus('status-connecting', 'Reconnecting video...');
                         socket.emit('controller_ready');
                    }
                } else if (data.mode === 'keyboard_disabled') {
                    keyboardToggle.classList.toggle('active', data.value);
                    keyboardToggle.textContent = data.value ? '🎹 Enable' : '🎹 Disable';
                } else if (data.mode === 'mouse_disabled') {
                    mouseToggle.classList.toggle('active', data.value);
                    mouseToggle.textContent = data.value ? '🖱️ Enable' : '🖱️ Disable';
                }
                updateControlOverlay();
            }
        });
        
        socket.on('connect', () => { 
            updateStatus('status-connecting', 'Server connected...');
            socket.emit('request_webrtc_config');
        });
        
        socket.on('webrtc_config', (config) => {
            iceServers = config.iceServers;
        });

        socket.on('client_available', () => {
            hasRemoteClient = true;
            if (controlsDisabled.screenshot_only) {
                updateStatus('status-connected', 'Screenshot Only Mode');
            } else {
                updateStatus('status-connecting', 'Client available, starting video...');
                socket.emit('controller_ready');
            }
        });

        socket.on('disconnect', () => { 
            updateStatus('status-disconnected', 'Server disconnected'); 
            closeConnection(); 
            hasRemoteClient = false;
            if (connectionCheckInterval) clearInterval(connectionCheckInterval);
        });
        
        socket.on('client_disconnected', () => { 
            updateStatus('status-disconnected', 'Remote PC Disconnected'); 
            closeConnection(); 
            hasRemoteClient = false;
        });
        
        socket.on('webrtc_offer', async (data) => {
            if (controlsDisabled.screenshot_only) return;
            try {
                await createPeerConnection();
                await pc.setRemoteDescription(new RTCSessionDescription(data.offer));
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                socket.emit('webrtc_answer', { answer: pc.localDescription.toJSON() });
            } catch (e) {
                console.error("Error handling WebRTC offer:", e);
                updateStatus('status-disconnected', 'WebRTC Error!');
            }
        });

        socket.on('webrtc_ice_candidate', (data) => {
            if (pc && data.candidate) {
                pc.addIceCandidate(new RTCIceCandidate(data.candidate)).catch(e => console.error("Error adding ICE candidate:", e));
            }
        });

        function sendControl(cmd) { 
            if (socket.connected && !controlsDisabled.screenshot_only) {
                if (cmd.action.includes('key') && controlsDisabled.keyboard) return;
                if (['move', 'click', 'scroll'].includes(cmd.action) && controlsDisabled.mouse) return;
                socket.emit('control_command', cmd); 
            }
        }
        
        function getRemoteCoords(e) { 
            const r = screenVideo.getBoundingClientRect(); 
            if (r.width === 0 || r.height === 0) return null; 
            return { 
                x: Math.round((e.offsetX / r.width) * remoteDimensions.width), 
                y: Math.round((e.offsetY / r.height) * remoteDimensions.height)
            }; 
        }
        
        screenViewArea.addEventListener('mousemove', e => { 
            const c = getRemoteCoords(e); 
            if (c) sendControl({ action: 'move', ...c }); 
        }); 
        screenViewArea.addEventListener('click', e => { 
            const c = getRemoteCoords(e); 
            if (c) sendControl({ action: 'click', button: 'left', ...c }); 
        }); 
        screenViewArea.addEventListener('contextmenu', e => { 
            e.preventDefault(); 
            const c = getRemoteCoords(e); 
            if (c) sendControl({ action: 'click', button: 'right', ...c }); 
        }); 
        screenViewArea.addEventListener('wheel', e => { 
            e.preventDefault(); 
            const dY = e.deltaY > 0 ? 1 : (e.deltaY < 0 ? -1 : 0); 
            const dX = e.deltaX > 0 ? 1 : (e.deltaX < 0 ? -1 : 0); 
            if (dY || dX) sendControl({ action: 'scroll', dx: dX, dy: dY }); 
        });
        document.body.addEventListener('keydown', e => { 
            if(body.classList.contains('text-mode') && e.target === injectionTextarea) return; 
            e.preventDefault(); 
            sendControl({ action: 'keydown', key: e.key, code: e.code }); 
        });
        document.body.addEventListener('keyup', e => { 
            if(body.classList.contains('text-mode') && e.target === injectionTextarea) return; 
            e.preventDefault(); 
            sendControl({ action: 'keyup', key: e.key, code: e.code }); 
        });
        toggleTextModeBtn.addEventListener('click', () => { 
            body.classList.toggle('text-mode'); 
            toggleTextModeBtn.classList.toggle('active'); 
            if (body.classList.contains('text-mode')) injectionTextarea.focus(); 
            else document.body.focus(); 
        });
        sendTextBtn.addEventListener('click', () => { 
            const text = injectionTextarea.value; 
            socket.emit('set_injection_text', { text: text, override: true }); 
            injectionStatus.textContent = "Sending (overrides previous)..."; 
        });
        socket.on('text_injection_ack', (data) => { 
            if (data.status === 'success') { 
                injectionStatus.textContent = "Text saved on client! (Previous text overridden)"; 
            } else { 
                injectionStatus.textContent = `Error: ${data.message}`; 
            } 
            setTimeout(() => { injectionStatus.textContent = ''; }, 3000); 
        });
    });
    </script>
</body>
</html>
"""

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
    else:
        logger.info(f"Controller {request.sid} disconnected.")
        if client_pc_sid:
            emit('controller_disconnected', {'sid': request.sid}, room=client_pc_sid)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    if data.get('token') == ACCESS_PASSWORD:
        client_pc_sid = request.sid
        logger.info(f"Remote PC registered: {client_pc_sid}")
        emit('client_available', broadcast=True, include_self=False)
    else:
        logger.warning(f"Failed registration attempt from SID {request.sid}")

@socketio.on('request_webrtc_config')
def handle_request_webrtc_config():
    if session.get('authenticated'):
        logger.info(f"Controller {request.sid} requested WebRTC config.")
        emit('webrtc_config', {'iceServers': ICE_SERVERS})
        for mode, value in control_modes.items():
            emit('control_mode_update', {'mode': mode, 'value': value})
        if client_pc_sid:
            emit('client_available')

@socketio.on('controller_ready')
def handle_controller_ready():
    if session.get('authenticated') and client_pc_sid:
        logger.info(f"Controller {request.sid} is ready, telling PC to start connection.")
        emit('start_webrtc_for_controller', {'sid': request.sid}, room=client_pc_sid)

@socketio.on('set_control_mode')
def handle_set_control_mode(data):
    if session.get('authenticated'):
        mode = data.get('mode')
        value = data.get('value')
        if mode in control_modes:
            control_modes[mode] = value
            logger.info(f"Control mode {mode} set to {value}")
            emit('control_mode_update', {'mode': mode, 'value': value}, broadcast=True, include_self=True)

@socketio.on('report_latency')
def handle_report_latency(data):
    if session.get('authenticated'):
        latency = data.get('latency', 0)
        connection_quality['latency'] = latency
        if latency < 50:
            quality_mode = 'high'
        elif latency < 150:
            quality_mode = 'medium'
        else:
            quality_mode = 'low'
        if quality_mode != connection_quality['quality_mode']:
            connection_quality['quality_mode'] = quality_mode
            if client_pc_sid:
                emit('quality_mode_change', {'quality_mode': quality_mode}, room=client_pc_sid)
        emit('connection_quality', { 'latency': latency, 'quality_mode': quality_mode }, room=request.sid)

@socketio.on('request_screenshot')
def handle_request_screenshot(data):
    if session.get('authenticated') and client_pc_sid:
        quality = data.get('quality', 'ultra')
        logger.info(f"Controller {request.sid} requested {quality} screenshot")
        emit('capture_screenshot', {'requester_sid': request.sid, 'quality': quality }, room=client_pc_sid)

@socketio.on('screenshot_data')
def handle_screenshot_data(data):
    if request.sid == client_pc_sid:
        requester_sid = data.get('requester_sid')
        screenshot_data = data.get('screenshot')
        quality = data.get('quality', 'ultra')
        if requester_sid and screenshot_data:
            timestamp = datetime.now().isoformat()
            logger.info(f"Received {quality} screenshot from client for {requester_sid}")
            emit('screenshot_received', { 'status': 'success', 'screenshot': screenshot_data, 'timestamp': timestamp, 'quality': quality }, room=requester_sid)

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
    if session.get('authenticated'):
        if client_pc_sid:
            emit('webrtc_ice_candidate', {'candidate': data['candidate'], 'from_sid': request.sid}, room=client_pc_sid)
    elif request.sid == client_pc_sid:
        if 'to_sid' in data:
            emit('webrtc_ice_candidate', {'candidate': data.get('candidate'), 'from_sid': request.sid}, room=data['to_sid'])

@socketio.on('control_command')
def handle_control_command(data):
    if client_pc_sid and not control_modes['screenshot_only']:
        action = data.get('action')
        if action in ['keydown', 'keyup'] and control_modes['keyboard_disabled']:
            return
        if action in ['move', 'click', 'scroll'] and control_modes['mouse_disabled']:
            return
        emit('command', data, room=client_pc_sid)

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if client_pc_sid:
        data['override'] = True
        emit('receive_injection_text', data, room=client_pc_sid)
        emit('text_injection_ack', {'status': 'success'}, room=request.sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting server on port {port}")
    logger.info(f"Access password is: {ACCESS_PASSWORD}")
    socketio.run(app, host='0.0.0.0', port=port)
