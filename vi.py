import os
import sys
import time
import json
import logging
import random
import requests
from datetime import datetime
from typing import List, Dict
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QProgressBar, QTextEdit, QFileDialog, QMessageBox,
                            QCheckBox, QTabWidget, QFormLayout, QComboBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QIntValidator, QPalette
from instaloader import Instaloader, Post, Profile
from TikTokApi import TikTokApi

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('downloader.log'),
        logging.StreamHandler()
    ]
)

class InstagramDownloader:
    def __init__(self):
        self.L = Instaloader(
            download_videos=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            post_metadata_txt_pattern='',
            max_connection_attempts=3
        )
        
    def login(self, username: str, password: str) -> bool:
        try:
            self.L.login(username, password)
            return True
        except Exception as e:
            logging.error(f"Instagram login error: {str(e)}")
            return False

    def download_by_username(self, username: str, count: int = 10, download_path: str = None) -> List[str]:
        downloaded_files = []
        try:
            profile = Profile.from_username(self.L.context, username)
            posts = profile.get_posts()
            
            for idx, post in enumerate(posts):
                if idx >= count:
                    break
                    
                try:
                    time.sleep(random.uniform(2, 4))
                    
                    if download_path:
                        self.L.download_post(post, target=download_path)
                    else:
                        self.L.download_post(post)
                        
                    downloaded_files.append(post.url)
                    logging.info(f"Downloaded post {post.shortcode}")
                    
                except Exception as e:
                    logging.error(f"Error downloading post: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error fetching profile: {str(e)}")
            
        return downloaded_files

    def download_by_hashtag(self, hashtag: str, count: int = 10, download_path: str = None) -> List[str]:
        downloaded_files = []
        try:
            posts = self.L.get_hashtag_posts(hashtag)
            
            for idx, post in enumerate(posts):
                if idx >= count:
                    break
                    
                try:
                    time.sleep(random.uniform(2, 4))
                    
                    if download_path:
                        self.L.download_post(post, target=download_path)
                    else:
                        self.L.download_post(post)
                        
                    downloaded_files.append(post.url)
                    logging.info(f"Downloaded hashtag post {post.shortcode}")
                    
                except Exception as e:
                    logging.error(f"Error downloading hashtag post: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error fetching hashtag: {str(e)}")
            
        return downloaded_files

class TikTokDownloader:
    def __init__(self):
        self.api = TikTokApi()
        self.session = self._create_session()
        
    def _create_session(self):
        session = requests.Session()
        session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        return session

    def download_by_username(self, username: str, count: int = 10, download_path: str = None) -> List[str]:
        downloaded_files = []
        try:
            user_videos = self.api.user(username=username).videos(count=count)
            
            for video in user_videos:
                try:
                    video_url = video.info()['video']['downloadAddr']
                    filename = f"{username}_{video.id}.mp4"
                    
                    if download_path:
                        filepath = os.path.join(download_path, filename)
                    else:
                        filepath = filename
                        
                    response = self.session.get(video_url, stream=True)
                    if response.status_code == 200:
                        with open(filepath, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=1024):
                                if chunk:
                                    f.write(chunk)
                        downloaded_files.append(filepath)
                        logging.info(f"Downloaded TikTok video: {filename}")
                    
                    time.sleep(random.uniform(1, 3))
                    
                except Exception as e:
                    logging.error(f"Error downloading video: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error fetching TikTok user: {str(e)}")
            
        return downloaded_files

    def download_by_hashtag(self, hashtag: str, count: int = 10, download_path: str = None) -> List[str]:
        downloaded_files = []
        try:
            hashtag_videos = self.api.hashtag(name=hashtag).videos(count=count)
            
            for video in hashtag_videos:
                try:
                    video_url = video.info()['video']['downloadAddr']
                    filename = f"hashtag_{hashtag}_{video.id}.mp4"
                    
                    if download_path:
                        filepath = os.path.join(download_path, filename)
                    else:
                        filepath = filename
                        
                    response = self.session.get(video_url, stream=True)
                    if response.status_code == 200:
                        with open(filepath, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=1024):
                                if chunk:
                                    f.write(chunk)
                        downloaded_files.append(filepath)
                        logging.info(f"Downloaded TikTok hashtag video: {filename}")
                    
                    time.sleep(random.uniform(1, 3))
                    
                except Exception as e:
                    logging.error(f"Error downloading hashtag video: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error fetching TikTok hashtag: {str(e)}")
            
        return downloaded_files

class DownloadWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)
    
    def __init__(self, platform: str, download_type: str, query: str, 
                 count: int, download_path: str, credentials: Dict[str, str] = None):
        super().__init__()
        self.platform = platform
        self.download_type = download_type
        self.query = query
        self.count = count
        self.download_path = download_path
        self.credentials = credentials
        self.is_running = True

    def run(self):
        try:
            if self.platform == "instagram":
                downloader = InstagramDownloader()
                
                if self.credentials:
                    if not downloader.login(
                        self.credentials.get('username'),
                        self.credentials.get('password')
                    ):
                        self.error.emit("Instagram login failed")
                        return
                
                if self.download_type == "username":
                    files = downloader.download_by_username(
                        self.query, self.count, self.download_path
                    )
                else:
                    files = downloader.download_by_hashtag(
                        self.query, self.count, self.download_path
                    )
                    
            else:  # TikTok
                downloader = TikTokDownloader()
                
                if self.download_type == "username":
                    files = downloader.download_by_username(
                        self.query, self.count, self.download_path
                    )
                else:
                    files = downloader.download_by_hashtag(
                        self.query, self.count, self.download_path
                    )
            
            self.progress.emit(f"İndirilen dosya sayısı: {len(files)}")
            self.finished.emit()
            
        except Exception as e:
            self.error.emit(str(e))
            logging.error(f"Download error: {str(e)}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_settings()
        self.current_worker = None

    def init_ui(self):
        self.setWindowTitle('Sosyal Medya İçerik İndirici')
        self.setGeometry(100, 100, 800, 600)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        tabs = QTabWidget()
        tabs.addTab(self.create_instagram_tab(), "Instagram")
        tabs.addTab(self.create_tiktok_tab(), "TikTok")
        layout.addWidget(tabs)

    def create_instagram_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Giriş ayarları
        login_group = QWidget()
        login_layout = QFormLayout(login_group)
        self.insta_username = QLineEdit()
        self.insta_password = QLineEdit()
        self.insta_password.setEchoMode(QLineEdit.Password)
        login_layout.addRow("Kullanıcı Adı:", self.insta_username)
        login_layout.addRow("Şifre:", self.insta_password)
        layout.addWidget(login_group)

        # İndirme ayarları
        form_layout = QFormLayout()
        self.insta_query = QLineEdit()
        self.insta_count = QLineEdit()
        self.insta_count.setValidator(QIntValidator(1, 100))
        self.insta_count.setText("10")
        
        form_layout.addRow("Kullanıcı/Hashtag:", self.insta_query)
        form_layout.addRow("İndirilecek Sayı:", self.insta_count)
        layout.addLayout(form_layout)

        # İndirme tipi
        self.insta_type = QComboBox()
        self.insta_type.addItems(["Kullanıcı", "Hashtag"])
        layout.addWidget(self.insta_type)

        # Butonlar
        button_layout = QHBoxLayout()
        
        self.insta_download_btn = QPushButton("İndir")
        self.insta_download_btn.clicked.connect(lambda: self.start_download("instagram"))
        button_layout.addWidget(self.insta_download_btn)
        
        self.insta_stop_btn = QPushButton("Durdur")
        self.insta_stop_btn.clicked.connect(self.stop_download)
        self.insta_stop_btn.setEnabled(False)
        button_layout.addWidget(self.insta_stop_btn)
        
        layout.addLayout(button_layout)

        # İlerleme
        self.insta_progress = QTextEdit()
        self.insta_progress.setReadOnly(True)
        layout.addWidget(self.insta_progress)

        return tab

    def create_tiktok_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # İndirme ayarları
        form_layout = QFormLayout()
        self.tiktok_query = QLineEdit()
        self.tiktok_count = QLineEdit()
        self.tiktok_count.setValidator(QIntValidator(1, 100))
        self.tiktok_count.setText("10")
        
        form_layout.addRow("Kullanıcı/Hashtag:", self.tiktok_query)
        form_layout.addRow("İndirilecek Sayı:", self.tiktok_count)
        layout.addLayout(form_layout)

        # İndirme tipi
        self.tiktok_type = QComboBox()
        self.tiktok_type.addItems(["Kullanıcı", "Hashtag"])
        layout.addWidget(self.tiktok_type)

        # Butonlar
        button_layout = QHBoxLayout()
        
        self.tiktok_download_btn = QPushButton("İndir")
        self.tiktok_download_btn.clicked.connect(lambda: self.start_download("tiktok"))
        button_layout.addWidget(self.tiktok_download_btn)
        
        self.tiktok_stop_btn = QPushButton("Durdur")
        self.tiktok_stop_btn.clicked.connect(self.stop_download)
        self.tiktok_stop_btn.setEnabled(False)
        button_layout.addWidget(self.tiktok_stop_btn)
        
        layout.addLayout(button_layout)

        # İlerleme
        self.tiktok_progress = QTextEdit()
        self.tiktok_progress.setReadOnly(True)
        layout.addWidget(self.tiktok_progress)

        return tab

    def start_download(self, platform):
        if self.current_worker and self.current_worker.isRunning():
            return

        # Input kontrolü
        query = self.insta_query.text().strip() if platform == "instagram" else self.tiktok_query.text().strip()
        if not query:
            QMessageBox.warning(self, "Hata", "Lütfen bir sorgu girin!")
            return

        try:
            count = int(self.insta_count.text() if platform == "instagram" else self.tiktok_count.text())
        except ValueError:
            count = 10

        # İndirme tipini belirle
        download_type = "username" if (
            self.insta_type.currentText() == "Kullanıcı" if platform == "instagram" 
            else self.tiktok_type.currentText() == "Kullanıcı"
        ) else "hashtag"

        # İndirme dizini oluştur
        download_path = self.get_download_path(platform)
        os.makedirs(download_path, exist_ok=True)

        # Kimlik bilgileri
        credentials = None
        if platform == "instagram":
            credentials = {
                'username': self.insta_username.text().strip(),
                'password': self.insta_password.text().strip()
            }
        
        # İndirme worker'ını başlat
        self.current_worker = DownloadWorker(
            platform=platform,
            download_type=download_type, 
            query=query,
            count=count,
            download_path=download_path,
            credentials=credentials
        )
        
        # Worker sinyallerini bağla
        self.current_worker.progress.connect(self.update_progress)
        self.current_worker.error.connect(self.show_error)
        self.current_worker.finished.connect(self.download_finished)
        
        # UI durumunu güncelle
        if platform == "instagram":
            self.insta_download_btn.setEnabled(False)
            self.insta_stop_btn.setEnabled(True)
            self.insta_progress.clear()
        else:
            self.tiktok_download_btn.setEnabled(False)
            self.tiktok_stop_btn.setEnabled(True)
            self.tiktok_progress.clear()
            
        # Worker'ı başlat
        self.current_worker.start()

    def stop_download(self):
        if self.current_worker:
            self.current_worker.is_running = False
            self.current_worker.wait()
            self.current_worker = None
            self.update_progress("İndirme durduruldu.")

    def update_progress(self, message):
        if self.current_worker.platform == "instagram":
            self.insta_progress.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")
        else:
            self.tiktok_progress.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")

    def show_error(self, message):
        QMessageBox.critical(self, "Hata", message)
        self.download_finished()

    def download_finished(self):
        if self.current_worker:
            platform = self.current_worker.platform
            self.current_worker = None
            
            if platform == "instagram":
                self.insta_download_btn.setEnabled(True)
                self.insta_stop_btn.setEnabled(False)
            else:
                self.tiktok_download_btn.setEnabled(True)
                self.tiktok_stop_btn.setEnabled(False)
                
            self.update_progress("İndirme tamamlandı.")

    def get_download_path(self, platform):
        settings = self.load_settings()
        base_path = settings.get('download_path', os.path.expanduser('~/Downloads'))
        return os.path.join(base_path, platform.capitalize())

    def load_settings(self):
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r') as f:
                    return json.load(f)
        except Exception as e:
            logging.error(f"Settings load error: {str(e)}")
        return {}

    def save_settings(self, settings):
        try:
            with open('settings.json', 'w') as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            logging.error(f"Settings save error: {str(e)}")

def main():
    app = QApplication(sys.argv)
    
    # Stil ayarları
    app.setStyle('Fusion')
    
    # Dark tema
    palette = QPalette()
    palette.setColor(QPalette.Window, Qt.darkGray)
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, Qt.darkGray)
    palette.setColor(QPalette.AlternateBase, Qt.gray)
    palette.setColor(QPalette.ToolTipBase, Qt.darkGray)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, Qt.darkGray)
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Highlight, Qt.blue)
    palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logging.critical(f"Application crashed: {str(e)}")
        raise