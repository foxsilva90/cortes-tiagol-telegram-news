"""Posta notícias de futebol (via RSS) no canal do Telegram.

Uso:
    python scripts/telegram_news.py            # posta novidades
    python scripts/telegram_news.py --dry-run  # só mostra o que postaria
    python scripts/telegram_news.py --test     # manda uma msg de teste no canal

Precisa no .env:
    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHANNEL_ID=@seucanal   (ou -100xxxxxxxxxx para canal privado)

Na primeira execução só marca as notícias atuais como vistas (não inunda o
canal). A partir daí, cada execução posta até --max notícias novas.
"""
import argparse
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
STATE_FILE = ROOT / "output" / "telegram_news_state.json"

FEEDS = [
    ("ge", "https://ge.globo.com/rss/ge/futebol/"),
    ("ESPN", "https://www.espn.com.br/rss/futebol"),
    ("Gazeta Esportiva", "https://www.gazetaesportiva.com/feed/"),
]

# Gazeta cobre outros esportes; bloqueia pelas seções da URL.
NON_FOOTBALL_URL = re.compile(r"/mais-esportes/|/(basquete|volei|tenis|f1|automobilismo|mma|boxe|natacao)/")
# Minuto a minuto, guias de transmissão, páginas de jogo e galerias de fotos
# não são notícia.
SKIP_TITLE = re.compile(
    r"\bao vivo\b|tempo real|siga tudo|onde assistir|- globoesporte\.com$|"
    r"\bfotos\b|\bgaleria\b|em imagens",
    re.IGNORECASE,
)

USER_AGENT = "Mozilla/5.0 (CortesTiaGOL news bot)"
MAX_AGE = timedelta(hours=6)
MAX_SEEN = 3000
SEND_DELAY_S = 4
# Títulos de fontes diferentes com >=50% das palavras em comum = mesma notícia.
SAME_STORY_OVERLAP = 0.5
HTTP_TIMEOUT_S = 20
CHANNEL_SIGNATURE = "⚽ <b>Cortes do TiaGOL</b> — notícias do futebol"

NS = {"media": "http://search.yahoo.com/mrss/"}


