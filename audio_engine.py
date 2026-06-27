"""
audio_engine.py — Síntese de áudio em tempo real.

- Tons binaurais (frequência independente por canal L/R)
- Ruídos coloridos gerados proceduralmente: white, pink, brown, green
- Sons ambientes sintetizados (chuva, mar, fogo, vento, etc.) — sem arquivos externos
- Fade in/out, crossfade, volume por camada
- Áudio espacial (panning lento L<->R) opcional por camada
- Tudo somado num único stream estéreo de 48 kHz

Não depende de arquivos .wav/.mp3 — todos os sons são gerados por DSP,
então o app é totalmente portátil (um arquivo só, sem assets).
"""
import numpy as np
import threading

try:
    import sounddevice as sd
    HAVE_SD = True
except Exception:
    HAVE_SD = False

SR = 48000
BLOCK = 1024


# ─────────────────────────────────────────────────────────── geradores ───────
class _NoiseState:
    """Mantém o estado dos filtros para gerar ruído contínuo sem emendas."""
    def __init__(self):
        # pink (Voss-McCartney / filtro)
        self.pink_b = np.zeros(7)
        # brown (integrador)
        self.brown_last = 0.0
        # green (band-passed ~ centrado em ~500 Hz)
        self.green_lp = 0.0
        self.green_hp = 0.0


def _white(n):
    return np.random.uniform(-1, 1, n).astype(np.float32)


def _pink(n, st):
    # filtro de Paul Kellet
    white = _white(n)
    out = np.empty(n, dtype=np.float32)
    b = st.pink_b
    for i in range(n):
        w = white[i]
        b[0] = 0.99886*b[0] + w*0.0555179
        b[1] = 0.99332*b[1] + w*0.0750759
        b[2] = 0.96900*b[2] + w*0.1538520
        b[3] = 0.86650*b[3] + w*0.3104856
        b[4] = 0.55000*b[4] + w*0.5329522
        b[5] = -0.7616*b[5] - w*0.0168980
        out[i] = b[0]+b[1]+b[2]+b[3]+b[4]+b[5]+b[6]+w*0.5362
        b[6] = w*0.115926
    return (out*0.11).astype(np.float32)


def _brown(n, st):
    white = _white(n)
    out = np.empty(n, dtype=np.float32)
    last = st.brown_last
    for i in range(n):
        last = (last + white[i]*0.02)
        last = max(-1, min(1, last))
        out[i] = last
    st.brown_last = last
    return (out*2.5).astype(np.float32)


def _green(n, st):
    # ruído "verde" ~ ruído centrado na faixa média, mais suave
    white = _white(n)
    out = np.empty(n, dtype=np.float32)
    lp, hp = st.green_lp, st.green_hp
    a_lp, a_hp = 0.04, 0.0008
    for i in range(n):
        lp += a_lp*(white[i]-lp)        # low-pass
        hp += a_hp*(lp-hp)              # remove DC bem grave
        out[i] = lp - hp
    st.green_lp, st.green_hp = lp, hp
    return (out*4.0).astype(np.float32)


# ─────────────────────────────────────────────── sons ambientes sintéticos ───
# Cada som é uma função(n, state)-> array mono [-1..1].
# Usam ruído colorido + modulação para imitar texturas reais.

class _AmbState:
    def __init__(self):
        self.noise = _NoiseState()
        self.phase = 0.0
        self.env   = 0.0
        self.t     = 0
        self.drops = []     # para chuva/gotas
        self.lp = 0.0
        self.lp2 = 0.0


def _amb_rain(n, st):
    base = _pink(n, st.noise)
    # adiciona estalos de gotas aleatórios
    out = base * 0.6
    if np.random.random() < 0.5:
        idx = np.random.randint(0, n)
        out[idx:idx+3] += np.random.uniform(0.2, 0.5)
    # leve low-pass para soar "molhado"
    lp = st.lp
    for i in range(n):
        lp += 0.2*(out[i]-lp); out[i] = lp
    st.lp = lp
    return out*0.9


