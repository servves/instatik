import os
import sys
import time
import json
import logging
import random
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

# GUI için kütüphaneler
from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

# Instagram kütüphaneleri
from instaloader import Instaloader, Post, Profile, NodeIterator
from instascrape import Profile as ScrapeProfile
from instagram_private_api import Client, ClientCompatPatch

# TikTok kütüphaneleri 
from TikTokApi import TikTokApi

# Çevre değişkenleri için
from dotenv import load_dotenv

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('social_downloader.log'),
        logging.StreamHandler()
    ]
)

class InstagramDownloader:
    def __init__(self, username: str = None, password: str = None):
        self.L = Instaloader(
            download_videos=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=True,
            compress_json=False,
            post_metadata_txt_pattern='',
            max_connection_attempts=3
        )
        
        # Proxy rotasyon sistemi
        self.proxies = self._load_proxies()
        self.current_proxy_index = 0
        
        if username and password:
            try:
                self.L.load_session_from_file(username)
                logging.info("Loaded existing session")
            except FileNotFoundError:
                try:
                    self.L.login(username, password)
                    self.L.save_session_to_file(username)
                    logging.info("Created new session")
                except Exception as e:
                    logging.error(f"Login failed: {str(e)}")
                    raise

        # Private API client
        self.api = None
        if username and password:
            try:
                self.api = Client(username, password)
                logging.info("Private API client initialized")
            except Exception as e:
                logging.warning(f"Private API initialization failed: {str(e)}")

    def _load_proxies(self) -> List[str]:
        """Proxy listesini yükle"""
        try:
            with open('proxies.txt', 'r') as f:
                return [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def _rotate_proxy(self):
        """Proxy rotasyonu yap"""
        if self.proxies:
            self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxies)
            current_proxy = self.proxies[self.current_proxy_index]
            self.L.context.session.proxies = {
                'http': f'http://{current_proxy}',
                'https': f'https://{current_proxy}'
            }

    def download_by_username(self, username: str, count: int = 10, download_path: str = None) -> List[str]:
        """Kullanıcı gönderilerini indir"""
        downloaded_files = []
        try:
            profile = Profile.from_username(self.L.context, username)
            posts = profile.get_posts()
            
            for idx, post in enumerate(posts):
                if idx >= count:
                    break
                    
                try:
                    # Rate limit kontrolü
                    time.sleep(random.uniform(2, 4))
                    
                    # Proxy rotasyonu
                    self._rotate_proxy()
                    
                    if download_path:
                        self.L.download_post(post, target=download_path)
                    else:
                        self.L.download_post(post)
                        
                    downloaded_files.append(post.url)
                    logging.info(f"Downloaded post {post.shortcode}")
                    
                except Exception as e:
                    logging.error(f"Error downloading post {post.shortcode}: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error fetching profile {username}: {str(e)}")
            
        return downloaded_files

    def download_by_hashtag(self, hashtag: str, count: int = 10, download_path: str = None) -> List[str]:
        """Hashtag gönderilerini indir"""
        downloaded_files = []
        try:
            posts = self.L.get_hashtag_posts(hashtag)
            
            for idx, post in enumerate(posts):
                if idx >= count:
                    break
                    
                try:
                    time.sleep(random.uniform(2, 4))
                    self._rotate_proxy()
                    
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
            logging.error(f"Error fetching hashtag {hashtag}: {str(e)}")
            
        return downloaded_files

class TikTokDownloader:
    def __init__(self):
        self.api = TikTokApi()
        self.session = self._create_session()
        self.device_id = self._generate_device_id()
        
    def _create_session(self):
        """TikTok için özel session oluştur"""
        session = requests.Session()
        session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        return session

    def _generate_device_id(self) -> str:
        """Unique device ID oluştur"""
        return ''.join(random.choices('0123456789', k=19))

    def download_by_username(self, username: str, count: int = 10, download_path: str = None) -> List[str]:
        """Kullanıcı videolarını indir"""
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
            logging.error(f"Error fetching TikTok user {username}: {str(e)}")
            
        return downloaded_files

    def download_by_hashtag(self, hashtag: str, count: int = 10, download_path: str = None) -> List[str]:
        """Hashtag videolarını indir"""
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
            logging.error(f"Error fetching TikTok hashtag {hashtag}: {str(e)}")
            
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
                downloader = InstagramDownloader(
                    username=self.credentials.get('username'),
                    password=self.credentials.get('password')
                )
                
                if self.download_type == "username":
                    files = downloader.download_by_username(
                        self.query, self.count, self.download_path
                    )
                else:
                    files = downloader.download_by_hashtag(
                        self.query, self.count, self.download_path
                    )
                    
            elif self.platform == "tiktok":
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

class SocialMediaDownloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_settings()
        self.current_worker = None

    def init_ui(self):
        # Ana UI kurulumu...
        self.setWindowTitle('Sosyal Medya İndirici')
        self.setGeometry(100, 100, 800, 600)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Tab widget
        tabs = QTabWidget()
        tabs.addTab(self.create_instagram_tab(), "Instagram")
        tabs.addTab(self.create_tiktok_tab(), "TikTok")
        layout.addWidget(tabs)

    def create_instagram_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Input alanları
        form_layout = QFormLayout()
        self.insta_query = QLineEdit()
        self.insta_count = QLineEdit()
        self.insta_count.setValidator(QIntValidator(1, 100))
        self.insta_count.setText("10")
        
        form_layout.addRow("Kullanıcı/Hashtag:", self.insta_query)
        form_layout.addRow("İndirilecek Sayı:", self.insta_count)
        layout.addLayout(form_layout)

        # Seçenekler
        self.insta_type = QComboBox()
        self.insta_type.addItems(["Kullanıcı", "Hashtag"])
        layout.addWidget(self.insta_type)

        # Butonlar
        button_layout = QHBoxLayout()
        download_btn = QPushButton("İndir")
        download_btn.clicked.connect(lambda: self.start_download("instagram"))
        button_layout.addWidget(download_btn)
        
        stop_btn = QPushButton("Durdur")
        stop_btn.clicked.connect(self.stop_download)
        button_layout.addWidget(stop_btn)
        layout.addLayout(button_layout)

        # Progress
        self.insta_progress = QTextEdit()
        self.insta_progress.setReadOnly(True)
        layout.addWidget(self.insta_progress)

        return tab

    def create_tiktok_tab(self):
        # TikTok tab'ı için benzer yapı...
        pass

    def start_download(self, platform):
        if self.current_worker and self.current_worker.isRunning():
            return

        query = self.insta_query.text().strip() if platform == "instagram" else self.tiktok_query.text().strip()
        if not query:
            QMessageBox.warning(self, "Hata", "Lütfen bir sorgu girin!")
            return

        try:
            count = int(self.insta_count.text() if platform == "instagram" else self.tiktok_count.text())
        except ValueError:
            count = 10

        download_type = "username" if self.insta_type.currentText() == "Kullanıcı" else "hashtag"
        
        self.current_worker = DownloadWorker(
            platform=platform,
            download_type=download_type,
            query=query,
            count=count,
            download_path=self.get_download_path(platform),
            credentials=self.get_credentials(platform)
        )
        
        self.current_worker.progress.connect(self.update_progress)
        self.current_worker.error.connect(self.show_error)
        self.current_worker.finished.connect(self.download_finished)
        self.current_worker.start()
        
        if platform == "instagram":
            self.insta_progress.clear()
        else:
            self.tiktok_progress.clear()

    def create_tiktok_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Input alanları
        form_layout = QFormLayout()
        self.tiktok_query = QLineEdit()
        self.tiktok_count = QLineEdit()
        self.tiktok_count.setValidator(QIntValidator(1, 100))
        self.tiktok_count.setText("10")
        
        form_layout.addRow("Kullanıcı/Hashtag:", self.tiktok_query)
        form_layout.addRow("İndirilecek Sayı:", self.tiktok_count)
        layout.addLayout(form_layout)

        # Seçenekler
        self.tiktok_type = QComboBox()
        self.tiktok_type.addItems(["Kullanıcı", "Hashtag"])
        layout.addWidget(self.tiktok_type)

        # Butonlar
        button_layout = QHBoxLayout()
        download_btn = QPushButton("İndir")
        download_btn.clicked.connect(lambda: self.start_download("tiktok"))
        button_layout.addWidget(download_btn)
        
        stop_btn = QPushButton("Durdur")
        stop_btn.clicked.connect(self.stop_download)
        button_layout.addWidget(stop_btn)
        layout.addLayout(button_layout)

        # Progress
        self.tiktok_progress = QTextEdit()
        self.tiktok_progress.setReadOnly(True)
        layout.addWidget(self.tiktok_progress)

        return tab

    def stop_download(self):
        if self.current_worker:
            self.current_worker.is_running = False
            self.current_worker.wait()
            self.current_worker = None

    def update_progress(self, message):
        if self.current_worker.platform == "instagram":
            self.insta_progress.append(message)
        else:
            self.tiktok_progress.append(message)

    def show_error(self, message):
        QMessageBox.critical(self, "Hata", message)

    def download_finished(self):
        platform = self.current_worker.platform
        self.current_worker = None
        QMessageBox.information(self, "Bilgi", f"{platform.capitalize()} indirme işlemi tamamlandı!")

    def get_download_path(self, platform):
        settings = self.load_settings()
        base_path = settings.get('download_path', os.path.expanduser('~/Downloads'))
        platform_path = os.path.join(base_path, platform.capitalize())
        os.makedirs(platform_path, exist_ok=True)
        return platform_path

    def get_credentials(self, platform):
        settings = self.load_settings()
        return settings.get(f'{platform}_credentials', {})

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
    
    window = SocialMediaDownloader()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logging.critical(f"Application crashed: {str(e)}")
        raise