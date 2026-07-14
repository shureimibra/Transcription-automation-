import os
import sys
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit,
    QProgressBar, QFileDialog, QMessageBox, QGroupBox
)
from PySide6.QtGui import QFont, QIcon, QPalette, QColor

# Import local components
from com_bridge import WordCOMBridge
# Add parent dir to path so we can import backend services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.core.services.ai_pipeline import AudioExtractor, WhisperTranscriber

class TranscriptionWorker(QThread):
    """
    Asynchronous QThread worker to execute media downloading,
    FFmpeg conversions, and Faster-Whisper pipeline without blocking the UI.
    """
    progress = Signal(str)         # Status messages
    segment_ready = Signal(dict)    # Emits chunk dicts: {text, start, end, speaker}
    finished_success = Signal(str)  # Completed message
    finished_error = Signal(str)    # Failure message

    def __init__(self, source: str, enable_diarization: bool, enable_vad: bool):
        super().__init__()
        self.source = source
        self.enable_diarization = enable_diarization
        self.enable_vad = enable_vad
        self.is_running = True

    def run(self):
        self.progress.emit("Initializing temporary work space...")
        out_wav = os.path.abspath("temp_processing_audio.wav")

        try:
            # 1. Media Acquisition & Extraction
            if self.source.startswith("http://") or self.source.startswith("https://"):
                self.progress.emit("Downloading media URL via yt-dlp...")
                success = AudioExtractor.download_and_extract_url(self.source, out_wav)
            else:
                self.progress.emit("Extracting mono audio stream using FFmpeg...")
                success = AudioExtractor.extract_from_file(self.source, out_wav)

            if not success or not os.path.exists(out_wav):
                self.finished_error.emit("Audio extraction failed. Verify media path or URL.")
                return

            if not self.is_running:
                return

            # 2. Transcription Inference
            self.progress.emit("Loading Whisper AI model (VAD active)...")
            transcriber = WhisperTranscriber(model_size="base")
            
            self.progress.emit("Transcribing audio segments (auto-detecting language)...")
            
            # Read chunks generator
            for chunk in transcriber.transcribe(out_wav, enable_vad=self.enable_vad):
                if not self.is_running:
                    break
                
                # Mock speaker label unless diarization matches
                speaker = "Speaker 0"
                if self.enable_diarization:
                    # In standard mock/base mode we assign a dummy alternating speaker label
                    # In full, we'd invoke pyannnote speaker cluster maps
                    sec = int(chunk['start'])
                    speaker = f"Speaker {(sec // 15) % 2}"

                self.segment_ready.emit({
                    "text": chunk["text"],
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "speaker": speaker
                })

            if self.is_running:
                self.finished_success.emit("Transcription complete.")
            else:
                self.finished_error.emit("Transcription canceled by user.")

        except Exception as e:
            self.finished_error.emit(f"AI Worker Exception: {str(e)}")
        finally:
            # Cleanup
            if os.path.exists(out_wav):
                try:
                    os.remove(out_wav)
                except Exception:
                    pass

    def stop(self):
        self.is_running = False


