import asyncio
import aiohttp
import json
import logging
import time
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

# Setup logging
logger = logging.getLogger(__name__)

class NetworkStatus(Enum):
    GOOD = "good"
    POOR = "poor" 
    OFFLINE = "offline"

class RetryManager:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.network_log_file = "network_status.log"
        self.history_json_file = "download_history.json"
        
        # Network monitoring settings
        self.ping_interval = 30  # Check network tiap 30 detik
        self.retry_interval = 120  # Coba retry tiap 2 menit kalau network bagus
        self.max_retries = 5
        self.timeout_threshold = 15  # Consider connection poor jika >15s
        
        # Current network status
        self.current_network_status = NetworkStatus.OFFLINE
        self.last_ping_time = 0
        self.consecutive_failures = 0
        
        self.session = None
        self.is_monitoring = False
        
        self.init_log_file()
    
    def init_log_file(self):
        """Initialize network log file"""
        if not os.path.exists(self.network_log_file):
            with open(self.network_log_file, 'w', encoding='utf-8') as f:
                f.write("# Network Status Log - Format: timestamp | status | response_time | error\n")
            logger.info(f"📁 Created {self.network_log_file}")
    
    def log_network_status(self, status: NetworkStatus, response_time: float = 0, error: str = ""):
        """Log network status ke file"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"{timestamp} | {status.value.upper()} | {response_time:.2f}s | {error}\n"
            
            with open(self.network_log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"Error logging network status: {e}")
    
    async def ping_telegram_api(self) -> Tuple[NetworkStatus, float]:
        """Ping Telegram API untuk cek network status"""
        try:
            start_time = time.time()
            
            if not self.session:
                timeout = aiohttp.ClientTimeout(total=20, connect=10)
                self.session = aiohttp.ClientSession(timeout=timeout)
            
            async with self.session.get(f"{self.base_url}/getMe") as response:
                response_time = time.time() - start_time
                
                if response.status == 200:
                    if response_time < 3:
                        return NetworkStatus.GOOD, response_time
                    elif response_time < self.timeout_threshold:
                        return NetworkStatus.POOR, response_time
                    else:
                        return NetworkStatus.POOR, response_time
                else:
                    return NetworkStatus.POOR, response_time
                    
        except asyncio.TimeoutError:
            response_time = time.time() - start_time
            return NetworkStatus.OFFLINE, response_time
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"Ping error: {e}")
            return NetworkStatus.OFFLINE, response_time
    
    async def update_network_status(self):
        """Update current network status"""
        status, response_time = await self.ping_telegram_api()
        
        # Log status change
        if status != self.current_network_status:
            status_change = f"{self.current_network_status.value} → {status.value}"
            logger.info(f"🌐 Network status changed: {status_change} ({response_time:.2f}s)")
            self.log_network_status(status, response_time, f"Status changed from {self.current_network_status.value}")
        
        self.current_network_status = status
        self.last_ping_time = time.time()
        
        # Update consecutive failures counter
        if status == NetworkStatus.OFFLINE:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
        
        return status
    
    def get_failed_uploads_from_history(self) -> List[Dict]:
        """Baca failed uploads dari history.json"""
        try:
            if not os.path.exists(self.history_json_file):
                logger.warning(f"History file {self.history_json_file} not found")
                return []
            
            with open(self.history_json_file, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
            
            # Filter entries dengan upload_status FAILED dan file masih ada
            failed_uploads = []
            for entry in history_data:
                if (entry.get('upload_status') == 'FAILED' 
                    and entry.get('download_status') == 'SUCCESS'
                    and entry.get('retry_count', 0) < self.max_retries):
                    
                    file_path = entry.get('file_path', '')
                    if os.path.exists(file_path):
                        failed_uploads.append(entry)
                    else:
                        logger.warning(f"File not found for retry: {file_path}")
            
            return failed_uploads
            
        except Exception as e:
            logger.error(f"Error reading failed uploads from history: {e}")
            return []
    
    def update_upload_status_in_history(self, download_id: str, new_status: str):
        """Update upload status di history.json - FIXED VERSION"""
        try:
            if not os.path.exists(self.history_json_file):
                return
            
            # Baca history
            with open(self.history_json_file, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
            
            # Find dan update entry - FIXED: check both 'download_id' and 'id'
            updated = False
            for entry in history_data:
                entry_id = entry.get('download_id') or entry.get('id')
                if entry_id == download_id:
                    entry['upload_status'] = new_status
                    entry['upload_updated'] = datetime.now().isoformat()
                    
                    if new_status == "SUCCESS":
                        entry['last_retry'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    elif new_status == "FAILED":
                        entry['retry_count'] = entry.get('retry_count', 0) + 1
                    updated = True
                    break
            
            if updated:
                # Save updated history
                with open(self.history_json_file, 'w', encoding='utf-8') as f:
                    json.dump(history_data, f, indent=2, ensure_ascii=False)
                
                logger.info(f"📝 Updated history: {download_id} → {new_status}")
            else:
                logger.warning(f"Download ID not found in history: {download_id}")
                
        except Exception as e:
            logger.error(f"Error updating history: {e}")
    
    async def send_file_telegram(self, user_id: int, file_path: str, file_type: str, caption: str = "") -> bool:
        """Kirim file ke Telegram"""
        try:
            if not self.session:
                timeout = aiohttp.ClientTimeout(total=120, connect=30)
                self.session = aiohttp.ClientSession(timeout=timeout)
            
            # Tentukan endpoint berdasarkan file type
            if file_type.lower() == 'mp3':
                endpoint = '/sendAudio'
                field_name = 'audio'
            else:  # mp4 or other video
                endpoint = '/sendVideo' 
                field_name = 'video'
            
            filename = os.path.basename(file_path)
            
            with open(file_path, 'rb') as file:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(user_id))
                data.add_field(field_name, file, filename=filename)
                
                if caption:
                    data.add_field('caption', caption)
                
                async with self.session.post(f"{self.base_url}{endpoint}", data=data) as response:
                    result = await response.json()
                    success = result.get('ok', False)
                    
                    if not success:
                        error_msg = result.get('description', 'Unknown error')
                        logger.error(f"Telegram API error: {error_msg}")
                    
                    return success
                    
        except asyncio.TimeoutError:
            logger.error(f"Timeout sending file: {file_path}")
            return False
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            return False
    
    async def retry_failed_uploads(self) -> Dict[str, int]:
        """Retry semua failed uploads saat network bagus - FIXED VERSION"""
        if self.current_network_status != NetworkStatus.GOOD:
            logger.info(f"Network not good for retries: {self.current_network_status.value}")
            return {'attempted': 0, 'successful': 0, 'failed': 0}
        
        failed_uploads = self.get_failed_uploads_from_history()
        if not failed_uploads:
            logger.info("No failed uploads to retry")
            return {'attempted': 0, 'successful': 0, 'failed': 0}
        
        logger.info(f"🔄 Starting retry of {len(failed_uploads)} failed uploads")
        
        attempted = 0
        successful = 0
        failed = 0
        
        for upload in failed_uploads:
            try:
                if upload.get('retry_count', 0) >= self.max_retries:
                    logger.info(f"Max retries reached for entry, skipping")
                    continue
                
                attempted += 1
                
                # FIXED: Handle both 'download_id' and 'id' keys
                download_id = upload.get('download_id') or upload.get('id')
                if not download_id:
                    logger.warning(f"Entry missing download_id: {upload}")
                    failed += 1
                    continue
                
                file_path = upload.get('file_path', '')
                user_id = upload.get('user_id')
                file_type = upload.get('type', 'UNKNOWN')
                username = upload.get('username', 'Unknown')
                file_size_mb = upload.get('file_size_mb', 0)
                
                # Validate required fields
                if not all([file_path, user_id, file_type]):
                    logger.warning(f"Missing required fields in upload entry: {download_id}")
                    failed += 1
                    continue
                
                logger.info(f"🔄 Retrying upload: {download_id} - {os.path.basename(file_path)}")
                
                # Buat caption
                caption = f"🔄 Retry upload - {file_type} ({file_size_mb:.1f}MB)\n👤 Requested by: {username}"
                
                # Coba kirim file
                success = await self.send_file_telegram(user_id, file_path, file_type, caption)
                
                if success:
                    successful += 1
                    self.update_upload_status_in_history(download_id, "SUCCESS")
                    logger.info(f"✅ Retry successful: {download_id}")
                    
                    # Hapus video file setelah berhasil dikirim (keep audio)
                    if file_type.upper() == 'MP4':
                        try:
                            os.remove(file_path)
                            logger.info(f"🗑️ Cleaned up video file: {file_path}")
                        except Exception as cleanup_error:
                            logger.warning(f"Failed to cleanup file {file_path}: {cleanup_error}")
                    
                    # Small delay antar uploads
                    await asyncio.sleep(2)
                else:
                    failed += 1
                    self.update_upload_status_in_history(download_id, "FAILED")
                    logger.warning(f"❌ Retry failed: {download_id}")
                    
            except Exception as retry_error:
                logger.error(f"Error during retry: {retry_error}")
                failed += 1
        
        stats = {'attempted': attempted, 'successful': successful, 'failed': failed}
        if attempted > 0:
            logger.info(f"🔄 Retry complete: {successful}/{attempted} successful")
        
        return stats
    
    async def start_monitoring(self):
        """Start background monitoring dan retry system"""
        if self.is_monitoring:
            logger.warning("Monitoring already running")
            return
        
        self.is_monitoring = True
        logger.info("🚀 Starting network monitoring and retry system")
        
        try:
            while self.is_monitoring:
                # Update network status
                await self.update_network_status()
                
                # Jika network bagus, coba retry failed uploads
                if self.current_network_status == NetworkStatus.GOOD:
                    stats = await self.retry_failed_uploads()
                    
                    # Log retry stats jika ada activity
                    if stats['attempted'] > 0:
                        self.log_network_status(
                            NetworkStatus.GOOD, 
                            0, 
                            f"Retry stats: {stats['successful']}/{stats['attempted']} successful"
                        )
                
                # Cleanup old network logs (keep last 1000 lines)
                if time.time() % 3600 < 60:  # Once per hour
                    await self.cleanup_old_logs()
                
                # Wait sebelum check berikutnya
                await asyncio.sleep(self.ping_interval)
                
        except asyncio.CancelledError:
            logger.info("🛑 Network monitoring stopped")
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
        finally:
            self.is_monitoring = False
            if self.session:
                await self.session.close()
    
    async def cleanup_old_logs(self):
        """Cleanup old network logs"""
        try:
            if not os.path.exists(self.network_log_file):
                return
            
            with open(self.network_log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Keep last 1000 lines
            if len(lines) > 1000:
                # Keep header + last 1000 lines
                header = [line for line in lines if line.startswith('#')]
                data_lines = [line for line in lines if not line.startswith('#')]
                
                with open(self.network_log_file, 'w', encoding='utf-8') as f:
                    f.writelines(header)
                    f.writelines(data_lines[-1000:])
                
                logger.info(f"🧹 Cleaned up network logs: kept last 1000 entries")
        
        except Exception as e:
            logger.error(f"Error cleaning up logs: {e}")
    
    def stop_monitoring(self):
        """Stop background monitoring"""
        self.is_monitoring = False
        logger.info("🛑 Network monitoring stop requested")
    
    def get_retry_stats(self) -> Dict:
        """Dapatkan statistik retry queue"""
        try:
            failed_uploads = self.get_failed_uploads_from_history()
            
            stats = {
                'total_failed': len(failed_uploads),
                'network_status': self.current_network_status.value,
                'consecutive_failures': self.consecutive_failures,
                'by_type': {'MP3': 0, 'MP4': 0},
                'by_retry_count': {}
            }
            
            for upload in failed_uploads:
                # Count by type
                upload_type = upload.get('type', 'UNKNOWN')
                if upload_type in stats['by_type']:
                    stats['by_type'][upload_type] += 1
                
                # Count by retry count
                retry_count = upload.get('retry_count', 0)
                if retry_count not in stats['by_retry_count']:
                    stats['by_retry_count'][retry_count] = 0
                stats['by_retry_count'][retry_count] += 1
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting retry stats: {e}")
            return {'error': str(e)}
    
    def get_network_history(self, limit: int = 50) -> List[str]:
        """Dapatkan network history terakhir"""
        try:
            if not os.path.exists(self.network_log_file):
                return []
            
            with open(self.network_log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Filter out comments and empty lines
            log_lines = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
            
            # Return last N entries
            return log_lines[-limit:] if log_lines else []
            
        except Exception as e:
            logger.error(f"Error reading network history: {e}")
            return []

# Global retry manager instance
retry_manager = None

def init_retry_manager(bot_token: str) -> RetryManager:
    """Initialize global retry manager"""
    global retry_manager
    retry_manager = RetryManager(bot_token)
    logger.info("🔄 Retry manager initialized")
    return retry_manager

async def start_background_monitoring():
    """Start background monitoring"""
    global retry_manager
    if retry_manager:
        await retry_manager.start_monitoring()
    else:
        logger.error("Retry manager not initialized!")

def stop_background_monitoring():
    """Stop background monitoring"""
    global retry_manager
    if retry_manager:
        retry_manager.stop_monitoring()

def get_network_status() -> str:
    """Dapatkan current network status"""
    global retry_manager
    if retry_manager:
        return retry_manager.current_network_status.value
    return "unknown"

def get_retry_statistics() -> Dict:
    """Dapatkan retry queue statistics"""
    global retry_manager
    if retry_manager:
        return retry_manager.get_retry_stats()
    return {}

def get_network_history_log(limit: int = 20) -> List[str]:
    """Dapatkan network history log"""
    global retry_manager
    if retry_manager:
        return retry_manager.get_network_history(limit)
    return []

# Convenience functions untuk integration dengan menu_utama.py
async def send_audio_with_retry(user_id: int, file_path: str, caption: str = "") -> bool:
    """Send audio dengan auto retry jika gagal"""
    global retry_manager
    if not retry_manager:
        logger.error("Retry manager not initialized")
        return False
    
    # Coba kirim langsung dulu
    success = await retry_manager.send_file_telegram(user_id, file_path, 'MP3', caption)
    
    if not success:
        logger.warning(f"Direct upload failed for {file_path}, will retry later")
    
    return success

async def send_video_with_retry(user_id: int, file_path: str, caption: str = "") -> bool:
    """Send video dengan auto retry jika gagal"""
    global retry_manager
    if not retry_manager:
        logger.error("Retry manager not initialized")
        return False
    
    # Coba kirim langsung dulu
    success = await retry_manager.send_file_telegram(user_id, file_path, 'MP4', caption)
    
    if not success:
        logger.warning(f"Direct upload failed for {file_path}, will retry later")
    
    return success