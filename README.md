# Jarvis

A local voice assistant: wake word, Whisper speech-to-text, a local LLM through LM Studio, and Kokoro text-to-speech.

## Setup

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Download the voice model into the project folder:

```bat
curl -L -O https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -O https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Start the LM Studio server (Developer tab) with a tool-capable model loaded, then run:

```bat
python jarvis.py
```