def _amb_storm(n, st):
    rain = _amb_rain(n, st)
    # trovão ocasional: rajada grave
    if np.random.random() < 0.004:
        st.env = 1.0
    if st.env > 0.001:
        t = np.arange(n)
        rumble = np.sin(2*np.pi*45*(t/SR)+st.phase) * st.env
        st.phase = (st.phase + 2*np.pi*45*n/SR) % (2*np.pi)
        st.env *= 0.9995
        rain = rain*0.7 + rumble.astype(np.float32)*0.6
    return rain


def _amb_sea(n, st):
    # ondas: brown noise modulado por envelope lento (respiração das ondas)
    b = _brown(n, st.noise)
    t = np.arange(n) + st.t
    st.t += n
    env = 0.5 + 0.5*np.sin(2*np.pi*0.08*(t/SR))   # ~12s por onda
    return (b*env*0.5).astype(np.float32)


def _amb_waterfall(n, st):
    return _white(n)*0.25 + _pink(n, st.noise)*0.5


def _amb_river(n, st):
    w = _pink(n, st.noise)
    t = np.arange(n) + st.t; st.t += n
    env = 0.7 + 0.3*np.sin(2*np.pi*0.3*(t/SR))
    return (w*env*0.5).astype(np.float32)


def _amb_forest(n, st):
    bg = _green(n, st.noise)*0.3
    # cantos de pássaros ocasionais (chirp)
    if np.random.random() < 0.02:
        st.env = 1.0; st.phase = 0
    if st.env > 0.01:
        t = np.arange(n)
        f = 2500 + 800*np.sin(2*np.pi*8*(t/SR))
        chirp = np.sin(2*np.pi*f*(t/SR)) * st.env * 0.15
        st.env *= 0.95
        bg = bg + chirp.astype(np.float32)
    return bg


def _amb_fire(n, st):
    # crepitar: brown grave + estalos
    base = _brown(n, st.noise)*0.4
    if np.random.random() < 0.3:
        idx = np.random.randint(0, n)
        base[idx:idx+2] += np.random.uniform(0.3, 0.7)
    return base


def _amb_wind(n, st):
    w = _pink(n, st.noise)
    t = np.arange(n) + st.t; st.t += n
    env = 0.4 + 0.6*np.abs(np.sin(2*np.pi*0.05*(t/SR)))
    return (w*env*0.6).astype(np.float32)


def _amb_fan(n, st):
    # ventilador: brown + leve componente periódica
    base = _brown(n, st.noise)*0.5
    t = np.arange(n) + st.t; st.t += n
    hum = 0.08*np.sin(2*np.pi*60*(t/SR))
    return (base + hum).astype(np.float32)


def _amb_ac(n, st):
    """Ar condicionado: hum de pá de ventilador com harmônicos, fluxo de ar."""
    t = np.arange(n) + st.t; st.t += n
    fan = _brown(n, st.noise) * 0.40
    blade = (np.sin(2*np.pi * 72 * (t/SR) + st.phase) * 0.07 +
             np.sin(2*np.pi * 144 * (t/SR) + st.phase * 2) * 0.03).astype(np.float32)
    st.phase = (st.phase + 2*np.pi * 72 * n/SR) % (2*np.pi)
    airflow = _white(n) * 0.04
    mod = (0.95 + 0.05 * np.sin(2*np.pi * 0.006 * (t/SR))).astype(np.float32)
    return np.clip((fan + blade + airflow) * mod, -1, 1).astype(np.float32)


def _amb_train(n, st):
    """Trem em movimento: clickety-clack rítmico de trilho, rumble, vento, apito."""
    t = np.arange(n) + st.t; st.t += n
    rumble = _brown(n, st.noise) * 0.38
    # Juntas do trilho a ~2.5 Hz — decaimento exponencial curto após cada batida
    click_period = SR / 2.5
    phase1 = t % click_period
    phase2 = (t + click_period * 0.55) % click_period
    decay = 220.0
    clicks  = (np.exp(-phase1 / decay)          * (phase1 < decay)).astype(np.float32) * 0.55
    clicks2 = (np.exp(-phase2 / (decay * 0.75)) * (phase2 < decay * 0.75)).astype(np.float32) * 0.40
    wind_env = (0.75 + 0.25 * np.sin(2*np.pi * 0.06 * (t/SR))).astype(np.float32)
    wind = _pink(n, st.noise) * wind_env * 0.18
    whistle = np.zeros(n, dtype=np.float32)
    if np.random.random() < 0.0007:
        st.env = 0.65
    if st.env > 0.01:
        whistle = (np.sin(2*np.pi * 880 * (t/SR) + st.phase) * st.env * 0.18).astype(np.float32)
        st.phase = (st.phase + 2*np.pi * 880 * n/SR) % (2*np.pi)
        st.env *= 0.9990
    return np.clip(rumble + clicks * 0.38 + clicks2 * 0.28 + wind + whistle, -1, 1).astype(np.float32)


