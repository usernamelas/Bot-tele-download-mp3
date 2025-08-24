# progress_manager.py - Real-time single message progress system

import asyncio
import time
import re
import aiohttp
import logging
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class RealTimeProgressManager:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        self.active_progress = {}  # {user_id: progress_data}
        self.update_lock = {}  # Prevent concurrent updates
        
    async def start_progress(self, chat_id: int, user_id: int, title: str = "📥 Processing") -> Optional[int]:
        """Start progress tracking dengan pesan pertama"""
        try:
            initial_text = self._format_progress_message(title, 0, "Initializing...")
            
            async with aiohttp.ClientSession() as session:
                data = {
                    'chat_id': chat_id,
                    'text': initial_text,
                    'parse_mode': 'HTML'
                }
                
                async with session.post(f"{self.api_url}/sendMessage", data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        message_id = result['result']['message_id']
                        
                        # Store progress info
                        self.active_progress[user_id] = {
                            'chat_id': chat_id,
                            'message_id': message_id,
                            'title': title,
                            'last_percentage': 0,
                            'last_status': "Initializing...",
                            'last_update': time.time(),
                            'speed': None,
                            'eta': None
                        }
                        
                        self.update_lock[user_id] = asyncio.Lock()
                        
                        logger.info(f"✅ Progress started for user {user_id}")
                        return message_id
                        
        except Exception as e:
            logger.error(f"❌ Error starting progress: {e}")
            return None
    
    def _create_progress_bar(self, percentage: float, width: int = 20) -> str:
        """Create beautiful ASCII progress bar"""
        if percentage > 100:
            percentage = 100
        elif percentage < 0:
            percentage = 0
            
        filled = int((percentage / 100) * width)
        bar = '▓' * filled + '░' * (width - filled)
        return f"[{bar}] {percentage:.1f}%"
    
    def _format_progress_message(self, title: str, percentage: float, status: str, 
                                speed: str = None, eta: str = None) -> str:
        """Format progress message dengan emoji dan info lengkap"""
        
        # Progress bar
        progress_bar = self._create_progress_bar(percentage)
        
        # Status emoji berdasarkan percentage
        if percentage < 25:
            emoji = "🔄"
        elif percentage < 50:
            emoji = "📥"
        elif percentage < 75:
            emoji = "⚡"
        elif percentage < 100:
            emoji = "🎯"
        else:
            emoji = "✅"
        
        # Base message
        message = f"<b>{emoji} {title}</b>\n\n"
        message += f"<b>{status}</b>\n"
        message += f"<code>{progress_bar}</code>\n\n"
        
        # Additional info
        info_parts = []
        if speed:
            info_parts.append(f"⚡ Speed: <b>{speed}</b>")
        if eta:
            info_parts.append(f"⏱️ ETA: <b>{eta}</b>")
        
        if info_parts:
            message += " | ".join(info_parts)
        else:
            message += f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        
        return message
    
    async def update_progress(self, user_id: int, percentage: float, status: str,
                            speed: str = None, eta: str = None, force_update: bool = False) -> bool:
        """Update progress message (throttled untuk avoid spam)"""
        
        if user_id not in self.active_progress:
            return False
        
        # Get update lock to prevent concurrent updates
        if user_id not in self.update_lock:
            return False
            
        async with self.update_lock[user_id]:
            progress_data = self.active_progress[user_id]
            current_time = time.time()
            
            # Throttling logic - update only if:
            # 1. Force update
            # 2. Percentage jumped significantly (>3%)
            # 3. Enough time passed (>1.5 seconds)
            # 4. Status changed
            # 5. Reached 100%
            time_diff = current_time - progress_data['last_update']
            percentage_diff = abs(percentage - progress_data['last_percentage'])
            status_changed = status != progress_data['last_status']
            
            should_update = (
                force_update or
                percentage_diff >= 3.0 or
                time_diff >= 1.5 or
                status_changed or
                percentage >= 100
            )
            
            if not should_update:
                return True
            
            try:
                # Format new message
                new_text = self._format_progress_message(
                    progress_data['title'], percentage, status, speed, eta
                )
                
                # Update message via Telegram API
                async with aiohttp.ClientSession() as session:
                    data = {
                        'chat_id': progress_data['chat_id'],
                        'message_id': progress_data['message_id'],
                        'text': new_text,
                        'parse_mode': 'HTML'
                    }
                    
                    async with session.post(f"{self.api_url}/editMessageText", data=data) as response:
                        if response.status == 200:
                            # Update progress data
                            progress_data['last_percentage'] = percentage
                            progress_data['last_status'] = status
                            progress_data['last_update'] = current_time
                            progress_data['speed'] = speed
                            progress_data['eta'] = eta
                            
                            return True
                        else:
                            # If edit fails, log but don't crash
                            error_data = await response.text()
                            logger.warning(f"Failed to update progress: {error_data}")
                            return False
                            
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
                return False
    
    async def finish_progress(self, user_id: int, success: bool = True, 
                            final_message: str = None) -> bool:
        """Finish progress dengan final message"""
        
        if user_id not in self.active_progress:
            return False
        
        try:
            if success:
                final_status = final_message or "Complete!"
                emoji = "✅"
                percentage = 100
            else:
                final_status = final_message or "Failed!"
                emoji = "❌"
                percentage = self.active_progress[user_id]['last_percentage']
            
            # Final update
            await self.update_progress(
                user_id, percentage, final_status, force_update=True
            )
            
            # Wait a bit then cleanup
            await asyncio.sleep(0.5)
            
            # Cleanup progress data
            if user_id in self.active_progress:
                del self.active_progress[user_id]
            if user_id in self.update_lock:
                del self.update_lock[user_id]
                
            logger.info(f"✅ Progress finished for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error finishing progress: {e}")
            return False
    
    def is_active(self, user_id: int) -> bool:
        """Check if progress is active for user"""
        return user_id in self.active_progress
    
    async def cancel_progress(self, user_id: int) -> bool:
        """Cancel progress untuk user"""
        if user_id in self.active_progress:
            await self.finish_progress(user_id, success=False, final_message="Cancelled")
            return True
        return False
    
    def get_progress_callback(self, user_id: int):
        """Get callback function untuk download.py"""
        async def progress_callback(progress_data: str):
            """Parse clean data dari download.py format: status|percentage|speed|eta"""
            try:
                # Parse format: "status|percentage|speed|eta"
                parts = progress_data.split('|')
                if len(parts) >= 2:
                    status = parts[0].strip()
                    percentage = float(parts[1].strip()) if parts[1].strip() else 0
                    speed = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                    eta = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
                    
                    # Update progress
                    await self.update_progress(user_id, percentage, status, speed, eta)
                else:
                    # Fallback parsing untuk backward compatibility
                    percentage_match = re.search(r'(\d+(?:\.\d+)?)%', progress_data)
                    percentage = float(percentage_match.group(1)) if percentage_match else 0
                    
                    status_match = re.search(r'<b>([^<]+)</b>', progress_data)
                    status = status_match.group(1) if status_match else "Processing..."
                    
                    await self.update_progress(user_id, percentage, status)
                
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")
                # Fallback
                if self.is_active(user_id):
                    await self.update_progress(user_id, 0, "Processing...", force_update=True)
        
        return progress_callback

# Global instance untuk easy access
_progress_manager = None

def init_progress_manager(bot_token: str) -> RealTimeProgressManager:
    """Initialize global progress manager"""
    global _progress_manager
    _progress_manager = RealTimeProgressManager(bot_token)
    logger.info("✅ Real-time progress manager initialized")
    return _progress_manager

def get_progress_manager() -> Optional[RealTimeProgressManager]:
    """Get global progress manager instance"""
    return _progress_manager

# Convenience functions untuk integration
async def start_download_progress(chat_id: int, user_id: int, download_type: str) -> Optional[int]:
    """Start progress untuk download"""
    if _progress_manager:
        title = f"📥 Downloading {download_type.upper()}"
        return await _progress_manager.start_progress(chat_id, user_id, title)
    return None

def get_download_progress_callback(user_id: int):
    """Get progress callback untuk download functions"""
    if _progress_manager:
        return _progress_manager.get_progress_callback(user_id)
    return None

async def finish_download_progress(user_id: int, success: bool, message: str = None):
    """Finish download progress"""
    if _progress_manager:
        await _progress_manager.finish_progress(user_id, success, message)