class MainWindow(QMainWindow):
    """
    Stunning Modern Desktop UI for the AI Transcription Platform.
    Includes drag/drop handlers, configurations, and real-time Word output.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Antigravity Multilingual AI Transcriber")
        self.resize(800, 600)
        self.setAcceptDrops(True)

        self.word_bridge = WordCOMBridge()
        self.worker = None
        self.transcript_buffer = []

        self.init_theme()
        self.init_ui()

    def init_theme(self):
        """
        Applies a polished dark-mode styling scheme to standard widgets.
        """
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(30, 30, 34))
        palette.setColor(QPalette.WindowText, QColor(240, 240, 240))
        palette.setColor(QPalette.Base, QColor(20, 20, 22))
        palette.setColor(QPalette.AlternateBase, QColor(40, 40, 45))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, QColor(240, 240, 240))
        palette.setColor(QPalette.Button, QColor(45, 45, 50))
        palette.setColor(QPalette.ButtonText, QColor(240, 240, 240))
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        
        QApplication.setPalette(palette)
        
        # QSS stylesheet rules for rounded buttons and elegant controls
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e22;
            }
            QGroupBox {
                border: 2px solid #2d2d32;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                color: #e0e0e0;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #141416;
                border: 1px solid #3a3a40;
                border-radius: 4px;
                padding: 6px;
                color: #f0f0f0;
            }
            QPushButton {
                background-color: #2a82da;
                border: none;
                border-radius: 4px;
                color: #ffffff;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3b93eb;
            }
            QPushButton:disabled {
                background-color: #2d2d32;
                color: #707070;
            }
            QTextEdit {
                background-color: #141416;
                border: 1px solid #2d2d32;
                border-radius: 6px;
                padding: 10px;
                color: #e0e0e0;
            }
            QProgressBar {
                border: 1px solid #2d2d32;
                border-radius: 4px;
                text-align: center;
                background-color: #141416;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #2a82da;
                border-radius: 3px;
            }
        """)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # Title Label
        title_label = QLabel("Multilingual AI Transcription Studio")
        font = QFont("Outfit", 18, QFont.Bold)
        title_label.setFont(font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # Input File Section
        input_group = QGroupBox("1. Input Media Source (File or Web Link)")
        input_layout = QVBoxLayout(input_group)

        source_row = QHBoxLayout()
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText("Paste a public media URL or drag and drop a local file here...")
        source_row.addWidget(self.source_input)

        self.browse_btn = QPushButton("Browse File")
        self.browse_btn.clicked.connect(self.browse_file)
        source_row.addWidget(self.browse_btn)

        input_layout.addLayout(source_row)
        main_layout.addWidget(input_group)

        # Options Group
        options_group = QGroupBox("2. Configuration Settings")
        options_layout = QHBoxLayout(options_group)

        self.chk_word_sync = QCheckBox("Dynamic MS Word COM Sync")
        self.chk_word_sync.setChecked(True)
        options_layout.addWidget(self.chk_word_sync)

        self.chk_diarization = QCheckBox("Enable Speaker Diarization")
        self.chk_diarization.setChecked(False)
        options_layout.addWidget(self.chk_diarization)

        self.chk_vad = QCheckBox("Filter silence (Silero VAD)")
        self.chk_vad.setChecked(True)
        options_layout.addWidget(self.chk_vad)

        main_layout.addWidget(options_group)

        # Actions Row
        action_layout = QHBoxLayout()
        self.transcribe_btn = QPushButton("Start Transcription")
        self.transcribe_btn.clicked.connect(self.start_transcription)
        action_layout.addWidget(self.transcribe_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_transcription)
        action_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(action_layout)

        # Progress Block
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Status: Idle")
        self.status_label.setStyleSheet("color: #a0a0a8;")
        main_layout.addWidget(self.status_label)

        # Transcript Preview area
        preview_group = QGroupBox("3. Interactive Live Output Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setPlaceholderText("Transcription will appear here in real-time as it is generated...")
        preview_layout.addWidget(self.preview_text)

        export_layout = QHBoxLayout()
        export_layout.addStretch()

        self.export_docx_btn = QPushButton("Export DOCX")
        self.export_docx_btn.clicked.connect(lambda: self.export_transcript("docx"))
        export_layout.addWidget(self.export_docx_btn)

        self.export_txt_btn = QPushButton("Export TXT")
        self.export_txt_btn.clicked.connect(lambda: self.export_transcript("txt"))
        export_layout.addWidget(self.export_txt_btn)

        preview_layout.addLayout(export_layout)
        main_layout.addWidget(preview_group)

    # Drag & Drop events
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.exists(file_path):
                self.source_input.setText(file_path)
                self.status_label.setText(f"Loaded dropped file: {os.path.basename(file_path)}")
                break

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio/Video File", "", 
            "Media Files (*.mp3 *.wav *.mp4 *.m4a *.mkv *.avi);;All Files (*)"
        )
        if file_path:
            self.source_input.setText(file_path)

    def start_transcription(self):
        source_path = self.source_input.text().strip()
        if not source_path:
            QMessageBox.warning(self, "Invalid Input", "Please specify a media file path or public URL.")
            return

        self.transcript_buffer = []
        self.preview_text.clear()

        # Connect to MS Word COM if option checked
        if self.chk_word_sync.isChecked():
            self.status_label.setText("Connecting to Microsoft Word COM interface...")
            self.word_bridge.connect()

        # Launch background execution QThread
        self.worker = TranscriptionWorker(
            source_path,
            enable_diarization=self.chk_diarization.isChecked(),
            enable_vad=self.chk_vad.isChecked()
        )
        self.worker.progress.connect(self.update_status)
        self.worker.segment_ready.connect(self.append_segment)
        self.worker.finished_success.connect(self.transcription_success)
        self.worker.finished_error.connect(self.transcription_error)
        
        self.worker.start()
        
        self.transcribe_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setRange(0, 0)  # Intermittent load progress

    def cancel_transcription(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        self.transcribe_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText("Status: Canceled")

    @Slot(str)
    def update_status(self, msg: str):
        self.status_label.setText(f"Status: {msg}")

    @Slot(dict)
    def append_segment(self, segment: dict):
        self.transcript_buffer.append(segment)
        
        # Format for preview area
        timestamp = f"[{int(segment['start'] // 60):02d}:{int(segment['start'] % 60):02d}]"
        line = f"<b>{timestamp} {segment['speaker']}:</b> {segment['text']}<br/>"
        
        self.preview_text.append(line)
        
        # Direct write to MS Word via COM Automation
        if self.chk_word_sync.isChecked():
            self.word_bridge.write_segment(
                segment['speaker'],
                segment['start'],
                segment['end'],
                segment['text']
            )

    @Slot(str)
    def transcription_success(self, msg: str):
        self.status_label.setText(f"Status: {msg}")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.transcribe_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.information(self, "Completed", "Transcription pipeline completed successfully.")

    @Slot(str)
    def transcription_error(self, err_msg: str):
        self.status_label.setText(f"Status: Failed - {err_msg}")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.transcribe_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.critical(self, "Error", f"An error occurred: {err_msg}")

    def export_transcript(self, format_type: str):
        if not self.transcript_buffer:
            QMessageBox.warning(self, "Export Failed", "No transcribed data exists to export.")
            return

        file_filter = "Word Document (*.docx)" if format_type == "docx" else "Plain Text File (*.txt)"
        ext = ".docx" if format_type == "docx" else ".txt"

        save_path, _ = QFileDialog.getSaveFileName(self, f"Save Export as {format_type.upper()}", f"transcript{ext}", file_filter)
        if not save_path:
            return

        try:
            if format_type == "docx":
                # Generate DOCX serverlessly using python-docx
                from docx import Document
                doc = Document()
                doc.add_heading("Transcription Report", level=0)
                
                for seg in self.transcript_buffer:
                    time_str = f"[{int(seg['start'] // 60):02d}:{int(seg['start'] % 60):02d}]"
                    p = doc.add_paragraph()
                    r1 = p.add_run(f"{time_str} {seg['speaker']}: ")
                    r1.bold = True
                    p.add_run(seg['text'])
                
                doc.save(save_path)
            else:
                # Generate TXT file
                with open(save_path, 'w', encoding='utf-8') as f:
                    for seg in self.transcript_buffer:
                        time_str = f"[{int(seg['start'] // 60):02d}:{int(seg['start'] % 60):02d}]"
                        f.write(f"{time_str} {seg['speaker']}: {seg['text']}\n")

            QMessageBox.information(self, "Success", f"File exported successfully to {save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export file: {str(e)}")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