def _amb_plane(n, st):
    """Avião: motor turbofan com drone harmônico, ar de cabine."""
    t = np.arange(n) + st.t; st.t += n
    engine = _brown(n, st.noise) * 0.44
    drone = (np.sin(2*np.pi * 103 * (t/SR) + st.phase) * 0.09 +
             np.sin(2*np.pi * 206 * (t/SR) + st.phase * 2) * 0.04 +
             np.sin(2*np.pi * 309 * (t/SR) + st.phase * 3) * 0.02).astype(np.float32)
    st.phase = (st.phase + 2*np.pi * 103 * n/SR) % (2*np.pi)
    cabin = _pink(n, st.noise) * 0.10
    mod = (1.0 + 0.04 * np.sin(2*np.pi * 0.025 * (t/SR))).astype(np.float32)
    return np.clip((engine + drone + cabin) * mod, -1, 1).astype(np.float32)


def _amb_cafe(n, st):
    """Café: murmúrio de vozes com AM na taxa de sílabas, steam, tinido de xícaras."""
    t = np.arange(n) + st.t; st.t += n
    speech = _pink(n, st.noise)
    # Três "vozes" sobrepostas com modulação na faixa de sílabas (3-6 Hz)
    v1 = np.abs(np.sin(2*np.pi * 4.3 * (t/SR) + 0.3)) * 0.55 + 0.18
    v2 = np.abs(np.sin(2*np.pi * 3.6 * (t/SR) + 2.1)) * 0.45 + 0.15
    v3 = np.abs(np.sin(2*np.pi * 5.7 * (t/SR) + 4.5)) * 0.35 + 0.12
    voices = speech * (v1 + v2 + v3).astype(np.float32) * 0.20
    warmth = _brown(n, st.noise) * 0.07
    if np.random.random() < 0.003:
        st.env = np.random.uniform(0.5, 0.9)
    steam = np.zeros(n, dtype=np.float32)
    if st.env > 0.01:
        steam = _white(n) * st.env * 0.14
        st.env *= 0.9975
    clink = np.zeros(n, dtype=np.float32)
    if np.random.random() < 0.006:
        idx = int(np.random.randint(0, max(1, n - 64)))
        length = min(64, n - idx)
        tt = np.arange(length)
        freq = float(np.random.uniform(1800, 3500))
        clink[idx:idx+length] = (np.sin(2*np.pi * freq * (tt/SR)) * np.exp(-tt / 9.0) * 0.28).astype(np.float32)
    return np.clip(voices + warmth + steam + clink, -1, 1).astype(np.float32)


def _amb_library(n, st):
    """Biblioteca: silêncio quase total, zumbido elétrico, farfar de páginas, passos distantes."""
    t = np.arange(n) + st.t; st.t += n
    air = _pink(n, st.noise) * 0.06
    hum = (np.sin(2*np.pi * 60 * (t/SR) + st.phase) * 0.012).astype(np.float32)
    st.phase = (st.phase + 2*np.pi * 60 * n/SR) % (2*np.pi)
    if np.random.random() < 0.003:
        st.env = 0.25
    rustle = np.zeros(n, dtype=np.float32)
    if st.env > 0.005:
        rustle = _white(n) * st.env * 0.10
        st.env *= 0.982
    thump = np.zeros(n, dtype=np.float32)
    if np.random.random() < 0.002:
        idx = int(np.random.randint(0, max(1, n - 40)))
        length = min(40, n - idx)
        tt = np.arange(length)
        thump[idx:idx+length] = (np.sin(2*np.pi * 75 * (tt/SR)) * np.exp(-tt / 7.0) * 0.10).astype(np.float32)
    return np.clip(air + hum + rustle + thump, -1, 1).astype(np.float32)


