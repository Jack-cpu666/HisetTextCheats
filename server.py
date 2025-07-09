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
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# --- Global State ---
client_pc_sid = None
client_pc_dimensions = {'width': 1920, 'height': 1080}

# --- HTML Template (No Authentication Required) ---
PUBLIC_INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Public Remote Screen</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <style>
        html, body { height: 100%; overflow: hidden; }
        .status-dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; }
        .status-connected { background-color: #4ade80; }
        .status-disconnected { background-color: #f87171; }
        .status-connecting { background-color: #fbbf24; }
        #main-content { display: flex; flex-direction: column; height: 100vh; }
        #screen-view-area { 
            flex-grow: 1; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            background-color: #000; 
            overflow: hidden; 
            position: relative; 
        }
        #screen-video { 
            max-width: 100%; 
            max-height: 100%; 
            cursor: crosshair; 
            object-fit: contain; 
        }
        #debug-panel { 
            position: absolute; 
            top: 10px; 
            left: 10px; 
            background: rgba(0,0,0,0.8); 
            color: white; 
            padding: 10px; 
            font-size: 12px; 
            border-radius: 5px; 
            max-width: 300px;
            z-index: 1000;
        }
        #header { 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
            color: white; 
            padding: 15px; 
            text-align: center; 
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .connection-info {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-top: 10px;
        }
        .retry-button {
            background: #ef4444;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 12px;
        }
        .retry-button:hover {
            background: #dc2626;
        }
    </style>
