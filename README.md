# Cortes TiaGOL — notícias de futebol no Telegram

GitHub Actions roda `scripts/telegram_news.py` a cada 15 min: lê RSS (ge,
Gazeta Esportiva, Placar, Trivela, Metrópoles; ESPN costuma bloquear o GitHub),
filtra (outros esportes, NFL, ao vivo, onde assistir, galerias de fotos), remove
duplicadas e posta 1 notícia por rodada no canal — a de maior pontuação
(`SCORE_RULES`: clubes grandes, Seleção, competições, contratações/demissões/
polêmicas somam; base e Série C/D subtraem; −0,5 ponto por hora de idade).

- Segredos do repositório: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`.
- `output/telegram_news_state.json` guarda o que já foi postado; o workflow faz
  commit dele a cada rodada.
- Rodar na mão: aba **Actions** → "Noticias de futebol no Telegram" → **Run workflow**.
- Teste local: `python scripts/telegram_news.py --dry-run`.

## Quem dispara

- **Principal:** job "Noticias Telegram" no [cron-job.org](https://cron-job.org)
  (conta do dono), a cada 15 min, `POST https://api.github.com/repos/foxsilva90/cortes-tiagol-telegram-news/actions/workflows/news.yml/dispatches`
  com body `{"ref":"main"}` e um fine-grained token (só Actions: read/write neste repo).
  A URL precisa ser `https://` — com `http://` o GitHub devolve 301 e nada roda.
- **Reserva:** `schedule` do próprio workflow (o GitHub pula muitas rodadas).
- O script ignora rodadas a menos de 12 min do último post, então os dois
  disparadores juntos não geram post dobrado.
