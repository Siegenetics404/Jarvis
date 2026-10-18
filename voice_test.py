import sounddevice as sd
from piper import PiperVoice

voice = PiperVoice.load("en_GB-alan-medium.onnx")

def speak(text):
    for chunk in voice.synthesize(text):
        sd.play(chunk.audio_float_array, chunk.sample_rate)
        sd.wait()

speak("Good evening, sir. All systems are online. Shall I open Docker Desktop?")