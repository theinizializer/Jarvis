"""Physical input grouping and measurable recording-quality checks."""
import math
import re


def physical_microphones(microphones):
    physical, aliases = {}, []
    # Prefer the sound server route when it and ALSA expose the same hardware.
    for mic in sorted(microphones, key=lambda item:item.get('source') != 'pactl'):
        name = mic.get('name', '')
        if '.monitor' in name or mic.get('monitor'):
            continue
        identity = mic.get('physical_id')
        match = re.search(r'\(hw:(\d+),(\d+)\)', name)
        if match:
            identity = f'alsa:{match[1]}:{match[2]}'
        if identity:
            physical.setdefault(identity, {**mic, 'physical_id': identity})
        elif name.casefold() in {'default', 'pipewire', 'pulse'}:
            aliases.append(mic)
        elif mic.get('source') == 'pactl' and mic.get('physical', True):
            physical.setdefault(mic.get('pa_name', name), mic)
        elif mic.get('source') == 'sd' and not re.search(r'upmix|vdownmix|speex|samplerate|lavrate|surround', name, re.I):
            physical.setdefault(name, mic)
    return list(physical.values()) if physical else aliases


def confirmed_microphones(results):
    working = {}
    for item in results:
        if not item['quality'].get('ok'):
            continue
        mic = item['microphone']
        name = mic.get('name', '')
        identity = mic.get('physical_id') or ('default-route' if name.casefold() in {'default', 'pulse', 'pipewire'} else mic.get('pa_name', name))
        working.setdefault(identity, mic)
    return list(working.values())


class VoiceActivity:
    def __init__(self):
        import webrtcvad
        self.detector = webrtcvad.Vad(2)

    def speech_seconds(self, signal, samplerate):
        import numpy as np
        pcm = (np.clip(signal, -1, 1) * 32767).astype('<i2').tobytes()
        size = int(samplerate * .03) * 2
        frames = (pcm[start:start+size] for start in range(0, len(pcm)-size+1, size))
        return sum(self.detector.is_speech(frame, samplerate) for frame in frames) * .03


def recording_report(audio, samplerate=16000, minimum_duration=10, minimum_voice=3):
    import numpy as np
    result = {'ok': False, 'duration': 0., 'voice_seconds': 0., 'rms_dbfs': -90., 'clipped_fraction': 0.,
              'message': 'La registrazione è vuota. Controlla il microfono e riprova.'}
    if audio is None:
        return result
    signal = np.asarray(audio, dtype=np.float32)
    if signal.ndim != 1 or not signal.size or not np.all(np.isfinite(signal)):
        return result
    duration = signal.size / samplerate
    variation = float(np.sqrt(np.mean((signal - float(signal.mean())) ** 2)))
    dbfs = 20 * math.log10(max(variation, .00003))
    clipped = float(np.mean(np.abs(signal) >= .995))
    result.update(duration=round(duration, 2), rms_dbfs=round(dbfs, 1), clipped_fraction=clipped)
    if duration < minimum_duration - .05:
        result['message'] = f'Registrazione interrotta dopo {duration:.1f} secondi. Riprova.'
        return result
    if dbfs < -65:
        result['message'] = 'Il microfono riceve silenzio o un segnale troppo debole. Parla vicino al microfono e controlla che sia attivo.'
        return result
    if clipped > .02:
        result['message'] = 'Il segnale è distorto: abbassa il volume di ingresso o allontanati dal microfono.'
        return result
    try:
        speech = VoiceActivity().speech_seconds(signal, samplerate)
    except (ImportError, ValueError, RuntimeError):
        result['message'] = 'Non è stato possibile verificare la presenza di voce. Controlla i componenti vocali.'
        return result
    result['voice_seconds'] = round(speech, 2)
    if speech < minimum_voice:
        result['message'] = 'Arriva audio, ma non abbastanza voce riconoscibile. Riprova parlando normalmente durante tutta la prova.'
        return result
    result.update(ok=True, message=f'Registrazione riuscita · {duration:.1f} s · voce rilevata per {speech:.1f} s')
    return result
