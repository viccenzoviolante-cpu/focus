"""
profiles.py — Dados estáticos: presets de ondas, perfis do Focus Engine,
conquistas, protocolos padrão.
"""

# ── Presets de ondas cerebrais ────────────────────────────────────────────────
WAVE_PRESETS = [
    {"id":"delta","name":"Delta","beat":2,  "base":120,"hex":"#4a4a8a",
     "range":"1–4 Hz",  "desc":"Sono profundo, descanso e recuperação total."},
    {"id":"theta","name":"Theta","beat":6,  "base":200,"hex":"#2d6a9f",
     "range":"4–8 Hz",  "desc":"Meditação profunda, silêncio interior, introspecção."},
    {"id":"alpha","name":"Alpha","beat":10, "base":200,"hex":"#2a8a6e",
     "range":"8–13 Hz", "desc":"Relaxamento alerta, mente calma, criatividade leve."},
    {"id":"beta", "name":"Beta", "beat":18, "base":220,"hex":"#9a7a20",
     "range":"14–30 Hz","desc":"Foco ativo, concentração e clareza mental."},
    {"id":"gamma","name":"Gamma","beat":40, "base":200,"hex":"#7a3a6a",
     "range":"30+ Hz",  "desc":"Alta percepção, processamento intenso e presença."},
]

def preset_by_id(pid):
    for p in WAVE_PRESETS:
        if p["id"] == pid:
            return p
    return WAVE_PRESETS[1]


# ── Focus Engine — perfis prontos ─────────────────────────────────────────────
# Cada perfil monta sozinho a sessão completa.
FOCUS_PROFILES = [
    {
        "id":"estudo","label":"Estudo","icon":"📚",
        "focus_min":50,"break_min":10,"preset":"beta",
        "base":220,"beat":16,"volume":40,
        "sounds":{"brown":35,"rain":20},
        "desc":"Sessões de 50 min com pausa de 10. Beta para concentração sustentada.",
    },
    {
        "id":"deepwork","label":"Deep Work","icon":"🧠",
        "focus_min":90,"break_min":20,"preset":"beta",
        "base":220,"beat":18,"volume":35,
        "sounds":{"brown":40},
        "desc":"Blocos longos de 90 min. Ideal para trabalho profundo sem interrupção.",
    },
    {
        "id":"leitura","label":"Leitura","icon":"📖",
        "focus_min":60,"break_min":10,"preset":"alpha",
        "base":200,"beat":10,"volume":35,
        "sounds":{"library":40,"rain":15},
        "desc":"Alpha relaxado com ambiente de biblioteca. Foco tranquilo.",
    },
    {
        "id":"programacao","label":"Programação","icon":"💻",
        "focus_min":52,"break_min":12,"preset":"beta",
        "base":220,"beat":15,"volume":40,
        "sounds":{"brown":35,"cafe":15},
        "desc":"Beta + brown noise + café. Estado de flow para codar.",
    },
    {
        "id":"escrita","label":"Escrita","icon":"✍️",
        "focus_min":45,"break_min":10,"preset":"alpha",
        "base":200,"beat":9,"volume":35,
        "sounds":{"rain":30,"cafe":15},
        "desc":"Alpha criativo com chuva e café. Fluência para escrever.",
    },
    {
        "id":"meditacao","label":"Meditação","icon":"🧘",
        "focus_min":20,"break_min":0,"preset":"theta",
        "base":200,"beat":6,"volume":45,
        "sounds":{"sea":35},
        "desc":"Theta profundo com som do mar. Silêncio e introspecção.",
    },
    {
        "id":"relax","label":"Relaxamento","icon":"🌿","focus_min":30,"break_min":0,
        "preset":"alpha","base":200,"beat":10,"volume":40,
        "sounds":{"forest":35,"river":20},
        "desc":"Alpha leve com floresta e rio. Descompressão mental.",
    },
    {
        "id":"criatividade","label":"Criatividade","icon":"🎨","focus_min":40,
        "break_min":10,"preset":"theta","base":200,"beat":8,"volume":35,
        "sounds":{"forest":30},
        "desc":"Theta-alpha com floresta e pássaros. Ideias fluem mais soltas.",
    },
    {
        "id":"sono","label":"Sono","icon":"🌙","focus_min":30,"break_min":0,
        "preset":"theta","base":150,"beat":4,"volume":35,
        "sounds":{"rain":30,"fire":15},
        "sleep_sequence":True,
        "desc":"Sequência Alpha→Theta→Delta com chuva e lareira. Para adormecer.",
    },
]

