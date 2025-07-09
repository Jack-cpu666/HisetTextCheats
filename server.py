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

# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates ---
LOGIN_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Login</title><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-100 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm"><h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Login</h1>{% if error %}<div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4"><span>{{ error }}</span></div>{% endif %}<form method="POST" action="{{ url_for('index') }}"><div class="mb-4"><label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label><input type="password" id="password" name="password" required class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"></div><button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md">Login</button></form></div></body></html>
"""

INTERFACE_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Remote Control</title><script src="https://cdn.tailwindcss.com"></script><script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script><style>html, body { height: 100%; overflow: hidden; } .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; } .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; } #main-content { display: flex; flex-direction: column; height: calc(100vh - 3.5rem); } #screen-view-area { flex-grow: 1; display: flex; align-items: center; justify-content: center; background-color: #000; overflow: hidden; transition: height 0.3s ease; position: relative; } #screen-video { max-width: 100%; max-height: 100%; cursor: crosshair; object-fit: contain; } #debug-info { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.7); color: white; padding: 5px; font-size: 12px; border-radius: 3px; } #text-input-container { height: 0; overflow: hidden; background-color: #f0f4f8; padding: 0; transition: all 0.3s ease; display: flex; flex-direction: column; } #injection-textarea { flex-grow: 1; resize: none; border-radius: 0.25rem; border: 1px solid #ccc; padding: 0.5rem; font-family: monospace; } .control-button { background-color: #3b82f6; color: white; padding: 0.5rem 1rem; border-radius: 0.25rem; transition: background-color 0.2s; } .control-button:hover { background-color: #2563eb; } .control-button.active { background-color: #16a34a; } .control-button.active:hover { background-color: #15803d; } body.text-mode #screen-view-area { height: 50%; } body.text-mode #text-input-container { height: 50%; padding: 1rem; }</style></head><body class="bg-gray-200" tabindex="0"><header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md h-14"><h1 class="text-lg font-semibold">Remote Desktop Control</h1><div class="flex items-center space-x-4"><button id="toggle-text-mode" class="control-button">Text Input</button><div id="connection-status" class="flex items-center text-sm"><span id="status-dot" class="status-dot status-connecting"></span><span id="status-text">Connecting...</span></div><a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md">Logout</a></div></header><main id="main-content"><div id="screen-view-area"><video id="screen-video" autoplay playsinline muted></video><div id="debug-info">Debug: Initializing...</div></div><div id="text-input-container"><textarea id="injection-textarea" placeholder="Paste or type text here. Client will type this on F2 press."></textarea><div class="flex justify-end items-center mt-2"><span id="injection-status" class="text-sm text-green-600 mr-4"></span><button id="send-text-button" class="control-button">Send Text to Client</button></div></div></main><script>
document.addEventListener('DOMContentLoaded', () => {
    const socket = io(); 
    const body = document.body; 
    const screenViewArea = document.getElementById('screen-view-area'); 
    const screenVideo = document.getElementById('screen-video'); 
    const statusText = document.getElementById('status-text'); 
    const statusDot = document.getElementById('status-dot'); 
    const toggleTextModeBtn = document.getElementById('toggle-text-mode'); 
    const injectionTextarea = document.getElementById('injection-textarea'); 
    const sendTextBtn = document.getElementById('send-text-button'); 
    const injectionStatus = document.getElementById('injection-status');
    const debugInfo = document.getElementById('debug-info');
    
    let pc = null; 
    let remoteDimensions = { width: 1920, height: 1080 };
    let connectionAttempts = 0;
    
    function updateStatus(s, msg) { 
        statusText.textContent = msg; 
        statusDot.className = `status-dot ${s}`; 
        updateDebugInfo(`Status: ${msg}`);
    }
    
    function updateDebugInfo(msg) {
        debugInfo.textContent = msg;
        console.log(msg);
    }
    
    function closeConnection() { 
        if (pc) { 
            pc.close(); 
            pc = null; 
        } 
        screenVideo.srcObject = null; 
        updateDebugInfo('Connection closed');
    }
    
    async function createPeerConnection() { 
        closeConnection(); 
        connectionAttempts++;
        updateDebugInfo(`Creating peer connection (attempt ${connectionAttempts})`);
        
        // Enhanced ICE server configuration
        const pcConfig = {
            iceServers: [
                { urls: 'stun:stun.l.google.com:19302' },
                { urls: 'stun:stun1.l.google.com:19302' },
                { urls: 'stun:stun2.l.google.com:19302' },
                { 
                    urls: "turn:numb.viagenie.ca", 
                    username: "webrtc@live.com", 
                    credential: "muazkh" 
                },
                {
                    urls: "turn:relay.metered.ca:80",
                    username: "85d4fae4a3569d1a11e09a7a",
                    credential: "JZEOEt2V3Qb0y27GRntt2u2PAYA="
                },
                {
                    urls: "turn:relay.metered.ca:443",
                    username: "85d4fae4a3569d1a11e09a7a",
                    credential: "JZEOEt2V3Qb0y27GRntt2u2PAYA="
                }
            ],
            iceCandidatePoolSize: 10,
            bundlePolicy: 'max-bundle',
            rtcpMuxPolicy: 'require'
        };
        
        pc = new RTCPeerConnection(pcConfig); 
        
        pc.onicecandidate = e => { 
            if (e.candidate) {
                updateDebugInfo(`Sending ICE candidate: ${e.candidate.candidate.substring(0, 50)}...`);
                socket.emit('webrtc_ice_candidate', { candidate: e.candidate.toJSON() }); 
            }
        }; 
        
        pc.ontrack = e => { 
            updateDebugInfo(`Received track: ${e.track.kind}`);
            if (e.track.kind === 'video') {
                if (screenVideo.srcObject !== e.streams[0]) {
                    screenVideo.srcObject = e.streams[0];
                    updateDebugInfo('Video stream connected');
                }
            }
        }; 
        
        pc.onconnectionstatechange = () => { 
            updateDebugInfo(`Connection state: ${pc.connectionState}`);
            if(pc.connectionState === 'connected') {
                updateStatus('status-connected', 'Remote PC Connected');
                updateDebugInfo('WebRTC connected successfully');
            } else if (['disconnected', 'failed', 'closed'].includes(pc.connectionState)) {
                updateStatus('status-disconnected', 'Video Disconnected');
                updateDebugInfo(`Connection failed: ${pc.connectionState}`);
            }
        };
        
        pc.onicecandidateerror = (e) => {
            updateDebugInfo(`ICE candidate error: ${e.errorText}`);
        };
        
        pc.oniceconnectionstatechange = () => {
            updateDebugInfo(`ICE connection state: ${pc.iceConnectionState}`);
        };
        
        pc.onicegatheringstatechange = () => {
            updateDebugInfo(`ICE gathering state: ${pc.iceGatheringState}`);
        };
    }
    
    socket.on('connect', () => { 
        updateStatus('status-connecting', 'Server connected...'); 
        socket.emit('controller_ready'); 
    });
    
    socket.on('disconnect', () => { 
        updateStatus('status-disconnected', 'Server disconnected'); 
        closeConnection(); 
    });
    
    socket.on('client_disconnected', () => { 
        updateStatus('status-disconnected', 'Remote PC Disconnected'); 
        closeConnection(); 
    });
    
    socket.on('webrtc_offer', async (data) => { 
        updateDebugInfo('Received WebRTC offer');
        await createPeerConnection(); 
        await pc.setRemoteDescription(new RTCSessionDescription(data.offer)); 
        const answer = await pc.createAnswer(); 
        await pc.setLocalDescription(answer); 
        socket.emit('webrtc_answer', { answer: pc.localDescription.toJSON() }); 
        updateDebugInfo('Sent WebRTC answer');
    });
    
    socket.on('webrtc_ice_candidate', (data) => { 
        if (pc && data.candidate) {
            pc.addIceCandidate(new RTCIceCandidate(data.candidate))
                .then(() => updateDebugInfo('Added ICE candidate'))
                .catch(e => updateDebugInfo(`ICE candidate error: ${e}`)); 
        }
    });
    
    // Add video event listeners for debugging
    screenVideo.addEventListener('loadstart', () => updateDebugInfo('Video load started'));
    screenVideo.addEventListener('loadedmetadata', () => updateDebugInfo('Video metadata loaded'));
    screenVideo.addEventListener('loadeddata', () => updateDebugInfo('Video data loaded'));
    screenVideo.addEventListener('canplay', () => updateDebugInfo('Video can play'));
    screenVideo.addEventListener('play', () => updateDebugInfo('Video playing'));
    screenVideo.addEventListener('error', (e) => updateDebugInfo(`Video error: ${e.error?.message || 'Unknown'}`));
    
    function sendControl(cmd) { 
        if (socket.connected) socket.emit('control_command', cmd); 
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
        socket.emit('set_injection_text', { text: text }); 
        injectionStatus.textContent = "Sending..."; 
    });
    
    socket.on('text_injection_ack', (data) => { 
        if (data.status === 'success') { 
            injectionStatus.textContent = "Text saved on client!"; 
        } else { 
            injectionStatus.textContent = `Error: ${data.message}`; 
        } 
        setTimeout(() => { 
            injectionStatus.textContent = ''; 
        }, 3000); 
    });
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
    else:
        logger.info(f"Client attempting to connect: {request.sid}")

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
    global client_pc_sid, client_pc_dimensions
    if data.get('token') == ACCESS_PASSWORD:
        client_pc_sid = request.sid
        client_pc_dimensions = data.get('dimensions', {'width': 1920, 'height': 1080})
        logger.info(f"Remote PC registered: {client_pc_sid} with dimensions {client_pc_dimensions}")
        emit('client_ready', broadcast=True)
    else:
        logger.warning(f"Failed registration attempt from SID {request.sid}")

@socketio.on('controller_ready')
def handle_controller_ready():
    if session.get('authenticated') and client_pc_sid:
        logger.info(f"Controller {request.sid} is ready, telling PC to start a new connection for them.")
        emit('start_webrtc_for_controller', {'sid': request.sid}, room=client_pc_sid)
    else:
        logger.warning(f"Controller {request.sid} ready but no PC connected or not authenticated")

# --- Enhanced WebRTC Relay Handlers ---
@socketio.on('webrtc_offer')
def handle_webrtc_offer(data):
    if request.sid == client_pc_sid and 'to_sid' in data:
        logger.info(f"Relaying WebRTC offer from PC to controller {data['to_sid']}")
        emit('webrtc_offer', {'offer': data['offer']}, room=data['to_sid'])
    else:
        logger.warning(f"Invalid WebRTC offer from {request.sid}")

@socketio.on('webrtc_answer')
def handle_webrtc_answer(data):
    if client_pc_sid and session.get('authenticated'):
        logger.info(f"Relaying WebRTC answer from controller {request.sid} to PC")
        emit('webrtc_answer', {'answer': data['answer'], 'from_sid': request.sid}, room=client_pc_sid)
    else:
        logger.warning(f"Invalid WebRTC answer from {request.sid}")

@socketio.on('webrtc_ice_candidate')
def handle_webrtc_ice_candidate(data):
    if client_pc_sid and 'from_sid' not in data:
        # From controller to PC
        logger.info(f"Relaying ICE candidate from controller {request.sid} to PC")
        emit('webrtc_ice_candidate', {'candidate': data['candidate'], 'from_sid': request.sid}, room=client_pc_sid)
    elif 'to_sid' in data:
        # From PC to controller
        logger.info(f"Relaying ICE candidate from PC to controller {data['to_sid']}")
        emit('webrtc_ice_candidate', {'candidate': data['candidate']}, room=data['to_sid'])
    else:
        logger.warning(f"Invalid ICE candidate from {request.sid}")

# --- Command Handlers ---
@socketio.on('control_command')
def handle_control_command(data):
    if client_pc_sid and session.get('authenticated'):
        emit('command', data, room=client_pc_sid)

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if client_pc_sid and session.get('authenticated'):
        emit('receive_injection_text', data, room=client_pc_sid)
        emit('text_injection_ack', {'status': 'success'}, room=request.sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
