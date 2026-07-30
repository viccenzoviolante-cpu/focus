"""
audio_engine.py — Síntese de áudio em tempo real.

- Tons binaurais (frequência independente por canal L/R)
- Ruídos coloridos gerados proceduralmente: white, pink, brown, green
- Sons ambientes sintetizados (chuva, mar, fogo, vento, etc.)
- EventScheduler: cada ambiente "vivo" (floresta, chuva, vento, café, biblioteca,
  trem, cidade) soma uma textura contínua de base com pequenos eventos
  independentes (pássaro, gota, porta, moto...) que nascem, tocam e morrem
  sozinhos — com posição estéreo, distância, pitch e duração próprios.
- Híbrido: alguns eventos e camadas de fundo usam amostras reais CC0 (pasta
  sounds/, baixadas via tools/fetch_sounds.py) misturadas com a síntese
  procedural — ex.: pássaro pode ser sintetizado OU uma gravação real; o
  murmúrio de vozes de um "lugar" pode ser real, num idioma específico.
  Sem sounds/ ou sem a lib soundfile, essas camadas simplesmente não tocam
  e tudo o mais continua funcionando (fallback silencioso, ver HAVE_SF).
- Fade in/out, crossfade, volume por camada
- Áudio espacial (panning lento L<->R) opcional por camada
- Tudo somado num único stream estéreo de 48 kHz
"""
import os
import numpy as np
import threading
import datetime as _dt

try:
    import sounddevice as sd
    HAVE_SD = True
except Exception:
    HAVE_SD = False

try:
    import soundfile as sf
    HAVE_SF = True
except Exception:
    HAVE_SF = False

SR = 48000
BLOCK = 1024
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")


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


# ═══════════════════════════════════ eventos sonoros (vida dos ambientes) ═════
# Filosofia: em vez de cada ambiente gerar tudo dentro de uma função monolítica,
# uma textura contínua de base (ar, ruído de fundo) toca o tempo inteiro, e
# pequenos SoundEvents independentes nascem, tocam e morrem por conta própria —
# cada um com posição estéreo, distância, pitch e duração. O EventScheduler
# decide, a cada bloco de áudio, se algum evento novo nasce (chance/cooldown/
# concorrência máxima). Isso é o que faz um ambiente soar vivo em vez de um
# loop repetitivo.

class SoundEvent:
    """Evento sonoro efêmero. Subclasses implementam _synth(n) -> mono float32."""
    def __init__(self, pan=0.0, distance=0.3, pitch=1.0, gain=1.0):
        self.pan      = max(-1.0, min(1.0, pan))
        self.distance = max(0.0, min(1.0, distance))   # 0 perto .. 1 longe
        self.pitch    = pitch
        self.gain     = gain
        self.alive    = True
        self._lp      = 0.0   # estado do filtro de distância

    def _synth(self, n):
        """Retorna array mono [-1..1] já com timbre + envelope. Deve setar
        self.alive=False quando o evento tiver acabado."""
        raise NotImplementedError

    def render(self, n):
        mono = self._synth(n)
        if mono is None:
            return None
        # distância: mais longe = mais baixo e mais abafado (ar absorve agudos)
        vol = self.gain * (1.0 - 0.75*self.distance)
        mono = mono * vol
        if self.distance > 0.15:
            cutoff = max(0.03, 0.5*(1.0 - self.distance))
            lp = self._lp
            for i in range(len(mono)):
                lp += cutoff*(mono[i]-lp); mono[i] = lp
            self._lp = lp
        # longe = mais centralizado no estéreo (a diferença de fase se perde)
        pan = self.pan * (1.0 - 0.6*self.distance)
        left  = mono * (1.0 - max(0.0, pan))
        right = mono * (1.0 + min(0.0, pan))
        return left.astype(np.float32), right.astype(np.float32)


class EventScheduler:
    """Tabela de eventos possíveis para um ambiente. A cada bloco decide se
    algum novo evento nasce, respeitando chance por bloco, cooldown (em
    segundos, aleatório dentro de uma faixa) e nº máximo simultâneo.
    Specs: {"cls":Classe, "chance":float, "cooldown":(min_s,max_s),
            "max":int, "kwargs":callable()->dict, "hour_mult":callable(h)->float}
    """
    def __init__(self, specs):
        self.specs = specs
        self._cooldown = [0]*len(specs)
        self.events = []

    def _count(self, cls):
        return sum(1 for e in self.events if isinstance(e, cls))

    def _tick(self, n):
        hour = _dt.datetime.now().hour
        for i, spec in enumerate(self.specs):
            if self._cooldown[i] > 0:
                self._cooldown[i] -= n
                continue
            if self._count(spec["cls"]) >= spec.get("max", 2):
                continue
            chance = spec["chance"]
            mult = spec.get("hour_mult")
            if mult:
                chance *= mult(hour)
            if np.random.random() < chance:
                kw = spec.get("kwargs")
                self.events.append(spec["cls"](**(kw() if kw else {})))
                lo, hi = spec["cooldown"]
                self._cooldown[i] = int(np.random.uniform(lo, hi) * SR)

    def render(self, n):
        self._tick(n)
        l = np.zeros(n, dtype=np.float32)
        r = np.zeros(n, dtype=np.float32)
        alive = []
        for e in self.events:
            out = e.render(n)
            if out is not None:
                el, er = out
                l += el; r += er
            if e.alive:
                alive.append(e)
        self.events = alive
        return l, r