def _amb_city(n, st):
    """Cidade à noite: tráfego com swell de carros passando, buzina ocasional."""
    t = np.arange(n) + st.t; st.t += n
    traffic = _brown(n, st.noise) * 0.30
    swell = (0.55 + 0.45 * np.abs(np.sin(2*np.pi * 0.09 * (t/SR) + 0.7))).astype(np.float32)
    traffic *= swell
    urban = _pink(n, st.noise) * 0.10
    if np.random.random() < 0.002:
        st.env = 0.85
    horn = np.zeros(n, dtype=np.float32)
    if st.env > 0.01:
        horn = (np.sin(2*np.pi * 490 * (t/SR) + st.phase) * st.env * 0.10).astype(np.float32)
        st.phase = (st.phase + 2*np.pi * 490 * n/SR) % (2*np.pi)
        st.env *= 0.9975
    return np.clip(traffic + urban + horn, -1, 1).astype(np.float32)


# catálogo: id -> (rótulo, função, espacial_default)
AMBIENTS = {
    "rain":      ("Chuva",            _amb_rain),
    "storm":     ("Tempestade",       _amb_storm),
    "sea":       ("Mar",              _amb_sea),
    "waterfall": ("Cachoeira",        _amb_waterfall),
    "river":     ("Rio",              _amb_river),
    "forest":    ("Floresta",         _amb_forest),
    "fire":      ("Lareira",          _amb_fire),
    "wind":      ("Vento",            _amb_wind),
    "fan":       ("Ventilador",       _amb_fan),
    "ac":        ("Ar condicionado",  _amb_ac),
    "train":     ("Trem",             _amb_train),
    "plane":     ("Avião",            _amb_plane),
    "cafe":      ("Café",             _amb_cafe),
    "library":   ("Biblioteca",       _amb_library),
    "city":      ("Cidade à noite",   _amb_city),
    "white":     ("White Noise",      lambda n, st: _white(n)*0.3),
    "pink":      ("Pink Noise",       lambda n, st: _pink(n, st.noise)),
    "brown":     ("Brown Noise",      lambda n, st: _brown(n, st.noise)),
    "green":     ("Green Noise",      lambda n, st: _green(n, st.noise)),
}


# ─────────────────────────────────────────────────────────────── camada ──────
class Layer:
    """Uma camada de som ambiente com volume, mute, fade e panning espacial."""
    def __init__(self, sound_id):
        self.id      = sound_id
        self.fn      = AMBIENTS[sound_id][1]
        self.state   = _AmbState()
        self.target_vol = 0.5
        self.cur_vol    = 0.0     # sobe via fade
        self.muted   = False
        self.spatial = False
        self.pan_phase = 0.0

    def render(self, n):
        mono = self.fn(n, self.state)
        # fade de volume suave
        eff_target = 0.0 if self.muted else self.target_vol
        # interpola cur_vol -> eff_target ao longo do bloco
        vols = np.linspace(self.cur_vol, eff_target, n, dtype=np.float32)
        self.cur_vol = eff_target
        mono = mono * vols

        if self.spatial:
            # panning lento entre L e R
            t = np.arange(n)
            pan = 0.5 + 0.5*np.sin(self.pan_phase + 2*np.pi*0.05*(t/SR))
            self.pan_phase = (self.pan_phase + 2*np.pi*0.05*n/SR) % (2*np.pi)
            left  = mono * (1-pan)
            right = mono * pan
        else:
            left = right = mono
        return left, right


