"""
fetch_sounds.py — Baixa amostras curtas CC0 da Freesound API para complementar
a sintese procedural em audio_engine.py (uso hibrido: sintese + amostra real).

So usa a stdlib (urllib) para nao adicionar dependencia nova so pra isso.
Le a API key de .env (FREESOUND_API_KEY=...), nunca commitada no git.

Uso:
    python tools/fetch_sounds.py
"""
import os, sys, json, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_PATH = os.path.join(ROOT, ".env")
OUT_DIR = os.path.join(ROOT, "sounds")

API = "https://freesound.org/apiv2"


def _load_api_key():
    if not os.path.exists(ENV_PATH):
        sys.exit(".env nao encontrado -- crie com FREESOUND_API_KEY=sua_chave")
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("FREESOUND_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("FREESOUND_API_KEY nao encontrada em .env")


API_KEY = _load_api_key()


def _get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Token {API_KEY}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url, dest):
    req = urllib.request.Request(url, headers={"Authorization": f"Token {API_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        f.write(r.read())


# nome do arquivo local -> (lista de queries a tentar em ordem, duracao min, duracao max em segundos)
WANTED = {
    "bird_1":     (["bird chirp single"], 0.3, 3.0),
    "bird_2":     (["bird song short"], 0.5, 4.0),
    "bird_3":     (["small bird tweet", "canary chirp"], 0.2, 6.0),
    "dog_bark":   (["dog bark single"], 0.3, 2.5),
    "owl_hoot":   (["owl hoot"], 0.5, 3.0),
    "door_creak": (["door creak wood"], 0.5, 3.5),
    "thunder":    (["thunder distant rumble"], 1.5, 6.0),
    "cup_clink":  (["cup clink ceramic"], 0.1, 1.5),
}

# murmúrio de vozes por idioma (para cafés / lugares públicos) — textura de
# fundo, então aceita duração maior (vai tocar em loop).
LANGUAGE_VOICES = {
    "en": (["cafe crowd talking english", "restaurant chatter english",
            "english pub chatter", "london cafe crowd"], 2.0, 40.0),
    "fr": (["french cafe conversation", "french crowd talking",
            "paris street cafe voices", "french people talking"], 2.0, 40.0),
    "de": (["german crowd talking", "german cafe chatter",
            "berlin cafe crowd", "german people talking"], 2.0, 40.0),
    "ja": (["japanese crowd talking", "japanese restaurant chatter",
            "tokyo cafe crowd", "japanese people talking"], 2.0, 40.0),
    "ko": (["korean crowd talking", "korean conversation cafe",
            "seoul cafe crowd", "korean people talking"], 2.0, 40.0),
    "es": (["spanish crowd talking", "spanish cafe chatter"], 2.0, 40.0),
    "it": (["italian crowd talking", "italian cafe chatter"], 2.0, 40.0),
}


def find_and_download(name, queries, dmin, dmax):
    for query in queries:
        params = {
            "query": query,
            "filter": 'license:"Creative Commons 0"',
            "fields": "id,name,previews,duration,avg_rating,license,tags",
            "sort": "rating_desc",
            "page_size": 10,
        }
        url = f"{API}/search/text/?{urllib.parse.urlencode(params)}"
        try:
            data = _get(url)
        except Exception as e:
            print(f"[{name}] busca '{query}' falhou: {e}")
            continue

        candidates = [r for r in data.get("results", [])
                      if dmin <= r.get("duration", 0) <= dmax
                      and r.get("previews", {}).get("preview-hq-ogg")]
        if not candidates:
            continue

        best = candidates[0]
        dest = os.path.join(OUT_DIR, f"{name}.ogg")
        try:
            _download(best["previews"]["preview-hq-ogg"], dest)
        except Exception as e:
            print(f"[{name}] download falhou: {e}")
            continue
        print(f"[{name}] OK -- '{best['name']}' ({best['duration']:.1f}s, "
              f"nota {best.get('avg_rating')}, query='{query}') -> {dest}")
        return True

    print(f"[{name}] nenhum resultado CC0 na faixa {dmin}-{dmax}s para {queries}")
    return False


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    ok = 0; total = 0
    for name, (queries, dmin, dmax) in WANTED.items():
        total += 1
        if find_and_download(name, queries, dmin, dmax): ok += 1
    for lang, (queries, dmin, dmax) in LANGUAGE_VOICES.items():
        total += 1
        if find_and_download(f"voices_{lang}", queries, dmin, dmax): ok += 1
    print(f"\n{ok}/{total} sons baixados em {OUT_DIR}")
    missing_langs = [lang for lang in LANGUAGE_VOICES
                      if not os.path.exists(os.path.join(OUT_DIR, f"voices_{lang}.ogg"))]
    if missing_langs:
        print(f"Idiomas sem amostra CC0 (vao cair no murmúrio sintético): {missing_langs}")
