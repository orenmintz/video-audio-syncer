"""
Claude-powered nutrition estimation.

Two jobs:
  estimate_meal(text)  - turn a free-text meal description into calories + macros
                         using structured outputs (guaranteed-valid JSON).
  suggest_foods(...)   - suggest what to eat with the calories left before bed.

Uses the official Anthropic Python SDK. The API key is read from the
ANTHROPIC_API_KEY environment variable by the SDK automatically.
"""

from typing import List

import anthropic
from pydantic import BaseModel, Field

from config import AI_MODEL

# Lazily-constructed singleton client so importing this module never fails just
# because the API key isn't set yet (e.g. when the dashboard imports it).
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# --- Structured output schema ------------------------------------------------

class FoodItem(BaseModel):
    name: str = Field(description="Name of the single food or drink item")
    quantity: str = Field(description="Estimated amount, e.g. '2 slices', '200 g', '1 cup'")
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float


class MealEstimate(BaseModel):
    items: List[FoodItem]
    total_calories: int
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    confidence: str = Field(description="One of: low, medium, high")
    note: str = Field(description="One short friendly sentence about the estimate")


_ESTIMATE_SYSTEM = """You are a precise nutrition estimator for a calorie-tracking app.

The user sends a short, casual description of something they ate or drank \
(for example: "2 scrambled eggs and a slice of buttered toast", "big mac meal \
with a coke", "handful of almonds"). Your job is to break it into individual \
items and estimate calories and macronutrients for each.

Rules:
- Assume typical/standard portion sizes when the user is vague.
- Use real-world nutrition data; be realistic, not optimistic.
- calories should be whole numbers; macros may have one decimal.
- total_* fields must equal the sum of the items.
- Set confidence to "low" if the description is very vague, "high" if it is \
specific with clear quantities.
- Keep note to a single short, encouraging sentence."""


def estimate_meal(description: str) -> MealEstimate:
    """Estimate calories/macros for a free-text meal. Raises on API failure."""
    resp = _get_client().messages.parse(
        model=AI_MODEL,
        max_tokens=2000,
        system=_ESTIMATE_SYSTEM,
        messages=[{"role": "user", "content": description}],
        output_format=MealEstimate,
    )
    return resp.parsed_output


# --- Free-text suggestion ----------------------------------------------------

def suggest_foods(
    remaining_calories: int,
    remaining_protein: int,
    hours_to_sleep: float,
    goal: str,
) -> str:
    """A short, friendly suggestion of what to eat with the calories left."""
    if remaining_calories <= 0:
        prompt = (
            f"The user has already hit their calorie target for the day "
            f"(they are {abs(remaining_calories)} kcal over). Their goal is to "
            f"{goal} weight and they go to sleep in about {hours_to_sleep:.0f} hours. "
            f"In 2 short sentences, gently encourage them to stop eating for the day "
            f"or pick something very light (like water, herbal tea, or a few veggies)."
        )
    else:
        prompt = (
            f"The user has {remaining_calories} kcal and about {remaining_protein} g of "
            f"protein left for the day. Their goal is to {goal} weight and they go to "
            f"sleep in about {hours_to_sleep:.0f} hours. Suggest 2-3 concrete, realistic "
            f"food options (with rough calorie counts) that fit the remaining budget. "
            f"Keep it to a short bulleted list, friendly and practical."
        )

    resp = _get_client().messages.create(
        model=AI_MODEL,
        max_tokens=500,
        system="You are a friendly, practical diet coach. Be concise.",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
