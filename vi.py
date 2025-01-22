import sys
import os
import hashlib
import json
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QProgressBar, QTextEdit, QFileDialog, QMessageBox,
                            QCheckBox, QTabWidget)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from instaloader import Instaloader, Post
import requests
from urllib.parse import urlparse
from TikTokApi import TikTokApi

class TikTokDownloadWorker(QThread):
    progress = pyqtSignal(str)
    download_progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, keyword_or_url, download_path, download_type="keyword"):
        super().__init__()
        self.keyword_or_url = keyword_or_url
        self.download_path = download_path
        self.download_type = download_type
        self.is_running = True
        self.hash_file = os.path.join(download_path, 'tiktok_downloaded_hashes.json')
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

    def download_video(self, video_url, output_path):
        response = requests.get(video_url, stream=True)
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def run(self):
        try:
            api = TikTokApi()
            
            if self.download_type == "keyword":
                # Hashtag araması
                videos = api.search.videos(self.keyword_or_url, count=50)
            else:
                # Tek video indirme
                video_id = self.extract_video_id(self.keyword_or_url)
                videos = [api.video(id=video_id)]

            total_downloaded = 0

            for video in videos:
                if not self.is_running:
                    break

                try:
                    video_data = video.info()
                    video_url = video_data['video']['downloadAddr']
                    
                    # Geçici dosya adı oluştur
                    temp_filename = os.path.join(self.download_path, f"temp_{video_data['id']}.mp4")
                    
                    # Videoyu indir
                    self.download_video(video_url, temp_filename)
                    
                    # Hash hesapla
                    file_hash = self.calculate_hash(temp_filename)
                    
                    if file_hash not in self.downloaded_hashes:
                        # Yeni dosya adı oluştur
                        new_filename = os.path.join(self.download_path, 
                                                  f"tiktok_{video_data['id']}_{file_hash[:8]}.mp4")
                        os.rename(temp_filename, new_filename)
                        
                        # Hash'i kaydet
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
                    
                    self.download_progress.emit(total_downloaded)
                    
                except Exception as e:
                    self.error.emit(f"Video indirme hatası: {str(e)}")
                    continue
                    
        except Exception as e:
            self.error.emit(f"Genel hata: {str(e)}")
        
        finally:
            self.finished.emit()

    def extract_video_id(self, url):
        # TikTok URL'sinden video ID'sini çıkar
        parsed = urlparse(url)
        path = parsed.path
        video_id = path.split('/')[-1]
        return video_id

    def stop(self):
        self.is_running = False

class SocialMediaDownloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.instagram_worker = None
        self.tiktok_worker = None

    def initUI(self):
        self.setWindowTitle('Sosyal Medya İçerik İndirici')
        self.setGeometry(100, 100, 900, 700)

        # Ana widget ve layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Tab widget oluştur
        self.tabs = QTabWidget()
        self.instagram_tab = QWidget()
        self.tiktok_tab = QWidget()
        
        self.tabs.addTab(self.instagram_tab, "Instagram")
        self.tabs.addTab(self.tiktok_tab, "TikTok")
        
        layout.addWidget(self.tabs)

        # Instagram sekmesi düzeni
        self.setup_instagram_tab()
        
        # TikTok sekmesi düzeni
        self.setup_tiktok_tab()

    def setup_instagram_tab(self):
        layout = QVBoxLayout(self.instagram_tab)

        # Arama alanı
        search_layout = QHBoxLayout()
        self.insta_search_input = QLineEdit()
        self.insta_search_input.setPlaceholderText('Instagram hashtag/kelime girin...')
        search_layout.addWidget(self.insta_search_input)
        
        self.insta_path_button = QPushButton('İndirme Dizini Seç')
        self.insta_path_button.clicked.connect(
            lambda: self.select_download_path('instagram'))
        search_layout.addWidget(self.insta_path_button)
        
        layout.addLayout(search_layout)

        # Seçenekler
        options_layout = QHBoxLayout()
        self.video_checkbox = QCheckBox('Videoları İndir')
        self.photo_checkbox = QCheckBox('Fotoğrafları İndir')
        self.video_checkbox.setChecked(True)
        self.photo_checkbox.setChecked(True)
        options_layout.addWidget(self.video_checkbox)
        options_layout.addWidget(self.photo_checkbox)
        layout.addLayout(options_layout)

        # İndirme butonları
        self.insta_download_button = QPushButton('İndirmeyi Başlat')
        self.insta_download_button.clicked.connect(self.start_instagram_download)
        layout.addWidget(self.insta_download_button)

        self.insta_stop_button = QPushButton('İndirmeyi Durdur')
        self.insta_stop_button.clicked.connect(self.stop_instagram_download)
        self.insta_stop_button.setEnabled(False)
        layout.addWidget(self.insta_stop_button)

        # İlerleme çubuğu
        self.insta_progress_bar = QProgressBar()
        layout.addWidget(self.insta_progress_bar)

        # Log alanı
        self.insta_log_text = QTextEdit()
        self.insta_log_text.setReadOnly(True)
        layout.addWidget(self.insta_log_text)

    def setup_tiktok_tab(self):
        layout = QVBoxLayout(self.tiktok_tab)

        # Arama türü seçimi
        search_type_layout = QHBoxLayout()
        self.tiktok_search_type = QCheckBox('URL Modunu Kullan')
        search_type_layout.addWidget(self.tiktok_search_type)
        layout.addLayout(search_type_layout)

        # Arama alanı
        search_layout = QHBoxLayout()
        self.tiktok_search_input = QLineEdit()
        self.tiktok_search_input.setPlaceholderText('TikTok hashtag/kelime veya video URL girin...')
        search_layout.addWidget(self.tiktok_search_input)
        
        self.tiktok_path_button = QPushButton('İndirme Dizini Seç')
        self.tiktok_path_button.clicked.connect(
            lambda: self.select_download_path('tiktok'))
        search_layout.addWidget(self.tiktok_path_button)
        
        layout.addLayout(search_layout)

        # İndirme butonları
        self.tiktok_download_button = QPushButton('İndirmeyi Başlat')
        self.tiktok_download_button.clicked.connect(self.start_tiktok_download)
        layout.addWidget(self.tiktok_download_button)

        self.tiktok_stop_button = QPushButton('İndirmeyi Durdur')
        self.tiktok_stop_button.clicked.connect(self.stop_tiktok_download)
        self.tiktok_stop_button.setEnabled(False)
        layout.addWidget(self.tiktok_stop_button)

        # İlerleme çubuğu
        self.tiktok_progress_bar = QProgressBar()
        layout.addWidget(self.tiktok_progress_bar)

        # Log alanı
        self.tiktok_log_text = QTextEdit()
        self.tiktok_log_text.setReadOnly(True)
        layout.addWidget(self.tiktok_log_text)

    def select_download_path(self, platform):
        dir_path = QFileDialog.getExistingDirectory(self, 'İndirme Dizini Seç')
        if dir_path:
            if platform == 'instagram':
                self.instagram_download_path = dir_path
            else:
                self.tiktok_download_path = dir_path

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

    def start_instagram_download(self):
        keyword = self.insta_search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, 'Hata', 'Lütfen bir anahtar kelime girin.')
            return

        os.makedirs(self.instagram_download_path, exist_ok=True)

        self.insta_download_button.setEnabled(False)
        self.insta_stop_button.setEnabled(True)
        self.insta_search_input.setEnabled(False)
        self.insta_progress_bar.setValue(0)
        self.insta_log_text.clear()

        # Instagram worker'ı başlat
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
        self.instagram_worker.start()

    def start_tiktok_download(self):
        input_text = self.tiktok_search_input.text().strip()
        if not input_text:
            QMessageBox.warning(self, 'Hata', 'Lütfen bir anahtar kelime veya URL girin.')
            return

        os.makedirs(self.tiktok_download_path, exist_ok=True)

        self.tiktok_download_button.setEnabled(False)
        self.tiktok_stop_button.setEnabled(True)
        self.tiktok_search_input.setEnabled(False)
        self.tiktok_progress_bar.setValue(0)
        self.tiktok_log_text.clear()

        # TikTok worker'ı başlat
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