def _rand_pan():                      return float(np.random.uniform(-1, 1))
def _rand_dist(near=0.1, far=0.9):    return float(np.random.uniform(near, far))
def _rand_pitch(spread=0.08):         return float(1.0 + np.random.uniform(-spread, spread))

def _mult_daytime(h):
    """Multiplicador de atividade diurna (pássaros): pico de manhã, quase nada à noite."""
    if 5 <= h <= 10:  return 1.6
    if 11 <= h <= 17: return 1.0
    if 18 <= h <= 20: return 0.5
    return 0.05

def _mult_nighttime(h):
    """Multiplicador de atividade noturna (grilos, coruja): quase nulo de dia."""
    if h >= 21 or h <= 5: return 1.4
    if 6 <= h <= 8 or 19 <= h <= 20: return 0.4
    return 0.02


# ── amostras reais (híbrido: síntese + gravação) ───────────────────────────────
_sample_cache = {}

def _load_sample(name):
    """Carrega sounds/<name>.ogg -> mono float32 @ SR, cacheado. None se
    indisponível (lib ausente, arquivo não baixado ainda) — quem chama deve
    tratar isso como "esse som não toca", nunca como erro fatal."""
    if not HAVE_SF:
        return None
    if name in _sample_cache:
        return _sample_cache[name]
    path = os.path.join(SOUNDS_DIR, f"{name}.ogg")
    if not os.path.exists(path):
        _sample_cache[name] = None
        return None
    try:
        data, sr = sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1).astype(np.float32)
        if sr != SR:
            old_idx = np.arange(len(data))
            new_len = max(1, int(len(data) * SR / sr))
            new_idx = np.linspace(0, len(data)-1, new_len)
            data = np.interp(new_idx, old_idx, data).astype(np.float32)
        peak = float(np.abs(data).max()) or 1.0
        data = (data / peak * 0.9).astype(np.float32)
        # fade curto nas pontas: evita clique tanto num único disparo quanto
        # na emenda quando a amostra toca em loop contínuo
        fade = min(len(data)//4, int(0.02*SR))
        if fade > 1:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            data[:fade] *= ramp
            data[-fade:] *= ramp[::-1]
    except Exception:
        data = None
    _sample_cache[name] = data
    return data


def sample_available(name):
    return _load_sample(name) is not None


# ── primitivas reutilizáveis (imitam a física, não o resultado final) ─────────
class NoiseBurst(SoundEvent):
    """Estouro de ruído filtrado com decaimento exponencial — base física para
    gotas, passos, tosse, latido, porta, cadeira etc. (um impacto = ruído
    branco moldado por um filtro e um envelope, igual a um impacto real)."""
    def __init__(self, dur=0.08, decay=0.02, filt="low", cutoff=0.3, amp=0.5, **kw):
        super().__init__(**kw)
        self.len_ = max(1, int(dur*SR)); self.decay_s = decay
        self.filt = filt; self.cutoff = cutoff; self.amp = amp
        self.pos = 0; self._f_lp = 0.0; self._f_hp_lp = 0.0

    def _synth(self, n):
        take = min(self.len_ - self.pos, n)
        if take <= 0:
            self.alive = False; return None
        w = np.random.uniform(-1, 1, take).astype(np.float32)
        tt = (np.arange(take)+self.pos)/SR
        env = np.exp(-tt/self.decay_s).astype(np.float32)
        sig = w*env
        if self.filt in ("low", "band"):
            lp = self._f_lp
            for i in range(take):
                lp += self.cutoff*(sig[i]-lp); sig[i] = lp
            self._f_lp = lp
        if self.filt in ("high", "band"):
            lp = self._f_hp_lp; hp = np.empty(take, dtype=np.float32)
            for i in range(take):
                lp += 0.35*(sig[i]-lp); hp[i] = sig[i]-lp
            self._f_hp_lp = lp; sig = hp
        out = np.zeros(n, dtype=np.float32); out[:take] = sig*self.amp
        self.pos += take
        if self.pos >= self.len_: self.alive = False
        return out


class PulseTrain(SoundEvent):
    """Vários NoiseBursts em sequência com gaps — tosse, passos, escrita,
    grilo, latido: nada na natureza é um único estalo, é sempre uma série."""
    def __init__(self, n_pulses=2, pulse_dur=0.05, decay=0.02, gap=(0.08, 0.18),
                 filt="band", cutoff=0.25, amp=0.4, **kw):
        super().__init__(**kw)
        self.remaining = n_pulses; self.pulse_dur = pulse_dur; self.decay_s = decay
        self.filt = filt; self.cutoff = cutoff; self.amp = amp; self.gap = gap
        self.len_ = max(1, int(pulse_dur*SR))
        self.pos = 0; self.state = "pulse"; self.gap_left = 0
        self._f_lp = 0.0; self._f_hp_lp = 0.0

    def _synth(self, n):
        out = np.zeros(n, dtype=np.float32); i = 0
        while i < n:
            if self.remaining <= 0:
                if i == 0: self.alive = False; return None
                break
            if self.state == "gap":
                take = min(self.gap_left, n-i); self.gap_left -= take; i += take
                if self.gap_left <= 0: self.state = "pulse"; self.pos = 0
                continue
            take = min(self.len_-self.pos, n-i)
            w = np.random.uniform(-1, 1, take).astype(np.float32)
            tt = (np.arange(take)+self.pos)/SR
            env = np.exp(-tt/self.decay_s).astype(np.float32)
            sig = w*env
            if self.filt in ("low", "band"):
                lp = self._f_lp
                for k in range(take): lp += self.cutoff*(sig[k]-lp); sig[k] = lp
                self._f_lp = lp
            if self.filt in ("high", "band"):
                lp = self._f_hp_lp; hp = np.empty(take, dtype=np.float32)
                for k in range(take): lp += 0.35*(sig[k]-lp); hp[k] = sig[k]-lp
                self._f_hp_lp = lp; sig = hp
            out[i:i+take] = sig*self.amp
            self.pos += take; i += take
            if self.pos >= self.len_:
                self.remaining -= 1; self.state = "gap"
                self.gap_left = int(np.random.uniform(*self.gap)*SR)
        return out


class Swell(SoundEvent):
    """Rajada longa: ruído colorido com envelope de subida/descida e filtro
    que se abre com o tempo — vento, chiado de máquina, ônibus, moto passando.
    O pan pode "varrer" o estéreo para simular algo passando por perto."""
    def __init__(self, dur=2.0, cutoff0=0.1, cutoff1=0.35, amp=0.5, pan_sweep=0.0, **kw):
        super().__init__(**kw)
        self.len_ = max(1, int(dur*SR))
        self.c0, self.c1 = cutoff0, cutoff1; self.amp = amp
        self.pan_sweep = pan_sweep; self._pan0 = self.pan
        self.pos = 0; self._lp = 0.0

    def _synth(self, n):
        take = min(self.len_-self.pos, n)
        if take <= 0:
            self.alive = False; return None
        w = np.random.uniform(-1, 1, take).astype(np.float32)
        frac = (self.pos+np.arange(take))/self.len_
        env = np.sin(np.pi*np.clip(frac, 0, 1)).astype(np.float32)
        cutoff = self.c0 + (self.c1-self.c0)*frac
        sig = np.empty(take, dtype=np.float32); lp = self._lp
        for i in range(take):
            lp += float(cutoff[i])*(w[i]-lp); sig[i] = lp
        self._lp = lp
        if take:
            self.pan = self._pan0 + self.pan_sweep*(float(frac[-1])-0.5)*2
        out = np.zeros(n, dtype=np.float32); out[:take] = sig*env*self.amp
        self.pos += take
        if self.pos >= self.len_: self.alive = False
        return out


class Tone(SoundEvent):
    """Tom com vibrato e pitch-bend opcionais — buzina, sirene distante,
    abelha, coruja, apito: física de uma coluna de ar ou corda vibrando."""
    def __init__(self, freq=440, dur=1.0, vibrato_hz=0.0, vibrato_depth=0.0,
                 bend=0.0, amp=0.3, **kw):
        super().__init__(**kw)
        self.freq = freq*self.pitch; self.len_ = max(1, int(dur*SR))
        self.vibrato_hz = vibrato_hz; self.vibrato_depth = vibrato_depth
        self.bend = bend; self.amp = amp; self.pos = 0; self.phase = 0.0

    def _synth(self, n):
        take = min(self.len_-self.pos, n)
        if take <= 0:
            self.alive = False; return None
        tt = (np.arange(take)+self.pos)/SR
        frac = (self.pos+np.arange(take))/self.len_
        env = np.sin(np.pi*np.clip(frac, 0, 1)).astype(np.float32)
        f = self.freq*(1+self.bend*frac) + self.vibrato_depth*np.sin(2*np.pi*self.vibrato_hz*tt)
        sig = np.sin(2*np.pi*f*tt+self.phase)
        if take: self.phase = (self.phase+2*np.pi*float(f[-1])*take/SR) % (2*np.pi)
        out = np.zeros(n, dtype=np.float32); out[:take] = (sig*env*self.amp).astype(np.float32)
        self.pos += take
        if self.pos >= self.len_: self.alive = False
        return out


# ── pássaros: várias espécies, cada uma com timbre e cadência próprios ────────
class SampleEvent(SoundEvent):
    """Toca uma amostra real (sounds/<name>.ogg) uma vez, com fade curto nas
    pontas (evita clique) e pitch ajustável via reamostragem. Se a amostra
    não estiver disponível, nasce já morta — o EventScheduler simplesmente
    não soma nada nesse bloco, sem quebrar nada."""
    def __init__(self, sample="", vol=0.6, **kw):
        super().__init__(**kw)
        base = _load_sample(sample)
        if base is None or len(base) < 8:
            self.alive = False
            self._data = None; self.pos = 0; self.vol = vol
            return
        if abs(self.pitch - 1.0) > 1e-3:
            new_len = max(8, int(len(base)/self.pitch))
            base = np.interp(np.linspace(0, len(base)-1, new_len),
                              np.arange(len(base)), base).astype(np.float32)
        fade = min(len(base)//8, int(0.008*SR))
        if fade > 1:
            base = base.copy()
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            base[:fade] *= ramp
            base[-fade:] *= ramp[::-1]
        self._data = base; self.pos = 0; self.vol = vol

    def _synth(self, n):
        if self._data is None:
            self.alive = False; return None
        take = min(len(self._data)-self.pos, n)
        if take <= 0:
            self.alive = False; return None
        out = np.zeros(n, dtype=np.float32)
        out[:take] = self._data[self.pos:self.pos+take]*self.vol
        self.pos += take
        if self.pos >= len(self._data): self.alive = False
        return out


class BirdEvent(SoundEvent):
    """Canto = oscilador com FM leve (a garganta do pássaro não é uma senoide
    fixa) + vários bicos curtos com gaps irregulares. Nunca soa igual duas vezes."""
    _SPECIES = [
        # nome,     peso, f0(Hz), profundidade FM, faixa nº de bicos, duração do bico(s)
        ("bemtevi", 0.15, 1900, 550, (2, 3), 0.10),
        ("sabia",   0.30, 2700, 950, (3, 5), 0.055),
        ("canario", 0.20, 3400, 1150,(4, 6), 0.04),
        ("pardal",  0.35, 2100, 380, (2, 3), 0.05),
    ]
    def __init__(self, **kw):
        super().__init__(**kw)
        w = np.array([s[1] for s in self._SPECIES]); w = w/w.sum()
        _, _, f0, fm, rng, dur = self._SPECIES[np.random.choice(len(self._SPECIES), p=w)]
        self.f0, self.fm, self.chirp_dur = f0*self.pitch, fm, dur
        self.n_chirps = int(np.random.randint(rng[0], rng[1]+1))
        self.gap_s = float(np.random.uniform(0.035, 0.09))
        self.phase = 0.0; self.i_chirp = 0; self.pos = 0
        self.state = "chirp"; self.gap_left = 0

    def _synth(self, n):
        out = np.zeros(n, dtype=np.float32); i = 0
        while i < n:
            if self.state == "done":
                if i == 0: self.alive = False; return None
                break
            if self.state == "gap":
                take = min(self.gap_left, n-i); self.gap_left -= take; i += take
                if self.gap_left <= 0: self.state = "chirp"; self.pos = 0
                continue
            clen = max(1, int(self.chirp_dur*SR))
            take = min(clen-self.pos, n-i)
            if take > 0:
                tt = (np.arange(take)+self.pos)/SR
                env = np.sin(np.pi*np.clip((self.pos+np.arange(take))/clen, 0, 1))
                f = self.f0 + self.fm*np.sin(2*np.pi*7.5*tt)
                out[i:i+take] = (np.sin(2*np.pi*f*tt+self.phase)*env).astype(np.float32)
                self.phase = (self.phase+2*np.pi*float(f[-1])*take/SR) % (2*np.pi)
            self.pos += take; i += take
            if self.pos >= clen:
                self.i_chirp += 1
                if self.i_chirp >= self.n_chirps: self.state = "done"
                else: self.state = "gap"; self.gap_left = int(self.gap_s*SR)
        return out*0.55


# ═══════════════════════════════════════════ tabelas de eventos por ambiente ══
FOREST_EVENTS = [
    {"cls": BirdEvent, "chance": 0.006, "cooldown": (2, 10), "max": 2,
     "hour_mult": _mult_daytime,
     "kwargs": lambda: dict(pan=_rand_pan(), distance=_rand_dist(0.05, 0.85), pitch=_rand_pitch(0.1))},
    {"cls": PulseTrain, "chance": 0.003, "cooldown": (3, 12), "max": 3,
     "hour_mult": _mult_nighttime,
     "kwargs": lambda: dict(n_pulses=np.random.randint(8, 17), pulse_dur=0.02, decay=0.015,
                             gap=(0.05, 0.12), filt="high", cutoff=0.4, amp=0.12,
                             pan=_rand_pan(), distance=_rand_dist(0.2, 0.9))},
    {"cls": Tone, "chance": 0.0006, "cooldown": (60, 180), "max": 1,
     "hour_mult": _mult_nighttime,
     "kwargs": lambda: dict(freq=np.random.uniform(420, 520), dur=np.random.uniform(0.9, 1.3),
                             bend=-0.15, amp=0.3, pan=_rand_pan(), distance=_rand_dist(0.5, 0.95))},
    {"cls": NoiseBurst, "chance": 0.01, "cooldown": (1, 6), "max": 3,
     "kwargs": lambda: dict(dur=np.random.uniform(0.3, 0.7), decay=0.15, filt="high", cutoff=0.3,
                             amp=np.random.uniform(0.12, 0.2), pan=_rand_pan(), distance=_rand_dist(0.1, 0.7))},
    {"cls": Swell, "chance": 0.004, "cooldown": (8, 25), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(3, 6), cutoff0=0.05, cutoff1=0.25, amp=0.3,
                             pan_sweep=0.3, pan=_rand_pan(), distance=_rand_dist(0.0, 0.4))},
    {"cls": Tone, "chance": 0.001, "cooldown": (20, 50), "max": 1,
     "kwargs": lambda: dict(freq=np.random.uniform(180, 220), dur=np.random.uniform(2, 4),
                             vibrato_hz=18, vibrato_depth=15, amp=0.1,
                             pan=_rand_pan(), distance=_rand_dist(0.1, 0.5))},
    # híbrido: canto de pássaro real (CC0), alternando com o BirdEvent sintético acima
    {"cls": SampleEvent, "chance": 0.004, "cooldown": (3, 12), "max": 1,
     "hour_mult": _mult_daytime,
     "kwargs": lambda: dict(sample=np.random.choice(["bird_1", "bird_2", "bird_3"]),
                             vol=np.random.uniform(0.4, 0.7), pitch=_rand_pitch(0.06),
                             pan=_rand_pan(), distance=_rand_dist(0.05, 0.8))},
    {"cls": SampleEvent, "chance": 0.0004, "cooldown": (80, 200), "max": 1,
     "hour_mult": _mult_nighttime,
     "kwargs": lambda: dict(sample="owl_hoot", vol=0.5, pitch=_rand_pitch(0.05),
                             pan=_rand_pan(), distance=_rand_dist(0.4, 0.9))},
]

RAIN_EVENTS = [
    {"cls": NoiseBurst, "chance": 0.03, "cooldown": (0.05, 0.2), "max": 6,
     "kwargs": lambda: dict(dur=np.random.uniform(0.04, 0.07), decay=0.02, filt="low", cutoff=0.35,
                             amp=np.random.uniform(0.35, 0.55), pan=_rand_pan(), distance=_rand_dist(0.0, 0.4))},
    {"cls": NoiseBurst, "chance": 0.05, "cooldown": (0.02, 0.08), "max": 8,
     "kwargs": lambda: dict(dur=np.random.uniform(0.015, 0.03), decay=0.01, filt="high", cutoff=0.5,
                             amp=np.random.uniform(0.15, 0.3), pan=_rand_pan(), distance=_rand_dist(0.0, 0.6))},
    {"cls": NoiseBurst, "chance": 0.006, "cooldown": (0.3, 1.2), "max": 3,
     "kwargs": lambda: dict(dur=np.random.uniform(0.12, 0.2), decay=0.06, filt="low", cutoff=0.2,
                             amp=np.random.uniform(0.25, 0.4), pan=_rand_pan(), distance=_rand_dist(0.2, 0.7))},
]

THUNDER_EVENTS = [
    {"cls": Swell, "chance": 0.0015, "cooldown": (60, 150), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(2.5, 4.5), cutoff0=0.01, cutoff1=0.08,
                             amp=np.random.uniform(0.5, 0.8), pan=_rand_pan(), distance=_rand_dist(0.3, 0.8))},
    {"cls": SampleEvent, "chance": 0.001, "cooldown": (60, 150), "max": 1,
     "kwargs": lambda: dict(sample="thunder", vol=np.random.uniform(0.5, 0.8),
                             pan=_rand_pan(), distance=_rand_dist(0.3, 0.7))},
]

