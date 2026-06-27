"""
Pure nutrition math — no I/O, no network. Easy to unit test.

Calorie target is computed with the Mifflin-St Jeor equation (BMR) multiplied
by an activity factor (TDEE), then adjusted for the user's goal.
"""

from dataclasses import dataclass

# Activity multipliers applied to BMR to get maintenance calories (TDEE).
ACTIVITY_FACTORS = {
    "sedentary": 1.2,      # little or no exercise
    "light": 1.375,        # 1-3 days/week
    "moderate": 1.55,      # 3-5 days/week
    "active": 1.725,       # 6-7 days/week
    "very_active": 1.9,    # hard exercise / physical job
}

# 1 kg of body fat ~= 7700 kcal. A 0.5 kg/week change ~= 550 kcal/day.
KCAL_PER_KG = 7700.0

# Safety floors so the app never recommends a dangerously low intake.
MIN_CALORIES = {"male": 1500, "female": 1200}


@dataclass
class Targets:
    bmr: int
    tdee: int
    daily_calories: int
    protein_g: int
    carbs_g: int
    fat_g: int


def bmr_mifflin_st_jeor(sex: str, age: int, height_cm: float, weight_kg: float) -> float:
    """Basal Metabolic Rate — calories burned at complete rest."""
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    if sex == "male":
        return base + 5.0
    return base - 161.0  # female


def compute_targets(
    sex: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity_level: str,
    goal: str,
    goal_rate_kg_per_week: float = 0.5,
) -> Targets:
    """
    Return the user's daily calorie + macro targets.

    goal: "lose" | "maintain" | "gain"
    goal_rate_kg_per_week: how fast they want to lose/gain (ignored for maintain).
    """
    bmr = bmr_mifflin_st_jeor(sex, age, height_cm, weight_kg)
    factor = ACTIVITY_FACTORS.get(activity_level, 1.2)
    tdee = bmr * factor

    daily_delta = goal_rate_kg_per_week * KCAL_PER_KG / 7.0
    if goal == "lose":
        daily = tdee - daily_delta
    elif goal == "gain":
        daily = tdee + daily_delta
    else:  # maintain
        daily = tdee

    daily = max(daily, MIN_CALORIES.get(sex, 1200))
    daily = round(daily)

    # Protein: 1.8 g/kg for a fat-loss bias, a bit lower at maintenance.
    protein_per_kg = {"lose": 1.8, "maintain": 1.6, "gain": 2.0}.get(goal, 1.6)
    protein_g = round(protein_per_kg * weight_kg)

    # Fat: ~25% of calories (9 kcal/g). Carbs: the remainder (4 kcal/g).
    fat_g = round((daily * 0.25) / 9.0)
    carbs_g = round((daily - protein_g * 4 - fat_g * 9) / 4.0)
    carbs_g = max(carbs_g, 0)

    return Targets(
        bmr=round(bmr),
        tdee=round(tdee),
        daily_calories=daily,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
    )
