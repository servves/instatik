import sys
import os
import hashlib
import json
import time
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QProgressBar, QTextEdit, QFileDialog, QMessageBox,
                            QCheckBox, QTabWidget, QDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QIntValidator
from instaloader import Instaloader, Profile, LoginRequiredException, TooManyRequestsException
import requests
from urllib.parse import urlparse
from TikTokApi import TikTokApi
import logging

# Logging ayarları
logging.basicConfig(
    filename='social_media_downloader.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class LoginDialog(QDialog):
    def __init__(self, platform, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle(f'{self.platform} Girişi')
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)

        title_label = QLabel(f'{self.platform} hesabınızla giriş yapın:')
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        self.username = QLineEdit(self)
        self.username.setPlaceholderText('Kullanıcı Adı')
        layout.addWidget(self.username)

        self.password = QLineEdit(self)
        self.password.setPlaceholderText('Şifre')
        self.password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.password)

        button_layout = QHBoxLayout()
        
        self.login_button = QPushButton('Giriş Yap', self)
        self.login_button.clicked.connect(self.accept)
        self.login_button.setStyleSheet("""
            QPushButton {
                background-color: #0095f6;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1aa1f6;
            }
        """)
        
        self.cancel_button = QPushButton('İptal', self)
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.login_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.remember_me = QCheckBox('Beni Hatırla', self)
        layout.addWidget(self.remember_me)

