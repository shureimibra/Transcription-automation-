# Multilingual AI Transcription Platform

This repository contains a high-performance, dynamic multilingual AI transcription system featuring real-time speech-to-text, dynamic 3-layer language validation, local files ingestion, and dual-mode VAD filters.

## System Architecture

- `/backend`: Django + ASGI/Daphne server handling database mappings, background audio download/extraction, and the `faster-whisper` inference pipeline.
- `/frontend`: React + Vite client dashboard providing job submission (local file upload or web URLs), target language configuration, and interactive transcription diff previews.
- `/desktop_agent`: PySide6 Windows client facilitating MS Word integration and local COM macro automation.

---

## Developer Setup Instructions

### 1. Pre-requisites & System Tools
You must have the following system libraries installed on your machine:
* **Python 3.10+**: Ensure Python is added to your environment `PATH`.
* **Node.js (v18+)**: Recommended for Vite bundler execution.
* **FFmpeg**: Required by Whisper and yt-dlp for media demuxing and conversion.
  * **Windows**: Run `winget install Gyan.FFmpeg` or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `bin/` folder to your system `PATH`.
  * **Mac**: Run `brew install ffmpeg`.
  * **Linux**: Run `sudo apt install ffmpeg`.

---

### 2. Backend Installation (Django)

1. Open a terminal in the project root folder.
2. Create and activate a Python virtual environment:
   ```bash
   # Windows
   python -m venv .venv
   .venv\Scripts\activate

   # Mac/Linux
   python -m venv .venv
   source .venv/bin/activate
   ```
3. Install Python dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
4. Run Django migrations to initialize the SQLite database:
   ```bash
   python backend/manage.py migrate
   ```
5. Launch the Daphne development backend server (bound to port `8002` as configured on the Vite frontend):
   ```bash
   python backend/manage.py runserver 8002
   ```

---

### 3. Frontend Installation (React + Vite)

1. Open a new terminal in the `frontend` folder:
   ```bash
   cd frontend
   ```
2. Install frontend node modules:
   ```bash
   npm install
   ```
3. Launch the Vite development client server:
   ```bash
   npm run dev
   ```
4. Open your browser and navigate to the printed URL (usually [http://localhost:5173](http://localhost:5173)).

---

## Running Verification Tests
To confirm that your backend, Dynamic Language Registry, Layer 3 validators, and VAD controls are working correctly without regression:
```bash
# Ensure your virtual environment is active
python backend/manage.py test apps.transcriptions
```