WIND_EVENTS = [
    {"cls": Swell, "chance": 0.01, "cooldown": (4, 12), "max": 2,
     "kwargs": lambda: dict(dur=np.random.uniform(2, 5), cutoff0=0.05, cutoff1=0.3,
                             amp=np.random.uniform(0.4, 0.7), pan_sweep=0.4,
                             pan=_rand_pan(), distance=_rand_dist(0.0, 0.3))},
    {"cls": Tone, "chance": 0.003, "cooldown": (15, 40), "max": 1,
     "kwargs": lambda: dict(freq=np.random.uniform(300, 500), dur=np.random.uniform(2, 4),
                             vibrato_hz=np.random.uniform(0.5, 1.2), vibrato_depth=40, bend=0.05,
                             amp=0.12, pan=_rand_pan(), distance=_rand_dist(0.2, 0.6))},
]

CAFE_EVENTS = [
    {"cls": Swell, "chance": 0.0015, "cooldown": (30, 80), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(1.5, 2.5), cutoff0=0.2, cutoff1=0.5, amp=0.3,
                             pan=_rand_pan(), distance=_rand_dist(0.1, 0.4))},
    {"cls": NoiseBurst, "chance": 0.0012, "cooldown": (20, 60), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(0.3, 0.5), decay=0.12, filt="band", cutoff=0.3,
                             amp=0.25, pan=_rand_pan(), distance=_rand_dist(0.2, 0.6))},
    {"cls": NoiseBurst, "chance": 0.0008, "cooldown": (40, 100), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(0.4, 0.6), decay=0.2, filt="low", cutoff=0.15,
                             amp=0.2, pan=_rand_pan(), distance=_rand_dist(0.3, 0.7))},
    {"cls": PulseTrain, "chance": 0.002, "cooldown": (15, 40), "max": 1,
     "kwargs": lambda: dict(n_pulses=np.random.randint(4, 8), pulse_dur=0.015, decay=0.01,
                             gap=(0.15, 0.3), filt="high", cutoff=0.5, amp=0.15,
                             pan=_rand_pan(), distance=_rand_dist(0.1, 0.4))},
    {"cls": SampleEvent, "chance": 0.0008, "cooldown": (40, 100), "max": 1,
     "kwargs": lambda: dict(sample="door_creak", vol=np.random.uniform(0.4, 0.6),
                             pan=_rand_pan(), distance=_rand_dist(0.3, 0.7))},
    {"cls": SampleEvent, "chance": 0.003, "cooldown": (10, 30), "max": 1,
     "kwargs": lambda: dict(sample="cup_clink", vol=np.random.uniform(0.3, 0.5),
                             pitch=_rand_pitch(0.15), pan=_rand_pan(), distance=_rand_dist(0.1, 0.4))},
]

