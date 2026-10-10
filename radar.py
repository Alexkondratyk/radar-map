import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут для живой карты
WEEK_MS = 7 * 24 * 60 * 60 * 1000  # 7 дней для архива статистики

CHANNEL_URL = "https://t.me/s/dnepr_bez_tck"

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

KNOWLEDGE_BASE_URL = FIREBASE_URL.replace("/points.json", "/knowledge_base.json")
STATS_ARCHIVE_URL = FIREBASE_URL.replace("/points.json", "/stats_archive.json")

def log(msg):
    print(msg, flush=True)

def find_working_model():
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
    try:
        r = requests.get(list_url, timeout=10)
        if r.status_code == 200:
            models_list = r.json().get("models", [])
            valid_candidates = []
            for m in models_list:
                methods = m.get("supportedGenerationMethods", [])
                name = m.get("name", "")
                if "generateContent" in methods and name:
                    valid_candidates.append(name)

            flash_first = sorted(valid_candidates, key=lambda x: 0 if "flash" in x.lower() else 1)
            for cand in flash_first:
                test_url = f"https://generativelanguage.googleapis.com/v1beta/{cand}:generateContent?key={GEMINI_KEY}"
                try:
                    test_r = requests.post(test_url, json={"contents": [{"parts": [{"text": "ping"}]}]}, timeout=6)
                    if test_r.status_code == 200:
                        log(f"🎯 Обрано модель: {cand}")
                        return cand
                except Exception:
                    continue
    except Exception as e:
        log(f"⚠️ Помилка автовизначення: {e}")

    return "models/gemini-1.5-flash-latest"

WORKING_MODEL = find_working_model()

