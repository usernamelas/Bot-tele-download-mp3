import os
import asyncio
import subprocess
import logging
import math
from typing import List, Tuple, Optional, Callable
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# Setup logging
logger = logging.getLogger(__name__)

class VideoSplitter:
    def __init__(self):
        self.max_chunk_size_mb = 45  # 45MB per chunk (buffer untuk metadata)
        self.temp_dir = "temp_splits"
        
        # Create temp directory
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            logger.info(f"📁 Created temp directory: {self.temp_dir}")
    
    def get_file_size_mb(self, file_path: str) -> float:
        """Dapatkan ukuran file dalam MB"""
        try:
            size_bytes = os.path.getsize(file_path)
            return size_bytes / (1024 * 1024)
        except:
            return 0.0
    
    def get_video_duration(self, file_path: str) -> float:
        """Dapatkan durasi video dalam detik menggunakan ffprobe"""
        try:
            cmd = [
                'ffprobe', 
                '-v', 'quiet',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return float(result.stdout.strip())
            return 0.0
            
        except Exception as e:
            logger.error(f"Error getting video duration: {e}")
            return 0.0
    
    def calculate_split_parts(self, file_path: str) -> Tuple[int, float]:
        """Hitung jumlah parts dan durasi per part"""
        file_size_mb = self.get_file_size_mb(file_path)
        duration_seconds = self.get_video_duration(file_path)
        
        if file_size_mb <= self.max_chunk_size_mb:
            return 1, duration_seconds
        
        # Hitung jumlah parts yang dibutuhkan
        num_parts = math.ceil(file_size_mb / self.max_chunk_size_mb)
        duration_per_part = duration_seconds / num_parts
        
        return num_parts, duration_per_part
    
    def create_progress_bar(self, percentage: float, length: int = 10) -> str:
        """Buat ASCII progress bar"""
        if percentage > 100:
            percentage = 100
        elif percentage < 0:
            percentage = 0
            
        filled = int(percentage / 100 * length)
        bar = '▓' * filled + '░' * (length - filled)
        return f"[{bar}] {percentage:.1f}%"
    
    async def compress_video_if_needed(self, input_path: str, target_size_mb: float = 45,
                                     progress_callback: Optional[Callable] = None) -> Optional[str]:
        """Compress video jika ukurannya masih terlalu besar"""
        try:
            current_size = self.get_file_size_mb(input_path)
            
            if current_size <= target_size_mb:
                return input_path  # Gak perlu compress
            
            if progress_callback:
                await progress_callback(f"🗜️ <b>Compressing video...</b>\n\n{self.create_progress_bar(10)}")
            
            # Output path untuk compressed video
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(self.temp_dir, f"{base_name}_compressed.mp4")
            
            # Hitung compression ratio yang dibutuhkan
            compression_ratio = target_size_mb / current_size
            
            # Adjust video bitrate berdasarkan compression ratio
            if compression_ratio < 0.3:
                # Compression sangat tinggi
                video_bitrate = "500k"
                audio_bitrate = "64k"
                crf = "28"
            elif compression_ratio < 0.6:
                # Compression sedang
                video_bitrate = "800k"
                audio_bitrate = "96k" 
                crf = "25"
            else:
                # Compression ringan
                video_bitrate = "1200k"
                audio_bitrate = "128k"
                crf = "23"
            
            logger.info(f"Compressing video: {current_size:.1f}MB → ~{target_size_mb}MB (ratio: {compression_ratio:.2f})")
            
            # FFmpeg command untuk compression
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-c:v', 'libx264',
                '-b:v', video_bitrate,
                '-c:a', 'aac',
                '-b:a', audio_bitrate,
                '-crf', crf,
                '-preset', 'fast',
                '-movflags', '+faststart',  # Optimize untuk streaming
                '-y',
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            if progress_callback:
                await progress_callback(f"🗜️ <b>Compressing video...</b>\n\n{self.create_progress_bar(50)}")
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0 and os.path.exists(output_path):
                compressed_size = self.get_file_size_mb(output_path)
                logger.info(f"✅ Compression successful: {current_size:.1f}MB → {compressed_size:.1f}MB")
                
                if progress_callback:
                    await progress_callback(f"✅ <b>Compression complete!</b>\n\n{self.create_progress_bar(100)}")
                
                return output_path
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Compression failed: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"Error compressing video: {e}")
            return None
    
    async def split_video(self, input_path: str, progress_callback: Optional[Callable] = None) -> List[str]:
        """Split video menjadi multiple parts"""
        try:
            # Hitung split parameters
            num_parts, duration_per_part = self.calculate_split_parts(input_path)
            
            if num_parts == 1:
                logger.info("File tidak perlu di-split")
                return [input_path]
            
            logger.info(f"Splitting video into {num_parts} parts ({duration_per_part:.1f}s each)")
            
            if progress_callback:
                await progress_callback(f"✂️ <b>Splitting video...</b>\n\n📹 {num_parts} parts\n{self.create_progress_bar(5)}")
            
            # Generate output filenames
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_paths = []
            
            for i in range(num_parts):
                start_time = i * duration_per_part
                output_filename = f"{base_name}_part{i+1:02d}.mp4"
                output_path = os.path.join(self.temp_dir, output_filename)
                output_paths.append(output_path)
                
                # FFmpeg command untuk split
                cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    '-ss', str(start_time),
                    '-t', str(duration_per_part),
                    '-c', 'copy',  # Copy streams tanpa re-encoding
                    '-avoid_negative_ts', 'make_zero',
                    '-y',
                    output_path
                ]
                
                logger.info(f"Creating part {i+1}/{num_parts}: {start_time:.1f}s - {start_time + duration_per_part:.1f}s")
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                # Update progress
                if progress_callback:
                    progress_percent = 5 + (i * 80 / num_parts)  # 5% to 85%
                    await progress_callback(f"✂️ <b>Creating part {i+1}/{num_parts}...</b>\n\n{self.create_progress_bar(progress_percent)}")
                
                stdout, stderr = await process.communicate()
                
                if process.returncode != 0:
                    error_msg = stderr.decode() if stderr else "Unknown error"
                    logger.error(f"Failed to create part {i+1}: {error_msg}")
                    return []
                
                # Verify part was created and has reasonable size
                if not os.path.exists(output_path):
                    logger.error(f"Part {i+1} was not created")
                    return []
                
                part_size = self.get_file_size_mb(output_path)
                logger.info(f"✅ Part {i+1}/{num_parts} created: {part_size:.1f}MB")
            
            if progress_callback:
                await progress_callback(f"✅ <b>Video split complete!</b>\n\n📹 {num_parts} parts ready\n{self.create_progress_bar(85)}")
            
            return output_paths
            
        except Exception as e:
            logger.error(f"Error splitting video: {e}")
            return []
    
    async def send_split_parts(self, part_paths: List[str], user_id: int, original_filename: str,
                              send_function: Callable, progress_callback: Optional[Callable] = None) -> Tuple[int, int]:
        """Kirim split parts secara berurutan"""
        if not part_paths:
            return 0, 0
        
        successful_uploads = 0
        failed_uploads = 0
        total_parts = len(part_paths)
        
        logger.info(f"📤 Sending {total_parts} parts to user {user_id}")
        
        for i, part_path in enumerate(part_paths):
            part_num = i + 1
            part_filename = os.path.basename(part_path)
            part_size = self.get_file_size_mb(part_path)
            
            # Buat caption untuk part
            caption = (
                f"🎬 <b>Part {part_num}/{total_parts}</b>\n"
                f"📁 {original_filename}\n"
                f"📊 Size: {part_size:.1f}MB"
            )
            
            # Update progress
            if progress_callback:
                upload_progress = 85 + (i * 15 / total_parts)  # 85% to 100%
                await progress_callback(
                    f"📤 <b>Sending part {part_num}/{total_parts}...</b>\n\n"
                    f"📁 {part_filename}\n"
                    f"{self.create_progress_bar(upload_progress)}"
                )
            
            logger.info(f"📤 Sending part {part_num}/{total_parts}: {part_filename} ({part_size:.1f}MB)")
            
            try:
                # Kirim file menggunakan function yang diberikan
                success = await send_function(user_id, part_path, caption)
                
                if success:
                    successful_uploads += 1
                    logger.info(f"✅ Part {part_num}/{total_parts} sent successfully")
                    
                    # Hapus part file setelah berhasil dikirim
                    try:
                        os.remove(part_path)
                        logger.info(f"🗑️ Cleaned up part: {part_filename}")
                    except:
                        pass
                    
                    # Small delay antar parts
                    if part_num < total_parts:
                        await asyncio.sleep(2)
                else:
                    failed_uploads += 1
                    logger.warning(f"❌ Failed to send part {part_num}/{total_parts}")
                    
            except Exception as e:
                failed_uploads += 1
                logger.error(f"Error sending part {part_num}: {e}")
        
        # Final progress update
        if progress_callback:
            if failed_uploads == 0:
                await progress_callback(f"✅ <b>All parts sent successfully!</b>\n\n📤 {successful_uploads}/{total_parts} parts delivered")
            else:
                await progress_callback(f"⚠️ <b>Upload completed with issues</b>\n\n📤 {successful_uploads}/{total_parts} parts delivered\n❌ {failed_uploads} parts failed")
        
        logger.info(f"📊 Upload summary: {successful_uploads}/{total_parts} successful, {failed_uploads} failed")
        return successful_uploads, failed_uploads
    
    def cleanup_temp_files(self, keep_recent_hours: int = 2):
        """Cleanup temporary split files"""
        try:
            if not os.path.exists(self.temp_dir):
                return
            
            import time
            current_time = time.time()
            cleanup_count = 0
            
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getmtime(file_path)
                    
                    # Hapus file yang lebih tua dari keep_recent_hours
                    if file_age > keep_recent_hours * 3600:
                        try:
                            os.remove(file_path)
                            cleanup_count += 1
                            logger.info(f"🗑️ Cleaned up temp file: {filename}")
                        except:
                            pass
            
            if cleanup_count > 0:
                logger.info(f"🧹 Cleaned up {cleanup_count} temporary files")
                
        except Exception as e:
            logger.error(f"Error cleaning temp files: {e}")
    
    async def process_large_video(self, input_path: str, user_id: int, send_function: Callable,
                                progress_callback: Optional[Callable] = None) -> Tuple[bool, str, int, int]:
        """Process video yang lebih besar dari 50MB dengan compression dan splitting"""
        try:
            original_filename = os.path.basename(input_path)
            original_size = self.get_file_size_mb(input_path)
            
            logger.info(f"🎬 Processing large video: {original_filename} ({original_size:.1f}MB)")
            
            if progress_callback:
                await progress_callback(f"🎬 <b>Processing large video...</b>\n\n📁 {original_filename}\n📊 {original_size:.1f}MB\n\n{self.create_progress_bar(5)}")
            
            # Step 1: Coba compress dulu
            compressed_path = await self.compress_video_if_needed(input_path, self.max_chunk_size_mb, progress_callback)
            
            if compressed_path and compressed_path != input_path:
                # Compression berhasil, cek apakah masih perlu di-split
                compressed_size = self.get_file_size_mb(compressed_path)
                
                if compressed_size <= self.max_chunk_size_mb:
                    # Compression cukup, gak perlu split
                    logger.info(f"✅ Compression successful, no splitting needed: {compressed_size:.1f}MB")
                    
                    if progress_callback:
                        await progress_callback(f"📤 <b>Sending compressed video...</b>\n\n{self.create_progress_bar(90)}")
                    
                    # Kirim compressed video
                    caption = f"🎬 {original_filename} (compressed)\n📊 {compressed_size:.1f}MB"
                    success = await send_function(user_id, compressed_path, caption)
                    
                    # Cleanup
                    try:
                        os.remove(compressed_path)
                    except:
                        pass
                    
                    if success:
                        return True, f"Video berhasil dikirim setelah compression ({compressed_size:.1f}MB)", 1, 0
                    else:
                        return False, "Gagal mengirim compressed video", 0, 1
                else:
                    # Masih terlalu besar, perlu split
                    working_path = compressed_path
            else:
                # Compression gagal atau tidak diperlukan, split original file
                working_path = input_path
            
            # Step 2: Split video
            if progress_callback:
                await progress_callback(f"✂️ <b>Splitting video...</b>\n\nFile terlalu besar, memecah jadi beberapa bagian...\n{self.create_progress_bar(20)}")
            
            part_paths = await self.split_video(working_path, progress_callback)
            
            if not part_paths:
                return False, "Gagal split video", 0, 0
            
            # Step 3: Kirim semua parts
            successful, failed = await self.send_split_parts(
                part_paths, user_id, original_filename, send_function, progress_callback
            )
            
            # Step 4: Cleanup
            if working_path != input_path:  # Hapus compressed file jika ada
                try:
                    os.remove(working_path)
                except:
                    pass
            
            # Cleanup any remaining part files
            for part_path in part_paths:
                try:
                    if os.path.exists(part_path):
                        os.remove(part_path)
                except:
                    pass
            
            total_parts = len(part_paths)
            
            if successful == total_parts:
                return True, f"Video berhasil dikirim dalam {total_parts} bagian", successful, failed
            elif successful > 0:
                return True, f"Sebagian video berhasil dikirim ({successful}/{total_parts} bagian)", successful, failed
            else:
                return False, f"Gagal mengirim semua bagian video ({total_parts} bagian)", successful, failed
                
        except Exception as e:
            logger.error(f"Error processing large video: {e}")
            return False, f"Error processing video: {str(e)}", 0, 0