LIBRARY_EVENTS = [
    {"cls": NoiseBurst, "chance": 0.002, "cooldown": (8, 25), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(0.25, 0.4), decay=0.12, filt="high", cutoff=0.35,
                             amp=0.12, pan=_rand_pan(), distance=_rand_dist(0.1, 0.5))},
    {"cls": PulseTrain, "chance": 0.0015, "cooldown": (15, 45), "max": 1,
     "kwargs": lambda: dict(n_pulses=np.random.randint(6, 15), pulse_dur=0.02, decay=0.015,
                             gap=(0.03, 0.08), filt="high", cutoff=0.45, amp=0.06,
                             pan=_rand_pan(), distance=_rand_dist(0.1, 0.4))},
    {"cls": PulseTrain, "chance": 0.0006, "cooldown": (40, 120), "max": 1,
     "kwargs": lambda: dict(n_pulses=2, pulse_dur=0.09, decay=0.04, gap=(0.15, 0.25),
                             filt="band", cutoff=0.2, amp=0.18,
                             pan=_rand_pan(), distance=_rand_dist(0.4, 0.8))},
    {"cls": PulseTrain, "chance": 0.0008, "cooldown": (20, 60), "max": 1,
     "kwargs": lambda: dict(n_pulses=np.random.randint(3, 7), pulse_dur=0.03, decay=0.015,
                             gap=(0.25, 0.4), filt="low", cutoff=0.25, amp=0.06,
                             pan=_rand_pan(), distance=_rand_dist(0.6, 0.95))},
]

