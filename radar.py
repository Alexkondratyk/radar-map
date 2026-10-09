import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
TG_URL = "https://t.me/s/dnepr_bez_tck"

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

def log(msg):
    """Мгновенный вывод сообщения в консоль GitHub."""
    print(msg, flush=True)

def find_working_model():
    """Находит реально работающую модель путем отправки тестового запроса."""
    log("🔍 Проверяем доступные модели Gemini живым тестом...")
    
    # Список кандидатов: сначала быстрые Flash, исключая закрытые 1.0
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
                    # Отсекаем старые закрытые версии 1.0 и эмбеддинги
                    if "generateContent" in methods and "1.0" not in name:
                        candidates.append((api_ver, name))
        except Exception as e:
            log(f"Сбой чтения списка моделей для {api_ver}: {e}")

    # Сортируем: Flash-модели ставим первыми
    candidates.sort(key=lambda x: (0 if "flash" in x[1].lower() else 1))

    # Запасной список, если API не вернул модели через поиск
    if not candidates:
        for ver in ["v1beta", "v1"]:
            for m in ["gemini-1.5-flash-latest", "gemini-1.5-flash-8b", "gemini-2.0-flash", "gemini-1.5-pro-latest"]:
                candidates.append((ver, m))

    log(f"Найдено подходящих моделей для проверки: {len(candidates)}")

    # Тестируем каждую модель реальным запросом, пока не получим 200 OK
    for api_ver, model_name in candidates:
        test_url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": "ping"}]}]}
        try:
            r = requests.post(test_url, json=payload, timeout=8)
            if r.status_code == 200:
                log(f"🎯 РАБОЧАЯ МОДЕЛЬ НАЙДЕНА: {model_name} (версия {api_ver})")
                return api_ver, model_name
            else:
                log(f"Пропуск {model_name}: код {r.status_code}")
        except Exception:
            continue

    log("⚠️ Живой тест не прошел, пробуем резервный gemini-1.5-flash-latest")
    return "v1beta", "gemini-1.5-flash-latest"

API_VERSION, MODEL_NAME = find_working_model()

def get_existing_messages():
    try:
        r = requests.get(FIREBASE_URL, timeout=10)
        if r.status_code == 200 and r.json():
            data = r.json()
            existing = {v.get("text", "").strip() for v in data.values() if isinstance(v, dict)}
            log(f"В базе Firebase уже сохранено точек: {len(existing)}")
            return existing
    except Exception as e:
        log(f"Ошибка проверки базы Firebase: {e}")
    return set()

def fetch_tg_posts():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(TG_URL, headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"Ошибка запроса к Telegram: статус {r.status_code}")
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            text = re.sub(r'<[^>]+>', '', p).strip()
            if text:
                clean.append(text)
        log(f"Успешно прочитано сообщений из канала: {len(clean)}")
        return clean[-6:]  # Берем последние 6 постов для защиты от минутного лимита
    except Exception as e:
        log(f"Ошибка загрузки постов: {e}")
        return []

def parse_with_gemini(text):
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    prompt = f"""Ты картографический аналитик города Днепр (Украина).
Проанализируй текст сообщения: "{text}"
Определи точную локацию/перекресток/район в черте г. Днепр.
Верни строго чистый JSON без markdown:
{{"valid": true, "address": "краткое место", "lat": 48.46, "lng": 35.04}}
Если точной локации в Днепре нет, спам, опрос или реклама — верни строго:
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
                log(f"Временная заминка Google ({resp.status_code}), пауза 5 сек...")
                time.sleep(5)
            else:
                log(f"Ответ Gemini API: код {resp.status_code}")
        except Exception as e:
            log(f"Исключение при запросе: {e}")
            time.sleep(2)
    return None

def sync_cycle():
    existing = get_existing_messages()
    posts = fetch_tg_posts()

    added = 0
    for post in posts:
        if post in existing:
            continue

        log(f"Обрабатываем пост: {post[:40]}...")
        result = parse_with_gemini(post)

        if result and result.get("valid") and "lat" in result and "lng" in result:
            now_ms = int(time.time() * 1000)
            payload = {
                "text": post,
                "address": result.get("address", "Днепр"),
                "lat": float(result["lat"]),
                "lng": float(result["lng"]),
                "time": now_ms,
                "timestamp": now_ms,
                "source": "dnepr_bez_tck"
            }
            try:
                r = requests.post(FIREBASE_URL, json=payload, timeout=10)
                if r.status_code == 200:
                    log(f"✅ Точка нанесена: {result.get('address')} ({result['lat']}, {result['lng']})")
                    existing.add(post)
                    added += 1
                else:
                    log(f"Ошибка записи в Firebase: код {r.status_code}")
            except Exception as e:
                log(f"Сбой отправки в Firebase: {e}")
        else:
            log("-> Координаты не найдены (не привязано к улице)")

        time.sleep(4)  # Пауза 4 сек держит нас строго в рамках бесплатного лимита 15 RPM

    log(f"Итог проверки: добавлено новых точек: {added}")

if __name__ == "__main__":
    log("🚀 Старт синхронизации радара...")
    sync_cycle()
    log("🏁 Проверка завершена успешно!")