# ─────────────────────────────────────────────────────── motor principal ─────
class AudioEngine:
    def __init__(self):
        self.sr = SR
        self.playing = False
        self._stream = None
        self._lock = threading.Lock()

        # binaural
        self.binaural_on = True
        self.base = 200.0
        self.beat = 6.0
        self.binaural_vol = 0.5
        self._pl = 0.0
        self._pr = 0.0

        # master fade (in/out global)
        self.master = 0.0
        self.master_target = 1.0
        self.fade_rate = 0.0008          # por amostra (~2.5s fade)

        # camadas ambientes
        self.layers = {}                 # id -> Layer

    # ── binaural ────────────────────────────────────────────────────────────
    def set_binaural(self, base=None, beat=None, vol=None, on=None):
        with self._lock:
            if base is not None: self.base = float(base)
            if beat is not None: self.beat = float(beat)
            if vol  is not None: self.binaural_vol = float(vol)/100.0
            if on   is not None: self.binaural_on = bool(on)

    # ── camadas ─────────────────────────────────────────────────────────────
    def add_layer(self, sound_id):
        with self._lock:
            if sound_id not in self.layers:
                self.layers[sound_id] = Layer(sound_id)

    def remove_layer(self, sound_id):
        with self._lock:
            self.layers.pop(sound_id, None)

    def set_layer_vol(self, sound_id, vol):
        with self._lock:
            if sound_id in self.layers:
                self.layers[sound_id].target_vol = float(vol)/100.0

    def set_layer_mute(self, sound_id, muted):
        with self._lock:
            if sound_id in self.layers:
                self.layers[sound_id].muted = bool(muted)

    def set_layer_spatial(self, sound_id, spatial):
        with self._lock:
            if sound_id in self.layers:
                self.layers[sound_id].spatial = bool(spatial)

    def active_layers(self):
        with self._lock:
            return list(self.layers.keys())

    def clear_layers(self):
        with self._lock:
            self.layers.clear()

    # ── master volume (para sleep mode / fade out) ──────────────────────────
    def fade_to(self, target, seconds=2.5):
        self.master_target = max(0.0, min(1.0, target))
        self.fade_rate = 1.0 / (seconds * self.sr) if seconds > 0 else 1.0

    # ── callback de áudio ───────────────────────────────────────────────────
    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            base, beat, bvol, bon = self.base, self.beat, self.binaural_vol, self.binaural_on
            layers = list(self.layers.values())

        left  = np.zeros(frames, dtype=np.float32)
        right = np.zeros(frames, dtype=np.float32)

        # binaural
        if bon and bvol > 0:
            t = np.arange(frames)/self.sr
            left  += (np.sin(2*np.pi*base*t + self._pl)*bvol*0.5).astype(np.float32)
            right += (np.sin(2*np.pi*(base+beat)*t + self._pr)*bvol*0.5).astype(np.float32)
            self._pl = (self._pl + 2*np.pi*base*frames/self.sr) % (2*np.pi)
            self._pr = (self._pr + 2*np.pi*(base+beat)*frames/self.sr) % (2*np.pi)

        # camadas ambientes
        for lyr in layers:
            l, r = lyr.render(frames)
            left  += l
            right += r

        # master fade (envelope por amostra)
        m = np.empty(frames, dtype=np.float32)
        cur = self.master
        for i in range(frames):
            if cur < self.master_target:
                cur = min(self.master_target, cur + self.fade_rate)
            elif cur > self.master_target:
                cur = max(self.master_target, cur - self.fade_rate)
            m[i] = cur
        self.master = cur
        left  *= m
        right *= m

        # limitador suave (evita clipping ao somar muitas camadas)
        np.clip(left,  -1, 1, out=left)
        np.clip(right, -1, 1, out=right)

        outdata[:, 0] = left
        outdata[:, 1] = right

    # ── controle ────────────────────────────────────────────────────────────
    def start(self):
        if not HAVE_SD:
            return False
        self.stop()
        self.master = 0.0
        self.master_target = 1.0
        self.fade_to(1.0, 2.0)
        self._stream = sd.OutputStream(
            samplerate=self.sr, channels=2, dtype='float32',
            callback=self._callback, blocksize=BLOCK)
        self._stream.start()
        self.playing = True
        return True

    def stop(self):
        if self._stream:
            self._stream.stop(); self._stream.close(); self._stream = None
        self.playing = False
        self._pl = self._pr = 0.0

    def stop_with_fade(self, seconds=3.0, on_done=None):
        """Fade out e então para. Roda numa thread para não travar a UI."""
        import time
        self.fade_to(0.0, seconds)
        def _worker():
            time.sleep(seconds + 0.3)
            self.stop()
            if on_done: on_done()
        threading.Thread(target=_worker, daemon=True).start()
