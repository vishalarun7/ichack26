from elevenlabs.client import ElevenLabs
from elevenlabs import play

client = ElevenLabs(
    api_key="sk_5ed9561f6e2dd28330d594c8e581714735a89f60028d6cc5"
)

audio_generator = client.text_to_speech.convert(
    voice_id="hpp4J3VqNfWAUOO0d1Us",
    text="hello hello bye",
    model_id="eleven_monolingual_v1")

audio_bytes = b"".join(audio_generator)

with open("output.mp3","wb") as f:
    f.write(audio_bytes)
