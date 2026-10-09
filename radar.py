import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут

CHANNELS = [
    {
        "id": "dnepr_bez_tck",
        "url": "https://t.me/s/dnepr_bez_tck",
        "name": "Канал 1",
        "color_clean": "green",
        "color_danger": "red"
    },
    {
        "id": "agendaDnepr",
        "url": "https://t.me/s/agendaDnepr",
        "name": "Канал 2",
        "color_clean": "blue",
        "color_danger": "purple"
    }
]

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

def log(msg):
    """Мгновенный вывод лога в консоль GitHub."""
    print(msg, flush=True)

def find_working_model():
    """Быстрый поиск проверенной рабочей модели."""
    for api_ver in ["v1beta", "v1"]:
        for model_name in ["gemini-flash-lite-latest", "gemini-1.5-flash-latest", "gemini-2.0-flash"]:
            test_url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent?key={GEMINI_KEY}"
            try:
                r = requests.post(test_url, json={"contents": [{"parts": [{"text": "ping"}]}]}, timeout=6)
                if r.status_code == 200:
                    log(f"🎯 Активная модель: {model_name} ({api_ver})")
                    return api_ver, model_name
            except Exception:
                continue
    return "v1beta", "gemini-flash-lite-latest"

API_VERSION, MODEL_NAME = find_working_model()

def cleanup_old_points():
    """Молниеносная очистка точек старше 40 минут."""
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            now_ms = int(time.time() * 1000)
            to_delete = {k: None for k, v in data.items() if isinstance(v, dict) and (now_ms - (v.get("time") or v.get("timestamp") or 0)) > LIFETIME_MS}
            if to_delete:
                base_url = FIREBASE_URL.replace("/points.json", "")
                requests.patch(f"{base_url}/points.json", json=to_delete, timeout=5)
                log(f"🧹 Удалено устаревших точек: {len(to_delete)}")
    except Exception as e:
        log(f"Ошибка очистки базы: {e}")

def get_existing_records():
    """Получает сохраненные посты для мгновенной сверки."""
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            records = set()
            for v in data.values():
                if isinstance(v, dict):
                    src = v.get("source", "")
                    raw = v.get("raw_text") or v.get("text", "")
                    raw = raw.replace(" (чисто)", "").strip()
                    records.add((src, raw))
            return records
    except Exception as e:
        log(f"Ошибка базы: {e}")
    return set()

