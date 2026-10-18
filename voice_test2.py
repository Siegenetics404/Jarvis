import sounddevice as sd
from kokoro_onnx import Kokoro

kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

for v in ["bm_george", "bm_fable", "bm_lewis", "bm_daniel"]:
    text = f"Good evening, sir. This is {v.split('_')[1]}. All systems are online. Shall I open Docker Desktop?"
    samples, rate = kokoro.create(text, voice=v, speed=1.0, lang="en-gb")
    sd.play(samples, rate)
    sd.wait()