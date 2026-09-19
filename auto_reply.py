import re

from analyzer import PROFILE


SAFE_INTENTS = {
    "location_question",
    "work_format_question",
    "skill_question",
    "experience_question",
    "job_question",
}


def _language(result):
    value = str(result.get("language") or "English").lower()
    if value.startswith("ru") or "russian" in value or "рус" in value:
        return "ru"
    if value.startswith("sr") or "serbian" in value or "срп" in value:
        return "sr"
    return "en"


def _flatten_profile_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _flatten_profile_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _flatten_profile_values(nested)
    elif value is not None:
        yield str(value)


def _normalize(text):
    return re.sub(r"[^a-z0-9а-яёђчћџžšđ]+", " ", str(text).lower()).strip()


def _mentioned_profile_skill(text):
    normalized_text = _normalize(text)
    if not normalized_text:
        return None
    for value in _flatten_profile_values(PROFILE.get("skills") or {}):
        normalized_value = _normalize(value)
        if normalized_value and re.search(r"(?<!\w)" + re.escape(normalized_value) + r"(?!\w)", normalized_text):
            return value
    return None


def _skill_reply(skill, lang):
    if lang == "ru":
        return f"Да, у меня есть профессиональный опыт работы с {skill}."
    if lang == "sr":
        return f"Da, imam profesionalno iskustvo sa {skill}."
    return f"Yes, I have professional experience with {skill}."


def _has_profile_skill(skill):
    needle = skill.lower()
    for values in (PROFILE.get("skills") or {}).values():
        for value in values:
            if needle == str(value).lower():
                return True
    return False


def _about_candidate(text):
    lowered = (text or "").lower()
    markers = (
        "you", "your", "candidate",
        "вы", "ваш", "ваша",
        "vi", "vaš", "vaša",
    )
    return any(marker in lowered for marker in markers)


def build_safe_auto_reply(text, result):
    """Return a deterministic reply only for a small allow-list of profile facts.

    None means the message must stay in Copilot mode.
    """
    if result.get("action") != "DRAFT":
        return None
    if result.get("intent") not in SAFE_INTENTS:
        return None
    if result.get("unknown_information"):
        return None

    intent = result.get("intent")
    lang = _language(result)
    text_lower = (text or "").lower()

    if intent == "location_question":
        if not _about_candidate(text):
            return None
        language_markers = (
            "language", "languages", "speak", "spoken", "english", "russian", "serbian",
            "язык", "языках", "говорите", "jezik", "jezici", "govorite",
        )
        asks_languages = any(marker in text_lower for marker in language_markers)
        if asks_languages:
            languages = PROFILE.get("languages") or {}
            location = str(PROFILE.get("location") or "my current location")
            language_text = ", ".join(f"{k} ({v})" for k, v in languages.items())
            if lang == "ru":
                return f"Я нахожусь в {location}. Я говорю на {language_text}." if language_text else f"Я нахожусь в {location}."
            if lang == "sr":
                return f"Nalazim se u {location}. Govorim: {language_text}." if language_text else f"Nalazim se u {location}."
            return f"I’m based in {location}. I speak {language_text}." if language_text else f"I’m based in {location}."
        location = str(PROFILE.get("location") or "my current location")
        preference = str(PROFILE.get("work_preference") or "").replace("_", " ").strip()
        if not preference:
            return None
        if lang == "ru":
            return f"Я нахожусь в {location} и рассматриваю {preference} позиции."
        if lang == "sr":
            return f"Nalazim se u {location} i trenutno razmatram {preference} pozicije."
        return f"I’m based in {location} and currently considering {preference} opportunities."

    if intent == "work_format_question":
        if not _about_candidate(text):
            return None
        preference = str(PROFILE.get("work_preference") or "").replace("_", " ").strip()
        if not preference:
            return None
        if lang == "ru":
            return f"Я рассматриваю {preference}."
        if lang == "sr":
            return f"Trenutno razmatram {preference}."
        return f"I’m currently considering {preference} opportunities."

    if intent == "skill_question":
        if not _about_candidate(text):
            return None
        skill = _mentioned_profile_skill(text) or _mentioned_profile_skill(" ".join(result.get("job", {}).get("technologies") or []))
        if not skill or not _has_profile_skill(skill):
            return None
        return _skill_reply(skill, lang)

    if intent == "experience_question":
        if not _about_candidate(text):
            return None
        banking = any(word in text_lower for word in ("bank", "banking", "бан", "banka"))
        years = any(word in text_lower for word in ("years", "year", "лет", "года", "godina"))
        if banking:
            experience = str(PROFILE.get("experience") or "relevant experience")
            if lang == "ru":
                return f"Мой опыт: {experience}."
            if lang == "sr":
                return f"Moje iskustvo: {experience}."
            return f"My experience: {experience}."
        if years:
            experience = str(PROFILE.get("experience") or "relevant experience")
            if lang == "ru":
                return f"Мой опыт: {experience}."
            if lang == "sr":
                return f"Moje iskustvo: {experience}."
            return f"My experience: {experience}."
        return None

    if intent == "job_question":
        roles_markers = ("role", "roles", "position", "positions", "looking for", "ищ", "позици", "uloga", "pozic")
        language_markers = ("language", "languages", "english", "russian", "serbian", "язык", "языках", "jezik", "jezic")
        if any(marker in text_lower for marker in roles_markers):
            roles = ", ".join(PROFILE.get("target_roles") or [])
            if lang == "ru":
                return "Я рассматриваю роли: {}.".format(roles)
            if lang == "sr":
                return "Trenutno razmatram sledeće pozicije: {}.".format(roles)
            return "I’m currently considering roles such as {}.".format(roles)
        if any(marker in text_lower for marker in language_markers):
            languages = PROFILE.get("languages") or {}
            language_text = ", ".join(f"{k} ({v})" for k, v in languages.items())
            if lang == "ru":
                return f"Я говорю на {language_text}."
            if lang == "sr":
                return f"Govorim: {language_text}."
            return f"I speak {language_text}."

    return None
