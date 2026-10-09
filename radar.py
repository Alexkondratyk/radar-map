import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
TG_URL = "https://t.me/s/dnepr_bez_tck"
LIFETIME_MS = 40 * 60 * 1000  # 40 минут в миллисекундах

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

def log(msg):
    """Мгновенный вывод сообщения в консоль GitHub."""
    print(msg, flush=True)

def find_working_model():
    """Находит реально работающую модель путем отправки тестового запроса."""
    log("🔍 Проверяем доступные модели Gemini живым тестом...")
    candidates = []
    for api_ver in ["v1beta", "v1"]:
        try:
            url = f"https://generativelanguage.googleapis.com/{api_ver}/models?key={GEMINI_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                raw_models = resp.json().get("models", [])
                for m in raw_models:
                    name = m.get("name", "").replace("models/", "")
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods and "1.0" not in name:
                        candidates.append((api_ver, name))
        except Exception as e:
            log(f"Сбой чтения списка моделей для {api_ver}: {e}")

    candidates.sort(key=lambda x: (0 if "flash" in x[1].lower() else 1))

    if not candidates:
        for ver in ["v1beta", "v1"]:
            for m in ["gemini-flash-lite-latest", "gemini-1.5-flash-latest", "gemini-2.0-flash"]:
                candidates.append((ver, m))

    for api_ver, model_name in candidates:
        test_url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": "ping"}]}]}
        try:
            r = requests.post(test_url, json=payload, timeout=8)
            if r.status_code == 200:
                log(f"🎯 РАБОЧАЯ МОДЕЛЬ НАЙДЕНА: {model_name} (версия {api_ver})")
                return api_ver, model_name
        except Exception:
            continue

    return "v1beta", "gemini-flash-lite-latest"

API_VERSION, MODEL_NAME = find_working_model()

def cleanup_old_points():
    """Удаляет из базы Firebase точки, которые старше 40 минут."""
    try:
        r = requests.get(FIREBASE_URL, timeout=10)
        if r.status_code == 200 and r.json():
            data = r.json()
            now_ms = int(time.time() * 1000)
            base_url = FIREBASE_URL.replace("/points.json", "")
            
            for key, val in data.items():
                if isinstance(val, dict):
                    pt_time = val.get("time") or val.get("timestamp") or 0
                    if now_ms - pt_time > LIFETIME_MS:
                        del_url = f"{base_url}/points/{key}.json"
                        requests.delete(del_url, timeout=5)
    except Exception as e:
        log(f"Ошибка очистки устаревших точек: {e}")

def get_existing_messages():
    try:
        r = requests.get(FIREBASE_URL, timeout=10)
        if r.status_code == 200 and r.json():
            data = r.json()
            return {v.get("text", "").strip() for v in data.values() if isinstance(v, dict)}
    except Exception as e:
        log(f"Ошибка базы: {e}")
    return set()

