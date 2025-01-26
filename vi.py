import sys
import os
import hashlib
import json
import time
import logging
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re
import tempfile
import shutil

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                          QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                          QProgressBar, QTextEdit, QFileDialog, QMessageBox,
                          QCheckBox, QTabWidget, QDialog, QSpinBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

# Instagram API
from instaloader import Instaloader, Profile, Post, LoginRequiredException, TooManyRequestsException

# Logging configuration
logging.basicConfig(
    filename='social_downloader.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class LoginDialog(QDialog):
    def __init__(self, platform: str, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle(f'{self.platform} Login')
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)

        # Login form
        title = QLabel(f"Login to {self.platform}")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self.username = QLineEdit(self)
        self.username.setPlaceholderText('Username')
        layout.addWidget(self.username)

        self.password = QLineEdit(self)
        self.password.setPlaceholderText('Password')
        self.password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.password)

        # Buttons
        btn_layout = QHBoxLayout()
        self.login_btn = QPushButton('Login', self)
        self.login_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton('Cancel', self)
        self.cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.login_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        # Remember me
        self.remember_me = QCheckBox('Remember me', self)
        layout.addWidget(self.remember_me)

class RetryableSession:
    def __init__(self, max_retries=3, base_delay=1):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.current_retry = 0
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        self.session.headers.update(self.headers)
    
    def reset(self):
        self.current_retry = 0
    
    def should_retry(self, exception) -> bool:
        if self.current_retry >= self.max_retries:
            return False
        
        retryable_exceptions = (
            requests.exceptions.RequestException,
            ConnectionError,
            TimeoutError
        )
        
        if isinstance(exception, retryable_exceptions):
            self.current_retry += 1
            time.sleep(self.base_delay * (2 ** (self.current_retry - 1)))
            return True
        
        return False

    def get(self, url, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url, **kwargs):
        return self.session.post(url, **kwargs)

class InstagramDownloader(QThread):
    progress = pyqtSignal(str)
    download_progress = pyqtSignal(int)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    login_required = pyqtSignal()

    def __init__(self, keyword: str, download_path: str, download_videos=True, 
                 download_photos=True, max_items=50):
        super().__init__()
        self.keyword = keyword
        self.download_path = download_path
        self.download_videos = download_videos
        self.download_photos = download_photos
        self.max_items = max_items
        self.is_running = True
        
        # Instaloader instance
        self.L = Instaloader(
            download_videos=download_videos,
            download_pictures=download_photos,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            filename_pattern='{date:%Y%m%d}_{shortcode}',
            quiet=True
        )

    def set_login(self, username: str, password: str) -> bool:
        try:
            session_file = f"{username}_instagram_session"
            
            # Try to load existing session
            try:
                self.L.load_session_from_file(username, session_file)
                self.progress.emit("Existing session loaded")
                
                # Verify session
                try:
                    test_profile = Profile.from_username(self.L.context, username)
                    return True
                except LoginRequiredException:
                    self.progress.emit("Session expired, logging in again...")
                    raise
                    
            except (FileNotFoundError, LoginRequiredException):
                # Create new session
                self.progress.emit("Creating new session...")
                self.L.login(username, password)
                self.L.save_session_to_file(session_file)
                return True
                
        except Exception as e:
            self.error.emit(f"Login error: {str(e)}")
            return False

    def run(self):
        try:
            # Check login status
            if not hasattr(self.L.context, 'username'):
                self.progress.emit("Login required")
                self.login_required.emit()
                return

            self.progress.emit(f"Searching for '{self.keyword}'...")
            
            try:
                posts = []
                if self.keyword.startswith('#'):
                    # Hashtag search
                    hashtag = self.keyword.lstrip('#')
                    self.progress.emit(f"Searching hashtag #{hashtag}")
                    posts = list(self.L.get_hashtag_posts(hashtag))[:self.max_items]
                else:
                    # Profile search
                    try:
                        profile = Profile.from_username(self.L.context, self.keyword)
                        self.progress.emit(f"Found profile: {profile.username}")
                        posts = list(profile.get_posts())[:self.max_items]
                    except Exception as profile_error:
                        self.error.emit(f"Profile error: {str(profile_error)}")
                        return

                total_posts = len(posts)
                if total_posts == 0:
                    self.error.emit("No posts found")
                    return

                self.progress.emit(f"Found {total_posts} posts")
                downloaded = 0
                
                # Create temp directory for downloads
                with tempfile.TemporaryDirectory() as temp_dir:
                    for i, post in enumerate(posts):
                        if not self.is_running:
                            break
                            
                        try:
                            # Download to temp directory first
                            self.L.download_post(post, temp_dir)
                            
                            # Move files to final destination
                            for filename in os.listdir(temp_dir):
                                src = os.path.join(temp_dir, filename)
                                dst = os.path.join(self.download_path, filename)
                                shutil.move(src, dst)
                            
                            downloaded += 1
                            progress = int(((i + 1) / total_posts) * 100)
                            self.download_progress.emit(progress)
                            self.progress.emit(f"Downloaded {downloaded}/{total_posts}")
                            
                            # Rate limiting
                            if i < total_posts - 1:  # Don't sleep after last item
                                time.sleep(2)
                                
                        except TooManyRequestsException:
                            self.progress.emit("Rate limit reached. Waiting 60 seconds...")
                            time.sleep(60)
                            continue
                            
                        except Exception as e:
                            self.error.emit(f"Download error: {str(e)}")
                            continue
                            
            except LoginRequiredException:
                self.progress.emit("Session expired")
                self.login_required.emit()
                return
                
            except TooManyRequestsException:
                self.error.emit("Rate limit exceeded. Please wait a few minutes.")
                return
                
            except Exception as e:
                self.error.emit(f"Error: {str(e)}")
                return
                
        finally:
            self.finished.emit()

    def stop(self):
        self.is_running = False