TRAIN_EVENTS = [
    {"cls": Tone, "chance": 0.0007, "cooldown": (90, 240), "max": 1,
     "kwargs": lambda: dict(freq=np.random.uniform(800, 900), dur=np.random.uniform(1.5, 2.5),
                             bend=-0.03, amp=0.18, pan=_rand_pan(), distance=_rand_dist(0.2, 0.5))},
]

CITY_EVENTS = [
    {"cls": Swell, "chance": 0.002, "cooldown": (10, 30), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(3, 5), cutoff0=0.3, cutoff1=0.6, amp=0.25,
                             pan_sweep=0.8, pan=_rand_pan(), distance=_rand_dist(0.1, 0.4))},
    {"cls": Swell, "chance": 0.0012, "cooldown": (20, 50), "max": 1,
     "kwargs": lambda: dict(dur=np.random.uniform(4, 7), cutoff0=0.05, cutoff1=0.15, amp=0.35,
                             pan_sweep=0.3, pan=_rand_pan(), distance=_rand_dist(0.2, 0.6))},
    {"cls": Tone, "chance": 0.0006, "cooldown": (60, 150), "max": 1,
     "kwargs": lambda: dict(freq=np.random.uniform(500, 650), dur=np.random.uniform(4, 8),
                             vibrato_hz=0.35, vibrato_depth=120, amp=0.12,
                             pan=_rand_pan(), distance=_rand_dist(0.6, 0.95))},
    {"cls": PulseTrain, "chance": 0.0009, "cooldown": (30, 90), "max": 1,
     "kwargs": lambda: dict(n_pulses=np.random.randint(2, 4), pulse_dur=0.08, decay=0.035,
                             gap=(0.2, 0.35), filt="band", cutoff=0.25, amp=0.2,
                             pan=_rand_pan(), distance=_rand_dist(0.3, 0.7))},
    {"cls": SampleEvent, "chance": 0.0009, "cooldown": (30, 90), "max": 1,
     "kwargs": lambda: dict(sample="dog_bark", vol=np.random.uniform(0.35, 0.6),
                             pitch=_rand_pitch(0.1), pan=_rand_pan(), distance=_rand_dist(0.3, 0.8))},
]


