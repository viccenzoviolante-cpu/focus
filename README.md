# 🎧 Ondas Binaurais

App de foco, relaxamento e sono para Windows, com geração de áudio em tempo real.
Todos os sons são **sintetizados por DSP** — não precisa baixar nenhum arquivo de áudio.

## ✨ Recursos

- **5 presets de ondas binaurais** (Delta, Theta, Alpha, Beta, Gamma) com frequência independente por canal
- **Mixer de 19 sons ambientes** — chuva, mar, lareira, floresta, café, trem, e ruídos white/pink/brown/green — cada um com volume, mute, favorito e modo 3D (áudio espacial)
- **Focus Engine** — escolha o objetivo e o app monta a sessão inteira (duração, pausa, ondas, sons e volume)
- **Sleep Mode** — sequência automática Alpha → Theta → Delta com fade out
- **Timer / Pomodoro** com avisos em 5 min, metade e 10 min restantes
- **Dashboard completo** — horas por dia/semana/mês/ano, gráfico, calendário tipo GitHub, streak, meta diária, XP e níveis
- **Conquistas** desbloqueáveis
- **Protocolos Biohacker** personalizados
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
pip install numpy sounddevice pystray Pillow
python main.py
```

**Requisito:** Python 3.9+ (marque "Add Python to PATH" ao instalar).

## 📁 Estrutura

| Arquivo | Função |
|---------|--------|
| `main.py` | Interface e lógica do app |
| `audio_engine.py` | Síntese de áudio (binaural + ruídos + ambientes) |
| `database.py` | Persistência em SQLite |
| `profiles.py` | Presets, perfis do Focus Engine e conquistas |

## 💾 Onde ficam os dados

`C:\Usuários\SEU_USUARIO\.ondabinaural\data.db`

## 🎯 Dica

Use **fones de ouvido** — as ondas binaurais só funcionam com um som diferente em cada ouvido.

## 📜 Licença

MIT