</head>
<body>
    <div id="header">
        <h1 class="text-2xl font-bold">🖥️ Remote Screen Viewer</h1>
        <div class="connection-info">
            <span id="status-dot" class="status-dot status-connecting"></span>
            <span id="status-text">Connecting...</span>
            <button id="retry-btn" class="retry-button" onclick="retryConnection()">Retry</button>
        </div>
    </div>
    
    <div id="main-content">
        <div id="screen-view-area">
            <video id="screen-video" autoplay playsinline muted></video>
            <div id="debug-panel">
                <div><strong>Debug Info:</strong></div>
                <div id="debug-info">Initializing...</div>
                <div id="webrtc-state">WebRTC: Not started</div>
                <div id="ice-state">ICE: Not started</div>
                <div id="video-state">Video: Not loaded</div>
                <hr style="margin: 10px 0;">
                <div id="error-log"></div>
            </div>
        </div>
    </div>

    <script>
    let socket;
    let pc = null;
    let remoteDimensions = { width: 1920, height: 1080 };
    let connectionAttempts = 0;
    let isRetrying = false;
    
    const statusText = document.getElementById('status-text');
    const statusDot = document.getElementById('status-dot');
    const screenVideo = document.getElementById('screen-video');
    const debugInfo = document.getElementById('debug-info');
    const webrtcState = document.getElementById('webrtc-state');
    const iceState = document.getElementById('ice-state');
    const videoState = document.getElementById('video-state');
    const errorLog = document.getElementById('error-log');
    
    function log(message) {
        console.log(message);
        debugInfo.textContent = message;
        
        // Add to error log if it's an error
        if (message.includes('Error') || message.includes('Failed')) {
            errorLog.innerHTML = `<div style="color: #ff6b6b; font-size: 11px; margin-top: 5px;">${message}</div>`;
        }
    }
    
    function updateStatus(statusClass, message) {
        statusText.textContent = message;
        statusDot.className = `status-dot ${statusClass}`;
        log(`Status: ${message}`);
    }
    
    function retryConnection() {
        if (isRetrying) return;
        isRetrying = true;
        
        log('Retrying connection...');
        closeConnection();
        
        setTimeout(() => {
            initializeConnection();
            isRetrying = false;
        }, 1000);
    }
    
    function closeConnection() {
        if (pc) {
            pc.close();
            pc = null;
        }
        if (socket) {
            socket.disconnect();
        }
        screenVideo.srcObject = null;
        webrtcState.textContent = 'WebRTC: Closed';
        iceState.textContent = 'ICE: Closed';
        videoState.textContent = 'Video: Closed';
    }
    
    async function createPeerConnection() {
        closeConnection();
        connectionAttempts++;
        log(`Creating WebRTC connection (attempt ${connectionAttempts})`);
        
        const config = {
            iceServers: [
                { urls: 'stun:stun.l.google.com:19302' },
                { urls: 'stun:stun1.l.google.com:19302' },
                { 
                    urls: 'turn:numb.viagenie.ca:3478',
                    username: 'webrtc@live.com',
                    credential: 'muazkh'
                },
                {
                    urls: 'turn:relay.metered.ca:80',
                    username: '85d4fae4a3569d1a11e09a7a',
                    credential: 'JZEOEt2V3Qb0y27GRntt2u2PAYA='
                }
            ],
            iceCandidatePoolSize: 10
        };
        
        pc = new RTCPeerConnection(config);
        webrtcState.textContent = 'WebRTC: Creating...';
        
        pc.onicecandidate = (event) => {
            if (event.candidate) {
                log(`Sending ICE candidate: ${event.candidate.candidate.substring(0, 30)}...`);
                socket.emit('webrtc_ice_candidate', { candidate: event.candidate.toJSON() });
            }
        };
        
        pc.ontrack = (event) => {
            log(`Received ${event.track.kind} track`);
            if (event.track.kind === 'video') {
                screenVideo.srcObject = event.streams[0];
                videoState.textContent = 'Video: Stream received';
            }
        };
        
        pc.onconnectionstatechange = () => {
            const state = pc.connectionState;
            webrtcState.textContent = `WebRTC: ${state}`;
            log(`WebRTC connection state: ${state}`);
            
            if (state === 'connected') {
                updateStatus('status-connected', 'Connected! 🎉');
                videoState.textContent = 'Video: Connected';
            } else if (state === 'failed') {
                updateStatus('status-disconnected', 'Connection failed 😞');
                setTimeout(retryConnection, 2000);
            } else if (state === 'disconnected') {
                updateStatus('status-disconnected', 'Disconnected');
            }
        };
        
        pc.oniceconnectionstatechange = () => {
            const state = pc.iceConnectionState;
            iceState.textContent = `ICE: ${state}`;
            log(`ICE connection state: ${state}`);
        };
        
        pc.onicegatheringstatechange = () => {
            log(`ICE gathering state: ${pc.iceGatheringState}`);
        };
        
        pc.onicecandidateerror = (event) => {
            log(`ICE candidate error: ${event.errorText}`);
        };
    }
    
    function initializeConnection() {
        socket = io();
        
        socket.on('connect', () => {
            updateStatus('status-connecting', 'Server connected, waiting for screen...');
            socket.emit('viewer_ready');
        });
        
        socket.on('disconnect', () => {
            updateStatus('status-disconnected', 'Server disconnected');
        });
        
        socket.on('client_disconnected', () => {
            updateStatus('status-disconnected', 'Remote PC disconnected');
            closeConnection();
        });
        
        socket.on('webrtc_offer', async (data) => {
            try {
                log('Received WebRTC offer');
                await createPeerConnection();
                
                await pc.setRemoteDescription(new RTCSessionDescription(data.offer));
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                
                socket.emit('webrtc_answer', { answer: pc.localDescription.toJSON() });
                log('Sent WebRTC answer');
            } catch (error) {
                log(`Error handling offer: ${error.message}`);
            }
        });
        
        socket.on('webrtc_ice_candidate', async (data) => {
            if (pc && data.candidate) {
                try {
                    await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
                    log('Added ICE candidate');
                } catch (error) {
                    log(`Error adding ICE candidate: ${error.message}`);
                }
            }
        });
        
        socket.on('error', (error) => {
            log(`Socket error: ${error}`);
        });
    }
    
    // Video event listeners
    screenVideo.addEventListener('loadstart', () => {
        videoState.textContent = 'Video: Loading...';
        log('Video started loading');
    });
    
    screenVideo.addEventListener('loadedmetadata', () => {
        videoState.textContent = 'Video: Metadata loaded';
        log('Video metadata loaded');
    });
    
    screenVideo.addEventListener('canplay', () => {
        videoState.textContent = 'Video: Can play';
        log('Video can play');
    });
    
    screenVideo.addEventListener('play', () => {
        videoState.textContent = 'Video: Playing';
        log('Video is playing');
    });
    
    screenVideo.addEventListener('error', (e) => {
        videoState.textContent = 'Video: Error';
        log(`Video error: ${e.error?.message || 'Unknown error'}`);
    });
    
    // Mouse control
    document.getElementById('screen-view-area').addEventListener('mousemove', (e) => {
        const rect = screenVideo.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        
        const x = Math.round((e.offsetX / rect.width) * remoteDimensions.width);
        const y = Math.round((e.offsetY / rect.height) * remoteDimensions.height);
        
        if (socket && socket.connected) {
            socket.emit('control_command', { action: 'move', x, y });
        }
    });
    
    document.getElementById('screen-view-area').addEventListener('click', (e) => {
        const rect = screenVideo.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        
        const x = Math.round((e.offsetX / rect.width) * remoteDimensions.width);
        const y = Math.round((e.offsetY / rect.height) * remoteDimensions.height);
        
        if (socket && socket.connected) {
            socket.emit('control_command', { action: 'click', button: 'left', x, y });
        }
    });
    
    // Keyboard control
    document.addEventListener('keydown', (e) => {
        if (socket && socket.connected) {
            e.preventDefault();
            socket.emit('control_command', { action: 'keydown', key: e.key, code: e.code });
        }
    });
    
    document.addEventListener('keyup', (e) => {
        if (socket && socket.connected) {
            e.preventDefault();
            socket.emit('control_command', { action: 'keyup', key: e.key, code: e.code });
        }
    });
    
    // Initialize on page load
    initializeConnection();
    </script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template_string(PUBLIC_INTERFACE_HTML)

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    if request.sid == client_pc_sid:
        logger.warning("Remote PC disconnected.")
        client_pc_sid = None
        emit('client_disconnected', broadcast=True, include_self=False)
    else:
        logger.info(f"Viewer {request.sid} disconnected.")
        if client_pc_sid:
            emit('controller_disconnected', {'sid': request.sid}, room=client_pc_sid)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid, client_pc_dimensions
    if data.get('token') == ACCESS_PASSWORD:
        client_pc_sid = request.sid
        client_pc_dimensions = data.get('dimensions', {'width': 1920, 'height': 1080})
        logger.info(f"Remote PC registered: {client_pc_sid} with dimensions {client_pc_dimensions}")
        emit('client_ready', broadcast=True)
    else:
        logger.warning(f"Failed registration attempt from SID {request.sid}")