# ─────────────────────────────────────────────── sons ambientes sintéticos ───
# Cada som é uma função(n, state)-> array mono [-1..1]. A textura contínua de
# base usa ruído colorido + modulação; a "vida" (eventos discretos) vem do
# EventScheduler guardado em st.scheduler (lazy, criado no primeiro uso).

class _AmbState:
    def __init__(self):
        self.noise = _NoiseState()
        self.phase = 0.0
        self.env   = 0.0
        self.t     = 0
        self.drops = []     # legado, não usado mais (gotas viraram eventos)
        self.lp = 0.0
        self.lp2 = 0.0
        self.scheduler  = None   # EventScheduler principal do ambiente
        self.scheduler2 = None   # scheduler extra (ex.: trovão na tempestade)


def _amb_rain(n, st):
    base = _pink(n, st.noise)
    lp = st.lp
    for i in range(n):
        lp += 0.2*(base[i]-lp); base[i] = lp
    st.lp = lp
    base = base*0.55
    if st.scheduler is None:
        st.scheduler = EventScheduler(RAIN_EVENTS)
    el, er = st.scheduler.render(n)
    # ambiente "chuva" é mono (sem spatializer de camada aqui) — soma o lado
    # esquerdo e direito dos eventos numa única textura mono, como as demais.
    return np.clip(base + (el+er)*0.5, -1, 1).astype(np.float32)


