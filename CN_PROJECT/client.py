"""
LAN Collaboration Suite - Client Application
MODERN DARK THEME - Optimized Layout v2.0
"""

import socket
import threading
import json
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog, ttk
from datetime import datetime
import cv2
import numpy as np
from PIL import Image, ImageTk, ImageGrab
import struct
import time
import pyaudio
import os
import config

# ==================== CLIENT CLASS ====================

class CollaborationClient:
    def __init__(self, server_ip, username):
        self.server_ip = server_ip
        self.username = username
        self.client_id = None
        
        self.tcp_socket = None
        self.udp_video_socket = None
        self.udp_audio_socket = None
        self.tcp_screen_socket = None
        
        self.connected = False
        self.running = False
        
        self.video_capture = None
        self.video_streaming = False
        self.video_thread = None
        
        self.audio = None
        self.audio_input_stream = None
        self.audio_output_stream = None
        self.audio_streaming = False
        self.audio_send_thread = None
        self.audio_receive_thread = None
        
        self.screen_sharing = False
        self.screen_thread = None
        self.is_presenter = False
        
        self.available_files = {}
        self.files_lock = threading.Lock()
        
        if not os.path.exists(config.CLIENT_DOWNLOADS_DIR):
            os.makedirs(config.CLIENT_DOWNLOADS_DIR)
        
        self.received_frames = {}
        self.frame_lock = threading.Lock()
        self.shared_screen = None
        self.screen_lock = threading.Lock()
        self.current_presenter = None
        
        self.gui = None
    
    def connect(self):
        """Connect to server"""
        try:
            print(f"🔌 Connecting to {self.server_ip}:{config.TCP_PORT}...")
            
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(10)
            self.tcp_socket.connect((self.server_ip, config.TCP_PORT))
            
            connect_msg = {
                'type': config.MSG_CONNECT,
                'username': self.username
            }
            self.tcp_socket.send(json.dumps(connect_msg).encode('utf-8'))
            
            ack_data = self.tcp_socket.recv(config.CHAT_BUFFER_SIZE)
            if not ack_data:
                return False, "No response from server"
            
            ack = json.loads(ack_data.decode('utf-8'))
            
            if ack.get('type') == 'ERROR':
                return False, ack.get('message', 'Connection failed')
            
            if ack.get('type') == 'ACK':
                self.client_id = ack.get('client_id')
                self.connected = True
                self.running = True
                
                with self.files_lock:
                    self.available_files = ack.get('available_files', {})
                
                self.udp_video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_video_socket.bind(('', 0))
                
                self.udp_audio_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_audio_socket.bind(('', 0))
                
                self.tcp_socket.settimeout(300)
                
                print(f"✅ Connected as {self.username}")
                
                threading.Thread(target=self.receive_messages, daemon=True).start()
                threading.Thread(target=self.receive_video_frames, daemon=True).start()
                threading.Thread(target=self.receive_screen_share, daemon=True).start()
                
                return True, "Connected successfully"
            else:
                return False, "Connection failed"
                
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return False
    
    def receive_messages(self):
        """Receive TCP messages"""
        while self.running:
            try:
                data = self.tcp_socket.recv(4)
                if not data or len(data) < 4:
                    break
                
                try:
                    msg_size = struct.unpack('I', data)[0]
                    
                    if msg_size < config.CHAT_BUFFER_SIZE:
                        msg_data = self.tcp_socket.recv(msg_size)
                        message = json.loads(msg_data.decode('utf-8'))
                        
                        if message.get('type') == config.MSG_SCREEN_SHARE:
                            frame_size_data = self.tcp_socket.recv(4)
                            if len(frame_size_data) == 4:
                                frame_size = struct.unpack('I', frame_size_data)[0]
                                
                                frame_data = b''
                                while len(frame_data) < frame_size:
                                    chunk = self.tcp_socket.recv(min(config.SCREEN_BUFFER_SIZE, 
                                                                     frame_size - len(frame_data)))
                                    if not chunk:
                                        break
                                    frame_data += chunk
                                
                                self.process_screen_frame(message.get('presenter_id'), frame_data)
                        else:
                            self.process_message(message)
                    else:
                        remaining = self.tcp_socket.recv(config.CHAT_BUFFER_SIZE - 4)
                        full_data = data + remaining
                        message = json.loads(full_data.decode('utf-8'))
                        self.process_message(message)
                        
                except (json.JSONDecodeError, struct.error):
                    pass
                    
            except Exception as e:
                if self.running:
                    pass
                break
        
        self.connected = False
        if self.gui:
            self.gui.on_disconnect()
    
    def receive_video_frames(self):
        """Receive video frames"""
        last_update_time = 0
        min_update_interval = 1.0 / 15  # 15 FPS maximum update rate
        
        while self.running:
            try:
                self.udp_video_socket.settimeout(5)
                data, _ = self.udp_video_socket.recvfrom(config.VIDEO_BUFFER_SIZE)
                
                if len(data) < 8:
                    continue
                
                try:
                    client_id_length = struct.unpack('I', data[0:4])[0]
                    if client_id_length < 1 or client_id_length > 100:
                        continue
                except:
                    continue
                
                if len(data) < 8 + client_id_length:
                    continue
                
                try:
                    sender_id = data[8:8+client_id_length].decode('utf-8')
                except:
                    continue
                
                frame_data = data[8+client_id_length:]
                
                if len(frame_data) < 10:
                    continue
                
                try:
                    frame_array = np.frombuffer(frame_data, dtype=np.uint8)
                    frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
                    
                    if frame is not None and frame.shape[0] > 0 and frame.shape[1] > 0:
                        current_time = time.time()
                        update_needed = (current_time - last_update_time) >= min_update_interval
                        
                        with self.frame_lock:
                            self.received_frames[sender_id] = frame
                            
                            # Only update display at maximum FPS
                            if update_needed:
                                last_update_time = current_time
                                if self.gui:
                                    # Schedule UI update on the main thread to avoid freezes
                                    if hasattr(self.gui, 'safe_update_video_display'):
                                        self.gui.safe_update_video_display()
                                    else:
                                        self.gui.update_video_display()
                            
                except Exception as e:
                    pass
                
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:
                    pass
    
    def receive_screen_share(self):
        """Receive screen updates"""
        pass
    
    def process_screen_frame(self, presenter_id, frame_data):
        """Process screen frame"""
        try:
            frame_array = np.frombuffer(frame_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
            
            if frame is not None:
                with self.screen_lock:
                    self.shared_screen = frame
                    self.current_presenter = presenter_id
                
                if self.gui:
                    self.gui.update_screen_display()
                    
        except:
            pass
    
    # ==================== VIDEO ====================
    
    def start_video_streaming(self):
        """Start video"""
        if self.video_streaming:
            return True
        
        try:
            self.video_capture = cv2.VideoCapture(0)
            
            if not self.video_capture.isOpened():
                return False
            
            self.video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.VIDEO_WIDTH)
            self.video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.VIDEO_HEIGHT)
            self.video_capture.set(cv2.CAP_PROP_FPS, config.VIDEO_FPS)
            
            self.video_streaming = True
            self.video_thread = threading.Thread(target=self.stream_video, daemon=True)
            self.video_thread.start()
            
            return True
            
        except Exception as e:
            return False
    
    def stream_video(self):
        """Stream video"""
        frame_delay = 1.0 / 15  # Reduce to 15 FPS for smoother display
        last_update_time = 0
        
        while self.video_streaming and self.running:
            try:
                current_time = time.time()
                elapsed = current_time - last_update_time
                
                if elapsed < frame_delay:
                    time.sleep(0.001)  # Small sleep to prevent CPU overuse
                    continue
                    
                last_update_time = current_time
                ret, frame = self.video_capture.read()
                if not ret:
                    continue
                
                frame = cv2.resize(frame, (config.VIDEO_WIDTH, config.VIDEO_HEIGHT))
                
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), config.VIDEO_QUALITY]
                ret, jpeg_frame = cv2.imencode('.jpg', frame, encode_param)
                
                if not ret or jpeg_frame is None:
                    continue
                
                frame_data = jpeg_frame.tobytes()
                
                if len(frame_data) < 10:
                    continue
                
                client_id_bytes = self.client_id.encode('utf-8')
                client_id_length = len(client_id_bytes)
                
                header = struct.pack('I', client_id_length) + struct.pack('I', len(frame_data))
                packet = header + client_id_bytes + frame_data
                
                self.udp_video_socket.sendto(packet, (self.server_ip, config.UDP_VIDEO_PORT))
                
                if self.gui:
                    with self.frame_lock:
                        self.received_frames[self.client_id] = frame
                    # Schedule UI update on the main thread
                    if hasattr(self.gui, 'safe_update_video_display'):
                        self.gui.safe_update_video_display()
                    else:
                        self.gui.update_video_display()
                    
                current_time = time.time()
                elapsed = current_time - last_update_time
                if elapsed < frame_delay:
                    time.sleep(max(0, frame_delay - elapsed))
                
            except Exception as e:
                pass
    
    def stop_video_streaming(self):
        """Stop video"""
        self.video_streaming = False
        
        if self.video_capture:
            try:
                self.video_capture.release()
            except:
                pass
            self.video_capture = None
        
        with self.frame_lock:
            if self.client_id in self.received_frames:
                del self.received_frames[self.client_id]
        
        if self.gui:
            # Proactively clear any self-tile image and stage image
            try:
                for pos in self.gui.video_positions.values():
                    if pos.get('client_id') == self.client_id:
                        pos['video_label'].config(image='', text='Video Disabled')
                        try:
                            pos['video_label'].image = None
                        except Exception:
                            pass
                        pos['name_label'].config(text='Participant')
                        pos['client_id'] = None
                        pos['frame'].place_forget()
                self.gui.stage_label.config(image='', text='')
                self.gui.stage_label.image = None
            except Exception:
                pass
            # Schedule full layout refresh on main thread
            if hasattr(self.gui, 'safe_update_video_display'):
                self.gui.safe_update_video_display()
            else:
                self.gui.update_video_display()
    
    # ==================== AUDIO ====================
    
    def start_audio_streaming(self):
        """Start audio"""
        if self.audio_streaming:
            return True
        
        try:
            self.audio = pyaudio.PyAudio()
            
            self.audio_input_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=config.AUDIO_CHANNELS,
                rate=config.AUDIO_RATE,
                input=True,
                frames_per_buffer=config.AUDIO_CHUNK
            )
            
            self.audio_output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=config.AUDIO_CHANNELS,
                rate=config.AUDIO_RATE,
                output=True,
                frames_per_buffer=config.AUDIO_CHUNK
            )
            
            self.audio_streaming = True
            
            msg = {'type': config.MSG_AUDIO_START}
            self.tcp_socket.send(json.dumps(msg).encode('utf-8'))
            
            self.audio_send_thread = threading.Thread(target=self.stream_audio, daemon=True)
            self.audio_send_thread.start()
            
            self.audio_receive_thread = threading.Thread(target=self.receive_audio, daemon=True)
            self.audio_receive_thread.start()
            
            return True
            
        except Exception as e:
            return False
    
    def stream_audio(self):
        """Stream audio"""
        while self.audio_streaming and self.running:
            try:
                audio_data = self.audio_input_stream.read(config.AUDIO_CHUNK, exception_on_overflow=False)
                
                client_id_bytes = self.client_id.encode('utf-8')
                client_id_length = len(client_id_bytes)
                header = struct.pack('I', client_id_length) + struct.pack('I', len(audio_data))
                packet = header + client_id_bytes + audio_data
                
                self.udp_audio_socket.sendto(packet, (self.server_ip, config.UDP_AUDIO_PORT))
                
            except Exception as e:
                break
    
    def receive_audio(self):
        """Receive audio"""
        while self.audio_streaming and self.running:
            try:
                self.udp_audio_socket.settimeout(5)
                data, _ = self.udp_audio_socket.recvfrom(config.AUDIO_BUFFER_SIZE)
                
                if len(data) < 8:
                    continue
                
                try:
                    client_id_length = struct.unpack('I', data[0:4])[0]
                    if client_id_length < 1 or client_id_length > 100:
                        continue
                except:
                    continue
                
                if len(data) < 8 + client_id_length:
                    continue
                
                audio_data = data[8+client_id_length:]
                
                if self.audio_output_stream and self.audio_output_stream.is_active():
                    self.audio_output_stream.write(audio_data)
                
            except socket.timeout:
                pass
            except Exception as e:
                break
    
    def stop_audio_streaming(self):
        """Stop audio"""
        self.audio_streaming = False
        
        try:
            msg = {'type': config.MSG_AUDIO_STOP}
            self.tcp_socket.send(json.dumps(msg).encode('utf-8'))
        except:
            pass
        
        if self.audio_input_stream:
            try:
                self.audio_input_stream.stop_stream()
                self.audio_input_stream.close()
            except:
                pass
            self.audio_input_stream = None
        
        if self.audio_output_stream:
            try:
                self.audio_output_stream.stop_stream()
                self.audio_output_stream.close()
            except:
                pass
            self.audio_output_stream = None
        
        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass
            self.audio = None
    
    # ==================== SCREEN SHARING ====================
    
    def start_screen_sharing(self):
        """Start screen sharing"""
        if self.screen_sharing:
            return True
        
        try:
            self.tcp_screen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_screen_socket.connect((self.server_ip, config.TCP_SCREEN_PORT))
            
            request_msg = {
                'type': config.MSG_SCREEN_START,
                'client_id': self.client_id
            }
            self.tcp_screen_socket.send(json.dumps(request_msg).encode('utf-8'))
            
            response = self.tcp_screen_socket.recv(config.CHAT_BUFFER_SIZE).decode('utf-8')
            response_msg = json.loads(response)
            
            if response_msg.get('type') == config.MSG_SCREEN_GRANT:
                self.screen_sharing = True
                self.is_presenter = True
                
                self.screen_thread = threading.Thread(target=self.stream_screen, daemon=True)
                self.screen_thread.start()
                
                return True
            else:
                self.tcp_screen_socket.close()
                self.tcp_screen_socket = None
                return False
                
        except Exception as e:
            if self.tcp_screen_socket:
                self.tcp_screen_socket.close()
                self.tcp_screen_socket = None
            return False
    
    def stream_screen(self):
        """Stream screen"""
        frame_delay = 1.0 / config.SCREEN_FPS
        
        while self.screen_sharing and self.running:
            try:
                start_time = time.time()
                
                screenshot = ImageGrab.grab()
                
                width, height = screenshot.size
                if width > config.SCREEN_MAX_WIDTH or height > config.SCREEN_MAX_HEIGHT:
                    scale = min(config.SCREEN_MAX_WIDTH / width, 
                               config.SCREEN_MAX_HEIGHT / height)
                    new_width = int(width * scale)
                    new_height = int(height * scale)
                    screenshot = screenshot.resize((new_width, new_height), Image.LANCZOS)
                
                import io
                buffer = io.BytesIO()
                screenshot.save(buffer, format='JPEG', quality=config.SCREEN_QUALITY)
                frame_data = buffer.getvalue()
                
                frame_size = len(frame_data)
                self.tcp_screen_socket.sendall(struct.pack('I', frame_size))
                self.tcp_screen_socket.sendall(frame_data)
                
                elapsed = time.time() - start_time
                sleep_time = max(0, frame_delay - elapsed)
                time.sleep(sleep_time)
                
            except Exception as e:
                break
    
    def stop_screen_sharing(self):
        """Stop screen sharing"""
        self.screen_sharing = False
        self.is_presenter = False
        
        if self.tcp_screen_socket:
            try:
                self.tcp_screen_socket.close()
            except:
                pass
            self.tcp_screen_socket = None
        
        with self.screen_lock:
            self.shared_screen = None
            self.current_presenter = None
        
        if self.gui:
            self.gui.update_screen_display()
    
    # ==================== FILE SHARING ====================
    
    def upload_file(self, filepath):
        """Upload file"""
        try:
            if not os.path.exists(filepath):
                return False, "File not found"
            
            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)
            
            if filesize > config.MAX_FILE_SIZE:
                return False, "Too large"
            
            file_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_socket.connect((self.server_ip, config.TCP_FILE_PORT))
            
            request = {
                'type': config.MSG_FILE_UPLOAD,
                'filename': filename,
                'filesize': filesize,
                'client_id': self.client_id
            }
            file_socket.send(json.dumps(request).encode('utf-8'))
            
            ack = file_socket.recv(1024)
            
            with open(filepath, 'rb') as f:
                sent = 0
                while sent < filesize:
                    chunk = f.read(config.FILE_CHUNK_SIZE)
                    if not chunk:
                        break
                    file_socket.sendall(chunk)
                    sent += len(chunk)
                    
                    if self.gui:
                        progress = (sent / filesize) * 100
                        self.gui.update_upload_progress(progress)
            
            response_data = file_socket.recv(config.CHAT_BUFFER_SIZE)
            response = json.loads(response_data.decode('utf-8'))
            
            file_socket.close()
            
            if response.get('status') == 'success':
                return True, "Uploaded"
            else:
                return False, response.get('message', 'Failed')
                
        except Exception as e:
            return False, str(e)
    
    def download_file(self, filename):
        """Download file"""
        try:
            file_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_socket.connect((self.server_ip, config.TCP_FILE_PORT))
            
            request = {
                'type': config.MSG_FILE_DOWNLOAD,
                'filename': filename,
                'client_id': self.client_id
            }
            file_socket.send(json.dumps(request).encode('utf-8'))
            
            response_data = file_socket.recv(config.CHAT_BUFFER_SIZE)
            response = json.loads(response_data.decode('utf-8'))
            
            if response.get('status') != 'success':
                file_socket.close()
                return False, response.get('message', 'Failed')
            
            filesize = response.get('filesize')
            
            file_socket.send(b'ACK')
            
            filepath = os.path.join(config.CLIENT_DOWNLOADS_DIR, filename)
            received = 0
            
            with open(filepath, 'wb') as f:
                while received < filesize:
                    chunk = file_socket.recv(min(config.FILE_CHUNK_SIZE, 
                                                filesize - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    
                    if self.gui:
                        progress = (received / filesize) * 100
                        self.gui.update_download_progress(progress)
            
            file_socket.close()
            
            if received == filesize:
                return True, f"Saved"
            else:
                return False, "Incomplete"
                
        except Exception as e:
            return False, str(e)
    
    def get_available_files(self):
        """Get file list"""
        with self.files_lock:
            return dict(self.available_files)
    
    def update_available_files(self, files):
        """Update file list"""
        with self.files_lock:
            self.available_files = dict(files)
    
    # ==================== HELPERS ====================
    
    def get_video_frames(self):
        """Get video frames"""
        with self.frame_lock:
            return dict(self.received_frames)
    
    def get_shared_screen(self):
        """Get shared screen"""
        with self.screen_lock:
            return self.shared_screen, self.current_presenter
    
    def process_message(self, message):
        """Process message"""
        msg_type = message.get('type')
        
        if msg_type == config.MSG_CHAT:
            username = message.get('username')
            content = message.get('content')
            timestamp = message.get('timestamp')
            is_system = message.get('system', False)
            
            if self.gui:
                self.gui.display_chat_message(username, content, timestamp, is_system)
        
        elif msg_type == config.MSG_SCREEN_STOP:
            with self.screen_lock:
                self.shared_screen = None
                self.current_presenter = None
            
            if self.gui:
                self.gui.update_screen_display()
        
        elif msg_type == config.MSG_FILE_UPDATE:
            files = message.get('files', {})
            self.update_available_files(files)
            
            if self.gui:
                self.gui.update_file_list()
    
    def send_chat_message(self, content):
        """Send chat"""
        if not self.connected:
            return False
        
        try:
            message = {
                'type': config.MSG_CHAT,
                'content': content,
                'timestamp': datetime.now().isoformat()
            }
            self.tcp_socket.send(json.dumps(message).encode('utf-8'))
            return True
        except:
            return False
    
    def disconnect(self):
        """Disconnect"""
        if not self.connected:
            return
        
        self.running = False
        self.stop_video_streaming()
        self.stop_audio_streaming()
        self.stop_screen_sharing()
        
        try:
            disconnect_msg = {'type': config.MSG_DISCONNECT}
            self.tcp_socket.send(json.dumps(disconnect_msg).encode('utf-8'))
        except:
            pass
        
        if self.tcp_socket:
            self.tcp_socket.close()
        if self.udp_video_socket:
            self.udp_video_socket.close()
        if self.udp_audio_socket:
            self.udp_audio_socket.close()
        
        self.connected = False


# ==================== MODERN GUI ====================

class ModernClientGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Collaboration Suite - Dark Theme")
        self.root.geometry("1920x1080")
        self.root.resizable(True, True)
        
        # Dark theme colors
        self.BG_PRIMARY = "#0d1117"
        self.BG_SECONDARY = "#161b22"
        self.BG_TERTIARY = "#21262d"
        self.FG_PRIMARY = "#c9d1d9"
        self.FG_SECONDARY = "#8b949e"
        self.ACCENT = "#58a6ff"
        self.SUCCESS = "#3fb950"
        self.DANGER = "#f85149"
        
        # Layout constants
        self.ASPECT_RATIO = 16/9  # Video aspect ratio
        self.MIN_TILE_WIDTH = 150  # Minimum tile width
        self.MIN_TILE_HEIGHT = int(self.MIN_TILE_WIDTH / self.ASPECT_RATIO)  # ~84px
        self.ANIMATION_DURATION = 300  # Animation duration in ms
        self.ANIMATION_STEPS = 20  # Steps for smooth animation
        
        # Animation state
        self.animation_jobs = {}  # Store animation jobs for cleanup
        
        # View mode and speaker tracking
        self.view_mode = tk.StringVar(value="gallery")
        self.active_speaker = None  # Currently speaking participant
        self.last_audio_activity = {}  # Track last audio activity time per participant
        self.audio_fade_threshold = 1.0  # Seconds of silence before removing highlight
        
        # Screen sharing and layout state
        self.pinned_participant = None  # Manually pinned participant
        self.is_paused = False  # Screen sharing pause state
        self.is_fullscreen = False  # Fullscreen state
        self.zoom_fit = True  # Screen zoom state
        self.paused_frame = None  # Store frame when paused
        self.restore_chat = False  # For fullscreen restore
        self.restore_files = False  # For fullscreen restore
        self.normal_state = None  # Store window state for fullscreen
        
        self.root.configure(bg=self.BG_PRIMARY)
        
        self.client = None
        self.video_labels = {}
        # chat_panel will be an embedded chat area inside the main window (not a separate Toplevel)
        self.chat_panel = None
        self.files_window = None

        self.create_main_interface()
        self.create_styles()

    def safe_update_video_display(self):
        """Thread-safe way to refresh video UI from background threads."""
        try:
            self.root.after(0, self.update_video_display)
        except Exception:
            pass
    
    def create_styles(self):
        """Create custom styles"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Configure dark theme
        style.configure('TTreeview',
                       background=self.BG_SECONDARY,
                       foreground=self.FG_PRIMARY,
                       fieldbackground=self.BG_SECONDARY,
                       borderwidth=0)
        style.map('TTreeview', background=[('selected', self.ACCENT)])
        
        style.configure('TScrollbar',
                       background=self.BG_TERTIARY,
                       troughcolor=self.BG_SECONDARY,
                       borderwidth=0)
    
    def create_main_interface(self):
        """Create main interface"""
        # Top bar
        top_bar = tk.Frame(self.root, bg=self.BG_SECONDARY, height=80)
        top_bar.pack(side=tk.TOP, fill=tk.X)
        top_bar.pack_propagate(False)
        
        # Create presenter toolbar (initially hidden)
        self.presenter_toolbar = tk.Frame(self.root, bg=self.BG_TERTIARY, bd=1, relief=tk.RAISED)
        
        # Stop sharing button
        self.stop_share_btn = tk.Button(self.presenter_toolbar, text="🟥 Stop Share",
                                      command=self.stop_screen_sharing,
                                      bg=self.DANGER, fg="white",
                                      font=('Segoe UI', 9, 'bold'),
                                      width=12, bd=0, cursor="hand2")
        self.stop_share_btn.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Pause button
        self.pause_share_btn = tk.Button(self.presenter_toolbar, text="⏸️ Pause",
                                       command=self.toggle_screen_pause,
                                       bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                       font=('Segoe UI', 9, 'bold'),
                                       width=12, bd=0, cursor="hand2")
        self.pause_share_btn.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Create viewer toolbar (initially hidden)
        self.viewer_toolbar = tk.Frame(self.root, bg=self.BG_TERTIARY, bd=1, relief=tk.RAISED)
        
        # Fullscreen toggle
        self.fullscreen_btn = tk.Button(self.viewer_toolbar, text="🔲 Fullscreen",
                                      command=self.toggle_fullscreen,
                                      bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                      font=('Segoe UI', 9, 'bold'),
                                      width=12, bd=0, cursor="hand2")
        self.fullscreen_btn.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Zoom fit toggle
        self.zoom_btn = tk.Button(self.viewer_toolbar, text="🔍 Fit",
                                command=self.toggle_screen_zoom,
                                bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                font=('Segoe UI', 9, 'bold'),
                                width=12, bd=0, cursor="hand2")
        self.zoom_btn.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Connection row
        conn_frame = tk.Frame(top_bar, bg=self.BG_SECONDARY)
        conn_frame.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(10, 5))
        
        tk.Label(conn_frame, text="IP Address", bg=self.BG_SECONDARY, fg=self.FG_PRIMARY,
                font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(0, 5))
        
        self.ip_entry = tk.Entry(conn_frame, width=15, font=('Segoe UI', 9),
                                bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                insertbackground=self.FG_PRIMARY, bd=0)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.pack(side=tk.LEFT, padx=5)
        
        tk.Label(conn_frame, text="Username", bg=self.BG_SECONDARY, fg=self.FG_PRIMARY,
                font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(20, 5))
        
        self.username_entry = tk.Entry(conn_frame, width=15, font=('Segoe UI', 9),
                                      bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                      insertbackground=self.FG_PRIMARY, bd=0)
        self.username_entry.insert(0, "User")
        self.username_entry.pack(side=tk.LEFT, padx=5)
        
        self.connect_btn = tk.Button(conn_frame, text="Connect",
                                    command=self.connect_to_server,
                                    bg=self.SUCCESS, fg="white",
                                    font=('Segoe UI', 10, 'bold'),
                                    width=12, bd=0, cursor="hand2")
        self.connect_btn.pack(side=tk.LEFT, padx=10)
        
        self.status_label = tk.Label(conn_frame, text="● Offline",
                                    bg=self.BG_SECONDARY, fg=self.DANGER,
                                    font=('Segoe UI', 10, 'bold'))
        self.status_label.pack(side=tk.LEFT, padx=15)
        
        # Controls row
        ctrl_frame = tk.Frame(top_bar, bg=self.BG_SECONDARY)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(5, 10))
        
        # View mode toggle (Gallery/Speaker)
        self.view_mode = tk.StringVar(value="gallery")
        self.view_btn = tk.Button(ctrl_frame, text="👥 Gallery View",
                                command=self.toggle_view_mode,
                                bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                font=('Segoe UI', 9, 'bold'),
                                width=12, bd=0, cursor="hand2")
        self.view_btn.pack(side=tk.LEFT, padx=3)

        # Video button
        self.video_btn = tk.Button(ctrl_frame, text="📹 Video",
                                  command=self.toggle_video,
                                  bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                  font=('Segoe UI', 9, 'bold'),
                                  width=12, bd=0, cursor="hand2",
                                  state='disabled')
        self.video_btn.pack(side=tk.LEFT, padx=3)
        
        # Audio button
        self.audio_btn = tk.Button(ctrl_frame, text="🎤 Audio",
                                  command=self.toggle_audio,
                                  bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                  font=('Segoe UI', 9, 'bold'),
                                  width=12, bd=0, cursor="hand2",
                                  state='disabled')
        self.audio_btn.pack(side=tk.LEFT, padx=3)
        
        # Screen button
        self.screen_btn = tk.Button(ctrl_frame, text="🖥️  Screen",
                                   command=self.toggle_screen,
                                   bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                   font=('Segoe UI', 9, 'bold'),
                                   width=12, bd=0, cursor="hand2",
                                   state='disabled')
        self.screen_btn.pack(side=tk.LEFT, padx=3)
        
        # Chat button
        self.chat_btn = tk.Button(ctrl_frame, text="💬 Chat",
                                 command=self.open_chat_window,
                                 bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                 font=('Segoe UI', 9, 'bold'),
                                 width=12, bd=0, cursor="hand2",
                                 state='disabled')
        self.chat_btn.pack(side=tk.LEFT, padx=3)
        
        # Files button
        self.files_btn = tk.Button(ctrl_frame, text="📁 Files",
                                  command=self.open_files_window,
                                  bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                  font=('Segoe UI', 9, 'bold'),
                                  width=12, bd=0, cursor="hand2",
                                  state='disabled')
        self.files_btn.pack(side=tk.LEFT, padx=3)
        
        # Main content area (single main stage + bottom filmstrip)
        content_frame = tk.Frame(self.root, bg=self.BG_PRIMARY)
        content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # MAIN STAGE: will show screen share or large video grid
        self.stage_frame = tk.Frame(content_frame, bg=self.BG_SECONDARY)
        self.stage_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.stage_label = tk.Label(self.stage_frame, text="Waiting for presentation...",
                                    bg=self.BG_TERTIARY, fg=self.FG_SECONDARY,
                                    font=('Segoe UI', 12, 'italic'))
        self.stage_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # FILMSTRIP / BOTTOM BAR: participant tiles
        bottom_bar = tk.Frame(content_frame, bg=self.BG_SECONDARY, height=140)
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)
        bottom_bar.pack_propagate(False)

        filmstrip_frame = tk.Frame(bottom_bar, bg=self.BG_SECONDARY)
        filmstrip_frame.pack(fill=tk.X, padx=10, pady=10)

        # Create video grid frames - support up to 12 participants
        self.video_positions = {}
        for i in range(12):
            # Create frame in main stage for grid layout
            frame = tk.Frame(self.stage_frame, bg=self.BG_TERTIARY)
            
            # Video display label
            label = tk.Label(frame, text="Video Disabled",
                           bg=self.BG_TERTIARY, fg=self.FG_SECONDARY,
                           font=('Segoe UI', 9))
            label.pack(fill=tk.BOTH, expand=True)

            # Participant info bar (name + status)
            name_label = tk.Label(frame, text="Participant",
                                bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                font=('Segoe UI', 8))
            name_label.pack(side=tk.BOTTOM, fill=tk.X)

            self.video_positions[i] = {
                'frame': frame,
                'video_label': label,
                'name_label': name_label,
                'client_id': None
            }
    
    def open_chat_window(self):
        """Show embedded chat panel inside the main window.

        This keeps the chat inside the same application window instead of opening
        a separate Toplevel window.
        """
        # If already created and visible, lift it; otherwise create and place it
        if self.chat_panel is not None and self.chat_panel.winfo_ismapped():
            # If already visible, hide it (toggle)
            self.chat_panel.place_forget()
            return

        if self.chat_panel is None:
            # Create embedded panel
            self.chat_panel = tk.Frame(self.root, bg=self.BG_PRIMARY, bd=1, relief=tk.RIDGE)

            # Chat display (inside panel)
            self.chat_display = scrolledtext.ScrolledText(
                self.chat_panel, state='disabled', wrap=tk.WORD,
                font=('Segoe UI', 9), bg=self.BG_SECONDARY, fg=self.FG_PRIMARY,
                insertbackground=self.FG_PRIMARY, bd=0)
            self.chat_display.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            self.chat_display.tag_config('system', foreground=self.FG_SECONDARY)
            self.chat_display.tag_config('username', foreground=self.ACCENT, font=('Segoe UI', 9, 'bold'))
            self.chat_display.tag_config('timestamp', foreground=self.FG_SECONDARY, font=('Segoe UI', 8))

            # Input frame
            input_frame = tk.Frame(self.chat_panel, bg=self.BG_SECONDARY)
            input_frame.pack(fill=tk.X, padx=10, pady=10)

            self.chat_entry = tk.Entry(input_frame, font=('Segoe UI', 9),
                                      bg=self.BG_TERTIARY, fg=self.FG_PRIMARY,
                                      insertbackground=self.FG_PRIMARY, bd=0)
            self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            self.chat_entry.bind('<Return>', self.send_chat_message)

            self.send_btn = tk.Button(input_frame, text="Send",
                                     command=self.send_chat_message,
                                     bg=self.ACCENT, fg="white",
                                     font=('Segoe UI', 9, 'bold'),
                                     width=8, bd=0, cursor="hand2")
            self.send_btn.pack(side=tk.LEFT)

        # Place chat_panel overlaying the left side of screen area
        # Use relative placement so it stays inside the main window
        self.chat_panel.place(relx=0.02, rely=0.12, relwidth=0.28, relheight=0.75)

        # Enable input if connected
        if self.client and self.client.connected:
            try:
                self.chat_entry.config(state='normal')
                self.send_btn.config(state='normal')
            except Exception:
                pass
    
    def open_files_window(self):
        """Open files window"""
        if self.files_window is not None and self.files_window.winfo_exists():
            self.files_window.lift()
            return
        
        self.files_window = tk.Toplevel(self.root)
        self.files_window.title("File Transfer")
        self.files_window.geometry("600x500")
        self.files_window.configure(bg=self.BG_PRIMARY)
        
        # Upload section
        upload_frame = tk.LabelFrame(self.files_window, text="Upload File",
                                    bg=self.BG_SECONDARY, fg=self.FG_PRIMARY,
                                    font=('Segoe UI', 10, 'bold'), bd=0)
        upload_frame.pack(fill=tk.X, padx=10, pady=10)
        
        btn_frame = tk.Frame(upload_frame, bg=self.BG_SECONDARY)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.upload_btn = tk.Button(btn_frame, text="📤 Select & Upload",
                                   command=self.upload_file,
                                   bg=self.SUCCESS, fg="white",
                                   font=('Segoe UI', 9, 'bold'),
                                   width=20, bd=0, cursor="hand2",
                                   state='disabled')
        self.upload_btn.pack(side=tk.LEFT)
        
        self.upload_progress_var = tk.DoubleVar()
        self.upload_progress = ttk.Progressbar(btn_frame, variable=self.upload_progress_var,
                                              maximum=100)
        self.upload_progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        
        # Download section
        download_frame = tk.LabelFrame(self.files_window, text="Download Files",
                                      bg=self.BG_SECONDARY, fg=self.FG_PRIMARY,
                                      font=('Segoe UI', 10, 'bold'), bd=0)
        download_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tree_frame = tk.Frame(download_frame, bg=self.BG_SECONDARY)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.file_tree = ttk.Treeview(tree_frame, columns=('Size', 'By'),
                                     height=12, show='tree headings')
        self.file_tree.heading('#0', text='Filename')
        self.file_tree.heading('Size', text='Size')
        self.file_tree.heading('By', text='Uploader')
        self.file_tree.column('#0', width=250)
        self.file_tree.column('Size', width=100)
        self.file_tree.column('By', width=150)
        
        file_scrollbar = tk.Scrollbar(tree_frame, orient="vertical",
                                     command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_scrollbar.set)
        
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        file_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Download button
        btn_frame2 = tk.Frame(download_frame, bg=self.BG_SECONDARY)
        btn_frame2.pack(fill=tk.X, padx=10, pady=10)
        
        self.download_btn = tk.Button(btn_frame2, text="📥 Download Selected",
                                     command=self.download_file,
                                     bg=self.ACCENT, fg="white",
                                     font=('Segoe UI', 9, 'bold'),
                                     width=20, bd=0, cursor="hand2",
                                     state='disabled')
        self.download_btn.pack(side=tk.LEFT)
        
        self.download_progress_var = tk.DoubleVar()
        self.download_progress = ttk.Progressbar(btn_frame2, variable=self.download_progress_var,
                                                maximum=100)
        self.download_progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        
        self.update_file_list()
        
        if self.client and self.client.connected:
            self.upload_btn.config(state='normal')
            self.download_btn.config(state='normal')
    
    def connect_to_server(self):
        """Connect to server"""
        server_ip = self.ip_entry.get().strip()
        username = self.username_entry.get().strip()
        
        if not server_ip or not username:
            messagebox.showerror("Error", "Enter IP and username", parent=self.root)
            return
        
        self.connect_btn.config(state='disabled', text="Connecting...")
        
        self.client = CollaborationClient(server_ip, username)
        self.client.gui = self
        
        def connect_thread():
            success, message = self.client.connect()
            self.root.after(0, lambda: self.on_connect_success() if success else self.on_connect_failure(message))
        
        threading.Thread(target=connect_thread, daemon=True).start()
    
    def on_connect_success(self):
        """Connected"""
        self.connect_btn.config(text="Disconnect", state='normal',
                               bg=self.DANGER, command=self.disconnect_from_server)
        self.status_label.config(text="● Online", fg=self.SUCCESS)
        
        # Enable buttons
        self.video_btn.config(state='normal')
        self.audio_btn.config(state='normal')
        self.screen_btn.config(state='normal')
        self.chat_btn.config(state='normal')
        self.files_btn.config(state='normal')
        
        if self.chat_panel and self.chat_panel.winfo_ismapped():
            try:
                self.chat_entry.config(state='normal')
                self.send_btn.config(state='normal')
            except Exception:
                pass
        
        if self.files_window and self.files_window.winfo_exists():
            self.upload_btn.config(state='normal')
            self.download_btn.config(state='normal')
        
        self.update_file_list()
    
    def on_connect_failure(self, message="Connection failed"):
        """Connect failed"""
        self.connect_btn.config(state='normal', text="Connect")
        messagebox.showerror("Error", message, parent=self.root)
    
    def disconnect_from_server(self):
        """Disconnect"""
        if self.client:
            self.client.disconnect()
        self.on_disconnect()
    
    def on_disconnect(self):
        """Disconnected"""
        self.connect_btn.config(text="Connect", state='normal',
                               bg=self.SUCCESS, command=self.connect_to_server)
        self.status_label.config(text="● Offline", fg=self.DANGER)
        
        # Disable buttons
        self.video_btn.config(state='disabled', bg=self.BG_TERTIARY)
        self.audio_btn.config(state='disabled', bg=self.BG_TERTIARY)
        self.screen_btn.config(state='disabled', bg=self.BG_TERTIARY)
        self.chat_btn.config(state='disabled', bg=self.BG_TERTIARY)
        self.files_btn.config(state='disabled', bg=self.BG_TERTIARY)
        
        if self.chat_panel and self.chat_panel.winfo_ismapped():
            try:
                self.chat_entry.config(state='disabled')
                self.send_btn.config(state='disabled')
            except Exception:
                pass
        
        if self.files_window and self.files_window.winfo_exists():
            self.upload_btn.config(state='disabled')
            self.download_btn.config(state='disabled')
        
        # Clear all video tiles
        for i in range(len(self.video_positions)):
            pos = self.video_positions[i]
            pos['video_label'].config(text="Video Disabled", image='')
            try:
                pos['video_label'].image = None
            except Exception:
                pass
            pos['name_label'].config(text=f"Participant {i+1}")
            pos['client_id'] = None
            pos['frame'].place_forget()
        
        # update main stage placeholder
        try:
            self.stage_label.config(text="Waiting for presentation...", image='')
            self.stage_label.image = None
        except Exception:
            pass
    
    def toggle_video(self):
        """Toggle video"""
        if not self.client or not self.client.connected:
            return
        
        if self.client.video_streaming:
            self.client.stop_video_streaming()
            self.video_btn.config(bg=self.BG_TERTIARY, text="📹 Video")
        else:
            if self.client.start_video_streaming():
                self.video_btn.config(bg="#ff6b6b", text="🛑 Stop Video")
            else:
                messagebox.showerror("Error", "Failed to start video", parent=self.root)
    
    def toggle_audio(self):
        """Toggle audio"""
        if not self.client or not self.client.connected:
            return
        
        if self.client.audio_streaming:
            self.client.stop_audio_streaming()
            self.audio_btn.config(bg=self.BG_TERTIARY, text="🎤 Audio")
        else:
            if self.client.start_audio_streaming():
                self.audio_btn.config(bg=self.SUCCESS, text="🎤 Unmute")
            else:
                messagebox.showerror("Error", "Failed to start audio", parent=self.root)
    
    def toggle_screen(self):
        """Toggle screen sharing"""
        if not self.client or not self.client.connected:
            return
        
        if self.client.screen_sharing:
            self.stop_screen_sharing()
        else:
            if self.client.start_screen_sharing():
                self.screen_btn.config(bg="#ff6b6b", text="🛑 Stop Share")
                self.show_presenter_toolbar()
            else:
                messagebox.showwarning("Screen", "Someone is already presenting", parent=self.root)
    
    def stop_screen_sharing(self):
        """Stop screen sharing and hide toolbar"""
        if self.client:
            self.client.stop_screen_sharing()
            self.screen_btn.config(bg=self.BG_TERTIARY, text="🖥️  Screen")
            self.presenter_toolbar.place_forget()
            self.is_paused = False
            self.pause_share_btn.config(text="⏸️ Pause")
    
    def show_presenter_toolbar(self):
        """Show floating presenter toolbar"""
        # Position toolbar at bottom-center of screen area
        self.presenter_toolbar.place(relx=0.5, rely=0.95, anchor=tk.S)
        
        # Make toolbar draggable
        def start_drag(event):
            self.drag_data = {'x': event.x, 'y': event.y}
            
        def on_drag(event):
            if hasattr(self, 'drag_data'):
                dx = event.x - self.drag_data['x']
                dy = event.y - self.drag_data['y']
                x = self.presenter_toolbar.winfo_x() + dx
                y = self.presenter_toolbar.winfo_y() + dy
                self.presenter_toolbar.place(x=x, y=y)
        
        self.presenter_toolbar.bind('<Button-1>', start_drag)
        self.presenter_toolbar.bind('<B1-Motion>', on_drag)
    
    def show_viewer_toolbar(self):
        """Show viewer controls when receiving screen share"""
        self.viewer_toolbar.place(relx=0.5, rely=0.95, anchor=tk.S)
    
    def hide_viewer_toolbar(self):
        """Hide viewer controls when screen share ends"""
        self.viewer_toolbar.place_forget()
        self.is_fullscreen = False
        self.fullscreen_btn.config(text="🔲 Fullscreen")
    
    def toggle_screen_pause(self):
        """Pause/resume screen sharing"""
        if not hasattr(self, 'is_paused'):
            self.is_paused = False
            
        self.is_paused = not self.is_paused
        
        if self.is_paused:
            self.pause_share_btn.config(text="▶️ Resume")
            # Store last frame to show while paused
            if self.client:
                with self.client.screen_lock:
                    self.paused_frame = self.client.shared_screen.copy() if self.client.shared_screen is not None else None
        else:
            self.pause_share_btn.config(text="⏸️ Pause")
            self.paused_frame = None
    
    def toggle_fullscreen(self):
        """Toggle fullscreen mode for viewers"""
        if not hasattr(self, 'is_fullscreen'):
            self.is_fullscreen = False
            
        self.is_fullscreen = not self.is_fullscreen
        
        if self.is_fullscreen:
            self.fullscreen_btn.config(text="❌ Exit Fullscreen")
            # Store current window state
            self.normal_state = {
                'geometry': self.root.geometry(),
                'state': self.root.state()
            }
            # Enter fullscreen
            self.root.attributes('-fullscreen', True)
            # Hide other UI elements
            self.hide_ui_for_fullscreen()
        else:
            self.fullscreen_btn.config(text="🔲 Fullscreen")
            # Restore window state
            self.root.attributes('-fullscreen', False)
            if hasattr(self, 'normal_state'):
                self.root.geometry(self.normal_state['geometry'])
                self.root.state(self.normal_state['state'])
            # Show UI elements
            self.show_ui_after_fullscreen()
    
    def toggle_screen_zoom(self):
        """Toggle between fit-to-window and actual size"""
        if not hasattr(self, 'zoom_fit'):
            self.zoom_fit = True
            
        self.zoom_fit = not self.zoom_fit
        self.zoom_btn.config(text="🔍 Fit" if self.zoom_fit else "🔍 1:1")
        # Force screen update to apply new zoom
        self.update_screen_display()
        
    def hide_ui_for_fullscreen(self):
        """Hide UI elements when entering fullscreen"""
        for widget in self.root.winfo_children():
            if isinstance(widget, tk.Frame) and widget.winfo_y() == 0:
                # This should be our top bar
                widget.pack_forget()
                self._stored_top_bar = widget
                break
                
        if self.chat_panel and self.chat_panel.winfo_ismapped():
            self.chat_panel.place_forget()
            self.restore_chat = True
        if self.files_window and self.files_window.winfo_exists():
            self.files_window.withdraw()
            self.restore_files = True
            
    def show_ui_after_fullscreen(self):
        """Restore UI elements after exiting fullscreen"""
        if hasattr(self, '_stored_top_bar'):
            self._stored_top_bar.pack(side=tk.TOP, fill=tk.X)
            
        if hasattr(self, 'restore_chat') and self.restore_chat:
            self.open_chat_window()
            self.restore_chat = False
        if hasattr(self, 'restore_files') and self.restore_files:
            self.files_window.deiconify()
            self.restore_files = False
    
    def upload_file(self):
        """Upload file"""
        if not self.client or not self.client.connected:
            return
        
        filepath = filedialog.askopenfilename(parent=self.files_window)
        if not filepath:
            return
        
        self.upload_progress_var.set(0)
        
        def upload_thread():
            success, msg = self.client.upload_file(filepath)
            self.files_window.after(0, lambda: self.on_upload_complete(success, msg))
        
        threading.Thread(target=upload_thread, daemon=True).start()
    
    def on_upload_complete(self, success, msg):
        """Upload done"""
        if success:
            messagebox.showinfo("Success", "File uploaded!", parent=self.files_window)
        else:
            messagebox.showerror("Error", msg, parent=self.files_window)
        
        self.upload_progress_var.set(0)
        self.update_file_list()
    
    def download_file(self):
        """Download file"""
        if not self.client or not self.client.connected:
            return
        
        selection = self.file_tree.selection()
        if not selection:
            messagebox.showwarning("Select", "Choose a file to download", parent=self.files_window)
            return
        
        filename = self.file_tree.item(selection[0])['text']
        self.download_progress_var.set(0)
        
        def download_thread():
            success, msg = self.client.download_file(filename)
            self.files_window.after(0, lambda: self.on_download_complete(success, msg))
        
        threading.Thread(target=download_thread, daemon=True).start()
    
    def on_download_complete(self, success, msg):
        """Download done"""
        if success:
            messagebox.showinfo("Success", "File downloaded!", parent=self.files_window)
        else:
            messagebox.showerror("Error", msg, parent=self.files_window)
        
        self.download_progress_var.set(0)
    
    def update_upload_progress(self, progress):
        """Update upload progress"""
        if self.files_window and self.files_window.winfo_exists():
            self.root.after(0, lambda: self.upload_progress_var.set(progress))
    
    def update_download_progress(self, progress):
        """Update download progress"""
        if self.files_window and self.files_window.winfo_exists():
            self.root.after(0, lambda: self.download_progress_var.set(progress))
    
    def update_file_list(self):
        """Update file list"""
        if not self.client or not (self.files_window and self.files_window.winfo_exists()):
            return
        
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        
        files = self.client.get_available_files()
        for filename, info in files.items():
            size = self.format_file_size(info.get('size', 0))
            uploader = info.get('uploader', 'Unknown')
            self.file_tree.insert('', 'end', text=filename, values=(size, uploader))
    
    def format_file_size(self, size):
        """Format file size"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}TB"
        
    def animate_widget(self, widget, start_pos, end_pos, callback=None):
        """Animate widget position/size change"""
        if not widget.winfo_exists():
            return
            
        # Cancel any existing animation for this widget
        if widget in self.animation_jobs:
            for job_id in self.animation_jobs[widget]:
                self.root.after_cancel(job_id)
                
        self.animation_jobs[widget] = []
        steps = self.ANIMATION_STEPS
        step_time = self.ANIMATION_DURATION / steps
        
        # Calculate steps
        dx = (end_pos['x'] - start_pos['x']) / steps
        dy = (end_pos['y'] - start_pos['y']) / steps
        dw = (end_pos['width'] - start_pos['width']) / steps
        dh = (end_pos['height'] - start_pos['height']) / steps
        
        def animate_step(step):
            if not widget.winfo_exists():
                return
                
            progress = step / steps
            # Ease in-out cubic
            t = progress
            if t < 0.5:
                t = 4 * t * t * t
            else:
                t = (t - 1) * (2 * t - 2) * (2 * t - 2) + 1
                
            current = {
                'x': start_pos['x'] + dx * step,
                'y': start_pos['y'] + dy * step,
                'width': start_pos['width'] + dw * step,
                'height': start_pos['height'] + dh * step
            }
            
            widget.place(x=int(current['x']),
                        y=int(current['y']),
                        width=int(current['width']),
                        height=int(current['height']))
            
            if step < steps:
                job_id = self.root.after(int(step_time), 
                                       lambda: animate_step(step + 1))
                self.animation_jobs[widget].append(job_id)
            else:
                if callback:
                    callback()
                if widget in self.animation_jobs:
                    del self.animation_jobs[widget]
        
        animate_step(0)
        
    def maintain_aspect_ratio(self, width, height, target_ratio=None):
        """Adjust dimensions to maintain aspect ratio"""
        if target_ratio is None:
            target_ratio = self.ASPECT_RATIO
            
        current_ratio = width / height
        
        if current_ratio > target_ratio:
            # Too wide, adjust width
            new_width = int(height * target_ratio)
            return new_width, height
        else:
            # Too tall, adjust height
            new_height = int(width / target_ratio)
            return width, new_height
        
    def pin_video(self, client_id):
        """Pin/unpin participant video to main view"""
        if not hasattr(self, 'pinned_participant'):
            self.pinned_participant = None
            
        if self.pinned_participant == client_id:
            self.pinned_participant = None
        else:
            self.pinned_participant = client_id
        
        self.update_video_display()
    
    def toggle_remote_audio(self, client_id):
        """Request server to mute/unmute participant (placeholder)"""
        # This would need server-side implementation to work
        # For now just shows a message
        if client_id == self.client.client_id:
            self.toggle_audio()
        else:
            messagebox.showinfo("Info", 
                              "Remote audio control requires server implementation",
                              parent=self.root)
    
    def highlight_active_speaker(self, client_id):
        """Highlight the active speaker with visual feedback"""
        # This would be called when audio levels indicate someone is speaking
        for pos in self.video_positions.values():
            if pos['client_id'] == client_id:
                pos['frame'].configure(highlightbackground="green",
                                    highlightthickness=2)
            else:
                pos['frame'].configure(highlightthickness=0)
    
    def send_chat_message(self, event=None):
        """Send chat message"""
        if not self.chat_panel or not self.chat_panel.winfo_ismapped():
            return
        
        msg = self.chat_entry.get().strip()
        
        if not msg:
            return
        
        if self.client and self.client.connected:
            if self.client.send_chat_message(msg):
                self.display_chat_message(self.client.username, msg,
                                        datetime.now().isoformat())
                self.chat_entry.delete(0, tk.END)
    
    def display_chat_message(self, username, content, timestamp, is_system=False):
        """Display chat message"""
        if not self.chat_panel or not self.chat_panel.winfo_ismapped():
            return
        
        self.chat_display.config(state='normal')
        
        try:
            dt = datetime.fromisoformat(timestamp)
            time_str = dt.strftime("%H:%M:%S")
        except:
            time_str = datetime.now().strftime("%H:%M:%S")
        
        if is_system:
            self.chat_display.insert('end', f"[{time_str}] ", 'timestamp')
            self.chat_display.insert('end', f"{content}\n", 'system')
        else:
            self.chat_display.insert('end', f"[{time_str}] ", 'timestamp')
            self.chat_display.insert('end', f"{username}: ", 'username')
            self.chat_display.insert('end', f"{content}\n")
        
        self.chat_display.see('end')
        self.chat_display.config(state='disabled')
    
    def toggle_view_mode(self):
        """Toggle between Gallery and Speaker view modes"""
        current_mode = self.view_mode.get()
        if current_mode == "gallery":
            self.view_mode.set("speaker")
            self.view_btn.config(text="🎤 Speaker View")
        else:
            self.view_mode.set("gallery")
            self.view_btn.config(text="👥 Gallery View")
        self.update_video_display()
    
    def on_participant_speaking(self, client_id):
        """Handle participant speaking event"""
        current_time = time.time()
        self.last_audio_activity[client_id] = current_time
        
        # Only update active speaker if in speaker view mode
        if self.view_mode.get() == "speaker" and self.active_speaker != client_id:
            self.active_speaker = client_id
            self.update_video_display()
        
        # Schedule a check to remove highlight after silence
        self.root.after(int(self.audio_fade_threshold * 1000), 
                       lambda: self.check_audio_timeout(client_id, current_time))
    
    def check_audio_timeout(self, client_id, timestamp):
        """Check if participant has been silent long enough to remove highlight"""
        if client_id in self.last_audio_activity:
            last_time = self.last_audio_activity[client_id]
            if last_time == timestamp:  # No new activity since timeout was scheduled
                if self.active_speaker == client_id:
                    self.active_speaker = None
                    self.update_video_display()
    
    def update_video_display(self):
        """Update video grid with adaptive layout based on participant count"""
        if not self.client:
            return

        frames = self.client.get_video_frames()
        active_clients = list(frames.keys())
        client_count = len(active_clients)

        # Ensure all required attributes are initialized
        if not hasattr(self, 'active_speaker'):
            self.active_speaker = None
        if not hasattr(self, 'pinned_participant'):
            self.pinned_participant = None

        # Clear the stage area
        self.stage_label.config(image='', text='')
        self.stage_label.image = None

        # If no participants, show waiting message
        if not active_clients:
            self.stage_label.config(text='Waiting for participants...')
            return

        # If screen share is active, skip (handled elsewhere)
        if getattr(self.client, 'current_presenter', None):
            return

        # Stage dimensions from stage_frame (more stable than label)
        stage_w = max(640, self.stage_frame.winfo_width() or 640)
        stage_h = max(360, self.stage_frame.winfo_height() or 360)

        # Speaker mode?
        speaker_mode = self.view_mode.get() == "speaker"
        focused_participant = self.active_speaker if speaker_mode else None

        if speaker_mode and (focused_participant in frames or self.pinned_participant in frames):
            # Speaker View: main area + filmstrip
            main_height = int(stage_h * 0.7)
            strip_height = stage_h - main_height

            display_id = focused_participant or self.pinned_participant
            if display_id in frames:
                try:
                    main_resized = cv2.resize(frames[display_id], (stage_w, main_height))
                    main_rgb = cv2.cvtColor(main_resized, cv2.COLOR_BGR2RGB)
                    main_img = Image.fromarray(main_rgb)
                    main_photo = ImageTk.PhotoImage(image=main_img)
                    self.stage_label.config(image=main_photo, text='')
                    self.stage_label.image = main_photo
                except Exception:
                    pass

            # Filmstrip thumbnails
            others = [cid for cid in active_clients if cid != display_id]
            count = max(1, len(others))
            thumb_w = stage_w // count
            thumb_w, thumb_h = self.maintain_aspect_ratio(thumb_w, strip_height)

            for idx, cid in enumerate(others):
                pos = self.video_positions.get(idx)
                if not pos:
                    continue
                try:
                    frame = frames[cid]
                    x = idx * thumb_w
                    y = main_height
                    pos['frame'].place(x=x, y=y, width=thumb_w, height=thumb_h)

                    thumb = cv2.resize(frame, (thumb_w, thumb_h))
                    thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(thumb_rgb)
                    photo = ImageTk.PhotoImage(image=img)
                    pos['video_label'].config(image=photo, text='')
                    pos['video_label'].image = photo
                    pos['name_label'].config(text=f"User {cid.split('_')[-1]}")
                    pos['client_id'] = cid
                except Exception:
                    pos['video_label'].config(text="Error", image='')

            # Hide remaining slots
            for i in range(len(others), len(self.video_positions)):
                p = self.video_positions[i]
                p['frame'].place_forget()
                p['video_label'].config(text="Video Disabled", image='')
                p['name_label'].config(text="Participant")

            return

        # Gallery view: compute grid
        if client_count <= 2:
            grid_cols, grid_rows = 2, 1
        elif client_count <= 4:
            grid_cols, grid_rows = 2, 2
        elif client_count <= 9:
            grid_cols, grid_rows = 3, 3
        else:
            grid_cols, grid_rows = 4, 3

        tile_w = stage_w // grid_cols
        tile_h = stage_h // grid_rows
        tile_w = max(self.MIN_TILE_WIDTH, tile_w)
        tile_h = max(self.MIN_TILE_HEIGHT, tile_h)
        tile_w, tile_h = self.maintain_aspect_ratio(tile_w, tile_h)

        for i in range(len(self.video_positions)):
            pos = self.video_positions[i]
            if i < client_count:
                cid = active_clients[i]
                frame = frames[cid]
                is_self = cid == self.client.client_id

                row = i // grid_cols
                col = i % grid_cols
                x = col * tile_w
                y = row * tile_h

                # Place tile
                pos['frame'].place(x=x, y=y, width=tile_w, height=tile_h)

                try:
                    resized = cv2.resize(frame, (tile_w, tile_h))
                    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(rgb)
                    photo = ImageTk.PhotoImage(image=img)
                    pos['video_label'].config(image=photo, text='')
                    pos['video_label'].image = photo
                except Exception:
                    pos['video_label'].config(text="Error", image='')

                # Label
                name = f"{self.client.username} (You)" if is_self else f"User {cid.split('_')[-1]}"
                status = "🎤" if getattr(self.client, 'audio_streaming', False) else "🔇"
                pos['name_label'].config(text=f"{name} {status}")
                pos['client_id'] = cid
            else:
                pos['frame'].place_forget()
                pos['video_label'].config(text="Video Disabled", image='')
                try:
                    pos['video_label'].image = None
                except Exception:
                    pass
                pos['name_label'].config(text="Participant")
                pos['client_id'] = None
    
    def update_screen_display(self):
        """Update screen display with presenter mode layout"""
        if not self.client:
            return

        screen, presenter = self.client.get_shared_screen()
        frames = self.client.get_video_frames() if self.client else {}

        # LAYOUT STATE 1: No screen share - show video grid
        if screen is None or presenter is None:
            self.presenter_toolbar.place_forget()
            self.hide_viewer_toolbar()
            
            # Clear any PiP settings
            for pos in self.video_positions.values():
                if 'was_pip' in pos:
                    del pos['was_pip']
            
            # Switch to gallery view
            self.update_video_display()
            return

        # LAYOUT STATE 2: Screen share active
        # Show appropriate toolbar
        if presenter == self.client.client_id:
            self.presenter_toolbar.lift()
        else:
            self.show_viewer_toolbar()

        # Handle paused state
        if hasattr(self, 'is_paused') and self.is_paused and hasattr(self, 'paused_frame'):
            screen = self.paused_frame

        if screen is not None:
            try:
                # Get stage dimensions
                stage_w = max(320, self.stage_label.winfo_width() or 640)
                stage_h = max(180, self.stage_label.winfo_height() or 360)

                # Convert screen to RGB
                screen_rgb = cv2.cvtColor(screen, cv2.COLOR_BGR2RGB)
                
                # Calculate dimensions based on zoom mode
                if not hasattr(self, 'zoom_fit'):
                    self.zoom_fit = True
                    
                if self.zoom_fit:
                    # Fit to window while preserving aspect ratio
                    h, w = screen_rgb.shape[:2]
                    scale = min(stage_w/w, stage_h/h)
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    screen_resized = cv2.resize(screen_rgb, (new_w, new_h))
                else:
                    # 1:1 size with scrolling
                    screen_resized = screen_rgb

                # Create image for display
                img = Image.fromarray(screen_resized)
                photo = ImageTk.PhotoImage(image=img)
                self.stage_label.config(image=photo, text='')
                self.stage_label.image = photo

                # LAYOUT STATE 2: Screen share layout
                # Main content area (75% height for shared screen)
                main_height = int(stage_h * 0.75)
                
                # Filmstrip area (25% height)
                filmstrip_height = stage_h - main_height
                filmstrip_y = main_height
                
                # Ensure minimum tile size in filmstrip
                min_tiles = max(1, (stage_w // self.MIN_TILE_WIDTH))
                participant_count = len([c for c in frames if c != presenter])
                
                # Calculate thumbnail width maintaining 16:9 aspect
                if participant_count > min_tiles:
                    # If too many participants, use minimum size and allow scrolling
                    thumb_width = self.MIN_TILE_WIDTH
                    thumb_height = self.MIN_TILE_HEIGHT
                else:
                    # Distribute space evenly
                    thumb_width = stage_w // max(1, participant_count)
                    thumb_width, thumb_height = self.maintain_aspect_ratio(thumb_width, filmstrip_height)
                
                # Position videos in filmstrip with animation
                col = 0
                for cid, frame in frames.items():
                    pos = self.video_positions.get(col)
                    if not pos:
                        continue
                        
                    is_presenter = (cid == presenter)
                    current_pos = {
                        'x': pos['frame'].winfo_x(),
                        'y': pos['frame'].winfo_y(),
                        'width': pos['frame'].winfo_width(),
                        'height': pos['frame'].winfo_height()
                    }
                    
                    if is_presenter and self.client.video_streaming:
                        # PiP in top-right corner (adjusted to avoid chat panel)
                        pip_width = 160
                        pip_height = 90
                        
                        # If chat panel is visible, adjust PiP position
                        chat_visible = (self.chat_panel and self.chat_panel.winfo_ismapped())
                        pip_x = stage_w - (pip_width + 10)
                        pip_y = 10 if not chat_visible else pip_height + 20
                        
                        target_pos = {
                            'x': pip_x,
                            'y': pip_y,
                            'width': pip_width,
                            'height': pip_height
                        }
                        
                        # Add "Presenter" label
                        pos['name_label'].config(text="👑 Presenter")
                        pos['was_pip'] = True
                    else:
                        # Regular participant in filmstrip
                        if not is_presenter:  # Skip presenter if not streaming
                            target_pos = {
                                'x': col * thumb_width,
                                'y': filmstrip_y,
                                'width': thumb_width,
                                'height': thumb_height
                            }
                            pos['was_pip'] = False
                            col += 1
                    
                    # Animate to new position
                    self.animate_widget(pos['frame'], current_pos, target_pos)

                    try:
                        # Resize video frame to thumbnail size
                        thumb_size = (pip_width, pip_height) if is_presenter else (thumb_width, filmstrip_height)
                        thumb = cv2.resize(frame, thumb_size)
                        thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(thumb_rgb)
                        photo = ImageTk.PhotoImage(image=img)
                        pos['video_label'].config(image=photo, text='')
                        pos['video_label'].image = photo
                    except Exception:
                        pos['video_label'].config(text="Error", image='')

            except Exception as e:
                print(f"Screen display error: {e}")
                self.stage_label.config(text="Display Error", image='')
        else:
            self.stage_label.config(image='', text='Waiting for presentation...')
            self.stage_label.image = None
            self.update_video_display()


def main():
    root = tk.Tk()
    app = ModernClientGUI(root)
    
    def on_closing():
        if app.client and app.client.connected:
            app.client.disconnect()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