def fetch_tg_posts(channel_url):
    """Считывает последние посты из веб-зеркала Telegram."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        r = requests.get(channel_url, headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"⚠️ Ошибка запроса к {channel_url}: код {r.status_code}")
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            t = re.sub(r'<[^>]+>', '', p).strip()
            if t:
                clean.append(t)
        return clean[-10:]
    except Exception as e:
        log(f"Ошибка загрузки {channel_url}: {e}")
        return []

def parse_batch_gemini(posts_list):
    """Анализирует ВСЕ посты разом со строгим JSON-режимом."""
    if not posts_list:
        return []

    cleaned_posts = []
    for p in posts_list:
        c = p.replace('"', "'").replace('\\', '/').replace('\n', ' ').strip()
        cleaned_posts.append(c)

    items = "\n".join([f"[{i}] {p}" for i, p in enumerate(cleaned_posts)])
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""Ты опытный диспетчер дорожной обстановки в городе Днепр (Украина).
Проанализируй список сообщений от водителей. Для КАЖДОГО определи локацию в г. Днепр и статус опасности.

ВАЖНЫЕ ПРАВИЛА ЛОКАЦИЙ И СЛЕНГА:
1. "Краснозаводская" / "Белелюбского" / "Павлова угол Белелюбского" -> угол ул. Академика Павлова и ул. Академика Белелюбского (бывш. Краснозаводская) [lat: 48.4795, lng: 35.0015].
2. "Павлова" -> ул. Академика Павлова [lat: 48.4775, lng: 34.9985].
3. "Огни" -> Вечный огонь на пр. Сергея Нигояна угол с пр. Ивана Мазепы [lat: 48.4716, lng: 34.9892].
4. "Водолечебница" -> заводы/промзона перед Кайдакским мостом со стороны Комбайнового (ул. Ударников) [lat: 48.4845, lng: 34.9648].
5. "Комбайновый" -> Днепрокомбайн / ул. Ударников перед мостом [lat: 48.4820, lng: 34.9620].
6. "Речпорт" -> Речной вокзал / Набережная Заводская [lat: 48.4802, lng: 35.0210].
7. "Озерка" -> рынок Озёрка / ул. Степана Бандеры [lat: 48.4687, lng: 35.0265].
8. "Артёма верх" / "верх Артёма" -> ул. Сечевых Стрельцов вверху [lat: 48.4370, lng: 35.0330].
9. "Артёма низ" / "низ Артёма" -> ул. Сечевых Стрельцов внизу [lat: 48.4600, lng: 35.0440].
10. "Кирова верх" / "верх Кирова" -> пр. Александра Поля вверху [lat: 48.4320, lng: 35.0180].
11. "Кирова низ" / "низ Кирова" -> пр. Александра Поля внизу [lat: 48.4610, lng: 35.0320].
12. "Рабочая верх" -> ул. Рабочая район ЮМЗ [lat: 48.4350, lng: 34.9980].
13. "Рабочая низ" -> ул. Рабочая район пр. Леси Украинки [lat: 48.4680, lng: 35.0080].
14. "Кротова" -> ул. Бориса Кротова / 12-й квартал [lat: 48.3970, lng: 34.9870].
15. "Гальченко" -> ул. Василия Гальченко [lat: 48.3990, lng: 34.9820].
16. "Шинная" -> ул. Шинная [lat: 48.4285, lng: 35.0194].
17. "Брама" -> ЖК Брама, Слобожанское [lat: 48.5330, lng: 35.0800].
18. "Лакокраска" -> район завода Лакокраска / ул. Журналистов [lat: 48.5030, lng: 35.0990].
19. "Островского" -> пл. Старомостовая / вокзал [lat: 48.4760, lng: 35.0240].
20. "Караван" -> ТРЦ Караван, Донецкое шоссе [lat: 48.5350, lng: 35.0240].
21. "Подстанция" -> кольцо пр. Науки / Дафи [lat: 48.4230, lng: 35.0250].
22. "Нагорка" -> Нагорный рынок / пр. Науки [lat: 48.4490, lng: 35.0620].
23. "Парус", "Победа", "Тополь", "Сокол", "Космическая" и другие улицы города.

ПРАВИЛО ДЛЯ СЛОВ БП:
- Слова "БП", "б.п.", "б/п", "блокпост", "мобпост", "фишка", "шлагбаум", "бус", "патруль" — это указание на проверку (status: danger), а улицу бери из соседних слов сообщения!

СТАТУС:
- "danger" (опасность): бп, б.п., блокпост, мобпост, дождь, гроза, тучи, мокро, поливают, оливки, синие, зеленые, пишут, обилечивают, тормозят, проверяют, бус, 🫒, 🌧️, ⚡, 👮, 📄.
- "clean" (чисто): чисто, пусто, спокойно, ясно, сухо, проехал, ок, 👍, 🫡, ✌️, 👌, ☀️, 🟢.

Сообщения:
{items}

Верни строго JSON массив:
[
  {{"id": 0, "valid": true, "address": "Название улицы/места", "lat": 48.46, "lng": 35.04, "status": "clean"}},
  {{"id": 1, "valid": false}}
]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            try:
                parsed = json.loads(raw_text)
                if isinstance(parsed, list):
                    return parsed
                elif isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            return v
                    return [parsed]
            except Exception:
                pass

            match = re.search(r'\[.*\]', raw_text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
        else:
            log(f"Ответ Gemini API: код {resp.status_code}")
    except Exception as e:
        log(f"Ошибка вызова Gemini: {e}")
    return []

def is_danger_text(text):
    t = text.lower()
    danger_patterns = [
        r'\bбп\b', r'б\.п', r'б/п', r'блокпост', r'блок\s*пост', r'мобпост', r'моб\.пост',
        r'фишк', r'шлагбаум', r'стоп-контрол', r'бус', r'патрул', r'дожд', r'туч', r'хмар',
        r'злив', r'оливк', r'баклажан', r'синие', r'зелен', r'пиксел', r'пиш[уе]', r'разда',
        r'обилеч', r'тормоз', r'останавл', r'провер', r'паку', r'кошмар'
    ]
    for pattern in danger_patterns:
        if re.search(pattern, t):
            return True
    return False

def sync_cycle():
    cleanup_old_points()
    existing_records = get_existing_records()

    for ch in CHANNELS:
        posts = fetch_tg_posts(ch["url"])
        new_posts = [p for p in posts if (ch["id"], p) not in existing_records]

        log(f"📡 [{ch['name']}] Всего постов: {len(posts)} | Новых для анализа: {len(new_posts)}")

        if not new_posts:
            continue

        results = parse_batch_gemini(new_posts)
        added_count = 0

        for item in results:
            idx = item.get("id")
            if idx is not None and 0 <= idx < len(new_posts):
                post = new_posts[idx]
                if item.get("valid") and "lat" in item and "lng" in item:
                    now_ms = int(time.time() * 1000)
                    
                    force_danger = is_danger_text(post)
                    has_clean = any(s in post for s in ["👍", "🫡", "✌️", "👌", "☀️", "🟢", "чисто", "спокійно", "ясно", "пусто", "сухо"])
                    
                    if force_danger:
                        is_clean = False
                    elif item.get("status", "").lower() == "danger":
                        is_clean = False
                    elif item.get("status", "").lower() == "clean" or has_clean:
                        is_clean = True
                    else:
                        is_clean = False

                    marker_color = ch["color_clean"] if is_clean else ch["color_danger"]
                    clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
                    final_text = f"{post}{clean_tag}"

                    payload = {
                        "text": final_text,
                        "raw_text": post,
                        "address": item.get("address", "Дніпро"),
                        "lat": float(item["lat"]),
                        "lng": float(item["lng"]),
                        "status": "чисто" if is_clean else "опасно",
                        "color": marker_color,
                        "time": now_ms,
                        "timestamp": now_ms,
                        "source": ch["id"],
                        "channel_name": ch["name"]
                    }
                    try:
                        r = requests.post(FIREBASE_URL, json=payload, timeout=8)
                        if r.status_code == 200:
                            log(f"  ✅ [{ch['name']}] Нанесено ({marker_color}): {item.get('address')}")
                            existing_records.add((ch["id"], post))
                            added_count += 1
                    except Exception as e:
                        log(f"Ошибка сохранения: {e}")

        log(f"  🏁 [{ch['name']}] Обработано! Добавлено меток: {added_count}")

if __name__ == "__main__":
    log("🚀 Запуск быстрого радара (2 канала синхронно)...")
    for step in range(8):
        sync_cycle()
        if step < 7:
            time.sleep(60)
    log("🏁 Цикл завершён успешно!")
