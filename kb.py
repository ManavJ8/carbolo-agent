"""
kb.py — Knowledge Base loader and grounded search.
All answers must come strictly from this file. Never invent features.
"""

import json
import os

KB_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.json")

with open(KB_PATH, "r") as f:
    KNOWLEDGE_BASE = json.load(f)


def get_all_models():
    """Return list of all car models in the KB."""
    return list(KNOWLEDGE_BASE.keys())


def get_variants(model: str):
    """Return variants for a given model, case-insensitive."""
    for kb_model in KNOWLEDGE_BASE:
        if kb_model.lower() == model.lower():
            return list(KNOWLEDGE_BASE[kb_model].keys())
    return []


def get_variant_data(model: str, variant: str):
    """
    Return full spec dict for a model+variant, or None if not found.
    None = the agent must NOT invent anything — say 'I'll check'.
    """
    for kb_model in KNOWLEDGE_BASE:
        if kb_model.lower() == model.lower():
            for kb_variant in KNOWLEDGE_BASE[kb_model]:
                if kb_variant.lower() == variant.lower():
                    return KNOWLEDGE_BASE[kb_model][kb_variant]
    return None


def search_kb(query: str) -> str:
    """
    Search the KB for a query and return relevant context as text.
    Handles English, Hinglish, and partial car names.
    This context is injected into the LLM system prompt — all answers
    must come strictly from this data, nothing else.
    """
    query_lower = query.lower()

    # ── Normalise common Hinglish / transliterated words ──────────────────
    # Maps Hindi/Hinglish words that appear in WhatsApp messages so that
    # the search can find the right KB section even for Hinglish queries.
    hinglish_map = {
        "kitna": "",       # "kitna hai" = "how much"
        "kitni": "",
        "kaisa": "",
        "kimat": "price",
        "daam":  "price",
        "rang":  "colors",
        "colour": "colors",
        "camera": "camera",
        "airbag": "airbags",
        "feature": "features",
        "sunroof": "sunroof",
        "petrol": "petrol",
        "diesel": "diesel",
        "cng": "cng",
    }
    normalised_query = query_lower
    for hindi, english in hinglish_map.items():
        normalised_query = normalised_query.replace(hindi, english)

    # ── Determine which models are mentioned ──────────────────────────────
    # Key insight: use ONLY the last word of the model name as the primary
    # keyword ("brezza", "swift", "baleno", "ertiga") so that the word
    # "maruti" alone does NOT match every model in the KB.
    model_keyword_map: dict[str, str] = {}
    for model in KNOWLEDGE_BASE.keys():
        last_word = model.split()[-1].lower()   # "brezza", "swift" …
        model_keyword_map[last_word] = model
        model_keyword_map[model.lower()] = model   # full name match too

    mentioned_models: set[str] = set()
    for keyword, model in model_keyword_map.items():
        if keyword in normalised_query:
            mentioned_models.add(model)

    # If no specific model mentioned → generic question, return ALL models
    generic_query = len(mentioned_models) == 0

    matched_sections = []

    for model, variants in KNOWLEDGE_BASE.items():
        # Skip models not mentioned when the query is model-specific.
        # This is the core anti-hallucination guard: asking about Brezza
        # must NEVER return Swift or Baleno data.
        if not generic_query and model not in mentioned_models:
            continue

        # Check if a specific variant is named in the query
        specific_variant_named = any(
            v.lower() in normalised_query for v in variants.keys()
        )

        for variant, specs in variants.items():
            variant_mentioned = variant.lower() in normalised_query

            # If a specific variant is in the query, only show that variant.
            # Otherwise show all variants for the model.
            if specific_variant_named and not variant_mentioned:
                continue

            section = f"\n--- {model} {variant} ---\n"
            for key, value in specs.items():
                if isinstance(value, list):
                    section += f"  {key}: {', '.join(str(v) for v in value)}\n"
                else:
                    section += f"  {key}: {value}\n"
            matched_sections.append(section)

    if not matched_sections:
        return "No matching car data found in knowledge base."

    return (
        "KNOWLEDGE BASE DATA (answer ONLY from this — "
        "never add any feature, spec, or price not listed here):\n"
        + "\n".join(matched_sections)
    )


def format_kb_for_model(model: str) -> str:
    """Return all variants for a specific model formatted as text."""
    result = ""
    for kb_model, variants in KNOWLEDGE_BASE.items():
        if kb_model.lower() == model.lower():
            result += f"\n{kb_model} — Available Variants:\n"
            for variant, specs in variants.items():
                result += f"\n  {variant}:\n"
                for key, value in specs.items():
                    if isinstance(value, list):
                        result += f"    {key}: {', '.join(str(v) for v in value)}\n"
                    else:
                        result += f"    {key}: {value}\n"
            return result
    return f"No data found for {model} in knowledge base."


def get_kb_summary() -> str:
    """Return a short summary of all available models for the agent's context."""
    lines = ["Available car models in our showroom:"]
    for model, variants in KNOWLEDGE_BASE.items():
        variant_list = ", ".join(variants.keys())
        lines.append(f"  - {model} (variants: {variant_list})")
    return "\n".join(lines)