# Global video splitter instance
video_splitter = None

def init_video_splitter() -> VideoSplitter:
    """Initialize global video splitter"""
    global video_splitter
    video_splitter = VideoSplitter()
    logger.info("✂️ Video splitter initialized")
    return video_splitter

async def process_large_video_file(input_path: str, user_id: int, send_function: Callable,
                                 progress_callback: Optional[Callable] = None) -> Tuple[bool, str, int, int]:
    """Process large video file dengan splitting"""
    global video_splitter
    if not video_splitter:
        video_splitter = init_video_splitter()
    
    return await video_splitter.process_large_video(input_path, user_id, send_function, progress_callback)

def cleanup_temp_split_files():
    """Cleanup temporary split files"""
    global video_splitter
    if video_splitter:
        video_splitter.cleanup_temp_files()

def get_file_split_info(file_path: str) -> Dict:
    """Get informasi tentang berapa parts yang dibutuhkan untuk split file"""
    global video_splitter
    if not video_splitter:
        video_splitter = init_video_splitter()
    
    try:
        file_size_mb = video_splitter.get_file_size_mb(file_path)
        duration_seconds = video_splitter.get_video_duration(file_path)
        num_parts, duration_per_part = video_splitter.calculate_split_parts(file_path)
        
        return {
            'file_size_mb': file_size_mb,
            'duration_seconds': duration_seconds,
            'num_parts': num_parts,
            'duration_per_part': duration_per_part,
            'needs_splitting': num_parts > 1
        }
    except Exception as e:
        return {'error': str(e)}

# Convenience functions untuk integration dengan menu_utama.py
async def handle_large_video(file_path: str, user_id: int, send_video_function: Callable,
                           progress_callback: Optional[Callable] = None) -> Tuple[bool, str]:
    """Handle large video dengan compression dan splitting"""
    
    # Wrapper function untuk send_video agar compatible dengan splitter
    async def send_wrapper(user_id: int, video_path: str, caption: str = "") -> bool:
        return await send_video_function(user_id, video_path, caption)
    
    success, message, successful_parts, failed_parts = await process_large_video_file(
        file_path, user_id, send_wrapper, progress_callback
    )
    
    return success, message

def needs_video_splitting(file_path: str) -> bool:
    """Check apakah video perlu di-split"""
    info = get_file_split_info(file_path)
    return info.get('needs_splitting', False)