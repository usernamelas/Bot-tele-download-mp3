import asyncio
import aiohttp
import json
import logging
import os
import sys
import time
from typing import Dict, List, Set, Optional
from datetime import datetime

# Add current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DownloadBot:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.last_update_id = 0
        self.session = None
        
        # File paths
        self.admin_file = "admin.txt"
        self.allowed_file = "allowed_user.txt"
        self.downloads_dir = "downloads"
        
        # User sessions - track apa yang user lagi lakuin
        self.user_sessions = {}  # {user_id: {'mode': 'mp3/mp4/idle', 'timestamp': datetime}}
        
        # Progress manager instance
        self.progress_manager = None
        
        # Initialize files & folders
        self.init_files()
        self.init_downloads_dir()
    
    def init_files(self):
        """Initialize required files if they don't exist"""
        if not os.path.exists(self.admin_file):
            with open(self.admin_file, 'w') as f:
                f.write("")
            logger.info(f"📁 Created {self.admin_file}")
        
        if not os.path.exists(self.allowed_file):
            with open(self.allowed_file, 'w') as f:
                f.write("")
            logger.info(f"📁 Created {self.allowed_file}")
    
    def init_downloads_dir(self):
        """Initialize downloads directory"""
        if not os.path.exists(self.downloads_dir):
            os.makedirs(self.downloads_dir)
            logger.info(f"📁 Created {self.downloads_dir} directory")
    
    def create_user_dir(self, user_id: int):
        """Create user-specific download directory"""
        user_dir = os.path.join(self.downloads_dir, str(user_id))
        audio_dir = os.path.join(user_dir, "audio")
        video_dir = os.path.join(user_dir, "video")
        
        os.makedirs(audio_dir, exist_ok=True)
        os.makedirs(video_dir, exist_ok=True)
        
        return user_dir, audio_dir, video_dir
    
    def read_file_ids(self, filename: str) -> Set[int]:
        """Read user IDs from file"""
        try:
            with open(filename, 'r') as f:
                ids = set()
                for line in f:
                    line = line.strip()
                    if line and line.isdigit():
                        ids.add(int(line))
                return ids
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            return set()
    
    def write_file_ids(self, filename: str, ids: Set[int]):
        """Write user IDs to file"""
        try:
            with open(filename, 'w') as f:
                for user_id in sorted(ids):
                    f.write(f"{user_id}\n")
        except Exception as e:
            logger.error(f"Error writing {filename}: {e}")
    
    def add_user_to_file(self, filename: str, user_id: int):
        """Add user ID to file"""
        ids = self.read_file_ids(filename)
        ids.add(user_id)
        self.write_file_ids(filename, ids)
    
    def remove_user_from_file(self, filename: str, user_id: int):
        """Remove user ID from file"""
        ids = self.read_file_ids(filename)
        ids.discard(user_id)
        self.write_file_ids(filename, ids)
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        admin_ids = self.read_file_ids(self.admin_file)
        return user_id in admin_ids
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user is in allowed list"""
        allowed_ids = self.read_file_ids(self.allowed_file)
        return user_id in allowed_ids
    
    def get_admin_list(self) -> List[int]:
        """Get list of admin IDs"""
        return list(self.read_file_ids(self.admin_file))
    
    def set_user_session(self, user_id: int, mode: str):
        """Set user session mode"""
        self.user_sessions[user_id] = {
            'mode': mode,
            'timestamp': datetime.now(),
            'username': self.user_sessions.get(user_id, {}).get('username', '')  # Preserve username
        }
        logger.info(f"👤 User {user_id} session: {mode}")
    
    def get_user_session(self, user_id: int) -> str:
        """Get user current session mode"""
        if user_id in self.user_sessions:
            return self.user_sessions[user_id]['mode']
        return 'idle'
    
    def clear_user_session(self, user_id: int):
        """Clear user session"""
        if user_id in self.user_sessions:
            del self.user_sessions[user_id]
            logger.info(f"🗑️ User {user_id} session cleared")
    
    async def start(self):
        """Initialize bot session"""
        # Initialize progress manager FIRST
        try:
            from progress_manager import init_progress_manager
            self.progress_manager = init_progress_manager(self.token)
            logger.info("📊 Progress manager initialized")
        except ImportError:
            logger.warning("⚠️ progress_manager.py not found, using basic progress mode")
            self.progress_manager = None
        except Exception as e:
            logger.warning(f"⚠️ Progress manager error: {e}")
            self.progress_manager = None
        
        # Initialize retry manager
        try:
            from ping import init_retry_manager, start_background_monitoring
            self.retry_manager = init_retry_manager(self.token)
            logger.info("🔄 Retry manager initialized")
            
            # Start background monitoring in separate task
            asyncio.create_task(start_background_monitoring())
            logger.info("📡 Background network monitoring started")
        except ImportError:
            logger.warning("⚠️ ping.py not found, using basic upload mode")
            self.retry_manager = None
        except Exception as e:
            logger.warning(f"⚠️ Retry manager error: {e}")
            self.retry_manager = None
        
        # Initialize video splitter
        try:
            from split import init_video_splitter
            self.video_splitter = init_video_splitter()
            logger.info("✂️ Video splitter initialized")
        except ImportError:
            logger.warning("⚠️ split.py not found, large videos will be rejected")
            self.video_splitter = None
        except Exception as e:
            logger.warning(f"⚠️ Video splitter error: {e}")
            self.video_splitter = None
        
        # Create session with timeout
        timeout = aiohttp.ClientTimeout(total=60, connect=30)
        self.session = aiohttp.ClientSession(timeout=timeout)
        logger.info("🤖 Download Bot started!")
        
        # Get bot info with retry
        for attempt in range(3):
            try:
                me = await self.get_me()
                if me:
                    logger.info(f"✅ Connected as: {me.get('first_name')} (@{me.get('username')})")
                    break
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
                else:
                    logger.error("Failed to connect after 3 attempts")
        
        # Show file status
        admin_count = len(self.read_file_ids(self.admin_file))
        allowed_count = len(self.read_file_ids(self.allowed_file))
        logger.info(f"📊 Admin: {admin_count}, Allowed Users: {allowed_count}")
        
        # Start polling
        await self.polling()
    
    async def get_me(self) -> Dict:
        """Get bot information"""
        try:
            async with self.session.get(f"{self.base_url}/getMe") as response:
                data = await response.json()
                return data.get('result', {})
        except Exception as e:
            logger.error(f"Error getting bot info: {e}")
            return {}
    
    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> bool:
        """Send message to chat"""
        try:
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML'
            }
            
            if reply_markup:
                payload['reply_markup'] = json.dumps(reply_markup)
            
            async with self.session.post(f"{self.base_url}/sendMessage", json=payload) as response:
                data = await response.json()
                return data.get('ok', False)
        
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False
    
    async def send_audio(self, chat_id: int, audio_path: str, caption: str = "") -> bool:
        """Send audio file"""
        try:
            with open(audio_path, 'rb') as audio_file:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(chat_id))
                data.add_field('audio', audio_file, filename=os.path.basename(audio_path))
                if caption:
                    data.add_field('caption', caption)
                
                async with self.session.post(f"{self.base_url}/sendAudio", data=data) as response:
                    result = await response.json()
                    return result.get('ok', False)
                    
        except Exception as e:
            logger.error(f"Error sending audio: {e}")
            return False
    
    async def send_video(self, chat_id: int, video_path: str, caption: str = "") -> bool:
        """Send video file"""
        try:
            with open(video_path, 'rb') as video_file:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(chat_id))
                data.add_field('video', video_file, filename=os.path.basename(video_path))
                if caption:
                    data.add_field('caption', caption)
                
                async with self.session.post(f"{self.base_url}/sendVideo", data=data) as response:
                    result = await response.json()
                    return result.get('ok', False)
                    
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            return False
    
    async def get_updates(self, offset: int = 0) -> List[Dict]:
        """Get updates from Telegram"""
        try:
            params = {
                'offset': offset,
                'timeout': 30,
                'allowed_updates': ['message', 'callback_query']
            }
            
            async with self.session.get(f"{self.base_url}/getUpdates", params=params) as response:
                data = await response.json()
                return data.get('result', [])
        
        except Exception as e:
            logger.error(f"Error getting updates: {e}")
            return []
    
    async def answer_callback_query(self, callback_query_id: str, text: str = ""):
        """Answer callback query"""
        try:
            payload = {
                'callback_query_id': callback_query_id,
                'text': text
            }
            
            async with self.session.post(f"{self.base_url}/answerCallbackQuery", json=payload):
                pass
        except Exception as e:
            logger.error(f"Error answering callback query: {e}")
    
    async def edit_message_text(self, chat_id: int, message_id: int, text: str):
        """Edit message text"""
        try:
            payload = {
                'chat_id': chat_id,
                'message_id': message_id,
                'text': text,
                'parse_mode': 'HTML'
            }
            
            async with self.session.post(f"{self.base_url}/editMessageText", json=payload):
                pass
        except Exception as e:
            logger.error(f"Error editing message: {e}")
    
    async def handle_start_command(self, user_id: int, username: str, first_name: str):
        """Handle /start command with different responses based on user status"""
        logger.info(f"📝 /start from user {user_id} ({first_name})")
        
        # Clear any existing session
        self.clear_user_session(user_id)
        
        # Store username for future use
        self.user_sessions[user_id] = {'mode': 'idle', 'username': username, 'timestamp': datetime.now()}
        
        # Check user status
        if self.is_admin(user_id):
            # Admin greeting with special menu
            admin_message = (
                f"👑 <b>Selamat datang Admin {first_name}!</b>\n\n"
                f"🎛️ <b>Menu Admin:</b>\n"
                f"• /approve &lt;user_id&gt; - Approve user\n"
                f"• /kick &lt;user_id&gt; - Remove user access\n"
                f"• /list - Lihat daftar allowed users\n"
                f"• /addadmin &lt;user_id&gt; - Tambah admin baru\n"
                f"• /listadmin - Lihat daftar admin\n"
                f"• /stats - Statistik bot\n"
                f"• /clearhistory - Clear JSON history\n"
                f"• /cleanup - Clean temp files\n\n"
                f"📥 <b>Download Menu (Real-time Progress):</b>\n"
                f"🎵 /mp3 - Download audio dari YouTube\n"
                f"🎬 /mp4 - Download video (YouTube, TikTok, Instagram)\n"
                f"❌ /close - Tutup session download\n\n"
                f"📋 <b>Status:</b>\n"
                f"👥 Allowed Users: {len(self.read_file_ids(self.allowed_file))}\n"
                f"👑 Total Admin: {len(self.read_file_ids(self.admin_file))}"
            )
            await self.send_message(user_id, admin_message)
            return
        
        elif self.is_allowed(user_id):
            # Allowed user greeting with usage info
            user_message = (
                f"✅ <b>Selamat datang kembali, {first_name}!</b>\n\n"
                f"🎉 Kamu sudah memiliki akses penuh ke bot ini.\n\n"
                f"📥 <b>Download Menu (Real-time Progress):</b>\n"
                f"🎵 /mp3 - Download audio dari YouTube\n"
                f"🎬 /mp4 - Download video (YouTube, TikTok, Instagram)\n"
                f"❌ /close - Tutup session download\n\n"
                f"📚 <b>Command Lain:</b>\n"
                f"• /help - Bantuan penggunaan\n"
                f"• /info - Informasi bot\n\n"
                f"💡 <b>Cara pakai:</b>\n"
                f"1. Pilih /mp3 atau /mp4\n"
                f"2. Kirim link video\n"
                f"3. Tunggu progress real-time & file dikirim!\n\n"
                f"🌟 <b>Features:</b>\n"
                f"✅ Real-time progress tracking\n"
                f"✅ Auto-split large files\n"
                f"✅ Network retry system"
            )
            await self.send_message(user_id, user_message)
            return
        
        else:
            # New user - send approval request to all admins
            admin_ids = self.get_admin_list()
            
            if not admin_ids:
                await self.send_message(
                    user_id,
                    "❌ Maaf, belum ada admin yang terdaftar.\n"
                    "Silakan hubungi pemilik bot."
                )
                return
            
            # Send approval request to all admins
            admin_message = (
                f"🔔 <b>Permintaan Akses Baru</b>\n\n"
                f"👤 Nama: {first_name}\n"
                f"🆔 Username: @{username if username else 'No username'}\n"
                f"🔢 User ID: <code>{user_id}</code>\n\n"
                f"Gunakan tombol di bawah atau command manual:"
            )
            
            # Create inline keyboard for admin
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": f"approve_{user_id}"},
                        {"text": "❌ Reject", "callback_data": f"reject_{user_id}"}
                    ]
                ]
            }
            
            # Send to all admins
            for admin_id in admin_ids:
                await self.send_message(admin_id, admin_message, keyboard)
            
            # Reply to user
            await self.send_message(
                user_id,
                f"👋 Halo {first_name}!\n\n"
                "📝 Permintaan aksesmu telah dikirim ke admin.\n"
                "⏳ Silakan tunggu persetujuan admin."
            )
    
    async def handle_mp3_command(self, user_id: int, first_name: str):
        """Handle /mp3 command"""
        if not (self.is_admin(user_id) or self.is_allowed(user_id)):
            await self.send_message(user_id, "❌ Kamu belum memiliki akses. Kirim /start untuk request akses.")
            return
        
        # Set user session to MP3 mode
        self.set_user_session(user_id, 'mp3')
        
        # Create user directories
        self.create_user_dir(user_id)
        
        mp3_message = (
            f"🎵 <b>Mode MP3 Aktif!</b>\n\n"
            f"👋 Halo {first_name}!\n"
            f"📎 Kirim link YouTube untuk didownload sebagai MP3.\n\n"
            f"✅ <b>Support:</b> YouTube only\n"
            f"🎧 <b>Format:</b> MP3 128kbps\n"
            f"📊 <b>Progress:</b> Real-time single message update\n"
            f"🔄 <b>Auto-retry:</b> Network resilience\n"
            f"❌ <b>Tutup mode:</b> /close\n\n"
            f"💡 Contoh: https://youtube.com/watch?v=xxx"
        )
        
        await self.send_message(user_id, mp3_message)
    
    async def handle_mp4_command(self, user_id: int, first_name: str):
        """Handle /mp4 command"""
        if not (self.is_admin(user_id) or self.is_allowed(user_id)):
            await self.send_message(user_id, "❌ Kamu belum memiliki akses. Kirim /start untuk request akses.")
            return
        
        # Set user session to MP4 mode
        self.set_user_session(user_id, 'mp4')
        
        # Create user directories
        self.create_user_dir(user_id)
        
        mp4_message = (
            f"🎬 <b>Mode MP4 Aktif!</b>\n\n"
            f"👋 Halo {first_name}!\n"
            f"📎 Kirim link video untuk didownload sebagai MP4.\n\n"
            f"✅ <b>Support:</b> YouTube, TikTok, Instagram\n"
            f"📹 <b>Quality:</b> 720p (auto-optimized)\n"
            f"✂️ <b>Auto-split:</b> File >50MB dibagi otomatis\n"
            f"🗜️ <b>Compression:</b> Smart size optimization\n"
            f"📊 <b>Progress:</b> Real-time single message update\n"
            f"🔄 <b>Auto-retry:</b> Network resilience\n"
            f"❌ <b>Tutup mode:</b> /close\n\n"
            f"💡 Contoh: https://youtube.com/watch?v=xxx"
        )
        
        await self.send_message(user_id, mp4_message)
    
    async def handle_close_command(self, user_id: int):
        """Handle /close command"""
        if not (self.is_admin(user_id) or self.is_allowed(user_id)):
            await self.send_message(user_id, "❌ Kamu belum memiliki akses. Kirim /start untuk request akses.")
            return
        
        current_mode = self.get_user_session(user_id)
        
        if current_mode == 'idle':
            await self.send_message(user_id, "ℹ️ Tidak ada session aktif.")
            return
        
        # Cancel any active progress
        if self.progress_manager and self.progress_manager.is_active(user_id):
            await self.progress_manager.cancel_progress(user_id)
        
        # Clear session
        self.clear_user_session(user_id)
        
        await self.send_message(
            user_id,
            f"✅ Session {current_mode.upper()} ditutup.\n\n"
            f"📥 Ketik /mp3 atau /mp4 untuk mulai download lagi."
        )
    
    async def handle_url_message(self, user_id: int, username: str, first_name: str, url: str):
        """Handle URL message dengan download integration"""
        current_mode = self.get_user_session(user_id)
        
        if current_mode == 'idle':
            await self.send_message(
                user_id,
                "❓ Pilih mode download dulu:\n"
                "🎵 /mp3 untuk audio\n"
                "🎬 /mp4 untuk video"
            )
            return
        
        # Import download functions dengan fallback graceful
        try:
            # Coba import download module
            import download
            
            # Cek apakah functions yang dibutuhkan ada
            required_functions = [
                'download_youtube_mp3_with_progress',
                'download_video_mp4_with_progress',
                'check_file_needs_splitting',
                'validate_download_url',
                'update_upload_status_in_history'
            ]
            
            missing_functions = []
            for func_name in required_functions:
                if not hasattr(download, func_name):
                    missing_functions.append(func_name)
            
            if missing_functions:
                await self.send_message(
                    user_id,
                    f"❌ Download module tidak lengkap.\n"
                    f"Functions yang hilang: {', '.join(missing_functions)}\n\n"
                    f"Pastikan download.py sudah update dengan semua functions yang diperlukan."
                )
                return
            
        except ImportError as e:
            await self.send_message(
                user_id,
                f"❌ Download module tidak ditemukan!\n\n"
                f"Error: {str(e)}\n\n"
                f"Pastikan file download.py ada di folder yang sama dengan menu_utama.py"
            )
            logger.error(f"Import error: {e}")
            return
        except Exception as e:
            await self.send_message(
                user_id,
                f"❌ Error loading download module: {str(e)}"
            )
            logger.error(f"Download module error: {e}")
            return
        
        # Validate URL
        try:
            is_valid, platform_info = download.validate_download_url(url)
            if not is_valid:
                await self.send_message(user_id, platform_info)
                return
        except Exception as e:
            await self.send_message(
                user_id,
                f"❌ Error validating URL: {str(e)}\n\n"
                f"URL: {url[:50]}..."
            )
            return
        
        logger.info(f"📥 Download request: {current_mode} | User: {user_id} | URL: {url}")
        
        try:
            # STEP 1: Initialize progress tracking
            progress_message_id = None
            progress_callback = None
            
            if self.progress_manager:
                # Use progress manager for real-time updates
                title = f"📥 Downloading {current_mode.upper()}"
                progress_message_id = await self.progress_manager.start_progress(user_id, user_id, title)
                
                if progress_message_id:
                    progress_callback = self.progress_manager.get_progress_callback(user_id)
                    logger.info(f"✅ Progress tracking started for user {user_id}")
                else:
                    # Fallback jika progress manager fail
                    await self.send_message(user_id, "⚠️ Progress tracking unavailable, using fallback mode...")
            
            # Fallback progress callback jika progress manager tidak available
            if not progress_callback:
                progress_updates = 0
                
                async def fallback_progress_callback(text):
                    nonlocal progress_updates
                    progress_updates += 1
                    # Limit updates untuk avoid spam
                    if progress_updates <= 3:  # Show only major milestones
                        await self.send_message(user_id, f"📊 {text}")
                
                progress_callback = fallback_progress_callback
                
                # Send initial processing message
                processing_msg = (
                    f"⏳ <b>Memproses...</b>\n\n"
                    f"🔗 Link: {url[:50]}...\n"
                    f"📥 Mode: {current_mode.upper()}\n"
                    f"👤 User: {first_name}\n"
                    f"🌐 Platform: {platform_info}\n\n"
                    f"🚀 Starting download..."
                )
                await self.send_message(user_id, processing_msg)
            
            # STEP 2: Execute download with progress
            if current_mode == 'mp3':
                success, message, file_path, download_id = await download.download_youtube_mp3_with_progress(
                    url, user_id, username or first_name, progress_callback
                )
                
                # STEP 3: Finish progress tracking
                if self.progress_manager and hasattr(self.progress_manager, 'is_active') and self.progress_manager.is_active(user_id):
                    if success:
                        await self.progress_manager.finish_progress(user_id, True, "Download complete!")
                    else:
                        await self.progress_manager.finish_progress(user_id, False, "Download failed!")
                
                # STEP 4: Handle file sending
                if success and file_path:
                    caption = f"🎵 {message}\n👤 Requested by: {first_name}"
                    
                    try:
                        # Use retry-enabled sending jika available
                        if hasattr(self, 'retry_manager') and self.retry_manager:
                            try:
                                from ping import send_audio_with_retry
                                audio_sent = await send_audio_with_retry(user_id, file_path, caption)
                            except ImportError:
                                # Fallback to basic sending
                                audio_sent = await self.send_audio(user_id, file_path, caption)
                        else:
                            # Fallback to basic sending
                            audio_sent = await self.send_audio(user_id, file_path, caption)
                        
                        # Update history
                        if download_id:
                            status = "SUCCESS" if audio_sent else "FAILED"
                            download.update_upload_status_in_history(download_id, status)
                        
                        if audio_sent:
                            await self.send_message(user_id, "✅ Audio berhasil dikirim!\n\nKirim link lain atau /close untuk keluar.")
                        else:
                            await self.send_message(user_id, 
                                "⏳ Audio download berhasil, tapi gagal dikirim karena koneksi.\n"
                                "📡 File akan otomatis dikirim ulang saat koneksi membaik!\n\n"
                                "Kirim link lain atau /close untuk keluar."
                            )
                    except Exception as e:
                        logger.error(f"Error sending audio: {e}")
                        await self.send_message(user_id, f"❌ Gagal mengirim file audio: {str(e)}")
                else:
                    await self.send_message(user_id, message)
            
            elif current_mode == 'mp4':
                success, message, file_path, download_id = await download.download_video_mp4_with_progress(
                    url, user_id, username or first_name, progress_callback
                )
                
                # Finish progress tracking
                if self.progress_manager and hasattr(self.progress_manager, 'is_active') and self.progress_manager.is_active(user_id):
                    if success:
                        await self.progress_manager.finish_progress(user_id, True, "Download complete!")
                    else:
                        await self.progress_manager.finish_progress(user_id, False, "Download failed!")
                
                if success and file_path:
                    # Check if file needs splitting
                    if download.check_file_needs_splitting(file_path):
                        # Large file - use split.py
                        await self.send_message(
                            user_id,
                            f"📹 {message}\n\n"
                            f"⚠️ File terlalu besar (>50MB)\n"
                            f"✂️ Memproses split & compression..."
                        )
                        
                        try:
                            if hasattr(self, 'video_splitter') and self.video_splitter:
                                try:
                                    from split import handle_large_video
                                    
                                    # Progress callback untuk splitting
                                    async def split_progress_callback(text):
                                        await self.send_message(user_id, text)
                                    
                                    # Function untuk send video (compatible dengan splitter)
                                    async def send_video_function(uid, video_path, caption=""):
                                        if hasattr(self, 'retry_manager') and self.retry_manager:
                                            try:
                                                from ping import send_video_with_retry
                                                return await send_video_with_retry(uid, video_path, caption)
                                            except ImportError:
                                                return await self.send_video(uid, video_path, caption)
                                        else:
                                            return await self.send_video(uid, video_path, caption)
                                    
                                    # Process large video
                                    split_success, split_message = await handle_large_video(
                                        file_path, user_id, send_video_function, split_progress_callback
                                    )
                                    
                                    # Update history
                                    if download_id:
                                        status = "SUCCESS" if split_success else "FAILED"
                                        download.update_upload_status_in_history(download_id, status)
                                    
                                    # Clean up original large file
                                    try:
                                        os.remove(file_path)
                                        logger.info(f"🗑️ Cleaned up original large file: {file_path}")
                                    except:
                                        pass
                                    
                                    if split_success:
                                        await self.send_message(user_id, f"✅ {split_message}\n\nKirim link lain atau /close untuk keluar.")
                                    else:
                                        await self.send_message(user_id, f"❌ {split_message}")
                                        
                                except ImportError:
                                    await self.send_message(
                                        user_id,
                                        f"❌ File terlalu besar untuk dikirim (>50MB)\n"
                                        f"Video splitter tidak tersedia (split.py not found).\n\n"
                                        f"Coba video yang lebih kecil atau /close untuk keluar."
                                    )
                            else:
                                await self.send_message(
                                    user_id,
                                    f"❌ File terlalu besar untuk dikirim (>50MB)\n"
                                    f"Video splitter tidak tersedia.\n\n"
                                    f"Coba video yang lebih kecil atau /close untuk keluar."
                                )
                        except Exception as e:
                            logger.error(f"Error processing large video: {e}")
                            await self.send_message(user_id, f"❌ Error memproses video besar: {str(e)}")
                    else:
                        # Normal size file - direct send
                        caption = f"🎬 {message}\n👤 Requested by: {first_name}"
                        
                        try:
                            # Use retry-enabled sending jika available
                            if hasattr(self, 'retry_manager') and self.retry_manager:
                                try:
                                    from ping import send_video_with_retry
                                    video_sent = await send_video_with_retry(user_id, file_path, caption)
                                except ImportError:
                                    # Fallback to basic sending
                                    video_sent = await self.send_video(user_id, file_path, caption)
                            else:
                                # Fallback to basic sending
                                video_sent = await self.send_video(user_id, file_path, caption)
                            
                            # Update history
                            if download_id:
                                status = "SUCCESS" if video_sent else "FAILED"
                                download.update_upload_status_in_history(download_id, status)
                            
                            if video_sent:
                                # Clean up video file after successful sending
                                try:
                                    os.remove(file_path)
                                    logger.info(f"🗑️ Cleaned up video file: {file_path}")
                                except:
                                    pass
                                
                                await self.send_message(user_id, "✅ Video berhasil dikirim!\n\nKirim link lain atau /close untuk keluar.")
                            else:
                                await self.send_message(user_id,
                                    "⏳ Video download berhasil, tapi gagal dikirim karena koneksi.\n"
                                    "📡 File akan otomatis dikirim ulang saat koneksi membaik!\n\n"
                                    "Kirim link lain atau /close untuk keluar."
                                )
                        except Exception as e:
                            logger.error(f"Error sending video: {e}")
                            await self.send_message(user_id, f"❌ Gagal mengirim file video: {str(e)}")
                else:
                    await self.send_message(user_id, message)
        
        except Exception as e:
            logger.error(f"Download error: {e}")
            
            # Cancel progress if active
            if self.progress_manager and hasattr(self.progress_manager, 'is_active') and self.progress_manager.is_active(user_id):
                await self.progress_manager.finish_progress(user_id, False, "Error occurred!")
            
            await self.send_message(
                user_id, 
                f"❌ Terjadi error saat download:\n{str(e)}\n\nCoba lagi atau /close untuk keluar."
            )
    
    # Admin Commands
    async def handle_approve_command(self, user_id: int, args: List[str]):
        """Handle /approve command"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        if not args:
            await self.send_message(user_id, "📝 Usage: /approve <user_id>\nContoh: /approve 123456789")
            return
        
        try:
            target_user_id = int(args[0])
            
            if self.is_allowed(target_user_id):
                await self.send_message(user_id, f"ℹ️ User ID {target_user_id} sudah diapprove sebelumnya.")
                return
            
            # Add to allowed users
            self.add_user_to_file(self.allowed_file, target_user_id)
            
            await self.send_message(user_id, f"✅ User ID {target_user_id} berhasil diapprove!")
            
            # Notify approved user
            await self.send_message(
                target_user_id,
                "🎉 <b>Selamat!</b>\n\n"
                "✅ Aksesmu telah disetujui admin.\n"
                "🚀 Sekarang kamu bisa menggunakan bot ini.\n\n"
                "Ketik /start untuk melihat menu!"
            )
            
        except ValueError:
            await self.send_message(user_id, "❌ User ID harus berupa angka!")
        except Exception as e:
            await self.send_message(user_id, f"❌ Error: {e}")
    
    async def handle_kick_command(self, user_id: int, args: List[str]):
        """Handle /kick command"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        if not args:
            await self.send_message(user_id, "📝 Usage: /kick <user_id>\nContoh: /kick 123456789")
            return
        
        try:
            target_user_id = int(args[0])
            
            if not self.is_allowed(target_user_id):
                await self.send_message(user_id, f"❌ User ID {target_user_id} tidak ditemukan dalam daftar allowed users.")
                return
            
            # Remove from allowed users
            self.remove_user_from_file(self.allowed_file, target_user_id)
            
            await self.send_message(user_id, f"✅ User ID {target_user_id} berhasil di-kick!")
            
            # Cancel active progress & clear session
            if self.progress_manager and hasattr(self.progress_manager, 'is_active') and self.progress_manager.is_active(target_user_id):
                await self.progress_manager.cancel_progress(target_user_id)
            
            self.clear_user_session(target_user_id)
            
            # Notify kicked user
            await self.send_message(
                target_user_id,
                "🚫 <b>Akses Dicabut</b>\n\n"
                "❌ Aksesmu telah dicabut oleh admin.\n"
                "📝 Kirim /start jika ingin mengajukan akses lagi."
            )
            
        except ValueError:
            await self.send_message(user_id, "❌ User ID harus berupa angka!")
        except Exception as e:
            await self.send_message(user_id, f"❌ Error: {e}")
    
    async def handle_list_command(self, user_id: int):
        """Handle /list command"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        allowed_ids = self.read_file_ids(self.allowed_file)
        
        if not allowed_ids:
            await self.send_message(user_id, "📋 Belum ada user yang diapprove.")
            return
        
        # Show active sessions too
        active_sessions = []
        progress_sessions = []
        
        for uid, session_info in self.user_sessions.items():
            if uid in allowed_ids:
                active_sessions.append(f"• {uid} - {session_info['mode']}")
                
                # Check if user has active progress
                if self.progress_manager and hasattr(self.progress_manager, 'is_active') and self.progress_manager.is_active(uid):
                    progress_sessions.append(f"• {uid} - downloading")
        
        user_list = "\n".join([f"• {uid}" for uid in sorted(allowed_ids)])
        
        message = f"📋 <b>Daftar Allowed Users ({len(allowed_ids)}):</b>\n\n<code>{user_list}</code>"
        
        if active_sessions:
            sessions_text = "\n".join(active_sessions)
            message += f"\n\n🔄 <b>Active Sessions:</b>\n<code>{sessions_text}</code>"
            
        if progress_sessions:
            progress_text = "\n".join(progress_sessions)
            message += f"\n\n📊 <b>Active Downloads:</b>\n<code>{progress_text}</code>"
        
        await self.send_message(user_id, message)
    
    async def handle_addadmin_command(self, user_id: int, args: List[str]):
        """Handle /addadmin command"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        if not args:
            await self.send_message(user_id, "📝 Usage: /addadmin <user_id>\nContoh: /addadmin 123456789")
            return
        
        try:
            target_user_id = int(args[0])
            
            if self.is_admin(target_user_id):
                await self.send_message(user_id, f"ℹ️ User ID {target_user_id} sudah admin.")
                return
            
            # Add to admin list
            self.add_user_to_file(self.admin_file, target_user_id)
            
            await self.send_message(user_id, f"✅ User ID {target_user_id} berhasil dijadikan admin!")
            
            # Notify new admin
            await self.send_message(
                target_user_id,
                "👑 <b>Selamat!</b>\n\n"
                "🎉 Kamu telah dijadikan admin bot ini.\n"
                "🛡️ Sekarang kamu memiliki akses penuh.\n\n"
                "Ketik /start untuk melihat menu admin!"
            )
            
        except ValueError:
            await self.send_message(user_id, "❌ User ID harus berupa angka!")
        except Exception as e:
            await self.send_message(user_id, f"❌ Error: {e}")
    
    async def handle_listadmin_command(self, user_id: int):
        """Handle /listadmin command"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        admin_ids = self.read_file_ids(self.admin_file)
        
        if not admin_ids:
            await self.send_message(user_id, "📋 Belum ada admin terdaftar.")
            return
        
        admin_list = "\n".join([f"• {uid}" for uid in sorted(admin_ids)])
        message = f"👑 <b>Daftar Admin ({len(admin_ids)}):</b>\n\n<code>{admin_list}</code>"
        
        await self.send_message(user_id, message)
    
    async def handle_stats_command(self, user_id: int):
        """Handle /stats command dengan progress manager info"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        admin_count = len(self.read_file_ids(self.admin_file))
        allowed_count = len(self.read_file_ids(self.allowed_file))
        active_sessions = len(self.user_sessions)
        
        # Count session types
        mp3_sessions = sum(1 for s in self.user_sessions.values() if s['mode'] == 'mp3')
        mp4_sessions = sum(1 for s in self.user_sessions.values() if s['mode'] == 'mp4')
        
        # Count active progress sessions
        active_downloads = 0
        if self.progress_manager and hasattr(self.progress_manager, 'is_active'):
            for uid in self.user_sessions.keys():
                try:
                    if self.progress_manager.is_active(uid):
                        active_downloads += 1
                except:
                    pass
        
        # Get network & retry stats
        network_status = "unknown"
        retry_stats = {}
        
        try:
            if hasattr(self, 'retry_manager') and self.retry_manager:
                try:
                    from ping import get_network_status, get_retry_statistics
                    network_status = get_network_status()
                    retry_stats = get_retry_statistics()
                except ImportError:
                    pass
        except:
            pass
        
        stats_message = (
            f"📊 <b>Statistik Bot</b>\n\n"
            f"👑 Total Admin: {admin_count}\n"
            f"👥 Allowed Users: {allowed_count}\n"
            f"🔄 Active Sessions: {active_sessions}\n"
            f"   🎵 MP3 Mode: {mp3_sessions}\n"
            f"   🎬 MP4 Mode: {mp4_sessions}\n"
            f"📊 Active Downloads: {active_downloads}\n\n"
            f"🌐 <b>Network Status:</b> {network_status.upper()}\n"
        )
        
        if retry_stats and 'total_failed' in retry_stats:
            stats_message += (
                f"🔄 <b>Retry Queue:</b> {retry_stats['total_failed']} files\n"
                f"   🎵 MP3: {retry_stats['by_type'].get('MP3', 0)}\n"
                f"   🎬 MP4: {retry_stats['by_type'].get('MP4', 0)}\n\n"
            )
        
        # Progress manager status
        progress_status = "✅ Active" if self.progress_manager else "❌ Not Available"
        stats_message += f"📊 <b>Progress Manager:</b> {progress_status}\n\n"
        
        stats_message += (
            f"📁 Download Directory: /{self.downloads_dir}/\n"
            f"🗂️ <b>Files:</b>\n"
            f"• {self.admin_file}\n"
            f"• {self.allowed_file}\n"
            f"• download_history.txt\n"
            f"• download_history.json\n"
            f"• progress_manager.py"
        )
        
        await self.send_message(user_id, stats_message)
    
    async def handle_clearhistory_command(self, user_id: int):
        """Handle /clearhistory command - clear JSON history only"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        try:
            cleared_count = download.clear_history_json()
            
            if cleared_count > 0:
                await self.send_message(
                    user_id,
                    f"🗑️ <b>History JSON Cleared</b>\n\n"
                    f"✅ {cleared_count} entries removed from JSON\n"
                    f"📝 TXT history tetap disimpan\n\n"
                    f"Retry queue telah dibersihkan."
                )
            else:
                await self.send_message(user_id, "ℹ️ JSON history sudah kosong.")
                
        except (ImportError, AttributeError):
            await self.send_message(user_id, "❌ Download module atau clear_history_json function tidak tersedia.")
        except Exception as e:
            await self.send_message(user_id, f"❌ Error clearing history: {e}")
    
    async def handle_cleanup_command(self, user_id: int):
        """Handle /cleanup command - cleanup temporary files"""
        if not self.is_admin(user_id):
            await self.send_message(user_id, "❌ Command ini khusus admin!")
            return
        
        try:
            cleanup_stats = []
            
            # Cancel all active downloads first
            if self.progress_manager and hasattr(self.progress_manager, 'cancel_progress'):
                cancelled_count = 0
                for uid in list(self.user_sessions.keys()):
                    try:
                        if hasattr(self.progress_manager, 'is_active') and self.progress_manager.is_active(uid):
                            await self.progress_manager.cancel_progress(uid)
                            cancelled_count += 1
                    except:
                        pass
                
                if cancelled_count > 0:
                    cleanup_stats.append(f"🛑 {cancelled_count} active downloads cancelled")
            
            # Cleanup split temp files
            if hasattr(self, 'video_splitter') and self.video_splitter:
                try:
                    from split import cleanup_temp_split_files
                    cleanup_temp_split_files()
                    cleanup_stats.append("✅ Split temp files cleaned")
                except ImportError:
                    cleanup_stats.append("⚠️ Split cleanup unavailable (split.py not found)")
                except Exception as e:
                    cleanup_stats.append(f"⚠️ Split cleanup error: {e}")
            
            # Cleanup old download files
            try:
                cleanup_count = 0
                
                # Clean video files older than 2 hours
                for root, dirs, files in os.walk(self.downloads_dir):
                    if 'video' in root:
                        for file in files:
                            file_path = os.path.join(root, file)
                            if os.path.isfile(file_path):
                                try:
                                    file_age = time.time() - os.path.getmtime(file_path)
                                    if file_age > 2 * 3600:  # 2 hours
                                        os.remove(file_path)
                                        cleanup_count += 1
                                except:
                                    pass
                
                if cleanup_count > 0:
                    cleanup_stats.append(f"✅ {cleanup_count} old video files cleaned")
                else:
                    cleanup_stats.append("ℹ️ No old video files to clean")
                    
            except Exception as e:
                cleanup_stats.append(f"⚠️ File cleanup error: {e}")
            
            if cleanup_stats:
                stats_text = "\n".join(cleanup_stats)
                await self.send_message(
                    user_id,
                    f"🧹 <b>Cleanup Complete</b>\n\n{stats_text}"
                )
            else:
                await self.send_message(user_id, "🧹 Cleanup completed successfully")
                
        except Exception as e:
            await self.send_message(user_id, f"❌ Error during cleanup: {e}")
    
    async def handle_help_command(self, user_id: int):
        """Handle /help command dengan info lengkap"""
        if self.is_admin(user_id):
            help_message = (
                f"🆘 <b>Bantuan Admin</b>\n\n"
                f"👑 <b>Command Admin:</b>\n"
                f"• /approve &lt;id&gt; - Approve user\n"
                f"• /kick &lt;id&gt; - Remove user\n"
                f"• /list - Daftar allowed users\n"
                f"• /addadmin &lt;id&gt; - Tambah admin\n"
                f"• /listadmin - Lihat daftar admin\n"
                f"• /stats - Statistik bot & network\n"
                f"• /clearhistory - Clear JSON history\n"
                f"• /cleanup - Clean temp files\n\n"
                f"📥 <b>Download Commands:</b>\n"
                f"• /mp3 - Mode download audio\n"
                f"• /mp4 - Mode download video\n"
                f"• /close - Tutup session\n\n"
                f"💡 <b>Features:</b>\n"
                f"✅ Real-time progress tracking\n"
                f"✅ Auto-split large files\n"
                f"✅ Network retry system\n"
                f"✅ Dual history logging"
            )
        else:
            help_message = (
                f"🆘 <b>Bantuan Pengguna</b>\n\n"
                f"📥 <b>Download Commands:</b>\n"
                f"• /mp3 - Download audio dari YouTube\n"
                f"• /mp4 - Download video (YT, TT, IG)\n"
                f"• /close - Tutup session download\n\n"
                f"📚 <b>Command Lain:</b>\n"
                f"• /start - Menu utama\n"
                f"• /help - Bantuan ini\n"
                f"• /info - Info bot\n\n"
                f"💡 <b>Cara pakai:</b>\n"
                f"1. Pilih /mp3 atau /mp4\n"
                f"2. Kirim link video\n"
                f"3. Lihat progress real-time!\n"
                f"4. File dikirim otomatis\n\n"
                f"🎯 <b>Features:</b>\n"
                f"✅ Progress tracking real-time\n"
                f"✅ Auto-split file besar (>50MB)\n"
                f"✅ Auto-retry jika gagal kirim\n"
                f"✅ Daily quota 100MB\n"
                f"✅ Multi-platform support"
            )
        
        await self.send_message(user_id, help_message)
    
    async def handle_info_command(self, user_id: int):
        """Handle /info command with enhanced info"""
        try:
            # Get download stats
            admin_count = len(self.read_file_ids(self.admin_file))
            allowed_count = len(self.read_file_ids(self.allowed_file))
            
            user_status = "👑 Admin" if self.is_admin(user_id) else ("✅ Allowed" if self.is_allowed(user_id) else "❌ Not Allowed")
            current_session = self.get_user_session(user_id)
            session_status = f"🔄 {current_session.upper()}" if current_session != 'idle' else "💤 Idle"
            
            # Check if user has active download
            download_status = ""
            if self.progress_manager and hasattr(self.progress_manager, 'is_active'):
                try:
                    if self.progress_manager.is_active(user_id):
                        download_status = "📊 Downloading..."
                except:
                    pass
            
            # Get network status
            network_status = "unknown"
            try:
                if hasattr(self, 'retry_manager') and self.retry_manager:
                    try:
                        from ping import get_network_status
                        network_status = get_network_status()
                    except ImportError:
                        pass
            except:
                pass
            
            info_message = (
                f"ℹ️ <b>Bot Information</b>\n\n"
                f"🤖 Bot: MongkayDownloader (v2.0)\n"
                f"👤 Your Status: {user_status}\n"
                f"📱 Session: {session_status}\n"
                f"🌐 Network: {network_status.upper()}\n"
            )
            
            if download_status:
                info_message += f"📊 Download: {download_status}\n"
            
            info_message += (
                f"\n📊 <b>Statistics:</b>\n"
                f"👑 Total Admin: {admin_count}\n"
                f"👥 Allowed Users: {allowed_count}\n"
                f"🔄 Active Sessions: {len(self.user_sessions)}\n\n"
                f"📥 <b>Supported:</b>\n"
                f"🎵 MP3: YouTube (128kbps)\n"
                f"🎬 MP4: YouTube, TikTok, Instagram (720p max)\n"
                f"✂️ Auto-split: Files >50MB\n"
                f"🔄 Auto-retry: Network resilience\n"
                f"📊 Real-time progress tracking\n\n"
            )
            
            # Add user-specific info
            if self.is_admin(user_id) or self.is_allowed(user_id):
                info_message += "📝 Kirim /help untuk bantuan penggunaan."
            else:
                info_message += "📝 Kirim /start untuk request akses."
            
            await self.send_message(user_id, info_message)
            
        except Exception as e:
            logger.error(f"Error in handle_info_command: {e}")
            await self.send_message(user_id, "❌ Error getting bot info.")
    
    async def handle_callback_query(self, callback_query):
        """Handle inline keyboard callbacks"""
        query_id = callback_query['id']
        user_id = callback_query['from']['id']
        data = callback_query['data']
        message = callback_query['message']
        
        await self.answer_callback_query(query_id)
        
        # Check if admin
        if not self.is_admin(user_id):
            return
        
        # Parse callback data
        if '_' not in data:
            return
        
        action, target_user_id = data.split('_', 1)
        target_user_id = int(target_user_id)
        
        if action == "approve":
            if not self.is_allowed(target_user_id):
                self.add_user_to_file(self.allowed_file, target_user_id)
            
            # Edit admin message
            new_text = f"✅ User ID {target_user_id} telah diapprove!\n\n{message['text']}"
            await self.edit_message_text(message['chat']['id'], message['message_id'], new_text)
            
            # Notify user
            await self.send_message(
                target_user_id,
                "🎉 <b>Selamat!</b>\n\n"
                "✅ Aksesmu telah disetujui admin.\n"
                "🚀 Sekarang kamu bisa menggunakan bot ini.\n\n"
                "Ketik /start untuk melihat menu download!"
            )
            
        elif action == "reject":
            # Edit admin message
            new_text = f"❌ User ID {target_user_id} telah ditolak.\n\n{message['text']}"
            await self.edit_message_text(message['chat']['id'], message['message_id'], new_text)
            
            # Notify user
            await self.send_message(
                target_user_id,
                "😔 <b>Permintaan Ditolak</b>\n\n"
                "❌ Maaf, permintaan aksesmu ditolak oleh admin.\n"
                "📝 Kamu bisa mencoba lagi nanti dengan /start"
            )
    
    def is_url(self, text: str) -> bool:
        """Check if text is a URL"""
        return text.startswith('http://') or text.startswith('https://')
    
    async def handle_message(self, message):
        """Handle incoming messages"""
        user = message.get('from', {})
        user_id = user.get('id')
        username = user.get('username', '')
        first_name = user.get('first_name', 'Unknown')
        text = message.get('text', '')
        
        # Handle non-command messages
        if not text.startswith('/'):
            # Check if user has access
            if not (self.is_admin(user_id) or self.is_allowed(user_id)):
                await self.send_message(user_id, "❌ Kamu belum memiliki akses. Kirim /start untuk request akses.")
                return
            
            # Check if it's a URL
            if self.is_url(text):
                await self.handle_url_message(user_id, username, first_name, text)
            else:
                current_mode = self.get_user_session(user_id)
                if current_mode != 'idle':
                    await self.send_message(
                        user_id,
                        f"📎 Kirim link untuk download {current_mode.upper()}.\n"
                        f"❌ Atau ketik /close untuk keluar."
                    )
            return
        
        # Parse command
        parts = text.split()
        command = parts[0].lower()
        args = parts[1:]
        
        # Handle commands
        if command == '/start':
            await self.handle_start_command(user_id, username, first_name)
        elif command == '/mp3':
            await self.handle_mp3_command(user_id, first_name)
        elif command == '/mp4':
            await self.handle_mp4_command(user_id, first_name)
        elif command == '/close':
            await self.handle_close_command(user_id)
        elif command == '/approve':
            await self.handle_approve_command(user_id, args)
        elif command == '/kick':
            await self.handle_kick_command(user_id, args)
        elif command == '/list':
            await self.handle_list_command(user_id)
        elif command == '/addadmin':
            await self.handle_addadmin_command(user_id, args)
        elif command == '/listadmin':
            await self.handle_listadmin_command(user_id)
        elif command == '/stats':
            await self.handle_stats_command(user_id)
        elif command == '/help':
            await self.handle_help_command(user_id)
        elif command == '/info':
            await self.handle_info_command(user_id)
        elif command == '/clearhistory':
            await self.handle_clearhistory_command(user_id)
        elif command == '/cleanup':
            await self.handle_cleanup_command(user_id)
        else:
            # Unknown command
            if self.is_admin(user_id) or self.is_allowed(user_id):
                await self.send_message(user_id, f"❓ Command '{command}' tidak dikenal. Ketik /help untuk bantuan.")
    
    async def handle_update(self, update):
        """Handle single update"""
        try:
            if 'message' in update:
                await self.handle_message(update['message'])
            elif 'callback_query' in update:
                await self.handle_callback_query(update['callback_query'])
        except Exception as e:
            logger.error(f"Error handling update: {e}")
    
    async def polling(self):
        """Main polling loop dengan cleanup periodic"""
        logger.info("📡 Starting polling...")
        
        try:
            last_cleanup = 0
            while True:
                updates = await self.get_updates(self.last_update_id + 1)
                
                for update in updates:
                    self.last_update_id = update['update_id']
                    await self.handle_update(update)
                
                # Periodic cleanup (every hour)
                current_time = time.time()
                if current_time - last_cleanup > 3600:  # 1 hour
                    try:
                        # Cleanup temp files
                        if hasattr(self, 'video_splitter') and self.video_splitter:
                            try:
                                from split import cleanup_temp_split_files
                                cleanup_temp_split_files()
                            except ImportError:
                                pass
                        
                        last_cleanup = current_time
                        logger.info("🧹 Periodic cleanup completed")
                    except Exception as e:
                        logger.error(f"Periodic cleanup error: {e}")
                
                if not updates:
                    await asyncio.sleep(1)
                    
        except KeyboardInterrupt:
            logger.info("🛑 Bot stopped by user")
        except Exception as e:
            logger.error(f"Polling error: {e}")
        finally:
            # Cleanup on exit
            if self.session:
                await self.session.close()
            
            # Stop monitoring
            try:
                if hasattr(self, 'retry_manager') and self.retry_manager:
                    try:
                        from ping import stop_background_monitoring
                        stop_background_monitoring()
                    except ImportError:
                        pass
            except:
                pass


# Main function
async def main():
    # Konfigurasi
    BOT_TOKEN = "7732517146:AAFxfT074Ma8kzWPWFXZgTR978hCAat-c0U"  # Token bot kamu
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Harap setting BOT_TOKEN terlebih dahulu!")
        print("1. Buat bot di @BotFather untuk dapat token")
        return
    
    print("🚀 Starting MongkayDownloader Bot...")
    print("📁 Files: admin.txt, allowed_user.txt akan dibuat otomatis")
    print("📂 Downloads akan disimpan di folder downloads/")
    print("🔄 Network monitoring & retry system enabled")
    print("✂️ Video splitting untuk file >50MB enabled")
    print("📊 Real-time progress tracking enabled")
    print("💡 Tips: Tambahkan user ID kamu ke admin.txt untuk menjadi admin pertama!")
    
    # Create and start bot
    bot = DownloadBot(BOT_TOKEN)
    await bot.start()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot dihentikan oleh user")
    except Exception as e:
        print(f"\n❌ Error menjalankan bot: {e}")
        print("💡 Pastikan semua dependencies terinstall dan file download.py tersedia")