class InstagramDownloadWorker(QThread):
    progress = pyqtSignal(str)
    download_progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, keyword, download_path, download_videos=True, download_photos=True):
        super().__init__()
        self.keyword = keyword
        self.download_path = download_path
        self.download_videos = download_videos
        self.download_photos = download_photos
        self.is_running = True
        self.hash_file = os.path.join(download_path, 'instagram_downloaded_hashes.json')
        self.downloaded_hashes = self.load_hashes()
        
        # Instaloader instance'ı oluştur
        self.L = Instaloader(
            download_videos=download_videos,
            download_pictures=download_photos,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False
        )

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

    def run(self):
        try:
            # Hashtag araması yap
            posts = self.L.get_hashtag_posts(self.keyword)
            total_downloaded = 0

            for post in posts:
                if not self.is_running:
                    break

                try:
                    # Post'un medya türünü kontrol et
                    is_video = post.is_video
                    if (is_video and self.download_videos) or (not is_video and self.download_photos):
                        # Geçici dosya adı oluştur
                        file_extension = 'mp4' if is_video else 'jpg'
                        temp_filename = os.path.join(
                            self.download_path, 
                            f"temp_{post.date_utc.strftime('%Y%m%d_%H%M%S')}.{file_extension}"
                        )

                        # Medyayı indir
                        self.L.download_post(post, target=self.download_path)

                        # Hash hesapla
                        file_hash = self.calculate_hash(temp_filename)

                        if file_hash not in self.downloaded_hashes:
                            # Yeni dosya adı oluştur
                            new_filename = os.path.join(
                                self.download_path,
                                f"instagram_{post.shortcode}_{file_hash[:8]}.{file_extension}"
                            )
                            os.rename(temp_filename, new_filename)

                            # Hash'i kaydet
                            self.downloaded_hashes[file_hash] = {
                                'date': datetime.now().isoformat(),
                                'shortcode': post.shortcode,
                                'type': 'video' if is_video else 'photo',
                                'file_path': new_filename
                            }
                            self.save_hashes()

                            total_downloaded += 1
                            self.progress.emit(f"İndirilen: {new_filename}")
                        else:
                            if os.path.exists(temp_filename):
                                os.remove(temp_filename)
                            self.progress.emit(f"Tekrar eden içerik atlandı: {post.shortcode}")

                        self.download_progress.emit(total_downloaded)

                except Exception as e:
                    self.error.emit(f"İçerik indirme hatası: {str(e)}")
                    continue

        except Exception as e:
            self.error.emit(f"Genel hata: {str(e)}")

        finally:
            self.finished.emit()

    def stop(self):
        self.is_running = False

def main():
    app = QApplication(sys.argv)
    
    # Varsayılan indirme dizinlerini ayarla
    default_download_path = os.path.join(os.path.expanduser('~'), 'Downloads', 'SocialMedia')
    instagram_download_path = os.path.join(default_download_path, 'Instagram')
    tiktok_download_path = os.path.join(default_download_path, 'TikTok')
    
    # Dizinleri oluştur
    os.makedirs(instagram_download_path, exist_ok=True)
    os.makedirs(tiktok_download_path, exist_ok=True)
    
    # Ana pencereyi oluştur ve göster
    window = SocialMediaDownloader()
    window.instagram_download_path = instagram_download_path
    window.tiktok_download_path = tiktok_download_path
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()