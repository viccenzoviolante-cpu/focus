# 🎧 Ondas Binaurais

App de foco, relaxamento e sono para Windows, com geração de áudio em tempo real.
A maior parte dos sons é **sintetizada por DSP** (eventos físicos: pássaro, gota,
porta, moto...); alguns são **híbridos**, misturando síntese com pequenas
gravações reais CC0 (pasta `sounds/`, ~2 MB) pra dar mais realismo sem depender
de baixar trilhas inteiras.

## ✨ Recursos

- **5 presets de ondas binaurais** (Delta, Theta, Alpha, Beta, Gamma) com frequência independente por canal
- **Mixer de sons ambientes** — chuva, mar, lareira, floresta, café, trem, cidade, vozes por idioma, e ruídos white/pink/brown/green — cada um com volume, mute, favorito e modo 3D (áudio espacial)
- **Motor de eventos** (`EventScheduler`) — cada ambiente "vivo" soma uma textura contínua a pequenos eventos independentes (pássaro, trovão, porta, moto passando...) com posição, distância, pitch e duração próprios, alternando síntese procedural com amostras reais CC0
- **Lugares** — cenários prontos (café em Paris, rua movimentada, biblioteca, trem noturno...) que combinam várias camadas de som de uma vez; nos que têm vozes de fundo dá pra escolher o idioma (inglês, francês, alemão, japonês, coreano, espanhol, italiano)
- **Focus Engine** — escolha o objetivo e o app monta a sessão inteira (duração, pausa, ondas, sons e volume)
- **Checklist da sessão** — tarefa principal + lista de itens marcáveis, direto na aba Player
- **Sleep Mode** — sequência automática Alpha → Theta → Delta com fade out
- **Timer / Pomodoro** com avisos em 5 min, metade e 10 min restantes
- **Dashboard completo** — horas por dia/semana/mês/ano, gráfico, calendário tipo GitHub, streak, meta diária, XP e níveis
- **Conquistas** desbloqueáveis
- **Protocolos Biohacker** personalizados
- **Sistema de Impulso** — ritual de entrada, check-in inicial, botão "estou com vontade de desistir" com reflexão guiada, classificação do impulso (necessidade física / emoção / hábito) e diário de impulsos com estatísticas
- **Bandeja do sistema** com o tempo no ícone
- Abrir com o Windows · modo invisível · tema AMOLED · lembretes de descanso
- Tudo salvo localmente em SQLite (sobrevive a reinicializações)

## 🚀 Instalação

### Opção 1 — Instalador automático (Windows)
1. Baixe ou clone este repositório.
2. Clique com o botão direito em `instalar.bat` → **Executar como administrador**.
3. O app abre sozinho e cria um atalho na área de trabalho.

### Opção 2 — Manual
```bash
pip install numpy sounddevice pystray Pillow soundfile
python main.py
```

**Requisito:** Python 3.9+ (marque "Add Python to PATH" ao instalar).

## 📁 Estrutura

| Arquivo | Função |
|---------|--------|
| `main.py` | Interface e lógica do app |
| `audio_engine.py` | Síntese de áudio (binaural + ruídos + ambientes) |
| `database.py` | Persistência em SQLite |
| `profiles.py` | Presets, perfis do Focus Engine, Lugares e conquistas |
| `sounds/` | Amostras reais CC0 (pássaros, latido, porta, vozes por idioma...) usadas junto com a síntese |
| `tools/fetch_sounds.py` | Script pra baixar/atualizar as amostras via API do freesound.org (precisa de `.env` com `FREESOUND_API_KEY`, não versionado) |

## 💾 Onde ficam os dados

`C:\Usuários\SEU_USUARIO\.ondabinaural\data.db`

## 🎯 Dica

Use **fones de ouvido** — as ondas binaurais só funcionam com um som diferente em cada ouvido.

## 🔊 Créditos de áudio

As amostras em `sounds/` são gravações **CC0** (domínio público) baixadas via
[freesound.org](https://freesound.org). CC0 permite uso e redistribuição livre,
inclusive comercial, sem exigir atribuição — mas o crédito aos autores originais
é uma boa prática, então: obrigado à comunidade da Freesound.

## 📜 Licença

MIT