def load_env():
    # Variáveis de ambiente (ex: segredos do GitHub Actions) valem se não houver .env.
    env = {k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = resp.read()
    # Alguns servidores (ex: ge) mandam gzip mesmo sem Accept-Encoding.
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def parse_date(text):
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def find_image(item):
    media = item.find("media:content", NS)
    if media is not None and media.get("url"):
        return media.get("url")
    thumb = item.find("media:thumbnail", NS)
    if thumb is not None and thumb.get("url"):
        return thumb.get("url")
    enclosure = item.find("enclosure")
    if enclosure is not None and "image" in (enclosure.get("type") or ""):
        return enclosure.get("url")
    match = re.search(r'<img[^>]+src="([^"]+)"', item.findtext("description") or "")
    return match.group(1) if match else None


def clean_text(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_feed(source, url):
    root = ET.fromstring(http_get(url))
    items = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        if NON_FOOTBALL_URL.search(link) or SKIP_TITLE.search(title):
            continue
        summary = clean_text(item.findtext("description"))
        items.append({
            "source": source,
            "title": title,
            "link": link,
            "summary": summary,
            "image": find_image(item),
            "published": parse_date(item.findtext("pubDate")),
        })
    return items


def fetch_all():
    items = []
    for source, url in FEEDS:
        try:
            items.extend(fetch_feed(source, url))
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
            print(f"[aviso] falha no feed {source}: {exc}", file=sys.stderr)
    return items


def title_key(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())[:80]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return None


def save_state(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"seen": seen[-MAX_SEEN:]}, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def item_keys(item):
    return [item["link"], "t:" + title_key(item["title"])]


def format_caption(item):
    summary = item["summary"]
    if len(summary) > 280:
        summary = summary[:277].rsplit(" ", 1)[0] + "…"
    parts = [f"<b>{html.escape(item['title'])}</b>"]
    if summary and summary.lower() != item["title"].lower():
        parts.append(html.escape(summary))
    parts.append(f'📰 {html.escape(item["source"])} · <a href="{html.escape(item["link"])}">Ler matéria</a>')
    parts.append(CHANNEL_SIGNATURE)
    return "\n\n".join(parts)


def telegram_call(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(payload).encode()
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, data=data, timeout=HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read() or b"{}")
            retry = body.get("parameters", {}).get("retry_after")
            if exc.code == 429 and retry:
                time.sleep(retry + 1)
                continue
            raise RuntimeError(f"Telegram {method} falhou: {body.get('description', exc)}") from exc
    raise RuntimeError(f"Telegram {method}: rate limit persistente")


def send_item(token, chat_id, item):
    caption = format_caption(item)
    if item["image"]:
        try:
            return telegram_call(token, "sendPhoto", {
                "chat_id": chat_id, "photo": item["image"],
                "caption": caption, "parse_mode": "HTML",
            })
        except RuntimeError as exc:
            print(f"[aviso] foto falhou, mandando só texto: {exc}", file=sys.stderr)
    return telegram_call(token, "sendMessage", {
        "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    })


def significant_words(title):
    words = re.findall(r"\w+", title.lower())
    return {w for w in words if len(w) > 3}


def is_same_story(words, other_words):
    if not words or not other_words:
        return False
    overlap = len(words & other_words) / min(len(words), len(other_words))
    return overlap >= SAME_STORY_OVERLAP


def pick_new(items, seen_set):
    now = datetime.now(timezone.utc)
    fresh, batch_keys, batch_words = [], set(), []
    for item in items:
        keys = item_keys(item)
        if any(k in seen_set or k in batch_keys for k in keys):
            continue
        if item["published"] and now - item["published"] > MAX_AGE:
            continue
        words = significant_words(item["title"])
        if any(is_same_story(words, other) for other in batch_words):
            continue
        batch_keys.update(keys)
        batch_words.append(words)
        fresh.append(item)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(fresh, key=lambda i: i["published"] or epoch)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max", type=int, default=1, help="máx. de posts por execução")
    parser.add_argument("--dry-run", action="store_true", help="não posta nem salva estado")
    parser.add_argument("--test", action="store_true", help="manda msg de teste e sai")
    parser.add_argument("--backfill", action="store_true", help="1ª execução posta em vez de só marcar como visto")
    return parser.parse_args()


def main():
    # O Agendador roda com console cp1252; sem isso um emoji no título derruba
    # o script depois de postar e antes de salvar o estado (post duplicado).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHANNEL_ID")
    if not args.dry_run and not (token and chat_id):
        sys.exit("Faltam TELEGRAM_BOT_TOKEN e/ou TELEGRAM_CHANNEL_ID no .env")

    if args.test:
        telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": "✅ Bot de notícias conectado!"})
        print("Mensagem de teste enviada.")
        return

    items = fetch_all()
    state = load_state()
    seen = state["seen"] if state else []
    new_items = pick_new(items, set(seen))

    if state is None and not args.backfill and not args.dry_run:
        for item in new_items:
            seen.extend(item_keys(item))
        save_state(seen)
        print(f"1ª execução: {len(new_items)} notícias marcadas como vistas. Próximas rodadas postam só novidades.")
        return

    to_post = new_items[-args.max:]
    print(f"{len(items)} itens lidos, {len(new_items)} novos, postando {len(to_post)}.")
    for item in to_post:
        if args.dry_run:
            print(f"- [{item['source']}] {item['title']}\n  {item['link']}\n  img={item['image']}")
            continue
        try:
            send_item(token, chat_id, item)
            print(f"postado: {item['title']}")
        except RuntimeError as exc:
            print(f"[erro] {exc}", file=sys.stderr)
            break
        seen.extend(item_keys(item))
        time.sleep(SEND_DELAY_S)

    # O que ficou de fora fica na fila pras próximas rodadas; some sozinho
    # quando passa de MAX_AGE.
    if not args.dry_run:
        save_state(seen)


if __name__ == "__main__":
    main()
