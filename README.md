# Cortes TiaGOL — notícias de futebol no Telegram

GitHub Actions roda `scripts/telegram_news.py` a cada 15 min: lê RSS (ge,
Gazeta Esportiva, Placar, Trivela, Metrópoles; ESPN costuma bloquear o GitHub),
filtra (outros esportes, NFL, ao vivo, onde assistir, galerias de fotos), remove
duplicadas e posta 1 notícia por rodada no canal.

- Segredos do repositório: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`.
- `output/telegram_news_state.json` guarda o que já foi postado; o workflow faz
  commit dele a cada rodada.
- Rodar na mão: aba **Actions** → "Noticias de futebol no Telegram" → **Run workflow**.
- Teste local: `python scripts/telegram_news.py --dry-run`.

O agendamento do GitHub pode atrasar alguns minutos em horário de pico.
