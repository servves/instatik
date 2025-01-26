import sys
import os
import hashlib
import json
import time
import logging
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                          QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                          QProgressBar, QTextEdit, QFileDialog, QMessageBox,
                          QCheckBox, QTabWidget, QDialog, QSpinBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

# Instagram API
from instagrapi import Client as InstagrapiClient
from instagrapi.types import Media
from instagrapi.exceptions import LoginRequired, ClientError, ClientLoginRequired

# TikTok API
from TikTokApi import TikTokApi

# Logging configuration
logging.basicConfig(
    filename='social_downloader.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class RetryableSession:
    """Rate limiting ve retry mekanizmalarını yöneten sınıf"""
    def __init__(self, max_retries=3, base_delay=1):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.current_retry = 0
    
    def reset(self):
        self.current_retry = 0
    
    def should_retry(self, exception) -> bool:
        if self.current_retry >= self.max_retries:
            return False
        
        retryable_exceptions = (
            ClientError,
            ConnectionError,
            TimeoutError
        )
        
        if isinstance(exception, retryable_exceptions):
            self.current_retry += 1
            time.sleep(self.base_delay * (2 ** (self.current_retry - 1)))
            return True
        
        return False

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

class DownloadWorker(QThread):
    """Base worker class for downloads"""
    progress = pyqtSignal(str)
    download_progress = pyqtSignal(int)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    login_required = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.is_running = True
        self.retry_session = RetryableSession()

    def stop(self):
        self.is_running = False

class InstagramDownloader(DownloadWorker):
    def __init__(self, keyword: str, download_path: str, download_videos=True, 
                 download_photos=True, max_items=50):
        super().__init__()
        self.keyword = keyword
        self.download_path = download_path
        self.download_videos = download_videos
        self.download_photos = download_photos
        self.max_items = max_items
        self.client = InstagrapiClient()
        
    def set_login(self, username: str, password: str) -> bool:
        try:
            session_file = f"{username}_instagram_session.json"
            
            if os.path.exists(session_file):
                self.client.load_settings(session_file)
                self.client.login(username, password)
            else:
                self.client.login(username, password)
                self.client.dump_settings(session_file)
            
            return True
            
        except (ClientLoginRequired, LoginRequired) as e:
            logging.error(f"Instagram login error: {str(e)}")
            self.error.emit("Login required")
            return False
            
        except Exception as e:
            logging.error(f"Instagram login error: {str(e)}")
            self.error.emit(f"Login error: {str(e)}")
            return False

    def run(self):
        try:
            if not self.client.user_id:
                self.login_required.emit()
                return

            self.progress.emit(f"Searching for '{self.keyword}'...")
            
            medias = []
            downloaded = 0
            
            # Hashtag veya kullanıcı araması
            if self.keyword.startswith('#'):
                hashtag = self.keyword[1:]
                medias = self.client.hashtag_medias_recent(hashtag, amount=self.max_items)
            else:
                try:
                    user_id = self.client.user_id_from_username(self.keyword)
                    medias = self.client.user_medias(user_id, amount=self.max_items)
                except Exception as e:
                    self.error.emit(f"User not found: {str(e)}")
                    return

            total_medias = len(medias)
            if total_medias == 0:
                self.error.emit("No media found")
                return

            self.progress.emit(f"Found {total_medias} media items")
            
            for i, media in enumerate(medias):
                if not self.is_running:
                    break
                    
                try:
                    if media.media_type == 1 and self.download_photos:  # Photo
                        path = self.client.photo_download(media.pk, self.download_path)
                        downloaded += 1
                    elif media.media_type == 2 and self.download_videos:  # Video
                        path = self.client.video_download(media.pk, self.download_path)
                        downloaded += 1
                    
                    progress = int((i + 1) / total_medias * 100)
                    self.download_progress.emit(progress)
                    self.progress.emit(f"Downloaded {downloaded}/{total_medias}")
                    
                except Exception as e:
                    if not self.retry_session.should_retry(e):
                        self.error.emit(f"Download error: {str(e)}")
                        continue
                        
                time.sleep(1)  # Rate limiting
                
        except Exception as e:
            self.error.emit(f"Error: {str(e)}")
        finally:
            self.finished.emit()

class TikTokDownloader(DownloadWorker):
    def __init__(self, keyword: str, download_path: str, max_items=50):
        super().__init__()
        self.keyword = keyword
        self.download_path = download_path
        self.max_items = max_items
        self.api = TikTokApi()

    def run(self):
        try:
            self.progress.emit(f"Searching for '{self.keyword}'...")
            
            downloaded = 0
            
            # URL veya hashtag kontrolü
            if self.keyword.startswith(('http://', 'https://')):
                # Tekil video indirme
                video_id = self.extract_video_id(self.keyword)
                if video_id:
                    video = self.api.video(id=video_id)
                    self.download_video(video)
                    downloaded += 1
            else:
                # Hashtag araması
                tag = self.keyword.strip('#')
                videos = self.api.hashtag(name=tag).videos(count=self.max_items)
                
                total_videos = min(len(videos), self.max_items)
                
                for i, video in enumerate(videos):
                    if not self.is_running:
                        break
                        
                    try:
                        if self.download_video(video):
                            downloaded += 1
                        
                        progress = int((i + 1) / total_videos * 100)
                        self.download_progress.emit(progress)
                        self.progress.emit(f"Downloaded {downloaded}/{total_videos}")
                        
                    except Exception as e:
                        if not self.retry_session.should_retry(e):
                            self.error.emit(f"Download error: {str(e)}")
                            continue
                            
                    time.sleep(1)  # Rate limiting
                    
        except Exception as e:
            self.error.emit(f"Error: {str(e)}")
        finally:
            self.finished.emit()

    def download_video(self, video) -> bool:
        try:
            video_data = video.bytes()
            filename = f"tiktok_{video.id}.mp4"
            filepath = os.path.join(self.download_path, filename)
            
            with open(filepath, 'wb') as f:
                f.write(video_data)
                
            return True
            
        except Exception as e:
            self.error.emit(f"Video download error: {str(e)}")
            return False

    def extract_video_id(self, url: str) -> Optional[str]:
        try:
            # TikTok URL'inden video ID çıkarma
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            
            if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
                # Kısa URL çözümleme
                import requests
                r = requests.head(url, allow_redirects=True)
                parsed = urlparse(r.url)
            
            path = parsed.path
            if '/video/' in path:
                return path.split('/video/')[1].split('/')[0]
            
            return None
            
        except Exception as e:
            self.error.emit(f"URL parse error: {str(e)}")
            return None

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

        # Main widget and layout
        central_widget = QWidget()
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
        
        # Download path
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

        # Control buttons
        btn_layout = QHBoxLayout()
        self.insta_start_btn = QPushButton('Start Download')
        self.insta_stop_btn = QPushButton('Stop Download')
        self.insta_start_btn.clicked.connect(self.start_instagram_download)
        self.insta_stop_btn.clicked.connect(self.stop_instagram_download)
        self.insta_stop_btn.setEnabled(False)
        btn_layout.addWidget(self.insta_start_btn)
        btn_layout.addWidget(self.insta_stop_btn)
        layout.addLayout(btn_layout)

        # Progress
        self.insta_progress = QProgressBar()
        layout.addWidget(self.insta_progress)

        # Log
        self.insta_log = QTextEdit()
        self.insta_log.setReadOnly(True)
        layout.addWidget(self.insta_log)

    def setup_tiktok_tab(self):
        layout = QVBoxLayout(self.tiktok_tab)

        # Search input
        search_layout = QHBoxLayout()
        self.tiktok_search = QLineEdit()
        self.tiktok_search.setPlaceholderText('Enter URL or #hashtag')
        search_layout.addWidget(self.tiktok_search)
        
        # Download path
        self.tiktok_path_btn = QPushButton('Select Download Folder')
        self.tiktok_path_btn.clicked.connect(lambda: self.select_download_path('tiktok'))
        search_layout.addWidget(self.tiktok_path_btn)
        layout.addLayout(search_layout)

        # Max items
        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel('Max Items:'))
        self.tiktok_max_items = QSpinBox()
        self.tiktok_max_items.setRange(1, 100)
        self.tiktok_max_items.setValue(50)
        limit_layout.addWidget(self.tiktok_max_items)
        layout.addLayout(limit_layout)

        # Control buttons
        btn_layout = QHBoxLayout()
        self.tiktok_start_btn = QPushButton('Start Download')
        self.tiktok_stop_btn = QPushButton('Stop Download')
        self.tiktok_start_btn.clicked.connect(self.start_tiktok_download)
        self.tiktok_stop_btn.clicked.connect(self.stop_tiktok_download)
        self.tiktok_stop_btn.setEnabled(False)
        btn_layout.addWidget(self.tiktok_start_btn)
        btn_layout.addWidget(self.tiktok_stop_btn)
        layout.addLayout(btn_layout)

        # Progress
        self.tiktok_progress = QProgressBar()
        layout.addWidget(self.tiktok_progress)

        # Log
        self.tiktok_log = QTextEdit()
        self.tiktok_log.setReadOnly(True)
        layout.addWidget(self.tiktok_log)

    def load_settings(self):
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r') as f:
                    settings = json.load(f)
                    self.instagram_path = settings.get('instagram_path', '')
                    self.tiktok_path = settings.get('tiktok_path', '')
            else:
                downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
                self.instagram_path = os.path.join(downloads_dir, 'Instagram')
                self.tiktok_path = os.path.join(downloads_dir, 'TikTok')
                
            # Klasörleri oluştur
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

    def update_progress(self, platform: str, value: int):
        if platform == 'instagram':
            self.insta_progress.setValue(value)
        else:
            self.tiktok_progress.setValue(value)

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

        self.instagram_worker.progress.connect(
            lambda msg: self.log_message('instagram', msg))
        self.instagram_worker.download_progress.connect(
            lambda val: self.update_progress('instagram', val))
        self.instagram_worker.error.connect(
            lambda msg: self.log_message('instagram', f"ERROR: {msg}"))
        self.instagram_worker.finished.connect(self.instagram_download_finished)
        self.instagram_worker.login_required.connect(self.show_instagram_login)

        self.instagram_worker.start()

    def start_tiktok_download(self):
        keyword = self.tiktok_search.text().strip()
        if not keyword:
            self.show_error('Please enter a URL or hashtag')
            return

        self.tiktok_start_btn.setEnabled(False)
        self.tiktok_stop_btn.setEnabled(True)
        self.tiktok_search.setEnabled(False)
        self.tiktok_progress.setValue(0)
        self.tiktok_log.clear()

        self.tiktok_worker = TikTokDownloader(
            keyword=keyword,
            download_path=self.tiktok_path,
            max_items=self.tiktok_max_items.value()
        )

        self.tiktok_worker.progress.connect(
            lambda msg: self.log_message('tiktok', msg))
        self.tiktok_worker.download_progress.connect(
            lambda val: self.update_progress('tiktok', val))
        self.tiktok_worker.error.connect(
            lambda msg: self.log_message('tiktok', f"ERROR: {msg}"))
        self.tiktok_worker.finished.connect(self.tiktok_download_finished)

        self.tiktok_worker.start()

    def stop_instagram_download(self):
        if self.instagram_worker:
            self.instagram_worker.stop()
            self.log_message('instagram', 'Download stopped by user')

    def stop_tiktok_download(self):
        if self.tiktok_worker:
            self.tiktok_worker.stop()
            self.log_message('tiktok', 'Download stopped by user')

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
                self.show_error('Username and password cannot be empty')
                self.instagram_download_finished()
                return
                
            self.log_message('instagram', f"Logging in as {username}...")
            
            if self.instagram_worker.set_login(username, password):
                if remember:
                    self.save_credentials('instagram', username, password)
                self.instagram_worker.start()
            else:
                self.instagram_download_finished()
                self.show_error('Instagram login failed')

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = SocialMediaDownloader()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()