def profile_by_id(pid):
    for p in FOCUS_PROFILES:
        if p["id"] == pid:
            return p
    return None


# ── Sleep Mode — sequência padrão de descida ──────────────────────────────────
SLEEP_SEQUENCE = [
    {"preset":"alpha","minutes":10},
    {"preset":"theta","minutes":10},
    {"preset":"delta","minutes":10},
]


# ── Protocolos Biohacker padrão (criados na primeira execução) ────────────────
DEFAULT_PROTOCOLS = [
    {"name":"Protocolo Exame","config":{
        "focus_min":90,"break_min":15,"preset":"alpha","base":200,"beat":10,
        "volume":40,"sounds":{"brown":30,"rain":20}}},
    {"name":"Protocolo Criatividade","config":{
        "focus_min":40,"break_min":10,"preset":"theta","base":200,"beat":8,
        "volume":35,"sounds":{"forest":35}}},
    {"name":"Protocolo Leitura","config":{
        "focus_min":60,"break_min":10,"preset":"alpha","base":200,"beat":10,
        "volume":35,"sounds":{"library":40}}},
    {"name":"Protocolo Sono","config":{
        "focus_min":30,"break_min":0,"preset":"theta","base":150,"beat":4,
        "volume":30,"sounds":{"rain":30},"sleep_sequence":True}},
]


# ── Conquistas ────────────────────────────────────────────────────────────────
# (key, ícone, título, descrição, função de checagem recebe stats dict)
ACHIEVEMENTS = [
    ("first_session","🥉","Primeira sessão","Complete sua primeira sessão.",
     lambda s: s["all_n"] >= 1),
    ("hours_10","🥈","10 horas","Acumule 10 horas de foco.",
     lambda s: s["all_min"] >= 600),
    ("hours_50","🥇","50 horas","Acumule 50 horas de foco.",
     lambda s: s["all_min"] >= 3000),
    ("hours_100","🏅","100 horas","Acumule 100 horas de foco.",
     lambda s: s["all_min"] >= 6000),
    ("hours_500","👑","500 horas","Acumule 500 horas de foco.",
     lambda s: s["all_min"] >= 30000),
    ("hours_1000","💎","1000 horas","Acumule 1000 horas de foco.",
     lambda s: s["all_min"] >= 60000),
    ("session_4h","⏳","Maratona","Uma sessão de 4 horas seguidas.",
     lambda s: s["biggest_min"] >= 240),
    ("pomodoros_100","🍅","100 Pomodoros","Complete 100 pomodoros.",
     lambda s: s["pomodoros"] >= 100),
]

# conquistas que dependem de contexto especial (streak, madrugada)
def check_special(streak_days, started_hour):
    """Retorna lista de (key,icon,title) recém-possíveis por contexto."""
    out = []
    if streak_days >= 100:
        out.append(("streak_100","🔥","100 dias seguidos"))
    if streak_days >= 7:
        out.append(("streak_7","📅","7 dias seguidos"))
    if started_hour is not None and 0 <= started_hour <= 4:
        out.append(("night_owl","🦉","Primeira madrugada","Sessão entre 0h e 5h"))
    return out


# ── Lembretes de descanso ─────────────────────────────────────────────────────
REST_TIPS = [
    "💧 Beba um copo de água.",
    "🧍 Levante e dê uns passos.",
    "🤸 Faça um alongamento rápido.",
    "👀 Regra 20-20-20: olhe 20 s para algo a 6 m de distância.",
    "🌬️ Respire fundo 3 vezes, devagar.",
]
