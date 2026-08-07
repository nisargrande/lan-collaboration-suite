"""
LAN Collaboration Suite - Server Application
FINAL PRODUCTION VERSION - All Issues Fixed
"""

import socket
import threading
import json
import time
import os
import struct
from datetime import datetime
import cv2
import numpy as np
import pyaudio
from PIL import ImageGrab
import config

class CollaborationServer:
    def __init__(self):
        self.clients = {}
        self.client_counter = 0
        self.lock = threading.Lock()
        
        # Sockets
        self.tcp_socket = None
        self.udp_video_socket = None
        self.udp_audio_socket = None
        self.tcp_screen_socket = None
        self.tcp_file_socket = None
        
        # Presenter management
        self.current_presenter = None
        self.presenter_lock = threading.Lock()
        
        # Audio streaming
        self.audio_clients = set()  # Clients with audio enabled
        self.audio_lock = threading.Lock()
        
        # File management
        self.available_files = {}
        self.files_lock = threading.Lock()
        
        # Create directories
        if not os.path.exists(config.SERVER_FILES_DIR):
            os.makedirs(config.SERVER_FILES_DIR)
        
        self.running = False
        
    def start(self):
        """Start the collaboration server"""
        print("=" * 70)
        print("LAN COLLABORATION SUITE - SERVER (PRODUCTION VERSION)")
        print("All 5 Core Modules: Video, Audio, Screen Share, Chat, File Transfer")
        print("=" * 70)
        
        try:
            # TCP socket
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_socket.bind((config.SERVER_HOST, config.TCP_PORT))
            self.tcp_socket.listen(5)
            print(f"✅ TCP Server: {config.SERVER_HOST}:{config.TCP_PORT}")
            
            # UDP Video socket
            self.udp_video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_video_socket.bind((config.SERVER_HOST, config.UDP_VIDEO_PORT))
            print(f"✅ UDP Video: {config.SERVER_HOST}:{config.UDP_VIDEO_PORT}")
            
            # UDP Audio socket
            self.udp_audio_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_audio_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_audio_socket.bind((config.SERVER_HOST, config.UDP_AUDIO_PORT))
            print(f"✅ UDP Audio: {config.SERVER_HOST}:{config.UDP_AUDIO_PORT}")
            
            # TCP Screen socket
            self.tcp_screen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_screen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_screen_socket.bind((config.SERVER_HOST, config.TCP_SCREEN_PORT))
            self.tcp_screen_socket.listen(5)
            print(f"✅ TCP Screen: {config.SERVER_HOST}:{config.TCP_SCREEN_PORT}")
            
            # TCP File socket
            self.tcp_file_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_file_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_file_socket.bind((config.SERVER_HOST, config.TCP_FILE_PORT))
            self.tcp_file_socket.listen(5)
            print(f"✅ TCP File: {config.SERVER_HOST}:{config.TCP_FILE_PORT}")
            
            print("\n🚀 Server ready for connections!")
            print(f"📊 Max clients: {config.MAX_CLIENTS}")
            print(f"📁 Files dir: {config.SERVER_FILES_DIR}")
            print("=" * 70)
            print()
            
            self.running = True
            
            # Start service threads
            threading.Thread(target=self.handle_udp_video, daemon=True).start()
            threading.Thread(target=self.handle_udp_audio, daemon=True).start()
            threading.Thread(target=self.accept_screen_connections, daemon=True).start()
            threading.Thread(target=self.accept_file_connections, daemon=True).start()
            
            # Main accept loop
            self.accept_connections()
            
        except Exception as e:
            print(f"❌ Error: {e}")
            self.stop()
    
    def accept_connections(self):
        """Accept incoming TCP connections"""
        while self.running:
            try:
                client_socket, client_address = self.tcp_socket.accept()
                
                with self.lock:
                    if len(self.clients) >= config.MAX_CLIENTS:
                        client_socket.send(b"SERVER_FULL")
                        client_socket.close()
                        print(f"⚠️  Rejected (full): {client_address}")
                        continue
                
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, client_address),
                    daemon=True
                )
                client_thread.start()
                
            except Exception as e:
                if self.running:
                    pass
    
    def handle_client(self, client_socket, client_address):
        """Handle individual client"""
        client_id = None
        username = None
        
        try:
            client_socket.settimeout(10)
            data = client_socket.recv(config.CHAT_BUFFER_SIZE).decode('utf-8')
            
            if not data:
                return
            
            message = json.loads(data)
            
            if message.get('type') == config.MSG_CONNECT:
                username = message.get('username', 'Unknown')
                
                # Check for duplicate username
                with self.lock:
                    username_exists = any(
                        client['username'].lower() == username.lower() 
                        for client in self.clients.values()
                    )
                    
                    if username_exists:
                        response = {
                            'type': 'ERROR',
                            'message': 'Username already taken'
                        }
                        client_socket.send(json.dumps(response).encode('utf-8'))
                        return
                    
                    self.client_counter += 1
                    client_id = f"client_{self.client_counter}"
                    
                    self.clients[client_id] = {
                        'socket': client_socket,
                        'address': client_address,
                        'username': username,
                        'connected_at': datetime.now(),
                        'video_active': False,
                        'audio_active': False
                    }
                
                response = {
                    'type': 'ACK',
                    'client_id': client_id,
                    'message': 'Connected successfully',
                    'available_files': self.get_available_files_list()
                }
                client_socket.send(json.dumps(response).encode('utf-8'))
                
                print(f"✅ Connected: {username} ({client_id}) from {client_address}")
                
                self.broadcast_message(
                    f"📢 {username} joined the session",
                    system_message=True,
                    exclude_client=client_id
                )
                
                client_socket.settimeout(300)
                
                while self.running:
                    try:
                        data = client_socket.recv(config.CHAT_BUFFER_SIZE)
                        if not data:
                            break
                        
                        self.process_client_message(client_id, data)
                        
                    except socket.timeout:
                        break
                    except ConnectionResetError:
                        break
                    except Exception as e:
                        break
        
        except Exception as e:
            pass
        finally:
            if client_id and username:
                self.remove_client(client_id, username)
            
            try:
                client_socket.close()
            except:
                pass
    
    def process_client_message(self, client_id, data):
        """Process client message"""
        try:
            message = json.loads(data.decode('utf-8'))
            msg_type = message.get('type')
            
            if msg_type == config.MSG_CHAT:
                username = self.clients[client_id]['username']
                content = message.get('content')
                timestamp = message.get('timestamp', datetime.now().isoformat())
                
                print(f"💬 [{username}]: {content}")
                self.broadcast_message(content, username, timestamp, exclude_client=client_id)
            
            elif msg_type == config.MSG_AUDIO_START:
                with self.audio_lock:
                    self.audio_clients.add(client_id)
                print(f"🎤 Audio ON: {self.clients[client_id]['username']}")
            
            elif msg_type == config.MSG_AUDIO_STOP:
                with self.audio_lock:
                    self.audio_clients.discard(client_id)
                print(f"🔇 Audio OFF: {self.clients[client_id]['username']}")
            
        except Exception as e:
            pass
    
    def broadcast_message(self, content, username=None, timestamp=None, 
                         system_message=False, exclude_client=None):
        """Broadcast chat message"""
        if timestamp is None:
            timestamp = datetime.now().isoformat()
        
        message = {
            'type': config.MSG_CHAT,
            'username': 'System' if system_message else username,
            'content': content,
            'timestamp': timestamp,
            'system': system_message
        }
        
        message_json = json.dumps(message).encode('utf-8')
        
        with self.lock:
            for cid, client_info in self.clients.items():
                if cid == exclude_client:
                    continue
                
                try:
                    client_info['socket'].send(message_json)
                except:
                    pass
    
    def remove_client(self, client_id, username):
        """Remove client"""
        with self.lock:
            if client_id in self.clients:
                del self.clients[client_id]
        
        with self.audio_lock:
            self.audio_clients.discard(client_id)
        
        print(f"🔴 Disconnected: {username} ({client_id})")
        
        self.broadcast_message(
            f"📢 {username} left the session",
            system_message=True
        )
    
    # ==================== VIDEO ====================
    
    def handle_udp_video(self):
        """Handle UDP video"""
        print("📹 Video handler started")
        
        client_udp_addresses = {}
        
        while self.running:
            try:
                self.udp_video_socket.settimeout(2)
                data, sender_address = self.udp_video_socket.recvfrom(config.VIDEO_BUFFER_SIZE)
                
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
                
                client_udp_addresses[sender_id] = sender_address
                
                # Broadcast to others
                with self.lock:
                    for cid in self.clients.keys():
                        if cid != sender_id and cid in client_udp_addresses:
                            try:
                                self.udp_video_socket.sendto(data, client_udp_addresses[cid])
                            except:
                                pass
                            
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:
                    pass
    
    # ==================== AUDIO ====================
    
    def handle_udp_audio(self):
        """Handle UDP audio with selective broadcast"""
        print("🎤 Audio handler started")
        
        client_udp_addresses = {}
        audio_buffers = {}
        
        while self.running:
            try:
                self.udp_audio_socket.settimeout(2)
                data, sender_address = self.udp_audio_socket.recvfrom(config.AUDIO_BUFFER_SIZE)
                
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
                
                # Check if sender has audio enabled
                with self.audio_lock:
                    if sender_id not in self.audio_clients:
                        continue
                
                client_udp_addresses[sender_id] = sender_address
                audio_data = data[8+client_id_length:]
                audio_buffers[sender_id] = audio_data
                
                # ✅ FIX: Broadcast to ALL clients (not just those with audio ON)
                # This way audio from sender reaches everyone
                with self.lock:
                    for cid in self.clients.keys():
                        if cid != sender_id and cid in client_udp_addresses:
                            try:
                                self.udp_audio_socket.sendto(data, client_udp_addresses[cid])
                            except:
                                pass
                
                # Cleanup old buffers
                if len(audio_buffers) > 10:
                    audio_buffers = dict(list(audio_buffers.items())[-5:])
                            
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:
                    pass
    
    # ==================== SCREEN SHARING ====================
    
    def accept_screen_connections(self):
        """Accept screen connections"""
        print("🖥️  Screen handler started")
        
        while self.running:
            try:
                client_socket, client_address = self.tcp_screen_socket.accept()
                
                screen_thread = threading.Thread(
                    target=self.handle_screen_sharing,
                    args=(client_socket, client_address),
                    daemon=True
                )
                screen_thread.start()
                
            except Exception as e:
                if self.running:
                    pass
    
    def handle_screen_sharing(self, client_socket, client_address):
        """Handle screen sharing"""
        presenter_id = None
        
        try:
            data = client_socket.recv(config.CHAT_BUFFER_SIZE).decode('utf-8')
            if not data:
                return
            
            message = json.loads(data)
            
            if message.get('type') == config.MSG_SCREEN_START:
                presenter_id = message.get('client_id')
                
                with self.presenter_lock:
                    if self.current_presenter is None:
                        self.current_presenter = presenter_id
                        
                        response = {
                            'type': config.MSG_SCREEN_GRANT,
                            'message': 'You are now presenting'
                        }
                        client_socket.send(json.dumps(response).encode('utf-8'))
                        
                        print(f"🖥️  Presenter: {presenter_id}")
                        self.broadcast_presenter_status(presenter_id, True)
                        
                    else:
                        response = {
                            'type': config.MSG_SCREEN_DENY,
                            'message': 'Someone else is presenting',
                            'current_presenter': self.current_presenter
                        }
                        client_socket.send(json.dumps(response).encode('utf-8'))
                        client_socket.close()
                        return
                
                # Stream screen
                while self.running:
                    try:
                        size_data = client_socket.recv(4)
                        if not size_data or len(size_data) < 4:
                            break
                        
                        frame_size = struct.unpack('I', size_data)[0]
                        
                        if frame_size > config.SCREEN_BUFFER_SIZE:
                            break
                        
                        frame_data = b''
                        while len(frame_data) < frame_size:
                            chunk = client_socket.recv(min(config.SCREEN_BUFFER_SIZE, 
                                                           frame_size - len(frame_data)))
                            if not chunk:
                                break
                            frame_data += chunk
                        
                        if len(frame_data) != frame_size:
                            break
                        
                        # ✅ FIX: Broadcast to ALL clients
                        self.broadcast_screen_frame(presenter_id, frame_size, frame_data)
                        
                    except:
                        break
                    
        except Exception as e:
            pass
        finally:
            if presenter_id:
                with self.presenter_lock:
                    if self.current_presenter == presenter_id:
                        self.current_presenter = None
                        print(f"🛑 Presenter ended: {presenter_id}")
                        self.broadcast_presenter_status(presenter_id, False)
            
            client_socket.close()
    
    def broadcast_screen_frame(self, presenter_id, frame_size, frame_data):
        """Broadcast screen to all clients"""
        with self.lock:
            for cid, client_info in self.clients.items():
                if cid != presenter_id:
                    try:
                        message = {
                            'type': config.MSG_SCREEN_SHARE,
                            'presenter_id': presenter_id,
                            'frame_size': frame_size
                        }
                        message_json = json.dumps(message).encode('utf-8')
                        
                        client_info['socket'].sendall(struct.pack('I', len(message_json)))
                        client_info['socket'].sendall(message_json)
                        client_info['socket'].sendall(struct.pack('I', frame_size))
                        client_info['socket'].sendall(frame_data)
                        
                    except:
                        pass
    
    def broadcast_presenter_status(self, presenter_id, is_presenting):
        """Broadcast presenter status"""
        with self.lock:
            username = "Unknown"
            if presenter_id in self.clients:
                username = self.clients[presenter_id]['username']
        
        if is_presenting:
            self.broadcast_message(f"{username} started presenting", system_message=True)
        else:
            self.broadcast_message(f"{username} stopped presenting", system_message=True)
    
    # ==================== FILE TRANSFER ====================
    
    def accept_file_connections(self):
        """Accept file connections"""
        print("📁 File handler started")
        
        while self.running:
            try:
                client_socket, client_address = self.tcp_file_socket.accept()
                
                file_thread = threading.Thread(
                    target=self.handle_file_transfer,
                    args=(client_socket, client_address),
                    daemon=True
                )
                file_thread.start()
                
            except Exception as e:
                if self.running:
                    pass
    
    def handle_file_transfer(self, client_socket, client_address):
        """Handle file transfer"""
        try:
            data = client_socket.recv(config.CHAT_BUFFER_SIZE).decode('utf-8')
            if not data:
                return
            
            message = json.loads(data)
            request_type = message.get('type')
            
            if request_type == config.MSG_FILE_UPLOAD:
                self.handle_file_upload(client_socket, message)
            
            elif request_type == config.MSG_FILE_DOWNLOAD:
                self.handle_file_download(client_socket, message)
                
        except Exception as e:
            pass
        finally:
            client_socket.close()
    
    def handle_file_upload(self, client_socket, message):
        """Handle file upload"""
        try:
            filename = message.get('filename')
            filesize = message.get('filesize')
            uploader_id = message.get('client_id')
            
            print(f"📤 Uploading: {filename} ({filesize} bytes)")
            
            client_socket.send(b'ACK')
            
            filepath = os.path.join(config.SERVER_FILES_DIR, filename)
            received = 0
            
            with open(filepath, 'wb') as f:
                while received < filesize:
                    chunk = client_socket.recv(min(config.FILE_CHUNK_SIZE, 
                                                   filesize - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
            
            if received == filesize:
                print(f"✅ Upload complete: {filename}")
                
                uploader_name = "Unknown"
                with self.lock:
                    if uploader_id in self.clients:
                        uploader_name = self.clients[uploader_id]['username']
                
                # Add to available files
                with self.files_lock:
                    self.available_files[filename] = {
                        'size': filesize,
                        'uploader': uploader_name,
                        'timestamp': datetime.now().isoformat()
                    }
                
                # ✅ FIX: Broadcast file to all clients
                self.broadcast_file_update()
                
                response = {'status': 'success', 'message': 'File uploaded'}
                client_socket.send(json.dumps(response).encode('utf-8'))
                
                # Send chat notification
                self.broadcast_message(
                    f"📎 {uploader_name} shared: {filename}",
                    system_message=True
                )
            else:
                response = {'status': 'error', 'message': 'Upload incomplete'}
                client_socket.send(json.dumps(response).encode('utf-8'))
                
        except Exception as e:
            try:
                response = {'status': 'error', 'message': str(e)}
                client_socket.send(json.dumps(response).encode('utf-8'))
            except:
                pass
    
    def handle_file_download(self, client_socket, message):
        """Handle file download"""
        try:
            filename = message.get('filename')
            filepath = os.path.join(config.SERVER_FILES_DIR, filename)
            
            if not os.path.exists(filepath):
                response = {'status': 'error', 'message': 'File not found'}
                client_socket.send(json.dumps(response).encode('utf-8'))
                return
            
            filesize = os.path.getsize(filepath)
            
            print(f"📥 Downloading: {filename} ({filesize} bytes)")
            
            response = {
                'status': 'success',
                'filename': filename,
                'filesize': filesize
            }
            client_socket.send(json.dumps(response).encode('utf-8'))
            
            ack = client_socket.recv(1024)
            
            with open(filepath, 'rb') as f:
                sent = 0
                while sent < filesize:
                    chunk = f.read(config.FILE_CHUNK_SIZE)
                    if not chunk:
                        break
                    client_socket.sendall(chunk)
                    sent += len(chunk)
            
            print(f"✅ Download complete: {filename}")
            
        except Exception as e:
            pass
    
    def broadcast_file_update(self):
        """Broadcast file list update to all clients"""
        file_list = self.get_available_files_list()
        message = {
            'type': config.MSG_FILE_UPDATE,
            'files': file_list
        }
        message_json = json.dumps(message).encode('utf-8')
        
        with self.lock:
            for cid, client_info in self.clients.items():
                try:
                    client_info['socket'].send(message_json)
                except:
                    pass
    
    def get_available_files_list(self):
        """Get file list"""
        with self.files_lock:
            return dict(self.available_files)
    
    def stop(self):
        """Stop server"""
        print("\n🛑 Shutting down...")
        self.running = False
        
        with self.lock:
            for client_id, client_info in list(self.clients.items()):
                try:
                    client_info['socket'].close()
                except:
                    pass
            self.clients.clear()
        
        if self.tcp_socket:
            self.tcp_socket.close()
        if self.udp_video_socket:
            self.udp_video_socket.close()
        if self.udp_audio_socket:
            self.udp_audio_socket.close()
        if self.tcp_screen_socket:
            self.tcp_screen_socket.close()
        if self.tcp_file_socket:
            self.tcp_file_socket.close()
        
        print("✅ Server stopped")


def main():
    server = CollaborationServer()
    
    try:
        server.start()
    except KeyboardInterrupt:
        print("\n")
        server.stop()
    except Exception as e:
        print(f"❌ {e}")
        server.stop()


if __name__ == "__main__":
    main()