def _amb_storm(n, st):
    rain = _amb_rain(n, st)
    if st.scheduler2 is None:
        st.scheduler2 = EventScheduler(THUNDER_EVENTS)
    el, er = st.scheduler2.render(n)
    return np.clip(rain + (el+er)*0.5, -1, 1).astype(np.float32)


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
    # base: "ar" da floresta — ruído verde suave + respiração lenta de vento
    bg = _green(n, st.noise)*0.28
    t = np.arange(n) + st.t; st.t += n
    bg = bg * (0.85 + 0.15*np.sin(2*np.pi*0.03*(t/SR))).astype(np.float32)
    if st.scheduler is None:
        st.scheduler = EventScheduler(FOREST_EVENTS)
    el, er = st.scheduler.render(n)
    return np.clip(bg + (el+er)*0.5, -1, 1).astype(np.float32)


def _amb_fire(n, st):
    # crepitar: brown grave + estalos
    base = _brown(n, st.noise)*0.4
    if np.random.random() < 0.3:
        idx = np.random.randint(0, n)
        base[idx:idx+2] += np.random.uniform(0.3, 0.7)
    return base


def _amb_wind(n, st):
    # o vento real não muda só de volume — o timbre (filtro) também varia
    w = _pink(n, st.noise)
    t = np.arange(n) + st.t; st.t += n
    env = 0.4 + 0.6*np.abs(np.sin(2*np.pi*0.05*(t/SR)))
    cutoff = 0.15 + 0.15*np.abs(np.sin(2*np.pi*0.017*(t/SR)))  # timbre variável
    lp = st.lp
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        lp += float(cutoff[i])*(w[i]-lp); out[i] = lp
    st.lp = lp
    base = (out*env*0.7).astype(np.float32)
    if st.scheduler is None:
        st.scheduler = EventScheduler(WIND_EVENTS)
    el, er = st.scheduler.render(n)
    return np.clip(base + (el+er)*0.5, -1, 1).astype(np.float32)


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
    """Trem em movimento: clickety-clack rítmico de trilho (velocidade variável),
    rumble, vento, apito (agora como evento independente)."""
    t = np.arange(n) + st.t; st.t += n
    rumble = _brown(n, st.noise) * 0.38
    # velocidade oscila lentamente (acelera/desacelera ao longo de ~50s)
    speed = 0.82 + 0.22*np.sin(2*np.pi*0.02*(t/SR))
    click_period = SR / (2.5*speed)
    phase1 = t % click_period
    phase2 = (t + click_period * 0.55) % click_period
    decay = 220.0
    clicks  = (np.exp(-phase1 / decay)          * (phase1 < decay)).astype(np.float32) * 0.55
    clicks2 = (np.exp(-phase2 / (decay * 0.75)) * (phase2 < decay * 0.75)).astype(np.float32) * 0.40
    wind_env = (0.75 + 0.25 * np.sin(2*np.pi * 0.06 * (t/SR))).astype(np.float32)
    wind = _pink(n, st.noise) * wind_env * 0.18
    if st.scheduler is None:
        st.scheduler = EventScheduler(TRAIN_EVENTS)
    el, er = st.scheduler.render(n)
    whistle = (el+er)*0.5
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