@socketio.on('viewer_ready')
def handle_viewer_ready():
    logger.info(f"Viewer {request.sid} is ready")
    if client_pc_sid:
        logger.info(f"Telling PC to start WebRTC for viewer {request.sid}")
        emit('start_webrtc_for_controller', {'sid': request.sid}, room=client_pc_sid)
    else:
        logger.warning(f"Viewer {request.sid} ready but no PC connected")

# --- WebRTC Relay Handlers ---
@socketio.on('webrtc_offer')
def handle_webrtc_offer(data):
    if request.sid == client_pc_sid and 'to_sid' in data:
        logger.info(f"Relaying WebRTC offer from PC to viewer {data['to_sid']}")
        emit('webrtc_offer', {'offer': data['offer']}, room=data['to_sid'])

@socketio.on('webrtc_answer')
def handle_webrtc_answer(data):
    if client_pc_sid:
        logger.info(f"Relaying WebRTC answer from viewer {request.sid} to PC")
        emit('webrtc_answer', {'answer': data['answer'], 'from_sid': request.sid}, room=client_pc_sid)

@socketio.on('webrtc_ice_candidate')
def handle_webrtc_ice_candidate(data):
    if client_pc_sid and 'from_sid' not in data:
        # From viewer to PC
        logger.info(f"Relaying ICE candidate from viewer {request.sid} to PC")
        emit('webrtc_ice_candidate', {'candidate': data['candidate'], 'from_sid': request.sid}, room=client_pc_sid)
    elif 'to_sid' in data:
        # From PC to viewer
        logger.info(f"Relaying ICE candidate from PC to viewer {data['to_sid']}")
        emit('webrtc_ice_candidate', {'candidate': data['candidate']}, room=data['to_sid'])

# --- Command Handlers ---
@socketio.on('control_command')
def handle_control_command(data):
    if client_pc_sid:
        emit('command', data, room=client_pc_sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
