import logging
import sys

logger = logging.getLogger("WordCOMBridge")

class WordCOMBridge:
    """
    Manages the COM interface connection with Microsoft Word.
    Works on Windows machines with Office installed. Falls back gracefully on non-Windows platforms.
    """
    def __init__(self):
        self.word_app = None
        self.doc = None
        self.is_connected = False

    def connect(self) -> bool:
        """
        Attempts to bind to a running instance of MS Word, or starts a new one.
        """
        if sys.platform != "win32":
            logger.warning("COM automation is only supported on Windows platforms. Running in mock/terminal fallback mode.")
            self.is_connected = False
            return False

        try:
            import win32com.client
            # Try to grab current Word instance
            try:
                self.word_app = win32com.client.GetActiveObject("Word.Application")
                logger.info("Connected to existing MS Word instance.")
            except Exception:
                self.word_app = win32com.client.Dispatch("Word.Application")
                logger.info("Started new MS Word application instance.")
            
            # Make sure Word is visible to the user
            self.word_app.Visible = True
            
            # Attach to active document or add a new one
            if self.word_app.Documents.Count > 0:
                self.doc = self.word_app.ActiveDocument
                logger.info("Bound to active Word document.")
            else:
                self.doc = self.word_app.Documents.Add()
                logger.info("Created new Word document.")
                
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MS Word COM interface: {e}")
            self.is_connected = False
            return False

    def write_segment(self, speaker: str, start_time: float, end_time: float, text: str):
        """
        Writes a single transcription segment line directly into the active Word document at the cursor.
        """
        time_str = f"[{int(start_time // 60):02d}:{int(start_time % 60):02d}]"
        line_text = f"\n{time_str} {speaker}: {text}\n"

        if self.is_connected and self.word_app is not None:
            try:
                selection = self.word_app.Selection
                selection.TypeText(line_text)
                logger.info(f"Wrote to Word: {line_text.strip()}")
            except Exception as e:
                logger.error(f"Error typing segment to MS Word: {e}")
        else:
            # Fallback output
            print(f"[LIVE WORD COM FALLBACK] {line_text.strip()}")

    def save_and_close(self, file_path: str = None):
        """
        Optionally saves the active document and disconnects.
        """
        if self.is_connected and self.doc is not None:
            try:
                if file_path:
                    self.doc.SaveAs(file_path)
                    logger.info(f"Document saved to {file_path}")
                self.is_connected = False
            except Exception as e:
                logger.error(f"Error saving document: {e}")
