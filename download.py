# download.py - Updated version dengan clean progress integration

import os
import asyncio
import subprocess
import logging
import json
import time
import re
from datetime import datetime
from typing import Optional, Tuple, Dict, Callable, List
from pathlib import Path
from uuid import uuid4

# Setup logging
logger = logging.getLogger(__name__)

class DownloadManager:
    def __init__(self, downloads_dir: str = "downloads"):
        self.downloads_dir = downloads_dir
        self.history_txt_file = "download_history.txt"
        self.history_json_file = "download_history.json"
        
        # Daily limits (MB per user per day)
        self.daily_limit_mb = 100
        self.user_usage = {}  # {user_id: {'date': 'YYYY-MM-DD', 'used_mb': int}}
        
        # Max file sizes
        self.max_single_file_mb = 50
        self.max_total_download_mb = 500
        
        self.init_history_files()
    
    def init_history_files(self):
        """Initialize history files if don't exist"""
        # TXT History
        if not os.path.exists(self.history_txt_file):
            with open(self.history_txt_file, 'w', encoding='utf-8') as f:
                f.write("# Download History - Format: timestamp | user_id | username | url | type | file_size_mb | download_status | upload_status\n")
            logger.info(f"📁 Created {self.history_txt_file}")
        
        # JSON History
        if not os.path.exists(self.history_json_file):
            with open(self.history_json_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
            logger.info(f"📁 Created {self.history_json_file}")
    
    def generate_download_id(self) -> str:
        """Generate unique download ID"""
        return f"dl_{int(time.time())}_{str(uuid4())[:8]}"
    
    def create_user_dirs(self, user_id: int):
        """Create user-specific directories"""
        user_dir = os.path.join(self.downloads_dir, str(user_id))
        audio_dir = os.path.join(user_dir, "audio")
        video_dir = os.path.join(user_dir, "video")
        
        os.makedirs(audio_dir, exist_ok=True)
        os.makedirs(video_dir, exist_ok=True)
        
        return audio_dir, video_dir
    
    def get_file_size_mb(self, file_path: str) -> float:
        """Get file size in MB"""
        try:
            if os.path.exists(file_path):
                return os.path.getsize(file_path) / (1024 * 1024)
        except:
            pass
        return 0.0
    
    def get_video_duration(self, file_path: str) -> float:
        """Get video duration in seconds using ffprobe"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return float(result.stdout.strip())
        except:
            return 0.0
    
    def check_daily_limit(self, user_id: int, size_mb: float) -> Tuple[bool, float]:
        """Check if user can download based on daily limit"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Reset if new day
        if user_id not in self.user_usage or self.user_usage[user_id]['date'] != today:
            self.user_usage[user_id] = {'date': today, 'used_mb': 0.0}
        
        current_used = self.user_usage[user_id]['used_mb']
        remaining = self.daily_limit_mb - current_used
        
        return size_mb <= remaining, remaining
    
    def update_usage(self, user_id: int, size_mb: float):
        """Update user usage"""
        today = datetime.now().strftime('%Y-%m-%d')
        if user_id not in self.user_usage or self.user_usage[user_id]['date'] != today:
            self.user_usage[user_id] = {'date': today, 'used_mb': 0.0}
        
        self.user_usage[user_id]['used_mb'] += size_mb
    
    def log_dual_history(self, download_id: str, user_id: int, username: str, url: str, 
                        file_type: str, file_size_mb: float, file_path: str, 
                        download_status: str, upload_status: str):
        """Log to both TXT and JSON history files"""
        timestamp = datetime.now().isoformat()
        
        # TXT History
        try:
            with open(self.history_txt_file, 'a', encoding='utf-8') as f:
                f.write(f"{timestamp} | {user_id} | {username} | {url} | {file_type} | {file_size_mb:.2f} | {download_status} | {upload_status}\n")
        except Exception as e:
            logger.error(f"Error writing TXT history: {e}")
        
        # JSON History
        try:
            # Read existing
            if os.path.exists(self.history_json_file):
                with open(self.history_json_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            else:
                history = []
            
            # Add new entry
            entry = {
                'download_id': download_id,
                'timestamp': timestamp,
                'user_id': user_id,
                'username': username,
                'url': url,
                'type': file_type,
                'file_size_mb': file_size_mb,
                'file_path': file_path,
                'download_status': download_status,
                'upload_status': upload_status,
                'retry_count': 0
            }
            
            history.append(entry)
            
            # Write back
            with open(self.history_json_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error writing JSON history: {e}")
    
    async def get_video_info(self, url: str, progress_callback: Optional[Callable] = None) -> Optional[Dict]:
        """Get video information using yt-dlp - CLEANED VERSION"""
        try:
            if progress_callback:
                await progress_callback("Getting video info...")
            
            cmd = [
                'yt-dlp',
                '--no-download',
                '--print-json',
                '--no-playlist',
                url
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"yt-dlp info error: {stderr.decode()}")
                return None
            
            info = json.loads(stdout.decode())
            
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'view_count': info.get('view_count', 0),
                'description': info.get('description', '')[:200],
                'thumbnail': info.get('thumbnail', ''),
                'webpage_url': info.get('webpage_url', url)
            }
            
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return None
    
    async def monitor_yt_dlp_progress(self, process, progress_callback: Optional[Callable] = None):
        """Monitor yt-dlp progress dan kirim ke progress_manager"""
        if not progress_callback:
            await process.communicate()
            return
        
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                
                line = line.decode('utf-8', errors='ignore').strip()
                
                # Parse yt-dlp progress output
                if '[download]' in line and '%' in line:
                    try:
                        # Extract percentage
                        percent_match = re.search(r'(\d+\.?\d*)%', line)
                        if percent_match:
                            percentage = float(percent_match.group(1))
                            
                            # Extract speed
                            speed_match = re.search(r'at\s+([^\s]+/s)', line)
                            speed = speed_match.group(1) if speed_match else None
                            
                            # Extract ETA
                            eta_match = re.search(r'ETA\s+([^\s]+)', line)
                            eta = eta_match.group(1) if eta_match else None
                            
                            # Send clean progress data
                            status = "Downloading..."
                            if progress_callback:
                                await progress_callback(f"{status}|{percentage}|{speed}|{eta}")
                    except:
                        pass
            
            await process.wait()
            
        except Exception as e:
            logger.error(f"Error monitoring yt-dlp progress: {e}")
            await process.communicate()
    
    async def monitor_ffmpeg_progress(self, process, total_duration: float, 
                                    progress_callback: Optional[Callable] = None, 
                                    phase_name: str = "Converting"):
        """Monitor FFmpeg progress - SIMPLIFIED VERSION"""
        if not progress_callback or total_duration <= 0:
            await process.communicate()
            return
        
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                
                line = line.decode('utf-8', errors='ignore')
                
                # Look for time progress in FFmpeg output
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.?\d*)', line)
                if time_match:
                    hours = int(time_match.group(1))
                    minutes = int(time_match.group(2))
                    seconds = float(time_match.group(3))
                    
                    current_time = hours * 3600 + minutes * 60 + seconds
                    percent = min((current_time / total_duration) * 100, 100)
                    
                    # Send simple progress data
                    if progress_callback:
                        await progress_callback(f"{phase_name}|{percent}||")
            
            await process.wait()
            
        except Exception as e:
            logger.error(f"Error monitoring FFmpeg progress: {e}")
            await process.communicate()
    
    async def download_mp3(self, url: str, user_id: int, username: str = "", 
                          progress_callback: Optional[Callable] = None) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """Download audio as MP3 - CLEANED VERSION"""
        download_id = self.generate_download_id()
        
        try:
            # Create user directories
            audio_dir, _ = self.create_user_dirs(user_id)
            
            # Get video info
            if progress_callback:
                await progress_callback("Getting video info|5||")
            
            info = await self.get_video_info(url, progress_callback)
            if not info:
                self.log_dual_history(download_id, user_id, username, url, "MP3", 0, "", "FAILED", "N/A")
                return False, "❌ Failed to get video info. Check URL validity.", None, None
            
            # Check quota
            duration_minutes = info.get('duration', 0) / 60 if info.get('duration') else 5
            estimated_mb = duration_minutes * 1.0
            
            can_download, remaining_mb = self.check_daily_limit(user_id, estimated_mb)
            if not can_download:
                return False, f"❌ Daily limit reached! Remaining: {remaining_mb:.1f}MB", None, None
            
            # Sanitize filename
            safe_title = "".join(c for c in info['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title[:50]
            
            # Start download
            if progress_callback:
                await progress_callback("Starting download|10||")
            
            temp_video_path = os.path.join(audio_dir, f"temp_{safe_title}.%(ext)s")
            
            cmd = [
                'yt-dlp',
                '--format', 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio',
                '--no-playlist',
                '--output', temp_video_path,
                '--newline',
                url
            ]
            
            logger.info(f"🎵 Starting MP3 download: {url}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Monitor download progress
            await self.monitor_yt_dlp_progress(process, progress_callback)
            
            if process.returncode != 0:
                stderr_output = await process.stderr.read()
                error_msg = stderr_output.decode()
                logger.error(f"MP3 download failed: {error_msg}")
                self.log_dual_history(download_id, user_id, username, url, "MP3", 0, "", "FAILED", "N/A")
                return False, f"❌ Download failed: {error_msg[:100]}...", None, None
            
            # Find downloaded file
            downloaded_files = [f for f in os.listdir(audio_dir) if f.startswith(f"temp_{safe_title}")]
            if not downloaded_files:
                return False, "❌ File not found after download", None, None
            
            temp_file_path = os.path.join(audio_dir, downloaded_files[0])
            final_audio_path = os.path.join(audio_dir, f"{safe_title}.mp3")
            
            # Convert to MP3 if needed
            if not temp_file_path.endswith('.mp3'):
                if progress_callback:
                    await progress_callback("Converting to MP3|85||")
                
                cmd = [
                    'ffmpeg',
                    '-i', temp_file_path,
                    '-acodec', 'mp3',
                    '-ab', '128k',
                    '-y',
                    final_audio_path
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                duration = self.get_video_duration(temp_file_path)
                await self.monitor_ffmpeg_progress(process, duration, progress_callback, "Converting to MP3")
                
                # Remove temp file
                try:
                    os.remove(temp_file_path)
                except:
                    pass
            else:
                # Already MP3, just rename
                os.rename(temp_file_path, final_audio_path)
            
            # Finalize
            if progress_callback:
                await progress_callback("Complete|100||")
            
            file_size_mb = self.get_file_size_mb(final_audio_path)
            
            # Update usage & log history
            self.update_usage(user_id, file_size_mb)
            self.log_dual_history(download_id, user_id, username, url, "MP3", file_size_mb, final_audio_path, "SUCCESS", "PENDING")
            
            logger.info(f"✅ MP3 download completed: {final_audio_path} ({file_size_mb:.2f}MB)")
            
            return True, f"Download successful! ({file_size_mb:.1f}MB)", final_audio_path, download_id
            
        except Exception as e:
            logger.error(f"MP3 download error: {e}")
            self.log_dual_history(download_id, user_id, username, url, "MP3", 0, "", "ERROR", "N/A")
            return False, f"❌ Error: {str(e)}", None, None
    
    async def download_mp4(self, url: str, user_id: int, username: str = "",
                          progress_callback: Optional[Callable] = None) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """Download video as MP4 - CLEANED VERSION"""
        download_id = self.generate_download_id()
        
        try:
            # Create user directories
            _, video_dir = self.create_user_dirs(user_id)
            
            # Get video info
            if progress_callback:
                await progress_callback("Getting video info|5||")
            
            info = await self.get_video_info(url, progress_callback)
            if not info:
                self.log_dual_history(download_id, user_id, username, url, "MP4", 0, "", "FAILED", "N/A")
                return False, "❌ Failed to get video info. Check URL validity.", None, None
            
            # Check quota
            duration_minutes = info.get('duration', 0) / 60 if info.get('duration') else 3
            estimated_mb = duration_minutes * 5.0
            
            can_download, remaining_mb = self.check_daily_limit(user_id, estimated_mb)
            if not can_download:
                return False, f"❌ Daily limit reached! Remaining: {remaining_mb:.1f}MB", None, None
            
            # Sanitize filename
            safe_title = "".join(c for c in info['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title[:50]
            
            # Start download
            if progress_callback:
                await progress_callback("Starting download|10||")
            
            output_path = os.path.join(video_dir, f"{safe_title}.%(ext)s")
            
            cmd = [
                'yt-dlp',
                '--format', 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
                '--no-playlist',
                '--output', output_path,
                '--newline',
                url
            ]
            
            logger.info(f"🎬 Starting MP4 download: {url}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Monitor download progress
            await self.monitor_yt_dlp_progress(process, progress_callback)
            
            if process.returncode != 0:
                stderr_output = await process.stderr.read()
                error_msg = stderr_output.decode()
                logger.error(f"MP4 download failed: {error_msg}")
                self.log_dual_history(download_id, user_id, username, url, "MP4", 0, "", "FAILED", "N/A")
                return False, f"❌ Download failed: {error_msg[:100]}...", None, None
            
            # Find downloaded file
            downloaded_files = [f for f in os.listdir(video_dir) if safe_title in f and any(f.endswith(ext) for ext in ['.mp4', '.mkv', '.webm'])]
            if not downloaded_files:
                return False, "❌ File not found after download", None, None
            
            file_path = os.path.join(video_dir, downloaded_files[0])
            
            # Convert to MP4 if needed
            if not file_path.endswith('.mp4'):
                if progress_callback:
                    await progress_callback("Converting to MP4|85||")
                
                mp4_path = file_path.rsplit('.', 1)[0] + '.mp4'
                await self.convert_to_mp4_with_progress(file_path, mp4_path, progress_callback)
                
                if os.path.exists(mp4_path):
                    os.remove(file_path)  # Remove original
                    file_path = mp4_path
            
            # Finalize
            if progress_callback:
                await progress_callback("Complete|100||")
            
            file_size_mb = self.get_file_size_mb(file_path)
            
            # Update usage
            self.update_usage(user_id, file_size_mb)
            
            # Log dual history
            self.log_dual_history(download_id, user_id, username, url, "MP4", file_size_mb, file_path, "SUCCESS", "PENDING")
            
            logger.info(f"✅ MP4 download completed: {file_path} ({file_size_mb:.2f}MB)")
            
            return True, f"✅ Download successful! ({file_size_mb:.1f}MB)", file_path, download_id
            
        except Exception as e:
            logger.error(f"MP4 download error: {e}")
            self.log_dual_history(download_id, user_id, username, url, "MP4", 0, "", "ERROR", "N/A")
            return False, f"❌ Error: {str(e)}", None, None
    
    async def convert_to_mp4_with_progress(self, input_path: str, output_path: str,
                                         progress_callback: Optional[Callable] = None):
        """Convert video to MP4 using ffmpeg - SIMPLIFIED"""
        try:
            # Get duration for progress calculation
            duration = self.get_video_duration(input_path)
            
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-crf', '23',
                '-preset', 'fast',
                '-progress', 'pipe:2',
                '-y',
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Monitor progress
            await self.monitor_ffmpeg_progress(process, duration, progress_callback, "Converting to MP4")
            
            logger.info(f"🔄 Converted to MP4: {output_path}")
            
        except Exception as e:
            logger.error(f"Error converting to MP4: {e}")


# ================================
# STANDALONE FUNCTIONS REQUIRED BY menu_utama.py
# ================================

# Global instance
_download_manager = None

def get_download_manager():
    """Get or create global download manager instance"""
    global _download_manager
    if _download_manager is None:
        _download_manager = DownloadManager()
    return _download_manager

def validate_download_url(url: str) -> Tuple[bool, str]:
    """Validate download URL - STANDALONE FUNCTION"""
    url = url.strip()
    
    if not url:
        return False, "❌ URL tidak boleh kosong!"
    
    if not (url.startswith('http://') or url.startswith('https://')):
        return False, "❌ URL harus dimulai dengan http:// atau https://"
    
    # YouTube
    if any(domain in url for domain in ['youtube.com', 'youtu.be', 'm.youtube.com']):
        return True, "YouTube"
    
    # TikTok
    if any(domain in url for domain in ['tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com']):
        return True, "TikTok"
    
    # Instagram
    if any(domain in url for domain in ['instagram.com', 'instagr.am']):
        return True, "Instagram"
    
    # Twitter/X
    if any(domain in url for domain in ['twitter.com', 'x.com', 't.co']):
        return True, "Twitter/X"
    
    return False, "❌ Platform tidak didukung. Gunakan YouTube, TikTok, atau Instagram."

def check_file_needs_splitting(file_path: str, max_size_mb: int = 50) -> bool:
    """Check if file needs to be split - STANDALONE FUNCTION"""
    try:
        if not os.path.exists(file_path):
            return False
        
        file_size = os.path.getsize(file_path)
        max_size_bytes = max_size_mb * 1024 * 1024  # Convert MB to bytes
        
        return file_size > max_size_bytes
    except Exception:
        return False

def update_upload_status_in_history(download_id: str, status: str):
    """Update upload status in history JSON - STANDALONE FUNCTION"""
    try:
        history_file = "download_history.json"
        
        # Read existing history
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []
        
        # Find and update entry
        for entry in history:
            if entry.get('download_id') == download_id:
                entry['upload_status'] = status
                entry['upload_updated'] = datetime.now().isoformat()
                break
        
        # Write back to file
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        logger.error(f"Error updating upload status: {e}")

def clear_history_json() -> int:
    """Clear JSON history and return count of cleared entries - STANDALONE FUNCTION"""
    try:
        history_file = "download_history.json"
        
        if not os.path.exists(history_file):
            return 0
        
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
        
        count = len(history)
        
        # Clear the file
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump([], f)
        
        return count
    except Exception:
        return 0

async def download_youtube_mp3_with_progress(url: str, user_id: int, username: str, progress_callback) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """Download YouTube MP3 with progress - STANDALONE FUNCTION"""
    dm = get_download_manager()
    return await dm.download_mp3(url, user_id, username, progress_callback)

async def download_video_mp4_with_progress(url: str, user_id: int, username: str, progress_callback) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """Download video MP4 with progress - STANDALONE FUNCTION"""
    dm = get_download_manager()
    return await dm.download_mp4(url, user_id, username, progress_callback)

# Additional utility functions
def get_file_size_mb(file_path: str) -> float:
    """Get file size in MB - STANDALONE FUNCTION"""
    try:
        if os.path.exists(file_path):
            return os.path.getsize(file_path) / (1024 * 1024)
    except:
        pass
    return 0.0

def create_user_directories(user_id: int) -> Tuple[str, str]:
    """Create user directories - STANDALONE FUNCTION"""
    dm = get_download_manager()
    return dm.create_user_dirs(user_id)