def _voices_synth(n, st):
    """Murmúrio de vozes sintético, sem idioma específico — usado dentro do
    café e como camada avulsa "Vozes (sintético)" em Lugares sem amostra
    real disponível pro idioma escolhido."""
    t = np.arange(n) + st.t; st.t += n
    speech = _pink(n, st.noise)
    # Três "vozes" sobrepostas com modulação na faixa de sílabas (3-6 Hz)
    v1 = np.abs(np.sin(2*np.pi * 4.3 * (t/SR) + 0.3)) * 0.55 + 0.18
    v2 = np.abs(np.sin(2*np.pi * 3.6 * (t/SR) + 2.1)) * 0.45 + 0.15
    v3 = np.abs(np.sin(2*np.pi * 5.7 * (t/SR) + 4.5)) * 0.35 + 0.12
    return (speech * (v1 + v2 + v3).astype(np.float32) * 0.20).astype(np.float32)


def _make_voice_loop(sample_name):
    """Cria uma função ambiente(n, st) que toca sounds/<sample_name>.ogg em
    loop contínuo (a amostra já tem fade nas pontas, então a emenda não
    estala) — usado pelas vozes reais por idioma em Lugares."""
    def _fn(n, st):
        data = _load_sample(sample_name)
        if data is None:
            return np.zeros(n, dtype=np.float32)
        L = len(data)
        idx = (st.t + np.arange(n)) % L
        st.t += n
        return (data[idx.astype(np.int64)] * 0.7).astype(np.float32)
    return _fn


def _amb_cafe(n, st):
    """Café: murmúrio de vozes com AM na taxa de sílabas, steam, tinido de
    xícaras + eventos longos (espresso, cadeira, porta, colher mexendo)."""
    voices = _voices_synth(n, st)
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
    if st.scheduler is None:
        st.scheduler = EventScheduler(CAFE_EVENTS)
    el, er = st.scheduler.render(n)
    return np.clip(voices + warmth + steam + clink + (el+er)*0.5, -1, 1).astype(np.float32)


def _amb_library(n, st):
    """Biblioteca: silêncio quase total, zumbido elétrico + eventos raros
    (página virando, lápis escrevendo, tosse, passos distantes)."""
    t = np.arange(n) + st.t; st.t += n
    air = _pink(n, st.noise) * 0.06
    hum = (np.sin(2*np.pi * 60 * (t/SR) + st.phase) * 0.012).astype(np.float32)
    st.phase = (st.phase + 2*np.pi * 60 * n/SR) % (2*np.pi)
    if st.scheduler is None:
        st.scheduler = EventScheduler(LIBRARY_EVENTS)
    el, er = st.scheduler.render(n)
    return np.clip(air + hum + (el+er)*0.5, -1, 1).astype(np.float32)


def _amb_city(n, st):
    """Cidade à noite: tráfego com swell de carros passando, buzina ocasional
    + eventos urbanos raros (moto, ônibus, sirene longínqua, cachorro)."""
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
    if st.scheduler is None:
        st.scheduler = EventScheduler(CITY_EVENTS)
    el, er = st.scheduler.render(n)
    return np.clip(traffic + urban + horn + (el+er)*0.5, -1, 1).astype(np.float32)


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
    # vozes por idioma (híbrido: amostra real CC0 em loop; sem amostra, cai
    # no murmúrio sintético) — usadas pelos "Lugares" com opção de idioma
    "voices_synth": ("Vozes (sintético)", _voices_synth),
    "voices_en":    ("Vozes — inglês",    _make_voice_loop("voices_en")),
    "voices_fr":    ("Vozes — francês",   _make_voice_loop("voices_fr")),
    "voices_de":    ("Vozes — alemão",    _make_voice_loop("voices_de")),
    "voices_ja":    ("Vozes — japonês",   _make_voice_loop("voices_ja")),
    "voices_ko":    ("Vozes — coreano",   _make_voice_loop("voices_ko")),
    "voices_es":    ("Vozes — espanhol",  _make_voice_loop("voices_es")),
    "voices_it":    ("Vozes — italiano",  _make_voice_loop("voices_it")),
}

LANGUAGES = [
    ("en", "🇬🇧 Inglês"), ("fr", "🇫🇷 Francês"), ("de", "🇩🇪 Alemão"),
    ("ja", "🇯🇵 Japonês"), ("ko", "🇰🇷 Coreano"), ("es", "🇪🇸 Espanhol"),
    ("it", "🇮🇹 Italiano"),
]

def voice_ambient_for_lang(lang):
    """Retorna o id de ambiente pra esse idioma — a amostra real se ela foi
    baixada (ver tools/fetch_sounds.py), senão o murmúrio sintético genérico."""
    sid = f"voices_{lang}"
    return sid if sample_available(sid) else "voices_synth"


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