class DownloadWorker(QThread):
    progress = pyqtSignal(str)
    download_progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, platform, download_path):
        super().__init__()
        self.platform = platform
        self.download_path = download_path
        self.is_running = True
        self.hash_file = os.path.join(download_path, f'{platform.lower()}_downloaded_hashes.json')
        self.downloaded_hashes = self.load_hashes()

    def load_hashes(self):
        if os.path.exists(self.hash_file):
            try:
                with open(self.hash_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def save_hashes(self):
        with open(self.hash_file, 'w', encoding='utf-8') as f:
            json.dump(self.downloaded_hashes, f, ensure_ascii=False, indent=4)

    def calculate_hash(self, file_path):
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            buf = f.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = f.read(65536)
        return hasher.hexdigest()

    def stop(self):
        self.is_running = False

class InstagramDownloadWorker(DownloadWorker):
    login_required = pyqtSignal()

    def __init__(self, keyword, download_path, download_videos=True, download_photos=True):
        super().__init__('Instagram', download_path)
        self.keyword = keyword
        self.download_videos = download_videos
        self.download_photos = download_photos
        
        self.L = Instaloader(
            download_videos=download_videos,
            download_pictures=download_photos,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True
        )

    def set_login_credentials(self, username, password):
        try:
            self.L.context.log("Attempting login...")
            self.session = self.L.login(username, password)

            # Verify login was successful
            if self.L.context.is_logged_in:
                self.L.context.log("Login successful")
                # Save session for future use
                self.L.save_session_to_file(f"{username}_session")
                return True
            else:
                raise Exception("Login verification failed")

        except Exception as e:
            self.error.emit(f"Giriş hatası: {str(e)}")
            logging.error(f"Instagram login error: {str(e)}")
            # Add retry mechanism
            self.handle_login_error(username, password)
            return False
    def save_session(self):
        if self.L.context.is_logged_in and hasattr(self, 'username'):
            try:
                self.L.save_session_to_file(f"{self.username}_session")
                logging.info("Session saved successfully")
            except Exception as e:
                logging.error(f"Error saving session: {str(e)}")
    
    def load_session(self):
        if hasattr(self, 'username'):
            try:
                self.L.load_session_from_file(f"{self.username}_session")
                return self.L.context.is_logged_in
            except Exception as e:
                logging.error(f"Error loading session: {str(e)}")
                return False
        return False
    def handle_login_error(self, username, password):
        max_retries = 3
        retry_delay = 5  # seconds

        for attempt in range(max_retries):
            try:
                time.sleep(retry_delay * (attempt + 1))
                self.L.context.log(f"Retry attempt {attempt + 1}")
                if self.L.login(username, password):
                    return True
            except Exception as e:
                logging.error(f"Login retry {attempt + 1} failed: {str(e)}")
        return False

    def run(self):
        try:
            # Oturum kontrolü ve giriş durumu
            if not self.L.context.is_logged_in:
                self.login_required.emit()
                return

            self.progress.emit(f"'{self.keyword}' için arama yapılıyor...")
            logging.info(f"Searching for '{self.keyword}' on Instagram")

            # Rate limit kontrolü için değişkenler
            max_retries = 3
            base_delay = 5
            posts = None

            for attempt in range(max_retries):
                try:
                    # Hashtag veya kullanıcı adı kontrolü
                    if self.keyword.startswith('#'):
                        hashtag = self.keyword.lstrip('#')
                        posts = list(self.L.get_hashtag_posts(hashtag))
                        self.progress.emit(f"#{hashtag} hashtag'i için sonuçlar bulundu")
                    else:
                        # Önce profil olarak dene
                        try:
                            profile = Profile.from_username(self.L.context, self.keyword)
                            posts = list(profile.get_posts())
                            self.progress.emit(f"Profil bulundu: {profile.username}")
                        except Exception as profile_error:
                            # Profil bulunamazsa hashtag olarak dene
                            logging.warning(f"Profile search failed, trying as hashtag: {str(profile_error)}")
                            posts = list(self.L.get_hashtag_posts(self.keyword))
                            self.progress.emit(f"#{self.keyword} hashtag'i için sonuçlar bulundu")

                    if posts:  # Eğer posts başarıyla alındıysa döngüden çık
                        break

                except TooManyRequestsException as e:
                    wait_time = base_delay * (2 ** attempt)
                    self.progress.emit(f"Rate limit aşıldı. {wait_time} saniye bekleniyor... ({attempt + 1}/{max_retries})")
                    logging.warning(f"Rate limit hit, waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    if attempt == max_retries - 1:
                        raise e
                    continue

                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = base_delay * (attempt + 1)
                        self.progress.emit(f"Hata oluştu. Yeniden deneniyor... ({attempt + 1}/{max_retries})")
                        logging.error(f"Error occurred: {str(e)}")
                        time.sleep(wait_time)
                    else:
                        raise e

            if not posts:
                self.error.emit("Gönderi bulunamadı!")
                return

            # İndirme işlemi
            total_downloaded = 0
            for post in posts:
                if not self.is_running:
                    break

                try:
                    is_video = post.is_video
                    if (is_video and self.download_videos) or (not is_video and self.download_photos):
                        self.progress.emit(f"İndiriliyor: {post.shortcode}")

                        # Rate limiting için kısa bekleme
                        time.sleep(2)

                        try:
                            self.L.download_post(post, target=self.download_path)
                        except TooManyRequestsException:
                            # Rate limit durumunda bekleme ve yeniden deneme
                            time.sleep(30)
                            self.L.download_post(post, target=self.download_path)

                        file_pattern = f"{post.date_utc.strftime('%Y-%m-%d_%H-%M-%S')}_{post.shortcode}"
                        downloaded_files = [f for f in os.listdir(self.download_path) 
                                         if f.startswith(file_pattern)]

                        for file_name in downloaded_files:
                            file_path = os.path.join(self.download_path, file_name)

                            try:
                                file_hash = self.calculate_hash(file_path)

                                if file_hash not in self.downloaded_hashes:
                                    new_name = f"instagram_{post.shortcode}_{file_hash[:8]}{os.path.splitext(file_name)[1]}"
                                    new_path = os.path.join(self.download_path, new_name)
                                    os.rename(file_path, new_path)

                                    self.downloaded_hashes[file_hash] = {
                                        'date': datetime.now().isoformat(),
                                        'shortcode': post.shortcode,
                                        'type': 'video' if is_video else 'photo',
                                        'file_path': new_path
                                    }

                                    total_downloaded += 1
                                    self.progress.emit(f"İndirilen: {new_name}")
                                else:
                                    os.remove(file_path)
                                    self.progress.emit(f"Tekrar eden içerik atlandı: {post.shortcode}")

                                self.save_hashes()
                                self.download_progress.emit(total_downloaded)

                            except Exception as hash_error:
                                logging.error(f"Hash calculation error: {str(hash_error)}")
                                continue

                except Exception as post_error:
                    self.error.emit(f"İçerik indirme hatası: {str(post_error)}")
                    logging.error(f"Content download error: {str(post_error)}")
                    time.sleep(5)  # Hata durumunda kısa bekleme
                    continue

        except TooManyRequestsException as e:
            self.error.emit("Rate limit aşıldı. Lütfen birkaç dakika bekleyin.")
            logging.error(f"Rate limit exceeded: {str(e)}")
            self.handle_rate_limit()
        except LoginRequiredException as e:
            self.error.emit("Oturum süresi doldu. Lütfen yeniden giriş yapın.")
            logging.error(f"Login required: {str(e)}")
            self.login_required.emit()
        except Exception as e:
            self.error.emit(f"Genel hata: {str(e)}")
            logging.error(f"General error: {str(e)}")
        finally:
            if total_downloaded > 0:
                self.progress.emit(f"Toplam {total_downloaded} içerik indirildi.")
            self.finished.emit()
        def handle_rate_limit(self):
            delay = 60
            max_retries = 5
            for retry in range(max_retries):
                if not self.is_running:
                    break
                logging.info(f"Rate limited, retrying in {delay} seconds... (Attempt {retry+1}/{max_retries})")
                self.progress.emit(f"Rate limited, retrying in {delay} seconds... (Attempt {retry+1}/{max_retries})")
                time.sleep(delay)
                delay *= 2

class TikTokDownloadWorker(DownloadWorker):
    def __init__(self, keyword_or_url, download_path, download_type="keyword"):
        super().__init__('TikTok', download_path)
        self.keyword_or_url = keyword_or_url
        self.download_type = download_type

    def download_video(self, video_url, output_path):
        try:
            response = requests.get(video_url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            downloaded_size = 0

            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    if not self.is_running:
                        f.close()
                        os.remove(output_path)
                        return False
                    
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size:
                            progress = (downloaded_size / total_size) * 100
                            self.download_progress.emit(int(progress))

            return True
        except Exception as e:
            self.error.emit(f"Video indirme hatası: {str(e)}")
            logging.error(f"TikTok download error: {str(e)}")
            return False

    def run(self):
        try:
            api = TikTokApi()
            
            if self.download_type == "keyword":
                self.progress.emit(f"'{self.keyword_or_url}' için arama yapılıyor...")
                videos = api.search.videos(self.keyword_or_url, count=50)
            else:
                video_id = self.extract_video_id(self.keyword_or_url)
                self.progress.emit(f"Video ID: {video_id} indiriliyor...")
                videos = [api.video(id=video_id)]

            total_downloaded = 0

            for video in videos:
                if not self.is_running:
                    break

                try:
                    video_data = video.info()
                    video_url = video_data['video']['downloadAddr']
                    
                    temp_filename = os.path.join(self.download_path, f"temp_{video_data['id']}.mp4")
                    
                    if self.download_video(video_url, temp_filename):
                        file_hash = self.calculate_hash(temp_filename)
                        
                        if file_hash not in self.downloaded_hashes:
                            new_filename = os.path.join(
                                self.download_path, 
                                f"tiktok_{video_data['id']}_{file_hash[:8]}.mp4"
                            )
                            os.rename(temp_filename, new_filename)
                            
                            self.downloaded_hashes[file_hash] = {
                                'date': datetime.now().isoformat(),
                                'video_id': video_data['id'],
                                'author': video_data['author']['uniqueId'],
                                'file_path': new_filename
                            }
                            self.save_hashes()
                            
                            total_downloaded += 1
                            self.progress.emit(f"İndirilen: {new_filename}")
                        else:
                            if os.path.exists(temp_filename):
                                os.remove(temp_filename)
                            self.progress.emit(f"Tekrar eden video atlandı: {video_data['id']}")

                except Exception as e:
                    self.error.emit(f"Video işleme hatası: {str(e)}")
                    logging.error(f"TikTok processing error: {str(e)}")
                    continue

                time.sleep(1)
                    
        except TooManyRequestsException as e:
            logging.error(f"Rate limit exceeded: {str(e)}")
            self.handle_rate_limit()
        except Exception as e:
            self.error.emit(f"Genel hata: {str(e)}")
            logging.error(f"General TikTok error: {str(e)}")
        finally:
            self.finished.emit()

    def handle_rate_limit(self):
        delay = 60
        max_retries = 5
        for retry in range(max_retries):
            if not self.is_running:
                break
            logging.info(f"Rate limited, retrying in {delay} seconds... (Attempt {retry+1}/{max_retries})")
            self.progress.emit(f"Rate limited, retrying in {delay} seconds... (Attempt {retry+1}/{max_retries})")
            time.sleep(delay)
            delay *= 2

    def extract_video_id(self, url):
        parsed = urlparse(url)
        path = parsed.path
        video_id = path.split('/')[-1]
        return video_id

class InstagramDownloadWorker(DownloadWorker):
    def __init__(self, keyword, download_path, download_videos=True, download_photos=True):
        super().__init__('Instagram', download_path)
        self.keyword = keyword
        self.download_videos = download_videos
        self.download_photos = download_photos
        self.session = None
        
        self.L = Instaloader(
            download_videos=download_videos,
            download_pictures=download_photos,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
            max_connection_attempts=3
        )

    def initUI(self):
        self.setWindowTitle('Sosyal Medya İçerik İndirici')
        self.setGeometry(100, 100, 900, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.tabs = QTabWidget()
        self.instagram_tab = QWidget()
        self.tiktok_tab = QWidget()
        
        self.tabs.addTab(self.instagram_tab, "Instagram")
        self.tabs.addTab(self.tiktok_tab, "TikTok")
        
        layout.addWidget(self.tabs)

        self.setup_instagram_tab()
        self.setup_tiktok_tab()

        self.statusBar().showMessage('Hazır')

    def setup_instagram_tab(self):
        layout = QVBoxLayout(self.instagram_tab)

        search_layout = QHBoxLayout()
        self.insta_search_input = QLineEdit()
        self.insta_search_input.setPlaceholderText('Kullanıcı adı veya hashtag girin...')
        search_layout.addWidget(self.insta_search_input)
        
        self.insta_path_button = QPushButton('İndirme Dizini Seç')
        self.insta_path_button.clicked.connect(
            lambda: self.select_download_path('instagram'))
        search_layout.addWidget(self.insta_path_button)
        
        layout.addLayout(search_layout)

        options_layout = QHBoxLayout()
        self.video_checkbox = QCheckBox('Videoları İndir')
        self.photo_checkbox = QCheckBox('Fotoğrafları İndir')
        self.video_checkbox.setChecked(True)
        self.photo_checkbox.setChecked(True)
        options_layout.addWidget(self.video_checkbox)
        options_layout.addWidget(self.photo_checkbox)
        layout.addLayout(options_layout)

        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel('İndirme Limiti:'))
        self.insta_limit_input = QLineEdit()
        self.insta_limit_input.setPlaceholderText('Boş bırakın veya sayı girin')
        self.insta_limit_input.setValidator(QIntValidator(1, 1000))
        limit_layout.addWidget(self.insta_limit_input)
        layout.addLayout(limit_layout)

        button_layout = QHBoxLayout()
        self.insta_download_button = QPushButton('İndirmeyi Başlat')
        self.insta_download_button.clicked.connect(self.start_instagram_download)
        button_layout.addWidget(self.insta_download_button)

        self.insta_stop_button = QPushButton('İndirmeyi Durdur')
        self.insta_stop_button.clicked.connect(self.stop_instagram_download)
        self.insta_stop_button.setEnabled(False)
        button_layout.addWidget(self.insta_stop_button)
        layout.addLayout(button_layout)

        self.insta_progress_bar = QProgressBar()
        layout.addWidget(self.insta_progress_bar)

        self.insta_log_text = QTextEdit()
        self.insta_log_text.setReadOnly(True)
        layout.addWidget(self.insta_log_text)

        self.insta_login_status = QLabel('Giriş durumu: Giriş yapılmadı')
        layout.addWidget(self.insta_login_status)

    def setup_tiktok_tab(self):
        layout = QVBoxLayout(self.tiktok_tab)

        search_type_layout = QHBoxLayout()
        self.tiktok_search_type = QCheckBox('URL Modunu Kullan')
        search_type_layout.addWidget(self.tiktok_search_type)
        layout.addLayout(search_type_layout)

        search_layout = QHBoxLayout()
        self.tiktok_search_input = QLineEdit()
        self.tiktok_search_input.setPlaceholderText('TikTok hashtag/kelime veya video URL girin...')
        search_layout.addWidget(self.tiktok_search_input)
        
        self.tiktok_path_button = QPushButton('İndirme Dizini Seç')
        self.tiktok_path_button.clicked.connect(
            lambda: self.select_download_path('tiktok'))
        search_layout.addWidget(self.tiktok_path_button)
        layout.addLayout(search_layout)

        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel('İndirme Limiti:'))
        self.tiktok_limit_input = QLineEdit()
        self.tiktok_limit_input.setPlaceholderText('Boş bırakın veya sayı girin')
        self.tiktok_limit_input.setValidator(QIntValidator(1, 1000))
        limit_layout.addWidget(self.tiktok_limit_input)
        layout.addLayout(limit_layout)

        button_layout = QHBoxLayout()
        self.tiktok_download_button = QPushButton('İndirmeyi Başlat')
        self.tiktok_download_button.clicked.connect(self.start_tiktok_download)
        button_layout.addWidget(self.tiktok_download_button)

        self.tiktok_stop_button = QPushButton('İndirmeyi Durdur')
        self.tiktok_stop_button.clicked.connect(self.stop_tiktok_download)
        self.tiktok_stop_button.setEnabled(False)
        button_layout.addWidget(self.tiktok_stop_button)
        layout.addLayout(button_layout)

        self.tiktok_progress_bar = QProgressBar()
        layout.addWidget(self.tiktok_progress_bar)

        self.tiktok_log_text = QTextEdit()
        self.tiktok_log_text.setReadOnly(True)
        layout.addWidget(self.tiktok_log_text)

    def load_settings(self):
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r') as f:
                    settings = json.load(f)
                    self.instagram_download_path = settings.get('instagram_path', '')
                    self.tiktok_download_path = settings.get('tiktok_path', '')
            else:
                # Varsayılan indirme dizinlerini ayarla
                downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
                self.instagram_download_path = os.path.join(downloads_dir, 'Instagram')
                self.tiktok_download_path = os.path.join(downloads_dir, 'TikTok')
        except Exception as e:
            logging.error(f"Ayarlar yüklenirken hata: {str(e)}")
            self.show_error_message("Ayarlar yüklenemedi!")

    def save_settings(self):
        try:
            settings = {
                'instagram_path': self.instagram_download_path,
                'tiktok_path': self.tiktok_download_path
            }
            with open('settings.json', 'w') as f:
                json.dump(settings, f)
        except Exception as e:
            logging.error(f"Ayarlar kaydedilirken hata: {str(e)}")
            self.show_error_message("Ayarlar kaydedilemedi!")

    def select_download_path(self, platform):
        dir_path = QFileDialog.getExistingDirectory(self, 'İndirme Dizini Seç')
        if dir_path:
            if platform == 'instagram':
                self.instagram_download_path = dir_path
            else:
                self.tiktok_download_path = dir_path
            self.save_settings()

    def show_error_message(self, message):
        QMessageBox.critical(self, 'Hata', message)

    def show_info_message(self, message):
        QMessageBox.information(self, 'Bilgi', message)

    def update_status(self, message):
        self.statusBar().showMessage(message)

    def start_instagram_download(self):
        keyword = self.insta_search_input.text().strip()
        if not keyword:
            self.show_error_message('Lütfen bir anahtar kelime girin.')
            return

        os.makedirs(self.instagram_download_path, exist_ok=True)

        self.insta_download_button.setEnabled(False)
        self.insta_stop_button.setEnabled(True)
        self.insta_search_input.setEnabled(False)
        self.insta_progress_bar.setValue(0)
        self.insta_log_text.clear()

        self.instagram_worker = InstagramDownloadWorker(
            keyword,
            self.instagram_download_path,
            self.video_checkbox.isChecked(),
            self.photo_checkbox.isChecked()
        )

        self.instagram_worker.progress.connect(
            lambda msg: self.log_message('instagram', msg))
        self.instagram_worker.download_progress.connect(
            lambda val: self.update_progress('instagram', val))
        self.instagram_worker.error.connect(
            lambda msg: self.log_message('instagram', msg))
        self.instagram_worker.finished.connect(self.instagram_download_finished)
        self.instagram_worker.login_required.connect(self.show_instagram_login)

        self.instagram_worker.start()

    def show_instagram_login(self):
        dialog = LoginDialog('Instagram', self)
        if dialog.exec_() == QDialog.Accepted:
            username = dialog.username.text()
            password = dialog.password.text()
            remember = dialog.remember_me.isChecked()

            if self.instagram_worker.set_login_credentials(username, password):
                self.insta_login_status.setText(f'Giriş durumu: {username} olarak giriş yapıldı')
                if remember:
                    self.save_credentials('instagram', username, password)
                self.instagram_worker.start()
            else:
                self.instagram_download_finished()
                self.show_error_message('Instagram girişi başarısız!')

    def save_credentials(self, platform, username, password):
        try:
            credentials = {
                'username': username,
                'password': password  # Gerçek uygulamada şifrelenmeli!
            }
            with open(f'{platform}_credentials.json', 'w') as f:
                json.dump(credentials, f)
        except Exception as e:
            logging.error(f"Kimlik bilgileri kaydedilirken hata: {str(e)}")

    def load_credentials(self, platform):
        try:
            if os.path.exists(f'{platform}_credentials.json'):
                with open(f'{platform}_credentials.json', 'r') as f:
                    return json.load(f)
        except Exception as e:
            logging.error(f"Kimlik bilgileri yüklenirken hata: {str(e)}")
        return None

    def start_tiktok_download(self):
        input_text = self.tiktok_search_input.text().strip()
        if not input_text:
            self.show_error_message('Lütfen bir anahtar kelime veya URL girin.')
            return

        os.makedirs(self.tiktok_download_path, exist_ok=True)

        self.tiktok_download_button.setEnabled(False)
        self.tiktok_stop_button.setEnabled(True)
        self.tiktok_search_input.setEnabled(False)
        self.tiktok_progress_bar.setValue(0)
        self.tiktok_log_text.clear()

        download_type = "url" if self.tiktok_search_type.isChecked() else "keyword"
        self.tiktok_worker = TikTokDownloadWorker(
            input_text,
            self.tiktok_download_path,
            download_type
        )

        self.tiktok_worker.progress.connect(
            lambda msg: self.log_message('tiktok', msg))
        self.tiktok_worker.download_progress.connect(
            lambda val: self.update_progress('tiktok', val))
        self.tiktok_worker.error.connect(
            lambda msg: self.log_message('tiktok', msg))
        self.tiktok_worker.finished.connect(self.tiktok_download_finished)

        self.tiktok_worker.start()

    def stop_instagram_download(self):
        if self.instagram_worker:
            self.instagram_worker.stop()
            self.log_message('instagram', "İndirme durduruldu.")

    def stop_tiktok_download(self):
        if self.tiktok_worker:
            self.tiktok_worker.stop()
            self.log_message('tiktok', "İndirme durduruldu.")

    def instagram_download_finished(self):
        self.insta_download_button.setEnabled(True)
        self.insta_stop_button.setEnabled(False)
        self.insta_search_input.setEnabled(True)
        self.log_message('instagram', "İndirme tamamlandı!")

    def tiktok_download_finished(self):
        self.tiktok_download_button.setEnabled(True)
        self.tiktok_stop_button.setEnabled(False)
        self.tiktok_search_input.setEnabled(True)
        self.log_message('tiktok', "İndirme tamamlandı!")

    def log_message(self, platform, message):
        if platform == 'instagram':
            self.insta_log_text.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        else:
            self.tiktok_log_text.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def update_progress(self, platform, value):
        if platform == 'instagram':
            self.insta_progress_bar.setValue(value)
        else:
            self.tiktok_progress_bar.setValue(value)

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    app_icon = QIcon('icon.png')
    app.setWindowIcon(app_icon)

    window = SocialMediaDownloader()
    window.show()

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()