class TikTokDownloader(QThread):
    progress = pyqtSignal(str)
    download_progress = pyqtSignal(int)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, url_or_keyword: str, download_path: str, is_url: bool = False):
        super().__init__()
        self.url_or_keyword = url_or_keyword
        self.download_path = download_path
        self.is_url = is_url
        self.is_running = True
        self.retry_session = RetryableSession()

    def extract_video_info(self, url: str) -> Optional[Dict]:
        try:
            response = self.retry_session.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract video URL and title from meta tags
            video_url = None
            video_tags = soup.find_all('video')
            if video_tags:
                video_url = video_tags[0].get('src')
            
            if not video_url:
                video_meta = soup.find('meta', property='og:video')
                if video_meta:
                    video_url = video_meta.get('content')
            
            title = soup.find('meta', property='og:title')
            if title:
                title = title.get('content')
            
            if video_url:
                return {
                    'url': video_url,
                    'title': title or f"tiktok_{int(time.time())}"
                }
            
            return None
            
        except Exception as e:
            self.error.emit(f"Error extracting video info: {str(e)}")
            return None

    def download_video(self, video_url: str, filename: str) -> bool:
        try:
            response = self.retry_session.get(video_url, stream=True)
            response.raise_for_status()
            
            file_path = os.path.join(self.download_path, f"{filename}.mp4")
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            downloaded = 0

            with open(file_path, 'wb') as f:
                for data in response.iter_content(block_size):
                    if not self.is_running:
                        f.close()
                        os.remove(file_path)
                        return False
                    
                    downloaded += len(data)
                    f.write(data)
                    
                    if total_size:
                        progress = int((downloaded / total_size) * 100)
                        self.download_progress.emit(progress)

            return True
            
        except Exception as e:
            self.error.emit(f"Download error: {str(e)}")
            return False

    def search_videos(self, keyword: str) -> List[Dict]:
        videos = []
        try:
            search_url = f"https://www.tiktok.com/tag/{keyword}"
            response = self.retry_session.get(search_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            video_links = soup.find_all('a', href=re.compile(r'https://www.tiktok.com/@[\w\d]+/video/\d+'))
            
            for link in video_links[:10]:  # Limit to first 10 videos
                video_url = link['href']
                videos.append({'url': video_url})
            
        except Exception as e:
            self.error.emit(f"Search error: {str(e)}")
            
        return videos

    def run(self):
        try:
            if self.is_url:
                # Single video download
                self.progress.emit("Getting video information...")
                video_info = self.extract_video_info(self.url_or_keyword)
                
                if video_info and video_info['url']:
                    if self.download_video(video_info['url'], video_info['title']):
                        self.progress.emit("Video downloaded successfully")
                    else:
                        self.error.emit("Failed to download video")
                else:
                    self.error.emit("Could not extract video information")
            
            else:
                # Search and download multiple videos
                self.progress.emit(f"Searching for '{self.url_or_keyword}'...")
                videos = self.search_videos(self.url_or_keyword)
                
                if not videos:
                    self.error.emit("No videos found")
                    return
                
                self.progress.emit(f"Found {len(videos)} videos")
                
                for i, video in enumerate(videos):
                    if not self.is_running:
                        break
                    
                    try:
                        video_info = self.extract_video_info(video['url'])
                        if video_info and video_info['url']:
                            filename = f"tiktok_search_{i+1}_{int(time.time())}"
                            if self.download_video(video_info['url'], filename):
                                self.progress.emit(f"Downloaded video {i+1}")
                            else:
                                self.error.emit(f"Failed to download video {i+1}")
                        
                        progress = int(((i + 1) / len(videos)) * 100)
                        self.download_progress.emit(progress)
                        
                        # Rate limiting
                        if i < len(videos) - 1:  # Don't sleep after last video
                            time.sleep(2)
                            
                    except Exception as e:
                        self.error.emit(f"Error downloading video {i+1}: {str(e)}")
                        continue
                
        except Exception as e:
            self.error.emit(f"General error: {str(e)}")
        finally:
            self.finished.emit()

    def stop(self):
        self.is_running = False

class SocialMediaDownloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.instagram_worker = None
        self.tiktok_worker = None
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        self.setWindowTitle('Social Media Downloader')
        self.setGeometry(100, 100, 800, 600)

        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Tab widget
        self.tabs = QTabWidget()
        self.instagram_tab = QWidget()
        self.tiktok_tab = QWidget()
        
        self.tabs.addTab(self.instagram_tab, "Instagram")
        self.tabs.addTab(self.tiktok_tab, "TikTok")
        
        self.setup_instagram_tab()
        self.setup_tiktok_tab()
        
        layout.addWidget(self.tabs)

    def setup_instagram_tab(self):
        layout = QVBoxLayout(self.instagram_tab)

        # Search input
        search_layout = QHBoxLayout()
        self.insta_search = QLineEdit()
        self.insta_search.setPlaceholderText('Enter username or #hashtag')
        search_layout.addWidget(self.insta_search)
        
        # Download path button
        self.insta_path_btn = QPushButton('Select Download Folder')
        self.insta_path_btn.clicked.connect(lambda: self.select_download_path('instagram'))
        search_layout.addWidget(self.insta_path_btn)
        layout.addLayout(search_layout)

        # Options
        options_layout = QHBoxLayout()
        self.insta_video_cb = QCheckBox('Download Videos')
        self.insta_photo_cb = QCheckBox('Download Photos')
        self.insta_video_cb.setChecked(True)
        self.insta_photo_cb.setChecked(True)
        options_layout.addWidget(self.insta_video_cb)
        options_layout.addWidget(self.insta_photo_cb)
        layout.addLayout(options_layout)

        # Max items
        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel('Max Items:'))
        self.insta_max_items = QSpinBox()
        self.insta_max_items.setRange(1, 100)
        self.insta_max_items.setValue(50)
        limit_layout.addWidget(self.insta_max_items)
        layout.addLayout(limit_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        self.insta_start_btn = QPushButton('Start Download')
        self.insta_stop_btn = QPushButton('Stop Download')
        self.insta_start_btn.clicked.connect(self.start_instagram_download)
        self.insta_stop_btn.clicked.connect(self.stop_instagram_download)
        self.insta_stop_btn.setEnabled(False)
        btn_layout.addWidget(self.insta_start_btn)
        btn_layout.addWidget(self.insta_stop_btn)
        layout.addLayout(btn_layout)

        # Progress bar
        self.insta_progress = QProgressBar()
        layout.addWidget(self.insta_progress)

        # Log
        self.insta_log = QTextEdit()
        self.insta_log.setReadOnly(True)
        layout.addWidget(self.insta_log)

    def setup_tiktok_tab(self):
        layout = QVBoxLayout(self.tiktok_tab)

        # URL Mode checkbox
        mode_layout = QHBoxLayout()
        self.tiktok_url_mode = QCheckBox('URL Mode')
        mode_layout.addWidget(self.tiktok_url_mode)
        layout.addLayout(mode_layout)

        # Search input
        search_layout = QHBoxLayout()
        self.tiktok_search = QLineEdit()
        self.tiktok_search.setPlaceholderText('Enter TikTok URL or #hashtag')
        search_layout.addWidget(self.tiktok_search)
        
        # Download path button
        self.tiktok_path_btn = QPushButton('Select Download Folder')
        self.tiktok_path_btn.clicked.connect(lambda: self.select_download_path('tiktok'))
        search_layout.addWidget(self.tiktok_path_btn)
        layout.addLayout(search_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        self.tiktok_start_btn = QPushButton('Start Download')
        self.tiktok_stop_btn = QPushButton('Stop Download')
        self.tiktok_start_btn.clicked.connect(self.start_tiktok_download)
        self.tiktok_stop_btn.clicked.connect(self.stop_tiktok_download)
        self.tiktok_stop_btn.setEnabled(False)
        btn_layout.addWidget(self.tiktok_start_btn)
        btn_layout.addWidget(self.tiktok_stop_btn)
        layout.addLayout(btn_layout)

        # Progress bar
        self.tiktok_progress = QProgressBar()
        layout.addWidget(self.tiktok_progress)

        # Log
        self.tiktok_log = QTextEdit()
        self.tiktok_log.setReadOnly(True)
        layout.addWidget(self.tiktok_log)

    def load_settings(self):
        try:
            downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
            
            if os.path.exists('settings.json'):
                with open('settings.json', 'r') as f:
                    settings = json.load(f)
                    self.instagram_path = settings.get('instagram_path', os.path.join(downloads_dir, 'Instagram'))
                    self.tiktok_path = settings.get('tiktok_path', os.path.join(downloads_dir, 'TikTok'))
            else:
                self.instagram_path = os.path.join(downloads_dir, 'Instagram')
                self.tiktok_path = os.path.join(downloads_dir, 'TikTok')

            # Create directories if they don't exist
            os.makedirs(self.instagram_path, exist_ok=True)
            os.makedirs(self.tiktok_path, exist_ok=True)
            
        except Exception as e:
            logging.error(f"Settings load error: {str(e)}")
            self.show_error("Failed to load settings!")

    def save_settings(self):
        try:
            settings = {
                'instagram_path': self.instagram_path,
                'tiktok_path': self.tiktok_path
            }
            with open('settings.json', 'w') as f:
                json.dump(settings, f)
        except Exception as e:
            logging.error(f"Settings save error: {str(e)}")
            self.show_error("Failed to save settings!")

    def select_download_path(self, platform: str):
        directory = QFileDialog.getExistingDirectory(self, 'Select Download Directory')
        if directory:
            if platform == 'instagram':
                self.instagram_path = directory
            else:
                self.tiktok_path = directory
            self.save_settings()

    def show_error(self, message: str):
        QMessageBox.critical(self, 'Error', message)

    def show_info(self, message: str):
        QMessageBox.information(self, 'Info', message)

    def log_message(self, platform: str, message: str):
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_text = f"[{timestamp}] {message}"
        
        if platform == 'instagram':
            self.insta_log.append(log_text)
        else:
            self.tiktok_log.append(log_text)
            
        logging.info(f"{platform}: {message}")

    def start_instagram_download(self):
        keyword = self.insta_search.text().strip()
        if not keyword:
            self.show_error('Please enter a username or hashtag')
            return

        self.insta_start_btn.setEnabled(False)
        self.insta_stop_btn.setEnabled(True)
        self.insta_search.setEnabled(False)
        self.insta_progress.setValue(0)
        self.insta_log.clear()

        self.instagram_worker = InstagramDownloader(
            keyword=keyword,
            download_path=self.instagram_path,
            download_videos=self.insta_video_cb.isChecked(),
            download_photos=self.insta_photo_cb.isChecked(),
            max_items=self.insta_max_items.value()
        )

        self.instagram_worker.progress.connect(lambda msg: self.log_message('instagram', msg))
        self.instagram_worker.download_progress.connect(lambda val: self.insta_progress.setValue(val))
        self.instagram_worker.error.connect(lambda msg: self.log_message('instagram', f"ERROR: {msg}"))
        self.instagram_worker.finished.connect(self.instagram_download_finished)
        self.instagram_worker.login_required.connect(self.show_instagram_login)

        self.instagram_worker.start()

    def start_tiktok_download(self):
        input_text = self.tiktok_search.text().strip()
        if not input_text:
            self.show_error('Please enter a URL or hashtag')
            return

        self.tiktok_start_btn.setEnabled(False)
        self.tiktok_stop_btn.setEnabled(True)
        self.tiktok_search.setEnabled(False)
        self.tiktok_progress.setValue(0)
        self.tiktok_log.clear()

        self.tiktok_worker = TikTokDownloader(
            url_or_keyword=input_text,
            download_path=self.tiktok_path,
            is_url=self.tiktok_url_mode.isChecked()
        )

        self.tiktok_worker.progress.connect(lambda msg: self.log_message('tiktok', msg))
        self.tiktok_worker.download_progress.connect(lambda val: self.tiktok_progress.setValue(val))
        self.tiktok_worker.error.connect(lambda msg: self.log_message('tiktok', f"ERROR: {msg}"))
        self.tiktok_worker.finished.connect(self.tiktok_download_finished)

        self.tiktok_worker.start()

    def stop_instagram_download(self):
        if self.instagram_worker:
            self.instagram_worker.stop()
            self.log_message('instagram', 'Download stopped')

    def stop_tiktok_download(self):
        if self.tiktok_worker:
            self.tiktok_worker.stop()
            self.log_message('tiktok', 'Download stopped')

    def instagram_download_finished(self):
        self.insta_start_btn.setEnabled(True)
        self.insta_stop_btn.setEnabled(False)
        self.insta_search.setEnabled(True)
        self.log_message('instagram', 'Download finished')

    def tiktok_download_finished(self):
        self.tiktok_start_btn.setEnabled(True)
        self.tiktok_stop_btn.setEnabled(False)
        self.tiktok_search.setEnabled(True)
        self.log_message('tiktok', 'Download finished')

    def show_instagram_login(self):
        dialog = LoginDialog('Instagram', self)
        if dialog.exec_() == QDialog.Accepted:
            username = dialog.username.text().strip()
            password = dialog.password.text().strip()
            remember = dialog.remember_me.isChecked()
            
            if not username or not password:
                self.show_error('Username and password are required')
                self.instagram_download_finished()
                return
            
            self.log_message('instagram', f'Logging in as {username}...')
            if self.instagram_worker.set_login(username, password):
                if remember:
                    self.save_credentials('instagram', username, password)
                self.instagram_worker.start()
            else:
                self.instagram_download_finished()
                self.show_error('Login failed')

    def save_credentials(self, platform: str, username: str, password: str):
        try:
            credentials = {
                'username': username,
                'password': password
            }
            with open(f'{platform}_credentials.json', 'w') as f:
                json.dump(credentials, f)
        except Exception as e:
            logging.error(f"Failed to save credentials: {str(e)}")

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = SocialMediaDownloader()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()