def fetch_tg_posts():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(TG_URL, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            text = re.sub(r'<[^>]+>', '', p).strip()
            if text:
                clean.append(text)
        return clean[-10:]
    except Exception as e:
        log(f"Ошибка загрузки постов: {e}")
        return []

def parse_with_gemini(text):
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    prompt = f"""Ты эксперт-штурман дорожной обстановки в городе Днепр (Украина).
Определи локацию и статус опасности из дорожного сообщения: "{text}"

ОРИЕНТИРЫ ДНЕПРА:
1. "Огни" -> Вечный огонь / памятник на пр. Сергея Нигояна, угол с пр. Ивана Мазепы (бывш. Петровского) [lat: 48.4716, lng: 34.9892]
2. "Водолечебница" / "РОВД водолечебница" -> заводы/промзона перед Кайдакским мостом со стороны Комбайнового завода (ул. Ударников) [lat: 48.4845, lng: 34.9648]
3. "Комбайновый" -> Днепрокомбайн / ул. Ударников, перед мостом [lat: 48.4820, lng: 34.9620]
4. "Павлова" -> ул. Академика Павлова [lat: 48.4775, lng: 34.9985]
5. "Речпорт" -> Речной вокзал / пл. Десантников / Набережная Заводская [lat: 48.4802, lng: 35.0210]
6. "Озерка" -> рынок Озёрка / ул. Степана Бандеры [lat: 48.4687, lng: 35.0265]
7. "Кротова" -> ул. Бориса Кротова / 12-й квартал [lat: 48.3970, lng: 34.9870]
8. "Гальченко" -> ул. Василия Гальченко [lat: 48.3990, lng: 34.9820]
9. "Шинная" -> ул. Шинная [lat: 48.4285, lng: 35.0194]
10. "Брама" -> ЖК Брама, Слобожанское [lat: 48.5330, lng: 35.0800]
11. "Лакокраска" -> район завода Лакокраска / ул. Журналистов [lat: 48.5030, lng: 35.0990]
12. "Островского" -> пл. Старомостовая / вокзал [lat: 48.4760, lng: 35.0240]
13. "Караван" -> ТРЦ Караван, Донецкое шоссе [lat: 48.5350, lng: 35.0240]
14. "Парус" -> ж/м Парус [lat: 48.4835, lng: 34.9080]
15. "Подстанция" -> кольцо пр. Науки / Дафи [lat: 48.4230, lng: 35.0250]
16. "Нагорка" -> Нагорный рынок / пр. Науки [lat: 48.4490, lng: 35.0620]

СТАТУС (status):
- "clean" (зеленый): 👍, 🫡, ✌️, 👌, ☀️, 🟢, или слова "чисто", "пусто", "спокойно", "ясно", "проехал", "ок".
- "danger" (красный): 🫒, 🌧️, ⚡, 👮, 📄, или слова "бп", "повестки", "дождь", "тучи", "синие", "оливки", "баклажаны", "пишут", "бус", "тормозят".

Верни СТРОГО чистый JSON:
{{"valid": true, "address": "Название улицы", "lat": 48.46, "lng": 35.04, "status": "clean" или "danger"}}

Если сообщению нет соответствия в Днепре (спам, вопрос):
{{"valid": false}}"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            elif resp.status_code in [429, 503]:
                time.sleep(4)
        except Exception:
            time.sleep(2)
    return None

def sync_cycle():
    cleanup_old_points()
    existing = get_existing_messages()
    posts = fetch_tg_posts()

    added = 0
    for post in posts:
        if post in existing:
            continue

        result = parse_with_gemini(post)
        if result and result.get("valid") and "lat" in result and "lng" in result:
            now_ms = int(time.time() * 1000)
            gemini_status = result.get("status", "").lower()
            has_clean = any(s in post for s in ["👍", "🫡", "✌️", "👌", "☀️", "🟢", "чисто", "спокійно", "ясно", "пусто"])
            is_clean = (gemini_status == "clean") or has_clean

            clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
            final_text = f"{post}{clean_tag}"

            payload = {
                "text": final_text,
                "address": result.get("address", "Днепр"),
                "lat": float(result["lat"]),
                "lng": float(result["lng"]),
                "status": "чисто" if is_clean else "опасно",
                "color": "green" if is_clean else "red",
                "time": now_ms,
                "timestamp": now_ms,
                "source": "dnepr_bez_tck"
            }
            try:
                r = requests.post(FIREBASE_URL, json=payload, timeout=10)
                if r.status_code == 200:
                    status_text = "🟢 ЧИСТО" if is_clean else "🔴 ОПАСНО"
                    log(f"✅ Точка нанесена [{status_text}]: {result.get('address')}")
                    existing.add(post)
                    added += 1
            except Exception as e:
                log(f"Ошибка сохранения: {e}")

        time.sleep(2)

    if added > 0:
        log(f"Добавлено новых точек: {added}")

if __name__ == "__main__":
    log("🚀 Запуск 8-минутного непрерывного цикла (проверка каждые 60 сек)...")
    for step in range(8):
        sync_cycle()
        if step < 7:
            time.sleep(60)
    log("🏁 Цикл завершён успешно!")