def get_knowledge_base():
    try:
        r = requests.get(f"{KNOWLEDGE_BASE_URL}?t={int(time.time())}", timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            if isinstance(data, dict): return list(data.values())
            elif isinstance(data, list): return [x for x in data if x]
    except Exception as e:
        log(f"Помилка завантаження бази знань: {e}")
    return []

def cleanup_old_points():
    now_ms = int(time.time() * 1000)
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            to_delete = {k: None for k, v in data.items() if isinstance(v, dict) and (now_ms - (v.get("time") or v.get("timestamp") or 0)) > LIFETIME_MS}
            if to_delete:
                base_url = FIREBASE_URL.replace("/points.json", "")
                requests.patch(f"{base_url}/points.json", json=to_delete, timeout=5)
                log(f"🧹 Видалено застарілих міток: {len(to_delete)}")
    except Exception as e:
        log(f"Помилка очистки карти: {e}")

    try:
        r_arch = requests.get(STATS_ARCHIVE_URL, timeout=8)
        if r_arch.status_code == 200 and r_arch.json():
            arch_data = r_arch.json()
            to_delete_arch = {k: None for k, v in arch_data.items() if isinstance(v, dict) and (now_ms - (v.get("time") or 0)) > WEEK_MS}
            if to_delete_arch:
                base_url = FIREBASE_URL.replace("/points.json", "")
                requests.patch(f"{base_url}/stats_archive.json", json=to_delete_arch, timeout=5)
                log(f"🧹 Очищено записів архіву: {len(to_delete_arch)}")
    except Exception as e:
        log(f"Помилка очистки архіву: {e}")

def get_existing_records():
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            records = set()
            for v in data.values():
                if isinstance(v, dict):
                    raw = v.get("raw_text") or v.get("text", "")
                    raw = raw.replace(" (чисто)", "").strip()
                    records.add(raw)
            return records
    except Exception as e:
        log(f"Помилка бази: {e}")
    return set()

def fetch_tg_posts():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(CHANNEL_URL, headers=headers, timeout=10)
        if r.status_code != 200: return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            t = re.sub(r'<[^>]+>', '', p).strip()
            if t: clean.append(t)
        return clean[-35:]
    except Exception as e:
        log(f"Помилка парсингу: {e}")
        return []

def normalize_stem(word):
    w = word.lower().replace("і", "и").replace("ї", "и").replace("є", "е").replace("ё", "е")
    w = re.sub(r'[^\w\s]', '', w)
    w = re.sub(r'(ов[аеуы]|ськ[аеий]|ського|ської|ськом|ом|ем|а|я|у|е|и|ой|ей|ів|ий|ый)$', '', w)
    return w.strip()

def match_with_knowledge_base(post, kb_list):
    post_norm = " ".join([normalize_stem(w) for w in post.split()])
    for item in kb_list:
        phrase = (item.get("phrase") or "").strip()
        if not phrase or len(phrase) < 3: continue
        phrase_stems = [normalize_stem(w) for w in phrase.split() if len(w) > 2]
        if not phrase_stems: continue
        if all(stem in post_norm for stem in phrase_stems):
            return item
    return None

# ФИЛЬТР ВОПРОСОВ И ПУСТЫХ СЛУХОВ
def is_question_or_pure_rumor(text):
    t = text.lower()
    if "?" in t:
        return True
    rumor_patterns = [
        r'\bпідкажіть\b', r'\bподскажите\b', r'\bчи\s+є\b', r'\bчи\s+стоят\b',
        r'\bчи\s+правда\b', r'\bхтось\s+знає\b', r'\bкто\s+знает\b',
        r'\bяк\s+обстановка\b', r'\bкак\s+обстановка\b', r'\bщо\s+чути\b',
        r'\bчто\s+слышно\b', r'\bможливо\s+готов\b', r'\bвозможно\s+готов\b',
        r'\bначебто\s+готов\b', r'\bвроде\s+готов\b'
    ]
    return any(re.search(p, t) for p in rumor_patterns)

def is_danger_text(text):
    t = text.lower()
    danger_patterns = [
        r'\bбп\b', r'б\.п', r'б/п', r'блокпост', r'блок\s*пост', r'мобпост',
        r'фишк', r'шлагбаум', r'бус', r'патрул', r'дожд', r'туч', r'хмар',
        r'злив', r'оливк', r'баклажан', r'синие', r'зелен', r'пиш[уе]', r'разда',
        r'обилеч', r'тормоз', r'останавл', r'провер', r'паку'
    ]
    return any(re.search(p, t) for p in danger_patterns)

def parse_batch_gemini(posts_list, kb_examples):
    global WORKING_MODEL
    if not posts_list: return []

    cleaned_posts = [p.replace('"', "'").replace('\\', '/').replace('\n', ' ').strip() for p in posts_list]
    items = "\n".join([f"[{i}] {p}" for i, p in enumerate(cleaned_posts)])
    
    kb_hints = ""
    if kb_examples:
        sample_kb = kb_examples[-10:]
        kb_hints = "Еталони координат із бази знань:\n" + "\n".join([
            f"- \"{k.get('phrase')}\" -> [lat: {k.get('lat')}, lng: {k.get('lng')}], адреса: {k.get('address')}"
            for k in sample_kb if k.get('phrase') and k.get('lat')
        ])

    prompt = f"""Ти експерт із географії міста Дніпро. Визнач точні координати в межах Дніпра та статус дорожньої обстановки.

{kb_hints}

ПРАВИЛО СТАТУСУ:
- danger: бп, блокпост, бус, патруль, поліція, тцк, оливки, сині, зелені, перевірка, гальмують, пишуть, роздають, дощ, хмари, 🫒, 🌧️.
- clean: чисто, сухо, спокійно, ясно, проїхав, 👍, 🫡, ☀️, 🟢.

Повідомлення:
{items}

Поверни суворо JSON:
[
  {{"id": 0, "valid": true, "address": "Назва орієнтира", "lat": 48.4800, "lng": 34.9900, "status": "danger"}},
  {{"id": 1, "valid": false}}
]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/{WORKING_MODEL}:generateContent?key={GEMINI_KEY}"

    try:
        resp = requests.post(url, json=payload, timeout=22)
        if resp.status_code == 200:
            raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            parsed = json.loads(raw_text)
            if isinstance(parsed, list): return parsed
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list): return v
                return [parsed]
        else:
            log(f"⚠️ Помилка Gemini API: HTTP {resp.status_code}")
            if resp.status_code in [404, 429]:
                WORKING_MODEL = find_working_model()
    except Exception as e:
        log(f"Помилка запиту Gemini: {e}")
    return []

def log_to_stats_archive(now_ms, post, address, lat, lng, is_clean):
    try:
        archive_entry = {
            "time": now_ms,
            "text": post[:120],
            "address": address,
            "lat": lat,
            "lng": lng,
            "is_clean": is_clean
        }
        requests.post(STATS_ARCHIVE_URL, json=archive_entry, timeout=5)
    except Exception as e:
        log(f"Помилка запису в архів: {e}")

def sync_cycle():
    cleanup_old_points()
    existing_records = get_existing_records()
    kb_list = get_knowledge_base()

    posts = fetch_tg_posts()
    new_posts = [p for p in posts if p not in existing_records]
    log(f"📡 В каналі: {len(posts)} | Нових: {len(new_posts)} | В базі знань: {len(kb_list)}")

    if not new_posts: return

    posts_needing_ai = []
    ai_index_map = {}

    for idx, post in enumerate(new_posts):
        # ОТСЕИВАЕМ ВОПРОСЫ И СЛУХИ
        if is_question_or_pure_rumor(post):
            log(f"  🔇 Пропущено питання/чутку: «{post[:45]}»")
            existing_records.add(post)
            continue

        matched_kb = match_with_knowledge_base(post, kb_list)
        now_ms = int(time.time() * 1000)

        force_danger = is_danger_text(post)
        has_clean = any(s in post for s in ["👍", "🫡", "✌️", "👌", "☀️", "🟢", "чисто", "спокійно", "ясно", "пусто", "сухо"])
        is_clean = not force_danger and has_clean
        marker_color = "green" if is_clean else "red"
        clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
        final_text = f"{post}{clean_tag}"

        if matched_kb:
            log(f"  🎯 [БАЗА ЗНАНЬ]: '{matched_kb.get('phrase')}' -> {matched_kb.get('address')}")
            payload = {
                "text": final_text,
                "raw_text": post,
                "address": matched_kb.get("address", "Дніпро"),
                "lat": float(matched_kb["lat"]),
                "lng": float(matched_kb["lng"]),
                "status": "чисто" if is_clean else "опасно",
                "color": marker_color,
                "time": now_ms,
                "timestamp": now_ms,
                "from_kb": True
            }
            try:
                requests.post(FIREBASE_URL, json=payload, timeout=8)
                existing_records.add(post)
                log_to_stats_archive(now_ms, post, matched_kb.get("address", "Дніпро"), float(matched_kb["lat"]), float(matched_kb["lng"]), is_clean)
            except Exception as e:
                log(f"Помилка збереження: {e}")
        else:
            ai_index_map[len(posts_needing_ai)] = post
            posts_needing_ai.append(post)

    if posts_needing_ai:
        results = parse_batch_gemini(posts_needing_ai, kb_list)
        added_count = 0

        for item in results:
            idx = item.get("id")
            if idx is not None and idx in ai_index_map:
                post = ai_index_map[idx]
                if item.get("valid") and "lat" in item and "lng" in item:
                    now_ms = int(time.time() * 1000)
                    force_danger = is_danger_text(post)
                    has_clean = any(s in post for s in ["👍", "🫡", "✌️", "👌", "☀️", "🟢", "чисто", "спокійно", "ясно", "пусто", "сухо"])
                    
                    if force_danger: is_clean = False
                    elif item.get("status", "").lower() == "danger": is_clean = False
                    elif item.get("status", "").lower() == "clean" or has_clean: is_clean = True
                    else: is_clean = False

                    marker_color = "green" if is_clean else "red"
                    clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
                    final_text = f"{post}{clean_tag}"

                    lat_val = float(item["lat"])
                    lng_val = float(item["lng"])
                    addr_val = item.get("address", "Дніпро")

                    payload = {
                        "text": final_text,
                        "raw_text": post,
                        "address": addr_val,
                        "lat": lat_val,
                        "lng": lng_val,
                        "status": "чисто" if is_clean else "опасно",
                        "color": marker_color,
                        "time": now_ms,
                        "timestamp": now_ms
                    }
                    try:
                        r = requests.post(FIREBASE_URL, json=payload, timeout=8)
                        if r.status_code == 200:
                            log(f"  ✅ + {addr_val} ({marker_color})")
                            existing_records.add(post)
                            added_count += 1
                            log_to_stats_archive(now_ms, post, addr_val, lat_val, lng_val, is_clean)
                    except Exception as e:
                        log(f"Помилка збереження: {e}")

        log(f"  🏁 Нових міток через ІІ: {added_count}")

if __name__ == "__main__":
    log("🚀 Запуск безперервної зміни радара 24/7...")
    for step in range(305):
        sync_cycle()
        if step < 304:
            time.sleep(60)
    log("🏁 Зміна успішно завершена!")
